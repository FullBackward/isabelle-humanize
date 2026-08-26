# Isabelle-Humanize — Feasibility of an RLCR-Style Harness for Isabelle/HOL

This document assesses the feasibility of porting the **humanize** harness pattern (see
`humanize-analysis.md`) to the Isabelle theorem prover, using the **IsabelleGym MCP
servers** (`/Users/winstonren/GitHub/IsabelleGym`, v3.0) as the prover interface. It
covers (1) why the idea is a natural fit, (2) the existing substrate and exactly where a
loop driver hooks in, (3) research novelty relative to prior work, and (4) a concrete
engineering harness design.

> Note on sources: all claims about local repositories were verified by direct code
> reading (file:line citations throughout). The related-work section draws on model
> knowledge (web search was unavailable in the research environment) and should be
> citation-checked before publication use.

---

## 1. The premise: why Isabelle is the best possible target for RLCR

The humanize pattern is **implement → review → iterate**, with two hard-won lessons from
both its generations:

1. The reviewer must be **independent** of the builder (v1: Codex reviews Claude; v2:
   any second agent).
2. The review must be **machine-parseable into a control signal** (the mandatory
   `ADVANCED/STALLED/REGRESSED` verdict, severity markers `[P0]`–`[P9]`, the DONE gate).

Both lessons expose the fundamental weakness of RLCR-for-code: **the reviewer is itself
an LLM, and its verdicts are heuristics.** Codex can miss bugs, hallucinate issues, or
rubber-stamp. Humanize compensates with elaborate anti-gaming machinery — fail-closed
parsing, drift circuit breakers, immutable goal trackers, anti-cheat bash validators.

Interactive theorem proving removes that weakness entirely:

- **The prover is a perfect reviewer.** Isabelle's kernel either accepts a proof or it
  does not. `verify_chunk` returning `success=True ∧ proof_open=False ∧ used_sorry=False`
  is a ground-truth verdict no LLM review can match. The entire anti-gaming gauntlet of
  humanize v1 collapses into one boolean conjunction.
- **The feedback is structured and local.** Per-command status
  (`ok/failed/running/unprocessed`), error messages with line ranges, remaining subgoals,
  and sledgehammer suggestions are exactly the "severity markers" of the code loop, but
  richer and noise-free.
- **Iteration is cheap and safe to automate.** Failed chunks roll back automatically;
  checkpoints allow speculative exploration; there is no deployment, no side effects, no
  flaky tests — the loop can run fully unattended with a clear conscience.

In RLCR terms: *the prover is the Codex that cannot be wrong.* The LLM builder's job
reduces to search; the harness's job reduces to orchestrating that search well. This is
a materially stronger loop than the code version, and that strength is the core of the
research opportunity.

---

## 2. The substrate: what already exists in IsabelleGym

IsabelleGym 3.0 (local lineage: 1.0 Milan/Cambridge → 2.0 Li/Edinburgh → 3.0 server
iteration by Ren) is a FastAPI HTTP server wrapping Isabelle 2025-2 behind a session
pool, explicitly built for training/evaluating LLM provers. Request flow: HTTP client →
FastAPI (`server/`) → Py4J → one shared Scala gateway JVM (`repl/`) → Isabelle/ML per
session. Three verification workflows: small-step REPL with checkpoints, **chunk**
(`verify_chunk`: a whole proof block as one PIDE edit with per-command status and
auto-rollback of failures), and big-step (`isabelle build`).

### 2.1 The two MCP servers (the harness's prover interface)

**`mcp_stepwise_server/`** — chunk-centric, 10 tools (`mcp_stepwise_server/app.py`).
Sessions/leases are auto-managed per MCP connection; the agent never sees a session id.
The surface maps directly onto loop roles:

| Tool | Loop role |
|---|---|
| `enter_theory(name, imports, field)` | attempt setup |
| `verify_chunk(text, timeout, detail)` | **the only execution tool** — returns header `success/proof_open/pending_qed/used_sorry/timed_out/stuck_line` plus failed/running rows, with targeted NOTEs that already read like reviewer comments (e.g. "goal discharged but the proof block is NOT closed — submit a bare `qed`") |
| `proof_state` | reviewer query: remaining subgoals, `proof_finished`, `pending_qed` |
| `diagnostic(command)` | guarded read-only queries (`thm`, `find_theorems`, `print_*`) — the "look things up" channel |
| `sledgehammer(timeout_s)` | automated escape hatch on an open goal |
| `checkpoint` / `restore` / `rollback` | speculative search, backtracking |
| `verify_batch(items, max_parallel)` | **fan-out**: many independent chunks verified concurrently across the session pool |
| `source`, `close_theory` | artifact extraction, teardown |

**`mcp_lsp_server/`** — lean-lsp-mcp-style file-sync workflow, 21 tools, keyed by
canonical file path; the MCP never writes files (the agent edits via runner-side file
tools; every tool call re-syncs from disk). Highlights for a loop harness:

- `isabelle_diagnostic_messages` — the primary review channel (per-command
  errors/warnings + `success/proof_open/used_sorry`).
- `isabelle_goal(file, line)` — `goals_before`/`goals_after` per command: a structured
  proof-state *diff*, the granular progress signal.
- **`isabelle_multi_attempt(file, line, candidates)`** — a ready-made **best-of-n
  critic**: N proof-step candidates verified in parallel on isolated warm scratch
  sessions, never mutating the working file. This is swarm mode for proof steps.
- `isabelle_run_code`, `isabelle_sledgehammer`, checkpoints, heap-pool tools
  (`isabelle_build_heap` — "build passing IS the verification gate").

### 2.2 `MCP-comparison/` — the loop skeleton that already runs

The decisive feasibility fact: **a single-agent version of the loop already exists and
benchmarks four Isabelle MCP servers.** `MCP-comparison/run_isabellegym.py` (696 lines)
implements, per attempt:

1. **Setup** — open stdio MCP session; `enter_theory`; warm the session with a throwaway
   lemma; **seed the target statement** via `verify_chunk` so the agent starts from an
   open proof; warm up sledgehammer.
2. **Agent loop** — up to `max_rounds` (default 100) / wall cap (default 2400 s): LLM
   chat with MCP tools → execute tool calls under timeout → feed rendered results back.
   Malformed tool args get error replies; mid-attempt `enter_theory` re-seeds the
   statement automatically.
3. **DONE gate** (`check_done_readiness`) — when the agent claims completion, the harness
   *objectively re-verifies* via MCP (`proof_state.proof_finished=true` ∧ sorry-free
   `source`), rejecting false claims with a critique message up to 2 nudges. This is
   precisely the humanize Stop-hook gate, ported.
4. **Neutral arbiter** (`common/arbiter.py`) — the only success judge: final `.thy`
   contains no `sorry`/`oops`, the target theorem is present, and
   `verify_bigstep_text` (a real `isabelle build`) passes. The agent's own claim is
   recorded only for agreement analysis.
5. **Metrics** (`common/metrics.py`) — `arbiter_solved`, rounds, tool calls, wall/prover/
   model time split, token usage, truncation/nudge counts → `results.jsonl`, with
   pass@1-over-repeats and mean-wall-solved defined in the companion framework doc.

The runner's prompt bodies (`_segment_prompt_body` etc.) encode hard-won repair
tactics — segmented submission, "never write external-solver calls directly, escalate to
sledgehammer after two failures on the same subgoal", timeout = stuck-line diagnosis,
`pending_qed` handling — directly reusable as the builder's system prompt.

### 2.3 Prior art in neighboring repos (for comparison, not foundation)

- **Isabelle-MCP** (`~/GitHub/Isabelle-MCP`) — mature single-session MCP server
  (PyPI, CI, 12 tools), but no sledgehammer, no concurrency, stdio-only.
- **isabelle-pide-mcp** — in-PIDE Scala MCP server with the cleanest per-command state
  report model and shipped agent-skill prompts; no tests/CI, no sledgehammer.
- **AutoCorrode I/Q** — the fourth system in the comparison harness.

These matter as *baselines* (the MCP-comparison harness already measures against them),
not as foundations.

### 2.4 Gap list — what is genuinely missing

| Missing piece | Closest existing prior art |
|---|---|
| **Multi-role loop** (separate builder/reviewer agents, reviewer persona, cross-iteration memory beyond the message list) | humanize v1/v2 RLCR itself; MCP-comparison runners are single-agent |
| **Loop orchestration as a reusable component** (budgets, termination policy, drift detection) | the runners hardcode one policy; humanize's state machine is the model |
| **Fine-grained progress signal** (proof-state delta classification, stall detection) | `server_gym/success_checker.py` (`is_proof_progress`, error classification) — but only reachable from the small-step API, not the MCP layer; `isabelle_goal`'s before/after subgoals are the raw material |
| **A BitLesson analog** (cross-attempt lemma/tactic memory) | nothing — greenfield |
| **Unified task format** (`.thy` with hole ↔ pass/fail oracle) | `common/problems.py` + arbiter, per-system runners |
| Minor engineering debt to inherit | `mcp<2` pin, stale `mcp_server.app` reference in `config.yaml` (rename fallout), Docker-only deployment (~24 GB RAM), no CI |

**Verdict on feasibility: high.** Every primitive the loop needs — execution, structured
feedback, rollback, fan-out, objective arbitration, metrics — already exists and is
already wired to LLM agents in `MCP-comparison/`. The work is *composition and
extension*, not invention of infrastructure.

---

## 3. Research novelty

### 3.1 Where the field stands (from model knowledge — verify before citing)

LLM-based theorem proving has largely organized into three paradigms:

- **Whole-proof generation** — a model emits a full proof, checked once (e.g.
  autoformalization pipelines, Baldur's proof generation+repair, DeepSeek-Prover's
  large-scale Lean 4 training). The prover is a filter, not an interlocutor.
- **Stepwise interactive proving** — the model acts inside an environment with proof
  state in/out: COPRA (in-context RL-style agent over Lean/Coq with a tactic state and
  backtracking), LeanDojo/ReProver (retrieval-augmented tactic prediction on Lean),
  Draft-Sketch-Prove (informal draft → formal sketch → ATP fill), LEGO-Prover (lemma
  library growth across problems). These are agentic but almost always **single-role**:
  one policy receiving prover feedback.
- **Informal multi-agent proving** — recent work uses multi-LLM discussion/critique for
  *informal* mathematics, and code-agent harnesses (SWE-agent style) for repo tasks.

Meanwhile the software-engineering side has independently converged on
**builder/reviewer loops** (humanize/RLCR, actor-reviewer flows like `official/rlar`,
agentic code review) — but there the review verdict is an LLM judgment, not ground truth.

### 3.2 The novel intersection

An Isabelle-humanize harness sits at an intersection that, to our knowledge, is not
occupied:

1. **Role-separated multi-agent proving with a ground-truth arbiter.** A builder LLM and
   a *distinct* reviewer LLM (different model family, humanize's "one build + one review"
   rule) iterating over an Isabelle development, where the prover — not a model — is the
   final judge at every gate. Prior interactive provers (COPRA et al.) are single-agent;
   prior multi-agent math systems lack kernel-checked verdicts; prior code loops lack
   ground-truth review. The research question this answers: **does independent LLM review
   add measurable value when the prover already gives perfect verification?** (Hypothesis:
   yes for *steering* — strategy, decomposition, lemma choice, drift detection — even
   though it is redundant for *correctness*. This decomposition of roles is itself a
   finding.)
2. **The drift/stall machinery of RLCR, made exact.** Humanize's
   `ADVANCED/STALLED/REGRESSED` verdict is an LLM's opinion about code. In Isabelle,
   progress is *measurable*: subgoal count/shape deltas (`isabelle_goal`'s
   `goals_before`/`goals_after`), checkpoint coverage, sledgehammer outcomes. A
   **proof-progress state machine with formal stall detection** — the RLCR drift circuit
   breaker rebuilt on objective signals — is a clean, citable contribution.
3. **Cross-attempt proof memory (the BitLesson analog).** A persistent, validated store
   of "lessons" — which tactics closed which goal shapes, which sledgehammer patterns
   fired, which lemma names exist — crystallized after each attempt and force-consulted
   by future attempts, mirrors humanize's BitLesson and LEGO-Prover's growing lemma
   library, but at the *agent-experience* level rather than the library level.
4. **A methodology, not just a system.** Humanize2's deepest export is SPEC-driven
   harness engineering (normative SPECs, objective arbiters, journal-everything
   observability). Applying that discipline to prover harnesses — where benchmarks are
   notoriously hard to compare — plus the existing four-way MCP comparison framework,
   yields an evaluation methodology contribution: same problems, same arbiter, same
   budgets, role architecture as the independent variable.

### 3.3 Honest novelty boundaries

- The **single-agent inner loop is not novel** — it already runs in `MCP-comparison/`
  and resembles COPRA. Claim it as infrastructure, not contribution.
- Sledgehammer-in-the-loop, draft-sketch decomposition, and lemma libraries all have
  prior art; the novelty is in their **orchestration by role-separated agents under
  formal progress semantics**.
- The strongest paper-shaped claim is the **controlled ablation**: identical budget,
  identical prover interface, varying only the loop architecture (single agent vs
  builder+reviewer vs builder+reviewer+memory), measured by `arbiter_solved` pass@1,
  wall time, and prover/model time split. The IsabelleGym comparison harness makes this
  ablation *cheap and fair*, which is rare in this literature.

---

## 4. Engineering harness design

### 4.1 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ isabelle-humanize loop driver (Python, async)               │
│                                                             │
│  ┌───────────┐   review verdict    ┌────────────┐           │
│  │  Builder  │ ◄────────────────── │  Reviewer  │           │
│  │  (LLM A)  │ ── proof chunk ──► │  (LLM B)   │           │
│  └─────┬─────┘                     └─────┬──────┘           │
│        │ MCP tool calls                  │ MCP queries      │
│  ┌─────▼─────────────────────────────────▼──────┐           │
│  │  mcp_stepwise_server  /  mcp_lsp_server      │  (forked  │
│  │  verify_chunk · proof_state · sledgehammer · │   from    │
│  │  multi_attempt · checkpoints · verify_batch  │  Isabelle-│
│  └─────┬────────────────────────────────────────┘   Gym)   │
│  ┌─────▼──────────┐   ┌────────────────┐                    │
│  │ Progress State │   │  Proof Memory  │  (BitLesson        │
│  │ Machine        │   │  (lessons KB)  │   analog)         │
│  └────────────────┘   └────────────────┘                    │
│  ┌──────────────────────────────────────────┐               │
│  │ Arbiter (fork common/arbiter.py):        │               │
│  │ isabelle build ∧ no sorry ∧ theorem      │  ground truth │
│  └──────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Fork points (concrete)

- **Fork `run_isabellegym_lsp.py`'s `run_attempt`** for the file-artifact model — the
  "implementation" is a `.thy` file the reviewer can diff and the arbiter can rebuild,
  which matches RLCR's code-review shape best. Keep `mcp_session`/`call_tool`,
  `ModelClient.chat`, `AttemptResult`/`append_result`, `arbiter.check`,
  `check_done_readiness`. (Alternative: fork `run_isabellegym.py` for chunk-granular
  state with checkpoints.)
- **Role mapping (LSP MCP):** builder = model + runner-side `write_file`; reviewer =
  second model armed with `isabelle_diagnostic_messages`, `isabelle_proof_state`,
  `isabelle_goal`, `isabelle_multi_attempt` (as its "try alternatives" instrument);
  verdict = progress state machine; final judge = `arbiter.check`.
- **Role mapping (stepwise MCP):** builder = `verify_chunk` + `diagnostic` +
  `sledgehammer`; reviewer = `proof_state` + `source` + the `_render_chunk` NOTE lines
  (already proto-critiques).
- Fix the stale `mcp_server.app` → `mcp_stepwise_server.app` reference in
  `config.yaml`; ensure `mcp<2` and `openai` in the runner environment.

### 4.3 The review protocol (RLCR → Isabelle)

Per iteration:

1. Builder submits a proof chunk/file revision.
2. Prover verifies (mechanical gate, replaces humanize's cheap pre-gates).
3. Reviewer LLM receives: the diff, the structured prover report, the current subgoals —
   and must emit a **mandatory verdict line**, exactly as in humanize:
   `PROGRESS: ADVANCED / STALLED / REGRESSED` plus actionable issues. Fail-closed parse;
   malformed → rerun review.
4. The **progress state machine** adjudicates objectively in parallel: subgoal delta
   from `isabelle_goal`, checkpoint coverage, time spent. LLM verdict and objective
   signal are both logged; disagreement between them is itself a logged metric (a
   research-grade signal about reviewer quality).
5. Stall counter ≥2 → forced strategy change (replan prompt: decompose lemma, try
   `isabelle_multi_attempt` candidates, escalate sledgehammer); ≥3 → terminate attempt
   (drift circuit breaker).
6. DONE gate + arbiter as in the existing runners.

### 4.4 Swarm mode

`isabelle_multi_attempt` (step-level best-of-n) and `verify_batch` (chunk-level fan-out
across the session pool) give two granularities of parallel search already. A
humanize-style "agent teams" mode = N builders proposing candidates + reviewer ranking
them by prover verdict — cheap to build on these primitives.

### 4.5 Observability and memory

- Reuse `AttemptResult`/`results.jsonl` per iteration, not just per attempt; add
  per-round fields (verdict, objective progress delta, reviewer/builder token split).
- Proof-memory KB: append-only YAML/markdown store (BitLesson schema: scope, goal shape,
  failing approach, working approach, evidence round); builder must cite consulted
  entries each round — enforced by the driver, mirroring `bitlesson-validate-delta.sh`.
- Optionally adopt humanize2's cycle-journal convention (`<when>-<which>/` dirs,
  event-per-line JSONL) for replay.

### 4.6 Phased plan

| Phase | Deliverable | Effort driver |
|---|---|---|
| 0 | Reproduce: run existing `MCP-comparison` baseline on a problem set; fix rename fallout | env setup, Docker |
| 1 | **Single-agent + progress state machine**: fork runner, add objective stall detection + forced strategy escalation | the state machine |
| 2 | **Builder + reviewer roles**: second `ModelClient`, verdict-line protocol, fail-closed parsing | prompt/protocol design |
| 3 | **Proof memory KB** with enforced consultation | KB schema + driver gate |
| 4 | **Swarm**: multi_attempt/batch-driven candidate search with reviewer ranking | orchestration |
| 5 | **Ablation study** on AFP-derived + HOL_corpus problems: architecture as independent variable | compute budget |

### 4.7 Risks

- **Deployment weight** — Docker container, JVM gateway, ~1.5–2.5 GB per session, 24 GB
  RAM cap; swarm phases need real memory headroom. Mitigate: small pool +
  `verify_batch` before true parallelism.
- **Cost/latency** — Isabelle chunks can take minutes; `max_tokens` and round budgets
  must be tuned per model (the existing config comments show 4096 max_tokens already
  broke reasoning models).
- **Inherited debt** — no CI in IsabelleGym, `mcp<2` pin, in-flight rename; isolate the
  new driver so upstream churn is absorbable.
- **Novelty risk** — if ablation shows reviewer adds nothing over prover feedback, the
  negative result is still publishable *because* the measurement is clean — but design
  the study so either outcome yields a claim.
- **Web-verification debt** — the related-work sketch (§3.1) needs a proper literature
  pass before any publication claim.

---

## 5. Verdict

**Feasible, and unusually well-positioned.** The prover interface (IsabelleGym MCPs), a
running single-agent loop, an objective arbiter, and a metrics framework all exist
locally and were built for exactly this kind of experiment. The engineering is a fork
and extension of `MCP-comparison/` — weeks, not months. The research contribution is
real but must be claimed carefully: not "an agent that proves theorems" (prior art is
crowded) but **"RLCR role separation made exact: what independent review buys when the
prover is the ground truth"** — a question the humanize lineage is uniquely suited to
ask and IsabelleGym is uniquely suited to answer.

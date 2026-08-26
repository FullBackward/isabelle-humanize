# Merge Plan — Reproducing Humanize (RLCR) on IsabelleGym

> **Status: partially superseded.** Option B has been abandoned — the project goes
> straight to option A. See **`implementation-plan.md`** for the current build plan.
> This document remains valid as the architecture rationale: the option comparison
> (§1), the MCP bridge mechanics and LSP-server decision (§2), and the v1-gate →
> flow-gate mapping (§3.2) are all still in force.

How to merge `humanize2` (`hmz`, the orchestration engine) with `IsabelleGym` (the
prover substrate + MCP servers) to reproduce the humanize RLCR loop with Isabelle/HOL
proof tasks as the workload. Builds on `humanize-analysis.md` and
`isabelle-humanize-feasibility.md`; all mechanisms cited there are not re-derived here.

---

## 0. What "reproducing the humanize result" means

Two distinct targets, kept separate throughout this plan:

1. **Behavioral reproduction** — the RLCR loop semantics run end-to-end on Isabelle
   tasks: implement → independent review → verdict-gated iteration → drift circuit
   breaker → finalize, producing the same artifact family (goal tracker, per-round
   summaries/reviews, terminal state taxonomy).
2. **Measured reproduction** — the loop demonstrably does what humanize claims loops
   do: iterate to an objectively-verified completion within budget, with the iteration
   (not one-shot generation) doing the work. Measured by `arbiter_solved` pass@1,
   rounds-to-solved, wall/prover/model time split — compared against the single-agent
   baseline that already exists in `MCP-comparison/`.

The merge is judged against both. Target 1 without target 2 is theater.

---

## 1. Merge architecture: where the two projects meet

Three options, evaluated:

| | A. hmz flow + MCP-attached agents (recommended) | B. Extend IsabelleGym's `MCP-comparison` runner | C. Port RLCR into hmz builtins |
|---|---|---|---|
| Shape | RLCR as a flowverse flow; builder/reviewer are ordinary hmz agents (any CLI) with IsabelleGym MCP servers attached | Add reviewer role + verdict protocol to `run_isabellegym*.py` (single Python repo) | Rewrite RLCR as typed Python under `src/hmz/flows/builtin/` |
| Fidelity to humanize | Highest — uses the actual v2 engine (Moments, cycles, traces, accounts) | Medium — reimplements loop semantics by hand | Highest but frozen |
| Effort | Medium | Lowest | High, and violates hmz's own rule that builtins hold only teaching flows |
| Dependencies | hmz installed + IsabelleGym Docker stack | IsabelleGym only | hmz + IsabelleGym |
| Gets traces/TUI/accounts for free | Yes | No | Yes |

**Recommendation: B first as the working prototype (it derisks the prover interaction),
then A as the actual merge deliverable.** B and A share prompts, the arbiter, and the
verdict protocol, so B's artifacts transfer. C is explicitly rejected: humanize2's
`flows/SPEC.md` reserves `builtin/` for "the flows that show what a flow is" — a flowverse
flow is the idiomatic home for RLCR (it is where `official/humanize1` lives).

The rest of this plan describes A, with B marked as the phase-1 stepping stone.

---

## 2. The bridge: attaching IsabelleGym MCP servers to hmz agents

Verified fact from close inspection: **hmz does not manage MCP configuration itself.**
`src/hmz/backends.py` treats MCP credential files (`mcp_credentials.json`,
`mcp-auth.json`) as opaque backend-owned data, and the drivers contain no MCP setup
logic. MCP attachment is entirely delegated to each backend CLI's own configuration —
which works in our favor, because hmz spawns every agent as a subprocess with
`cwd=<workspace>` and the CLIs discover workspace config themselves.

**Which MCP: the LSP server is the primary interface.** IsabelleGym ships two MCP
servers; the merge uses **`mcp_lsp_server`** (the lean-lsp-mcp-style file-sync server),
not the chunk-centric `mcp_stepwise_server`, for four reasons:

1. **The artifact model matches RLCR.** The loop is code-review-shaped: the
   "implementation" is a `.thy` file, the reviewer diffs it, the arbiter rebuilds it.
   The LSP MCP is file-sync by design (the MCP never writes files; every tool call
   re-syncs from disk via `load_document`). The stepwise MCP holds the proof as session
   state — no file to diff or rebuild without an extra extraction step.
2. **Structural role separation.** Because the LSP MCP is read-only by design, the
   reviewer *cannot* mutate the proof — least privilege is enforced by the tool layer,
   not by prompts. And since hmz agents are full CLI agents (Claude Code edits files
   natively), the builder needs no runner-side `write_file` shim at all — the CLI edits
   the `.thy` itself and the MCP re-syncs.
3. **`isabelle_goal(file, line)` returns `goals_before`/`goals_after`** — the granular,
   objective progress signal the drift state machine (§3.4) consumes. Stepwise's
   `proof_state` only exposes the tip state.
4. **`isabelle_multi_attempt(file, line, candidates)` is the swarm primitive** —
   best-of-n candidates *at a position in one open proof*, each verified on an isolated
   warm scratch session. Stepwise's `verify_batch` fans out *independent theories*, not
   alternative candidates on one proof.

The stepwise MCP remains as (a) a **fallback** if the file-sync/sync-latency model
proves awkward for a given backend CLI, and (b) an **ablation arm** — `MCP-comparison/`
already runs both servers, so "chunk-centric vs file-sync interface" is a free
independent variable. Its `verify_chunk` rendering (auto-rollback NOTEs, `pending_qed`
handling, `stuck_line` diagnosis) is also the best reference text for the builder's
feedback prompts.

Concretely, per backend CLI used as builder/reviewer:

- **Claude Code** — a `.mcp.json` in the flow's workspace:
  ```json
  {
    "mcpServers": {
      "isabellegym": {
        "command": "python",
        "args": ["-m", "mcp_lsp_server.app"],
        "env": {
          "PYTHONPATH": "/Users/winstonren/GitHub/IsabelleGym",
          "ISABELLE_MCP_LSP_GYM_URL": "http://localhost:8000",
          "ISABELLE_MCP_LSP_TRANSPORT": "stdio"
        }
      }
    }
  }
  ```
- **Codex** — equivalent `[mcp_servers.isabellegym]` table in the workspace/user
  `config.toml`.
- **Kimi / other ACP CLIs** — their native MCP config equivalents (verify per CLI before
  committing to it as a role; this is a phase-0 checklist item).

Prerequisites on the IsabelleGym side (from its AGENTS.md):

1. `docker compose up -d --build`, then `docker compose exec isabelle-gym python -m
   server.app.main` (plain `exec`, not `bash -lc`); health-check `curl
   http://localhost:8000/healthz`.
2. The MCP servers run **host-side** and require `"mcp<2"` (mcp 2.0 removed FastMCP —
   this pin is load-bearing; the LSP server is not in the container image). Config is
   `ISABELLE_MCP_LSP_*` env vars (`mcp_lsp_server/config.py`).
3. **Fix the rename fallout first** if copying anything from `MCP-comparison/`:
   `config.yaml` still references `python -m mcp_server.app`; the canonical stepwise
   module is `mcp_stepwise_server.app`.

Tool-surface partitioning by role (least privilege per agent):

| Role | Tools exposed | How |
|---|---|---|
| Builder | its CLI's native file editing (Edit/Write on the `.thy`) **plus** LSP MCP: `isabelle_open`/`sync`, `isabelle_diagnostic_messages`, `isabelle_goal`, `isabelle_proof_state`, `isabelle_query`, `isabelle_sledgehammer`, `isabelle_multi_attempt`, `isabelle_checkpoint`/`restore`/`rollback` | the builder's edits land on disk; the MCP re-syncs and reports |
| Reviewer | LSP MCP read-only tools only: `isabelle_diagnostic_messages`, `isabelle_goal`, `isabelle_proof_state`, `isabelle_source`, `isabelle_query`, `isabelle_hover_info`, `isabelle_definition`, plus `isabelle_multi_attempt` for "try alternatives" on scratch sessions | **structurally read-only** — the LSP MCP never writes files; no file tools are given to this agent |
| Arbiter | not an agent — a Python call in the flow | `MCP-comparison/common/arbiter.py::check` forked into the flow |

---

## 3. The `isabelle_rlcr` flow

A flowverse flow (`<flowverse>/flows/isabelle_rlcr/__init__.py` + `skills/`), modeled on
`official/humanize1` but exploiting that the prover is ground truth.

### 3.1 Signature

```python
class Roles(NamedTuple):
    builder: Agent      # e.g. claude/claude-opus-4-8:high
    reviewer: Agent     # e.g. codex/gpt-5.6-sol:high — MUST be a different family
    human: Person       # open-question escalation, "begin with the end in mind" quiz

@flow(name="rlcr", resumable=True)
def run(agents: Roles, task: str, config: RLCRConfig, state: dict) -> None: ...
```

`RLCRConfig` is a pydantic model (v1's 23 flags become fields: `max_iterations=42`,
`full_review_round=5`, `arbiter_timeout_s=900`, `drift_stall_breaker=3`,
`bitlesson_required=True`, …). `resumable=True` gives crash-resume via the cycle's
`state.json` for free — v1 hand-rolled this with `state.md`.

### 3.2 Gate pipeline: v1 Stop-hook gates → flow gates

The v1 loop is a Stop-hook gate pipeline (§1.3 of the analysis doc). In the flow, each
round ends with the same gates, re-expressed in Python and — where possible — upgraded
from heuristic to prover-checked:

| v1 gate (bash hook) | isabelle-humanize gate (flow Python) |
|---|---|
| Loop discovery / session binding | engine-handled (flow drives its own agents) |
| Background-task parking | engine-handled (async turns) |
| State schema validation (fail-closed) | pydantic validation of `state` each round |
| Branch consistency (`start_branch`) | keep verbatim if the workspace is a git repo (theory projects usually are) |
| Plan integrity (backup byte-compare) | goal-tracker IMMUTABLE section check, same byte-preservation rule |
| Incomplete-todo scan of transcript | drop — replaced by explicit round contract in `state` |
| Git cleanliness | keep (commit per round = reviewable diff, exactly as v1) |
| Large-file block (>2000 lines) | keep (theory files also rot) |
| Round summary + contract presence | same filenames, written into the cycle dir |
| **BitLesson delta validation** | **proof-memory delta validation** (§3.5) |
| Goal-tracker placeholder check (round 0) | same, plus: task `.thy` must contain exactly one target `theorem … sorry` hole (reuse `common/problems.py` regexes) |
| Max iterations → methodology analysis | same terminal taxonomy: `complete / cancel / maxiter / stop / unexpected` |

### 3.3 The termination upgrade: arbiter replaces Codex COMPLETE

v1's loop exits the implementation phase when Codex prints `COMPLETE`, then runs a
`codex review` phase. In the merge, **every round's stop-gate calls the arbiter**:

```python
from arbiter import check  # forked from MCP-comparison/common/arbiter.py
verdict = check(problem, final_thy_path, gym_url)  # isabelle build ∧ no sorry ∧ theorem present
if verdict["solved"]:
    return finalize(state, reason="complete")
```

This is the single biggest simplification the merge buys: v1's entire review phase,
severity-marker scanning (`[P0]`–`[P9]`), and DONE-gate nudge machinery collapse into
one ground-truth call. The reviewer LLM is **not** the termination authority — it steers
(§3.4). `check_done_readiness` (proof_finished ∧ sorry-free via MCP) stays as the cheap
pre-filter before paying for a full `isabelle build`.

### 3.4 The reviewer role: steering without termination authority

Per round, after the builder's chunk:

1. **Mechanical review (free)** — `isabelle_diagnostic_messages` + `isabelle_proof_state` +
   `isabelle_goal` before/after deltas. This alone is stronger than v1's Codex review.
2. **LLM review (steering)** — reviewer agent receives: the round's diff, the structured
   prover report, current subgoals, and the goal tracker. It must emit the mandatory
   verdict line, exactly as v1: `Mainline Progress Verdict: ADVANCED / STALLED /
   REGRESSED` + issues. Parse fail-closed (malformed → rerun review, as v1 does).
3. **Objective cross-check (new, research-grade)** — the flow independently classifies
   progress from prover signals (subgoal count/shape delta, new discharged goals,
   sledgehammer hits). Reviewer verdict vs objective signal are both logged; their
   agreement rate is a first-class metric (this is the "what does LLM review add when
   the prover is ground truth" measurement from the feasibility doc).
4. **Drift state machine, ported verbatim**: ADVANCED resets the stall counter;
   STALLED/REGRESSED increments; ≥2 → next builder prompt is the replan template
   (decompose lemma / try `isabelle_multi_attempt` candidates / escalate sledgehammer);
   ≥3 → drift circuit breaker → `stop` terminal state.
5. **Open Question escalation** — reviewer raises "Open Question" → flow asks the
   `human` place via `asked()` (v1's awk-splice forcing `AskUserQuestion`, now a typed
   schema-ask).

### 3.5 Proof memory (BitLesson analog)

- KB file `proof-lessons.md` in the workspace, v1's entry schema adapted: `BL-<date>-<name>`
  with Scope / Goal Shape / Failing Approach / Working Approach / Evidence Round.
- Each round summary must contain a `## Lesson Delta` section (`Action: none|add|update`
  + concrete IDs) — enforced by the flow before the round may close, the direct port of
  `bitlesson-validate-delta.sh`.
- Selector routing (cheap model picks relevant lessons per task) maps to a `schema`-ask
  to a low-effort account of any backend — v1's `model-router.sh` becomes one
  `agent(prompt, schema=…)` call.

### 3.6 Artifact mapping (reproduction fidelity)

| v1 artifact (`.humanize/rlcr/<ts>/`) | Merge artifact (cycle dir + workspace) |
|---|---|
| `state.md` (YAML frontmatter) | cycle `state.json` + pydantic `RLCRConfig` |
| `goal-tracker.md` (IMMUTABLE/MUTABLE) | identical file, byte-preservation enforced by the flow |
| `round-N-{summary,contract}.md` | same names, in the cycle dir |
| `round-N-review-result.md` | reviewer verdict + prover report, JSON + rendered |
| `{complete,stop,maxiter,cancel,unexpected}-state.md` | cycle terminal event with the same five-way taxonomy |
| Codex run logs in `~/.cache/humanize/` | backend session logs, symlinked into the cycle by the engine |
| `humanize monitor` dashboard | hmz TUI Monitor + `hmz trace collect` → perfetto |

The reproduction deliverable includes a **conformance checker** script: given a finished
cycle, verify every v1 gate has a merge equivalent that fired (or is documented as
deliberately upgraded/dropped, per the §3.2 table).

---

## 4. Phased plan

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **0. Bring-up** (~days) | IsabelleGym Docker stack healthy; host-side LSP MCP smoke test (`isabelle_open` → edit file → `isabelle_diagnostic_messages` → `isabelle_goal`) passes; `mcp_server.app`→`mcp_stepwise_server.app` rename fixed in any config we copy; per-CLI MCP attach verified (Claude `.mcp.json`, codex `config.toml`, one more) | one theorem proved by a manually-driven CLI agent editing a `.thy` through the LSP MCP |
| **1. Prototype in IsabelleGym (option B)** (~1–2 wks) | Fork `run_isabellegym_lsp.py`: add reviewer `ModelClient`, verdict-line protocol, objective progress classifier, stall/breaker logic | RLCR-shaped run on `MCP-comparison/problems/` with arbiter-solved result; logs show verdicts + stalls |
| **2. The flow (option A)** (~2–3 wks) | `isabelle_rlcr` flowverse flow: roles, gates (§3.2), arbiter stop-gate, drift machine, human escalation; `.mcp.json` workspace template | same problems solved through `hmz exec -f isabelle_rlcr:rlcr -a <builder> -a <reviewer>`; conformance checker green |
| **3. Memory + finalize** (~1 wk) | proof-lessons KB + delta gate; finalize phase + sanitized methodology retrospective (v1 parity) | KB entries cited across rounds; methodology artifact produced |
| **4. Swarm + ablation** (~2–4 wks, compute-bound) | candidate fan-out via `isabelle_multi_attempt`/`verify_batch` with reviewer ranking; controlled ablation: single-agent vs RLCR vs RLCR+memory, identical budgets, AFP-derived + `HOL_corpus` problems | results table: `arbiter_solved` pass@1, wall/prover/model split, reviewer-agreement metric |

Phases 0–2 are the merge; 3–4 are the research payoff.

---

## 5. Risks and open questions

- **Per-CLI MCP parity** — the plan assumes each role's CLI can attach a stdio MCP
  server with env vars. Claude and codex can; verify kimi/others in phase 0 before
  assigning them roles. hmz's `hushed()` strips account-readable env vars at spawn —
  confirm `PYTHONPATH`/`ISABELLE_MCP_*` survive (they live in `.mcp.json`'s `env`, which
  is read by the CLI, not inherited — should be safe, but test).
- **Two roles, one pool** — builder and reviewer each bind the same canonical file path
  through their own MCP connections, hence separate pool sessions; both re-sync from
  the same on-disk `.thy`. The LSP MCP never writes, so the reviewer cannot corrupt the
  builder's proof even if it misbehaves; `isabelle_multi_attempt` candidates run on
  separate scratch sessions. Session-pool sizing (`ISABELLE_POOL_SIZE` vs 24 GB
  container, ~1.5–2.5 GB per session) bounds how many roles + swarm candidates run
  concurrently.
- **Model pinning** — reproduction claims need pinned model ids (v1 defaults
  `gpt-5.5:high` etc.); record ids in the cycle config exactly as `MCP-comparison`
  records `model_id` per result.
- **Arbiter cost** — `isabelle build` per round is too slow; keep v1's shape (cheap
  MCP-level gates per round, arbiter only on claimed completion and at finalize). The
  900 s arbiter timeout governs terminal latency, not per-round latency.
- **Scope discipline** — do not modify IsabelleGym's server core or hmz's layers; the
  merge lives in a flowverse flow + a forked arbiter + workspace config, per both
  projects' own extension rules (MCP servers are "strictly additive layers"; flows are
  "content").

---

## 6. Summary

The merge is not a code merge — it is a **role merge**: humanize2 supplies the loop
engine, the roles, the artifacts, and the observability; IsabelleGym supplies the
executor, the reviewer-grade feedback, and the only termination verdict that matters.
The integration surface is exactly three files' worth of new content: a flowverse flow
(`isabelle_rlcr`), a workspace `.mcp.json` template, and a forked `arbiter.py` — plus
the conformance checker that proves the reproduction is faithful.

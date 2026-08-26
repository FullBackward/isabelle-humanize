# Isabelle-Humanize — Full Implementation Plan (Option A)

Status: **supersedes the phasing in `merge-plan.md` §1/§4.** Option B (prototype inside
IsabelleGym's `MCP-comparison/`) is **abandoned**; we go straight to option A — RLCR as
a humanize2 flowverse flow driving MCP-attached CLI agents against IsabelleGym.
`merge-plan.md` remains the architecture rationale (option comparison, bridge
mechanics, gate mapping); this document is the build plan.

Design sources: `humanize-analysis.md` (v1 RLCR mechanics, v2 engine),
`isabelle-humanize-feasibility.md` (substrate + novelty), `merge-plan.md` (LSP-MCP
decision, role partitioning).

---

## 1. Deliverable and definition of done

**Deliverable:** the `isabelle-humanize` repo becomes a **flowverse** (a git repo with
`flows/` at its root) shipping the flow `isabelle_rlcr`, runnable as:

```sh
hmz exec -f isabelle-humanize/isabelle_rlcr:rlcr \
    -a claude/claude-opus-4-8:high -a codex/gpt-5.6-sol:high \
    "prove the pending theorem in ./workspaces/<task>/"
```

**Done when:** on a held-out set of single-theorem `.thy` problems, the loop reaches
arbiter-verified completion (`isabelle build` passes, no `sorry`/`oops`, target theorem
present) within budget; the cycle contains the full v1-equivalent artifact family; the
conformance checker (§7) is green; and a single-agent ablation arm on identical
problems/budgets exists for comparison.

**Hard constraints (from both upstream projects' own rules):**

- No modifications to IsabelleGym's server core or MCP servers — the MCPs are
  "strictly additive layers"; we consume them as-is.
- No modifications to hmz — flows are "content"; everything lives in the flowverse.
- The flow's only hmz import is `hmz.flows` (SPEC rule); **no new third-party
  dependencies** — flow-side code is stdlib-only (`urllib`, `json`, `re`,
  `subprocess`, `pathlib`). Agent-side power comes from the CLIs' own MCP attachment.

---

## 2. Target repo layout

```
isabelle-humanize/
├── flows/
│   └── isabelle_rlcr/
│       ├── __init__.py          # @flow entry, round loop, role orchestration
│       ├── gates.py             # v1 Stop-hook gate pipeline, re-expressed in Python
│       ├── arbiter.py           # stdlib bigstep client (fork of common/arbiter.py logic)
│       ├── progress.py          # objective proof-progress classifier
│       ├── memory.py            # proof-lessons KB (BitLesson analog) + delta validation
│       ├── schemas.py           # pydantic models: RLCRConfig, Verdict, Readiness, state
│       └── prompts/             # ported v1 prompt-template/, Isabelle-adapted
│           ├── round0.md  next_round.md  drift_replan.md
│           ├── review.md  finalize.md    methodology.md
│           └── lesson_delta.md
├── workspaces/
│   └── template/
│       ├── .mcp.json            # LSP MCP attach (builder+reviewer shared)
│       ├── goal-tracker.md      # IMMUTABLE/MUTABLE sections (v1 format)
│       └── problem.thy          # single-theorem file with `sorry` hole
├── tools/
│   ├── smoke_lsp.py             # phase-0 MCP smoke test (no hmz involved)
│   ├── conformance_check.py     # cycle → v1-gate-equivalence checker
│   └── ablation.py              # runs single-agent arm on identical problems
├── tests/                       # pytest, pure-function tests (no live agents)
├── humanize-analysis.md         # docs (existing)
├── isabelle-humanize-feasibility.md
└── merge-plan.md
```

Flow loading note: hmz loads a flow with `runpy.run_path` and the flow's own directory
on `sys.path`, so sibling imports (`gates`, `arbiter`, …) work; the flow is re-read
fresh on every run, so edits between runs take effect immediately.

---

## 3. Architecture: who talks to Isabelle how

Two channels, kept strictly separate:

| Channel | Consumer | Mechanism | Used for |
|---|---|---|---|
| **MCP (LSP server)** | builder & reviewer agents | each CLI attaches `mcp_lsp_server` via workspace `.mcp.json` | all *agent* prover interaction: edit-file + `isabelle_diagnostic_messages`, `isabelle_goal`, `isabelle_proof_state`, `isabelle_query`, `isabelle_sledgehammer`, `isabelle_multi_attempt`, checkpoints |
| **REST (gym HTTP)** | the flow itself | stdlib `urllib` against `http://localhost:8000` | the **arbiter** (one-shot stateless `verify_bigstep_text`-equivalent) — no session, no lease, no MCP client needed |

The reviewer additionally returns *observed* prover state (subgoals before/after,
`proof_finished`, sorry-free) inside its structured verdict (§5.3), so the flow needs
no MCP client of its own. This keeps the flow stdlib-only and each concern in one
place: agents act, the flow judges.

`.mcp.json` (workspace template; env carries what the server needs, read by the CLI —
not subject to hmz's `hushed()` stripping, but verified in M0):

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

Role enforcement is structural: the LSP MCP never writes files. The **builder** edits
`problem.thy` with its CLI's native Edit/Write; the **reviewer** is given no file-write
capability in its prompt and cannot mutate through the MCP — worst case it calls
`isabelle_multi_attempt`, which runs on isolated scratch sessions.

---

## 4. Workstream 0 — infrastructure bring-up (M0, ~1 week)

No flow code until every item is green. This replaces the derisking that option B would
have provided.

1. **IsabelleGym stack** — in `/Users/winstonren/GitHub/IsabelleGym`:
   `docker compose up -d --build`; start the API with
   `docker compose exec isabelle-gym python -m server.app.main` (plain `exec`, not
   `bash -lc`); verify `curl http://localhost:8000/healthz`.
2. **Host-side LSP MCP** — `pip install "mcp<2" httpx` in a host venv; launch
   `PYTHONPATH=. python -m mcp_lsp_server.app`; `tools/smoke_lsp.py` (a minimal MCP
   stdio client, stdlib + `mcp` package) drives: `isabelle_open(problem.thy)` → append
   a proof step on disk → `isabelle_diagnostic_messages` → `isabelle_goal` →
   `isabelle_sledgehammer` → `isabelle_multi_attempt` with 2 candidates. Exit criterion:
   all return structured results; a trivially true theorem (`lemma True by simp`) is
   closed end-to-end.
3. **Per-CLI MCP attach matrix** — for each candidate role CLI: Claude (`.mcp.json`),
   codex (`config.toml` `[mcp_servers]`), kimi (native equivalent). Manually drive one
   proof step through each. Any CLI that cannot attach a stdio MCP with env vars is
   excluded from role assignment now, not mid-build.
4. **hmz spawn-environment check** — run `hmz exec -f builtin/chat -a <cli> "list your
   MCP tools"` in the template workspace; confirm the isabellegym tools appear (i.e.
   `PYTHONPATH`/`ISABELLE_MCP_LSP_*` from `.mcp.json` `env` survive hmz's spawn path).
5. **Arbiter endpoint check** — `tools/smoke_lsp.py --arbiter` POSTs a known-good and a
   `sorry`-containing theory text to the bigstep endpoint; expect `theory_verified`
   true/false respectively. Records the exact request/response shape that
   `flows/isabelle_rlcr/arbiter.py` will reimplement with stdlib.
6. **Model pinning** — freeze builder/reviewer model ids + efforts in
   `workspaces/template/README`; record in cycle config at run time (reproduction
   claims need pinned ids, same discipline as `MCP-comparison`'s `model_id`).

---

## 5. Workstream 1 — the flow (M1–M3)

### 5.1 Signature and config (`flows/isabelle_rlcr/__init__.py`, `schemas.py`)

```python
class Roles(NamedTuple):
    builder: Agent     # file-editing CLI + LSP MCP
    reviewer: Agent    # different model family; LSP MCP read-only usage
    human: Person      # open-question escalation; plan quiz at round 0

@flow(name="rlcr", resumable=True)
def run(agents: Roles, task: str, config: RLCRConfig, state: RLCRState) -> None: ...
```

`RLCRConfig` (pydantic; the TUI renders its settings menu from it) — v1's 23 flags,
Isabelle-adjusted:

| Field | Default | From |
|---|---|---|
| `max_iterations` | 42 | v1 |
| `full_review_round` | 5 | v1 (full-alignment rounds re-check the *whole* goal tracker) |
| `drift_stall_replan` / `drift_stall_breaker` | 2 / 3 | v1 state machine |
| `arbiter_timeout_s` | 900 | `MCP-comparison` arbiter |
| `builder_timeout_s` / `reviewer_timeout_s` | 600 / 300 | new (turn-level) |
| `bitlesson_required` | true | v1 |
| `push_every_round` | false | v1 |
| `ask_open_questions` | true | v1 `ask_codex_question` |
| `model_builder` / `model_reviewer` | recorded only | pinning |

`RLCRState` (resumable via the cycle's `state.json`): `current_round`,
`review_started`, `mainline_stall_count`, `last_verdict`, `drift_status`,
`goal_tracker_hash`, `lesson_kb_version`, `terminal: Optional[Literal[complete,
cancel, maxiter, stop, unexpected]]` — the v1 `state.md` frontmatter, typed.

### 5.2 Round loop (explicit `while`, not a STOP-hook replica)

v1 drives iteration by blocking Claude's exit; the flow equivalent is an explicit loop
calling `builder(prompt)` once per round — same semantics, no hook trickery (this is
exactly `builtin/ralph_loop`'s shape). One round:

1. **Pre-gates** (`gates.py`, fail-closed, cheapest first): branch consistency;
   goal-tracker IMMUTABLE section byte-preserved; workspace git clean except
   `problem.thy` + artifacts; round contract present; lesson-delta from last round
   valid; `current_round < max_iterations`.
2. **Builder turn** — `builder(prompt, suppress=True)`. Round-0 prompt =
   `prompts/round0.md` (goal tracker init + plan quiz for the human place, porting
   v1's "Begin with the End in Mind"); later rounds = `next_round.md` with the
   reviewer verdict spliced verbatim between v1-style markers. The builder prompt
   embeds the Isabelle repair tactics harvested from `MCP-comparison`'s segment prompt
   (segmented submission; never hand-write external-solver calls — escalate to
   `isabelle_sledgehammer` after two failures on the same subgoal; timeout ⇒
   stuck-line diagnosis; `pending_qed` ⇒ submit bare `qed`; never `sorry`).
3. **Mechanical gate** — the reviewer agent (not the flow) is schema-asked for
   readiness: `Readiness{proof_finished: bool, sorry_free: bool, diagnostics: list,
   goals_summary}` based on its MCP queries. Cheap pre-filter before any LLM critique.
4. **Reviewer turn** — §5.3.
5. **Drift state machine** — verdict ADVANCED ⇒ `stall=0`; STALLED/REGRESSED ⇒
   `stall+=1`; `stall ≥ drift_stall_replan` ⇒ next builder prompt is
   `drift_replan.md` (must change strategy: decompose, `isabelle_multi_attempt`
   candidates, sledgehammer escalation); `stall ≥ drift_stall_breaker` ⇒ terminate
   `stop`.
6. **Arbiter gate** — on `Readiness.proof_finished ∧ sorry_free`, run
   `arbiter.check(workspace)` (§5.4). Solved ⇒ finalize phase. Not solved ⇒ the
   rejection reason becomes the next builder prompt (v1's DONE-rejection nudge,
   max 2 per round).
7. **Commit** — `git add -A && git commit -m "rlcr: round N (<verdict>)"` in the
   workspace (per-round diffs = reviewable history, v1's `BASE_COMMIT..HEAD`
   integral-context equivalent).
8. **Journal** — append one JSON line to the cycle log: round, verdict, objective
   progress delta (§5.5), tokens/latency if exposed, arbiter result.

Turn failures surface as `subprocess.CalledProcessError` ("a flow catches turns rather
than transports") — caught, journaled, retried once, then counted as a stalled round.

### 5.3 Reviewer protocol (structured, not text-parsed)

v1 parses a mandatory verdict line fail-closed from prose. hmz gives us structured
output (`agent(prompt, schema=…)` — native `--json-schema` where supported, heuristic
fenced-block extraction otherwise), so the reviewer returns:

```python
class Verdict(BaseModel):
    mainline: Literal["ADVANCED", "STALLED", "REGRESSED"]   # mandatory, v1 parity
    issues: list[str]                  # actionable, ordered by severity
    open_question: Optional[str]       # triggers human escalation
    observed_goals_before: list[str]   # from isabelle_goal — objective signal
    observed_goals_after: list[str]
    readiness: Readiness
```

Extraction failure ⇒ reviewer is re-asked once; second failure ⇒ round counts as
STALLED (fail-closed, v1's "malformed output blocks" rule). `open_question` set ∧
`ask_open_questions` ⇒ the `human` place is schema-asked before the next builder turn.

Reviewer prompt (`prompts/review.md`) receives: the round diff (`git show`),
diagnostics, goal tracker, and the last 3 verdicts (v1's "integral context"). It is
explicitly **not** the termination authority — the arbiter is (§5.4). Every full-review
round (`round % full_review_round == full_review_round - 1`) the reviewer must
re-audit the entire goal tracker against the current theory, not just the increment.

### 5.4 Arbiter (`arbiter.py`, stdlib-only)

Fork the *logic* of `MCP-comparison/common/arbiter.py:27-99`, not the code (it depends
on IsabelleGym's async client; we reimplement with `urllib.request`):

1. read final `problem.thy` from the workspace;
2. regex `\b(sorry|oops)\b` ⇒ unsolved;
3. target theorem name present (from the task spec) ⇒ else unsolved;
4. POST to the gym bigstep endpoint (`verify_bigstep_text` shape recorded in M0.5),
   `arbiter_timeout_s` budget, deriving the parent session from dotted imports
   (`HOL-Computational_Algebra.X` → field `HOL-Computational_Algebra`);
5. `solved = theory_verified`. Exceptions ⇒ unsolved with typed error, never crash the
   loop.

### 5.5 Objective progress (`progress.py`)

Classify each round from the reviewer's `observed_goals_before/after` (and, when
empty, treat as UNKNOWN rather than trusting prose):

- **PROGRESS**: fewer open goals, or same count with strictly simpler goal text
  (heuristic: fewer `⋀`/`⟹` connectives), or new discharged helper lemmas.
- **STAGNATION**: identical goal set after a successful chunk.
- **REGRESSION**: more open goals / rollback to an earlier checkpoint.

Logged alongside the reviewer verdict; their agreement rate is the research metric
("what does LLM review add when the prover is ground truth"). The drift machine uses
the *reviewer* verdict (v1 parity); an ablation flag
`RLCRConfig.drift_source: Literal["reviewer", "objective", "either"]` switches it —
a one-line independent variable for the study.

### 5.6 Proof memory (`memory.py`)

- `proof-lessons.md` in the workspace; entry schema `BL-<yyyymmdd>-<name>`:
  Scope / Goal Shape / Failing Approach / Working Approach / Evidence Round (v1's
  BitLesson template, proof-adapted).
- **Delta gate**: every round summary must end with `Action: none|add|update` +
  concrete IDs existing in the KB + non-placeholder notes — validated in
  `gates.py` before the round may close (port of `bitlesson-validate-delta.sh`,
  including the fence/comment-aware section extraction).
- **Selector**: before each builder turn, a schema-ask to the *reviewer at low effort*
  (or a third cheap account) returns relevant lesson IDs; empty KB short-circuits to
  NONE without an LLM call (v1's `bitlesson-select.sh` short-circuit).

### 5.7 Finalize and termination

- **Finalize** (arbiter green): one optional `sorry`-free simplification pass
  ("functionality-equivalent only", v1 parity) → final arbiter re-check →
  `finalize-summary.md`.
- **Methodology retrospective** (unless `privacy=True`): reviewer writes a sanitized
  report — no theorem names, no file paths, no domain terms (v1's sanitization rules)
  → `methodology-analysis.md`.
- **Terminal taxonomy** (v1's five, recorded in state + cycle): `complete` (arbiter
  solved), `cancel` (human place answers "cancel" to an escalation), `maxiter`,
  `stop` (drift breaker), `unexpected` (gate corruption).

---

## 6. Workstream 2 — prompts and workspace template (M1–M2, parallel)

- Port v1 `prompt-template/claude/*.md` → `flows/isabelle_rlcr/prompts/`, keeping the
  `{{VAR}}` single-pass-substitution convention (re-implement the 20-line renderer in
  the flow; injected content must never be re-expanded).
- Isabelle-specific content comes from `MCP-comparison/run_isabellegym.py`'s
  `_segment_prompt_body` and `run_isabellegym_lsp.py`'s `_lsp_prompt_body` — the
  solver rule, escalation ladder, DONE criteria — re-worded for the two-role setting
  (builder) and critique setting (reviewer).
- `workspaces/template/`: `.mcp.json` (§3), `goal-tracker.md` (v1's exact section
  layout), `problem.thy` scaffold with the task spec as a header comment (theorem name,
  imports, field) so gates and arbiter read one source of truth.
- Problem intake: a `tools/import_problem.py` that converts `MCP-comparison`-format
  single-theorem `.thy` files (statement + `sorry`) into workspaces, reusing their
  regex conventions (`common/problems.py`).

---

## 7. Workstream 3 — conformance checker and ablation (M3)

`tools/conformance_check.py` — given a finished cycle dir, verifies the reproduction
is faithful. Every v1 gate must map to an observed flow behavior or a documented
upgrade/drop (the table in `merge-plan.md` §3.2 is the checklist, encoded as asserts):

- state schema validated each round (cycle log), goal-tracker byte-preservation
  checked, round artifacts present, large-file gate fired or N/A, lesson-delta gate
  fired per round, max-iteration gate, terminal state from the five-way taxonomy,
  arbiter invoked at least once before `complete`.

`tools/ablation.py` — runs the **single-agent arm**: same flow with
`reviewer = builder` replaced by a no-critique self-check (mechanical gate + arbiter
only), identical problems/budgets/model. This is the control group that makes the
RLCR claim measurable. Metrics per run (aligned with `MCP-comparison`
`AttemptResult` so numbers are comparable with their baselines): solved (arbiter),
rounds, wall/prover/model time split, token usage, stall count, reviewer-agreement
rate.

---

## 8. Workstream 4 — tests

`tests/` (pytest, no live agents, no live Isabelle — fast and CI-able):

- `test_gates.py` — each gate accepts/rejects fixture workspaces (dirty git, tampered
  IMMUTABLE section, missing delta, oversized file, bad schema).
- `test_arbiter.py` — request construction + response parsing against recorded M0.5
  fixtures; sorry regex; theorem-presence check; timeout/exception paths.
- `test_progress.py` — goal-delta classification fixtures (PROGRESS/STAGNATION/
  REGRESSION/UNKNOWN).
- `test_memory.py` — delta-section extraction incl. fence/comment edge cases; ID
  existence enforcement; empty-KB short-circuit.
- `test_verdict_parse.py` — schema-ask extraction failure → re-ask → STALLED path.
- `test_template_render.py` — single-pass substitution; `{{` in injected content is
  not re-expanded.
- End-to-end: manual `hmz exec` runs (real agents cost tokens — same convention as
  hmz's own `--run-agents` gate), one per milestone on 3 smoke problems.

---

## 9. Milestones

| | Deliverable | Exit criterion |
|---|---|---|
| **M0** (~1 wk) | Workstream 0 complete | smoke matrix green: MCP attach per CLI, arbiter round-trip, hmz env survival |
| **M1** (~1–2 wks) | **Vertical slice**: flow with builder only (reviewer = mechanical readiness gate + arbiter), round loop, pre-gates, journaling | 3 smoke problems solved end-to-end via `hmz exec`; cycle artifacts complete |
| **M2** (~2 wks) | Full RLCR: reviewer role, `Verdict` schema, drift machine, human escalation, full-review rounds | verdicts logged with fail-closed behavior demonstrated; drift breaker fires on a seeded stall |
| **M3** (~1 wk) | Memory KB + delta gate; finalize + methodology phases; conformance checker; ablation tool | conformance green on M2 cycles; ablation arm runs |
| **M4** (2–4 wks, compute-bound) | Study: RLCR vs single-agent vs `drift_source=objective`, on AFP-derived + `HOL_corpus` problems; optionally swarm via `isabelle_multi_attempt` candidate ranking | results table (solved pass@1, time split, tokens, agreement rate) |

M1 exists *because* option B was dropped — it is the derisking milestone, inside the
target architecture instead of a throwaway prototype.

---

## 10. Risks (updated for no-B)

- **Integration surprises land in the target architecture** (previously B's job) —
  mitigated by M0's smoke matrix and M1's single-agent slice; nothing in M2+ is
  started before M1 is green.
- **Per-CLI MCP parity** — a CLI that can't attach stdio MCP shrinks the role
  assignment matrix; checked in M0.3, not mid-build.
- **Schema-ask portability** — verdict-by-schema degrades to heuristic extraction on
  backends without native structured output; the re-ask-then-STALLED rule bounds the
  damage, and M2 includes a parsing-robustness test across the two chosen CLIs.
- **Session pool memory** — builder + reviewer + `multi_attempt` scratch sessions
  against a 24 GB container (1.5–2.5 GB/session): keep `ISABELLE_POOL_SIZE` ≤ 6 and
  `ISABELLE_MCP_LSP_SCRATCH_POOL_SIZE` ≤ 4 until M4.
- **Reviewer overreach** — reviewer has no write path (structural), but a reviewer
  *prompted* into trying to fix proofs wastes turns; the review prompt forbids
  proof-text output, and readiness/goals must come from MCP observations, not prose.
- **Upstream drift** — IsabelleGym has no CI and an in-flight rename; pin the known-good
  commit in `workspaces/template/README` and record it in every cycle.

---

## 11. What this plan deliberately does not build

- No fork of IsabelleGym server/MCP code, no fork of hmz (§1 constraints).
- No new arbiter — the neutral judge stays `isabelle build` semantics exactly as
  `MCP-comparison` defines them, so results remain comparable with their four-server
  baselines.
- No training/RL — this is an inference-time harness; the metrics are designed so an
  RL follow-up could consume the same journals.

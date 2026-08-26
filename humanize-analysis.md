# Humanize v1 & v2 — Achievements and Implementation Mechanisms

An analysis of the two generations of the `humanize` project, based on close reading of
both repositories:

- **v1** — `/Users/winstonren/GitHub/humanize` (version 1.16.0): a Claude Code plugin,
  originated at UCLA PolyArch (`PolyArch/humanize`), derived from the GAAC
  (GitHub-as-a-Context) project.
- **v2** — `/Users/winstonren/GitHub/humanize2` (package `hmz`): a ground-up Python
  rewrite at `humanfia/humanize2`, contributed to by NVIDIA Research, MIT HAN LAB,
  NUNCHAKU and community members.

File paths below are relative to the respective repository root.

---

## Part 1 — Humanize v1: the RLCR plugin

### 1.1 What it achieves

Humanize v1 turns Claude Code from a one-shot code generator into an **iterative,
externally-reviewed development loop**. Its core concept is **RLCR — "Ralph-Loop with
Codex Review"** (also read as *Reinforcement Learning with Code Review*): Claude
implements, Codex independently reviews, issues feed back into implementation, and the
loop runs until all acceptance criteria pass — with hard limits, drift detection, and
human checkpoints throughout.

The design philosophy stated in the README:

- **Iteration over perfection** — catch issues early in continuous feedback loops rather
  than expecting perfect one-shot output.
- **One build + one review** — two different model families (Claude and Codex) so neither
  grades its own homework.
- **Begin with the End in Mind** — before the loop starts, the *human* must pass a quiz
  on the plan. The human remains the architect.

Around the loop, v1 ships a full pipeline of supporting commands:

| Command | Achievement |
|---|---|
| `/humanize:gen-idea` | Expands a loose thought into an evidence-grounded idea draft via N (default 6) parallel read-only "directed swarm" Explore subagents, each returning `APPROACH_SUMMARY` / `OBJECTIVE_EVIDENCE` / `KNOWN_RISKS` / `CONFIDENCE`, with an anti-fabrication sentinel (`exploratory, no concrete precedent`). |
| `/humanize:gen-plan` | Converts a draft into a strict-schema plan (acceptance criteria `AC-X` with positive/negative TDD tests, path boundaries, task breakdown tagged `coding`/`analyze`), using a Codex first-pass risk analysis and a Claude↔Codex **convergence loop** (`AGREE:`/`DISAGREE:`/`REQUIRED_CHANGES:`, max 3 rounds) before user sign-off. |
| `/humanize:refine-plan` | Consumes human review annotations (`CMT:`…`ENDCMT`, `<cmt>`, `<comment>`) via a stateful scanner, classifies each as question/change/research request, and rewrites the plan atomically with a QA ledger of dispositions. |
| `/humanize:start-rlcr-loop` | Runs the loop proper (below). |
| `/humanize:ask-codex`, `/humanize:ask-gemini` | One-shot consults; Gemini is mandated to use Google Search before answering. All invocations are journaled under `.humanize/skill/<id>/`. |
| `humanize monitor …` | A live terminal dashboard (state parsing + log tailing) for watching a loop from a second terminal. |

### 1.2 Architecture: a program written in markdown and bash

v1 is a standard Claude Code plugin (`/.claude-plugin/plugin.json`, marketplace
`PolyArch`). Its architecture has a clear separation of concerns:

- **`commands/*.md`** — slash commands whose markdown bodies are *programs in natural
  language* that the Claude model executes step by step. Hard logic is delegated to
  whitelisted bash scripts via `allowed-tools` frontmatter (e.g.
  `commands/start-rlcr-loop.md:5` allows only the setup script, `Read`, `Task`,
  `AskUserQuestion`).
- **`hooks/hooks.json`** — six hooks that intercept Claude Code lifecycle events; this is
  where the loop actually lives (§1.3).
- **`scripts/` + `hooks/lib/`** — ~19 shell scripts plus shared libraries, including
  `loop-common.sh` (1578 lines of state parsing, loop discovery, block-message
  rendering).
- **`prompt-template/`** — `{{VAR}}` templates for every generated prompt, rendered with
  **single-pass substitution** so injected content can never be re-expanded
  (`hooks/lib/template-loader.sh:52-55`).
- **`agents/*.md`** — subagent personas (plan-compliance checker, quiz generator,
  bitlesson selector).
- **`skills/`** — ports of the same workflows to Codex CLI and Kimi CLI.
- **`tests/`** — ~45 parallel shell test suites including 16 `robustness/` suites (state
  corruption, hook-input fuzzing, cancel-security, path traversal).

Runtime state lives in the *target project's* `.humanize/` directory (gitignored by
hook enforcement): `rlcr/<timestamp>/` per loop, `skill/` per consult, `ideas/`,
`bitlesson.md`, `config.json`. Debug logs go to `~/.cache/humanize/…` so the agents
never read them.

### 1.3 The RLCR loop mechanism

#### Startup gates

`/humanize:start-rlcr-loop docs/plan.md` runs two LLM-driven pre-flight checks *before*
any state is created (`commands/start-rlcr-loop.md`):

1. **Plan Compliance Pre-Check** — a sonnet subagent verifies the plan is relevant to the
   repo and contains no branch-switch instructions (the loop requires a constant working
   branch). Output is strictly `PASS:`/`FAIL_RELEVANCE:`/`FAIL_BRANCH_SWITCH:`; parsing
   is **fail-closed** — malformed output aborts.
2. **Plan Understanding Quiz** — an opus subagent generates 2 multiple-choice questions
   from the plan; the human answers via `AskUserQuestion`. Advisory, not blocking; skipped
   by `--skip-quiz`/`--yolo`.

Then `scripts/setup-rlcr-loop.sh` (1541 lines) performs hard validation: dependencies
(`codex`, `jq`, `git`), one-active-loop mutual exclusion, aggressive plan-path hardening
(no absolute paths, symlinks, `..`, submodules, shell metacharacters), plan content
minimums, a clean-git-tree gate, base-branch detection with a captured `BASE_COMMIT` SHA,
and finally creates the loop state:

```
.humanize/rlcr/<YYYY-MM-DD_HH-MM-SS>/
├── state.md            # YAML frontmatter: current_round, max_iterations (42),
│                       # codex_model/effort, review_started, mainline_stall_count,
│                       # drift_status, agent_teams, bitlesson_required, ...
├── goal-tracker.md     # IMMUTABLE: Ultimate Goal + Acceptance Criteria;
│                       # MUTABLE: Active Tasks ([mainline]/[blocking]/[queued] lanes),
│                       # Completed and Verified, Plan Evolution Log
├── plan.md             # backup of the plan (integrity-checked each round)
├── round-0-summary.md  # scaffold, includes a "## BitLesson Delta" section
└── round-0-prompt.md   # printed to stdout → enters Claude's context
```

#### The loop driver is a Stop hook

There is **no external loop process**. The entire loop is `hooks/loop-codex-stop-hook.sh`
(2221 lines), registered as a Claude Code `Stop` hook with a 7200 s timeout. Every time
Claude tries to end its turn, the hook either exits silently (turn ends) or prints
`{"decision":"block","reason":...}` — and the `reason` becomes Claude's next instruction.
Re-entry is deliberately not guarded: *re-entry is the iteration mechanism*
(`loop-codex-stop-hook.sh:30-36`).

On each stop attempt the hook runs a 13-stage gate pipeline, cheapest first:

0. **Loop discovery** — find the active loop bound to this Claude session (session
   binding is established by a PostToolUse hook that patches `session_id` into
   `state.md`, so team members and foreign sessions aren't gated).
1. **Background-task guard** — if Claude is just waiting on background tasks, allow the
   stop and "park" the loop (avoids burning Codex tokens on no-op stops).
2. **Phase detection** — the state *filename* is the phase:
   `state.md` (implementation) → `finalize-state.md` → `methodology-analysis-state.md` →
   `{complete,stop,maxiter,cancel,unexpected}-state.md`.
3. **State schema validation** — fail-closed on missing/outdated fields, with upgrade
   instructions.
4. **Branch consistency** — `HEAD` must equal `start_branch`.
5. **Plan integrity** — backup exists, tracked plan unmodified, content matches backup.
6. **Incomplete tasks** — transcript is scanned for unfinished todos; blocks *without*
   spending a Codex call.
7. **Git cleanliness** — uncommitted changes block the round.
8. **Large files** — any changed file >2000 lines blocks with "split into smaller
   modules".
9. **Summary & round contract presence** — `round-N-summary.md` and
   `round-N-contract.md` must exist.
10. **BitLesson delta validation** — see §1.5.
11. **Goal tracker initialization** (round 0) — no placeholder text allowed in Ultimate
    Goal / Acceptance Criteria.
12. **Max iterations** — default 42 rounds, then methodology analysis and `maxiter` exit.

#### Implementation-phase review (`codex exec`)

If all gates pass, the hook renders a review prompt from templates and runs:

```bash
printf '%s' "$PROMPT" | codex exec -m gpt-5.5 -c model_reasoning_effort=high \
  --full-auto -C "$PROJECT_ROOT" -
```

Every fifth round (configurable) is a **Full Alignment Review** against the whole plan;
other rounds review the increment. Prompts embed the plan, Claude's round summary, and an
"integral context" section (`git log BASE_COMMIT..HEAD` plus the last three rounds of
summaries/reviews). Codex must end with a mandatory verdict line — `Mainline Progress
Verdict: ADVANCED / STALLED / REGRESSED` — and a final line of `COMPLETE`, `STOP`, or
issues.

The verdict drives a **drift state machine**: ADVANCED resets the stall counter;
STALLED/REGRESSED increments it; ≥2 stalls → `drift_status: replan_required` (next prompt
is a replanning template); ≥3 → a **drift circuit breaker** stops the loop. Issues are
spliced verbatim into the next round's prompt between
`<!-- CODEX's REVIEW RESULT START/END -->` markers. If the review raises an "Open
Question", an awk splice forces Claude to `AskUserQuestion` the human before proceeding.

#### Review phase (`codex review`)

When Codex declares `COMPLETE`, `review_started=true` is set and the loop switches
protocol: every subsequent stop runs `codex review --base "$BASE_COMMIT"` — a native
diff review against the fixed base SHA captured at loop start. Issues are detected by
scanning the last 50 log lines for **severity markers** `[P0]`–`[P9]` at line start; any
hit extracts the findings into `round-N-review-result.md` and blocks Claude into a
fix round. A clean review enters the finalize phase.

#### Finalize, methodology analysis, termination

- **Finalize** — one optional refactoring pass ("functionality-equivalent only"), then
  `finalize-summary.md`.
- **Methodology analysis** — unless `--privacy`, an Opus agent must write a *sanitized*
  retrospective (no paths, identifiers, code, or domain terms) — distilling reusable
  methodology before the loop may end. During this phase the Write/Edit/Bash validator
  hooks refuse virtually all other file mutations.
- **Termination** — five terminal states: `complete` (review passed), `cancel` (user ran
  the cancel command; the bash validator painstakingly distinguishes the authorized
  `mv` from cheating), `maxiter`, `stop` (Codex/drift circuit breaker), `unexpected`
  (corrupted state).

#### Anti-gaming hooks

Because the loop's correctness depends on Claude *not* rewriting its own constraints, v1
ships a gauntlet of PreToolUse validators (`hooks/hooks.json`):

- **Write/Edit validators** — state files, prompt files, plan backup, and wrong-round
  summaries are read-only to the agent; the goal tracker's IMMUTABLE section must be
  byte-preserved (verified via a perl-based edit preview).
- **Bash validator** (573 lines) — blocks `>`, `>>`, `tee`, `sed -i`, `mv`, `cp`, `rm`,
  `dd`, `perl -i` against protected files; splits chained commands on `;`/`&&`/`||`/`|`;
  sees through `sh -c`, `sudo`, `env`, `timeout` wrappers; blocks `git push` (unless
  `--push-every-round`), `git add .humanize`, and direct execution of the stop hook
  itself.
- **Read validator** — blocks reading stale round files.

All block messages render from 40+ `prompt-template/block/*.md` templates with graceful
fallbacks.

### 1.4 Config, monitoring, and the Bitter Lesson workflow

- **Config** — four layers merged with jq recursive `*` (plugin defaults →
  `~/.config/humanize/config.json` → project `.humanize/config.json` → CLI flags),
  validated per key (`scripts/lib/config-loader.sh`).
- **Monitoring** — `scripts/humanize.sh` (sourced into the user's shell) polls `state.md`
  and `goal-tracker.md` every 2 s, rendering an 11-line `tput` status bar plus tails of
  the latest Codex logs.
- **BitLesson (Bitter Lesson workflow)** — cross-round project memory at
  `.humanize/bitlesson.md`. Entries are strictly templated (`BL-YYYYMMDD-name`:
  Scope/Problem/Root Cause/Solution/Constraints/Validation Evidence). Before each task a
  **selector** (a cheap model — haiku or codex-low, routed by `model-router.sh`) picks
  relevant lessons; every round summary must contain a `## BitLesson Delta` section
  (`Action: none|add|update` + concrete lesson IDs), enforced by
  `scripts/bitlesson-validate-delta.sh` from the stop hook. The intent: problems that took
  multiple rounds to solve must be crystallized so future rounds are forced to consult
  them.

### 1.5 Assessment of v1

v1's achievement is demonstrating that a **reliable autonomous development loop can be
built with no runtime at all** — just lifecycle hooks, shell scripts, and prompt
engineering. Its weaknesses are the flip side of the same choice: 2200-line bash hook
scripts are fragile and hard to extend; the loop is bound to Claude Code + Codex
specifically; multi-agent parallelism is limited to Claude Code's experimental agent
teams; and observability is log-tailing rather than structured tracing. v1.16.0's README
banner itself points users to Humanize2.

---

## Part 2 — Humanize2 (`hmz`): the Python rewrite

### 2.1 What it achieves

v2 generalizes v1 from a Claude Code plugin into a **backend-agnostic agent orchestration
system**: *"Orchestrate, execute, and observe agent flows."* Its headline achievements:

- **One engine, many agent CLIs** — drivers for `agy`, `claude`, `codex`, `grok`, `kimi`,
  `pi`, `qwen`, `opencode`, `mimo`, plus user-added ACP-protocol CLIs, plus a bundled
  **DeepSeek Harness** backend that needs no separate install.
- **Flows as user-writable content** — v1's fixed pipeline becomes *a directory of
  Python* that anyone can write, fork, and distribute through git "flowverses". v1 itself
  survives as a flow (`official/humanize1` — its three commands are three flows in one
  file).
- **A full TUI** (`hmz`) that mimics the Claude Code interface while driving any flow
  over any agents.
- **Real observability** — per-run cycle journals and Chrome-format traces viewable in
  ui.perfetto.dev, with process-tree profiling.
- **Remote execution** (`hmz anchor` / coganchor) — the agent runs locally but all its
  file I/O and subprocesses are transparently executed on a remote machine via
  seccomp/ptrace syscall interposition.
- **Accounts and providers** — named credential sets per backend with retry/fallback
  chains, environment isolation, and privacy-scrubbed opt-in telemetry.

### 2.2 Architecture and the SPEC methodology

`src/hmz/` is one package with strictly layered modules (`backends` → `models` →
`agents` → `flows`/`machines` → `runner` → `cli`/`tui`). **Layering is a hard rule** —
"No two layers MUST name each other" — enforced by `tests/test_layering.py`, which holds
an explicit allowed-import table and even verifies that `hmz anchor serve` loads only its
allowed modules in an empty `PYTHONPATH`.

The most unusual feature of v2's engineering is its **SPEC-driven development**: eight
normative `SPEC.md` files sit beside the code, written in RFC-style MUST/MUST NOT prose
where every rule states its *reason*. `AGENTS.md` forbids modifying SPECs unless
explicitly instructed — change the code to match the SPEC, or propose the SPEC change
separately. SPECs pin exact public signatures and invariants; tests encode them. Combined
with Conventional Commits, Standard Readme, whole-project pyright-strict and
ruff-`ALL` pre-commit gates, and coverage CI on Python 3.12–3.14, this is an unusually
disciplined methodology for an agent-tooling project — and it is itself a mechanism worth
copying (see the feasibility document).

### 2.3 The flow abstraction

A flow is **"a function marked with `flow`, and nothing else"** (`src/hmz/flows/SPEC.md`).
The decorator marks rather than wraps:

```python
@flow(resumable=True)
def run(agents: tuple[Agent], task: str, state: dict[str, Any]) -> None:
    (agent,) = agents
    while True:
        state["rounds"] = state.get("rounds", 0) + 1
        agent(task, suppress=True)   # one turn in a throwaway session
        time.sleep(5)
```

(`src/hmz/flows/builtin/ralph_loop/__init__.py` — the direct descendant of v1's loop.)

Key mechanisms:

- **"Reading a flow means running it"** — flows are loaded with `runpy.run_path` and
  surgically evicted from `sys.modules`, so a flow rewritten between runs (even by an
  agent it is driving) runs as it now is. This is why adding a flowverse is explicitly
  documented as trusting that repository with your machine.
- **What a flow drives is read off type annotations** — `get_type_hints` on the entry
  point; `agents` must be a fixed-length tuple or NamedTuple of `Agent`/`Person` places,
  with `Annotated` extras declaring requirements (`Goal`, `Remote`, `Isolated(image)`,
  hook `Moment`s) validated against the chosen agents *before the first turn* — "a run
  that cannot reach one MUST say so here rather than an hour into a loop".
- **Flowverses** — git repos cloned to `~/.humanize/flowverses/`; lookup order
  `local` → `user` → `official` → `builtin` (nearest-first shadowing), and `fork()` copies
  a flow into `.humanize/flows`. Only three flows are builtin (`chat`, `ralph_loop`,
  `stateful_ralph`) — deliberately just "the flows that show what a flow is".
- **Multi-agent coordination is the flow's Python, not the engine** — e.g.
  `official/flame_chase` (two agents alternate on one task, each reading the repo rather
  than a history) and `official/rlar` (actor/reviewer where the review *is* the actor's
  next prompt, word for word, and the reviewer declares completion).

### 2.4 The agent abstraction

`AgentBase`/`SessionBase` (`src/hmz/agents/base.py`) satisfy the flow-facing
`Agent`/`Session`/`Person` Protocols structurally — the flows layer never imports the
drivers, and the drivers never import the flows. The core surface:

- `agent(prompt, suppress=True)` — one turn in a throwaway session ("which is what a
  Ralph loop is made of"); every call has an async twin.
- `agent.new(cwd)` → a persistent `SessionBase` whose single primitive is `stream()` —
  a turn as an event stream ending in exactly one `result` event. A turn lock serializes
  turns per session.
- Two session families: `CommandSessionBase` (one subprocess per turn) and
  `StreamSessionBase` (one long-lived process spoken to line-at-a-time — which makes
  `interject`, mid-turn steering, possible).
- **Spec parsing** — `cli[@provider]/model:effort` (e.g. `claude/claude-opus-4-8:high`);
  effort ladders are per-backend vocabulary; model ids are never hardcoded — each backend
  is asked for its own catalogue.
- **Spawning** — subprocess argv lists (never shell strings) through a single choke point
  that appends provider args, rewrites credential paths, and `hushed()`-strips ambient
  env vars the backend would read an account from.
- **Permissions** — `permission` defaults to `"bypass"` (mapped per driver, e.g. Claude's
  `--dangerously-skip-permissions`); the README is blunt that nothing turns prompts back
  on, and remaining interactive permission requests are auto-answered unless a flow hook
  refuses — *"a flow watches its agent rather than gating it."*
- **Hooks** — `Moment` mirrors the CLIs' own hook names (`SessionStart`,
  `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `Stop`, …) as Python callables
  hung on a live agent. Crucially, **`Moment.STOP` refusal re-prompts the agent with the
  hook's text — which is exactly how v1's Stop-hook loop is re-expressed**: the
  `official/humanize1` flow "blocks the builder's exit and puts the round to the reviewer
  in a Stop hook; so does this, with a `Moment.STOP` hook" (`docs/reference/flows.md`).
- **Retry/fallback** — turn failures walk per-account retries (exponential+jitter by
  default, capped at one turn's length) then fall back to the next named account *inside
  the same conversation*; backend-declared `Unrecoverable` errors (e.g. context length)
  are never retried. "A flow catches turns rather than transports."
- **HumanAgent** — the person at the prompt, driven as an agent, so flows can mix human
  and AI places uniformly.

### 2.5 Observability and state

- **Cycle journal** — every run gets `~/.humanize/cycles/<workspace>/<when>-<which>/`
  with an event-per-line `cycle.jsonl`, `state.json` for resumable flows, `profile.jsonl`,
  and *symlinks* (not copies) to each backend's own session logs. Cycles record whose
  session each was and which account took its turns.
- **Tracing** — per-backend *readers* parse the CLIs' own session logs into a shared
  Session/Action model, group sessions into tracks per agent, and emit **Chrome JSON
  traces** for ui.perfetto.dev (`hmz trace collect`, traced by recorded session ids, not
  by directory). A psutil sampler profiles the whole process tree every 0.05 s —
  "MUST sample rather than intercept".
- **TUI** — a Textual app mimicking Claude Code's layout that drives the same `Runner`
  engine; slash commands (`/flow`, `/agents`, `/providers`, `/cycles`, `/status`, …), a
  three-stage ctrl+C gesture (warn → stop flow → kill conversations), and a `Monitor`
  that derives the run's handover graph purely from agent watch-events.

### 2.6 Remote execution and accounts

- **coganchor** — the agent process runs locally and unchanged under a seccomp+ptrace
  supervisor (Linux x86-64) that rewrites syscall path arguments so its file I/O,
  spawned commands, and their network happen on the target machine; the local workspace
  is a synced "shadow". Transports: `ssh://` (ships a self-contained zipapp),
  `docker://`, `tcp://`. The same interposition mechanism powers **providers** —
  credential paths are rewritten into an account directory, so one CLI binary can run
  under many isolated accounts. The security model is stated without euphemism: "an
  `hmz anchor` port is equivalent to a shell on that machine."
- **Telemetry** — sentry, opt-in asked once, with an explicit `SENT`/`KEPT` privacy
  contract and a `before_send` scrubber that strips paths, credentials, and command lines
  (which would contain the task).

### 2.7 Relationship to v1

v1 is not ported as code but **re-expressed as content**: the `official/humanize1` flow
in the flowverse re-implements gen-idea, gen-plan, and the RLCR loop, writing the same
`.humanize/rlcr/<timestamp>/` artifacts (state, goal-tracker, per-round
prompt/summary/contract/review), with every v1 flag becoming a pydantic field on the
flow's config model (23 of them). The mechanism mapping is direct:

| v1 mechanism | v2 mechanism |
|---|---|
| Stop hook blocks Claude's exit, injects next round | `Moment.STOP` hook on the builder agent |
| PreToolUse validators | `Moment.PERMISSION_REQUEST` / `PreToolUse` hooks |
| `codex exec` review subprocess | just another `Agent` place in the flow's tuple |
| Bash state files (`state.md`) | flow config (pydantic) + resumable `state.json` in the cycle |
| `humanize monitor` log tailing | cycle journal + Chrome traces + TUI Monitor |
| Hardcoded Claude+Codex | any of 10+ backends, mixed freely per flow |

What v2 deliberately keeps from v1: the loop philosophy (iteration over perfection), the
builder/reviewer separation, the human-as-architect gates, and the RLCR artifact format.
What it adds: the engine (typed flows, validation-before-first-turn, retry/fallback),
the ecosystem (flowverses, accounts), and the observation plane (cycles, traces, TUI).

---

## Part 3 — Comparative analysis

### 3.1 The shared insight

Both versions implement the same bet: **LLM output quality scales with structured
iteration and independent review better than with better single-shot prompting.** The
loop, not the model, is the product. Both also share the meta-principle that the human
must remain the architect (quizzes, open-question escalation, methodology
retrospectives).

### 3.2 Where the generations differ

| Dimension | v1 (plugin) | v2 (`hmz`) |
|---|---|---|
| Locus of control | Claude Code Stop hook (re-entry *is* the loop) | Python flow functions calling `agent(prompt)` in a `while` loop |
| Program medium | Natural-language command markdown + 2200-line bash hooks | Typed Python with pyright-strict and normative SPECs |
| Backends | Claude (build) + Codex (review) only | 10+ CLIs + bundled DeepSeek Harness; roles assignable per flow |
| Reviewer independence | Different vendor (OpenAI vs Anthropic) | Configurable — any agent place, including a human (`Person`) |
| State | YAML frontmatter in `.humanize/rlcr/*/state.md` | Cycle journal `state.json` + pydantic flow config |
| Integrity enforcement | Anti-gaming PreToolUse bash validators | Validation before first turn; hooks watch rather than gate |
| Parallelism | Experimental agent teams | Async flows, per-session parallelism, multi-agent flows |
| Observability | Log tailing dashboard | Structured traces (perfetto), profiling, TUI monitor |
| Distribution | Claude Code plugin marketplace | pip package + git flowverses |
| Trust model | Plugin runs in your Claude session | "A flow is a directory of Python, and reading one means running it" |

### 3.3 Mechanisms worth transplanting

For any new harness built in this lineage, the load-bearing mechanisms are:

1. **The loop driver as a gate, not a scheduler** — the reviewer decides whether the
   builder may stop. In v1 this is the Stop hook; in v2 a `Moment.STOP` hook; the
   pattern is what matters.
2. **Independent review with a mandatory machine-parseable verdict** — the
   `ADVANCED/STALLED/REGRESSED` line and the drift circuit breaker turn review prose
   into a control signal.
3. **Immutable goal state** — the goal tracker's IMMUTABLE section, byte-preserved by
   hook enforcement, is how long loops avoid objective drift.
4. **Fail-closed validation at every boundary** — malformed reviewer output blocks
   rather than degrades.
5. **Cross-round memory** — BitLesson (v1) and resumable cycle state (v2) prevent
   re-learning the same lesson per round.
6. **SPEC-driven development** (v2) — normative prose beside the code, with tests
   encoding invariants, is what made a 10-backend rewrite tractable.
7. **Observability by journal + trace** (v2) — if you cannot replay a run, you cannot
   improve the loop.

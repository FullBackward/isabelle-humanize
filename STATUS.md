# STATUS — what is built, what is validated, and where we deviated from the plan

Last updated: 2026-09-02 (M3 validation session: real ablation, conformance,
stale-resume fix). Read this before `implementation-plan.md`; the plan stays
the spec, this file is the truth about the code.

## Milestone state

| Milestone | State | Evidence |
|---|---|---|
| M0 bring-up | **Done** (kimi only) | `.m0/M0-report.md`; healthz green, LSP smoke green, arbiter fixtures in `tests/fixtures/arbiter/`, kimi attach + hmz spawn PASS |
| M1 vertical slice | **Done, validated** | 3/3 smoke problems (`smoke_true`, `smoke_conj`, `smoke_add0`) reached `terminal=complete` via `hmz exec`, each arbiter-verified (`build_strict`) |
| M2 full RLCR | **Done, validated once** | reviewer schema-verdicts, drift machine, escalation, full-review rounds all fired in the `m2_revrev` validation runs |
| M3 memory + finalize + tools | **Done, validated** | 113 unit tests green; real ablation executed and conformance green (below); finalize/methodology exercised live |

### M3 ablation (2026-09-02, kimi-k3 both roles, `/home/winst/ablation-runs/m3-conformance`)

| problem | arm | solved | terminal | rounds | wall_s |
|---|---|---|---|---|---|
| smoke_true | rlcr | ✓ | complete | 0 | 305 |
| smoke_true | solo | ✓ | complete | 0 | 223 |
| smoke_conj | rlcr | ✓ | complete | 0 | 267 |
| smoke_conj | solo | ✓ | complete | 0 | 190 |
| smoke_add0 | rlcr | ✓ | complete | 0 | 236 |
| smoke_add0 | solo | ✓ | complete | 0 | 227 |

Conformance checker: 7/7 PASS on every rlcr arm; 6 PASS + verdict-malformed
N-A (by design) on every solo arm. Smoke problems are trivial (round-0
solves), so rlcr-vs-solo separation is expected on real problems, not here —
this sweep validates the tooling, not the research claim.
| M4 study | **IMO 2026 P1 solved** | imo2026_p1 solved 2026-09-10 (below); putnam_2023_a1 solved 2026-09-04; full study not started |

### IMO 2026 P1 — SOLVED in one round (2026-09-10)

Fifth attempt overall; the first on the fully-fixed stack (persistent
streamable-http MCP, maxheap 9216, 20g container, sledgehammer-first +
looping-tactic ban + scratch discipline + reviewer ATP gate, readiness 600 s,
permission denies that fire). `terminal=complete` at **round 0**, arbiter
`build_strict` verified in 44 s, 1059-line theory with **all five sorries
discharged** (target `statement_a_termination` plus unique-large, invariance,
terminal-value, `Mval>1`), 0 stalls, conformance 7/7 PASS, 3 lessons. Wall
4 h 09 m (06:12→10:21 UTC), agent-active 248 min, ~$5.15 (peak window) —
cost-comparable to humanfia's Kimi-K3 IMO Q1 run ($5.73 / 87 min API on Lean).
Attempts 1–4 were the infrastructure R&D documented below (memory caps, MCP
stdio races, opencode permission semantics); their spend (~$8) is excluded
from the run figure. Archive: `runs/imo2026_p1-rlcr-deepseek/`.

### First real problem: Putnam 2023 A1 — SOLVED (2026-09-04)

4 attempts (watchdog-supervised, 5 then 15-min cadence), ~3 h 20 m wall
(17:06–20:25 UTC, incl. infra downtime), both roles
`opencode/deepseek/deepseek-v4-pro:high`, terminal `complete` at round 2,
arbiter `build_strict` verified, conformance 7/7 PASS. The final 311-line
proof is self-contained over `Complex_Main` (the builder dropped the
`HOL-Analysis.Derivative` import and re-derived the derivative machinery it
needed as local lemmas — kernel-accepted either way). KB: 5 lessons,
several reused across attempts (the memory mechanism visibly working).

Bugs the attempts surfaced, all fixed flow-side (125 tests green):
import/TASK-header mangling → arbiter builds file-aligned
(`dependencies: []`, parent session from current imports) + spec snapshot at
setup; PEP 3110 `UnboundLocalError` on double readiness failure;
readiness acquire passing stale spec imports (timeouts) → file-current
imports. Attempt 3 died to a WSL distro crash (SIGTERM), not the flow.

Post-solve hardening (2026-09-04, from the run analysis): the arbiter now
also enforces an **original-imports subset gate** — a builder may add
libraries but never remove one (stage `imports`; kills the "drop
HOL-Analysis and re-implement deriv locally" shortcut structurally), and
`round0.md` forbids re-implementing library constants, editing the TASK
comment, and ad-hoc side-channel build loops (feedback comes from the MCP;
first cold-heap diagnostics call is slow, wait for it). Answer-hint
scrubbing stays manual (owner's choice — no importer gate was built).

Prompt tooling refresh (2026-09-10, from the updated `mcp_lsp_server`
README in the Windows IsabelleGym clone `C:\Users\winst\GitHub\IsabelleGym`
— note the WSL clone's copy of that README is stale; the code itself is
content-identical between clones modulo line endings): builder prompts
(`round0.md`, `next_round.md`, `drift_replan.md`) now document
`isabelle_query` as the sanctioned in-prover library-search channel,
scratch-only experimentation (`isabelle_run_code`/`isabelle_multi_attempt`),
theories-at-acquire (imports beyond `Main` resolve on their own), and
1-based tool positions. New hard rule (owner decision 2026-09-10):
**sledgehammer first** — no automated method on a goal (`simp`, `blast`,
`auto`, `presburger`, ATP calls, including `multi_attempt` candidates)
without an `isabelle_sledgehammer` call on that goal first. The reviewer
`PROVER_ACCESS` block gained `isabelle_query` and `isabelle_sledgehammer`
(read-only steering aids).

IMO q1 run post-mortem (2026-09-10, owner decisions): the first real RC0
run (15 rounds, ~8 h) produced an 881-line sorry-free file that the strict
arbiter build then FAILED — 4+ logical errors at lines 571–639 (hand-written
`metis` with wrong fact lists, which also looped and OOM-killed the build
poly at the 20g cgroup cap, even on a quiet container; `oom_kill 13`). Three
owner rulings: (1) **scratch discipline** — one prover session per
workspace; `isabelle_run_code`/`isabelle_multi_attempt` are optional extras,
abandoned under any pool/memory pressure, and the file session is NEVER
closed/destroyed to make room for scratch (now in `round0.md` rule 4; the
builder had done exactly that trade); (2) the **sledgehammer-first rule was
ignored** — rule 7 now spells out that hand-written `metis`/`smt` with
guessed facts is the loop's worst failure mode (looping + OOM), every such
call must be a verbatim sledgehammer suggestion — generalized same day to
ALL looping automation (`blast`/`force`/`fastforce`/hand-fed `auto`/`simp`),
and the reviewer was made an **ATP gate**: `review.md` now audits every
round for automation abuse and judges a round REGRESSED — regardless of
diagnostics — when the proof carries a massive amount of hand-guessed
`metis`/`smt`/looping tactics, with the offending lines as the top issue
for the builder to replace; (3) **build memory cap** —
not flow-side (a urllib client can't prlimit the server's subprocess);
handed off as `isabellegym-build-memory-cap-issue(temp).md` (owner will set
it container-side, e.g. `ML_OPTIONS --maxheap`). The pre-arbiter admin
session-cleanup idea was abandoned (token plumbing not worth it). Also
structural: the flow's `readiness_timeout_s=180` default is too small for
this theory class (load_document >180 s, build ~370 s) — pass a larger
value via `-c` on heavy problems, and note the loop's readiness gate can
starve behind agent-held pool slots (28 readiness errors this run) while
the arbiter itself needs no session.

IMO q1 successful-run post-mortem (2026-09-10, `runs/imo2026_p1-rlcr-deepseek/ANALYSIS.md`):
24% of the run lost to 12×300 s MCP timeouts after a gateway JVM death
(handoff `isabellegym-jvm-restart-and-reload-cost-issue(temp).md`, also
covering the 36% full-reload-per-edit cost and the LOAD_TIMEOUT mismatch);
prompt compliance clean (0 metis/smt, scratch discipline held). Owner steer
from the same analysis: the builder UNDER-used sledgehammer (2 calls in 4 h
— the ATP bans overshot into abstinence). Rule 6 is now tiered:
**sledgehammer early and often** — routine finishing goals may go straight
to `simp`/`auto`/`blast`, but anything those don't close quickly gets an
immediate sledgehammer call ("two edits on the same subgoal without closing
it means sledgehammer NOW"), `metis`/`smt` still verbatim-suggestion-only
(rule 7 unchanged); the reviewer's ATP audit now also flags
under-automation (manual grind where one sledgehammer call would do).

Attempt 2 (2026-09-10, aborted after ~15 min): the builder's FIRST MCP call
returned "Not connected" — a transient startup race (opencode 1.18.27 tears
down and re-establishes idle MCP connections; the call landed in a reconnect
window, 4 s after a "MCP connection closed" lifecycle WARN). Reproduced and
cleared: manual stdio handshake OK, `opencode mcp list` connected, and a
one-shot `opencode run` in the workspace called `isabelle_heap_status`
successfully in ~1 s. The real defect was agent behavior: from that single
error it spent 10 min "debugging" the harness, then started a **local
isabelle build** (`nohup .../Isabelle2025-2/bin/isabelle build -b
HOL-Computational_Algebra`) — the exact side-channel rule 4 forbids.
`round0.md` rule 4 now states that one MCP error is transient (wait and
retry over a couple of minutes) and explicitly bans harness debugging,
config touching, and local builds as workarounds.

Attempts 3–4 (2026-09-10, both aborted early): same "Not connected" at the
first MCP call — the stdio MCP connection died 4–8 s after every
hmz-spawned opencode start (big prompt + `deepseek-v4-pro:high` first-token
latency ~2 min; small-prompt probes always worked). Root cause: opencode
1.18.27's stdio MCP lifecycle (5 s default tools-fetch timeout + idle
teardown, no keepalive option) versus a python MCP whose cold start can
exceed 5 s. **Fix (validated): run `mcp_lsp_server` as a PERSISTENT
streamable-http service and point opencode at it as a `type: "remote"`
MCP** — no stdio process for opencode to spawn or kill; idle periods are
harmless HTTP reconnects. Service launch (must be up before runs):

```sh
cd ~/IsabelleGym && nohup env PYTHONPATH=. \
  ISABELLE_MCP_LSP_GYM_URL=http://localhost:8001 \
  ISABELLE_MCP_LSP_TRANSPORT=streamable-http \
  ISABELLE_MCP_LSP_HOST=127.0.0.1 ISABELLE_MCP_LSP_PORT=8849 \
  ISABELLE_MCP_LSP_LOAD_TIMEOUT=300 ISABELLE_MCP_LSP_HTTP_TIMEOUT=900 \
  ISABELLE_MCP_LSP_SCRATCH_POOL_SIZE=1 \
  ~/.venvs/lsp-mcp/bin/python -m mcp_lsp_server.app > ~/mcp-lsp-http.log 2>&1 &
# opencode.json mcp.isabellegym: {"type":"remote","url":"http://127.0.0.1:8849/mcp","timeout":300000}
```

Verified with a 45 s-idle-then-call probe under the exact hmz spawn shape
(pro/high/--auto): diagnostics `completed, success=true`. Attempt 5's
builder's first MCP call succeeded and it went straight into
`find_theorems` exploration. Side effect: builder and reviewer now share
one MCP service (file bindings keyed by file_path) — fewer sessions, one
file session as the owner wants. The workspace `.mcp.json` (stdio) remains
for Claude Code only.

Also fixed the same day: opencode `permission.bash` rules — docs-conformant
order is catch-all `"*"` FIRST, specific denies AFTER (**last matching rule
wins**); a denies-first order silently disables every deny (verified by
A/B). Denies now cover `curl/wget/docker/kill` plus local-Isabelle builds
(`*/Isabelle2025-2/bin/isabelle*`, `./bin/isabelle*`, `isabelle build*`) —
the structural backstop against agent-built heaps. Note the deny evaluation
log still shows `action.pattern=*` for allowed commands; denies log their
own pattern. Agent-built heaps from attempts 2–3
(`~/.isabelle/Isabelle2025-2/heaps/.../HOL-Computational_Algebra`,
`Problem_Check` logs) were removed; the Sep-4 `Build_local`/`Build_final`
artifacts predate this and were left in place.

## Validated end-to-end behavior (live runs, kimi/kimi-k3 for both roles)

- **2026-09-03: DeepSeek-both-seats validated.** One rlcr run with builder =
  reviewer = `opencode/deepseek/deepseek-v4-flash:high` on `smoke_true`:
  `terminal=complete` at round 0, arbiter-solved, conformance 7/7 PASS
  (`/home/winst/m4-workspaces/smoke-opencode`). The DeepSeek reviewer
  produced a schema-valid ADVANCED verdict via the MCP
  (`isabelle_diagnostic_messages`/`isabelle_goal`/`isabelle_proof_state`)
  and independently caught a real process wrinkle: the review prompt pointed
  at `git show HEAD`, but the per-round commit lands *after* the review, so
  the round's changes are always uncommitted at review time. `review.md` now
  says so explicitly (working-tree diff first, HEAD = previous round).
- **The gym API server does not auto-start with the container.** Docker
  Desktop restarts (or `docker compose up` on a stopped stack) bring the
  container up without `server.app.main`; the MCP then reports unreachable
  until `docker compose exec -d isabelle-gym bash -c "python -m
  server.app.main > /app/logs/m0-server.out 2>&1"` runs again. (In this
  validation run the *builder agent* diagnosed and restarted the stack
  itself, then finished the proof — amusing, but not a strategy.)

- Builder proves via the LSP MCP (edit on disk → diagnostics), never trusted:
  REST readiness gate → reviewer schema-verdict → drift counters → arbiter.
- Arbiter rejects `sorry` twice over: flow regex + strict-mode build
  ("Cheating requires quick_and_dirty mode!").
- Fail-closed paths exercised live: gate refusal (dirty workspace) fixed by
  the builder; readiness 503s journaled and retried; malformed reviewer output
  counted STALLED (unit-tested); `GateCorruption` ends `unexpected` journaled.
- Session-pool failure mode found and fixed in config: zombie leases from dead
  MCP instances filled the pool (`ISABELLE_MAX_LEASE_AGE` now 600,
  `ISABELLE_MCP_LSP_SCRATCH_POOL_SIZE` now 1, `ISABELLE_POOL_SIZE` 6).

## Deviations from implementation-plan.md (deliberate)

1. **Mechanical readiness is flow-side REST, not reviewer-reported.** The plan
   has the reviewer return observed prover state "so the flow needs no MCP
   client". The flow instead acquires a session and reads the
   `load_document(report=True)` report itself (stdlib urllib, same channel as
   the arbiter). Stronger: readiness no longer depends on any agent's honesty.
   The reviewer's `observed_goals_*` remains as a fallback only.
2. **Objective progress is flow-side too.** `readiness()` also reads the
   session's `/subgoals` endpoint each round; the per-round goal list drives
   `progress.classify` (`kept["last_goals"]` delta), so the research metric
   (reviewer vs objective agreement) survives a reviewer with no MCP. Added
   for the dsh switch (below); the reviewer's own goal reports are used only
   when the REST fetch returns nothing.
3. **Reviewer is dsh (DeepSeek Harness), not codex/claude — superseded by
   opencode.** Owner decision 2026-09-02: codex/claude were never planned;
   reviewer-family diversity comes from DeepSeek models. dsh has **no MCP
   support** (bash + fs tools only), so `RLCRConfig.reviewer_mcp=False`
   renders a git-only review prompt; prover state reaches the reviewer
   through the flow's injected mechanical readiness, and goal state flows
   through deviation 2. dsh also has no native structured output — the
   schema-ask degrades to hmz's heuristic extraction, bounded by the
   re-ask-then-STALLED rule.
   **Superseded 2026-09-03 for the DeepSeek-both-seats plan (IMO/Putnam
   attempts, same model family on both seats — the humanfia PutnamBench
   precedent):** opencode 1.18.27 is installed at `~/.opencode/bin/opencode`
   with `~/.config/opencode/opencode.json` defining a DeepSeek provider
   (official API, key from `{env:DEEPSEEK_API_KEY}`) and the isabellegym
   MCP server. Verified: `opencode models` lists
   `deepseek/deepseek-v4-flash|-pro`; `opencode mcp list` shows isabellegym
   connected. This gives DeepSeek models **with** full MCP on both seats,
   no hmz modification. hmz reads opencode's catalogue from
   `opencode models`, so the agent spec is
   `opencode/deepseek/deepseek-v4-flash:<effort>` (live hmz-run
   confirmation pending the API key). The `reviewer_mcp: false` path stays
   as the dsh fallback.
4. **kimi attaches the MCP via user-level `~/.kimi-code/mcp.json`** —
   project-level config is blocked by workspace trust in headless mode. The
   workspace `.mcp.json` template remains for Claude Code (untested, CLI absent).
5. **Round-contract pre-gate not implemented.** The plan's §5.2 gate list has
   "round contract present"; the loop instead enforces the round summary +
   lesson delta (the load-bearing half). Recorded here so the conformance
   checker can cite it as a documented drop.
6. **hmz runs must be pointed at the flow by path**
   (`-f /mnt/c/.../flows/isabelle_rlcr:rlcr`); the flowverse is not registered
   with `hmz flowverses` yet. Register when the layout settles (blocked on the
   repo's first real commit — `flowverses add` clones).
7. **The IsabelleGym image carries a pip patch** (prometheus deps) on top of
   the pre-`9391b28` base; the pinned commit `0001971f…` does not fully
   describe the image until the owner rebuilds (dead registry mirror).

## Bugs found and fixed during validation

- `gates.git()` stripped porcelain output, corrupting the first status line's
  leading column → `workspace_clean` false refusals (found by the test
  subagent's xfail; fixed by returning raw stdout).
- Flow's per-round commit failed silently on workspaces with no git identity
  (now `-c user.email/user.name` per commit).
- Readiness check burned a builder turn per round while the pool was full
  (now one 30 s backoff retry first).
- Round-prompt artifacts were named for the round just ended rather than the
  round the prompt is for; round-0 prompt was never persisted.
- `immutable_sha` was not journaled (now in the `setup` record).
- `validate_delta`'s malformed-ID branch was unreachable (check reordered).
- Resume-state vs workspace mismatch (state says setup done, tracker deleted)
  crashed with a traceback instead of a journaled `unexpected` terminal.
- `tools/ablation.py` copied workspaces that carry no `.git` of their own
  (anything committed inside this repo) verbatim → every run died at
  `require_git_repo`. Copies now get git init + one initial commit
  (`prepare_run_dir`, reusing `import_problem.init_git`).
- **hmz resume is keyed on the workspace PATH.** Deleting and recreating a
  workspace at the same path (ablation sweeps reusing a `--runs-dir`)
  resurrects the previous incarnation's flow state: setup was skipped in a
  workspace with no tracker/journal, and every conformance check then FAILed
  on the missing `setup` record. Fixed flow-side: `gates.stale_resume`
  detects state-without-artifacts and re-runs setup (journaled as
  `resume_reset`). A tracker deleted while the journal survives is still
  tampering, refused by the pre-gates — not reset.
- **`parse_spec` could not see one-line theory headers.** A source written
  `theory Problem imports Complex_Main` (PutnamBench layout) fell through
  the imports-line regex and got a degenerate `imports=` TASK comment — and
  the degenerate comment was then invisible to both the parse regex and the
  re-import stripper. Fixed: header-body import parsing (`_HEADER` +
  `_HEADER_IMPORTS`), and `_TASK_COMMENT_LOOSE` strips degenerate comments
  on re-import. Round-trip test added. (The arbiter itself was never at
  risk: the bigstep server extracts imports from the theory text when
  `dependencies` is empty — `build_verify.py:120`.)
- **First HOL-Analysis run stalls on the heap build.** A problem importing
  `HOL-Analysis.*` triggers a full session-image build on first use
  (tens of minutes); the builder reads it as the prover hanging. Pre-build
  once: `docker compose exec -d isabelle-gym bash -c "isabelle build -b
  HOL-Analysis > /app/logs/heap-HOL-Analysis.log 2>&1"` — `/root/.isabelle`
  is a named volume, so it persists across container restarts.
  **`ML_SYSTEM_64=true` is required** (now in IsabelleGym `.env`, a config
  change like the pool tuning): the default 32-bit PolyML gets SIGKILLed
  building HOL-Analysis under the 14g container cap, and 64-bit heaps live
  in a different heap dir than 32-bit sessions read — the setting must be
  container-wide or the server's own builds hit the same wall. Note 64-bit
  sessions use more heap each; if the pool pressures the cap on
  HOL-Analysis problems, lower `ISABELLE_POOL_SIZE` from 6.

## Environment gotchas (this machine, WSL)

- **Contamination & side-channel controls (updated 2026-09-07).** Prompt
  rules as before (no internet, no solution lookups), plus now
  **structural bash-pattern denies in `~/.config/opencode/opencode.json`**:
  `curl/wget/docker/kill *: deny` — verified to hold under hmz's exact
  spawn shape (`opencode run --auto`), including a `webfetch: deny` rule.
  Remaining gap by design: python-urllib heredocs can't be pattern-denied
  without killing legitimate `python3` math use; the root-cause fix is the
  upstream `header_imports` repair (above). Earlier incident history
  (Putnam 2023 B6 solution-PDF download, prompt-level rules) unchanged.
- Docker Desktop does not start from `cmd /c start`; it does from PowerShell
  `Start-Process 'C:\Program Files\Docker\Docker\Docker Desktop.exe'`
  (`.m0/wait_docker.sh` polls until the WSL integration is up).
- hmz spawns agent CLIs by bare name: a non-interactive WSL shell resolves
  `kimi` to the Windows-side shim `/mnt/c/Users/winst/.kimi-code/bin/kimi`
  (EACCES) unless `~/.kimi-code/bin` is prepended to PATH first, and
  `opencode` is not found at all (`FileNotFoundError` at the first builder
  turn, 2026-09-10) unless `~/.opencode/bin` is on PATH. `.bashrc` exports
  both (kimi line ~120, opencode appended 2026-09-10), but only INTERACTIVE
  shells read it — non-interactive launches need the exports inline.
- Live-status helpers: `.m0/ablation_status.sh` (per-arm journal snapshot;
  `watch -n 20 -t` it), `.m0/conformance_all.sh`, `.m0/inspect_run*.sh`.
- **Memory resize 2026-09-10 (IMO q1 run blocked on 503s):** the RC0
  container's 14g cap was unreachable anyway — WSL itself was capped at the
  default ~50% of host RAM (~15 GiB), and two RC0 Isabelle sessions
  (~6.5 GB each) + JVM filled it, so the 80% memory-admission gate 503'd
  every further acquire (the builder burned 31 steps diagnosing it; zero
  file edits). Fix: `C:\Users\winst\.wslconfig` now sets `memory=24GB`
  (host 32 GB), WSL + Docker Desktop restarted, and `isabelle-gym-rc0`
  recreated manually (no compose service for RC0) with `--memory 20g`,
  same image/env/volume/port (`isabelle_rc0_user_data`,
  8001→8000, env snapshot in `~/rc0.env.backup`). Gotcha: a *recreated*
  container has no `/app/logs` — `mkdir -p /app/logs` before the detached
  `server.app.main` start or the redirect kills it silently. Verified:
  heaps intact (HOL … HOL-Computational_Algebra, HOL-Library),
  acquire/release OK at 2.4/20 GiB, arbiter smoke green.

## Known gaps / next actions

- **LSP MCP cannot load non-Main imports — RESOLVED (upstream).**
  Fixed 2026-09-04 (theories-at-acquire). **Follow-up header-imports regex
  bug — RESOLVED 2026-09-09 (Plan B landed):** one canonical parser
  (`server/app/services/theory_parsing.py`, nested-comment stripping +
  header anchoring) now backs `header_imports` and
  `build_verify.extract_imports`, plus the structural endpoint
  `POST /api/v1/parse_theory_header` → `{theory_name, imports,
  suggested_field}` (+ `async_client` wrapper). Verified on the real
  `~/putnam-runs/q4/problem.thy`: `isabelle_open` + diagnostics succeed, zero
  comment pollution. Side note (pre-existing, not the parser): bigstep's
  single-parent build can't span two session families in one build.
- **Lease-leak handoff — RESOLVED 2026-09-09:** public listing no longer
  carries `lease_id`; full listing gated behind
  `GET /api/v1/admin/sessions` (`X-Admin-Token` vs `ISABELLE_ADMIN_TOKEN` in
  the gym `.env`); DELETEs are audit-logged + counted
  (`isabellegym_sessions_force_closed_total`); `isabelle_close(destroy=)` +
  `ISABELLE_MCP_LSP_CLOSE_DESTROYS` gives the sanctioned teardown.
  `tests/test_lease_security.py` covers it; live acceptance green on :8001.
- **Run-readiness (2026-09-09): the flow targets RC0 on :8001.**
  `RLCRConfig.gym_url` default flipped to `http://localhost:8001`; all
  `.mcp.json` copies (user-level `~/.kimi-code/mcp.json`, template, `.m0`)
  point at :8001. Fixes above are live AND baked into the refreshed image
  `isabellegym-isabelle-gym:2026rc0`. Heaps: HOL + HOL-Analysis present;
  HOL-Number_Theory and HOL-Combinatorics building in the idle build
  container (q4's cross-session imports already resolve on demand).
- **Third handoff (2026-09-09): `isabellegym-lease-leak-issue.md`.**
  `GET /api/v1/sessions` returns every session's `lease_id` without a
  lease, so the lease check on `DELETE /sessions/{id}` is bypassable by any
  client (enumerate → steal → destroy), with no task_group scoping.
  Observed in the wild: our agents did exactly this (their own sessions)
  during the Putnam runs. Includes a feature ask: a sanctioned destroy path
  in the LSP MCP (`isabelle_close(destroy=true)` or
  `ISABELLE_MCP_LSP_CLOSE_DESTROYS`) so agents stop hand-rolling curl
  DELETEs; scratch pool's `drop_scratch` is the precedent.
- **RC0 migration landed (2026-09, IsabelleGym side).** Commits `a9e1683`
  (REPL Scala backend RC0-compatible), `d865f31` (JVM aliveness recovery via
  RC0's Event_Timer probe — the Bug-9 hardening: schedule a no-op on the
  JVM-global timer to detect its cancellation), `af14618` (RC0 Dockerfile).
  RC0 server runs on **:8001** (2025-2 remains on :8000); agent MCP configs
  flipped to :8001 at cutover (2026-09-09) and `RLCRConfig.gym_url` defaults
  to it. Note: Event_Timer is *not* in RC0's NEWS; the Bug-9 fix is the
  detection/probe approach plus the upstream `88acf2619921` task-hardening
  (both validated: 12/12 stress rounds on RC0).
- **`DEEPSEEK_API_KEY` is set** (in `~/.bashrc` and opencode's own
  `~/.local/share/opencode/auth.json`, mode 600 — hmz-hush-proof). The
  opencode/deepseek route is validated end-to-end (rlcr run, conformance
  7/7). **Key rotation must update both stores:** opencode prefers the
  `auth.json` credential over the env var — the 2026-09-10 q1 attempt kept
  using the stale Sep-4 key until `auth.json` was re-synced to the bashrc
  key. Optional follow-ups: qwen 401 re-auth (third family), dsh fallback
  (`reviewer_mcp: false`) implemented but likely unnecessary.
- qwen re-auth (401) — owner's credentials (optional third family).
- `workspaces/template/.mcp.json` carries WSL-machine paths (documented as
  placeholders in its README).
- hmz Sentry telemetry is on; `HUMANIZE_SENTRY=off` for scripted runs.
- Flowverse not registered with `hmz flowverses` — the repo's flow code is
  uncommitted, and `flowverses add` clones, so registration waits on a commit
  (owner's call). `-f <path>:rlcr` works regardless.

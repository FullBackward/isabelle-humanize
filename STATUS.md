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
| M4 study | **Not started** | blocked on reviewer-family diversity (claude/codex) and compute budget |

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

## Environment gotchas (this machine, WSL)

- Docker Desktop does not start from `cmd /c start`; it does from PowerShell
  `Start-Process 'C:\Program Files\Docker\Docker\Docker Desktop.exe'`
  (`.m0/wait_docker.sh` polls until the WSL integration is up).
- hmz spawns agent CLIs by bare name: a non-interactive WSL shell resolves
  `kimi` to the Windows-side shim `/mnt/c/Users/winst/.kimi-code/bin/kimi`
  (EACCES) unless `~/.kimi-code/bin` is prepended to PATH first
  (`.bashrc` does it, but non-interactive shells don't read it).
- Live-status helpers: `.m0/ablation_status.sh` (per-arm journal snapshot;
  `watch -n 20 -t` it), `.m0/conformance_all.sh`, `.m0/inspect_run*.sh`.

## Known gaps / next actions

- **`DEEPSEEK_API_KEY` not set anywhere** — needed for the DeepSeek runs
  (owner's). Once set: live-confirm hmz accepts
  `opencode/deepseek/deepseek-v4-flash:<effort>`, then one rlcr validation
  run with both roles opencode/deepseek (the IMO/Putnam configuration) and
  one with builder kimi + reviewer opencode/deepseek (the
  family-diverse configuration). The dsh fallback
  (`reviewer_mcp: false`) is implemented but likely unnecessary now.
- qwen re-auth (401) — owner's credentials (optional third family).
- `workspaces/template/.mcp.json` carries WSL-machine paths (documented as
  placeholders in its README).
- hmz Sentry telemetry is on; `HUMANIZE_SENTRY=off` for scripted runs.
- Flowverse not registered with `hmz flowverses` — the repo's flow code is
  uncommitted, and `flowverses add` clones, so registration waits on a commit
  (owner's call). `-f <path>:rlcr` works regardless.

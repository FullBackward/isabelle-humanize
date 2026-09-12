# runs/ — finished-run analysis archive

Per-run artifacts, copied out of the WSL workspaces (`~/putnam-runs/q*`) for
analysis. Sources are never run from here. Each run dir holds:

- `.rlcr/` — the loop's journal (`journal.jsonl`, the metrics source) plus all
  round artifacts (prompts, review prompts/results, summaries, terminal state)
- `problem.thy` — the final, arbiter-verified theory
- `goal-tracker.md`, `proof-lessons.md`, `methodology-analysis.md`,
  `finalize-summary.md` — the v1-parity artifact family
- `.git/` — the workspace's own history (per-round commits)
- `attempts/` — raw agent console logs where they exist (q1 only; later runs
  were driven from the owner's terminal)
- `metrics.json` — computed by `tools/ablation.py::summarize_run`

## Results so far (all runs: both roles `opencode/deepseek/deepseek-v4-pro:high`)

| Run | Problem | Result | Rounds | Wall (s) | Agent-active (min) | Cost (USD) | Humanize-Lean ref | Stalls | Lessons | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| `putnam_2023_a1-rlcr-deepseek` | 2023 A1 | **solved** (complete, build_strict) | 2 | 11 964 | 213.7 | $3.28 (≈25 CNY, console-verified) | passed at turn 1 (ref subset); avg $44.50/problem | 2 | 5 | First real solve; 4 attempts (infra fixes between) |
| `putnam_2023_b6-degenerate-rlcr-deepseek` | 2023 B6 (upstream-degenerate statement) | "solved" — `det = 0` | 0 | 2 449 | 38.4 | $0.43 (est.) | avg $44.50/problem (no per-problem data) | 1 | 4 | NOT a real result: PutnamBench's Isabelle B6 counts over unrestricted `int` (infinite sets ⇒ card 0 ⇒ zero matrix). See the round-0 review and `PutnamBench/putnam_2023_b6.thy` (corrected encoding) |
| `putnam_2023_b5-rlcr-deepseek` | 2023 B5 | **solved** (complete, build_strict) | 5 | 51 463 | 812.6 | $14.72 (≈100 CNY, console-verified) | avg $44.50/problem (no per-problem data) | 6 | 16 | Hard-problem run: REGRESSED round 3, replan, ADVANCED ×5. One 5-hour stalled turn discarded; resume finished in ~40 min |
| `imo2026_p1-rlcr-deepseek` | IMO 2026 P1 (5-theorem file, target `statement_a_termination`) | **solved** (complete, build_strict, 44 s) | 0 | 14 949 | 248.2 | $5.15 (peak window) | IMO Q1: Kimi-K3 $5.73 / 87.1 min API; GPT-5.6 $8.11 / 38.1 min ([humanfia/imo2026](https://github.com/humanfia/imo2026)) | 0 | 3 | **Single-round solve**, first run on the post-fix stack (persistent remote MCP, maxheap 9216, ATP/scratch rules). 1059-line theory, 0 sorries, conformance 7/7 PASS. Attempts 1–4 were infra R&D (memory, MCP races); their cost is not included |

**Agent-active / cost** are computed by `tools/agent_time.py` from opencode's
session DB. Agent-active is the sum of step-start→step-finish durations
(includes in-step tool execution, so it overstates pure model-API time on
incident-heavy runs). Cost is **computed from token counts × DeepSeek's
official price table** (2026-09-10, off-peak rates — both solved runs ran in
off-peak UTC windows), NOT opencode's `cost` field, whose stale catalogue
underreports ~3.3×. The A1/B5 figures match the owner's DeepSeek console
(≈25 / ≈100 CNY); B6 is the same computation, unverified against console.
Note deepseek-v4-pro retires 2026-09-14 (requests then route to V4.1 Flash
at Flash prices) — later runs are not comparable on price.

**Humanize-Lean ref** is what humanfia publishes for the same problems on
Lean 4 ([humanfia/putnambench](https://github.com/humanfia/putnambench)):
672/672 solved, worker/reviewer `gpt-5.6-sol` xhigh, reported **$44.50/problem
average cost** and no per-problem times; per-problem `terminal_turn` exists
only for a 98-problem reference subset
(`reference/FINAL_ARTIFACTS.tsv`), which of our three contains only A1
(passed at turn 1). Not apples-to-apples: different prover (Lean vs
Isabelle), different model, and their harness is purpose-built batch
verification vs our interactive LSP loop.

Analysis companions: `.m0/agent-behaviors-beneath-tools.md` (side-channel
behavior census), `.m0/attempt-logs/` (A1 attempt consoles), `STATUS.md`
(deviations and incident history).

# isabelle-humanize

A research project porting the **humanize RLCR loop** ("Ralph-Loop with Codex
Review": one agent builds, an independent agent reviews, they iterate under a
drift state machine until done) from software engineering to **Isabelle/HOL
theorem proving**. The core bet: in theorem proving the prover kernel is a
*ground-truth* reviewer, so the LLM reviewer steers while `isabelle build`
decides.

The repo ships one [humanize2](https://github.com/humanfia/humanize2) (`hmz`)
flow, **`isabelle_rlcr`** (`flows/isabelle_rlcr/`), plus workspace templates,
tooling, tests, and an archive of finished runs (`runs/`).

**Note: humanize2 does not support Windows machines, if you are trying to use a Windows machine, better setup everything from WSL.**

How a run works, in one line: **hmz drives builder/reviewer CLI agents; each
agent talks to the prover through the IsabelleGym LSP MCP; the flow itself
talks to the gym only over REST for the mechanical readiness gate and the
final arbiter (`isabelle build` ∧ no `sorry`/`oops` ∧ target theorem
present).** Agents act, the flow judges; only the arbiter can declare
`complete`.

---

## Reproducing the run environment (fresh machine)

Everything below §1–§6 documents the reference machine; for a fresh machine,
use the automated setup instead.

**You need:** WSL2 (Ubuntu) + Docker Desktop; **a running IsabelleGym server**
— set that up first, from the IsabelleGym repo: `./setup.sh` (see its `README.md`); an IsabelleGym
checkout regardless (the MCP server code runs host-side from it); a humanize2
checkout; this repo; a DeepSeek API key.

```sh
bash tools/setup_repro.sh \
    --gym-src    ~/IsabelleGym \
    --hmz-src    /path/to/humanize2
```

The script is idempotent and covers the **shared, humanize-side wiring** (it
assumes the IsabelleGym server is already up per the IsabelleGym repo):
prerequisite checks; WSL memory (`.wslconfig` 24 GB — needs one `wsl
--shutdown`); gym healthz + heap verification; the `lsp-mcp` venv (`mcp<2` +
httpx); the **persistent streamable-http MCP service** on `127.0.0.1:8849`
(the fix for opencode's stdio MCP drops — do not go back to stdio for
opencode); **harness availability checks**; the hmz venv; and smoke checks
(healthz + arbiter good/sorry probe). `bash tools/setup_repro.sh --check`
verifies prerequisites only.

**The agent harnesses are set up by you, not the script** — install clients such as: Kimi code, Clause Code, Opencode and etc, log in, configure providers and API keys (see §2). The script
only reports what it finds.

**The run constraints are applied per harness by a second script** — pick
which harness(es) to isolate:

```sh
bash tools/setup_isolation.sh opencode        # permission block in opencode.json
bash tools/setup_isolation.sh kimi            # [[permission.rules]] denies in config.toml
bash tools/setup_isolation.sh opencode kimi   # both seats
bash tools/setup_isolation.sh --dry-run kimi  # preview without writing
```

Both write the same constraint set the runs used: no internet
(`webfetch`/`FetchURL`/`WebSearch` deny), no `curl`/`wget`/`docker`/`kill`,
no local `isabelle build` (opencode: catch-all first, denies last —
last-matching-rule-wins; kimi: `[[permission.rules]]` denies — first match
wins, so review ordering if your config already has broad allow rules). Do
not weaken these; they are what "no internet, no side-channel builds" means
structurally. `dsh` has no config-level isolation — it is prompt-level only.
Remaining gap by design: python one-liners can't be pattern-denied — that's
prompt-level (see `flows/isabelle_rlcr/prompts/round0.md`).

After setup, one problem runs as (also printed by the script):

```sh
python tools/import_problem.py <source.thy> ~/runs/<name>
printf 'readiness_timeout_s: 600\n' > /tmp/rlcr.yaml   # heavy theories need >180 s loads
cd ~/runs/<name> && HUMANIZE_SENTRY=off ~/.venvs/hmz/bin/hmz exec \
  -f <this-repo>/flows/isabelle_rlcr:rlcr -c /tmp/rlcr.yaml \
  -a opencode/deepseek/deepseek-v4-pro:high -a opencode/deepseek/deepseek-v4-pro:high \
  "prove the pending theorem in problem.thy in the current workspace, discharge all sorries."
```

Note: `deepseek-v4-pro` retires 2026-09-14 (routes to V4.1 Flash, billed at
Flash price) — later reproductions should switch the `-a` specs accordingly.

---

## Prerequisites

- **WSL 2** (the whole setup runs WSL-native; this repo is read through
  `/mnt/c/...` but workspaces and IsabelleGym live in the WSL filesystem)
- **Docker Desktop** with WSL integration
- **[IsabelleGym](https://github.com/FullBackward/IsabelleGym)** v3.0,
  cloned WSL-native at `~/IsabelleGym` — consumed **unmodified**
- **humanize2 (`hmz`)** in a venv, e.g. `~/.venvs/hmz` (provides `hmz.flows`
  and pydantic to flows)
- **Agent CLIs**, at least one per role:
  - `kimi` (Kimi Code CLI) — builder; attaches the LSP MCP via user-level
    `~/.kimi-code/mcp.json`
  - `opencode` (≥ 1.18.27) with a DeepSeek provider — reviewer (or both
    seats); needs `DEEPSEEK_API_KEY` in the environment
  - fallback: `dsh` (DeepSeek Harness) — has **no MCP support**, so runs use
    `reviewer_mcp: false`
- a venv for the MCP server: `~/.venvs/lsp-mcp` with `mcp<2` + `httpx`

## 1. IsabelleGym server (assumed running)

The runs expect an IsabelleGym server at `http://localhost:8001` (the
`gym_url` default in `flows/isabelle_rlcr/schemas.py`). **Set it up from the
IsabelleGym repo** — turnkey image per its
`Isabelle2026-RC0_version_docker_image_instruction.md` (2026-RC0 branch), or
`./setup.sh` from a checkout per its `README.md` — then verify:

```sh
curl http://localhost:8001/healthz   # {"status":"alive"}
```

What the runs assume of that server (all satisfied by the turnkey image and
by the repo's default `.env.example` + entrypoint):

- Isabelle **2026-RC0** with session heaps pre-built (HOL, HOL-Library,
  HOL-Computational_Algebra, HOL-Analysis, HOL-Number_Theory,
  HOL-Combinatorics — `curl localhost:8001/api/v1/heaps/available`).
- **ML heap cap** (Poly/ML `--maxheap`, default 9 GB) so one pathological
  theory fails its build cleanly instead of OOM-killing the container —
  written into the Isabelle user settings by the gym's container entrypoint.
  (Note: we run **32-bit** `x86_64_32-linux` PolyML with the cap; an earlier
  revision of this README recommended `ML_SYSTEM_64=true` — superseded.)
- Pool tuning against zombie leases: `ISABELLE_POOL_SIZE=3`,
  `ISABELLE_MAX_LEASE_AGE=600`, `ISABELLE_MCP_LSP_SCRATCH_POOL_SIZE=1`.

## 2. Wire up the agents' MCP

The agents reach the prover through IsabelleGym's `mcp_lsp_server`
(structurally read-only: file-sync + diagnostics, never writes files).

- **kimi**: project-level `.mcp.json` is blocked by workspace trust in
  headless mode, so the config lives at **`~/.kimi-code/mcp.json`** (same
  shape as `workspaces/.mcp.json`).
- **opencode**: `~/.config/opencode/opencode.json` defines a DeepSeek
  provider (official API, key from `{env:DEEPSEEK_API_KEY}`) and the
  isabellegym MCP server — see "API keys" below. Since 2026-09-10 the MCP
  is a **`type: "remote"` server at `http://127.0.0.1:8849/mcp`**: the
  `mcp_lsp_server` runs as a *persistent service* (streamable-http), not a
  per-spawn stdio process — opencode 1.18.27's stdio lifecycle (5 s default
  tools-fetch timeout + idle teardown) killed the connection seconds after
  spawn on slow models, so agents' first MCP calls failed with
  "Not connected". Start the service before any run:

  ```sh
  cd ~/IsabelleGym && nohup env PYTHONPATH=. \
    ISABELLE_MCP_LSP_GYM_URL=http://localhost:8001 \
    ISABELLE_MCP_LSP_TRANSPORT=streamable-http \
    ISABELLE_MCP_LSP_HOST=127.0.0.1 ISABELLE_MCP_LSP_PORT=8849 \
    ISABELLE_MCP_LSP_LOAD_TIMEOUT=300 ISABELLE_MCP_LSP_HTTP_TIMEOUT=900 \
    ISABELLE_MCP_LSP_SCRATCH_POOL_SIZE=1 \
    ~/.venvs/lsp-mcp/bin/python -m mcp_lsp_server.app > ~/mcp-lsp-http.log 2>&1 &
  ```

  Verify with `opencode mcp list` (should show connected) and
  `opencode models`.
- **Claude Code**: would read the workspace `.mcp.json` directly (untested).

`workspaces/.mcp.json` carries machine-specific paths, treated as
placeholders: `command` is the `~/.venvs/lsp-mcp` python, `PYTHONPATH` is the
WSL-native IsabelleGym clone. Adapt to your machine.

### API keys (DeepSeek for opencode)

The DeepSeek models (`deepseek/deepseek-v4-flash`, `deepseek/deepseek-v4-pro`)
are served through DeepSeek's official API; you need a key from
<https://platform.deepseek.com/>. Two ways to give it to opencode — both are
used on the reference machine, either alone works:

**A. Environment variable + provider config.** Put the key in your shell
environment (hmz spawns opencode as a child process, so it inherits it):

```sh
# ~/.bashrc
export DEEPSEEK_API_KEY="sk-..."
```

and reference it from `~/.config/opencode/opencode.json` with the
`{env:...}` placeholder (the key itself never sits in the config file). The
full config shape on the reference machine, MCP server included:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "deepseek": {
      "options": { "apiKey": "{env:DEEPSEEK_API_KEY}" }
    }
  },
  "mcp": {
    "isabellegym": {
      "type": "local",
      "command": ["/home/winst/.venvs/lsp-mcp/bin/python", "-m", "mcp_lsp_server.app"],
      "environment": {
        "PYTHONPATH": "/home/winst/IsabelleGym",
        "ISABELLE_MCP_LSP_GYM_URL": "http://localhost:8001",
        "ISABELLE_MCP_LSP_TRANSPORT": "stdio",
        "ISABELLE_MCP_LSP_LOAD_TIMEOUT": "300",
        "ISABELLE_MCP_LSP_HTTP_TIMEOUT": "900"
      }
    }
  }
}
```

(Paths are machine-specific placeholders, same as `workspaces/.mcp.json`.)

**B. `opencode auth login`.** Interactive login stores the credential in
`~/.local/share/opencode/auth.json` (mode 600) — no environment variable
needed. On the reference machine both A and B are set, so the key survives
non-interactive shells that skip `.bashrc`.

**Key rotation must update BOTH stores.** When the two disagree, opencode
uses the `auth.json` credential over the env var — a run can silently keep
billing the old key (observed 2026-09-10). After rotating, set the new key
in `~/.bashrc` *and* re-run `opencode auth login` (or edit `auth.json`),
then re-source your shell before launching.

Verify the wiring before a run:

```sh
opencode models     # should list deepseek/deepseek-v4-flash and -pro
opencode mcp list   # isabellegym should show connected
```

A 401 from `opencode models` means the key is missing/expired — re-run
`opencode auth login` or fix the env var. **Never commit keys**: keep them in
`~/.bashrc` / `auth.json` only; workspace `.mcp.json` files and anything
under `flows/` must stay credential-free (the flowverse is arbitrary code —
it runs with your full privileges every run).

kimi carries its own authentication (Kimi Code CLI login, independent of
DeepSeek); the dsh fallback uses the same DeepSeek account but has no MCP —
pair it with `reviewer_mcp: false` as shown in §4.

## 3. Create a workspace

A workspace is one problem: a git repo holding `problem.thy` (statement ends
in `sorry`) with a task-spec header comment — the one source of truth the
gates and arbiter read:

```
(* TASK: theorem=<name> imports=<imports> field=HOL *)
```

Import any single-theorem `.thy` file (the theory is renamed to `Problem`,
the canonical TASK header injected, `.mcp.json`/`.gitignore` copied from
`workspaces/template/`, git initialized with one commit):

```sh
python tools/import_problem.py path/to/source.thy ~/my-workspace [--field HOL]
```

Ready-made smoke problems live in `workspaces/smoke_true/`,
`workspaces/smoke_conj/`, `workspaces/smoke_add0/` — **copy them out before
running** (`tools/ablation.py` does this for you); never run a workspace in
place inside this repo.

## 4. Run the flow

```sh
# hmz spawns agent CLIs by bare name: the WSL kimi and opencode bin dirs must
# be on PATH (both are exported by ~/.bashrc, which only INTERACTIVE shells
# read -- non-interactive launches need the exports inline), or `kimi`
# resolves to the Windows-side shim in /mnt/c (EACCES) and `opencode` is not
# found at all (FileNotFoundError at the first builder turn)
export PATH="$HOME/.kimi-code/bin:$HOME/.opencode/bin:$PATH"
cd ~/my-workspace

# builder kimi + reviewer DeepSeek-via-opencode (both with MCP):
~/.venvs/hmz/bin/hmz exec \
    -f /mnt/c/Users/winst/GitHub/isabelle-humanize/flows/isabelle_rlcr:rlcr \
    -a kimi/moonshot-cn/kimi-k3:high \
    -a opencode/deepseek/deepseek-v4-flash:high \
    "prove the pending theorem in problem.thy"
```

Variants:

```sh
# DeepSeek on both seats: pass the opencode spec twice
# dsh reviewer (no MCP): write a config file first
printf 'reviewer_mcp: false\n' > /tmp/rlcr-dsh.yaml
hmz exec -f .../flows/isabelle_rlcr:rlcr -c /tmp/rlcr-dsh.yaml \
    -a kimi/moonshot-cn/kimi-k3:high -a dsh/deepseek-v4-flash:high \
    "prove the pending theorem in problem.thy"

# single-agent ablation arm (no reviewer, no drift machine — gates + arbiter):
hmz exec -f .../flows/isabelle_rlcr:solo \
    -a kimi/moonshot-cn/kimi-k3:high \
    "prove the pending theorem in problem.thy"
```

Notes:

- The flowverse is **not registered** with `hmz flowverses`; always point at
  the flow by path with `-f <path>:<flow>`.
- `HUMANIZE_SENTRY=off` for scripted runs.
- **hmz resume is keyed on the workspace path** — deleting and recreating a
  workspace at the same path triggers the flow's `resume_reset` path; use
  fresh directories for independent runs.
- Runs cost tokens and take hours on real problems. Smoke problems solve in
  round 0; that validates tooling, not the research claim.

### Configuration (`-c config.yaml`, all optional)

The TUI settings menu renders from the same model
(`flows/isabelle_rlcr/schemas.py::RLCRConfig`):

| key | default | meaning |
|---|---|---|
| `max_iterations` | 42 | rounds before `maxiter` stop |
| `full_review_round` | 5 | rounds between full-alignment reviews |
| `drift_stall_replan` | 2 | consecutive stalls before a replan prompt |
| `drift_stall_breaker` | 3 | consecutive stalls before the circuit breaker (`stop`) |
| `arbiter_timeout_s` | 900 | budget for one `isabelle build` arbiter call |
| `readiness_timeout_s` | 180 | budget for one REST readiness load |
| `reviewer_mcp` | true | false for MCP-less backends (dsh) |
| `solo` | false | single-agent ablation arm |
| `drift_source` | reviewer | `reviewer` / `objective` / `either` (ablation flag) |
| `bitlesson_required` | true | every round summary carries a lesson delta |
| `finalize_simplify` | true | one sorry-free simplification pass before `complete` |
| `gym_url` | `http://localhost:8001` | IsabelleGym server base URL |
| `privacy` | false | skip the methodology analysis at exit |

Terminal states are exactly v1's taxonomy: `complete / cancel / maxiter /
stop / unexpected`.

## 5. What a run produces

Inside the workspace:

- `.rlcr/` — the journal (`journal.jsonl`, the metrics source) and all round
  artifacts (round prompts, review prompts/results, summaries, terminal
  state)
- `goal-tracker.md` — IMMUTABLE (byte-preserved) task section + MUTABLE plan
- `proof-lessons.md` — the proof-lessons knowledge base (v1's BitLesson
  analog); lessons persist across rounds and attempts
- per-round git commits in the workspace's own repo
- on exit: `methodology-analysis.md`, `finalize-summary.md` (unless
  `privacy: true`)

After a run, analyze it with:

```sh
# conformance: does the run show every v1 gate behavior? (7 checks)
python tools/conformance_check.py <workspace-dir> [--json]

# ablation: batch rlcr-vs-solo sweeps over prepared run dirs + metrics.json
python tools/ablation.py --flow flows/isabelle_rlcr \
    --agent kimi/moonshot-cn/kimi-k3:high --workspace DIR... [--list]
```

Finished-run archives (journals, final theories, metrics) live in `runs/` —
see `runs/README.md` for the index and results so far (Putnam 2023 A1 and B5
solved, arbiter-verified).

### Monitoring a live run

Runs are long (hours on real problems); these are the standing monitoring
commands. Helper scripts live in `.m0/` (gitignored scratch — paths below are
from the repo root, run them in WSL):

```sh
# one-line dashboard: gym health, container memory, hmz process count,
# journal record counts, last journal entry, terminal state if finished.
# Set WS inside the script to the workspace being watched, then:
watch -n 30 -t bash .m0/monitor_putnam.sh

# the raw journal, live:
tail -f <workspace>/.rlcr/journal.jsonl

# compact journal summary by hand:
grep -o '"kind": "[a-z_]*"' <workspace>/.rlcr/journal.jsonl | sort | uniq -c
grep '"terminal"' <workspace>/.rlcr/journal.jsonl | tail -1   # empty while running

# ablation sweeps: per-arm snapshot (run it under watch) and, afterwards,
# conformance across every finished arm:
watch -n 20 -t bash .m0/ablation_status.sh <runs-dir>
bash .m0/conformance_all.sh <runs-dir>

# one run dir under the microscope: journal kinds, .rlcr artifacts,
# per-round git commits, hmz cycle dir:
bash .m0/inspect_run.sh <run-dir>

# infrastructure health:
curl http://localhost:8001/healthz
docker stats isabelle-gym-rc0 --no-stream     # RC0 track container (post-cutover;
                                              # `isabelle-gym` was the retired :8000 one)
curl -s http://localhost:8001/api/v1/sessions  # session pool / leases
tail -30 ~/IsabelleGym/logs/server.log         # gym file log (compose setup: repo is
                                               # mounted at /app). Otherwise:
                                               # docker logs --tail 30 isabelle-gym-rc0
ls ~/.humanize/cycles/                         # hmz's own cycle journals
```

If `hmz exec` disappears from `ps` without a `"terminal"` record in the
journal, the run died (WSL crash, OOM, SIGTERM) — re-run the same command in
the same workspace; resume is keyed on the path and the flow's
`stale_resume` gate repairs interrupted state.

## 6. Tests

```sh
cd /mnt/c/Users/winst/GitHub/isabelle-humanize
~/.venvs/hmz/bin/python -m pytest tests/ -q   # 117+ pure-function tests
```

Tests are pure functions only — no live agents, no live Isabelle — so they
stay fast and CI-able. Live end-to-end runs are manual and token-gated.

## Repository map

| path | role |
|---|---|
| `flows/isabelle_rlcr/` | the flow: loop (`__init__.py`, rlcr + solo arms), gates, arbiter, progress classifier, memory, schemas, prompts |
| `workspaces/` | `template/` + smoke problems; copy out, never run in place |
| `tools/` | `import_problem.py`, `smoke_lsp.py`, `conformance_check.py`, `ablation.py`, `agent_time.py` (per-run cost/token/agent-active metrics from opencode's DB) |
| `tests/` | pytest suite + recorded arbiter fixtures |
| `runs/` | finished-run analysis archive |
| `STATUS.md` | **truth about the code**: milestone state, deviations, incidents, gotchas — read this first |
| `implementation-plan.md` | the authoritative spec of what to build |
| `AGENTS.md` | guidance for AI coding agents (constraints, commands) |
| `merge-plan.md`, `humanize-analysis.md`, `humanize2-explained.md`, `isabelle-humanize-feasibility.md` | the why: architecture and mechanism docs |

## Hard constraints (contributing)

- **Never modify IsabelleGym or hmz** — everything lives flow-side; upstream
  surprises get flow-side workarounds plus a record in `STATUS.md`.
- **No new third-party dependencies** — flow code is stdlib-only; the single
  allowed hmz import is `hmz.flows`.
- Fail-closed everywhere; the reviewer is never the termination authority —
  only the arbiter declares `complete`.

# isabelle_rlcr workspace template

A workspace is one problem: a git repository holding `problem.thy` (with a
`sorry` hole), this `.mcp.json`, and nothing else the loop has not made. The
flow writes `goal-tracker.md`, `.rlcr/` (journal, terminal state) and the
per-round commits.

`problem.thy` carries the task spec as a header comment -- the one source of
truth the gates and the arbiter read:

```
(* TASK: theorem=<name> imports=Main field=HOL *)
```

## Pinned for M0/M1 (record in every cycle)

- IsabelleGym commit: `0001971f77f51f051f23a269186ac32988e22390`
  (WSL-native clone `~/IsabelleGym`; image = pre-9391b28 base + prometheus patch)
- Builder: `kimi/moonshot-cn/kimi-k3:high` (kimi 0.39.1, user-level
  `~/.kimi-code/mcp.json` attaches the LSP MCP)
- Reviewer: `opencode/deepseek/deepseek-v4-flash:high` (opencode 1.18.27,
  `~/.config/opencode/opencode.json`: DeepSeek provider + isabellegym MCP,
  both verified; needs `DEEPSEEK_API_KEY`). DeepSeek-both-seats runs use the
  same spec for the builder. Fallback: `dsh/deepseek-v4-flash:high` with
  `reviewer_mcp: false` (no MCP in the dsh harness).
- Gym: `http://localhost:8000`, `ISABELLE_POOL_SIZE=6`

## `.mcp.json` paths

Machine-specific (M0 reality, treated as placeholders per AGENTS.md):
`command` is the `~/.venvs/lsp-mcp` venv python (has `mcp<2` + httpx);
`PYTHONPATH` is the WSL-native IsabelleGym clone. kimi ignores this file and
uses `~/.kimi-code/mcp.json` instead (workspace-trust blocks project-level
config in headless mode); Claude Code reads this file directly.

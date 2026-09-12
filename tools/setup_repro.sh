#!/usr/bin/env bash
# setup_repro.sh — reproduce the isabelle_rlcr HUMANIZE-SIDE wiring on a fresh
# WSL2 machine. The IsabelleGym server itself is NOT set up here: bring it up
# first from the IsabelleGym repo (turnkey image per its EXPORT.md, or
# ./setup.sh from a checkout per its README.md). This script assumes the gym
# answers at http://localhost:8001 and verifies it.
#
# What this sets up (idempotent — safe to re-run):
#   1. prerequisites check (WSL2, Docker, python3, git)
#   2. WSL memory (.wslconfig, 24 GB) — warns, Windows side needs a WSL restart
#   3. gym verification: healthz on :8001 + expected session heaps
#   4. lsp-mcp venv (~/.venvs/lsp-mcp, mcp<2 + httpx)
#   5. persistent streamable-http MCP service on 127.0.0.1:8849
#   6. harness AVAILABILITY CHECKS (opencode / kimi / dsh) — the harnesses
#      themselves are installed, logged in, and MCP-wired BY THE USER; the run
#      isolation config is applied separately per harness by
#      tools/setup_isolation.sh <harness...>
#   7. hmz venv (~/.venvs/hmz) — needs --hmz-src or a working `pip install hmz`
#   8. smoke checks: healthz + arbiter good/sorry probe
#
# Usage:
#   bash tools/setup_repro.sh [--gym-src /path/to/IsabelleGym]
#                             [--hmz-src /path/to/humanize2] [--check]
# Required manual input: a running IsabelleGym server (see above), the agent
# harnesses set up by the user, isolation via tools/setup_isolation.sh, and
# DEEPSEEK_API_KEY in ~/.bashrc.
set -euo pipefail

HMZ_SRC="" ; GYM_SRC="${HOME}/IsabelleGym" ; CHECK_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --gym-src) GYM_SRC="$2"; shift 2;;
    --hmz-src) HMZ_SRC="$2"; shift 2;;
    --check)   CHECK_ONLY=1; shift;;
    *) echo "unknown flag: $1" >&2; exit 2;;
  esac
done

GYM_PORT=8001
MCP_PORT=8849
ok()   { echo "  OK  $*"; }
warn() { echo "  !!  $*"; }
die()  { echo "  XX  $*" >&2; exit 1; }
step() { echo; echo "== $*"; }

step "1. prerequisites"
[ -n "${WSL_DISTRO_NAME:-}" ] || warn "WSL_DISTRO_NAME unset — this script targets WSL2"
command -v docker   >/dev/null || die "docker not found (install Docker Desktop with WSL integration)"
docker info >/dev/null 2>&1    || die "docker daemon not reachable (start Docker Desktop)"
command -v python3  >/dev/null || die "python3 not found"
command -v git      >/dev/null || die "git not found"
ok "docker, python3, git present"
[ "$CHECK_ONLY" = 1 ] && { echo; echo "(check mode — stopping after prerequisites)"; exit 0; }

step "2. WSL memory (.wslconfig = 24 GB)"
WIN_HOME="$(wslpath "$(cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')" 2>/dev/null || true)"
if [ -n "$WIN_HOME" ] && [ -w "$WIN_HOME" ]; then
  if ! grep -q '^memory=' "$WIN_HOME/.wslconfig" 2>/dev/null; then
    printf '[wsl2]\nmemory=24GB\n' >> "$WIN_HOME/.wslconfig"
    warn "wrote $WIN_HOME/.wslconfig — run 'wsl --shutdown' on Windows and reopen to apply"
  else
    ok ".wslconfig already sets memory"
  fi
else
  warn "cannot reach Windows profile — create C:\\Users\\<you>\\.wslconfig with: [wsl2] / memory=24GB, then 'wsl --shutdown'"
fi
FREE_GB="$(free -g | awk '/^Mem:/ {print $2}')"
[ "${FREE_GB:-0}" -ge 20 ] && ok "WSL has ${FREE_GB} GB" || warn "WSL sees only ${FREE_GB} GB — the gym container will OOM under load until .wslconfig applies"

step "3. IsabelleGym server verification (setup is the IsabelleGym repo's job)"
curl -s -m 5 "http://localhost:$GYM_PORT/healthz" | grep -q alive \
  && ok "gym healthy on :$GYM_PORT" \
  || die "no gym on :$GYM_PORT — set it up first from the IsabelleGym repo: turnkey image per its EXPORT.md, or ./setup.sh from a checkout per its README.md"
HEAPS="$(curl -s -m 10 "http://localhost:$GYM_PORT/api/v1/heaps/available" || true)"
for want in HOL HOL-Library HOL-Computational_Algebra; do
  echo "$HEAPS" | grep -q "\"$want\"" && ok "heap: $want" || warn "heap missing: $want (first runs on its theories will be slow)"
done

step "4. lsp-mcp venv"
[ -d "$GYM_SRC/mcp_lsp_server" ] || die "$GYM_SRC has no mcp_lsp_server — pass --gym-src /path/to/IsabelleGym"
if [ ! -x "$HOME/.venvs/lsp-mcp/bin/python" ]; then
  python3 -m venv "$HOME/.venvs/lsp-mcp"
  "$HOME/.venvs/lsp-mcp/bin/pip" install -q --upgrade pip
  "$HOME/.venvs/lsp-mcp/bin/pip" install -q 'mcp<2' httpx
  ok "created ~/.venvs/lsp-mcp (mcp<2 pin is load-bearing)"
else
  ok "~/.venvs/lsp-mcp exists"
fi

step "5. persistent MCP service (127.0.0.1:$MCP_PORT)"
if ! curl -s -m 5 -X POST "http://127.0.0.1:$MCP_PORT/mcp" -H 'Content-Type: application/json' \
     -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"setup","version":"0"}}}' \
     | grep -q '"result"'; then
  pkill -f 'mcp_lsp_server.ap[p]' 2>/dev/null || true
  ( cd "$GYM_SRC" && nohup env PYTHONPATH=. \
      ISABELLE_MCP_LSP_GYM_URL="http://localhost:$GYM_PORT" \
      ISABELLE_MCP_LSP_TRANSPORT=streamable-http \
      ISABELLE_MCP_LSP_HOST=127.0.0.1 ISABELLE_MCP_LSP_PORT=$MCP_PORT \
      ISABELLE_MCP_LSP_LOAD_TIMEOUT=300 ISABELLE_MCP_LSP_HTTP_TIMEOUT=900 \
      ISABELLE_MCP_LSP_SCRATCH_POOL_SIZE=1 \
      "$HOME/.venvs/lsp-mcp/bin/python" -m mcp_lsp_server.app \
      > "$HOME/mcp-lsp-http.log" 2>&1 & )
  sleep 6
fi
curl -s -m 5 -X POST "http://127.0.0.1:$MCP_PORT/mcp" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"setup","version":"0"}}}' \
  | grep -q '"result"' && ok "MCP service answering on :$MCP_PORT" || die "MCP service not answering — see ~/mcp-lsp-http.log"

step "6. harness availability (user-managed — checked, never configured)"
# The harnesses themselves (install, login, provider/API keys, MCP attachment)
# are set up by the user; the run constraints are applied separately by
# tools/setup_isolation.sh <harness...>. This step only reports.
OC="$HOME/.config/opencode/opencode.json"
if command -v opencode >/dev/null 2>&1 || [ -x "$HOME/.opencode/bin/opencode" ]; then
  ok "opencode installed"
  [ -f "$OC" ] || warn "$OC missing — create it (provider + MCP) yourself"
  if [ -f "$OC" ]; then
    python3 - "$OC" <<'PY' || warn "opencode.json: check provider/MCP/isolation manually"
import json, sys
d = json.load(open(sys.argv[1]))
assert "deepseek" in d.get("provider", {}), "no deepseek provider"
mcp = d.get("mcp", {}).get("isabellegym", {})
assert mcp.get("type") == "remote", "isabellegym MCP not remote (stdio drops!)"
b = d.get("permission", {}).get("bash", {})
iso = b.get("curl *") == "deny" and list(b or {"*": 1})[0] == "*"
print("  OK  opencode.json: provider + remote MCP present; isolation", "present" if iso else "MISSING — run: bash tools/setup_isolation.sh opencode")
PY
  fi
else
  warn "opencode not installed — https://opencode.ai"
fi
if command -v kimi >/dev/null 2>&1 || [ -x "$HOME/.kimi-code/bin/kimi" ]; then
  ok "kimi installed"
  [ -f "$HOME/.kimi-code/config.toml" ] || warn "~/.kimi-code/config.toml missing — run 'kimi login'"
  grep -q 'isabelle_rlcr run isolation' "$HOME/.kimi-code/config.toml" 2>/dev/null \
    && ok "kimi isolation present" || warn "kimi isolation MISSING — run: bash tools/setup_isolation.sh kimi"
else
  warn "kimi not installed (needed only for kimi-seat runs) — https://code.kimi.com"
fi
command -v dsh >/dev/null 2>&1 && ok "dsh installed" || warn "dsh not installed (optional fallback harness)"
{ grep -q 'DEEPSEEK_API_KEY="sk-' "$HOME/.bashrc" 2>/dev/null || [ -n "${DEEPSEEK_API_KEY:-}" ]; } \
  && ok "DEEPSEEK_API_KEY present" || warn "DEEPSEEK_API_KEY not set — add to ~/.bashrc (and 'opencode auth login' if you use it)"

step "7. hmz venv"
if [ -x "$HOME/.venvs/hmz/bin/hmz" ]; then
  ok "~/.venvs/hmz exists"
elif [ -n "$HMZ_SRC" ]; then
  python3 -m venv "$HOME/.venvs/hmz"
  "$HOME/.venvs/hmz/bin/pip" install -q --upgrade pip
  "$HOME/.venvs/hmz/bin/pip" install -q -e "$HMZ_SRC"
  ok "installed hmz from $HMZ_SRC"
else
  warn "hmz not installed — re-run with --hmz-src /path/to/humanize2 (or pip install hmz into ~/.venvs/hmz)"
fi

step "8. smoke checks"
curl -s -m 5 "http://localhost:$GYM_PORT/healthz" | grep -q alive && ok "healthz" || die "healthz failed"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -x "$HOME/.venvs/hmz/bin/python" ]; then
  ( cd "$SCRIPT_DIR/.." && "$HOME/.venvs/hmz/bin/python" tools/smoke_lsp.py --arbiter ) \
    && ok "arbiter probe (good theory verified, sorry rejected)" || warn "arbiter probe failed — check gym and heaps"
else
  warn "skipping arbiter probe (no hmz venv python)"
fi

echo
echo "== setup complete. To run a problem:"
echo "   python tools/import_problem.py <source.thy> ~/runs/<name>"
echo "   printf 'readiness_timeout_s: 600\n' > /tmp/rlcr.yaml"
echo "   cd ~/runs/<name> && HUMANIZE_SENTRY=off ~/.venvs/hmz/bin/hmz exec \\"
echo "     -f $SCRIPT_DIR/../flows/isabelle_rlcr:rlcr -c /tmp/rlcr.yaml \\"
echo "     -a opencode/deepseek/deepseek-v4-pro:high -a opencode/deepseek/deepseek-v4-pro:high \\"
echo "     'prove the pending theorem in problem.thy in the current workspace, discharge all sorries.'"

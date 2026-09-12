#!/usr/bin/env bash
# setup_repro.sh — reproduce the isabelle_rlcr run environment on a fresh WSL2 machine.
#
# What this sets up (idempotent — safe to re-run):
#   1. prerequisites check (WSL2, Docker, python3, git)
#   2. WSL memory (.wslconfig, 24 GB) — warns, Windows side needs a WSL restart
#   3. IsabelleGym RC0 container (from --image-tar, or expects image present)
#      + heap volume (from --volume-tar, or empty — heaps build on first use, SLOW)
#   4. gym server inside the container (port 8001)
#   5. lsp-mcp venv (~/.venvs/lsp-mcp, mcp<2 + httpx)
#   6. persistent streamable-http MCP service on 127.0.0.1:8849
#   7. opencode CLI + ~/.config/opencode/opencode.json: DeepSeek provider,
#      remote MCP, and the PERMISSION DENIES (no curl/wget/docker/kill,
#      no local isabelle builds, no webfetch) — these rules ARE the run
#      constraints; do not weaken them
#   8. hmz venv (~/.venvs/hmz) — needs --hmz-src or a working `pip install hmz`
#   9. .bashrc PATH exports (kimi + opencode bins)
#  10. smoke checks: healthz, opencode mcp list, arbiter good/sorry probe
#
# Usage:
#   bash tools/setup_repro.sh [--image-tar isabellegym-rc0.tar[.gz]]
#                             [--volume-tar isabelle_rc0_user_data.tar.gz]
#                             [--hmz-src /path/to/humanize2] [--gym-src /path/to/IsabelleGym]
#                             [--check]
# Required manual input: DEEPSEEK_API_KEY in ~/.bashrc (script appends a
# placeholder line if absent — edit it!), plus the docker image/volume tarballs.
set -euo pipefail

IMAGE_TAR="" ; VOLUME_TAR="" ; HMZ_SRC="" ; GYM_SRC="${HOME}/IsabelleGym" ; CHECK_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --image-tar)  IMAGE_TAR="$2"; shift 2;;
    --volume-tar) VOLUME_TAR="$2"; shift 2;;
    --hmz-src)    HMZ_SRC="$2"; shift 2;;
    --gym-src)    GYM_SRC="$2"; shift 2;;
    --check)      CHECK_ONLY=1; shift;;
    *) echo "unknown flag: $1" >&2; exit 2;;
  esac
done

CONTAINER=isabelle-gym-rc0
IMAGE=isabellegym-isabelle-gym:2026rc0
VOLUME=isabelle_rc0_user_data
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
[ "${FREE_GB:-0}" -ge 20 ] && ok "WSL has ${FREE_GB} GB" || warn "WSL sees only ${FREE_GB} GB — containers will OOM under load until .wslconfig applies"

step "3. IsabelleGym RC0 container"
if [ -n "$IMAGE_TAR" ]; then
  docker load -i "$IMAGE_TAR" && ok "loaded image from $IMAGE_TAR"
fi
docker image inspect "$IMAGE" >/dev/null 2>&1 || die "image $IMAGE missing — pass --image-tar"
docker volume inspect "$VOLUME" >/dev/null 2>&1 || docker volume create "$VOLUME" >/dev/null
if [ -n "$VOLUME_TAR" ]; then
  docker run --rm -v "$VOLUME":/data -v "$(cd "$(dirname "$VOLUME_TAR")" && pwd)":/backup alpine \
    sh -c "cd /data && tar xzf /backup/$(basename "$VOLUME_TAR")" && ok "restored heap volume from $VOLUME_TAR"
fi
if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  ENVF="$GYM_SRC/.env"
  [ -f "$ENVF" ] || die "IsabelleGym checkout not found at $GYM_SRC (need its .env) — pass --gym-src"
  docker run -d --name "$CONTAINER" --memory 20g -p $GYM_PORT:8000 \
    -v "$VOLUME":/root/.isabelle --env-file "$ENVF" -w /app \
    "$IMAGE" sleep infinity >/dev/null
  ok "container $CONTAINER started (20g cap, :$GYM_PORT)"
else
  ok "container $CONTAINER already running"
fi

step "4. gym server"
docker exec "$CONTAINER" mkdir -p /app/logs   # a recreated container lacks it; the redirect kills the server silently otherwise
if ! curl -s -m 3 "http://localhost:$GYM_PORT/healthz" | grep -q alive; then
  docker exec -d "$CONTAINER" bash -c 'cd /app && python -m server.app.main > /app/logs/m0-server.out 2>&1'
  for i in $(seq 1 24); do sleep 5; curl -s -m 3 "http://localhost:$GYM_PORT/healthz" | grep -q alive && break; done
fi
curl -s -m 3 "http://localhost:$GYM_PORT/healthz" | grep -q alive && ok "gym healthy on :$GYM_PORT" || die "gym server failed to start — see: docker exec $CONTAINER tail /app/logs/m0-server.out"

step "5. lsp-mcp venv"
[ -d "$GYM_SRC/mcp_lsp_server" ] || die "$GYM_SRC has no mcp_lsp_server — pass --gym-src /path/to/IsabelleGym"
if [ ! -x "$HOME/.venvs/lsp-mcp/bin/python" ]; then
  python3 -m venv "$HOME/.venvs/lsp-mcp"
  "$HOME/.venvs/lsp-mcp/bin/pip" install -q --upgrade pip
  "$HOME/.venvs/lsp-mcp/bin/pip" install -q 'mcp<2' httpx
  ok "created ~/.venvs/lsp-mcp (mcp<2 pin is load-bearing)"
else
  ok "~/.venvs/lsp-mcp exists"
fi

step "6. persistent MCP service (127.0.0.1:$MCP_PORT)"
if ! curl -s -m 3 "http://127.0.0.1:$MCP_PORT/mcp" -o /dev/null -w '%{http_code}' | grep -qE '400|405|406'; then
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

step "7. opencode + config (DeepSeek provider, remote MCP, permission denies)"
if ! command -v opencode >/dev/null 2>&1; then
  if [ -x "$HOME/.opencode/bin/opencode" ]; then
    export PATH="$HOME/.opencode/bin:$PATH"
  else
    warn "opencode not found — install from https://opencode.ai (curl -fsSL https://opencode.ai/install | bash), then re-run"
  fi
fi
OC="$HOME/.config/opencode/opencode.json"
mkdir -p "$HOME/.config/opencode"
python3 - "$OC" <<'PY'
import json, sys
p = sys.argv[1]
try:
    d = json.load(open(p))
except Exception:
    d = {"$schema": "https://opencode.ai/config.json"}
d.setdefault("provider", {})["deepseek"] = {
    "npm": "@ai-sdk/openai-compatible",
    "name": "DeepSeek",
    "options": {"baseURL": "https://api.deepseek.com", "apiKey": "{env:DEEPSEEK_API_KEY}"},
}
d.setdefault("mcp", {})["isabellegym"] = {
    "type": "remote", "url": "http://127.0.0.1:8849/mcp", "enabled": True, "timeout": 300000,
}
# RUN CONSTRAINTS — docs order: catch-all first, denies after (last match wins)
d["permission"] = {
    "webfetch": "deny",
    "bash": {
        "*": "allow",
        "curl *": "deny", "wget *": "deny", "docker *": "deny", "kill *": "deny",
        "*/Isabelle2025-2/bin/isabelle*": "deny", "./bin/isabelle*": "deny",
        "isabelle build*": "deny",
    },
}
json.dump(d, open(p, "w"), indent=2)
print("  OK  wrote", p)
PY
grep -q 'DEEPSEEK_API_KEY="sk-' "$HOME/.bashrc" 2>/dev/null || {
  echo 'export DEEPSEEK_API_KEY="sk-REPLACE-ME"' >> "$HOME/.bashrc"
  warn "added DEEPSEEK_API_KEY placeholder to ~/.bashrc — EDIT IT with your real key"
}
opencode mcp list 2>/dev/null | grep -q isabellegym && ok "opencode sees isabellegym MCP" || warn "opencode mcp list did not confirm — check opencode install"

step "8. hmz venv"
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

step "9. .bashrc PATH exports"
grep -q '.kimi-code/bin'    "$HOME/.bashrc" 2>/dev/null || echo 'export PATH="$HOME/.kimi-code/bin:$PATH"'    >> "$HOME/.bashrc"
grep -q '.opencode/bin'     "$HOME/.bashrc" 2>/dev/null || echo 'export PATH="$HOME/.opencode/bin:$PATH"'     >> "$HOME/.bashrc"
ok "kimi + opencode bin dirs on PATH for interactive shells"

step "10. smoke checks"
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

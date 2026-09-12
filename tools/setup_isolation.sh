#!/usr/bin/env bash
# setup_isolation.sh — write the RUN ISOLATION config for the given harness(es).
#
# Isolation = the structural constraints the runs require: no internet
# (webfetch/search), no curl/wget/docker/kill, no local isabelle builds.
# These are per-harness configs; the harnesses themselves (install, login,
# provider/API keys, MCP attachment) are set up by the user — see README
# "Reproducing the run environment".
#
# Usage:
#   bash tools/setup_isolation.sh [--dry-run] opencode|kimi|dsh|all ...
#
#   opencode — permission block in ~/.config/opencode/opencode.json
#              (docs order: catch-all "*" first, denies after; last matching
#              rule wins — verified by A/B on 1.18.27). Provider/MCP config
#              is preserved.
#   kimi     — [[permission.rules]] denies appended to ~/.kimi-code/config.toml
#              (first matching rule wins: if your config already has broad
#              allow rules, review ordering manually). Tools matched:
#              Bash(cmd *) denies + FetchURL/WebSearch tool denies.
#   dsh      — no config-level isolation exists (bash + fs tools only);
#              isolation is prompt-level (reviewer_mcp: false + prompt rules).
#
# Idempotent. --dry-run prints exactly what would be written, changes nothing.
set -euo pipefail

DRY=0; HARNESSES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift;;
    opencode|kimi|dsh|all) HARNESSES+=("$1"); shift;;
    *) echo "unknown harness: $1" >&2; exit 2;;
  esac
done
[ ${#HARNESSES[@]} -gt 0 ] || { sed -n '1,20p' "$0"; exit 2; }
for h in "${HARNESSES[@]}"; do [ "$h" = all ] && HARNESSES=(opencode kimi dsh); done

ok()   { echo "  OK  $*"; }
warn() { echo "  !!  $*"; }

for h in "${HARNESSES[@]}"; do
  echo "== $h"
  case "$h" in

  opencode)
    OC="$HOME/.config/opencode/opencode.json"
    if [ "$DRY" = 1 ]; then
      echo "  would write into $OC (preserving provider/mcp):"
      cat <<'EOF'
  "permission": {
    "webfetch": "deny",
    "bash": {
      "*": "allow",
      "curl *": "deny", "wget *": "deny", "docker *": "deny", "kill *": "deny",
      "*/Isabelle2025-2/bin/isabelle*": "deny", "./bin/isabelle*": "deny",
      "isabelle build*": "deny"
    }
  }
EOF
      continue
    fi
    mkdir -p "$HOME/.config/opencode"
    python3 - "$OC" <<'PY'
import json, sys
p = sys.argv[1]
try:
    d = json.load(open(p))
except Exception:
    d = {"$schema": "https://opencode.ai/config.json"}
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
print("  OK  wrote permission block into", p)
PY
    python3 - "$OC" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))["permission"]["bash"]
assert d["*"] == "allow" and d["curl *"] == "deny" and list(d)[0] == "*", "order/content wrong"
print("  OK  verified: catch-all first, denies present (last-match-wins order)")
PY
    ;;

  kimi)
    KC="$HOME/.kimi-code/config.toml"
    MARKER="# isabelle_rlcr run isolation (tools/setup_isolation.sh)"
    BLOCK="$MARKER
[[permission.rules]]
decision = \"deny\"
pattern = \"Bash(curl *)\"
reason = \"rlcr run isolation: no internet\"
[[permission.rules]]
decision = \"deny\"
pattern = \"Bash(wget *)\"
reason = \"rlcr run isolation: no internet\"
[[permission.rules]]
decision = \"deny\"
pattern = \"Bash(docker *)\"
reason = \"rlcr run isolation: no container access\"
[[permission.rules]]
decision = \"deny\"
pattern = \"Bash(kill *)\"
reason = \"rlcr run isolation: no process killing\"
[[permission.rules]]
decision = \"deny\"
pattern = \"Bash(*/Isabelle2025-2/bin/isabelle*)\"
reason = \"rlcr run isolation: no local isabelle\"
[[permission.rules]]
decision = \"deny\"
pattern = \"Bash(./bin/isabelle*)\"
reason = \"rlcr run isolation: no local isabelle\"
[[permission.rules]]
decision = \"deny\"
pattern = \"Bash(isabelle build*)\"
reason = \"rlcr run isolation: no side-channel builds\"
[[permission.rules]]
decision = \"deny\"
pattern = \"FetchURL\"
reason = \"rlcr run isolation: no internet\"
[[permission.rules]]
decision = \"deny\"
pattern = \"WebSearch\"
reason = \"rlcr run isolation: no internet\""
    if [ -f "$KC" ] && grep -qF "$MARKER" "$KC"; then
      ok "isolation block already present in $KC"
      continue
    fi
    if [ "$DRY" = 1 ]; then
      echo "  would append to $KC:"
      echo "$BLOCK" | sed 's/^/    /'
      continue
    fi
    mkdir -p "$HOME/.kimi-code"
    [ -f "$KC" ] || printf '# Kimi Code CLI config\n' > "$KC"
    grep -q '\[\[permission.rules\]\]' "$KC" \
      && warn "existing [[permission.rules]] found — kimi applies the FIRST matching rule; ensure no broad allow precedes the denies below"
    printf '\n%s\n' "$BLOCK" >> "$KC"
    ok "appended deny rules to $KC"
    ;;

  dsh)
    warn "dsh has no config-level isolation (bash + fs tools only, no MCP)."
    warn "Isolation is prompt-level: run reviewers with reviewer_mcp: false and"
    warn "rely on the flow's round0/review prompts (no internet, no side channels)."
    ;;

  esac
done

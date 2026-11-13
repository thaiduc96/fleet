#!/bin/bash
# Cài fleetd trên một Mac: uv + venv, token, config, LaunchAgent.
#   ./install.sh                          # máy đầu tiên (tạo token mới)
#   ./install.sh --token <token>          # máy thứ hai (dùng chung token)
#   ./install.sh --machine work --label "💼 Công ty"
set -euo pipefail
cd "$(dirname "$0")"

TOKEN=""; MACHINE=""; LABEL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --token) TOKEN="$2"; shift 2;;
    --machine) MACHINE="$2"; shift 2;;
    --label) LABEL="$2"; shift 2;;
    *) echo "unknown option $1" >&2; exit 2;;
  esac
done

UV="$(command -v uv || true)"
if [[ -z "$UV" && -x "$HOME/.local/bin/uv" ]]; then UV="$HOME/.local/bin/uv"; fi
if [[ -z "$UV" ]]; then
  echo "→ Cài uv (https://astral.sh/uv) vào ~/.local/bin"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  UV="$HOME/.local/bin/uv"
fi

# The venv lives outside the repo: ~/Documents is often synced by iCloud Drive,
# which marks files hidden/dataless and makes Python skip the editable .pth.
export UV_PROJECT_ENVIRONMENT="${FLEET_HOME:-$HOME/.fleet}/venv"
mkdir -p "${FLEET_HOME:-$HOME/.fleet}"
echo "$UV" > "${FLEET_HOME:-$HOME/.fleet}/uv-path"   # remembered for `bin/fleet update`
echo "→ Tạo môi trường Python ở $UV_PROJECT_ENVIRONMENT"
# --reinstall-package: the version number rarely changes, so force a fresh build of fleetd itself.
"$UV" sync --no-dev -q --no-editable --reinstall-package fleet

chmod +x bin/fleet
if [[ -n "$TOKEN" ]]; then bin/fleet token set "$TOKEN"; else bin/fleet token init; fi
bin/fleet config ${MACHINE:+"$MACHINE"} ${LABEL:+"$LABEL"}
bin/fleet install

cat <<EOF

Xong. Các bước tiếp theo:
  1. Mở cho tailnet (HTTPS):   bin/fleet serve
  2. Trên máy kia, thêm máy này: bin/fleet peer add <id> https://<máy-này>.<tailnet>.ts.net "<nhãn>"
  3. Mở dashboard: http://127.0.0.1:7777  hoặc URL ts.net ở bước 1 (cả trên iPhone)
EOF

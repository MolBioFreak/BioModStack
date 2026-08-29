#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"

export PATH="$HOME/bin:$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

# Preserve service-owned values while loading the shared authentication policy.
declare -A _BMS_LAUNCH_ENV=()
while IFS= read -r key; do
    _BMS_LAUNCH_ENV["$key"]="${!key}"
done < <(compgen -A variable BMS_)

if [[ -f "$HOME/.biomodstack/env.sh" ]]; then
    source "$HOME/.biomodstack/env.sh"
fi
for key in "${!_BMS_LAUNCH_ENV[@]}"; do
    export "$key=${_BMS_LAUNCH_ENV[$key]}"
done

if ! command -v uv >/dev/null 2>&1; then
    echo "BioModStack mobile update publisher requires uv on PATH" >&2
    exit 1
fi

export BMS_HOME="$PROJECT_DIR"
export UV_CACHE_DIR="${UV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/biomodstack/uv}"
mkdir -p "$UV_CACHE_DIR"

cd "$PROJECT_DIR/platform/api"
port="${BMS_MOBILE_UPDATE_PUBLISHER_PORT:-18003}"
exec uv run --frozen uvicorn mobile_update_publisher_app:app \
    --host 127.0.0.1 \
    --port "$port" \
    --no-proxy-headers \
    --no-access-log

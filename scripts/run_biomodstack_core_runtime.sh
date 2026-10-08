#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"
COMPOSE_FILE="$PROJECT_DIR/compose.core-runtime.yml"
ACTION="${1:-up}"
if [ "$#" -gt 0 ]; then
    shift
fi

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
export BMS_HOME="$PROJECT_DIR"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-biomodstack-core-runtime}"

if [ -f "$HOME/.biomodstack/env.sh" ]; then
    source "$HOME/.biomodstack/env.sh"
fi

CORE_RUNTIME_ENV_FILE="${BMS_CORE_RUNTIME_ENV_FILE:-$PROJECT_DIR/.env.core-runtime.local}"
compose_extra_args=()
if [ -f "$CORE_RUNTIME_ENV_FILE" ]; then
    set -a
    source "$CORE_RUNTIME_ENV_FILE"
    set +a
    compose_extra_args=(--env-file "$CORE_RUNTIME_ENV_FILE")
fi

if [ -z "${BMS_STATE_DIR:-}" ]; then
    if [ -d /mnt/BioModStack ]; then
        export BMS_STATE_DIR=/mnt/BioModStack
    else
        export BMS_STATE_DIR="$HOME/.biomodstack"
    fi
fi

export BMS_CONTAINER_STATE_PATH="${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}"
export BMS_API_HOST_PORT="${BMS_API_HOST_PORT:-8000}"
export BMS_WEB_HOST_PORT="${BMS_WEB_HOST_PORT:-5173}"
mkdir -p "$BMS_STATE_DIR"

compose_cmd=()
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    compose_cmd=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    compose_cmd=(docker-compose)
else
    echo "BioModStack container runtime requires docker compose (plugin or docker-compose binary)." >&2
    exit 1
fi

run_compose() {
    "${compose_cmd[@]}" "${compose_extra_args[@]}" -f "$COMPOSE_FILE" "$@"
}

case "$ACTION" in
    up)
        exec "${compose_cmd[@]}" "${compose_extra_args[@]}" -f "$COMPOSE_FILE" up --build --remove-orphans "$@"
        ;;
    down)
        run_compose down --remove-orphans "$@"
        ;;
    restart)
        run_compose restart "$@"
        ;;
    logs)
        run_compose logs "$@"
        ;;
    ps)
        run_compose ps "$@"
        ;;
    config)
        run_compose config "$@"
        ;;
    pull)
        run_compose pull "$@"
        ;;
    *)
        echo "Usage: $0 {up|down|restart|logs|ps|config|pull} [compose-args...]" >&2
        exit 1
        ;;
esac

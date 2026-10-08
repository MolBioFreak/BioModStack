#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CONFIG="$ROOT/docker/tailnet-production-proxy.conf"
COMPOSE=(docker compose --project-name biomodstack-tailnet-control --project-directory "$ROOT" -f "$ROOT/compose.core-runtime.yml")
SERVICE=tailnet-production-proxy
CONTAINER=biomodstack-tailnet-production-proxy

[[ -f "$CONFIG" ]] || { printf 'missing proxy config: %s\n' "$CONFIG" >&2; exit 1; }
export BMS_TAILNET_PROXY_CONFIG_SHA256="$(sha256sum "$CONFIG" | cut -d' ' -f1)"

start_proxy() {
  "${COMPOSE[@]}" up -d --no-deps --no-build "$SERVICE"
  PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -c \
    'from pathlib import Path; import biomodstack_tailnet as t; print(t._validated_production_tailnet_proxy(Path.cwd()))' \
    </dev/null
}

case "${1:-status}" in
  start)
    start_proxy
    ;;
  adopt)
    # Refuse to replace unknown state: the current container must first pass the
    # exact pinned image/config/command provenance validator.
    PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -c \
      'from pathlib import Path; import biomodstack_tailnet as t; t._validated_production_tailnet_proxy(Path.cwd())' \
      </dev/null
    docker rm -f "$CONTAINER"
    start_proxy
    ;;
  restart)
    "${COMPOSE[@]}" up -d --no-deps --no-build --force-recreate "$SERVICE"
    PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -c \
      'from pathlib import Path; import biomodstack_tailnet as t; print(t._validated_production_tailnet_proxy(Path.cwd()))' \
      </dev/null
    ;;
  stop)
    "${COMPOSE[@]}" stop "$SERVICE"
    ;;
  status)
    "${COMPOSE[@]}" ps "$SERVICE"
    docker inspect "$CONTAINER" --format '{{json .Config.Labels}} {{json .HostConfig.RestartPolicy}} {{.Image}}'
    ;;
  *)
    printf 'usage: %s {start|adopt|restart|stop|status}\n' "$0" >&2
    exit 2
    ;;
esac

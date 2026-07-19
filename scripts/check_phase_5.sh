#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHIM_DIR="$(mktemp -d /tmp/bms-phase5-corepack.XXXXXX)"
TEST_HOME="$(mktemp -d /tmp/bms-phase5-home.XXXXXX)"
JUNIT_PATH="${BMS_PHASE5_JUNIT_PATH:-/tmp/bms-phase5-api.xml}"
mkdir -p "$TEST_HOME/.config" "$TEST_HOME/.cache"

runtime_fingerprint() {
  {
    if command -v systemctl >/dev/null 2>&1; then
      systemctl --user show biomodstack-api.service \
        -p ActiveState -p SubState -p MainPID --no-pager 2>/dev/null || true
    else
      printf '%s\n' 'systemctl=unavailable'
    fi

    if command -v ss >/dev/null 2>&1; then
      ss -H -ltn 'sport = :8002' 2>/dev/null || true
    else
      printf '%s\n' 'ss=unavailable'
    fi

    if command -v docker >/dev/null 2>&1; then
      docker ps -aq \
        --filter 'label=com.docker.compose.project=biomodstack-core-runtime' 2>/dev/null \
        | sort \
        | while IFS= read -r container_id; do
            [[ -n "$container_id" ]] || continue
            docker inspect --format \
              '{{.Id}} {{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
              "$container_id" 2>/dev/null || true
          done \
        | sort
    else
      printf '%s\n' 'docker=unavailable'
    fi
  } | sha256sum | cut -d' ' -f1
}

BEFORE_RUNTIME="$(runtime_fingerprint)"
finish() {
  local rc=$?
  trap - EXIT
  local after_runtime
  after_runtime="$(runtime_fingerprint)"
  rm -rf "$SHIM_DIR" "$TEST_HOME"

  if [[ "$BEFORE_RUNTIME" != "$after_runtime" ]]; then
    printf 'ERROR: runtime state changed during Phase 5 validation\n' >&2
    printf 'before=%s\nafter=%s\n' "$BEFORE_RUNTIME" "$after_runtime" >&2
    exit 99
  fi

  exit "$rc"
}
trap finish EXIT

run_api_pytest() {
  env -i \
    HOME="$TEST_HOME" \
    XDG_CONFIG_HOME="$TEST_HOME/.config" \
    XDG_CACHE_HOME="$TEST_HOME/.cache" \
    PATH="$PATH" \
    USER="${USER:-phase5}" \
    LOGNAME="${LOGNAME:-${USER:-phase5}}" \
    LANG="${LANG:-C.UTF-8}" \
    LC_ALL="${LC_ALL:-C.UTF-8}" \
    TMPDIR="${TMPDIR:-/tmp}" \
    PYTHONDONTWRITEBYTECODE=1 \
    BIOXP_LIVE_TESTS=0 \
    BMS_RUNTIME_INTEGRATION_TESTS=0 \
    BMS_ALLOW_SERVICE_CONTROL_TESTS=0 \
    "$ROOT/platform/api/.venv/bin/python" -m pytest "$@"
}

export BIOXP_LIVE_TESTS=0
export BMS_RUNTIME_INTEGRATION_TESTS=0
export BMS_ALLOW_SERVICE_CONTROL_TESTS=0

command -v uv >/dev/null 2>&1
command -v docker >/dev/null 2>&1
command -v corepack >/dev/null 2>&1

if ! command -v pnpm >/dev/null 2>&1; then
  corepack enable --install-directory "$SHIM_DIR" >/dev/null
  export PATH="$SHIM_DIR:$PATH"
fi

printf '%s\n' '[1/7] Full API acceptance suite'
(
  cd "$ROOT/platform/api"
  run_api_pytest -q --junitxml="$JUNIT_PATH" tests
)

printf '%s\n' '[2/7] Compact BioXP API and containment contracts'
(
  cd "$ROOT/platform/api"
  run_api_pytest -q tests/test_bioxp_compact_api.py tests/test_bioxp_phase1_containment.py
)

printf '%s\n' '[3/7] Compose contract rendering (validation-only secret)'
BMS_ANALYTICAL_DB_PASSWORD="${BMS_ANALYTICAL_DB_PASSWORD:-phase5-validation-only}" \
  docker compose -f "$ROOT/compose.core-runtime.yml" config >/dev/null

printf '%s\n' '[4/7] Transactional release plan (no deploy)'
uv run --directory "$ROOT/platform/api" \
  python ../../scripts/biomodstack_release.py plan

printf '%s\n' '[5/7] Frontend test suite'
pnpm --dir "$ROOT/platform/frontend" test

printf '%s\n' '[6/7] Isolated frontend production build'
pnpm --dir "$ROOT/platform/frontend" run build:isolated

printf '%s\n' '[7/7] Electron test suite'
pnpm --dir "$ROOT/platform/desktop-electron" test

printf 'Phase 5 check passed; runtime fingerprint unchanged: %s\n' "$BEFORE_RUNTIME"

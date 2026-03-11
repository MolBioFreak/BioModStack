#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${HOME}/.biomodstack/env.sh"
CCTV_CONFIG_FILE="${HOME}/.config/coolercontrol/cctv.json"

DAEMON_ADDRESS="${BMS_COOLERCONTROL_DAEMON_ADDRESS:-127.0.0.1}"
DAEMON_PORT="${BMS_COOLERCONTROL_DAEMON_PORT:-11987}"
DAEMON_USERNAME="${BMS_COOLERCONTROL_USERNAME:-CCAdmin}"
DAEMON_PASSWORD="${BMS_COOLERCONTROL_PASSWORD:-coolAdmin}"
AUTO_MODE_NAME="${BMS_COOLERCONTROL_MODE_AUTO:-BMS Auto}"
MANUAL_MODE_NAME="${BMS_COOLERCONTROL_MODE_MANUAL:-BMS Manual}"

log() {
  printf '[coolercontrol-fix] %s\n' "$*"
}

warn() {
  printf '[coolercontrol-fix] WARNING: %s\n' "$*" >&2
}

die() {
  printf '[coolercontrol-fix] ERROR: %s\n' "$*" >&2
  exit 1
}

daemon_base_url() {
  printf 'http://%s:%s\n' "${DAEMON_ADDRESS}" "${DAEMON_PORT}"
}

wait_for_daemon_health() {
  local waited=0
  local max_wait=45

  while ! curl -fsS "$(daemon_base_url)/health" >/dev/null 2>&1; do
    sleep 1
    waited=$((waited + 1))
    if [ "${waited}" -ge "${max_wait}" ]; then
      return 1
    fi
  done
  return 0
}

wait_for_apt_locks() {
  local waited=0
  local max_wait=300
  local locks=(
    "/var/lib/apt/lists/lock"
    "/var/lib/dpkg/lock-frontend"
    "/var/cache/apt/archives/lock"
  )

  while sudo fuser "${locks[@]}" >/dev/null 2>&1; do
    if [ "${waited}" -eq 0 ]; then
      log "Waiting for apt/dpkg lock to clear..."
    fi
    sleep 5
    waited=$((waited + 5))
    if [ "${waited}" -ge "${max_wait}" ]; then
      die "apt/dpkg lock held longer than ${max_wait}s. Retry after package updates finish."
    fi
  done
}

ensure_env_file() {
  mkdir -p "$(dirname "${ENV_FILE}")"
  if [ ! -f "${ENV_FILE}" ]; then
    cat > "${ENV_FILE}" <<'EOF'
#!/bin/bash
# BioModStack Environment Variables
EOF
  fi
}

upsert_export() {
  local key="$1"
  local value="$2"
  local tmp
  local escaped

  tmp="$(mktemp)"
  grep -vE "^export ${key}=" "${ENV_FILE}" > "${tmp}" || true
  mv "${tmp}" "${ENV_FILE}"

  printf -v escaped '%q' "${value}"
  printf 'export %s=%s\n' "${key}" "${escaped}" >> "${ENV_FILE}"
}

resolve_cctv_bin() {
  if [ -n "${BMS_COOLERCONTROL_CLI:-}" ] && [ -x "${BMS_COOLERCONTROL_CLI}" ]; then
    printf '%s\n' "${BMS_COOLERCONTROL_CLI}"
    return 0
  fi
  if [ -x "/usr/bin/cctv" ]; then
    printf '%s\n' "/usr/bin/cctv"
    return 0
  fi
  if [ -x "/usr/local/bin/cctv" ]; then
    printf '%s\n' "/usr/local/bin/cctv"
    return 0
  fi
  if [ -x "${HOME}/.cargo/bin/cctv" ]; then
    printf '%s\n' "${HOME}/.cargo/bin/cctv"
    return 0
  fi
  if command -v cctv >/dev/null 2>&1; then
    command -v cctv
    return 0
  fi
  return 1
}

write_cctv_config() {
  mkdir -p "$(dirname "${CCTV_CONFIG_FILE}")"
  cat > "${CCTV_CONFIG_FILE}" <<EOF
{
  "daemon_address": "${DAEMON_ADDRESS}",
  "port": ${DAEMON_PORT},
  "time_range_s": 60,
  "username": "${DAEMON_USERNAME}",
  "skip_splash": true,
  "tasks": []
}
EOF
}

is_service_installed() {
  command -v systemctl >/dev/null 2>&1 || return 1
  systemctl list-unit-files --type=service 2>/dev/null | awk '{print $1}' | grep -qx 'coolercontrold.service'
}

is_service_active() {
  command -v systemctl >/dev/null 2>&1 || return 1
  systemctl is-active --quiet coolercontrold
}

install_coolercontrol_daemon() {
  local codename

  command -v apt-get >/dev/null 2>&1 || die "apt-get not found; install CoolerControl manually for this distro."

  codename=""
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
  fi
  if [ -z "${codename}" ]; then
    codename="noble"
  fi

  wait_for_apt_locks
  log "Installing CoolerControl daemon packages via apt (ubuntu/${codename})..."
  sudo apt-get update
  sudo apt-get install -y curl ca-certificates gnupg
  curl -1sLf 'https://dl.cloudsmith.io/public/coolercontrol/coolercontrol/setup.deb.sh' \
    | sudo -E env distro=ubuntu codename="${codename}" bash
  wait_for_apt_locks
  sudo apt-get update
  sudo apt-get install -y coolercontrold coolercontrol
}

start_coolercontrol_daemon() {
  if wait_for_daemon_health; then
    log "CoolerControl daemon already reachable at $(daemon_base_url)."
    return 0
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not found; skipping coolercontrold start."
    return 0
  fi

  if ! is_service_installed; then
    warn "coolercontrold.service not installed; attempting install now."
    install_coolercontrol_daemon
  fi

  if ! is_service_installed; then
    warn "coolercontrold.service still not present after install attempt."
    return 0
  fi

  if is_service_active; then
    log "coolercontrold is already active."
    return 0
  fi

  log "Enabling and starting coolercontrold with sudo..."
  sudo systemctl enable --now coolercontrold || warn "Failed to enable/start coolercontrold."

  if ! wait_for_daemon_health; then
    warn "Daemon health endpoint still unreachable after start attempt."
  fi
}

create_session_cookie() {
  local response_headers
  response_headers="$(
    curl -sS -D - -o /dev/null \
      -X POST \
      -u "${DAEMON_USERNAME}:${DAEMON_PASSWORD}" \
      "$(daemon_base_url)/login" || true
  )"
  printf '%s\n' "${response_headers}" \
    | awk 'BEGIN{IGNORECASE=1} /^set-cookie:[[:space:]]*cc=/{gsub(/\r/,""); sub(/^set-cookie:[[:space:]]*/,""); sub(/;.*/,""); print; exit}'
}

create_mode_if_missing() {
  local cookie="$1"
  local name="$2"
  local modes_json

  modes_json="$(curl -sS "$(daemon_base_url)/modes" || true)"
  if printf '%s' "${modes_json}" | grep -qi "\"name\"[[:space:]]*:[[:space:]]*\"${name}\""; then
    log "Mode already exists: ${name}"
    return 0
  fi

  local status
  status="$(
    curl -sS -o /tmp/coolercontrol_mode_create.json -w '%{http_code}' \
      -X POST \
      -H "Cookie: ${cookie}" \
      -H "Content-Type: application/json" \
      -d "{\"name\":\"${name}\"}" \
      "$(daemon_base_url)/modes" || true
  )"

  if printf '%s' "${status}" | grep -Eq '^2[0-9][0-9]$'; then
    log "Created mode: ${name}"
    return 0
  fi

  warn "Failed creating mode '${name}' (HTTP ${status}). Response:"
  sed -n '1,120p' /tmp/coolercontrol_mode_create.json 2>/dev/null || true
  return 1
}

reset_daemon_password_to_default() {
  if [ ! -x /usr/bin/coolercontrold ]; then
    warn "coolercontrold binary not found; cannot reset password automatically."
    return 1
  fi

  log "Resetting CoolerControl admin password to default via sudo..."
  if ! sudo /usr/bin/coolercontrold --reset-password; then
    warn "Failed to reset CoolerControl admin password."
    return 1
  fi

  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl restart coolercontrold || warn "Failed to restart coolercontrold after password reset."
  fi
  if ! wait_for_daemon_health; then
    warn "Daemon did not become healthy after password reset restart."
    return 1
  fi
  return 0
}

bootstrap_modes_if_empty() {
  local cctv_bin="$1"
  local modes_line
  local modes
  local cookie

  modes_line="$(
    CCTV_DAEMON_PASSWORD="${DAEMON_PASSWORD}" timeout 15s "${cctv_bin}" --list-modes 2>/dev/null \
      | sed -n '1p' || true
  )"
  modes="${modes_line#Available modes: }"
  modes="$(printf '%s' "${modes}" | tr -d '[:space:]')"
  if [ -n "${modes}" ]; then
    log "Detected existing CoolerControl modes."
    return 0
  fi

  log "No CoolerControl modes found; attempting to create fallback modes via API."
  cookie="$(create_session_cookie)"
  if [ -z "${cookie}" ]; then
    warn "Failed to obtain CoolerControl session cookie with current credentials."
    if reset_daemon_password_to_default; then
      DAEMON_PASSWORD="coolAdmin"
      upsert_export "BMS_COOLERCONTROL_PASSWORD" "${DAEMON_PASSWORD}"
      cookie="$(create_session_cookie)"
    fi
  fi

  if [ -z "${cookie}" ]; then
    warn "Still unable to obtain CoolerControl session cookie. Cannot auto-create modes."
    return 0
  fi

  create_mode_if_missing "${cookie}" "${AUTO_MODE_NAME}" || true
  create_mode_if_missing "${cookie}" "${MANUAL_MODE_NAME}" || true

  modes_line="$(
    CCTV_DAEMON_PASSWORD="${DAEMON_PASSWORD}" timeout 15s "${cctv_bin}" --list-modes 2>/dev/null \
      | sed -n '1p' || true
  )"
  modes="${modes_line#Available modes: }"
  modes="$(printf '%s' "${modes}" | tr -d '[:space:]')"
  if [ -z "${modes}" ]; then
    warn "Mode bootstrap finished but mode list is still empty."
  else
    log "Mode bootstrap succeeded."
  fi
}

restart_bms() {
  [ -x "${PROJECT_DIR}/start_ui.sh" ] || die "start_ui.sh not found at ${PROJECT_DIR}"
  log "Restarting BioModStack UI/API..."
  (
    cd "${PROJECT_DIR}"
    ./start_ui.sh restart
  )
}

verify() {
  local cctv_bin="$1"
  if curl -fsS "$(daemon_base_url)/health" >/dev/null 2>&1; then
    log "Checking cctv modes..."
    if command -v timeout >/dev/null 2>&1; then
      CCTV_DAEMON_PASSWORD="${DAEMON_PASSWORD}" timeout 25s "${cctv_bin}" --list-modes || warn "cctv --list-modes failed."
    else
      CCTV_DAEMON_PASSWORD="${DAEMON_PASSWORD}" "${cctv_bin}" --list-modes || warn "cctv --list-modes failed."
    fi
  else
    warn "CoolerControl daemon is unreachable; skipping cctv --list-modes verification."
  fi

  log "Checking /api/gpu/fan-control..."
  if command -v jq >/dev/null 2>&1; then
    curl -sS "http://127.0.0.1:8000/api/gpu/fan-control" | jq '{backend,supported,message,available_modes}'
  else
    curl -sS "http://127.0.0.1:8000/api/gpu/fan-control"
    printf '\n'
  fi
}

main() {
  local cctv_bin

  cctv_bin="$(resolve_cctv_bin)" || die "cctv not found. Install it first (expected ${HOME}/.cargo/bin/cctv)."

  log "Using cctv binary: ${cctv_bin}"
  ensure_env_file
  write_cctv_config

  upsert_export "BMS_FAN_CONTROL_BACKEND" "coolercontrol"
  upsert_export "BMS_COOLERCONTROL_CLI" "${cctv_bin}"
  upsert_export "BMS_COOLERCONTROL_PASSWORD" "${DAEMON_PASSWORD}"
  upsert_export "BMS_COOLERCONTROL_DAEMON_ADDRESS" "${DAEMON_ADDRESS}"
  upsert_export "BMS_COOLERCONTROL_DAEMON_PORT" "${DAEMON_PORT}"
  upsert_export "BMS_COOLERCONTROL_USERNAME" "${DAEMON_USERNAME}"
  upsert_export "BMS_COOLERCONTROL_MODE_AUTO" "${AUTO_MODE_NAME}"
  upsert_export "BMS_COOLERCONTROL_MODE_MANUAL" "${MANUAL_MODE_NAME}"

  log "Wrote env vars to ${ENV_FILE}"
  log "Wrote cctv config to ${CCTV_CONFIG_FILE}"

  start_coolercontrol_daemon
  bootstrap_modes_if_empty "${cctv_bin}"
  restart_bms
  verify "${cctv_bin}"

  log "Completed."
}

main "$@"

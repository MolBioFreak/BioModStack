#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "launch_api_with_rapl_caps.sh must run as root" >&2
    exit 1
fi

: "${TARGET_USER:?TARGET_USER is required}"
: "${TARGET_UID:?TARGET_UID is required}"
: "${TARGET_GID:?TARGET_GID is required}"
: "${TARGET_HOME:?TARGET_HOME is required}"
: "${TARGET_PATH:?TARGET_PATH is required}"
: "${PROJECT_DIR:?PROJECT_DIR is required}"
: "${API_LOG:?API_LOG is required}"
: "${API_CMD:?API_CMD is required}"

SETPRIV_BIN="${SETPRIV_BIN:-$(command -v setpriv || true)}"
if [ -z "$SETPRIV_BIN" ]; then
    echo "setpriv is required to launch uvicorn with CAP_DAC_READ_SEARCH" >&2
    exit 1
fi

RAPL_PATH="${BMS_CPU_POWER_RAPL_PATH:-/sys/class/powercap/intel-rapl:0/energy_uj}"
if [ ! -e "$RAPL_PATH" ]; then
    echo "RAPL powercap path not found: $RAPL_PATH" >&2
    exit 1
fi

mkdir -p "$(dirname "$API_LOG")"
rm -f "$API_LOG"
touch "$API_LOG"
chmod 0644 "$API_LOG"

launch_cmd=""
printf -v launch_cmd '%sexport HOME=%q\n' "$launch_cmd" "$TARGET_HOME"
printf -v launch_cmd '%sexport USER=%q\n' "$launch_cmd" "$TARGET_USER"
printf -v launch_cmd '%sexport LOGNAME=%q\n' "$launch_cmd" "$TARGET_USER"
printf -v launch_cmd '%sexport PATH=%q\n' "$launch_cmd" "$TARGET_PATH"
printf -v launch_cmd '%sif [ -f %q ]; then source %q; fi\n' "$launch_cmd" "$TARGET_HOME/.biomodstack/env.sh" "$TARGET_HOME/.biomodstack/env.sh"
if [ -n "${BMS_INPUTS:-}" ]; then
    printf -v launch_cmd '%sexport BMS_INPUTS=%q\n' "$launch_cmd" "$BMS_INPUTS"
fi
if [ -n "${BMS_FAN_CONTROL_BACKEND:-}" ]; then
    printf -v launch_cmd '%sexport BMS_FAN_CONTROL_BACKEND=%q\n' "$launch_cmd" "$BMS_FAN_CONTROL_BACKEND"
fi
printf -v launch_cmd '%scd %q\n' "$launch_cmd" "$PROJECT_DIR/platform/api"
printf -v launch_cmd '%sAPI_CMD=%q\n' "$launch_cmd" "$API_CMD"
printf -v launch_cmd '%seval "$API_CMD"\n' "$launch_cmd"

nohup "$SETPRIV_BIN" \
    --reuid "$TARGET_UID" \
    --regid "$TARGET_GID" \
    --clear-groups \
    --inh-caps +dac_read_search \
    --ambient-caps +dac_read_search \
    /bin/bash -lc "$launch_cmd" > "$API_LOG" 2>&1 &

printf '%s\n' "$!"

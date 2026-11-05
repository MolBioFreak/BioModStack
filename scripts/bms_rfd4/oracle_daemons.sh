# Host-side AF3/OF3/AF2 refolding-oracle launcher daemons for modules/rfd4_proteina.nf.
# Sourced only when the resolved oracle runtime has an AF3/OF3 or AF2 family. Same contract as upstream's
# emit_af3_daemon_block/emit_af2_daemon_block (RFD4-Proteina scripts/common.sh): the daemon runs on the HOST,
# the container only receives <PREFIX>_LAUNCHER_SOCKET_TEMPLATE and talks to it over a Unix socket.
# Everything is best effort: if a daemon cannot start, the command line is left as it would be without one.

BMS_ORACLE_ARGS=()
BMS_ORACLE_PIDS=()
BMS_ORACLE_SOCKETS=()
BMS_ORACLE_DIR=''

bms_oracle_stop() {
  local sock pid
  trap - EXIT
  set +e
  for sock in "${BMS_ORACLE_SOCKETS[@]}"; do "$BMS_ORACLE_PYTHON" "$BMS_ORACLE_HELPER" shutdown "$sock" >/dev/null 2>&1; done
  for pid in "${BMS_ORACLE_PIDS[@]}"; do
    for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    bms_oracle_kill_tree "$pid" TERM
    sleep 1
    bms_oracle_kill_tree "$pid" KILL
  done
  [ -z "$BMS_ORACLE_DIR" ] || rm -rf "$BMS_ORACLE_DIR"
}

bms_oracle_kill_tree() {
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do bms_oracle_kill_tree "$child" "$2"; done
  kill -"$2" "$1" 2>/dev/null
  return 0
}

# bms_oracle_begin EXECUTABLE IMAGE CODE_ROOT GPU_ID
bms_oracle_begin() {
  local executable="$1" image="$2" code_root="$3" gpu="${4%%,*}"
  BMS_ORACLE_EXECUTABLE="$executable"
  BMS_ORACLE_HELPER="$code_root/scripts/bms_rfd4/oracle_daemons.py"
  BMS_ORACLE_GPU="$gpu"
  BMS_ORACLE_PYTHON=/usr/bin/python3
  [ -x "$BMS_ORACLE_PYTHON" ] || BMS_ORACLE_PYTHON=python3
  # The AF_UNIX path limit is 107 bytes; a task directory on a worker is longer, so sockets live in /tmp.
  BMS_ORACLE_DIR="$(mktemp -d /tmp/bms-oracle.XXXXXX 2>/dev/null)" || { BMS_ORACLE_DIR=''; return 0; }
  trap bms_oracle_stop EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  trap 'exit 129' HUP
  mkdir -p oracle_daemon
  # The daemon scripts are the image's own rfproteina/{af3,af2} sources (host-python safe, no torch).
  "$executable" exec --no-home --bind "$PWD:$PWD" --bind "$code_root/scripts:/bms/scripts:ro" "$image" \
    python /bms/scripts/bms_rfd4/oracle_daemons.py extract "$PWD/oracle_daemon" || true
  if [ "$(basename "$executable")" != apptainer ]; then
    # Rented workers have no usable root apptainer; the daemon's nested `apptainer run|exec` goes to the same
    # launcher the task itself uses. Weights stay read-only, as in the task command.
    mkdir -p oracle_daemon/bin
    cat > oracle_daemon/bin/apptainer <<'BMS_ORACLE_SHIM'
#!/bin/bash
# Take this shim's own directory off PATH so the launcher (and any other caller) sees the real apptainer.
here="$(cd "$(dirname "$0")" && pwd)"
PATH="$(printf '%s' "$PATH" | tr ':' '\n' | grep -v -x -F "$here" | paste -sd: -)"; export PATH
sub="$1"; shift
# Only run|exec go to the task launcher; everything else (e.g. `sif list`, which the launcher itself calls) is real apptainer.
case "$sub" in run|exec) ;; *) exec apptainer "$sub" "$@" ;; esac
args=()
for a in "$@"; do
  for m in "${AF3_MODEL_DIR:-}" "${AF2_MODEL_DIR:-}"; do
    if [ -n "$m" ] && [ "$a" = "$m:$m" ]; then a="$m:$m:ro"; fi
  done
  args+=("$a")
done
exec "$BMS_ORACLE_EXECUTABLE" exec "${args[@]}"
BMS_ORACLE_SHIM
    chmod +x oracle_daemon/bin/apptainer
    export BMS_ORACLE_EXECUTABLE
  fi
  # Reaps the daemons even if this shell is SIGKILLed (cancel): the watcher outlives it by one poll.
  ( while kill -0 "$$" 2>/dev/null; do sleep 2; done; bms_oracle_stop ) >/dev/null 2>&1 &
  BMS_ORACLE_WATCH=$!
}

# bms_oracle_start PREFIX(AF3|AF2) IMAGE MODEL_DIR [extra serve flags]
bms_oracle_start() {
  local prefix="$1" image="$2" model="$3" lower pid sock cache
  shift 3
  [ -n "$BMS_ORACLE_DIR" ] || return 0
  lower="$(printf '%s' "$prefix" | tr 'A-Z' 'a-z')"
  [ -f "oracle_daemon/$lower/${lower}_daemon.py" ] || return 0
  sock="$BMS_ORACLE_DIR/${lower}_daemon_rank0.sock"
  cache="$PWD/oracle_daemon/cache/$lower/jax"
  mkdir -p "$cache" || return 0
  (
    export "${prefix}_IMAGE=$image" "${prefix}_MODEL_DIR=$model" "${prefix}_JAX_CACHE_DIR=$cache"
    # Binds the daemon's container needs beyond its weights/cache/script dir: the task directory (/tmp is added
    # by the daemon itself, and is where AF3Oracle/AF2Oracle write their manifests and fold outputs).
    export APPTAINER_BIND_PATHS="$PWD:$PWD" CUDA_DEVICE_ORDER=PCI_BUS_ID
    gpu_flag=()
    [ -z "$BMS_ORACLE_GPU" ] || [ "$BMS_ORACLE_GPU" != "${BMS_ORACLE_GPU//[!0-9]/}" ] || gpu_flag=(--gpu-device "$BMS_ORACLE_GPU")
    [ ! -d "$PWD/oracle_daemon/bin" ] || export PATH="$PWD/oracle_daemon/bin:$PATH"
    exec "$BMS_ORACLE_PYTHON" "$PWD/oracle_daemon/$lower/${lower}_daemon.py" serve --skip-if-no-env \
      --socket-path "$sock" ${gpu_flag[@]+"${gpu_flag[@]}"} "$@"
  ) >"$PWD/oracle_daemon_$lower.log" 2>&1 &
  pid=$!
  BMS_ORACLE_PIDS+=("$pid")
  for _ in $(seq 1 60); do [ -S "$sock" ] && break; kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  [ -S "$sock" ] || return 0
  BMS_ORACLE_SOCKETS+=("$sock")
  [ "${#BMS_ORACLE_ARGS[@]}" -gt 0 ] || BMS_ORACLE_ARGS+=(--bind /tmp:/tmp)
  BMS_ORACLE_ARGS+=(--env "${prefix}_LAUNCHER_SOCKET_TEMPLATE=$BMS_ORACLE_DIR/${lower}_daemon_rank{rank}.sock")
}

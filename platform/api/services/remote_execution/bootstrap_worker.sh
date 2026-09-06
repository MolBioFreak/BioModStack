#!/usr/bin/env bash
# Invoked with bash -s over pinned SSH. No provider/driver/network configuration.
set -euo pipefail
fail() { printf 'BMS_SETUP_ERROR:%s\n' "$1"; exit 20; }
priv=()
if [ "$(id -u)" != 0 ]; then
    command -v sudo >/dev/null && sudo -n true || fail 'Root or noninteractive sudo is required'
    priv=(sudo -n)
fi
# Capabilities, not a Docker filename or provider template label, authorize setup.
command -v unshare >/dev/null || fail 'Namespace support cannot be checked (unshare missing)'
"${priv[@]}" unshare -m true || fail 'Mount namespaces unavailable; use a compatible VM'
"${priv[@]}" unshare -Ur true || fail 'User namespaces unavailable; use a compatible VM'
"${priv[@]}" unshare -pf true || fail 'PID namespaces unavailable; use a compatible VM'
nvidia-smi >/dev/null || fail 'NVIDIA driver unavailable; automatic setup does not change drivers'
[ "$(uname -m)" = x86_64 ] || fail 'Automatic setup supports x86_64 only'
. /etc/os-release
case "$ID" in ubuntu|debian) ;; *) fail 'Automatic setup supports Ubuntu or Debian only';; esac
command -v apt-get >/dev/null || fail 'apt-get is required'
packages=()
for pair in 'python3:python3' 'rsync:rsync' 'tar:tar' 'sha256sum:coreutils' 'curl:curl'; do
    command -v "${pair%%:*}" >/dev/null || packages+=("${pair#*:}")
done
if ! java -version 2>&1 | grep -Eq 'version "(17|18|19|2[0-5])\.'; then
    command -v java >/dev/null && java -version >/dev/null 2>&1 && fail 'Existing Java is unsupported; Java 17-25 required'
    packages+=(openjdk-17-jre-headless)
fi
need_apptainer=0
if ! apptainer --version >/dev/null 2>&1; then
    need_apptainer=1
    packages+=(ca-certificates)
fi
[ "${1:-}" != check ] || exit 0
# Only create the explicitly requested worker root; never take over an existing tree.
if [ -n "${2:-}" ]; then
    if [ ! -e "$2" ]; then
        "${priv[@]}" mkdir -p -- "$2" || fail 'Worker root creation failed'
        "${priv[@]}" chown "$(id -u):$(id -g)" -- "$2" || fail 'Worker root ownership setup failed'
    fi
    [ -d "$2" ] && [ -w "$2" ] && [ ! -L "$2" ] || fail 'Worker root is not a writable directory'
fi
if [ "${#packages[@]}" -gt 0 ] || [ "$need_apptainer" = 1 ]; then
    "${priv[@]}" env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=2 -o Acquire::http::Timeout=60 update || fail 'Package installation failed; check apt repository access and retry'
    if [ "${#packages[@]}" -gt 0 ]; then
        "${priv[@]}" env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends "${packages[@]}" || fail 'Package installation failed; check apt repository access and retry'
    fi
fi
if [ "$need_apptainer" = 1 ]; then
    temp=$(mktemp -d)
    trap 'rm -rf "$temp"' EXIT
    curl --fail --location --proto '=https' --tlsv1.2 --connect-timeout 30 --max-time 1800 --retry 2 \
        https://github.com/apptainer/apptainer/releases/download/v1.3.0/apptainer_1.3.0_amd64.deb -o "$temp/apptainer.deb" || fail 'Apptainer download failed; check GitHub access and retry'
    # Official v1.3.0 sha256sums release asset (legacy GitHub asset digest is null).
    printf '%s  %s\n' ad1dc126e45edaacb4ac08fc7bb09d6d1cdc7f163710edf2b0309af8123258f3 "$temp/apptainer.deb" | sha256sum -c - || fail 'Apptainer checksum mismatch'
    "${priv[@]}" env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends "$temp/apptainer.deb" || fail 'Apptainer package installation failed'
fi
java -version >/dev/null 2>&1 || fail 'Java installation failed'
apptainer --version >/dev/null || fail 'Apptainer installation failed'

#!/usr/bin/env bash
# Run manually as root from a reviewed checkout. The installer renders one
# root-owned recovery compose artifact; the installed helper never reads this
# checkout, its Compose file, or any project .env file.
set -euo pipefail

if (( EUID != 0 )); then
    printf '%s\n' 'Run with sudo: sudo ./config/mk1d-reconnect/install-root-helper.sh' >&2
    exit 2
fi

repo_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
native_api_user="${BMS_NATIVE_API_USER:-${SUDO_USER:-dalab}}"
minknow_service="${MINKNOW_SYSTEMD_SERVICE:-minknow.service}"
compose_project="${BMS_DOCKER_COMPOSE_PROJECT:-biomodstack-core-runtime}"
compose_file="$repo_root/compose.core-runtime.yml"
recovery_dir='/etc/biomodstack'
recovery_compose='/etc/biomodstack/mk1d-reconnect-compose.json'
recovery_env='/etc/biomodstack/mk1d-reconnect.env'
legacy_env="$repo_root/.env.core-runtime.local"

if [[ ! "$native_api_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || [[ ! "$minknow_service" =~ ^[A-Za-z0-9_.@-]+\.service$ ]] || [[ ! "$compose_project" =~ ^[A-Za-z0-9_.-]+$ ]] || [[ ! -f "$compose_file" ]]; then
    printf '%s\n' 'Invalid reviewed Mk1D recovery installation configuration.' >&2
    exit 2
fi
if ! native_api_uid="$(id -u "$native_api_user" 2>/dev/null)" || ! native_api_gid="$(id -g "$native_api_user" 2>/dev/null)" || ! native_api_group="$(id -gn "$native_api_user" 2>/dev/null)" || [[ "$native_api_uid" == 0 ]] || [[ ! "$native_api_gid" =~ ^[0-9]+$ ]] || [[ ! "$native_api_group" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
    printf '%s\n' 'Mk1D recovery requires a validated non-root native BMS API user and primary group.' >&2
    exit 2
fi
native_api_home="$(getent passwd "$native_api_user" | awk -F: 'NF >= 6 {print $6}')"
if [[ ! "$native_api_home" =~ ^/ ]] || [[ ! -d "$native_api_home" ]]; then
    printf '%s\n' 'Unable to resolve the native BMS API user home directory.' >&2
    exit 2
fi
profile_env="${BMS_CORE_RUNTIME_ENV_FILE:-${XDG_CONFIG_HOME:-$native_api_home/.config}/biomodstack/core-runtime.env}"
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    printf '%s\n' 'Docker Compose plugin is required to render the recovery artifact.' >&2
    exit 2
fi

# Use the primary group of the real native BMS API user. Creating a detached
# recovery group leaves a root socket unusable by that native process.
recovery_group="$native_api_group"
recovery_gid="$native_api_gid"

# Rendering is an installation-time action only. Explicit --env-file prevents
# Compose from implicitly loading checkout .env; the supported runtime profile
# is used when available so the root artifact matches the managed host-agent.
compose_env='/dev/null'
if [[ -f "$profile_env" ]]; then
    compose_env="$profile_env"
elif [[ -f "$legacy_env" ]]; then
    compose_env="$legacy_env"
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
rendered_json="$tmp_dir/rendered-compose.json"
docker compose --env-file "$compose_env" -p "$compose_project" -f "$compose_file" config --format json >"$rendered_json"

install -d -o root -g root -m 0755 "$recovery_dir"
python3 - "$repo_root" "$minknow_service" "$compose_project" "$recovery_group" "$recovery_gid" "$tmp_dir" "$rendered_json" "$recovery_compose" "$recovery_env" <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

(
    repo_root,
    minknow_service,
    compose_project,
    recovery_group,
    recovery_gid,
    tmp_dir,
    rendered_json,
    recovery_compose,
    recovery_env,
) = sys.argv[1:]
source = Path(repo_root) / "config" / "mk1d-reconnect"
rendered = json.loads(Path(rendered_json).read_text(encoding="utf-8"))
services = rendered.get("services")
service = services.get("bms-host-agent") if isinstance(services, dict) else None
if not isinstance(service, dict):
    raise SystemExit("rendered Compose has no bms-host-agent service")
if service.get("container_name") != "biomodstack-host-agent" or not isinstance(service.get("image"), str) or not service["image"]:
    raise SystemExit("recovery artifact requires literal biomodstack-host-agent image service")
recovery_service = dict(service)
for forbidden in ("build", "depends_on", "develop", "profiles", "env_file"):
    recovery_service.pop(forbidden, None)
recovery = {"services": {"bms-host-agent": recovery_service}}
serialized = json.dumps(recovery, sort_keys=True, separators=(",", ":")) + "\n"
if "${" in serialized or "$" in serialized:
    raise SystemExit("recovery artifact must contain no unresolved Compose interpolation")


def atomic_root_write(path: Path, data: bytes, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, 0, 0)
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(fd)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


atomic_root_write(Path(recovery_compose), serialized.encode("utf-8"), 0o600)
atomic_root_write(Path(recovery_env), f"BMS_MK1D_RECOVERY_GID={recovery_gid}\n".encode("ascii"), 0o644)
replacements = {
    "__BMS_REPO_ROOT__": repo_root,
    "__MINKNOW_SYSTEMD_SERVICE__": minknow_service,
    "__BMS_RECOVERY_COMPOSE_FILE__": recovery_compose,
    "__BMS_COMPOSE_PROJECT__": compose_project,
    "__BMS_RECOVERY_GROUP__": recovery_group,
}
for source_name, target_name in (
    ("bms-reconnect-mk1d.template", "bms-reconnect-mk1d"),
    ("bms-reconnect-mk1d.socket.template", "bms-reconnect-mk1d.socket"),
    ("bms-reconnect-mk1d@.service.template", "bms-reconnect-mk1d@.service"),
):
    content = (source / source_name).read_text(encoding="utf-8")
    for old, new in replacements.items():
        content = content.replace(old, new)
    if "__BMS_" in content or "__MINKNOW_" in content:
        raise SystemExit("unresolved recovery helper template placeholder")
    (Path(tmp_dir) / target_name).write_text(content, encoding="utf-8")
PY

install -D -o root -g root -m 0750 "$tmp_dir/bms-reconnect-mk1d" /usr/local/sbin/bms-reconnect-mk1d
install -D -o root -g root -m 0644 "$tmp_dir/bms-reconnect-mk1d.socket" /etc/systemd/system/bms-reconnect-mk1d.socket
install -D -o root -g root -m 0644 "$tmp_dir/bms-reconnect-mk1d@.service" /etc/systemd/system/bms-reconnect-mk1d@.service
install -D -o root -g root -m 0644 "$repo_root/config/mk1d-reconnect/bms-mk1d-reconnect.conf" /etc/tmpfiles.d/bms-mk1d-reconnect.conf

systemd-tmpfiles --create /etc/tmpfiles.d/bms-mk1d-reconnect.conf
systemctl daemon-reload
systemctl enable bms-reconnect-mk1d.socket
# Restart makes an already-bound socket adopt a corrected SocketGroup on rerun.
systemctl restart bms-reconnect-mk1d.socket
printf 'Installed Mk1D reconnect helper for native API user %s (primary GID %s) and root-owned recovery artifact. Restart bms-api with scripts/run_biomodstack_core_runtime.sh so it imports the validated recovery GID.\n' "$native_api_user" "$recovery_gid"

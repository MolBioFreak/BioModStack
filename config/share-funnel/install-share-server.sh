#!/usr/bin/env bash
# Install the public MolBio share server as a hardened, socket-activated unit.
#
# Run manually as root from a reviewed checkout (after the guest frontend is
# built). The service cannot read /home (ProtectHome=yes), so this copies a
# minimal, root-owned runtime to BMS_SHARE_INSTALL_ROOT:
#   python/          relocatable CPython used by the API's locked venv
#   site-packages/   only the locked pure-compute dependencies
#   app/             share_server + pure restriction modules + catalog assets
#   static/          the guest-only frontend build
# Nothing here starts Funnel; enable bms-share-funnel.{timer,path} separately.
set -euo pipefail

if (( EUID != 0 )); then
    printf '%s\n' 'Run with sudo: sudo ./config/share-funnel/install-share-server.sh' >&2
    exit 2
fi

repo_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
api_root="$repo_root/platform/api"
install_root="${BMS_SHARE_INSTALL_ROOT:-/opt/bms-share}"
publish_dir="${BMS_SHARE_PUBLISH_DIR:-/srv/bms-share/publish}"
share_port="${BMS_SHARE_PORT:-18090}"
api_user="${BMS_NATIVE_API_USER:-${SUDO_USER:-}}"
tailscale_operator="${BMS_TAILSCALE_OPERATOR:-$api_user}"
static_build="${BMS_SHARE_STATIC_BUILD:-$repo_root/platform/frontend/dist-share}"
venv_python="$api_root/.venv/bin/python"

fail() { printf '%s\n' "$1" >&2; exit 2; }
[[ "$install_root" =~ ^/(opt|srv|usr/local)/[A-Za-z0-9._/-]+$ ]] || fail 'BMS_SHARE_INSTALL_ROOT must be under /opt, /srv or /usr/local.'
[[ "$publish_dir" =~ ^/(srv|var/lib|opt)/[A-Za-z0-9._/-]+$ ]] || fail 'BMS_SHARE_PUBLISH_DIR must be under /srv, /var/lib or /opt (not /home).'
[[ "$share_port" =~ ^[0-9]+$ ]] && (( share_port >= 1024 && share_port <= 65535 )) || fail 'BMS_SHARE_PORT is invalid.'
(( share_port != 8443 && share_port != 443 )) || fail 'BMS_SHARE_PORT must be a private loopback port, not 443/8443.'
[[ "$api_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] && id -u "$api_user" >/dev/null 2>&1 || fail 'Set BMS_NATIVE_API_USER to the native BMS API user.'
[[ "$tailscale_operator" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || fail 'BMS_TAILSCALE_OPERATOR is invalid.'
[[ -x "$venv_python" ]] || fail "Locked API venv missing: $venv_python (run uv sync --frozen in platform/api)."
[[ -f "$static_build/share.html" ]] || fail "Guest frontend build missing: $static_build/share.html (build the guest frontend into platform/frontend/dist-share)."

if ! getent group bms-share >/dev/null; then groupadd --system bms-share; fi
if ! id -u bms-share >/dev/null 2>&1; then
    useradd --system --gid bms-share --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin bms-share
fi

staging="$(mktemp -d "${install_root%/*}/.bms-share-install.XXXXXX")"
trap 'rm -rf "$staging"' EXIT

# 1. Relocatable interpreter that backs the locked venv.
base_prefix="$("$venv_python" -c 'import sys; print(sys.base_prefix)')"
[[ -x "$base_prefix/bin/python3" ]] || fail 'Cannot resolve the base interpreter of the API venv.'
cp -a "$base_prefix/." "$staging/python/"

# 2. Only the locked dependencies the share server imports (plus their deps).
"$venv_python" - "$staging/site-packages" <<'PY'
import importlib.metadata as md
import shutil
import sys
from pathlib import Path

target = Path(sys.argv[1])
target.mkdir(parents=True)
roots = ["uvicorn", "pydantic", "rfc8785", "jsonschema"]
skip = {"colorama"}
seen: set[str] = set()
pending = list(roots)
while pending:
    name = pending.pop()
    key = name.lower().replace("_", "-")
    if key in seen or key in skip:
        continue
    seen.add(key)
    dist = md.distribution(name)
    for requirement in dist.requires or []:
        if "extra ==" in requirement:
            continue
        if ";" in requirement and "sys_platform == \"win32\"" in requirement:
            continue
        pending.append(requirement.split(";")[0].split("[")[0].split("<")[0].split(">")[0].split("=")[0].split("!")[0].split("~")[0].strip())
    for file in dist.files or []:
        source = Path(dist.locate_file(file))
        if not source.is_file() or "__pycache__" in source.parts:
            continue
        destination = target / file
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
print("copied:", ", ".join(sorted(seen)))
PY

# 3. Application code: share server + pure restriction computation + assets.
app="$staging/app"
install -d "$app/platform/api/services" "$app/platform/api/config/molbio" "$app/schemas"
cp -a "$api_root/share_server" "$app/platform/api/share_server"
for module in restriction_catalog restriction_analysis restriction_digest; do
    install -m 0644 "$api_root/services/$module.py" "$app/platform/api/services/$module.py"
done
install -m 0644 "$api_root/restriction_catalog_integrity.py" "$app/platform/api/restriction_catalog_integrity.py"
cp -a "$api_root/config/molbio/restriction" "$app/platform/api/config/molbio/restriction"
cp -a "$repo_root/schemas/molbio" "$app/schemas/molbio"
find "$app" -name '__pycache__' -prune -exec rm -rf {} +

# 4. Guest-only static frontend.
cp -a "$static_build/." "$staging/static/"
install -m 0644 "$repo_root/config/share-funnel/README.md" "$staging/README.md"

chown -R root:root "$staging"
chmod -R u=rwX,go=rX "$staging"
if [[ -d "$install_root" ]]; then rm -rf "$install_root.previous"; mv "$install_root" "$install_root.previous"; fi
mv "$staging" "$install_root"
trap - EXIT

# 5. Publish directory: written by the BMS API user, group-readable by bms-share.
install -d -o root -g root -m 0755 "$(dirname "$publish_dir")"
install -d -o "$api_user" -g bms-share -m 2750 "$publish_dir"

# 6. Units.
render() {
    sed -e "s#__BMS_INSTALL_ROOT__#$install_root#g" \
        -e "s#__BMS_SHARE_PUBLISH_DIR__#$publish_dir#g" \
        -e "s#__BMS_SHARE_PORT__#$share_port#g" \
        -e "s#__BMS_TAILSCALE_OPERATOR__#$tailscale_operator#g" "$1"
}
for unit in bms-share.socket bms-share.service bms-share-funnel.service bms-share-funnel.timer bms-share-funnel.path; do
    rendered="$(render "$repo_root/config/share-funnel/$unit.template")"
    if grep -q '__BMS_' <<<"$rendered"; then fail "unresolved placeholder in $unit"; fi
    printf '%s\n' "$rendered" | install -D -o root -g root -m 0644 /dev/stdin "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now bms-share.socket

cat <<EOF
Installed share server to $install_root (socket 127.0.0.1:$share_port, publish dir $publish_dir).
Next, as the operator:
  1. Configure the BMS API environment (supported env file) with
       BMS_SHARE_PUBLISH_DIR=$publish_dir
       BMS_SHARE_ACCESS_LOG_DIR=/var/log/bms-share
       BMS_SHARE_PUBLIC_HOST=<this node's ts.net name>
       BMS_SHARE_AGENT_TOKEN_FILE=<0600 file outside the repo>   (optional)
     and restart bms-api through its supported launcher.
     The API user needs read access to /var/log/bms-share/access.jsonl, e.g.
       setfacl -m u:$api_user:rx /var/log/bms-share   (and default ACL r for files).
  2. sudo $repo_root/config/share-funnel/verify-share-sandbox.sh
  3. Only after review: sudo systemctl enable --now bms-share-funnel.timer bms-share-funnel.path
EOF

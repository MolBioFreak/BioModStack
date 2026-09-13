#!/usr/bin/env bash
# Invoked with bash -s over pinned SSH. No provider/driver/network configuration.
set -euo pipefail
fail() { printf 'BMS_SETUP_ERROR:%s\n' "$1"; exit 20; }
priv=()
if [ "$(id -u)" != 0 ]; then
    command -v sudo >/dev/null && sudo -n true || fail 'Root or noninteractive sudo is required'
    priv=(sudo -n)
fi
# Namespace availability is not a setup prerequisite. The managed actor
# qualifies execution with the real selected backend before activation.
nvidia-smi >/dev/null || fail 'NVIDIA driver unavailable; automatic setup does not change drivers'
[ "$(uname -m)" = x86_64 ] || fail 'Automatic setup supports x86_64 only'
. /etc/os-release
case "$ID" in ubuntu|debian) ;; *) fail 'Automatic setup supports Ubuntu or Debian only';; esac
command -v apt-get >/dev/null || fail 'apt-get is required'
# The managed/cache helpers and provisioning envelope run under system python3,
# before the support runtime exists. 3.11 is the qualified stdlib helper floor;
# command presence (or the boot path's 3.9 dict union alone) is not qualification.
# Reject before root creation/packages: installing a distro's python3 package
# does not guarantee this contract, and must not silently replace an interpreter.
command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || fail 'System python3 3.11 or newer is required; provision a compatible interpreter before worker setup'
packages=()
for pair in 'rsync:rsync' 'tar:tar' 'sha256sum:coreutils' 'curl:curl' 'unsquashfs:squashfs-tools'; do
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
case "${2:-}" in /*) ;; *) fail 'Explicit absolute worker root is required';; esac
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
# Only the managed actor requests the fallback toolset after real Apptainer
# execution fails. Normal bootstrap leaves working Apptainer workers alone.
[ "${1:-}" = udocker ] || exit 0
# Exact upstream toolset; real backend qualification is managed below.
python3 - "$2" <<'PY_INSTALL'
import hashlib, json, os, pathlib, shutil, tarfile, tempfile, urllib.request, zipfile
root = pathlib.Path(__import__('sys').argv[1])
final = root / 'tools/udocker-1.3.17'
final.parent.mkdir(parents=True, exist_ok=True)
if final.parent.is_symlink():
    raise ValueError('udocker tool root must not be a symlink')
with tempfile.TemporaryDirectory(prefix='.udocker-', dir=final.parent) as tmp:
    tmp = pathlib.Path(tmp)
    assets = [
        ('wheel', 'https://files.pythonhosted.org/packages/55/d6/caafad263b0e2375c2a8c586ac8b91fb7d3e363e6d1ab1e35a365d684254/udocker-1.3.17-py2.py3-none-any.whl', 'fd6589de0f3af7c1cd6a29554f2c00f2ef1fbd91da184ca30f8cea1eb42bcd2b', 119558),
        ('engines', 'https://download.a.incd.pt/udocker/udocker-englib-1.2.11.tar.gz', '2a4804ba82e087ca3e99305fce9887227925d2b56aaefcad6474c4cb86b7a157', 46237418)]
    stage = tmp / 'install'
    (stage / 'bin').mkdir(parents=True)
    for name, url, digest, size in assets:
        path = tmp / name
        with urllib.request.urlopen(url, timeout=180) as response, path.open('wb') as out:
            shutil.copyfileobj(response, out)
        with path.open('rb') as stream:
            if path.stat().st_size != size or hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('upstream checksum mismatch')
    with zipfile.ZipFile(tmp / 'wheel') as wheel:
        for name in wheel.namelist():
            if name.startswith('udocker/') and not name.endswith('/'):
                if '..' in pathlib.PurePosixPath(name).parts:
                    raise ValueError('unsafe wheel member')
                target = stage / 'lib' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(wheel.read(name))
    with tarfile.open(tmp / 'engines') as archive:
        for name in ('bin/proot-x86_64', 'bin/proot-x86_64-4_8_0', 'lib/VERSION'):
            member = archive.getmember('udocker_dir/' + name)
            if not member.isfile():
                raise ValueError('unsafe engine member')
            target = stage / 'engines' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
            target.chmod(0o755 if name.startswith('bin/') else 0o644)
    launcher = stage / 'bin/udocker'
    launcher.write_text("#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))\nfrom udocker.maincmd import main\nsys.exit(main())\n")
    launcher.chmod(0o755)
    records = {str(p.relative_to(stage)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in stage.rglob('*') if p.is_file()}
    (stage / 'manifest.json').write_text(json.dumps(dict(assets=assets, files=records), sort_keys=True))
    if final.exists():
        if final.is_symlink() or (final / 'manifest.json').read_bytes() != (stage / 'manifest.json').read_bytes():
            raise ValueError('udocker installation identity mismatch')
        for name, digest in records.items():
            path = final / name
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('udocker installed byte mismatch')
    else:
        os.rename(stage, final)
PY_INSTALL

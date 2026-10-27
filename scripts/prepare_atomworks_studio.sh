#!/bin/sh
# Build a separate editor-only environment; never use the BMS API/model venv.
set -eu
if [ "$#" -ne 2 ]; then
  printf 'usage: %s NEW_VENV RELEASE_WHEEL\n' "$0" >&2
  exit 2
fi
venv=$1
wheel=$2
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -e "$venv" ]; then
  printf 'Refusing to replace existing environment: %s\n' "$venv" >&2
  exit 1
fi
python3.12 "$root/scripts/atomworks_studio_package.py" verify "$wheel"
uv venv --python python3.12 "$venv"
# Installed assets are patched below: never hardlink them to uv's shared cache.
UV_LINK_MODE=copy uv pip install --python "$venv/bin/python" --index-url https://download.pytorch.org/whl/cpu 'torch==2.7.1+cpu'
UV_LINK_MODE=copy uv pip install --python "$venv/bin/python" --constraint "$root/docker/atomworks-studio-requirements.txt" "$wheel"
uv pip check --python "$venv/bin/python"
index=$("$venv/bin/python" -c 'import annotation_studio; from pathlib import Path; print(Path(annotation_studio.__file__).parent / "web" / "index.html")')
python3.12 "$root/scripts/atomworks_studio_package.py" disable-worker "$index"
mkdir -p "$venv/share/atomworks-studio/notices"
cp "$root/docker/atomworks-studio-notices/Patinae-LICENSE" "$venv/share/atomworks-studio/notices/Patinae-LICENSE"
printf 'Prepared %s. Do not expose Studio directly; authenticated BMS ingress is not wired.\n' "$venv"

#!/bin/sh
# Build a separate editor-only environment; never use the BMS API/model venv.
set -eu
if [ "$#" -ne 3 ]; then
  printf 'usage: %s NEW_VENV RELEASE_WHEEL DISPOSABLE_SOURCE\n' "$0" >&2
  exit 2
fi
venv=$1
wheel=$2
source=$3
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -e "$venv" ]; then
  printf 'Refusing to replace existing environment: %s\n' "$venv" >&2
  exit 1
fi
python3.12 "$root/scripts/atomworks_studio_package.py" verify "$wheel"
# Source verification, actual locked build, exact rebuilt asset hashes, and RECORD.
python3.12 "$root/scripts/atomworks_studio_package.py" build-patched "$source" "$wheel" "$venv-package"
patched="$venv-package/atomworks_studio-0.0.5-1bms03-py3-none-any.whl"
uv venv --python python3.12 "$venv"
# Hash-locked transitive runtime, with CPU Torch only; never resolve Studio extras.
UV_LINK_MODE=copy uv pip sync --python "$venv/bin/python" --require-hashes --only-binary :all: --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match "$root/docker/atomworks-studio.lock"
UV_LINK_MODE=copy uv pip install --python "$venv/bin/python" --no-deps "$patched"
uv pip check --python "$venv/bin/python"
# A metadata-only pip check cannot detect an interrupted binary install.
"$venv/bin/python" -c 'import torch; from annotation_studio.main import app; assert torch.__version__ == "2.7.1+cpu"'
web=$("$venv/bin/python" -c 'import annotation_studio; from pathlib import Path; print(Path(annotation_studio.__file__).parent / "web")')
"$venv/bin/python" "$root/scripts/atomworks_studio_package.py" prepare-host "$web" "$venv/share/atomworks-studio" --patched-wheel "$patched"
printf 'Prepared %s. Start only behind BMS private Studio router; see docker/atomworks-studio-README.md.\n' "$venv"

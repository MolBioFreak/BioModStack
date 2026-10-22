#!/usr/bin/env bash
# Run the pinned environment directly; avoid micromamba process-cache state.
set -euo pipefail
annotation_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
annotation_environment="$annotation_root/micromamba/envs/plannotate"
export PATH="$annotation_environment/bin:$PATH"
exec "$annotation_environment/bin/plannotate" "$@"

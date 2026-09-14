#!/bin/bash
# Scope Nextflow's private CLI adapter to this immutable managed release.
set -euo pipefail
release=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
if [[ ${BMS_CONTAINER_BACKEND:-apptainer} == udocker ]]; then
    if [[ ${BMS_CONTAINER_EXECUTABLE:-} != "$release/bin/bms-container" ]]; then
        printf '%s\n' 'Nextflow container launcher does not match its managed release' >&2
        exit 125
    fi
    # SingularityBuilder prefixes forwarded host variables. Tell the bridge
    # which prefixes were explicit container overrides before that translation,
    # so inherited PATH/library settings cannot overwrite the SIF environment.
    export BMS_NEXTFLOW_EXPLICIT_ENV
    BMS_NEXTFLOW_EXPLICIT_ENV=$(python3 -c 'import json,os; print(json.dumps([k for k in os.environ if k.startswith("SINGULARITYENV_")]))')
    export PATH="$release/nextflow/container-bin:$PATH"
    # PRoot uses the existing process owner, not a new kernel PID namespace.
    export NXF_SINGULARITY_NEW_PID_NAMESPACE=false
fi
exec "$release/nextflow/nextflow" "$@"

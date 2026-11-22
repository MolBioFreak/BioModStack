#!/usr/bin/env bash
# Offline integrated launch -> inert model leaf -> verified SQLite publication.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${BMS_TEST_NEXTFLOW_JAR:?cached Nextflow one.jar required}"
: "${BMS_FOLD_CP_FIXTURE_PYTHON:?RDKit/PyYAML interpreter required}"
export PYTHONPATH="$PWD/platform/api:$PWD:$PWD/scripts${PYTHONPATH:+:$PYTHONPATH}" PYTHONDONTWRITEBYTECODE=1 NXF_OFFLINE=true
export _BIOXP_PYTEST_NETNS=1 _BIOXP_PYTEST_PARENT_NETNS=$(readlink /proc/self/ns/net)
mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}"
evidence=$(mktemp -d "${XDG_CACHE_HOME:-$HOME/.cache}/sealed-row-evidence-XXXXXXXX")
printf 'Evidence: %s\n' "$evidence"
run() {
    local tag=$1
    shift
    unshare -Urn "${PYTHON:-python3}" -m pytest -q -o addopts='' -p no:cacheprovider --basetemp="$evidence/$tag" --junitxml="$evidence/$tag.xml" "$@" | tee "$evidence/$tag.log"
}
# The API conftest constrains subprocesses. Collect the root workflow harness separately.
run workflows tests/test_standalone_prediction_rows.py
run api platform/api/tests/test_boltz_launch_authority.py platform/api/tests/test_fold_cp_batch_launch.py platform/api/tests/test_fold_cp_result_identity.py

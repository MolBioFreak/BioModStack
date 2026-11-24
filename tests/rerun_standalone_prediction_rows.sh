#!/usr/bin/env bash
# Offline transport only. Requires a cached Nextflow 25.10.1 jar, Java 17+, API test venv.
set -euo pipefail
cd "$(dirname "$0")/.."
root=$PWD
export PATH="${JAVA_HOME:+$JAVA_HOME/bin:}$PATH" PYTHONPATH="$root:$root/scripts:$root/platform/api${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1 NXF_OFFLINE=true BMS_TEST_NEXTFLOW_JAR="${BMS_TEST_NEXTFLOW_JAR:-$HOME/.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar}"
test -f "$BMS_TEST_NEXTFLOW_JAR"
mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}"
evidence=$(mktemp -d "${XDG_CACHE_HOME:-$HOME/.cache}/prediction-row-evidence-XXXXXXXX")
printf 'Evidence: %s\n' "$evidence"
"${PYTHON:-python3}" -m pytest -q --basetemp="$evidence/tasks" --junitxml="$evidence/tests.xml" \
  tests/test_standalone_prediction_rows.py tests/test_structure_prediction_consumed_complex.py \
  tests/test_structure_prediction_file_entrypoint.py | tee "$evidence/tests.log"

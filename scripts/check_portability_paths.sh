#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

TARGETS=(
  modules
  workflows
  platform
  scripts
  nextflow.config
  main.nf
  start_ui.sh
)

RG_COMMON=(
  --line-number
  --color=never
  --glob '!work/**'
  --glob '!node_modules/**'
  --glob '!.nextflow/**'
  --glob '!backups/**'
  --glob '!scripts/check_portability_paths.sh'
)

check_fail_pattern() {
  local description="$1"
  local pattern="$2"
  local matches
  matches="$(rg "${RG_COMMON[@]}" "${pattern}" "${TARGETS[@]}" || true)"
  if [[ -n "${matches}" ]]; then
    echo "FAIL: ${description}"
    echo "${matches}"
    echo
    return 1
  fi
  return 0
}

check_warn_pattern() {
  local description="$1"
  local pattern="$2"
  local matches
  matches="$(rg "${RG_COMMON[@]}" "${pattern}" "${TARGETS[@]}" || true)"
  if [[ -n "${matches}" ]]; then
    echo "WARN: ${description}"
    echo "${matches}"
    echo
  fi
}

failed=0

check_fail_pattern "Hardcoded module container literal (use params.container_dir)." "container ['\\\"]apptainer/[^'\\\"]+\\.sif['\\\"]" || failed=1
check_fail_pattern "Hardcoded /mnt/BioModStack path." "/mnt/BioModStack" || failed=1
check_fail_pattern "Hardcoded /home/dalab path." "/home/dalab" || failed=1

# Some HPC profiles intentionally reference cluster-local paths.
check_warn_pattern "Cluster-local /vast/projects path found (review for portability)." "/vast/projects"

if [[ "${failed}" -ne 0 ]]; then
  echo "Portability path check failed."
  exit 1
fi

echo "Portability path check passed."

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

# Site-specific references are checked across every tracked file. The patterns
# are generic: any real-looking home, mount, tailnet name or tailnet address
# fails unless it is one of the neutral placeholders used by docs and tests.
check_tracked_pattern() {
  local description="$1"
  local pattern="$2"
  shift 2
  local matches
  matches="$(git grep -I -n -P "${pattern}" -- . ':!scripts/check_portability_paths.sh' "$@" || true)"
  if [[ -n "${matches}" ]]; then
    echo "FAIL: ${description}"
    echo "${matches}"
    echo
    return 1
  fi
  return 0
}

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  check_tracked_pattern "Home directory of a real account (use \$HOME or /home/example)." \
    '/home/(?!(example|ubuntu|user|runner|private|framework|fixture|secret|operator|biomodstack|reference|john|attacker)\b)[a-z_][a-z0-9_-]*/' || failed=1
  check_tracked_pattern "Site mount point (use the install profile data root or /data/bms)." \
    '/mnt/(?!(seed|private)\b)[A-Za-z0-9_-]+' || failed=1
  check_tracked_pattern "Real tailnet name (use node.example.ts.net)." \
    '\b(?!node\.example\.|lab\.tail1234\.)[A-Za-z0-9-]+\.[A-Za-z0-9-]+\.ts\.net\b' || failed=1
  check_tracked_pattern "Real tailnet address (use 100.64.0.x)." \
    '(?<![\d.])100\.(?!64\.0\.|100\.100\.100)(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}(?![\d.])' \
    ':!*.lock' ':!*lock.json' ':!*.def' ':!*requirements*.txt' ':!*identity.json' || failed=1
  check_tracked_pattern "Fixed session uid (derive from XDG_RUNTIME_DIR or the current uid)." \
    '/run/user/\d+' ':!*/tests/*' ':!tests/*' ':!scripts/acceptance/*' || failed=1
  check_tracked_pattern "Pytest temp path naming a real account." 'pytest-of-(?!user\b)[a-z]' || failed=1
  if [[ -n "${BMS_PORTABILITY_SITE_NAMES:-}" ]]; then
    check_tracked_pattern "Operator-listed site name." "(?i)${BMS_PORTABILITY_SITE_NAMES}" || failed=1
  fi
fi

# Some HPC profiles intentionally reference cluster-local paths.
check_warn_pattern "Cluster-local /vast/projects path found (review for portability)." "/vast/projects"

if [[ "${failed}" -ne 0 ]]; then
  echo "Portability path check failed."
  exit 1
fi

echo "Portability path check passed."

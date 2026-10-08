#!/bin/bash
# =============================================================================
# Cleanup Nextflow Work Directory
# =============================================================================
# Removes old task cache files to prevent disk/inotify exhaustion.
# Safe to run - Nextflow will regenerate cache as needed (but -resume won't work
# for deleted tasks).
#
# Usage:
#   ./cleanup_work_dir.sh        # Delete files older than 30 days (default)
#   ./cleanup_work_dir.sh 7      # Delete files older than 7 days
#   ./cleanup_work_dir.sh 0      # Delete ALL files (full purge)
# =============================================================================

set -e

DAYS=${1:-30}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Nextflow Work Directory Cleanup ==="
echo "Retention: $DAYS days"
echo ""

python3 "$SCRIPT_DIR/cleanup_workdirs.py" --days "$DAYS"

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
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
WORK_DIR="$PROJECT_ROOT/work"

if [ ! -d "$WORK_DIR" ]; then
    echo "Work directory does not exist: $WORK_DIR"
    exit 0
fi

echo "=== Nextflow Work Directory Cleanup ==="
echo "Directory: $WORK_DIR"
echo "Retention: $DAYS days"
echo ""

# Count before
BEFORE_COUNT=$(find "$WORK_DIR" -type f 2>/dev/null | wc -l)
BEFORE_SIZE=$(du -sh "$WORK_DIR" 2>/dev/null | cut -f1)
echo "Before: $BEFORE_COUNT files, $BEFORE_SIZE"

if [ "$DAYS" -eq 0 ]; then
    echo "Purging ALL files..."
    rm -rf "$WORK_DIR"/*
else
    echo "Deleting files older than $DAYS days..."
    find "$WORK_DIR" -type f -mtime +$DAYS -delete 2>/dev/null || true
    echo "Removing empty directories..."
    find "$WORK_DIR" -type d -empty -delete 2>/dev/null || true
fi

# Count after
AFTER_COUNT=$(find "$WORK_DIR" -type f 2>/dev/null | wc -l)
AFTER_SIZE=$(du -sh "$WORK_DIR" 2>/dev/null | cut -f1)
echo ""
echo "After: $AFTER_COUNT files, $AFTER_SIZE"
echo "Deleted: $((BEFORE_COUNT - AFTER_COUNT)) files"
echo "Done."

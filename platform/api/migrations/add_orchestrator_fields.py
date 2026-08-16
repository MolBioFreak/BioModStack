#!/usr/bin/env python3
"""
Migration script to add GPU orchestrator fields to the jobs table.

Run from the platform/api directory:
    python migrations/add_orchestrator_fields.py

This migration adds the following columns to the 'jobs' table:
- batch_id, batch_name: For grouping related jobs
- queue_status, paused, pinned_gpu, assigned_gpu, priority: Queue management
- vram_estimate_mb, sequence_length: VRAM estimation
- retry_count, max_retries, oom_tolerance: OOM recovery
"""

import sqlite3
import sys
from pathlib import Path

from paths import get_db_path

# Database path (same as in database.py)
DB_PATH = Path(get_db_path())

MIGRATIONS = [
    # Queue Management
    ("batch_id", "VARCHAR(36)", None),
    ("batch_name", "VARCHAR(255)", None),
    ("queue_status", "VARCHAR(20)", "'queued'"),
    ("paused", "BOOLEAN", "0"),
    ("pinned_gpu", "INTEGER", None),
    ("assigned_gpu", "INTEGER", None),
    ("priority", "INTEGER", "0"),
    
    # VRAM Estimation
    ("vram_estimate_mb", "INTEGER", None),
    ("sequence_length", "INTEGER", None),
    
    # OOM Recovery
    ("retry_count", "INTEGER", "0"),
    ("max_retries", "INTEGER", "2"),
    ("oom_tolerance", "VARCHAR(20)", "'allow'"),
]


def get_existing_columns(cursor) -> set:
    """Get set of existing column names in jobs table."""
    cursor.execute("PRAGMA table_info(jobs)")
    return {row[1] for row in cursor.fetchall()}


def run_migration():
    """Add new columns to the jobs table if they don't exist."""
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        print("Run the API server first to create the database, then run this migration.")
        sys.exit(1)
    
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cursor = conn.cursor()
    
    try:
        existing = get_existing_columns(cursor)
        print(f"Existing columns in 'jobs' table: {len(existing)}")
        
        added = 0
        skipped = 0
        
        for col_name, col_type, default in MIGRATIONS:
            if col_name in existing:
                print(f"  [SKIP] {col_name} - already exists")
                skipped += 1
                continue
            
            # Build ALTER TABLE statement
            if default is not None:
                sql = f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type} DEFAULT {default}"
            else:
                sql = f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}"
            
            print(f"  [ADD]  {col_name} {col_type}" + (f" DEFAULT {default}" if default else ""))
            cursor.execute(sql)
            added += 1
        
        # Create index on batch_id for fast batch queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_jobs_batch_id ON jobs(batch_id)
        """)
        
        # Create index on queue_status for orchestrator polling
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_jobs_queue_status ON jobs(queue_status)
        """)
        
        conn.commit()
        print(f"\nMigration complete: {added} columns added, {skipped} skipped")
        print("Indexes created: ix_jobs_batch_id, ix_jobs_queue_status")
        
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print(f"GPU Orchestrator Migration")
    print(f"Database: {DB_PATH}")
    print("-" * 50)
    run_migration()

#!/usr/bin/env python3
"""
Migration script to add Antibody Design fields to the designs table.

Run from the platform/api directory:
    python migrations/add_antibody_fields.py
"""

import sqlite3
import sys
from pathlib import Path

from paths import get_db_path

# Database path (same as in database.py)
DB_PATH = Path(get_db_path())

MIGRATIONS = [
    # CDR Sequences
    ("cdr_h1", "VARCHAR(100)", None),
    ("cdr_h2", "VARCHAR(100)", None),
    ("cdr_h3", "VARCHAR(100)", None),
    ("cdr_l1", "VARCHAR(100)", None),
    ("cdr_l2", "VARCHAR(100)", None),
    ("cdr_l3", "VARCHAR(100)", None),
    ("numbering_scheme", "VARCHAR(20)", "'imgt'"),
    
    # Antibody Properties
    ("humanness_score", "FLOAT", None),
    ("developability_flags", "JSON", None),
    
    # Stability / Inverse Folding
    ("stability_data", "JSON", None),
    ("antifold_logits_path", "VARCHAR(500)", None),
]


def get_existing_columns(cursor, table_name) -> set:
    """Get set of existing column names in table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


def run_migration():
    """Add new columns to the designs table."""
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        sys.exit(1)
    
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cursor = conn.cursor()
    
    try:
        existing = get_existing_columns(cursor, "designs")
        print(f"Existing columns in 'designs' table: {len(existing)}")
        
        added = 0
        skipped = 0
        
        for col_name, col_type, default in MIGRATIONS:
            if col_name in existing:
                print(f"  [SKIP] {col_name} - already exists")
                skipped += 1
                continue
            
            # Build ALTER TABLE statement
            if default is not None:
                sql = f"ALTER TABLE designs ADD COLUMN {col_name} {col_type} DEFAULT {default}"
            else:
                sql = f"ALTER TABLE designs ADD COLUMN {col_name} {col_type}"
            
            print(f"  [ADD]  {col_name} {col_type}" + (f" DEFAULT {default}" if default else ""))
            cursor.execute(sql)
            added += 1
        
        conn.commit()
        print(f"\nMigration complete: {added} columns added, {skipped} skipped")
        
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print(f"Antibody Fields Migration")
    print(f"Database: {DB_PATH}")
    print("-" * 50)
    run_migration()

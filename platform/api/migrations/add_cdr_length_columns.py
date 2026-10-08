"""
Migration: Add CDR length and binder size columns to designs table.

This adds columns for sorting antibody designs by CDR loop lengths and total binder size.
"""
import sqlite3
from pathlib import Path

from paths import get_db_path

def migrate():
    db_path = Path(get_db_path())
    
    if not db_path.exists():
        print(f"Database not found at {db_path}")
        return
    
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cursor = conn.cursor()
    
    # New columns to add
    columns = [
        ("binder_length", "INTEGER"),
        ("cdr_h1_length", "INTEGER"),
        ("cdr_h2_length", "INTEGER"),
        ("cdr_h3_length", "INTEGER"),
        ("cdr_l1_length", "INTEGER"),
        ("cdr_l2_length", "INTEGER"),
        ("cdr_l3_length", "INTEGER"),
        ("antibody_type", "VARCHAR(20)"),
    ]
    
    for col_name, col_type in columns:
        try:
            cursor.execute(f"ALTER TABLE designs ADD COLUMN {col_name} {col_type}")
            print(f"✓ Added column: {col_name}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"- Column already exists: {col_name}")
            else:
                print(f"✗ Error adding {col_name}: {e}")
    
    conn.commit()
    conn.close()
    print("\nMigration complete!")

if __name__ == "__main__":
    migrate()

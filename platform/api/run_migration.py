import sqlite3
import os

from paths import get_db_path

DB_PATH = str(get_db_path())

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"DB not found at {DB_PATH}")
        return

    print(f"Migrating {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cursor = conn.cursor()
    
    columns = [
        ("current_stage", "VARCHAR(50)"),
        ("completed_stages", "JSON"),
        ("stage_outputs", "JSON"),
        ("awaiting_input", "BOOLEAN DEFAULT 0"),
        ("awaiting_stage", "VARCHAR(50)"),
        ("awaiting_payload", "JSON"),
        ("decision_history", "JSON"),
    ]
    
    for col_name, col_type in columns:
        try:
            cursor.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}")
            print(f"Added column {col_name}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print(f"Column {col_name} already exists")
            else:
                print(f"Error adding {col_name}: {e}")
                
    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()

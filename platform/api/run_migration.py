import sqlite3
import os

DB_PATH = "biomodstack.db"

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"DB not found at {DB_PATH}")
        return

    print(f"Migrating {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    columns = [
        ("current_stage", "VARCHAR(50)"),
        ("completed_stages", "JSON"),
        ("stage_outputs", "JSON"),
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

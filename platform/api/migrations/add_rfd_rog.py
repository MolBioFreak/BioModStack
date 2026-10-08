"""Add rfd_rog column to designs table."""
import sqlite3
import sys


def migrate(db_path: str = "./biomodstack.db") -> None:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("ALTER TABLE designs ADD COLUMN rfd_rog REAL")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "./biomodstack.db"
    migrate(db)

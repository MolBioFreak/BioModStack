"""Add provenance fields to nucleotide_sequences table."""
import sqlite3
import sys


def migrate(db_path: str = "./biomodstack.db") -> None:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    columns = [
        ("parent_id", "TEXT"),
        ("operation", "TEXT"),
        ("operation_params", "TEXT"),  # JSON stored as TEXT
        ("version", "INTEGER DEFAULT 1"),
    ]

    for col_name, col_type in columns:
        try:
            cursor.execute(f"ALTER TABLE nucleotide_sequences ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "./biomodstack.db"
    migrate(db)

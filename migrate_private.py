#!/usr/bin/env python3
"""
migrate_private.py — Add maps_url and photo_url columns to private_campgrounds.

Safe to run multiple times — skips columns that already exist.

Usage:
    python migrate_private.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "private.db"

NEW_COLUMNS = [
    ("maps_url",  "TEXT"),  # Google Maps deep link (maps.google.com/?cid=...)
    ("photo_url", "TEXT"),  # Photo URL or local path
]


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def main():
    print(f"Opening {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)

    added = []
    for col_name, col_type in NEW_COLUMNS:
        if column_exists(conn, "private_campgrounds", col_name):
            print(f"  Column '{col_name}' already exists — skipping.")
        else:
            conn.execute(
                f"ALTER TABLE private_campgrounds ADD COLUMN {col_name} {col_type}"
            )
            added.append(col_name)
            print(f"  Added column '{col_name} {col_type}'.")

    if added:
        conn.commit()
        print(f"\nMigration complete. Added: {', '.join(added)}")
    else:
        print("\nNo changes needed.")

    conn.close()


if __name__ == "__main__":
    main()

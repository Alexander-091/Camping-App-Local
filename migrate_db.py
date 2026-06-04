#!/usr/bin/env python3
"""
Migrate from corrupted sepaq.db to fresh sepaq_new.db
This swaps the databases so the scraper can begin populating the fresh one.
"""
import os
import shutil

DATA_DIR = "data"
OLD_DB = os.path.join(DATA_DIR, "sepaq.db")
NEW_DB = os.path.join(DATA_DIR, "sepaq_new.db")
ARCHIVE_DB = os.path.join(DATA_DIR, "sepaq.db.corrupted_archived")

def migrate():
    print("=" * 60)
    print("Database Migration")
    print("=" * 60)

    # Verify new DB exists and is valid
    if not os.path.exists(NEW_DB):
        print(f"✗ New database not found: {NEW_DB}")
        return False

    import sqlite3
    try:
        conn = sqlite3.connect(NEW_DB)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(sites)")
        cols = {col[1] for col in cursor.fetchall()}
        conn.close()

        if 'x_pct' not in cols or 'y_pct' not in cols:
            print("✗ New database missing x_pct/y_pct columns")
            return False

        print(f"✓ New database valid with {len(cols)} columns")
    except Exception as e:
        print(f"✗ Cannot validate new database: {e}")
        return False

    # Archive old DB
    if os.path.exists(OLD_DB):
        try:
            shutil.move(OLD_DB, ARCHIVE_DB)
            print(f"✓ Archived old database: {ARCHIVE_DB}")
        except Exception as e:
            print(f"✗ Cannot archive old database: {e}")
            return False

    # Move new to old
    try:
        shutil.move(NEW_DB, OLD_DB)
        print(f"✓ Activated new database: {OLD_DB}")
    except Exception as e:
        print(f"✗ Cannot activate new database: {e}")
        return False

    print("\n✓ Migration complete!")
    print("\nNext steps:")
    print("  1. Get fresh Cloudflare cookies: python scraper/get_cookie.py")
    print("  2. Run scraper: python scraper/scraper.py")

    return True

if __name__ == "__main__":
    success = migrate()
    exit(0 if success else 1)

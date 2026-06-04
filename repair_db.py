#!/usr/bin/env python3
"""
Emergency database repair: rebuild with proper schema.
"""
import sqlite3
import os
import shutil

DB_PATH = "data/sepaq.db"
BACKUP_PATH = "data/sepaq.db.backup"

# If corrupted file exists, remove it and restore backup
if os.path.exists(DB_PATH) and os.path.exists(BACKUP_PATH):
    try:
        # Try to open corrupted DB
        conn = sqlite3.connect(DB_PATH, timeout=2)
        conn.execute("SELECT 1")
        conn.close()
    except sqlite3.DatabaseError:
        # Corrupted, try to restore
        print("✓ Database corrupted, attempting recovery...")
        try:
            os.remove(DB_PATH)
            shutil.copy(BACKUP_PATH, DB_PATH)
            print("✓ Restored from backup")
        except Exception as e:
            print(f"✗ Backup restore failed: {e}")

# Clean up journal files
for f in [DB_PATH + "-journal", DB_PATH + "-wal", DB_PATH + "-shm"]:
    if os.path.exists(f):
        try:
            os.remove(f)
            print(f"✓ Cleaned up {os.path.basename(f)}")
        except Exception as e:
            print(f"Warning: Could not remove {f}: {e}")

# Open fresh connection
print("\nConnecting to database...")
conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA journal_mode=DELETE")
conn.execute("PRAGMA synchronous=NORMAL")

# Create schema if missing (idempotent)
conn.executescript("""
    CREATE TABLE IF NOT EXISTS parks (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,
        slug       TEXT UNIQUE,
        scraped_at TEXT
    );

    CREATE TABLE IF NOT EXISTS sectors (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        park_id    INTEGER REFERENCES parks(id),
        name       TEXT,
        slug       TEXT,
        url        TEXT,
        scraped_at TEXT,
        UNIQUE(park_id, slug)
    );

    CREATE TABLE IF NOT EXISTS boucles (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        sector_id        INTEGER REFERENCES sectors(id),
        name             TEXT,
        slug             TEXT,
        url              TEXT,
        is_sector_level  INTEGER DEFAULT 0,
        map_url          TEXT,
        scraped_at       TEXT,
        UNIQUE(sector_id, slug)
    );

    CREATE TABLE IF NOT EXISTS sites (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        boucle_id   INTEGER REFERENCES boucles(id),
        unit_id     TEXT,
        site_name   TEXT,
        site_type   TEXT,
        url         TEXT,
        x_pct       REAL,
        y_pct       REAL,
        photo_url   TEXT,
        photo_data  BLOB,
        scraped_at  TEXT,
        UNIQUE(boucle_id, unit_id)
    );

    CREATE TABLE IF NOT EXISTS range_availability (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        sector_id       INTEGER REFERENCES sectors(id),
        checkin         TEXT NOT NULL,
        checkout        TEXT NOT NULL,
        sites_available INTEGER NOT NULL DEFAULT 0,
        scraped_at      TEXT,
        UNIQUE(sector_id, checkin, checkout)
    );
""")
conn.commit()

print("✓ Schema ready")

# Verify columns exist
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(sites)")
cols = {col[1] for col in cursor.fetchall()}
print(f"\nSites table columns: {sorted(cols)}")
print(f"  x_pct: {'✓' if 'x_pct' in cols else '✗'}")
print(f"  y_pct: {'✓' if 'y_pct' in cols else '✗'}")

# Check data
cursor.execute("SELECT COUNT(*) FROM sites")
site_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM boucles")
boucle_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM boucles WHERE map_url IS NOT NULL")
boucles_with_map = cursor.fetchone()[0]

print(f"\nData state:")
print(f"  {site_count:,} sites")
print(f"  {boucle_count} boucles ({boucles_with_map} with map_url)")

if site_count > 0:
    cursor.execute("SELECT COUNT(*) FROM sites WHERE x_pct IS NOT NULL")
    sites_with_pos = cursor.fetchone()[0]
    print(f"  {sites_with_pos} sites with x_pct/y_pct")

conn.close()
print("\n✓ Database ready for Phase 1 work")

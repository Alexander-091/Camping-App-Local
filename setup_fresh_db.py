#!/usr/bin/env python3
"""
Fresh database setup script.
Run this from your Camping App directory to initialize sepaq.db with proper schema.
"""
import sqlite3
import os
import shutil

def setup_fresh_db():
    data_dir = "data"
    db_path = os.path.join(data_dir, "sepaq.db")

    # Ensure data directory exists
    os.makedirs(data_dir, exist_ok=True)

    # Backup any existing DB
    if os.path.exists(db_path):
        backup_path = db_path + ".backup"
        try:
            shutil.move(db_path, backup_path)
            print(f"✓ Backed up existing database to {backup_path}")
        except Exception as e:
            print(f"✗ Cannot backup existing database: {e}")
            return False

    # Create fresh database
    try:
        conn = sqlite3.connect(db_path)

        # Create schema with x_pct, y_pct columns
        conn.executescript("""
            CREATE TABLE parks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                slug       TEXT UNIQUE,
                scraped_at TEXT
            );

            CREATE TABLE sectors (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                park_id    INTEGER REFERENCES parks(id),
                name       TEXT,
                slug       TEXT,
                url        TEXT,
                scraped_at TEXT,
                UNIQUE(park_id, slug)
            );

            CREATE TABLE campsites (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                park_id    INTEGER REFERENCES parks(id),
                sector_id  INTEGER REFERENCES sectors(id),
                name       TEXT,
                site_id    TEXT,
                type       TEXT,
                amenities  TEXT,
                scraped_at TEXT,
                UNIQUE(sector_id, site_id)
            );

            CREATE TABLE availability (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                campsite_id     INTEGER REFERENCES campsites(id),
                date            TEXT,
                available       INTEGER,
                sites_available INTEGER DEFAULT 0,
                price           REAL,
                scraped_at      TEXT,
                UNIQUE(campsite_id, date)
            );

            CREATE TABLE raw_responses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT,
                park_slug   TEXT,
                status_code INTEGER,
                body        TEXT,
                captured_at TEXT
            );

            CREATE TABLE boucles (
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

            CREATE TABLE sites (
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

            CREATE TABLE range_availability (
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

        # Verify
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(sites)")
        cols = {col[1] for col in cursor.fetchall()}

        print("\n" + "=" * 60)
        print("✓ Fresh database created successfully!")
        print("=" * 60)
        print(f"Location: {os.path.abspath(db_path)}")
        print(f"Size: {os.path.getsize(db_path)} bytes")
        print(f"\nSites table columns ({len(cols)}):")
        for col in sorted(cols):
            print(f"  • {col}")

        has_x = 'x_pct' in cols
        has_y = 'y_pct' in cols
        print(f"\nPosition columns:")
        print(f"  • x_pct: {'✓ READY' if has_x else '✗ MISSING'}")
        print(f"  • y_pct: {'✓ READY' if has_y else '✗ MISSING'}")

        conn.close()
        return True

    except Exception as e:
        print(f"✗ Error creating database: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("Fresh Database Setup\n")
    success = setup_fresh_db()

    if success:
        print("\n" + "=" * 60)
        print("Next steps:")
        print("=" * 60)
        print("1. Refresh Cloudflare cookies:")
        print("   python scraper/get_cookie.py")
        print("\n2. Run the scraper:")
        print("   python scraper/scraper.py")
        print("\nThe scraper will populate parks, sectors, boucles, and")
        print("sites with map URLs and position data (x_pct, y_pct).")
        print("=" * 60)

    exit(0 if success else 1)

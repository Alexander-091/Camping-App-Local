"""
inspect_db.py
-------------
Run this after scraper.py to see what was captured.
Prints a summary of parks, campsites, and availability.

Usage:
    python inspect_db.py
"""

import sqlite3
import os
import json

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "sepaq.db")


def inspect():
    if not os.path.exists(DB_PATH):
        print("No database found. Run scraper.py first.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # ── Parks ─────────────────────────────────────────────────────────────────
    parks = conn.execute("SELECT * FROM parks ORDER BY name").fetchall()
    print(f"\n{'='*60}")
    print(f"  PARKS ({len(parks)} total)")
    print(f"{'='*60}")

    for park in parks:
        site_count = conn.execute(
            "SELECT COUNT(*) AS c FROM campsites WHERE park_id = ?", (park["id"],)
        ).fetchone()["c"]

        avail_count = conn.execute("""
            SELECT COUNT(*) AS c
            FROM   availability a
            JOIN   campsites cs ON cs.id = a.campsite_id
            WHERE  cs.park_id = ? AND a.available = 1 AND a.date >= date('now')
        """, (park["id"],)).fetchone()["c"]

        status = f"{site_count} sites  |  {avail_count} available future dates"
        print(f"  • {park['name']:<45}  {status}")

    # ── Overall counts ────────────────────────────────────────────────────────
    total_sites = conn.execute("SELECT COUNT(*) AS c FROM campsites").fetchone()["c"]
    total_avail = conn.execute("SELECT COUNT(*) AS c FROM availability").fetchone()["c"]
    future_avail = conn.execute(
        "SELECT COUNT(*) AS c FROM availability WHERE available = 1 AND date >= date('now')"
    ).fetchone()["c"]

    print(f"\n{'='*60}")
    print(f"  TOTALS")
    print(f"{'='*60}")
    print(f"  Campsites tracked:          {total_sites}")
    print(f"  Availability rows:          {total_avail}")
    print(f"  Future available dates:     {future_avail}")

    # ── Price range ───────────────────────────────────────────────────────────
    prices = conn.execute("""
        SELECT MIN(price) AS lo, MAX(price) AS hi, AVG(price) AS avg
        FROM   availability
        WHERE  available = 1 AND price IS NOT NULL AND date >= date('now')
    """).fetchone()

    if prices and prices["lo"] is not None:
        print(f"  Price range (future):       ${prices['lo']:.2f} – ${prices['hi']:.2f}  "
              f"(avg ${prices['avg']:.2f})")

    # ── Raw responses ─────────────────────────────────────────────────────────
    api_calls = conn.execute(
        "SELECT park_slug, status_code, captured_at, LENGTH(body) AS bytes "
        "FROM raw_responses ORDER BY captured_at DESC LIMIT 10"
    ).fetchall()

    if api_calls:
        print(f"\n{'='*60}")
        print(f"  RECENT API RESPONSES (last 10)")
        print(f"{'='*60}")
        for call in api_calls:
            size = f"{call['bytes']:,} bytes" if call["bytes"] else "empty"
            print(f"  {call['captured_at'][:19]}  {call['park_slug']:<45}  {size}")

    # ── Upcoming availability sample ──────────────────────────────────────────
    upcoming = conn.execute("""
        SELECT p.name AS park, a.date, COUNT(*) AS sites, MIN(a.price) AS min_price
        FROM   availability a
        JOIN   campsites cs ON cs.id = a.campsite_id
        JOIN   parks p      ON p.id  = cs.park_id
        WHERE  a.available = 1 AND a.date >= date('now')
        GROUP  BY p.id, a.date
        ORDER  BY a.date, p.name
        LIMIT  10
    """).fetchall()

    if upcoming:
        print(f"\n{'='*60}")
        print(f"  NEXT AVAILABLE DATES (sample)")
        print(f"{'='*60}")
        for row in upcoming:
            price = f"${row['min_price']:.2f}/night" if row["min_price"] else "—"
            print(f"  {row['date']}  {row['park']:<42}  {row['sites']} sites  {price}")
    else:
        print(f"\n  ⚠  No availability data yet.")
        print(f"     If all parks returned [], run get_cookie.py to refresh cookies,")
        print(f"     then run scraper.py again within 30 minutes.")

    conn.close()


if __name__ == "__main__":
    inspect()

#!/usr/bin/env python3
"""
enrich_private.py — Enrich private campgrounds via Google Places API.

For each campground missing phone / website / address / maps_url / photo_url,
searches the Google Places Nearby Search API using the known lat/lon + name,
validates the match by distance, and writes the results back.

Prerequisites:
  1. Run migrate_private.py first (adds maps_url, photo_url columns).
  2. Create a .env file with GOOGLE_PLACES_API_KEY=<your key>.
     Get a key at: https://console.cloud.google.com/apis/library/places-backend.googleapis.com
     Enable: Places API (New) — or legacy Places API.

Usage:
    python enrich_private.py              # dry-run: show what would be written
    python enrich_private.py --apply      # write to DB
    python enrich_private.py --apply --limit 50   # process only 50 rows (testing)
    python enrich_private.py --apply --force       # re-process already-enriched rows

Rules:
  - Never overwrites an existing non-empty value (unless --force).
  - Skips a match if the Places result is > MAX_DISTANCE_KM from the known coords.
  - Saves progress: rows where enrichment was attempted get enriched_at set,
    so re-runs skip them automatically (unless --force).
  - Rate-limited to avoid quota errors (REQUESTS_PER_SECOND).

Cost estimate (Google Places API, 2024 pricing):
  - Nearby Search: $0.032 per request → ~$51 for all 1,599 missing rows.
  - Photo:         $0.007 per request → additional ~$11 if photos enabled.
  - Run with --limit first to spot-check quality before committing budget.
"""

import argparse
import json
import math
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ─── Config ──────────────────────────────────────────────────────────────────

DB_PATH              = Path(__file__).parent / "data" / "private.db"
ENV_FILE             = Path(__file__).parent / ".env"

MAX_DISTANCE_KM      = 2.0    # reject Places match if farther than this
REQUESTS_PER_SECOND  = 5      # stay well under the 10 QPS default quota
NEARBY_RADIUS_M      = 2000   # search radius for Nearby Search (metres)

PLACES_NEARBY_URL    = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
PLACES_DETAIL_URL    = "https://maps.googleapis.com/maps/api/place/details/json"
PLACES_PHOTO_URL     = "https://maps.googleapis.com/maps/api/place/photo"

DETAIL_FIELDS        = "name,formatted_phone_number,website,formatted_address,url,photos"

# ─── Helpers ─────────────────────────────────────────────────────────────────

def load_env():
    """Load key=value pairs from .env into os.environ."""
    if not ENV_FILE.exists():
        return
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def http_get(url: str) -> dict:
    """Simple GET returning parsed JSON."""
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode())


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())

# ─── Places API ──────────────────────────────────────────────────────────────

def nearby_search(api_key: str, lat: float, lon: float, name: str) -> list[dict]:
    """Return Nearby Search results for a campground name near lat/lon."""
    params = urllib.parse.urlencode({
        "location":  f"{lat},{lon}",
        "radius":    NEARBY_RADIUS_M,
        "keyword":   name,
        "type":      "campground",
        "key":       api_key,
    })
    data = http_get(f"{PLACES_NEARBY_URL}?{params}")
    status = data.get("status", "")
    if status not in ("OK", "ZERO_RESULTS"):
        raise RuntimeError(f"Places Nearby Search error: {status} — {data.get('error_message','')}")
    return data.get("results", [])


def place_details(api_key: str, place_id: str) -> dict:
    """Fetch detail fields for a place_id."""
    params = urllib.parse.urlencode({
        "place_id": place_id,
        "fields":   DETAIL_FIELDS,
        "key":      api_key,
    })
    data = http_get(f"{PLACES_DETAIL_URL}?{params}")
    status = data.get("status", "")
    if status != "OK":
        raise RuntimeError(f"Places Details error: {status} — {data.get('error_message','')}")
    return data.get("result", {})


def photo_url(api_key: str, photo_reference: str, max_width: int = 800) -> str:
    """Return the redirect URL for a Places photo (does not download the image)."""
    params = urllib.parse.urlencode({
        "photoreference": photo_reference,
        "maxwidth":       max_width,
        "key":            api_key,
    })
    # The photo endpoint returns a redirect; we just store the API URL.
    # The app can resolve it at display time, or a follow-up script can download blobs.
    return f"{PLACES_PHOTO_URL}?{params}"

# ─── Main ────────────────────────────────────────────────────────────────────

def enrich_row(api_key: str, row: sqlite3.Row, dry_run: bool, force: bool) -> dict | None:
    """
    Look up one campground in Places. Return a dict of fields to update,
    or None if no confident match found.
    """
    lat, lon = row["lat"], row["lon"]
    name     = row["name"] or ""

    try:
        results = nearby_search(api_key, lat, lon, name)
    except Exception as e:
        print(f"    [WARN] Nearby Search failed: {e}")
        return None

    if not results:
        return None

    # Pick the closest result within MAX_DISTANCE_KM
    best = None
    best_dist = float("inf")
    for r in results:
        loc = r.get("geometry", {}).get("location", {})
        rlat, rlon = loc.get("lat"), loc.get("lng")
        if rlat is None or rlon is None:
            continue
        dist = haversine_km(lat, lon, rlat, rlon)
        if dist < best_dist:
            best_dist = dist
            best = r

    if best is None or best_dist > MAX_DISTANCE_KM:
        print(f"    [SKIP] No confident match (closest={best_dist:.2f} km > {MAX_DISTANCE_KM} km)")
        return None

    place_id = best.get("place_id")
    if not place_id:
        return None

    # Fetch details
    try:
        detail = place_details(api_key, place_id)
    except Exception as e:
        print(f"    [WARN] Details fetch failed: {e}")
        return None

    # Build update dict — only fill fields that are currently empty (or force)
    updates = {}

    def maybe_set(field: str, value: str | None):
        if not value:
            return
        existing = row[field]
        if force or not existing:
            updates[field] = value

    maybe_set("phone",   detail.get("formatted_phone_number"))
    maybe_set("website", detail.get("website"))
    maybe_set("address", detail.get("formatted_address"))
    maybe_set("maps_url", detail.get("url"))  # maps.google.com/... deep link

    # Photo — store the Places API URL (expires; a follow-up could download blobs)
    photos = detail.get("photos", [])
    if photos:
        ref = photos[0].get("photo_reference")
        if ref:
            maybe_set("photo_url", photo_url(api_key, ref))

    if not updates:
        return None  # nothing new to add

    print(f"    [MATCH] dist={best_dist:.2f} km | {list(updates.keys())}")
    return updates


def main():
    parser = argparse.ArgumentParser(description="Enrich private campgrounds via Google Places.")
    parser.add_argument("--apply",  action="store_true", help="Write to DB (default: dry-run)")
    parser.add_argument("--force",  action="store_true", help="Re-process already-enriched rows")
    parser.add_argument("--limit",  type=int, default=0,  help="Max rows to process (0 = all)")
    args = parser.parse_args()

    load_env()
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if not api_key or api_key == "your_key_here":
        print("ERROR: GOOGLE_PLACES_API_KEY not set. Copy .env.example to .env and add your key.")
        raise SystemExit(1)

    dry_run = not args.apply
    if dry_run:
        print("DRY-RUN mode — pass --apply to write changes.\n")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Verify migration has been run
    if not column_exists(conn, "private_campgrounds", "maps_url"):
        print("ERROR: maps_url column not found. Run migrate_private.py first.")
        conn.close()
        raise SystemExit(1)

    # Check for enriched_at column; add it if missing (tracks progress)
    if not column_exists(conn, "private_campgrounds", "enriched_at"):
        conn.execute("ALTER TABLE private_campgrounds ADD COLUMN enriched_at TEXT")
        conn.commit()
        print("Added 'enriched_at' tracking column.\n")

    # Select rows that need enrichment
    where_clauses = []
    if not args.force:
        where_clauses.append("enriched_at IS NULL")  # skip already-processed
    # Only bother with rows missing at least one field
    where_clauses.append(
        "(phone IS NULL OR phone = '' "
        " OR website IS NULL OR website = '' "
        " OR address IS NULL OR address = '' "
        " OR maps_url IS NULL "
        " OR photo_url IS NULL)"
    )

    where = " AND ".join(f"({c})" for c in where_clauses)
    query = f"SELECT * FROM private_campgrounds WHERE {where} ORDER BY id"
    if args.limit:
        query += f" LIMIT {args.limit}"

    rows = conn.execute(query).fetchall()
    total = len(rows)
    print(f"Rows to process: {total}\n")

    interval = 1.0 / REQUESTS_PER_SECOND
    updated = 0
    skipped = 0

    for i, row in enumerate(rows, 1):
        row_id = row["id"]
        name   = row["name"] or "(unnamed)"
        print(f"[{i}/{total}] id={row_id} {name}")

        updates = enrich_row(api_key, row, dry_run=dry_run, force=args.force)

        # Always record that we attempted this row (even if no match)
        updates = updates or {}
        updates["enriched_at"] = utcnow()

        if dry_run:
            if len(updates) > 1:  # more than just enriched_at
                print(f"    Would write: {updates}")
                updated += 1
            else:
                skipped += 1
        else:
            set_clause = ", ".join(f"{f} = ?" for f in updates)
            values     = list(updates.values()) + [row_id]
            conn.execute(
                f"UPDATE private_campgrounds SET {set_clause} WHERE id = ?",
                values
            )
            conn.commit()
            if len(updates) > 1:
                updated += 1
            else:
                skipped += 1

        # Rate limit: two API calls per row (nearby + details), so halve the interval
        time.sleep(interval * 2)

    print(f"\n{'[DRY-RUN] ' if dry_run else ''}Done — {updated} enriched, {skipped} no-match/skipped.")
    conn.close()


if __name__ == "__main__":
    main()

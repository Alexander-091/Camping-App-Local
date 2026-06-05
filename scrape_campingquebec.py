#!/usr/bin/env python3
"""
scrape_campingquebec.py — Scrape campingquebec.com for contact info.

Uses the WordPress REST API to enumerate all 826 member campgrounds, then
fetches each detail page and parses: address, phone, email, website, Google
Maps URL, and lat/lon.

Results are matched against private.db by name similarity + geographic
proximity and used to fill empty fields.  Campgrounds not already in the DB
are inserted as new rows (source='campingquebec').

Prerequisites:
    pip install requests beautifulsoup4 thefuzz python-Levenshtein

Usage:
    python scrape_campingquebec.py              # dry-run
    python scrape_campingquebec.py --apply      # write to DB
    python scrape_campingquebec.py --apply --limit 20   # test batch
    python scrape_campingquebec.py --apply --force      # re-scrape already done

The script is resumable: rows already scraped have cq_scraped_at set and are
skipped unless --force is passed.
"""

import argparse
import html
import json
import math
import re
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
    from thefuzz import fuzz
except ImportError:
    print("Missing dependencies. Run:")
    print("  pip install requests beautifulsoup4 thefuzz python-Levenshtein")
    raise SystemExit(1)

# ─── Config ──────────────────────────────────────────────────────────────────

DB_PATH         = Path(__file__).parent / "data" / "private.db"
BASE_URL        = "https://www.campingquebec.com"
API_URL         = f"{BASE_URL}/wp-json/wp/v2/campings"
DETAIL_BASE     = f"{BASE_URL}/en/campings"

# Match thresholds
NAME_SCORE_MIN  = 75    # fuzz.token_sort_ratio minimum to consider a name match
DIST_MAX_KM     = 5.0   # max distance to accept a geo match

REQUESTS_PER_SEC = 3    # be polite
SLEEP_BETWEEN    = 1.0 / REQUESTS_PER_SEC

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CampingAppBot/1.0; +https://github.com/alexmil/camping-app)",
    "Accept-Language": "en-CA,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def column_exists(conn, table, col):
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def ensure_columns(conn):
    """Add any columns that might not exist yet."""
    needed = [
        ("maps_url",      "TEXT"),
        ("photo_url",     "TEXT"),
        ("enriched_at",   "TEXT"),
        ("cq_scraped_at", "TEXT"),  # tracks Camping Québec scrape progress
    ]
    for col, typ in needed:
        if not column_exists(conn, "private_campgrounds", col):
            conn.execute(f"ALTER TABLE private_campgrounds ADD COLUMN {col} {typ}")
    conn.commit()

# ─── REST API enumeration ────────────────────────────────────────────────────

def fetch_all_slugs(session: requests.Session) -> list[dict]:
    """
    Return list of {id, slug, region_slug, title, link} for all campgrounds.
    Uses WP REST API, 100 per page.
    """
    all_items = []
    page = 1
    while True:
        url = f"{API_URL}?per_page=100&page={page}&_fields=id,slug,link,title"
        resp = session.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        items = resp.json()
        if not items:
            break
        for item in items:
            # link is like /fr/campings/{region}/{slug} — extract region
            link = item.get("link", "")
            parts = link.rstrip("/").split("/")
            # parts: [..., 'campings', '{region}', '{slug}']
            region_slug = parts[-2] if len(parts) >= 2 else ""
            all_items.append({
                "id":          item["id"],
                "slug":        item["slug"],
                "region_slug": region_slug,
                "title":       html.unescape(item["title"]["rendered"]),
            })
        total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
        print(f"  Fetched page {page}/{total_pages} ({len(all_items)} total so far)")
        if page >= total_pages:
            break
        page += 1
        time.sleep(SLEEP_BETWEEN)
    return all_items


# ─── Detail page scraping ────────────────────────────────────────────────────

def parse_detail_page(html_text: str, page_url: str) -> dict:
    """Parse a campground detail page and return contact fields."""
    soup = BeautifulSoup(html_text, "html.parser")
    result = {
        "phone":    None,
        "email":    None,
        "website":  None,
        "address":  None,
        "maps_url": None,
        "lat":      None,
        "lon":      None,
        "cq_url":   page_url,
    }

    # Phone — first tel: link
    tel = soup.find("a", href=re.compile(r"^tel:"))
    if tel:
        result["phone"] = tel["href"].replace("tel:", "").strip()

    # Email
    mail = soup.find("a", href=re.compile(r"^mailto:"))
    if mail:
        result["email"] = mail["href"].replace("mailto:", "").strip()

    # Google Maps link → also extract lat/lon
    maps = soup.find("a", href=re.compile(r"google\.[a-z]+/maps\?q="))
    if maps:
        href = maps["href"]
        result["maps_url"] = href
        m = re.search(r"q=([-\d.]+),([-\d.]+)", href)
        if m:
            result["lat"] = float(m.group(1))
            result["lon"] = float(m.group(2))

    # Website — <a> with text "Website" pointing off-domain
    for a in soup.find_all("a", href=re.compile(r"^https?://")):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if text.lower() == "website" and "campingquebec.com" not in href:
            result["website"] = href
            break

    # Address — text block above the map link
    # The info section contains address lines before the phone/map links.
    # Strategy: find the "INFORMATION" heading, then grab the text block
    # before the first tel: link.
    info_heading = soup.find(string=re.compile(r"INFORMATION", re.I))
    if info_heading:
        # Walk up to find the section container, then collect text
        section = info_heading.find_parent(["section", "div", "article"])
        if section:
            # Collect all text nodes before the first tel: link
            lines = []
            for elem in section.descendants:
                if hasattr(elem, "name") and elem.name == "a":
                    href = elem.get("href", "")
                    if href.startswith("tel:") or href.startswith("mailto:"):
                        break
                    if "google" in href and "maps" in href:
                        break
                elif hasattr(elem, "string") and elem.string:
                    text = elem.string.strip()
                    # Skip nav/heading noise
                    if text and text not in ("INFORMATION", "Share", "Home") and len(text) > 3:
                        lines.append(text)
            # The address is typically 2 lines: street, then city+postal
            addr_lines = [l for l in lines if l and not l.startswith("See ")]
            if addr_lines:
                result["address"] = ", ".join(addr_lines[:2])

    return result


def fetch_detail(session: requests.Session, region_slug: str, slug: str) -> dict | None:
    """Fetch and parse a campground detail page. Returns None on error."""
    url = f"{DETAIL_BASE}/{region_slug}/{slug}"
    try:
        resp = session.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 404:
            print(f"    [404] {url}")
            return None
        resp.raise_for_status()
        return parse_detail_page(resp.text, url)
    except Exception as e:
        print(f"    [ERROR] {url}: {e}")
        return None


# ─── DB matching ─────────────────────────────────────────────────────────────

def load_db_campgrounds(conn) -> list[dict]:
    """Load all private_campgrounds as dicts for matching."""
    rows = conn.execute(
        "SELECT id, name, lat, lon, phone, website, address, maps_url, email "
        "FROM private_campgrounds"
    ).fetchall()
    return [dict(r) for r in rows]


def find_best_match(cq: dict, db_rows: list[dict]) -> dict | None:
    """
    Find the best matching row in private.db for a Camping Québec campground.
    Requires both name similarity ≥ NAME_SCORE_MIN AND distance ≤ DIST_MAX_KM.
    Returns the matching row dict or None.
    """
    cq_lat, cq_lon = cq.get("lat"), cq.get("lon")
    cq_name = cq["title"].upper()

    best_row  = None
    best_score = 0

    for row in db_rows:
        if row["lat"] is None or row["lon"] is None:
            continue

        dist = haversine_km(cq_lat, cq_lon, row["lat"], row["lon"])
        if dist > DIST_MAX_KM:
            continue

        name_score = fuzz.token_sort_ratio(cq_name, (row["name"] or "").upper())
        # Combine: name score weighted, distance as tiebreaker
        combined = name_score - (dist * 2)  # small distance penalty

        if name_score >= NAME_SCORE_MIN and combined > best_score:
            best_score = combined
            best_row = row

    return best_row


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape campingquebec.com for contact info.")
    parser.add_argument("--apply",  action="store_true", help="Write to DB (default: dry-run)")
    parser.add_argument("--force",  action="store_true", help="Re-scrape already-processed rows")
    parser.add_argument("--limit",  type=int, default=0,  help="Max campgrounds to process (0=all)")
    args = parser.parse_args()

    dry_run = not args.apply
    if dry_run:
        print("DRY-RUN mode — pass --apply to write changes.\n")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_columns(conn)

    # Track which CQ slugs we've already processed
    already_done: set[str] = set()
    if not args.force:
        rows = conn.execute(
            "SELECT source_id FROM private_campgrounds "
            "WHERE source='campingquebec' OR cq_scraped_at IS NOT NULL"
        ).fetchall()
        already_done = {r[0] for r in rows}

    db_rows = load_db_campgrounds(conn)
    print(f"Loaded {len(db_rows)} rows from private.db\n")

    session = requests.Session()
    session.headers.update(HEADERS)

    # Step 1: Get all slugs from REST API
    print("Fetching campground list from REST API...")
    all_slugs = fetch_all_slugs(session)
    print(f"\nTotal from Camping Québec: {len(all_slugs)}\n")

    if args.limit:
        all_slugs = all_slugs[:args.limit]

    enriched   = 0
    inserted   = 0
    skipped    = 0
    no_match   = 0
    errors     = 0

    for i, cq_meta in enumerate(all_slugs, 1):
        slug        = cq_meta["slug"]
        region_slug = cq_meta["region_slug"]
        title       = cq_meta["title"]

        if slug in already_done:
            skipped += 1
            continue

        print(f"[{i}/{len(all_slugs)}] {title}")

        detail = fetch_detail(session, region_slug, slug)
        time.sleep(SLEEP_BETWEEN)

        if detail is None:
            errors += 1
            continue

        # Need lat/lon to do matching
        cq_lat, cq_lon = detail.get("lat"), detail.get("lon")
        if cq_lat is None or cq_lon is None:
            print(f"  [SKIP] No coordinates on page")
            no_match += 1
            # Still mark as attempted so we don't retry
            if not dry_run:
                conn.execute(
                    "UPDATE private_campgrounds SET cq_scraped_at=? "
                    "WHERE source='campingquebec' AND source_id=?",
                    (utcnow(), slug)
                )
                conn.commit()
            continue

        # Try to match against existing OSM record
        cq_for_match = {**cq_meta, "lat": cq_lat, "lon": cq_lon}
        match = find_best_match(cq_for_match, db_rows)

        if match:
            # Build update dict — only fill empty fields
            updates: dict[str, object] = {"cq_scraped_at": utcnow()}
            def maybe(field, value):
                if value and not match.get(field):
                    updates[field] = value

            maybe("phone",    detail["phone"])
            maybe("website",  detail["website"])
            maybe("address",  detail["address"])
            maybe("maps_url", detail["maps_url"])
            # Store email in tags_json extension or skip (no dedicated column)
            # For now we skip email — can add a column later if wanted

            filled = [k for k in updates if k != "cq_scraped_at"]
            print(f"  [MATCH] '{match['name']}' | filling: {filled}")

            if not dry_run and updates:
                set_clause = ", ".join(f"{f}=?" for f in updates)
                conn.execute(
                    f"UPDATE private_campgrounds SET {set_clause} WHERE id=?",
                    list(updates.values()) + [match["id"]]
                )
                conn.commit()
                # Refresh db_rows entry so subsequent matches see updated data
                for r in db_rows:
                    if r["id"] == match["id"]:
                        r.update(updates)
                        break
            enriched += 1

        else:
            # No match — insert as new campground
            print(f"  [NEW] No OSM match — inserting")
            if not dry_run:
                conn.execute("""
                    INSERT OR IGNORE INTO private_campgrounds
                    (source, source_id, name, lat, lon, website, phone,
                     address, region, maps_url, fetched_at, cq_scraped_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    "campingquebec",
                    slug,
                    title,
                    cq_lat,
                    cq_lon,
                    detail["website"],
                    detail["phone"],
                    detail["address"],
                    region_slug.replace("-", " ").title(),
                    detail["maps_url"],
                    utcnow(),
                    utcnow(),
                ))
                conn.commit()
                # Add to in-memory list so future iterations can match against it
                db_rows.append({
                    "id": conn.execute("SELECT last_insert_rowid()").fetchone()[0],
                    "name": title, "lat": cq_lat, "lon": cq_lon,
                    "phone": detail["phone"], "website": detail["website"],
                    "address": detail["address"], "maps_url": detail["maps_url"],
                    "email": detail.get("email"),
                })
            inserted += 1

    print(f"\n{'[DRY-RUN] ' if dry_run else ''}Done:")
    print(f"  Enriched existing rows : {enriched}")
    print(f"  Inserted new rows      : {inserted}")
    print(f"  Skipped (already done) : {skipped}")
    print(f"  No coords / no match   : {no_match}")
    print(f"  Errors                 : {errors}")
    conn.close()


if __name__ == "__main__":
    main()

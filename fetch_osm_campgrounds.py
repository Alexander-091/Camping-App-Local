#!/usr/bin/env python3
"""
Fetch private campgrounds from OpenStreetMap via Overpass API.

This script queries the Overpass API for all camping sites (node + way with
tourism=camp_site) across Quebec and neighboring regions, normalizes the data,
filters out SEPAQ-operated sites, and optionally loads into the database.

Usage:
    python fetch_osm_campgrounds.py              # Fetch and print summary
    python fetch_osm_campgrounds.py --save       # Fetch and save to database
    python fetch_osm_campgrounds.py --json       # Output raw JSON to stdout
"""

import json
import sqlite3
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

# ─── Config ───────────────────────────────────────────────────────────────────

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Regional bounding boxes — Quebec + IMMEDIATE neighbours only
# (Ontario near QC, New Brunswick/Nova Scotia, New York / Vermont / Maine).
# Format: (south, west, north, east) for each region. Boxes kept adjacent to
# Quebec so we don't pull campgrounds far outside the target area.
REGIONS = {
    # Quebec itself (south to north, west to east)
    "southern_quebec": {"south": 45.0, "west": -74.5, "north": 46.5, "east": -71.0},
    "central_quebec":  {"south": 46.0, "west": -76.0, "north": 49.0, "east": -66.0},
    "eastern_quebec":  {"south": 46.0, "west": -67.5, "north": 49.5, "east": -63.0},
    # Ontario — only the QC-adjacent eastern portion (Ottawa valley / east)
    "ontario_east":    {"south": 44.0, "west": -79.0, "north": 46.0, "east": -74.5},
    # Maritimes — New Brunswick + western Nova Scotia (border QC's east)
    "maritimes":       {"south": 45.0, "west": -67.5, "north": 48.0, "east": -63.0},
    # US immediate neighbours — northern New York, Vermont, Maine
    "us_border":       {"south": 43.5, "west": -75.0, "north": 45.1, "east": -69.0},
}

# Filter out SEPAQ-operated sites
SEPAQ_FILTERS = ["sepaq", "sépaq", "parc national", "national park"]

DB_PATH = "data/private.db"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_overpass_query(south: float, west: float, north: float, east: float) -> str:
    """Build Overpass query for camping sites in a bounding box."""
    # Query for both nodes and ways tagged with tourism=camp_site
    # Bbox format is (south, west, north, east)
    query = f"""[out:json][timeout:120];
(node["tourism"="camp_site"]({south},{west},{north},{east});way["tourism"="camp_site"]({south},{west},{north},{east}););
out center;"""
    return query


def fetch_osm_data() -> dict:
    """Query Overpass API across all regions and return combined JSON response."""
    all_elements = []

    for region_name, bbox in REGIONS.items():
        south, west, north, east = bbox["south"], bbox["west"], bbox["north"], bbox["east"]
        query = build_overpass_query(south, west, north, east)

        try:
            print(f"\nQuerying region: {region_name}")
            print(f"  Bbox: ({south}, {west}) to ({north}, {east})")

            # Overpass API expects POST with form-encoded query
            data_encoded = urllib.parse.urlencode({'data': query}).encode('utf-8')
            req = urllib.request.Request(
                OVERPASS_URL,
                data=data_encoded,
                headers={'User-Agent': 'CampingApp/1.0'}
            )

            with urllib.request.urlopen(req, timeout=180) as response:
                body = response.read().decode('utf-8')
                print(f"  ✓ Response received ({len(body)} bytes)")

                # Try to parse JSON
                try:
                    data = json.loads(body)
                    elements = data.get('elements', [])
                    print(f"  ✓ Parsed JSON: {len(elements)} elements")
                    all_elements.extend(elements)
                except json.JSONDecodeError as je:
                    print(f"  ✗ JSON parsing failed: {je}")
                    print(f"  Response body (first 300 chars):")
                    print(f"  {body[:300]}")

        except urllib.error.HTTPError as e:
            print(f"  ✗ HTTP {e.code}: {e.reason}")
            body = e.read().decode('utf-8', errors='ignore')
            if e.code == 504:
                print(f"  → Gateway timeout; region may have too many results")
            else:
                print(f"  Response: {body[:200]}")

        except urllib.error.URLError as e:
            print(f"  ✗ Network error: {e.reason}")

        except Exception as e:
            print(f"  ✗ Error: {type(e).__name__}: {e}")

    # Return combined result
    return {"elements": all_elements}


def is_sepaq_site(tags: dict) -> bool:
    """Check if campground is operated by SEPAQ."""
    name = tags.get('name', '').lower()
    operator = tags.get('operator', '').lower()

    for filter_str in SEPAQ_FILTERS:
        if filter_str in name or filter_str in operator:
            return True
    return False


def normalize_campground(element: dict, element_id: str) -> Optional[dict]:
    """Extract and normalize a campground record from an OSM element."""
    tags = element.get('tags', {})

    # Skip SEPAQ sites
    if is_sepaq_site(tags):
        return None

    # Extract coordinates
    if 'center' in element:
        lat = element['center']['lat']
        lon = element['center']['lon']
    elif 'lat' in element and 'lon' in element:
        lat = element['lat']
        lon = element['lon']
    else:
        return None  # No coordinates

    # Extract standard tags
    name = tags.get('name', '').strip()
    if not name:
        return None  # Skip unnamed sites

    # Website: try 'website' first, then 'contact:website'
    website = tags.get('website', '') or tags.get('contact:website', '')
    website = website.strip() if website else None
    # OSM often stores URLs without a scheme (e.g. "aucontedormant.com").
    # Add https:// so the browser treats it as an absolute external link rather
    # than a path relative to the app (which became http://localhost:5000/...).
    if website and not website.lower().startswith(("http://", "https://")):
        website = "https://" + website.lstrip("/")

    # Phone
    phone = tags.get('phone', '') or tags.get('contact:phone', '')
    phone = phone.strip() if phone else None

    # Operator
    operator = tags.get('operator', '').strip() or None

    # Address: try several tags, building from parts when needed
    if tags.get('addr:full'):
        address = tags['addr:full'].strip()
    elif tags.get('address'):
        address = tags['address'].strip()
    else:
        number = tags.get('addr:housenumber', '').strip()
        street = tags.get('addr:street', '').strip()
        city   = tags.get('addr:city', '').strip()
        parts  = []
        if number and street:
            parts.append(f"{number} {street}")
        elif street:
            parts.append(street)
        if city:
            parts.append(city)
        address = ', '.join(parts) or None
    if address == '':
        address = None

    # Infer region from tags or coordinates
    region = tags.get('addr:state', '') or tags.get('addr:province', '') or None

    return {
        'source': 'osm',
        'source_id': element_id,
        'name': name,
        'lat': lat,
        'lon': lon,
        'website': website,
        'phone': phone,
        'operator': operator,
        'address': address,
        'region': region,
        'tags_json': json.dumps(tags),
        'fetched_at': utcnow(),
    }


def process_osm_data(data: dict) -> list:
    """Extract and normalize campgrounds from OSM response."""
    campgrounds = []

    for element in data.get('elements', []):
        element_type = element.get('type')
        element_id = element.get('id')

        if element_type not in ('node', 'way'):
            continue

        # Build source_id as "node:12345" or "way:67890"
        source_id = f"{element_type}:{element_id}"

        normalized = normalize_campground(element, source_id)
        if normalized:
            campgrounds.append(normalized)

    return campgrounds


def print_summary(campgrounds: list) -> None:
    """Print summary statistics."""
    total = len(campgrounds)
    with_website = sum(1 for c in campgrounds if c['website'])
    with_phone = sum(1 for c in campgrounds if c['phone'])
    with_operator = sum(1 for c in campgrounds if c['operator'])

    # Region split (rough, from tags or None)
    regions = {}
    for c in campgrounds:
        region = c['region'] or 'Unknown'
        regions[region] = regions.get(region, 0) + 1

    print(f"\n{'=' * 60}")
    print(f"OSM Campground Fetch Summary")
    print(f"{'=' * 60}")
    print(f"Total campgrounds: {total}")
    print(f"  With website: {with_website} ({100*with_website/total:.1f}%)")
    print(f"  With phone: {with_phone} ({100*with_phone/total:.1f}%)")
    print(f"  With operator: {with_operator} ({100*with_operator/total:.1f}%)")

    print(f"\nRegional split:")
    for region in sorted(regions.keys()):
        count = regions[region]
        print(f"  {region}: {count}")

    print(f"\nSample records (first 3):")
    for i, c in enumerate(campgrounds[:3], 1):
        print(f"\n  {i}. {c['name']}")
        print(f"     Lat/Lon: {c['lat']}, {c['lon']}")
        if c['website']:
            print(f"     Website: {c['website']}")
        if c['phone']:
            print(f"     Phone: {c['phone']}")
        if c['operator']:
            print(f"     Operator: {c['operator']}")

    print(f"\n{'=' * 60}")


def save_to_database(campgrounds: list) -> None:
    """Upsert campgrounds into the database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Create table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS private_campgrounds (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source          TEXT NOT NULL,
                source_id       TEXT NOT NULL,
                name            TEXT NOT NULL,
                lat             REAL NOT NULL,
                lon             REAL NOT NULL,
                website         TEXT,
                phone           TEXT,
                operator        TEXT,
                address         TEXT,
                region          TEXT,
                tags_json       TEXT,
                fetched_at      TEXT,
                UNIQUE(source, source_id)
            )
        """)

        # Upsert records
        inserted = 0
        updated = 0

        for c in campgrounds:
            cursor.execute("""
                INSERT INTO private_campgrounds
                (source, source_id, name, lat, lon, website, phone, operator, address, region, tags_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    name=excluded.name,
                    lat=excluded.lat,
                    lon=excluded.lon,
                    website=COALESCE(excluded.website, website),
                    phone=COALESCE(excluded.phone, phone),
                    operator=COALESCE(excluded.operator, operator),
                    address=COALESCE(excluded.address, address),
                    region=COALESCE(excluded.region, region),
                    tags_json=excluded.tags_json,
                    fetched_at=excluded.fetched_at
            """, (
                c['source'], c['source_id'], c['name'], c['lat'], c['lon'],
                c['website'], c['phone'], c['operator'], c['address'],
                c['region'], c['tags_json'], c['fetched_at']
            ))

        conn.commit()

        # Count result
        cursor.execute("SELECT COUNT(*) FROM private_campgrounds")
        total = cursor.fetchone()[0]

        conn.close()
        print(f"✓ Database saved: {total} campgrounds in private_campgrounds table")

    except Exception as e:
        print(f"✗ Database error: {type(e).__name__}: {e}")
        sys.exit(1)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch private campgrounds from OpenStreetMap"
    )
    parser.add_argument('--save', action='store_true',
                       help='Save results to database')
    parser.add_argument('--json', action='store_true',
                       help='Output raw JSON to stdout')

    args = parser.parse_args()

    # Fetch data
    osm_data = fetch_osm_data()

    # Process
    campgrounds = process_osm_data(osm_data)

    # Print summary
    if not args.json:
        print_summary(campgrounds)

    # Output JSON if requested
    if args.json:
        print(json.dumps(campgrounds, indent=2))

    # Save to database
    if args.save:
        save_to_database(campgrounds)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
recover_osm_tags.py — Promote OSM tag data into private_campgrounds columns.

Some campgrounds have phone / website / address buried in tags_json that was
not promoted to the top-level columns during the original OSM import.  This
script recovers that data without hitting any external API.

Rules:
  - Never overwrites an existing non-empty value.
  - Builds address from addr:housenumber + addr:street + addr:city parts.
  - Logs every change made.

Usage:
    python recover_osm_tags.py          # dry-run (prints what would change)
    python recover_osm_tags.py --apply  # writes changes to the DB
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "private.db"

# Tag keys to try for each field, in priority order.
PHONE_KEYS  = ["phone", "contact:phone"]
WEB_KEYS    = ["website", "contact:website", "url"]
ADDR_NUMBER = ["addr:housenumber"]
ADDR_STREET = ["addr:street", "contact:street"]
ADDR_CITY   = ["addr:city", "contact:city"]
ADDR_PROV   = ["addr:province", "addr:state"]


def first(tags: dict, keys: list[str]) -> str:
    for k in keys:
        v = tags.get(k, "").strip()
        if v:
            return v
    return ""


def build_address(tags: dict) -> str:
    number = first(tags, ADDR_NUMBER)
    street = first(tags, ADDR_STREET)
    city   = first(tags, ADDR_CITY)
    prov   = first(tags, ADDR_PROV)

    parts = []
    if number and street:
        parts.append(f"{number} {street}")
    elif street:
        parts.append(street)
    if city:
        parts.append(city)
    if prov:
        parts.append(prov)
    return ", ".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Recover OSM tag data into top-level columns.")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = parser.parse_args()

    dry_run = not args.apply

    if dry_run:
        print("DRY-RUN mode — pass --apply to write changes.\n")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        "SELECT id, name, phone, website, address, tags_json "
        "FROM private_campgrounds WHERE tags_json IS NOT NULL"
    )
    rows = cur.fetchall()

    updates = []  # (id, field, old_value, new_value)

    for row in rows:
        row_id = row["id"]
        tags_raw = row["tags_json"]
        try:
            tags = json.loads(tags_raw)
        except (json.JSONDecodeError, TypeError):
            continue

        changes = {}

        if not row["phone"]:
            v = first(tags, PHONE_KEYS)
            if v:
                changes["phone"] = v

        if not row["website"]:
            v = first(tags, WEB_KEYS)
            # Skip internal OSM source URLs
            if v and "openstreetmap" not in v and "source:license" not in v:
                changes["website"] = v

        if not row["address"]:
            v = build_address(tags)
            if v:
                changes["address"] = v

        for field, new_val in changes.items():
            updates.append((row_id, row["name"], field, row[field], new_val))

    if not updates:
        print("Nothing to recover — all tag data already in top-level columns.")
        conn.close()
        return

    print(f"{'Would update' if dry_run else 'Updating'} {len(updates)} field(s) across rows:\n")
    for row_id, name, field, old_val, new_val in updates:
        print(f"  id={row_id} [{name}]  {field}: {repr(old_val)} → {repr(new_val)}")

    if not dry_run:
        # Group by id for efficient updates
        by_id: dict[int, dict] = {}
        for row_id, name, field, old_val, new_val in updates:
            by_id.setdefault(row_id, {})[field] = new_val

        for row_id, changes in by_id.items():
            set_clause = ", ".join(f"{f} = ?" for f in changes)
            values = list(changes.values()) + [row_id]
            cur.execute(f"UPDATE private_campgrounds SET {set_clause} WHERE id = ?", values)

        conn.commit()
        print(f"\nDone — {len(updates)} field(s) updated.")
    else:
        print(f"\nRe-run with --apply to commit these changes.")

    conn.close()


if __name__ == "__main__":
    main()

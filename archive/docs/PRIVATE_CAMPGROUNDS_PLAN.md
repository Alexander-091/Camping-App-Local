# Private Campgrounds Integration — Project Plan

**Date:** June 3, 2026
**Goal:** Overlay non-SEPAQ (private) campgrounds onto the existing Camping App map, alongside the current SEPAQ parks.
**Status:** Planning complete — ready to start Phase 0.

---

## Decisions Already Made

- **Phase-one scope:** Map pins first. Get private campgrounds onto the map with locations + booking links. Per-day availability is a *later, separate* effort, not part of this plan's core.
- **Geographic scope:** Quebec + immediate neighbours only — Ontario, New Brunswick, New York, Vermont, Maine (plus whatever NH / NS / MA falls in the bounding box).
- **Sourcing:** Comfortable with the same scrape-what's-needed approach used for SEPAQ. But for the *list itself* we deliberately use open data (OpenStreetMap), because it's cleaner and zero-risk — scraping muscle is reserved for the optional availability phase.

---

## Core Architectural Insight

There is **no single API for "private campgrounds in Quebec."** Private grounds are hundreds of independent operators. So the two halves of this problem have very different difficulty:

1. **List + locations** — *solved problem.* OpenStreetMap (via the Overpass API) tags every campground as `tourism=camp_site` with name, lat/lon, and often website/phone/operator/fee tags. Free, no key, no Cloudflare, ODbL-licensed (needs attribution). This is the source for Phase 1.
2. **Per-day availability** — *hard, fragmented.* No shared source. Only viable platform-by-platform (Campspot, Hipcamp, Camping Québec each expose structured JSON behind search). Deferred to Phase 5, opt-in per platform.

**Keep the two systems decoupled.** Private campgrounds get their own table and their own map layer. The existing SEPAQ scraper, DB hierarchy (`parks → sectors → boucles → sites`), and live-availability logic are **not touched**.

---

## Environment Note (important for the new chat)

The Cowork sandbox **cannot reach Overpass / OSM hosts** — its network allowlist returns 403/000 for `overpass-api.de`, `nominatim.openstreetmap.org`, etc., and the web-fetch tool won't return the raw API JSON. **The OSM data fetch must run on the user's local machine** (which has normal internet), exactly like the SEPAQ scraper is run locally. Build the fetch script to run locally; the assistant can write/verify the parser logic but cannot pull live OSM data itself.

---

## Pre-req / Blocker: Database is Malformed

As of June 3, 2026, `data/sepaq.db` **fails `PRAGMA integrity_check`** ("database disk image is malformed") — `sqlite_master` itself errors out. Nothing can be written to it until this is fixed. There are existing `repair_db.py`, `migrate_db.py`, `setup_fresh_db.py` scripts and an `old.data/` folder, so this has been an ongoing issue. **Phase 0 must resolve this first.**

---

## Phases

### Phase 0 — Fix the database (BLOCKER)
**Goal:** A healthy `sepaq.db` that passes integrity check.
- Run `PRAGMA integrity_check` to confirm the corruption.
- Attempt recovery via `.recover` (SQLite CLI) or the existing `repair_db.py`.
- If unrecoverable, rebuild with `setup_fresh_db.py` and re-run the SEPAQ scraper (needs fresh cookies — see `get_cookie.py`).
- Verify all SEPAQ tables and row counts are restored (expected ~15 parks, ~60 sectors, ~135 boucles, ~4,042 sites).
- **Exit criterion:** integrity check passes; the app loads SEPAQ data normally.

### Phase 1 — Fetch OSM campground data (runs locally)
**Goal:** A clean list of private campgrounds with coordinates.
- Write `fetch_osm_campgrounds.py` (stdlib only — `urllib`, `json`, `sqlite3`; no pip deps).
- Query Overpass for `node` + `way["tourism"="camp_site"]` across the bounding box covering QC + neighbours (approx `south=42.5, west=-83.5, north=52.0, east=-64.0`).
- Use `out center tags;` so ways get a representative lat/lon.
- Normalize each result: `name`, `lat`, `lon`, `website` (from `website` or `contact:website`), `phone`, `operator`, `address`, raw `tags_json`.
- **Exclude SEPAQ-operated sites** to avoid duplicating existing parks (filter on `operator`/name containing "Sépaq"/"SEPAQ").
- **Exit criterion:** script runs locally and prints total count, # named, # with website, and a rough region split. User eyeballs sample quality **before** any UI work.

### Phase 2 — Database schema for private campgrounds
**Goal:** A place to store the list, decoupled from SEPAQ tables.
- New table `private_campgrounds`: `id, source ('osm'), source_id, name, lat, lon, website, phone, operator, address, region, tags_json, fetched_at`.
- Add a `UNIQUE(source, source_id)` constraint so re-running the fetch upserts rather than duplicates.
- Loader logic in `fetch_osm_campgrounds.py` writes/upserts into this table.
- **Exit criterion:** table populated; row count matches the Phase 1 fetch.

### Phase 3 — Backend API endpoint
**Goal:** Serve private campgrounds to the frontend.
- New Flask route in `app/app.py`, e.g. `GET /api/private-campgrounds?bbox=south,west,north,east` (bbox optional; default returns all).
- Returns JSON array of pins with name, lat/lon, website, phone, operator.
- **Exit criterion:** endpoint returns expected count; spot-check a few records against OSM.

### Phase 4 — Frontend map layer
**Goal:** Show private campgrounds on the map, visually distinct from SEPAQ.
- Add a **second Leaflet marker layer** with a distinct pin colour/icon.
- Add a **layer toggle** (SEPAQ / Private / both) so the two are visually separable.
- Popups show name + phone + a "Visit / book site" link out to the campground's own website (from OSM `website` tag).
- Add **OSM attribution** to the map (ODbL requirement).
- **Exit criterion:** both layers render; toggle works; popups link out correctly; attribution present.

### Phase 5 — (Optional, later) Availability
**Goal:** Per-day availability for private grounds where feasible.
- **Not** a single source — tackle platform-by-platform, only for grounds the user actually cares about.
- Candidate sources: Campspot, Hipcamp, Camping Québec (each has structured JSON behind search).
- Likely a per-platform scraper module + a `private_availability` table keyed by campground + date.
- Reuse the SEPAQ cookie/curl_cffi patterns where a platform has bot protection.
- **Decision gate:** only start once Phase 4 ships and the user identifies which platforms their target grounds use.

---

## Suggested First-Chat Prompt

> Continuing the Camping App. We're adding private (non-SEPAQ) campgrounds as a second map layer. Read `PRIVATE_CAMPGROUNDS_PLAN.md` for the full plan, and `PROJECT_SUMMARY.md` + `SESSION_SUMMARY.md` for app context.
>
> Start with **Phase 0**: the database `data/sepaq.db` is currently malformed (fails integrity check). Diagnose and fix/rebuild it before anything else. Then move to Phase 1 (write the local OSM fetch script) — note the sandbox can't reach OSM, so that script is run on my machine.
>
> Don't write or change code without asking me first.

---

## Files Referenced
- `app/app.py` — Flask backend (add endpoint in Phase 3)
- `app/templates/index.html` — frontend map (add layer in Phase 4)
- `data/sepaq.db` — database (fix in Phase 0, extend in Phase 2)
- `repair_db.py`, `migrate_db.py`, `setup_fresh_db.py` — existing DB tools
- `scraper/get_cookie.py`, `scraper/scraper.py` — SEPAQ refresh (if Phase 0 needs a rebuild)
- **New:** `fetch_osm_campgrounds.py` — OSM fetch + loader (Phase 1/2)

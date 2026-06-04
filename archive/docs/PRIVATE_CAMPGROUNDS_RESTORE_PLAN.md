# Private Campgrounds — Restore Plan

**Date:** 2026-06-03
**Goal:** Bring back the private (non-SEPAQ) campgrounds map layer that was lost in the `app.py` overwrite, and make it **off by default** (user opts in via the front-end toggle).

---

## What already exists (survived the overwrite)

- **`fetch_osm_campgrounds.py`** (project root, 347 lines) — fetches campgrounds
  from OpenStreetMap's Overpass API across 7 regional bounding boxes (southern/
  northern/eastern Quebec, Ontario east/west, Maritimes, US northeast),
  normalizes each record, filters out SEPAQ-operated sites, and with `--save`
  creates a `private_campgrounds` table and upserts rows.
  - Table schema it creates:
    `private_campgrounds(id, source, source_id, name, lat, lon, website, phone,
    operator, address, region, tags_json, fetched_at, UNIQUE(source, source_id))`
- **Frontend (`app/templates/index.html`)** — the whole layer is still wired:
  - `showPrivateCampgrounds` toggle checkbox (line ~457) — currently `checked`.
  - `privateCampgroundsLayer` Leaflet layer group, orange teardrop markers,
    popups (name / phone / operator / "Visit website" link).
  - `loadPrivateCampgrounds()` → `fetch("/api/private-campgrounds")`.
  - `updatePrivateCampgroundsMarkers()` and `updatePrivateCampgroundsVisibility()`.
  - OSM/ODbL attribution already on the map tile layer.

## What is missing / wrong

1. **Backend route gone.** `/api/private-campgrounds` is NOT in the current
   `app/app.py` (dropped in the overwrite). The frontend calls it → 404 → no pins.
2. **No data.** The rebuilt `sepaq.db` was a fresh SEPAQ-only scrape, so the
   `private_campgrounds` table almost certainly doesn't exist / is empty.
3. **Toggle defaults ON.** You want it **OFF by default** — user toggles it on.
4. **Environment:** the OSM fetch must run on your machine (Overpass is not
   reachable from the Cowork sandbox), exactly like the SEPAQ scraper.

---

## The Plan (phased, no code written yet)

### Phase 0 — Verify current state (read-only, on your machine)
- Confirm `/api/private-campgrounds` is absent from `app.py` (it is).
- Check whether `private_campgrounds` exists in `data/sepaq.db` and its row count.
- **Done when:** we know exactly whether we need to (re)fetch data or just
  re-add the endpoint.

### Phase 1 — Fetch the OSM data (runs locally) → SEPARATE DB
- **Decision (made):** store private campgrounds in their own database,
  `data/private.db`, NOT in `sepaq.db`. Keeps SEPAQ data and the private-OSM data
  fully decoupled (consistent with the photos.db split), so re-fetching one can
  never touch the other.
- `fetch_osm_campgrounds.py` currently has `DB_PATH = "data/sepaq.db"` and writes
  the `private_campgrounds` table there. **Change needed:** point it at
  `data/private.db` instead (one constant). The table schema it creates is
  unchanged:
  `private_campgrounds(id, source, source_id, name, lat, lon, website, phone,
  operator, address, region, tags_json, fetched_at, UNIQUE(source, source_id))`.
- Run `python fetch_osm_campgrounds.py` first (no `--save`) to eyeball the count
  and a sample, confirming Overpass still returns good data.
- Then `python fetch_osm_campgrounds.py --save` to create/populate
  `data/private.db`.
- **Done when:** `data/private.db` exists and is populated; row count matches the
  fetch summary; a few records spot-checked against OSM.

### Phase 2 — Re-add the backend endpoint (reads from `private.db`)
- Add a `get_private_db()` helper in `app/app.py` that opens `data/private.db`
  and returns `None` if the file doesn't exist (mirrors the `get_photos_db()`
  pattern just added for photos).
- Re-create `GET /api/private-campgrounds` reading from `private.db`, returning
  the same JSON shape the frontend already consumes:
  `[{ id, name, lat, lon, website, phone, operator, address, region }, ...]`.
- Must degrade gracefully: if `private.db` (or the table) doesn't exist, return
  `[]` — so the app never errors when data hasn't been fetched yet.
- Optional `?bbox=south,west,north,east` filter (the original supported it).
- **Done when:** the endpoint returns the expected count; frontend
  `loadPrivateCampgrounds()` succeeds (no 404).

### Phase 3 — Make the toggle OFF by default
- Remove the `checked` attribute on the `showPrivateCampgrounds` checkbox so it
  starts unchecked.
- On map init, the layer should start **hidden**; only added to the map when the
  user ticks the box. `updatePrivateCampgroundsVisibility()` already handles the
  add/remove based on the checkbox state — the only change is the default.
- Verify the layer isn't force-added on load (the init currently calls
  `updatePrivateCampgroundsVisibility()`, which will now keep it hidden since the
  box is unchecked — good).
- **Done when:** on a fresh page load the private pins are NOT shown; ticking the
  box shows them; unticking hides them.

### Phase 4 — Verify end-to-end
- Fresh load: only SEPAQ markers visible, private toggle off.
- Tick toggle: orange private pins appear; popups show name + website/phone.
- Untick: pins disappear.
- Spot-check a couple of private pins against OSM/their website.
- Confirm SEPAQ functionality (parks, sectors, live availability, photos) is
  untouched.

---

## Decisions
1. **Data location:** ✅ DECIDED — separate `data/private.db` (not `sepaq.db`).
2. **Default toggle:** ✅ DECIDED — OFF by default; user opts in.
3. **Geographic scope:** ✅ DECIDED — Quebec + immediate neighbours only
   (Ontario, New Brunswick, New York, Vermont, Maine + whatever NH/NS/MA falls in
   the box). The fetch script's `REGIONS` dict currently includes broader Ontario
   and US-northeast boxes — trim/adjust to the immediate-neighbour bounding box
   (~ south=42.5, west=-83.5, north=52.0, east=-64.0, or the equivalent regional
   boxes) so we don't pull campgrounds far outside the target area.
4. **Marker/popup style:** ✅ DECIDED — keep as-is: orange teardrop marker;
   popup shows name / phone / operator / "Visit website" link. No change.

## Files involved
- `fetch_osm_campgrounds.py` — OSM fetch + `--save` loader. **Change:** repoint
  `DB_PATH` from `data/sepaq.db` to `data/private.db` (Phase 1). Runs locally.
- `app/app.py` — add `get_private_db()` + **re-add** `/api/private-campgrounds`
  reading from `private.db` (Phase 2).
- `app/templates/index.html` — flip toggle default to OFF (Phase 3); layer code
  already present.
- `data/private.db` — NEW separate database holding `private_campgrounds` (Phase 1).

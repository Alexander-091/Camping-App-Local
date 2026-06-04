# Map Redesign — Project Plan

**Goal:** Replace the static JPG/GIF region maps with a hybrid, interactive drill-down that mirrors SEPAQ's own flow (Park → Sector → Boucle → individual campsite), with the map on the left and availability cards on the right, and bidirectional hover/click highlighting between the two.

This document is self-contained: it assumes the executor has **no prior context**. Read "Background" first, then work the steps in order.

---

## Decision already made

**Hybrid map model:**

- **Park + Sector level** → keep the existing **geographic Leaflet map** (real lat/lon markers). Already works.
- **Boucle + individual-site level** → **interactive overlay on SEPAQ's schematic GIF**: colour-coded dots positioned over the schematic image at the x/y percentages SEPAQ provides.

A true GPS map at the site level was rejected — SEPAQ exposes no per-site GPS coordinates (only schematic %), so it would require manually geolocating ~12,000 sites.

---

## Background — how the app works today

**Stack:** Flask backend (`app/app.py`), single-page vanilla-JS frontend (`app/templates/index.html`), SQLite DB (`data/sepaq.db`), scraper (`scraper/scraper.py`). See `PROJECT_SUMMARY.md` for full detail.

**Data hierarchy:** `parks → sectors → boucles → sites`. DB currently holds 15 parks, 60 sectors, 405 boucles, 12,126 sites (last scraped 2026-06-02).

**How SEPAQ exposes site positions (critical):** SEPAQ's per-boucle "maps" are **schematic GIFs**, e.g. `https://imagescloud.s3-accelerate.amazonaws.com/images/maps/aig/aig_001.gif`. On the boucle page, each campsite is an `<a id="unit_NNNNNN" data-couleur="green|yellow|red" data-url="…" data-prix="…" data-surnom="…">` wrapped in a positioned list item:

```html
<li style="left:44.81%; top:18.75%">
  <a id="unit_101007" data-url="/en/reservation/camping/.../le-refuge-103"
     data-couleur="green" data-surnom="Campsite with 3 services"
     data-prix="Starting at <strong>$57.60</strong>/night">
</li>
```

`data-couleur`: green = available, yellow = partial, red = full. The `left`/`top` percentages are the dot positions over the GIF.

**What already exists in the code (do NOT rebuild these):**

- `openSectorRangeAvail()` in `index.html` already renders **map-left / cards-right** layout.
- `renderMapPanel()` already draws the schematic `<img>` and overlays `.map-dot` elements positioned by `x_pct`/`y_pct`, coloured by availability.
- `attachMapHighlighting()` already implements **bidirectional hover** (dot↔card, keyed on matching `data-unit-id`), including scroll-into-view.
- `switchBoucle()` already provides boucle tab switching on the left panel.
- The live endpoint `GET /api/sectors/<id>/live-sites?from=&to=` and its `_SiteParser` class in `app.py` **already parse `x_pct`/`y_pct`, colour, price, and site type** from the live boucle HTML in real time.

**Why it still looks like "just JPGs" — the actual gaps:**

1. **DB schema gap.** The `sites` table is *missing* the `x_pct` and `y_pct` columns. The scraper's migration to add them (`ALTER TABLE sites ADD COLUMN x_pct/y_pct`) never ran on the current DB. Verify with: `PRAGMA table_info(sites)`.
2. **Missing map URLs.** Only **135 of 405 boucles** have a stored `map_url`.
3. **List view vs map view.** SEPAQ often serves a list view with no positioned `<li>`, so the live fetch returns `x_pct = null` and the frontend's `s.x_pct != null` filter renders zero dots — leaving a bare image.

So the architecture is correct; the **data plumbing** is incomplete.

---

## Step 1 — Fix the data foundation (the real blocker)

Everything else is cosmetic until site positions reliably exist.

1. **Add the missing columns.** Ensure the migration runs so `sites` has `x_pct REAL` and `y_pct REAL`. Confirm via `PRAGMA table_info(sites)`.
2. **Make the scraper request the map/plan view**, not the list view, so each boucle page contains the positioned `<li style="left…top…">` markers. Investigate how SEPAQ toggles the two (URL param, query string, or a view-mode cookie) and force the positioned variant.
3. **Backfill `map_url`** for the ~270 boucles currently missing one (re-run discovery; `extract_map_url()` already exists in the scraper).
4. **Persist per-site `x_pct`/`y_pct`** (and current `colour`) when scraping, so positions survive without a live fetch.
5. **Refresh cookies first** (`python scraper/get_cookie.py`) — the scrape and live endpoint both fail silently on stale Cloudflare cookies.

**Done when:** a representative park (e.g. Oka) has `map_url` on every boucle and non-null `x_pct`/`y_pct` on its sites in the DB.

---

## Step 2 — Make the schematic overlay render reliably

The pieces exist in `renderMapPanel()`; this is hardening, not rebuilding.

1. Draw a colour-coded dot (green/yellow/red) for **every** site that has a position, not only available ones.
2. Add a graceful fallback for boucles that genuinely have **no** schematic map (`map_url` empty): show the cards-only list with a small "map not available" note.
3. Make dots **clickable** through to the SEPAQ booking page (`data-url`), in addition to hoverable.
4. Add a small **colour legend** (Available / Partial / Full).
5. Confirm dot sizing/contrast is legible over the GIF; add subtle border/shadow.

**Done when:** opening a sector for a date range shows the schematic with correctly coloured, hoverable, clickable dots for all positioned sites.

---

## Step 3 — Wire the hybrid drill-down end to end

The left panel's map *type* should change as you descend, matching SEPAQ.

1. **Park level:** existing geographic Leaflet map (unchanged).
2. **Park → Sector:** clicking a park lists its sectors (existing `loadParkSectors`).
3. **Sector → Boucle:** clicking a sector switches the left panel to the **schematic overlay** with boucle tabs (`switchBoucle` scaffolding exists).
4. Ensure the **breadcrumb** (Park › Sector › Boucle) stays consistent through the transitions.
5. Verify the geo map and schematic overlay don't fight over the same DOM container — clean teardown/rebuild on each level change.

**Done when:** a user can click from the national map down to a single boucle's schematic without dead ends or stale panels.

---

## Step 4 — Finish bidirectional highlight + interaction

`attachMapHighlighting()` already links dot↔card both ways; make it robust.

1. Re-attach highlighting after **every** re-render (boucle switch, filter change) — verify it survives.
2. Add **click-to-select** (sticky highlight) in addition to hover, on both map and card.
3. Sync **scroll**: selecting a dot scrolls its card into view and vice-versa (card→dot already scrolls; verify dot→card).
4. Make the highlight visually obvious on **both** sides simultaneously (e.g. enlarge dot + outline card).

**Done when:** hovering or clicking either a dot or a card visibly highlights its counterpart, reliably, after any re-render.

---

## Step 5 — Verify against SEPAQ

1. Pick a park you know well (Oka) and a real date range.
2. Open the same boucle on sepaq.com and in the app side by side.
3. Confirm dot **positions** and **availability colours** match SEPAQ for those dates.
4. Confirm graceful behaviour for: no-map boucles, fully-booked boucles, and stale cookies (clear error, not a blank panel).
5. Spot-check 2–3 other parks of different structure (a standard single-boucle park and a multi-boucle one like Oka/Frontenac).

**Done when:** the app's site-level map visually agrees with SEPAQ for multiple parks.

---

## Key files & symbols (quick reference)

| File | What to touch |
|------|---------------|
| `scraper/scraper.py` | `discover_units()`, `_SiteParser` (`_li_x/_li_y`), `extract_map_url()`, `save_sites()`, migrations adding `x_pct/y_pct`; force map view |
| `app/app.py` | `_SiteParser` (already parses x/y/colour), `/api/sectors/<id>/live-sites`, `_parse_boucle_page()` |
| `app/templates/index.html` | `openSectorRangeAvail()`, `renderMapPanel()`, `switchBoucle()`, `attachMapHighlighting()`, `.map-dot` / `.boucle-map-img` CSS |
| `data/sepaq.db` | Add `sites.x_pct`, `sites.y_pct`; backfill `boucles.map_url` |

## Effort note

Step 1 (scraper/DB) is the bulk of the work and the gating item. Steps 2–4 are mostly hardening code that already exists. Step 5 is validation.

## Prerequisites before starting

```
pip install flask curl_cffi requests playwright
playwright install chromium
cd scraper && python get_cookie.py   # refresh Cloudflare + session cookies
```

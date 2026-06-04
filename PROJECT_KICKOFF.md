# Camp Planner — Quick Kickoff (read me first)

A 2-minute orientation for any new chat picking up this project. For full detail
see **CAMP_PLANNER_PRD.md**.

## What this is
**Milosevic Camp Planner** — a local Flask + vanilla-JS/Leaflet + SQLite app for
finding & booking SEPAQ (Quebec parks) campsites, with live per-site
availability on schematic maps, weather, photos, and an optional private
(OpenStreetMap) campground layer.

Hierarchy: **Park → Sector → Boucle → Site**.

## How to run
```
python app/app.py            # serves http://localhost:5000
```
Local SEPAQ scraping/cookies must run on the user's machine (network reaches
SEPAQ; the Cowork sandbox does NOT).

## The one thing to understand: SEPAQ scraping
- No public API. Scrape sepaq.com behind Cloudflare.
- Needs `cf_clearance` cookie (from a real browser) + **curl_cffi
  `impersonate="chrome120"`**. Plain urllib/requests = 403.
- Cookies expire in ~30–60 min → app shows a banner; refresh via
  `scraper/get_cookie.py`.
- **Live availability** per sector+dates = (per boucle, own session):
  warm-up GET park page → POST dates to `/en/reservation/search` → GET boucle
  page → parse `<a id="unit_*" data-couleur=...>`. Colours: green=avail,
  yellow=partial, red=full, blue=TBD, empty=unknown.

## Databases (3, intentionally separate)
- `data/sepaq.db` — parks/sectors/boucles/sites + cached availability + weather.
  Sites carry `x_pct/y_pct` (dot position on schematic map).
- `data/photos.db` — `site_photos(unit_id, photo_url, photo_data BLOB)`.
- `data/private.db` — `private_campgrounds(...)` from OSM/Overpass.

## Key files
- `app/app.py` — Flask backend (all routes + live-fetch logic).
- `app/templates/index.html` — entire single-page frontend (Map/Browse/Search).
- `scraper/scraper.py` — SEPAQ scraper; `scraper/get_cookie.py` — cookie capture.
- `scrape_photos.py` — downloads site photos → photos.db (resumable).
- `fetch_osm_campgrounds.py` — fetches private campgrounds → private.db (run with `--save`).

## Done recently (2026-06-03 session)
- Restored after an overwrite: rebuilt understanding, fixed live availability
  (curl_cffi warm-up sequence — was returning empty/grey), cookie-expiry banner
  + `/api/cookie-status`, park-level Leaflet map, photo serving from photos.db
  + photo scraper, private campgrounds restored into separate private.db with
  toggle **off by default**, fixed private website links (absolute https).

## Current open items
- **Blue dot meaning** unconfirmed (how to render / whether it's "available").
- **Cookie automation** — manual refresh every ~30–60 min is the main pain.
- Photos ~94%+ done; rerun `scrape_photos.py` (refresh cookies) to top up.
- Private campgrounds: verify pins/links on the Map tab (toggle on).

## Future direction
Rebuild as a multi-user **web app** (see PRD §6/§8): server-side scraping worker
with automated session acquisition, Postgres + object storage, React/Vue + Leaflet.

## Working norms (user preferences)
- Be concise; don't over-explain.
- **Don't write/modify code without asking first.**
- Don't assume — ask when unclear; flag if a task will be token-heavy.
- Note: the Cowork sandbox sees a STALE copy of the files and CANNOT reach
  SEPAQ/Overpass — so SEPAQ/OSM fetches and DB checks against live data must run
  on the user's machine. Claude CAN drive the user's Chrome (Claude-in-Chrome)
  to view SEPAQ directly.

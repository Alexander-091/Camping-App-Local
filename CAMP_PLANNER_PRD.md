# Milosevic Camp Planner — Functional PRD

**Version:** 1.1
**Date:** 2026-06-05
**Changelog:** v1.1 — Private campground enrichment pipeline; Camping Québec scraper; two-tier pin display.
**Purpose:** Functional specification of the existing desktop app, written so it
can be rebuilt as a production web app. This describes *what the app does and
how*, including the non-obvious mechanics (live SEPAQ availability, schematic
map overlays, cookie handling) that are the hard-won core of the product.

---

## 1. Product Overview

### 1.1 What it is
A trip-planning tool for finding and booking campsites in Quebec's **SEPAQ**
provincial parks, with an overlay of **private (non-SEPAQ) campgrounds** for
broader options. It shows real-time per-site availability on SEPAQ's own
schematic maps, weather, and direct booking links — things SEPAQ's own site
makes tedious to compare across parks and dates.

### 1.2 Who it's for
A camper (currently single-user, local) planning trips across multiple SEPAQ
parks who wants to compare availability and conditions at a glance instead of
clicking through SEPAQ park-by-park, date-by-date.

### 1.3 Core value proposition
- **One view across all parks** — availability, price, and weather side by side.
- **Live per-site availability** on the actual schematic park maps (which sites,
  not just "how many"), colour-coded, clickable straight to SEPAQ booking.
- **Private campgrounds** as an optional second layer for non-SEPAQ options.

### 1.4 Current state / constraints
- Runs locally: a **Flask** backend + a single-page **vanilla-JS + Leaflet**
  frontend, backed by **SQLite**.
- SEPAQ has no public API. All SEPAQ data is obtained by **scraping** with
  Cloudflare-bypass cookies and TLS impersonation (`curl_cffi`). This is the
  central technical reality the rebuild must preserve.
- Single-user, no auth. A web-app rebuild would add multi-user, hosting, and a
  server-side scraping/caching service.

---

## 2. Data Sources & The Scraping Reality

This section is the most important for a rebuild — the app lives or dies on it.

### 2.1 SEPAQ (primary)
- **No public API.** Data comes from scraping `https://www.sepaq.com`.
- **Cloudflare protection.** Requests need a valid `cf_clearance` cookie
  (plus `__cf_bm`, `__cflb`) captured from a real browser session, AND TLS
  fingerprint impersonation (`curl_cffi` with `impersonate="chrome120"`).
  Plain `urllib`/`requests` get **403'd**.
- **Cookie lifetime is short** (~30–60 min). The app must detect expiry (401/403)
  and prompt a refresh; it cannot keep a session alive indefinitely.
- **Hierarchy:** `Park → Sector → Boucle (loop) → Site (unit)`.
- **Two kinds of availability data:**
  1. *Scraped/cached* (the `availability` / `range_availability` tables) — used
     for the park-list "X dates open" summaries. As fresh as the last scrape.
  2. *Live* — fetched on demand for a specific sector + date range, parsed from
     the rendered boucle HTML. This is what powers the interactive map.

### 2.2 Live availability mechanism (critical detail)
To get per-site availability for a date range, the sequence MUST be (per boucle,
each in its own session to avoid races):
1. **Warm up**: `GET /en/reservation/camping/<park_slug>` to establish a
   JSESSIONID in the booking context. *Skipping this returns a page with no
   availability colours.*
2. **Set dates**: `POST /en/reservation/search` with `arrivalDate`,
   `departureDate`, `booking.arrivalDate`, `booking.departureDate`,
   `booking.adults`.
3. **Fetch**: `GET <boucle_url>` (the full path including the boucle segment,
   e.g. `.../<sector>/<boucle>/<site>`).
4. **Parse** anchors `<a id="unit_NNN" data-couleur="..." data-url="..."
   data-prix="..." data-surnom="...">`:
   - `data-couleur`: `green` = available, `yellow` = partial, `red` = full,
     `blue` = a distinct status (semantics TBD — see Open Questions),
     empty/missing = unknown.
   - `unit_id` = id minus the `unit_` prefix.
   - Site position for the map comes from stored `x_pct`/`y_pct` (scraped
     separately), merged by `unit_id`.

### 2.3 Site photos
- Not in the page HTML by the standard path; the working site-detail URL
  (`.../<sector>/<boucle>/<site>`) embeds gallery image URLs of the form
  `imagescloud.s3-accelerate.amazonaws.com/images/galleriemedia/YY/MM/DD/<name>_<id>.jpg`.
- Photos are downloaded and stored as **BLOBs in a separate `photos.db`**, keyed
  by `unit_id`, so the main DB stays lean.

### 2.4 Private campgrounds (secondary)
- Primary source: **OpenStreetMap** via the **Overpass API** (`tourism=camp_site`),
  free, ODbL-licensed (requires attribution).
- Scope: Quebec + immediate neighbours (Ontario-east, NB/NS, NY/VT/ME).
- SEPAQ-operated sites filtered out. Stored in a **separate `private.db`**.
- Secondary source: **Camping Québec** (`campingquebec.com`) — the provincial
  campground association. 826 member campgrounds scraped via their WordPress REST
  API + static HTML detail pages (`scrape_campingquebec.py`). No API key required;
  contact data (phone, address, website, Google Maps URL) is in server-rendered
  HTML. Used to enrich OSM records and add new campgrounds not in OSM.
- **Enrichment pipeline** (run order):
  1. `fetch_osm_campgrounds.py --save` — fetch/refresh OSM data (upsert preserves
     existing enriched values via `COALESCE`).
  2. `recover_osm_tags.py --apply` — promote contact fields already in `tags_json`
     to top-level columns (free, no network calls).
  3. `migrate_private.py` — adds `maps_url`, `photo_url`, `enriched_at`,
     `cq_scraped_at` columns (safe to re-run).
  4. `scrape_campingquebec.py --apply` — scrapes Camping Québec; fuzzy name +
     proximity match against existing rows; inserts unmatched campgrounds as new.
- **Current coverage** (as of 2026-06-05): 2,797 total campgrounds; 948 with
  phone, 836 with website, 1,119 with address, 818 with Google Maps URL.
- Per-day availability for private grounds is OUT OF SCOPE (no shared source).

### 2.5 Weather
- Open-Meteo API (free, no key) per park lat/lon, cached in the DB.
- Drives a per-park weather label on the map and a derived "mosquito activity"
  heuristic.

---

## 3. Functional Requirements

### 3.1 Map view (geographic)
- Leaflet map of Quebec with a marker per SEPAQ park.
- Marker colour reflects availability density (green = lots, light-green = some,
  grey = none) over the next ~90 days from cached data.
- Optional per-park weather label on the marker.
- **Private campgrounds layer**: two independent toggles, both OFF by default:
  - *"Private campgrounds"* — **orange pins** for campgrounds that have at least
    a phone number or website. Popups show name, phone, operator, "Visit website"
    link, and Google Maps link when available.
  - *"Private (no data)"* — **gray pins** for campgrounds with neither phone nor
    website. Popup notes that no contact info is available.
- OSM/ODbL attribution shown on the map.
- Filters: by site type, and a result count.

### 3.2 Browse view (drill-down)
- **Park level**: list of parks (each showing sector count + "X dates open").
  Left panel shows an interactive geographic map of the selected park (Leaflet),
  since SEPAQ has no single park-wide schematic.
- **Sector level**: pick check-in/check-out dates → live fetch → left panel shows
  the **schematic boucle map (GIF)** with colour-coded dots for every positioned
  site; right panel lists available/partial sites as cards.
  - Multiple boucles → tabs to switch (no server re-fetch).
  - Dots and cards cross-highlight on hover; click a dot opens SEPAQ booking.
  - Each card shows photo (from photos.db), site type, price, colour status,
    and a "Book on SEPAQ" link.
- **Map always renders all positioned sites** (grey if unknown), so an empty
  date still shows the map, not a blank panel.
- Breadcrumb navigation; clean teardown of listeners between views.

### 3.3 Search view
- Cross-park search for availability by criteria (dates, site type).

### 3.4 Cookie / session management
- The app detects missing or expired SEPAQ cookies and shows a banner:
  - none → "No SEPAQ cookies found. Run get_cookie.py…"
  - present but stale/old or last live fetch 403'd → "cookies may have expired…"
- `cf_clearance` is captured manually from a browser (a helper script prompts
  for it). A rebuild should automate this capture (headless login / token
  service) since manual refresh every ~30–60 min is the biggest UX pain.

### 3.5 Scraper control
- The app can launch the scraper as a subprocess and stream its progress (SSE),
  with a running/idle status indicator ("Refresh Data" button).

### 3.6 Weather + mosquito
- Per-park weather (temp, conditions) for selected dates, cached.
- A derived "mosquito activity" indicator from weather inputs.

---

## 4. Data Model (current SQLite)

### 4.1 `sepaq.db` (main)
- `parks(id, name, slug, scraped_at)`
- `sectors(id, park_id, name, slug, url, scraped_at)`
- `boucles(id, sector_id, name, slug, url, is_sector_level, map_url, scraped_at)`
  — `map_url` = S3 schematic GIF.
- `sites(id, boucle_id, unit_id, site_name, site_type, url, x_pct, y_pct,
  photo_url, photo_data, scraped_at)` — `x_pct`/`y_pct` = dot position on the
  schematic (percent). (photo_url/photo_data legacy here; photos now live in
  photos.db.)
- `campsites`, `availability`, `range_availability` — scraped/cached availability
  used for park-list summaries and search.
- `weather_cache` — cached Open-Meteo results.
- `raw_responses` — raw scrape payloads (debugging).

### 4.2 `photos.db` (separate) — site images
- `site_photos(unit_id PRIMARY KEY, photo_url, photo_data BLOB, fetched_at)`.

### 4.3 `private.db` (separate) — private campgrounds
- `private_campgrounds(id, source, source_id, name, lat, lon, website, phone,
  operator, address, region, tags_json, fetched_at, maps_url, photo_url,
  enriched_at, cq_scraped_at, UNIQUE(source, source_id))`.
- `source` is `'osm'` for OpenStreetMap records or `'campingquebec'` for
  campgrounds added via the Camping Québec scraper.
- `maps_url` — Google Maps deep link (`maps.google.ca/?q=lat,lon`) parsed from
  the Camping Québec detail page.
- `photo_url` — reserved for future photo enrichment (currently unpopulated).
- `cq_scraped_at` — timestamp set when a row has been processed by
  `scrape_campingquebec.py`; used for resumability.

**Rebuild note:** keeping photos and private data in separate stores is
intentional — large BLOBs and an independently-refreshed external dataset
shouldn't share a lifecycle with the core SEPAQ data. In a web rebuild these map
naturally to separate tables/buckets (e.g. object storage for images).

---

## 5. API Surface (current Flask routes)

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/parks` | All parks + availability summary + sector count |
| GET | `/api/parks/<id>/sectors` | Sectors for a park |
| GET | `/api/parks/<id>/availability` | Park availability by date |
| GET | `/api/sectors/<id>/availability` | Sector availability (cached) |
| GET | `/api/sectors/<id>/live-sites?from&to[&available_only][&site_type]` | **Live** per-site availability (the core map feed). Returns sites + `cookie_status`. |
| GET | `/api/sectors/<id>/range-availability?from&to` | Product types available every night in range |
| GET | `/api/site-types` | Distinct site types |
| GET | `/api/private-campgrounds[?bbox=s,w,n,e]` | Private campgrounds from private.db. Returns `maps_url` and `photo_url` when columns exist (backward-compatible). |
| GET | `/api/site-photos?unit_ids=a,b,c` | Per-unit photo availability map (`"cached"` or URL) |
| GET | `/api/site-photo-img/<unit_id>` | Serve a site photo BLOB |
| GET | `/api/search` | Cross-park availability search |
| GET | `/api/weather?from&to` | Cached weather (stale-while-revalidate) |
| POST | `/api/scrape` | Launch scraper subprocess |
| GET | `/api/scrape/stream` | SSE progress stream |
| GET | `/api/scrape/status` | Scraper running/idle |
| GET | `/api/cookie-status` | Cookie presence/freshness for the banner |
| GET | `/` | Single-page frontend |

---

## 6. Non-Functional / Rebuild Considerations

- **Scraping service**: in a web app, scraping + cookie management must move
  server-side as a scheduled/queued job, not a subprocess triggered per user.
  Cloudflare bypass (curl_cffi/impersonation) and cookie freshness are the key
  risks; budget for a real session-acquisition strategy (headless browser).
- **Caching**: live fetches are cached ~30 min; expired/empty results are not
  cached so a cookie refresh takes effect immediately. Preserve this.
- **Concurrency**: live fetches parallelise per boucle, each in its OWN session
  (shared cookie jars race and return empty). Preserve isolation.
- **Legal/ToS**: SEPAQ scraping is a grey area; OSM data is ODbL (attribution
  required). A public web app raises both issues — review before launch.
- **Resilience**: all live features degrade gracefully when cookies are
  missing/expired (banners, grey dots, hidden photo slots) — never hard-error.

---

## 7. Open Questions / Known Gaps

1. **Blue site status** — SEPAQ returns `data-couleur="blue"` for many sites
   (not tied to a single site type). Meaning is unconfirmed (likely "available
   but not for the exact requested dates / min-stay"). Decide how to render and
   whether it counts as available.
2. **Cookie automation** — manual `cf_clearance` capture every 30–60 min is the
   biggest UX wart; a rebuild should automate session acquisition.
3. **Photos** — ~94%+ coverage; a few sites genuinely have no gallery photo.
4. **Private availability** — intentionally out of scope (no shared source);
   would be a per-platform effort (Campspot/Hipcamp/Camping Québec) if pursued.
5. **Multi-user / hosting / auth** — not yet designed; required for a web app.
6. **Private campground photos** — `photo_url` column exists but is unpopulated.
   Camping Québec detail pages have photos; a future pass of `scrape_campingquebec.py`
   could download and store them (similar to the SEPAQ `photos.db` pattern).
7. **Camping Québec re-scrape cadence** — membership and contact info changes
   seasonally. Run `scrape_campingquebec.py --apply --force` at the start of each
   camping season to refresh.

---

## 8. Tech Stack (current → suggested rebuild)

| Concern | Current | Web-app suggestion |
|---|---|---|
| Backend | Flask (single file) | FastAPI/Flask or Node; separate scraping worker |
| Scraping | curl_cffi + manual cookies | Headless-browser session service + queue |
| DB | SQLite (3 files) | Postgres + object storage for images |
| Frontend | Vanilla JS + Leaflet, 1 template | React/Vue + Leaflet/Mapbox |
| Maps | Leaflet + SEPAQ schematic GIF overlays | same approach (overlay dots on GIF) |
| Weather | Open-Meteo (no key) | same |
| Hosting | local | containerised; scheduled scrape jobs |

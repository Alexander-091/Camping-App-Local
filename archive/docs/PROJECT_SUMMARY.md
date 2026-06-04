# Milosevic Camp Planner — Project Summary

## What This App Does

A personal camping availability tracker for SEPAQ (Société des établissements de plein air du Québec) national parks in Quebec. The app scrapes SEPAQ's booking system to show:

- Which parks / sectors / individual campsites are available for a chosen date range
- Weather forecast (temperature, precipitation, wind, conditions) per park for the selected dates
- Estimated mosquito activity level per park based on weather
- Interactive map with colour-coded availability markers
- Browse and date-range search across all 15 parks with camping

---

## Folder Structure

```
Camping App/
├── app/
│   ├── app.py                  # Flask web server + all API endpoints
│   ├── requirements.txt
│   ├── start.bat               # Windows launch script
│   └── templates/
│       └── index.html          # Single-page frontend (map, browse, search tabs)
├── data/
│   └── sepaq.db                # SQLite database
├── scraper/
│   ├── scraper.py              # Main scraper
│   ├── get_cookie.py           # Interactive cookie capture helper
│   ├── inspect_db.py           # Debug/audit tool
│   ├── cookies.json            # Playwright-format cookies (CF + session)
│   └── session_cookie.json     # Simple key/value cookie file
└── PROJECT_SUMMARY.md          # This file
```

---

## How to Run

### Prerequisites
```
pip install flask curl_cffi requests playwright
playwright install chromium
```

### Start the web app
```
cd app
python app.py
# Open http://localhost:5000
```

### Refresh camping data
```
cd scraper
python get_cookie.py        # capture fresh Cloudflare + JSESSIONID cookies
python scraper.py           # full scrape (~15–20 min)
```

---

## Architecture

### Database Schema (`sepaq.db`)

```sql
parks           -- 15 parks with camping (slug, name, scraped_at)
sectors         -- ~60 sectors across all parks (park_id, slug, url)
boucles         -- Sub-sectors within each sector (sector_id, slug, url, is_sector_level)
                   is_sector_level=1 means the sector IS the boucle (standard parks)
                   is_sector_level=0 means it's a physical loop within the sector (Oka-style)
sites           -- Individual campsite spots (boucle_id, unit_id, site_name, site_type, url)
                   unit_id = SEPAQ's internal unit ID (e.g. "101007")
                   site_name = human-readable spot name (e.g. "le-refuge-103")
campsites       -- Legacy: product-type aggregate per sector (Tent/Standard, Serviced RV, etc.)
availability    -- Legacy: daily aggregate availability per campsite-type per sector
weather_cache   -- Cached Open-Meteo forecasts (from_date, to_date, data JSON)
raw_responses   -- Debug: raw API responses saved during scraping
```

**Key relationship:**
`parks → sectors → boucles → sites`

For standard parks (most), sectors have `is_sector_level=1` boucles — the sector page itself contains all the individual unit links. For Oka/Frontenac-style parks, sectors have multiple boucles (physical loops), each containing 20–60 individual sites.

---

## The SEPAQ Booking System (Critical Technical Knowledge)

### Session Architecture
SEPAQ is a Java Spring MVC application. It uses two session cookies:
- `JSESSIONID` — base session, obtained by visiting any page
- `JSESSIONID_TRANSAC` — transaction session, scoped when you visit a specific unit/boucle page
- Cloudflare cookies: `cf_clearance`, `__cf_bm`, `__cflb` — required to bypass bot protection

### Site Structure Hierarchy
```
Park (e.g. Parc national d'Oka)
  └── Sector (e.g. Le Refuge)
        └── Boucle (e.g. le-refuge-boucle-3)   ← physical loop of campsites
              └── Site (e.g. le-refuge-103)     ← individual campsite spot
```

For standard parks (Aiguebelle, etc.):
```
Park
  └── Sector (e.g. Ojibway)
        └── Sites directly (no boucle level)
```

### Availability Data — The Right Way (CRITICAL)
**The `/availabilities` API endpoint is NOT the right data source for per-site availability.**

The correct approach, discovered through browser inspection:
1. **POST to `https://www.sepaq.com/en/reservation/search`** with `arrivalDate`, `departureDate`, `booking.arrivalDate`, `booking.departureDate` — this sets the date range in the Java session
2. **GET each boucle page** (e.g. `.../le-refuge/le-refuge-boucle-3`)
3. **Parse the server-rendered HTML** — each individual site appears as:
   ```html
   <a id="unit_101007"
      data-url="/en/reservation/camping/.../le-refuge-103"
      data-couleur="green"        <!-- green=available, red=full, yellow=partial -->
      data-surnom="Campsite furnished with 3 services (water, electricity, sewer hookup)"
      data-prix="Starting at <strong>$57.60</strong>/night">
   ```
4. Filter by `data-couleur="green"` for available sites

**Why the old `/availabilities` API was wrong:** It returns aggregate counts by product type (76320=Tent, 76322=Serviced RV, etc.) — `nbDisponibleTotal` means total sites of that type available across the whole boucle. Visiting only one boucle gave data for that boucle only, and visiting only boucle-1 missed availability in boucles 2–6.

### Live Availability Endpoint
`app.py` has `/api/sectors/<id>/live-sites?from=&to=` which:
1. Loads cookies from `scraper/cookies.json` or `scraper/session_cookie.json`
2. POSTs dates to SEPAQ to set session state
3. Fetches all boucle pages for the sector **in parallel** (8 concurrent)
4. Parses `unit_*` HTML elements for per-site availability
5. Caches results 30 minutes in memory

This gives **real-time, per-site, date-specific** availability matching exactly what SEPAQ shows users.

### Scraper Strategy — Legacy Aggregate (for map/sidebar stats)
The scraper still calls the `/availabilities` API to populate aggregate stats used by the map markers and park sidebar. This data is inaccurate at the site level but gives a rough "does this park have any availability" signal.

**The scraper also now runs a discovery phase** that fetches each boucle page and stores individual site metadata (`unit_id`, `site_name`, `site_type`) in the `sites` table. This is what tells the live endpoint which boucle URLs to fetch.

---

## Known Bugs Fixed During This Session

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| Oka / Frontenac showing 0 availability | `_UnitLinkParser` only looked for `<a id="unit_*">` but Oka sector pages have sub-path boucle links instead | Added Strategy 2 in `discover_units()` to find sub-path links |
| Only last scraped sector per park had data | `UNIQUE(park_id, site_id)` on campsites table caused earlier sectors to lose their product rows when later sectors shared the same product IDs | Changed to `UNIQUE(sector_id, site_id)` |
| All 5 Oka sectors returned identical data | The scraper visited only the first boucle and called the API once per sector — but all sectors shared the same JSESSIONID_TRANSAC | Added per-boucle mode: visit each boucle, aggregate `nbDisponibleTotal` across boucles |
| Even with aggregation, Oka still showed 0 availability for some dates | The aggregate API only returns 0/1 counts per product type, not per physical site. Boucle-1 was full but boucle-3 had sites. The aggregate didn't capture this correctly | Discovered the correct approach: parse HTML `data-couleur` instead of calling the API |

---

## Weather Feature

Uses Open-Meteo API (free, no key required):
```
https://api.open-meteo.com/v1/forecast?latitude=...&longitude=...
  &daily=temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode,windspeed_10m_max
  &timezone=America%2FToronto&start_date=...&end_date=...
```

- All 15 parks fetched **in parallel** using `ThreadPoolExecutor`
- Results persisted in `weather_cache` SQLite table (survives restarts)
- Stale-while-revalidate: returns cached data instantly, refreshes in background if >1 hour old
- WMO weather codes mapped to emojis (☀️ ⛅ 🌧️ ❄️ ⛈️ etc.)
- Marker labels show today's dominant emoji + high temp (e.g. `⛅ 22°`)
- Popups show a per-day forecast table with temp, precipitation, wind

---

## Mosquito Activity Model

Computed client-side from weather data (no external API). Formula per day:

```
score = tempScore × precipMultiplier × windMultiplier × quebecSeasonMultiplier
```

- **Temperature**: peaks at 22–26°C, zero below 5°C, drops above 32°C
- **Precipitation**: light rain increases breeding (+30%), heavy rain suppresses (−10%)
- **Wind**: above 20 km/h suppresses activity (−28%), above 30 km/h (−45%)
- **Quebec season**: June/July peak (1.0–1.15×), May ramp-up (0.65×), September drops (0.45×)

Rating 1–5: Minimal → Low → Moderate → High → Severe, shown in park popups.

---

## Map Filter

The map has a filter bar with:
- Check-in / Check-out date pickers
- Site type dropdown (populated from DB: Tent/Standard, Serviced RV, Rustic, Serviced Premium, Prêt-à-camper)
- When filtered: parks WITH availability show coloured markers, parks WITHOUT show grey/faded markers
- Result count shown (e.g. "8 parks available (Serviced RV)")

When map filter dates are set and you click a park → sector, the browse view calls the **live-sites endpoint** and shows individual campsite cards grouped by boucle.

---

## Product Type IDs (Assumed — Needs Visual Verification)

SEPAQ uses internal product IDs. These were assumed from price/context; **not yet verified against the SEPAQ website**:

| Product ID | Assumed Type |
|-----------|-------------|
| 76320 | Tent / Standard |
| 76321 | Serviced (Premium) |
| 76322 | Serviced (RV) |
| 76324 | Rustic |
| 76326 | Prêt-à-camper (Glamping) |

---

## Pending / Recommended Next Steps

1. **Re-run the scraper with fresh cookies** to populate `boucles` and `sites` tables — this is required before the live-sites endpoint returns data
2. **Verify product type ID mappings** — open SEPAQ for a park you know well and cross-reference against the DB product IDs
3. **Clean up debug `print` statements** in `scraper.py` — `visit_park()` and `fetch_sector_html()` still have verbose HTTP diagnostic prints
4. **Add a site-type filter to the Browse view** — currently site_type filter only works from the map panel
5. **Handle parks with no camping** gracefully — Île-Bonaventure, Lac-Mégantic, Mont-Saint-Bruno, Pointe-Taillon, Saguenay are in the DB but have no campsite data; they show "No availability" which is correct but could show a more informative message
6. **Cookie freshness warning** — if cookies are >24h old, the live endpoint will fail silently; add a warning in the UI
7. **The Search tab** still uses the legacy aggregate availability, not the live per-site data

---

## Important Files to Read First

If continuing on a new machine, read these files in order:
1. `scraper/scraper.py` — understand `discover_units()`, `_SiteParser`, `save_boucle()`, `save_sites()`, and the `scrape()` function
2. `app/app.py` — understand `_fetch_live_sites()` and `/api/sectors/<id>/live-sites`
3. `app/templates/index.html` — understand `openSectorAvail()`, `openSectorRangeAvail()`, `fetchWeather()`, `mosquitoRating()`

---

## Cookie Management

Cookies must be refreshed periodically (Cloudflare cookies expire ~24h; JSESSIONID may last longer).

```
cd scraper
python get_cookie.py
```

This opens a Playwright browser, navigates to SEPAQ, and prompts you to copy cookies. **Only paste CF cookies** (`cf_clearance`, `__cf_bm`, `__cflb`). Do NOT paste `JSESSIONID` or `JSESSIONID_TRANSAC` manually — these are captured automatically by the scraper's session management.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.10+, Flask |
| HTTP scraping | `curl_cffi` (Cloudflare TLS bypass), `urllib.request` |
| HTML parsing | Python `html.parser` (stdlib) |
| Browser automation | Playwright (cookie capture only) |
| Database | SQLite via `sqlite3` |
| Frontend | Vanilla JS + HTML/CSS (no framework) |
| Maps | Leaflet.js |
| Weather | Open-Meteo API (free, no key) |
| Concurrency | `ThreadPoolExecutor` (parallel weather + boucle fetches) |

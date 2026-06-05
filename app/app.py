"""
SEPAQ Camping — Flask web app
Serves the frontend and exposes API routes that read from sepaq.db.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from flask import Flask, jsonify, render_template, request, Response, stream_with_context

# ─── Config ───────────────────────────────────────────────────────────────────

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, "..", "data", "sepaq.db")
PHOTOS_DB_PATH = os.path.join(BASE_DIR, "..", "data", "photos.db")
PRIVATE_DB_PATH = os.path.join(BASE_DIR, "..", "data", "private.db")
SCRAPER_DIR  = os.path.join(BASE_DIR, "..", "scraper")
SCRAPER_PY   = os.path.join(SCRAPER_DIR, "scraper.py")
COOKIE_FILE  = os.path.join(SCRAPER_DIR, "session_cookie.json")
COOKIE_FILE_PW = os.path.join(SCRAPER_DIR, "cookies.json")
SEPAQ_BASE   = "https://www.sepaq.com"

# Cloudflare-protected requests (warm-up, date POST, boucle GET) must use
# curl_cffi's TLS impersonation to get past Cloudflare — plain urllib is 403'd,
# which is why live availability colours came back empty. Matches the scraper.
try:
    from curl_cffi import requests as cffi_requests
except Exception:
    cffi_requests = None

_CF_DOMAIN_COOKIES = {"cf_clearance", "__cf_bm", "__cflb"}
_LIVE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_CFFI_HTML_HEADERS = {
    "User-Agent":      _LIVE_UA,
    "Accept-Language": "en-CA,en;q=0.9,fr;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# Approximate lat/lon for all 20 SEPAQ national parks
PARK_COORDS = {
    "parc-national-d-aiguebelle":                             (48.517, -78.717),
    "parc-national-du-bic":                                   (48.367, -68.817),
    "parc-national-de-frontenac":                             (45.983, -71.117),
    "parc-national-de-la-gaspesie":                           (48.917, -66.083),
    "parc-national-des-grands-jardins":                       (47.683, -70.850),
    "parc-national-des-hautes-gorges-de-la-riviere-malbaie":  (47.800, -70.417),
    "parc-national-de-l-ile-bonaventure-et-du-rocher-perce":  (48.517, -64.217),
    "parc-national-de-la-jacques-cartier":                    (47.317, -71.350),
    "parc-national-du-lac-megantic":                          (45.567, -70.883),
    "parc-national-du-lac-temiscouata":                       (47.683, -68.900),
    "parc-national-du-mont-megantic":                         (45.450, -71.133),
    "parc-national-du-mont-orford":                           (45.317, -72.217),
    "parc-national-du-mont-saint-bruno":                      (45.533, -73.350),
    "parc-national-du-mont-tremblant":                        (46.483, -74.583),
    "parc-national-des-monts-valin":                          (48.633, -70.850),
    "parc-national-d-oka":                                    (45.467, -74.083),
    "parc-national-de-plaisance":                             (45.617, -75.117),
    "parc-national-de-pointe-taillon":                        (48.717, -72.283),
    "parc-national-du-saguenay":                              (48.233, -70.400),
    "parc-national-de-la-yamaska":                            (45.400, -72.917),
}

_scraper_state = {"running": False, "process": None}

# ─── Live-site fetching (HTML scrape of SEPAQ boucle pages) ──────────────────

_live_cache: dict = {}      # (sector_id, from, to) → {ts, data}
_LIVE_TTL = 1800            # 30 min
_last_live_expired = False  # True if the most recent live fetch hit a 401/403

class _SiteParser(HTMLParser):
    """Parse unit_ elements with availability data-* from a rendered boucle page."""
    def __init__(self):
        super().__init__()
        self.sites: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        a = dict(attrs)
        uid = a.get("id", "")
        if not uid.startswith("unit_"):
            return
        data_url  = a.get("data-url", "")
        site_name = data_url.split("/")[-1] if data_url else ""
        pm = re.search(r'\$(\d+(?:\.\d+)?)', a.get("data-prix", ""))
        self.sites.append({
            "unit_id":   uid[5:],
            "site_name": site_name,
            "url":       data_url,
            "colour":    a.get("data-couleur", ""),
            "available": a.get("data-couleur", "") == "green",
            "partial":   a.get("data-couleur", "") == "yellow",
            "site_type": a.get("data-surnom", ""),
            "price":     float(pm.group(1)) if pm else None,
        })


def _load_sepaq_cookies() -> dict:
    """Load cookies from whichever scraper cookie file exists."""
    for fpath in [COOKIE_FILE_PW, COOKIE_FILE]:
        if not os.path.exists(fpath):
            continue
        with open(fpath) as f:
            raw = json.load(f)
        if isinstance(raw, list):          # Playwright format
            return {c["name"]: c["value"] for c in raw}
        if isinstance(raw, dict):
            return raw
    return {}


def _build_cookie_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


_LIVE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9,fr-CA;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}


def _make_sepaq_opener():
    """Build a urllib opener backed by a cookie jar.

    SEPAQ stores the chosen date range in a server-side Java session keyed by a
    JSESSIONID cookie it hands out on the date-setting POST. A shared opener's
    jar captures that session cookie so it can be replayed on the subsequent
    boucle page GETs, which then render the per-site availability grid. Without
    this the session cookie is discarded and every page comes back with no sites.
    """
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _opener_jar(opener):
    """Return the CookieJar attached to an opener, or None."""
    if opener is None:
        return None
    for h in opener.handlers:
        if hasattr(h, "cookiejar"):
            return h.cookiejar
    return None


def _merged_cookie_header(cookie_header: str, opener=None) -> str:
    """Combine the base (Cloudflare) cookie header with any session cookies the
    opener's jar has captured, into a single Cookie header.

    urllib's HTTPCookieProcessor will NOT inject jar cookies when an explicit
    Cookie header is already present on the request, so we merge them by hand.
    Jar cookies win on name collisions.
    """
    jar = _opener_jar(opener)
    if not jar:
        return cookie_header
    pairs = {}
    if cookie_header:
        for part in cookie_header.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                pairs[k] = v
    for c in jar:
        pairs[c.name] = c.value
    return "; ".join(f"{k}={v}" for k, v in pairs.items())


def _note_http_error(exc, status):
    """If exc is a 401/403 (expired/blocked cookies), flag it on the status dict."""
    if status is not None and isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403):
        status["expired"] = True


def _sepaq_get(url: str, cookie_header: str, opener=None, status=None) -> str | None:
    """GET a SEPAQ page and return its decoded text, or None on error."""
    hdr = _merged_cookie_header(cookie_header, opener)
    req = urllib.request.Request(url, headers={**_LIVE_HEADERS, "Cookie": hdr})
    _open = opener.open if opener is not None else urllib.request.urlopen
    try:
        with _open(req, timeout=15) as resp:
            raw = resp.read()
            encoding = resp.headers.get_content_charset("utf-8")
            return raw.decode(encoding, errors="replace")
    except Exception as exc:
        _note_http_error(exc, status)
        return None


def _set_session_dates(from_date: str, to_date: str, cookie_header: str, opener=None, status=None) -> bool:
    """POST to /en/reservation/search to store dates in the Java session."""
    url  = f"{SEPAQ_BASE}/en/reservation/search"
    body = urllib.parse.urlencode({
        "arrivalDate":             from_date,
        "departureDate":           to_date,
        "booking.arrivalDate":     from_date,
        "booking.departureDate":   to_date,
        "booking.adults":          "2",
    }).encode()
    hdr = _merged_cookie_header(cookie_header, opener)
    req = urllib.request.Request(url, data=body, headers={
        **_LIVE_HEADERS,
        "Cookie":       hdr,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    _open = opener.open if opener is not None else urllib.request.urlopen
    try:
        with _open(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as exc:
        _note_http_error(exc, status)
        return False


def _parse_boucle_page(url: str, cookie_header: str, opener=None, status=None) -> list[dict]:
    html = _sepaq_get(SEPAQ_BASE + url if url.startswith("/") else url, cookie_header, opener, status)
    if not html:
        return []
    p = _SiteParser()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.sites


def _fetch_live_sites(sector_id: int, from_date: str, to_date: str) -> dict:
    """
    Set dates in SEPAQ session, then fetch each boucle page for the sector
    and parse per-site availability from the rendered HTML.

    Returns {"sites": [...], "cookie_status": "ok"|"missing"|"expired"}.
    """
    status = {"expired": False}
    conn = get_db()
    sector = conn.execute(
        "SELECT s.*, p.name AS park_name, p.slug AS park_slug"
        " FROM sectors s JOIN parks p ON p.id=s.park_id WHERE s.id=?", (sector_id,)
    ).fetchone()
    boucles = conn.execute(
        "SELECT id, slug, url, is_sector_level, map_url FROM boucles WHERE sector_id=?", (sector_id,)
    ).fetchall()
    # Full stored site list for this sector — used as the BASE so the map always
    # shows every positioned site (grey/unknown), with live availability overlaid.
    db_sites = conn.execute(
        "SELECT s.unit_id, s.site_name, s.site_type, s.url, s.x_pct, s.y_pct,"
        "       b.slug AS boucle_slug, b.map_url AS boucle_map_url"
        " FROM sites s JOIN boucles b ON b.id=s.boucle_id WHERE b.sector_id=?", (sector_id,)
    ).fetchall()
    conn.close()

    if not sector:
        return {"sites": [], "cookie_status": "ok"}

    # Base map of every known site, keyed by unit_id, defaulting to unknown.
    base_by_unit = {}
    for r in db_sites:
        base_by_unit[str(r["unit_id"])] = {
            "unit_id":        str(r["unit_id"]),
            "site_name":      r["site_name"],
            "site_type":      r["site_type"] or "",
            "url":            r["url"] or "",
            "x_pct":          r["x_pct"],
            "y_pct":          r["y_pct"],
            "boucle":         r["boucle_slug"],
            "boucle_map_url": r["boucle_map_url"] or "",
            "park_name":      sector["park_name"],
            "sector_name":    sector["name"],
            "sector_url":     sector["url"],
            "colour":         "",
            "available":      False,
            "partial":        False,
            "price":          None,
        }

    cookies    = _load_sepaq_cookies()
    cookie_hdr = _build_cookie_header(cookies)

    if not cookies:
        return {"sites": [], "cookie_status": "missing"}

    def _result():
        return {"sites": list(base_by_unit.values()),
                "cookie_status": "expired" if status["expired"] else "ok"}

    park_slug = sector["park_slug"]

    def _fetch_one(url):
        """Fetch one boucle/sector page in its OWN isolated SEPAQ session.

        Uses curl_cffi with TLS impersonation, mirroring the scraper's proven
        _range_session_worker. Plain urllib gets 403'd by Cloudflare on the
        warm-up / date-POST, so dates never bind and availability colours come
        back empty. The working sequence is:
          1. GET the park camping page to establish a JSESSIONID in booking context.
          2. POST the date range to /en/reservation/search.
          3. GET the boucle page (now reflecting those dates, with data-couleur).

        Each call gets its own session so concurrent boucle fetches don't clobber
        each other's server-side state.
        """
        if cffi_requests is None:
            # curl_cffi unavailable — nothing we can do that Cloudflare won't block.
            status["expired"] = True
            return []

        full_url = url if url.startswith("http") else SEPAQ_BASE + url
        try:
            sess = cffi_requests.Session(impersonate="chrome120")
            sess.headers.update(_CFFI_HTML_HEADERS)
            for name, value in cookies.items():
                domain = ".sepaq.com" if name in _CF_DOMAIN_COOKIES else "www.sepaq.com"
                sess.cookies.set(name, value, domain=domain)

            # 1. Warm up the booking session.
            if park_slug:
                sess.get(f"{SEPAQ_BASE}/en/reservation/camping/{park_slug}", timeout=20)
            # 2. Apply the date range.
            sess.post(f"{SEPAQ_BASE}/en/reservation/search", data={
                "arrivalDate":           from_date,
                "departureDate":         to_date,
                "booking.arrivalDate":   from_date,
                "booking.departureDate": to_date,
                "booking.adults":        "2",
            }, timeout=15)
            # 3. Fetch the boucle page.
            r = sess.get(full_url, timeout=15)
            if r.status_code in (401, 403):
                status["expired"] = True
                return []
            p = _SiteParser()
            try:
                p.feed(r.text)
            except Exception:
                pass
            return p.sites
        except Exception:
            return []

    def _overlay(live_sites):
        """Overlay live availability onto the base site map by unit_id."""
        for s in live_sites:
            uid = str(s.get("unit_id", ""))
            base = base_by_unit.get(uid)
            if base is None:
                # Site present live but not in DB — add it (no stored position).
                base = {**s, "park_name": sector["park_name"],
                        "sector_name": sector["name"], "sector_url": sector["url"]}
                base_by_unit[uid] = base
            base["colour"]    = s.get("colour", "")
            base["available"] = s.get("available", False)
            base["partial"]   = s.get("partial", False)
            if s.get("price") is not None:
                base["price"] = s["price"]
            if s.get("url"):
                base["url"] = s["url"]

    if not boucles:
        # No boucle data yet — fall back to fetching the sector page directly
        url = sector["url"] or (
            f"{SEPAQ_BASE}/en/reservation/camping/{sector['park_slug']}/{sector['slug']}"
        )
        _overlay(_fetch_one(url))
        return _result()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_fetch_one, b["url"]) for b in boucles]
        for future in as_completed(futures):
            try:
                _overlay(future.result())
            except Exception:
                pass

    return _result()

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_photos_db():
    """Connection to the separate photos.db (built by scrape_photos.py).

    Returns None if the photo database doesn't exist yet, so photo endpoints
    degrade gracefully (cards just hide their photo slot).
    """
    if not os.path.exists(PHOTOS_DB_PATH):
        return None
    conn = sqlite3.connect(PHOTOS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_private_db():
    """Connection to the separate private.db (built by fetch_osm_campgrounds.py).

    Returns None if the database doesn't exist yet, so the private-campgrounds
    endpoint degrades gracefully (returns []) until the OSM data is fetched.
    """
    if not os.path.exists(PRIVATE_DB_PATH):
        return None
    conn = sqlite3.connect(PRIVATE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─── Weather cache (DB-backed, parallelised) ──────────────────────────────────

_WEATHER_TTL   = 3600          # refresh if older than 1 hour
_weather_mem   = {}            # in-memory layer: key → {ts, data}
_weather_lock  = threading.Lock()
_refreshing    = set()         # keys currently being refreshed in background

def _ensure_weather_table():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weather_cache (
            from_date  TEXT,
            to_date    TEXT,
            data       TEXT,
            fetched_at TEXT,
            PRIMARY KEY (from_date, to_date)
        )
    """)
    conn.commit()
    conn.close()

def _fetch_one_park_weather(slug, lat, lon, from_date, to_date):
    """Fetch Open-Meteo for a single park. Returns (slug, days_list)."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=temperature_2m_max,temperature_2m_min"
        ",precipitation_sum,weathercode,windspeed_10m_max"
        "&timezone=America%2FToronto"
        f"&start_date={from_date}&end_date={to_date}"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        d = json.loads(resp.read()).get("daily", {})
    days = [
        {
            "date":     d["time"][i],
            "temp_max": d["temperature_2m_max"][i],
            "temp_min": d["temperature_2m_min"][i],
            "precip":   d["precipitation_sum"][i],
            "code":     d["weathercode"][i],
            "wind":     d["windspeed_10m_max"][i],
        }
        for i in range(len(d.get("time", [])))
    ]
    return slug, days

def _do_weather_refresh(from_date, to_date):
    """Fetch all parks in parallel, write to DB and memory."""
    key = (from_date, to_date)
    parks_with_coords = [
        (slug, lat, lon)
        for slug, (lat, lon) in PARK_COORDS.items()
    ]

    result_by_slug = {}
    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {
            pool.submit(_fetch_one_park_weather, slug, lat, lon, from_date, to_date): slug
            for slug, lat, lon in parks_with_coords
        }
        for future in as_completed(futures):
            try:
                slug, days = future.result()
                result_by_slug[slug] = days
            except Exception:
                pass   # leave missing parks out; they'll show no weather

    # Attach park_id / name from DB
    conn = get_db()
    result = []
    for slug, days in result_by_slug.items():
        park = conn.execute("SELECT id, name FROM parks WHERE slug=?", (slug,)).fetchone()
        if park:
            result.append({"park_id": park["id"], "name": park["name"], "days": days})

    serialised = json.dumps(result)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO weather_cache (from_date, to_date, data, fetched_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(from_date, to_date) DO UPDATE SET
            data=excluded.data, fetched_at=excluded.fetched_at
    """, (from_date, to_date, serialised, now))
    conn.commit()
    conn.close()

    with _weather_lock:
        _weather_mem[key] = {"ts": time.time(), "data": result}
        _refreshing.discard(key)

def _trigger_refresh(from_date, to_date):
    key = (from_date, to_date)
    with _weather_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)
    threading.Thread(target=_do_weather_refresh, args=(from_date, to_date), daemon=True).start()

def _load_weather_cache_from_db():
    """On startup: pull all cached weather rows into memory."""
    try:
        _ensure_weather_table()
        conn = get_db()
        rows = conn.execute("SELECT from_date, to_date, data, fetched_at FROM weather_cache").fetchall()
        conn.close()
        for r in rows:
            key = (r["from_date"], r["to_date"])
            try:
                fetched_ts = datetime.fromisoformat(r["fetched_at"]).timestamp()
            except Exception:
                fetched_ts = 0
            with _weather_lock:
                _weather_mem[key] = {"ts": fetched_ts, "data": json.loads(r["data"])}
    except Exception:
        pass

# Warm the cache on startup
threading.Thread(target=_load_weather_cache_from_db, daemon=True).start()


# ─── Parks ────────────────────────────────────────────────────────────────────

@app.get("/api/parks")
def api_parks():
    """All parks with availability summary and sector count."""
    conn = get_db()
    parks = conn.execute("SELECT * FROM parks ORDER BY name").fetchall()

    result = []
    for p in parks:
        slug = p["slug"] or ""
        lat, lon = PARK_COORDS.get(slug, (None, None))

        avail = conn.execute("""
            SELECT COUNT(DISTINCT a.date)  AS avail_dates,
                   SUM(a.sites_available)  AS total_sites_available,
                   MIN(a.price)            AS min_price
            FROM   campsites cs
            JOIN   availability a ON a.campsite_id = cs.id
            WHERE  cs.park_id  = ?
              AND  a.available = 1
              AND  a.date >= date('now')
              AND  a.date <= date('now', '+90 days')
        """, (p["id"],)).fetchone()

        sector_count = conn.execute(
            "SELECT COUNT(*) FROM sectors WHERE park_id=?", (p["id"],)
        ).fetchone()[0]

        result.append({
            "id":           p["id"],
            "name":         p["name"],
            "slug":         slug,
            "lat":          lat,
            "lon":          lon,
            "avail_dates":  avail["avail_dates"]  if avail else 0,
            "sector_count": sector_count,
            "min_price":    avail["min_price"]    if avail else None,
            "scraped_at":   p["scraped_at"],
        })

    conn.close()
    return jsonify(result)


# ─── Sectors ──────────────────────────────────────────────────────────────────

@app.get("/api/parks/<int:park_id>/sectors")
def api_park_sectors(park_id):
    """
    List sectors for a park, each with availability summary for the
    next 90 days.  If the park has no sectors (park-level fallback data),
    returns an empty list — the caller should fall back to
    /api/parks/<id>/availability.
    """
    conn = get_db()
    park = conn.execute("SELECT * FROM parks WHERE id=?", (park_id,)).fetchone()
    if not park:
        conn.close()
        return jsonify({"error": "Park not found"}), 404

    sectors = conn.execute(
        "SELECT * FROM sectors WHERE park_id=? ORDER BY name", (park_id,)
    ).fetchall()

    result = []
    for s in sectors:
        avail = conn.execute("""
            SELECT COUNT(DISTINCT a.date)           AS avail_dates,
                   MAX(a.sites_available)           AS max_sites,
                   MIN(a.price)                     AS min_price
            FROM   campsites cs
            JOIN   availability a ON a.campsite_id = cs.id
            WHERE  cs.sector_id = ?
              AND  a.available  = 1
              AND  a.date >= date('now')
              AND  a.date <= date('now', '+90 days')
        """, (s["id"],)).fetchone()

        result.append({
            "id":          s["id"],
            "park_id":     park_id,
            "name":        s["name"],
            "slug":        s["slug"],
            "url":         s["url"],
            "avail_dates": avail["avail_dates"] if avail else 0,
            "max_sites":   avail["max_sites"]   if avail else 0,
            "min_price":   avail["min_price"]   if avail else None,
        })

    conn.close()
    return jsonify({
        "park":    {"id": park["id"], "name": park["name"], "slug": park["slug"]},
        "sectors": result,
    })


@app.get("/api/sectors/<int:sector_id>/availability")
def api_sector_availability(sector_id):
    """
    Per-date availability for one sector: how many sites are open,
    minimum price, and a direct link to SEPAQ to book.
    Optionally filtered by ?from=YYYY-MM-DD&to=YYYY-MM-DD.
    """
    from_date = request.args.get("from", datetime.now().strftime("%Y-%m-%d"))
    to_date   = request.args.get("to", "")

    conn = get_db()
    sector = conn.execute(
        "SELECT s.*, p.name AS park_name, p.slug AS park_slug"
        " FROM sectors s JOIN parks p ON p.id = s.park_id"
        " WHERE s.id=?", (sector_id,)
    ).fetchone()
    if not sector:
        conn.close()
        return jsonify({"error": "Sector not found"}), 404

    query = """
        SELECT a.date,
               SUM(a.sites_available)                           AS sites_available,
               MIN(CASE WHEN a.available=1 THEN a.price END)    AS min_price,
               SUM(a.available)                                 AS products_available
        FROM   availability a
        JOIN   campsites cs ON cs.id = a.campsite_id
        WHERE  cs.sector_id = ?
          AND  a.date >= ?
    """
    params = [sector_id, from_date]
    if to_date:
        query += " AND a.date <= ?"
        params.append(to_date)
    query += " GROUP BY a.date ORDER BY a.date"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    # Build SEPAQ booking URL for this sector
    sepaq_url = sector["url"] or (
        f"https://www.sepaq.com/en/reservation/camping"
        f"/{sector['park_slug']}/{sector['slug']}"
    )

    return jsonify({
        "sector": {
            "id":        sector["id"],
            "name":      sector["name"],
            "slug":      sector["slug"],
            "park_name": sector["park_name"],
            "park_slug": sector["park_slug"],
            "sepaq_url": sepaq_url,
        },
        "dates": [dict(r) for r in rows],
    })


# ─── Live per-site availability (real-time HTML scrape) ──────────────────────

@app.get("/api/sectors/<int:sector_id>/live-sites")
def api_live_sites(sector_id):
    """
    Real-time per-site availability for a sector on a specific date range.
    Fetches SEPAQ boucle pages live using stored session cookies.
    Results cached 30 min.
    ?from=YYYY-MM-DD&to=YYYY-MM-DD[&available_only=1]
    """
    from_date      = request.args.get("from")
    to_date        = request.args.get("to")
    available_only = request.args.get("available_only", "0") == "1"
    site_type_filter = request.args.get("site_type", "")

    if not from_date or not to_date:
        return jsonify({"error": "Provide ?from=&to="}), 400

    key = (sector_id, from_date, to_date)
    with _weather_lock:    # reuse the existing lock for simplicity
        cached = _live_cache.get(key)
    # Only serve cache if it was a successful (non-expired) fetch — never cache
    # an expired/empty result, so a cookie refresh takes effect immediately.
    if cached and (time.time() - cached["ts"]) < _LIVE_TTL \
            and cached["data"].get("cookie_status") == "ok":
        fetched = cached["data"]
    else:
        fetched = _fetch_live_sites(sector_id, from_date, to_date)
        with _weather_lock:
            _live_cache[key] = {"ts": time.time(), "data": fetched}
        global _last_live_expired
        _last_live_expired = fetched.get("cookie_status") == "expired"

    sites = fetched.get("sites", [])
    cookie_status = fetched.get("cookie_status", "ok")

    if available_only:
        sites = [s for s in sites if s.get("available") or s.get("partial")]
    if site_type_filter:
        sites = [s for s in sites if site_type_filter.lower() in s.get("site_type","").lower()]

    # Build SEPAQ booking URL for this sector
    conn = get_db()
    sector = conn.execute(
        "SELECT s.url, s.slug, p.slug AS park_slug FROM sectors s"
        " JOIN parks p ON p.id=s.park_id WHERE s.id=?", (sector_id,)
    ).fetchone()
    conn.close()
    sepaq_url = (sector["url"] if sector and sector["url"] else
                 f"https://www.sepaq.com/en/reservation/camping"
                 f"/{sector['park_slug']}/{sector['slug']}" if sector else "#")

    return jsonify({
        "from_date": from_date,
        "to_date":   to_date,
        "sepaq_url": sepaq_url,
        "sites":     sites,
        "has_cookies": bool(_load_sepaq_cookies()),
        "cookie_status": cookie_status,
    })


# ─── Sector range-availability (every night in range) ────────────────────────

@app.get("/api/sectors/<int:sector_id>/range-availability")
def api_sector_range_availability(sector_id):
    """
    Returns campsite product types that have at least 1 site available
    for EVERY night in [from, to).  to_date is checkout day (not a night).
    ?from=YYYY-MM-DD&to=YYYY-MM-DD[&site_type=Serviced (RV)]
    """
    from_date = request.args.get("from")
    to_date   = request.args.get("to")
    site_type = request.args.get("site_type", "")
    if not from_date or not to_date:
        return jsonify({"error": "Provide ?from=&to="}), 400

    conn = get_db()
    sector = conn.execute(
        "SELECT s.*, p.name AS park_name, p.slug AS park_slug"
        " FROM sectors s JOIN parks p ON p.id=s.park_id WHERE s.id=?", (sector_id,)
    ).fetchone()
    if not sector:
        conn.close()
        return jsonify({"error": "Sector not found"}), 404

    type_clause = "AND cs.type = ?" if site_type else ""
    params      = [sector_id, from_date, to_date] + ([site_type] if site_type else [])

    # nights = number of distinct dates from from_date up to (not including) to_date
    nights_row = conn.execute(
        "SELECT CAST(julianday(?) - julianday(?) AS INTEGER) AS n", (to_date, from_date)
    ).fetchone()
    nights = nights_row["n"] if nights_row else 0

    rows = conn.execute(f"""
        SELECT cs.id, cs.name, cs.type,
               COUNT(DISTINCT a.date)                        AS covered_nights,
               MIN(a.sites_available)                        AS min_sites,
               MIN(CASE WHEN a.available=1 THEN a.price END) AS min_price,
               MAX(CASE WHEN a.available=1 THEN a.price END) AS max_price
        FROM   campsites cs
        JOIN   availability a ON a.campsite_id = cs.id
        WHERE  cs.sector_id = ?
          AND  a.available  = 1
          AND  a.date >= ? AND a.date < ?
          {type_clause}
        GROUP  BY cs.id
        HAVING covered_nights = ?
    """, params + [nights]).fetchall()

    sepaq_url = sector["url"] or (
        f"https://www.sepaq.com/en/reservation/camping"
        f"/{sector['park_slug']}/{sector['slug']}"
    )
    conn.close()

    return jsonify({
        "sector":    {"id": sector["id"], "name": sector["name"],
                      "park_name": sector["park_name"], "sepaq_url": sepaq_url},
        "from_date": from_date,
        "to_date":   to_date,
        "nights":    nights,
        "products":  [dict(r) for r in rows],
    })


# ─── Park-level availability (fallback / aggregate) ───────────────────────────

@app.get("/api/parks/<int:park_id>/availability")
def api_park_availability(park_id):
    """
    Aggregate availability across all sectors (or park-level data if
    no sectors).  Used as a fallback when a park has no sector breakdown.
    """
    from_date = request.args.get("from", datetime.now().strftime("%Y-%m-%d"))
    to_date   = request.args.get("to", "")

    conn = get_db()
    park = conn.execute("SELECT * FROM parks WHERE id=?", (park_id,)).fetchone()
    if not park:
        conn.close()
        return jsonify({"error": "Park not found"}), 404

    query = """
        SELECT a.date,
               SUM(a.sites_available)                        AS sites_available,
               MIN(CASE WHEN a.available=1 THEN a.price END) AS min_price
        FROM   availability a
        JOIN   campsites cs ON cs.id = a.campsite_id
        WHERE  cs.park_id = ?
          AND  a.date >= ?
    """
    params = [park_id, from_date]
    if to_date:
        query += " AND a.date <= ?"
        params.append(to_date)
    query += " GROUP BY a.date ORDER BY a.date"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    return jsonify({
        "park":  {"id": park["id"], "name": park["name"], "slug": park["slug"]},
        "dates": [dict(r) for r in rows],
    })


# ─── Search ───────────────────────────────────────────────────────────────────

@app.get("/api/site-types")
def api_site_types():
    """Distinct campsite types present in the DB."""
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT type FROM campsites WHERE type IS NOT NULL ORDER BY type"
    ).fetchall()
    conn.close()
    return jsonify([r["type"] for r in rows])


@app.get("/api/private-campgrounds")
def api_private_campgrounds():
    """Private (non-SEPAQ) campgrounds from OSM, served from the separate
    private.db. Returns [] if the data hasn't been fetched yet, so the map's
    optional private layer simply shows nothing rather than erroring.

    Optional ?bbox=south,west,north,east filters to a bounding box.
    """
    conn = get_private_db()
    if conn is None:
        return jsonify([])

    # Guard against the table not existing yet.
    has_table = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='private_campgrounds'"
    ).fetchone()[0]
    if not has_table:
        conn.close()
        return jsonify([])

    # Check which optional enrichment columns exist (added by migrate_private.py).
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(private_campgrounds)")}
    extra = ", ".join(c for c in ("maps_url", "photo_url") if c in existing_cols)
    select_extra = f", {extra}" if extra else ""

    query = (f"SELECT id, name, lat, lon, website, phone, operator, address, region{select_extra}"
             " FROM private_campgrounds")
    params = []
    bbox = request.args.get("bbox", "")
    if bbox:
        try:
            south, west, north, east = map(float, bbox.split(","))
            query += " WHERE lat >= ? AND lat <= ? AND lon >= ? AND lon <= ?"
            params = [south, north, west, east]
        except (ValueError, IndexError):
            pass
    query += " ORDER BY name"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/site-photos")
def api_site_photos():
    """Bulk photo-availability lookup for site cards.

    ?unit_ids=a,b,c → { unit_id: "cached" | "<cdn url>" }
    "cached" means a local BLOB exists (served via /api/site-photo-img/<id>);
    otherwise the value is the direct CDN photo URL. Units with no photo at
    all are omitted so the frontend hides their photo slot.
    """
    raw = request.args.get("unit_ids", "")
    unit_ids = [u.strip() for u in raw.split(",") if u.strip()]
    if not unit_ids:
        return jsonify({})

    conn = get_photos_db()
    if conn is None:
        return jsonify({})   # photos.db not built yet
    placeholders = ",".join("?" for _ in unit_ids)
    rows = conn.execute(
        f"SELECT unit_id, photo_url, (photo_data IS NOT NULL) AS has_blob"
        f" FROM site_photos WHERE unit_id IN ({placeholders})",
        unit_ids,
    ).fetchall()
    conn.close()

    out = {}
    for r in rows:
        if r["has_blob"]:
            out[str(r["unit_id"])] = "cached"
        elif r["photo_url"]:
            out[str(r["unit_id"])] = r["photo_url"]
    return jsonify(out)


@app.get("/api/site-photo-img/<unit_id>")
def api_site_photo_img(unit_id):
    """Serve a single site's locally-stored photo BLOB from photos.db."""
    conn = get_photos_db()
    if conn is None:
        return ("", 404)
    row = conn.execute(
        "SELECT photo_data, photo_url FROM site_photos WHERE unit_id=? AND photo_data IS NOT NULL LIMIT 1",
        (unit_id,),
    ).fetchone()
    conn.close()
    if not row or not row["photo_data"]:
        return ("", 404)

    data = row["photo_data"]
    # Infer content type from the URL extension, default to JPEG.
    url = (row["photo_url"] or "").lower()
    if url.endswith(".png"):
        ctype = "image/png"
    elif url.endswith(".webp"):
        ctype = "image/webp"
    elif url.endswith(".gif"):
        ctype = "image/gif"
    else:
        ctype = "image/jpeg"
    return Response(data, mimetype=ctype, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/search")
def api_search():
    """
    Cross-park search by date range and optional site type.
    ?from=YYYY-MM-DD&to=YYYY-MM-DD[&site_type=Serviced (RV)]
    """
    from_date = request.args.get("from", "")
    to_date   = request.args.get("to",   "")
    site_type = request.args.get("site_type", "")
    if not from_date or not to_date:
        return jsonify({"error": "Provide ?from=YYYY-MM-DD&to=YYYY-MM-DD"}), 400

    conn = get_db()
    type_clause = "AND cs.type = ?" if site_type else ""

    def p(*args):
        return (*args, site_type) if site_type else args

    # Parks with availability in range
    park_rows = conn.execute(f"""
        SELECT p.id, p.name, p.slug,
               COUNT(DISTINCT a.date)        AS avail_dates,
               SUM(a.sites_available)        AS total_sites,
               MIN(a.price)                  AS min_price
        FROM   parks p
        JOIN   campsites cs ON cs.park_id = p.id
        JOIN   availability a ON a.campsite_id = cs.id
        WHERE  a.available = 1
          AND  a.date >= ? AND a.date <= ?
          {type_clause}
        GROUP  BY p.id
        ORDER  BY avail_dates DESC
    """, p(from_date, to_date)).fetchall()

    result = []
    for park in park_rows:
        slug = park["slug"] or ""
        lat, lon = PARK_COORDS.get(slug, (None, None))

        sector_rows = conn.execute(f"""
            SELECT s.id, s.name, s.slug, s.url,
                   COUNT(DISTINCT a.date)   AS avail_dates,
                   SUM(a.sites_available)   AS total_sites,
                   MIN(a.price)             AS min_price
            FROM   sectors s
            JOIN   campsites cs ON cs.sector_id = s.id
            JOIN   availability a ON a.campsite_id = cs.id
            WHERE  s.park_id  = ?
              AND  a.available = 1
              AND  a.date >= ? AND a.date <= ?
              {type_clause}
            GROUP  BY s.id
            ORDER  BY avail_dates DESC
        """, p(park["id"], from_date, to_date)).fetchall()

        result.append({
            "id":          park["id"],
            "name":        park["name"],
            "slug":        slug,
            "lat":         lat,
            "lon":         lon,
            "avail_dates": park["avail_dates"],
            "total_sites": park["total_sites"],
            "min_price":   park["min_price"],
            "sectors":     [dict(s) for s in sector_rows],
        })

    conn.close()
    return jsonify(result)

# ─── Private Campgrounds (OpenStreetMap) ──────────────────────────────────────


# ─── Weather ──────────────────────────────────────────────────────────────────

@app.get("/api/weather")
def api_weather():
    """
    Returns cached weather instantly. Triggers a background refresh if
    the cache is missing or stale (> 1 hour). Stale-while-revalidate.
    ?from=YYYY-MM-DD&to=YYYY-MM-DD  (defaults to today)
    """
    today     = datetime.now().strftime("%Y-%m-%d")
    from_date = request.args.get("from", today)
    to_date   = request.args.get("to",   from_date)
    key       = (from_date, to_date)

    with _weather_lock:
        cached = _weather_mem.get(key)

    if cached:
        stale = (time.time() - cached["ts"]) > _WEATHER_TTL
        if stale:
            _trigger_refresh(from_date, to_date)
        return jsonify(cached["data"])

    # No cache at all — fetch synchronously but in parallel (fast first load)
    _trigger_refresh(from_date, to_date)
    # Wait up to 8s for the background job to finish
    deadline = time.time() + 8
    while time.time() < deadline:
        time.sleep(0.15)
        with _weather_lock:
            if key in _weather_mem:
                return jsonify(_weather_mem[key]["data"])
    return jsonify([])


# ─── Scraper control ──────────────────────────────────────────────────────────

@app.post("/api/scrape")
def api_scrape_start():
    if _scraper_state["running"]:
        return jsonify({"error": "Scraper already running"}), 409
    proc = subprocess.Popen(
        [sys.executable, SCRAPER_PY],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, cwd=SCRAPER_DIR,
    )
    _scraper_state["running"] = True
    _scraper_state["process"] = proc

    def _watch():
        proc.wait()
        _scraper_state["running"] = False
        _scraper_state["process"] = None

    threading.Thread(target=_watch, daemon=True).start()
    return jsonify({"status": "started", "pid": proc.pid})


@app.get("/api/scrape/stream")
def api_scrape_stream():
    def generate():
        proc = _scraper_state.get("process")
        if proc is None:
            yield "data: Scraper is not running.\n\n"
            return
        for line in proc.stdout:
            yield f"data: {line.rstrip()}\n\n"
        yield "data: [done]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/scrape/status")
def api_scrape_status():
    return jsonify({"running": _scraper_state["running"]})


@app.get("/api/cookie-status")
def api_cookie_status():
    """Report SEPAQ cookie freshness so the frontend can warn when expired.

    Field names match the frontend's checkCookieStatus():
      has_cookies, stale, age_hours  (plus extras).
    has_cookies reflects whether the loader actually finds a usable cookie,
    not merely that a file exists.
    """
    has_cookies = bool(_load_sepaq_cookies())

    path = None
    for fpath in [COOKIE_FILE_PW, COOKIE_FILE]:
        if os.path.exists(fpath):
            path = fpath
            break

    age_hours = None
    if path:
        age_hours = (time.time() - os.path.getmtime(path)) / 3600

    # Cloudflare clearance cookies typically expire within ~30-60 min, so warn
    # once they're older than 30 min, or if the last live fetch hit a 401/403.
    stale = bool(has_cookies and (
        _last_live_expired or (age_hours is not None and age_hours > 0.5)
    ))

    return jsonify({
        "has_cookies":        has_cookies,
        "stale":              stale,
        "age_hours":          round(age_hours, 1) if age_hours is not None else None,
        "last_fetch_expired": _last_live_expired,
    })


# ─── Frontend ─────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    print(f"DB path: {os.path.abspath(DB_PATH)}")
    print(f"Open http://localhost:5000 in your browser")
    app.run(debug=True, port=5000)

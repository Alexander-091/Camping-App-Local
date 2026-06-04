"""
SEPAQ Camping Scraper — unit-scoped edition
--------------------------------------------
Scrapes campsite availability from SEPAQ's internal JSON API and stores
the results in a local SQLite database.

Key discovery: SEPAQ's /availabilities endpoint only returns real data when
the session's JSESSIONID_TRANSAC is scoped to a specific unit page.
The scrape flow per sector is:
  1. Visit park page  → establishes JSESSIONID
  2. Visit sector page (server-rendered HTML) → extract unit URLs
  3. Visit first unit page → scopes JSESSIONID_TRANSAC to that product
  4. GET /availabilities → returns full availability data for the sector

Sector slugs are stored in KNOWN_SECTORS (discovered 2026-05-28 via browser).
Park pages render sector links via JavaScript so static HTML parsing is skipped.

Usage:
    python scraper.py              ← full scrape all parks
    python scraper.py --explore    ← test one park (Gaspésie), print structure
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

from curl_cffi import requests

# ─── Config ───────────────────────────────────────────────────────────────────

DB_PATH          = os.path.join(os.path.dirname(__file__), "..", "data", "sepaq.db")
COOKIE_FILE      = os.path.join(os.path.dirname(__file__), "session_cookie.json")
COOKIE_FILE_PW   = os.path.join(os.path.dirname(__file__), "cookies.json")

# Cloudflare cookies scope to .sepaq.com (all subdomains).
# Loading them with the correct domain lets the server refresh __cf_bm
# on the first page hit — loading as www.sepaq.com would let the stale
# value take precedence and the API would silently return [].
CF_DOMAIN_COOKIES = {"cf_clearance", "__cf_bm", "__cflb"}

# Only load these cookies from the saved file.  JSESSIONID / JSESSIONID_TRANSAC
# are tied to the user's browser session; loading them into a curl_cffi session
# causes the SEPAQ server to see a mismatched transaction state and return [].
# Let the server create fresh session cookies for each curl_cffi run.
ALLOWED_SAVED_COOKIES = {"cf_clearance", "__cf_bm", "__cflb"}

BASE_URL           = "https://www.sepaq.com"
CAMPING_PAGE_URL   = f"{BASE_URL}/en/reservation/camping"
INIT_CAMPING_URL   = f"{BASE_URL}/en/reservation/camping/init"
AVAILABILITIES_URL = f"{BASE_URL}/en/reservation/availabilities"

SEPAQ_PARKS = [
    {"name": "Parc national d'Aiguebelle",                              "slug": "parc-national-d-aiguebelle"},
    {"name": "Parc national du Bic",                                    "slug": "parc-national-du-bic"},
    {"name": "Parc national de Frontenac",                              "slug": "parc-national-de-frontenac"},
    {"name": "Parc national de la Gaspésie",                           "slug": "parc-national-de-la-gaspesie"},
    {"name": "Parc national des Grands-Jardins",                        "slug": "parc-national-des-grands-jardins"},
    {"name": "Parc national des Hautes-Gorges-de-la-Rivière-Malbaie",  "slug": "parc-national-des-hautes-gorges-de-la-riviere-malbaie"},
    {"name": "Parc national de la Jacques-Cartier",                     "slug": "parc-national-de-la-jacques-cartier"},
    {"name": "Parc national du Lac-Témiscouata",                       "slug": "parc-national-du-lac-temiscouata"},
    {"name": "Parc national du Mont-Mégantic",                         "slug": "parc-national-du-mont-megantic"},
    {"name": "Parc national du Mont-Orford",                            "slug": "parc-national-du-mont-orford"},
    {"name": "Parc national du Mont-Tremblant",                         "slug": "parc-national-du-mont-tremblant"},
    {"name": "Parc national des Monts-Valin",                           "slug": "parc-national-des-monts-valin"},
    {"name": "Parc national d'Oka",                                     "slug": "parc-national-d-oka"},
    {"name": "Parc national de Plaisance",                              "slug": "parc-national-de-plaisance"},
    {"name": "Parc national de la Yamaska",                             "slug": "parc-national-de-la-yamaska"},
]

# Known sector slugs per park, discovered via browser rendering (JS-rendered, not in static HTML).
# These are used to seed the DB on first run. Populated 2026-05-28 by browsing each park page.
# Parks with no camping reservations (Île-Bonaventure, Lac-Mégantic, Mont-Saint-Bruno,
# Pointe-Taillon, Saguenay) are omitted from SEPAQ_PARKS above.
KNOWN_SECTORS: dict[str, list[str]] = {
    "parc-national-d-aiguebelle":                              ["abijevis", "barlow-espace-vr", "du-sablon", "ojibway"],
    "parc-national-du-bic":                                    ["camping-riviere-du-sud-ouest", "la-coulee", "rioux", "tombolo-camping"],
    "parc-national-de-frontenac":                              ["saint-daniel", "secteur-sud"],
    "parc-national-de-la-gaspesie":                            ["camping-de-la-riviere", "camping-lac-cascapedia", "camping-mont-albert", "camping-mont-jacques-cartier", "la-vallee-espace-vr"],
    "parc-national-des-grands-jardins":                        ["arthabaska", "du-pied-des-monts", "la-roche", "etang-malbaie"],
    "parc-national-des-hautes-gorges-de-la-riviere-malbaie":   ["de-l-equerre", "le-cran", "pin-blanc"],
    "parc-national-de-la-jacques-cartier":                     ["de-la-vallee-espace-vr", "des-alluvions", "grand-duc", "l-escarpement", "la-betulaie", "le-heron", "le-morillon", "les-hirondelles"],
    "parc-national-du-lac-temiscouata":                        ["anse-a-william", "grand-lac-touladi", "grands-pins"],
    "parc-national-du-mont-megantic":                          ["de-franceville", "grande-ourse"],
    "parc-national-du-mont-orford":                            ["lac-fraser", "lac-stukely", "le-vallonnier"],
    "parc-national-du-mont-tremblant":                         ["de-la-sablonniere", "l-assomption", "la-voliere-lac-provost", "lac-des-cypres-et-lac-girondin", "lac-aux-rats-et-lac-lajoie", "lac-cache", "lac-chat", "lac-des-sables", "lac-escalier", "lac-herman", "lac-monroe", "savane-ouest"],
    "parc-national-des-monts-valin":                           ["le-septentrional"],
    "parc-national-d-oka":                                     ["de-l-anse", "la-crete", "le-meandre", "le-refuge", "les-dunes"],
    "parc-national-de-plaisance":                              ["camping-du-parc-de-plaisance", "fer-a-cheval-espace-vr"],
    "parc-national-de-la-yamaska":                             ["camping-du-parc-de-la-yamaska", "le-rivage"],
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Session-level headers — safe for all request types (no XHR markers)
HEADERS = {
    "User-Agent":      UA,
    "Accept-Language": "en-CA,en;q=0.9,fr;q=0.8",
}

# Extra headers for HTML page fetches (park / sector / unit pages)
HTML_HEADERS = {
    **HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# Extra headers for XHR / JSON API calls (/availabilities etc.)
XHR_HEADERS = {
    **HEADERS,
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# ─── Session ──────────────────────────────────────────────────────────────────

def build_session() -> requests.Session:
    """
    Create an HTTP session and load saved cookies.

    Prefers session_cookie.json (written by get_cookie.py — always most
    recent) and falls back to cookies.json (Playwright format).

    Cloudflare cookies are loaded with domain .sepaq.com so the server
    can refresh __cf_bm on the first page hit.
    """
    session = requests.Session(impersonate="chrome120")
    session.headers.update(HEADERS)

    def _load_playwright(raw):
        names = []
        for c in raw:
            name   = c.get("name", "")
            value  = c.get("value", "")
            if name not in ALLOWED_SAVED_COOKIES:
                continue
            domain = c.get("domain", ".sepaq.com")
            if domain and not domain.startswith(".") and "." in domain:
                domain = "." + domain
            session.cookies.set(name, value, domain=domain)
            names.append(name)
        return names

    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE) as f:
            cookies = json.load(f)
        if isinstance(cookies, list):
            names = _load_playwright(cookies)
            print(f"✓ Loaded cookies ({len(names)}): {names}")
        else:
            loaded = {}
            for name, value in cookies.items():
                if name not in ALLOWED_SAVED_COOKIES:
                    continue
                domain = ".sepaq.com" if name in CF_DOMAIN_COOKIES else "www.sepaq.com"
                session.cookies.set(name, value, domain=domain)
                loaded[name] = value
            print(f"✓ Loaded cookies: {list(loaded.keys())}")
    elif os.path.exists(COOKIE_FILE_PW):
        with open(COOKIE_FILE_PW) as f:
            raw = json.load(f)
        if isinstance(raw, list):
            names = _load_playwright(raw)
            print(f"✓ Loaded Playwright cookies ({len(names)}): {names}")
    else:
        print("⚠ No saved cookies — run: python get_cookie.py")

    return session


# ─── Database ─────────────────────────────────────────────────────────────────

def init_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS parks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            slug       TEXT UNIQUE,
            scraped_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sectors (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id    INTEGER REFERENCES parks(id),
            name       TEXT,
            slug       TEXT,
            url        TEXT,
            scraped_at TEXT,
            UNIQUE(park_id, slug)
        );

        -- Each product type (accommodation type) within a sector
        CREATE TABLE IF NOT EXISTS campsites (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id    INTEGER REFERENCES parks(id),
            sector_id  INTEGER REFERENCES sectors(id),
            name       TEXT,
            site_id    TEXT,
            type       TEXT,
            amenities  TEXT,
            scraped_at TEXT,
            UNIQUE(sector_id, site_id)
        );

        -- Daily availability per product per sector
        CREATE TABLE IF NOT EXISTS availability (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            campsite_id     INTEGER REFERENCES campsites(id),
            date            TEXT,
            available       INTEGER,
            sites_available INTEGER DEFAULT 0,
            price           REAL,
            scraped_at      TEXT,
            UNIQUE(campsite_id, date)
        );

        CREATE TABLE IF NOT EXISTS raw_responses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT,
            park_slug   TEXT,
            status_code INTEGER,
            body        TEXT,
            captured_at TEXT
        );

        -- Sub-sectors (boucles) within a sector. For standard parks the sector
        -- itself acts as the boucle (is_sector_level=1).
        CREATE TABLE IF NOT EXISTS boucles (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            sector_id        INTEGER REFERENCES sectors(id),
            name             TEXT,
            slug             TEXT,
            url              TEXT,
            is_sector_level  INTEGER DEFAULT 0,
            map_url          TEXT,
            scraped_at       TEXT,
            UNIQUE(sector_id, slug)
        );

        -- Individual physical campsites discovered from boucle pages.
        CREATE TABLE IF NOT EXISTS sites (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            boucle_id   INTEGER REFERENCES boucles(id),
            unit_id     TEXT,
            site_name   TEXT,
            site_type   TEXT,
            url         TEXT,
            x_pct       REAL,
            y_pct       REAL,
            photo_url   TEXT,
            photo_data  BLOB,
            scraped_at  TEXT,
            UNIQUE(boucle_id, unit_id)
        );

        -- Per-sector per-night availability from HTML boucle page parsing.
        -- One row per (sector, checkin night). checkout = checkin + 1 day.
        -- sites_available = count of green+yellow unit elements on that night.
        -- Used by map filter and search instead of the unreliable /availabilities API.
        CREATE TABLE IF NOT EXISTS range_availability (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sector_id       INTEGER REFERENCES sectors(id),
            checkin         TEXT NOT NULL,
            checkout        TEXT NOT NULL,
            sites_available INTEGER NOT NULL DEFAULT 0,
            scraped_at      TEXT,
            UNIQUE(sector_id, checkin, checkout)
        );
    """)
    conn.commit()

    # Incremental migrations for older DB versions
    migrations = [
        "ALTER TABLE raw_responses ADD COLUMN park_slug TEXT",
        "ALTER TABLE raw_responses ADD COLUMN status_code INTEGER",
        "ALTER TABLE raw_responses ADD COLUMN body TEXT",
        "ALTER TABLE raw_responses ADD COLUMN captured_at TEXT",
        "ALTER TABLE campsites ADD COLUMN sector_id INTEGER REFERENCES sectors(id)",
        "ALTER TABLE availability ADD COLUMN sites_available INTEGER DEFAULT 0",
        "ALTER TABLE boucles ADD COLUMN map_url TEXT",
        "ALTER TABLE sites ADD COLUMN photo_url TEXT",
        "ALTER TABLE sites ADD COLUMN photo_data BLOB",
        "ALTER TABLE sites ADD COLUMN x_pct REAL",
        "ALTER TABLE sites ADD COLUMN y_pct REAL",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    print(f"✓ Database: {os.path.abspath(DB_PATH)}")
    return conn


import re as _re

# ─── HTML parsers ─────────────────────────────────────────────────────────────

class _UnitLinkParser(HTMLParser):
    """
    Extracts unit page URLs from a sector page's HTML.
    SEPAQ sector pages are server-rendered and contain elements like:
        <a id="unit_118880" data-url="/en/reservation/camping/{park}/{sector}/{unit}">
    Visiting one of these unit URLs scopes JSESSIONID_TRANSAC to that product,
    which is required before /availabilities returns real data.
    """

    def __init__(self):
        super().__init__()
        self.unit_urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        a = dict(attrs)
        if not a.get("id", "").startswith("unit_"):
            return
        data_url = a.get("data-url", "")
        if data_url:
            full = f"{BASE_URL}{data_url}" if data_url.startswith("/") else data_url
            self.unit_urls.append(full)


def discover_units(html: str, sector_url: str = "") -> tuple[list[str], bool]:
    """
    Return (unit_urls, per_unit_mode).

    Two strategies:
      1. <a id="unit_*" data-url="..."> — most parks.
         One availabilities call scopes to the full sector; use first unit only.
         per_unit_mode = False

      2. <a href="{sector_path}/{boucle}"> — Oka, Frontenac etc.
         Each boucle is a physical loop with independent availability.
         Must call availabilities for EVERY boucle and aggregate.
         per_unit_mode = True
    """
    import re

    # Strategy 1: id="unit_*" data-url="..."
    parser = _UnitLinkParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    if parser.unit_urls:
        return parser.unit_urls, False

    # Strategy 2: sub-path boucle links
    if sector_url:
        sector_path = sector_url.rstrip("/").replace(BASE_URL, "")
        pattern = re.compile(
            r'href=["\'](' + re.escape(sector_path) + r'/[^"\'/?#]+)["\']'
        )
        matches = pattern.findall(html)
        seen: set[str] = set()
        results: list[str] = []
        for path in matches:
            if path not in seen:
                seen.add(path)
                results.append(f"{BASE_URL}{path}")
        if results:
            return results, True

    return [], False


class _SiteParser(HTMLParser):
    """
    Parses individual campsite unit elements from a rendered boucle/sector page.
    Each element looks like:
        <li style="left:44.81%; top:18.75%">
          <a id="unit_101007" data-url="...le-refuge-103" data-couleur="green"
             data-surnom="Campsite furnished with 3 services..." data-prix="Starting at...">

    The <li> style gives the dot position as a percentage of the map image,
    which we store so the frontend can overlay interactive dots on the map.
    """
    def __init__(self):
        super().__init__()
        self.sites: list[dict] = []
        self._li_x: float | None = None
        self._li_y: float | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li":
            style = a.get("style", "")
            mx = _re.search(r'left\s*:\s*([\d.]+)', style)
            my = _re.search(r'top\s*:\s*([\d.]+)', style)
            self._li_x = float(mx.group(1)) if mx else None
            self._li_y = float(my.group(1)) if my else None
            return
        if tag != "a":
            return
        uid = a.get("id", "")
        if not uid.startswith("unit_"):
            return
        unit_id   = uid[5:]
        data_url  = a.get("data-url", "")
        site_name = data_url.split("/")[-1] if data_url else ""
        price_text = a.get("data-prix", "")
        pm = _re.search(r'\$(\d+(?:\.\d+)?)', price_text)
        self.sites.append({
            "unit_id":   unit_id,
            "site_name": site_name,
            "url":       data_url,
            "colour":    a.get("data-couleur", ""),
            "available": a.get("data-couleur", "") == "green",
            "partial":   a.get("data-couleur", "") == "yellow",
            "site_type": a.get("data-surnom", ""),
            "price":     float(pm.group(1)) if pm else None,
            "x_pct":     self._li_x,
            "y_pct":     self._li_y,
        })
        self._li_x = self._li_y = None


def parse_sites_from_html(html: str) -> list[dict]:
    p = _SiteParser()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.sites


def extract_map_url(html: str) -> str | None:
    """Extract the S3 campground map GIF URL from a boucle page's HTML."""
    m = _re.search(r'src="((?:https?:)?//imagescloud[^"]+/maps/[^"]+\.(?:gif|png|jpg))"', html)
    if not m:
        return None
    url = m.group(1)
    return ("https:" + url) if url.startswith("//") else url


def save_boucle(conn: sqlite3.Connection, sector_id: int,
                slug: str, url: str, is_sector_level: bool,
                map_url: str | None = None) -> int:
    name = slug.replace("-", " ").title()
    conn.execute("""
        INSERT INTO boucles (sector_id, name, slug, url, is_sector_level, map_url, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sector_id, slug) DO UPDATE SET
            url=excluded.url, is_sector_level=excluded.is_sector_level,
            map_url=COALESCE(excluded.map_url, boucles.map_url),
            scraped_at=excluded.scraped_at
    """, (sector_id, name, slug, url, 1 if is_sector_level else 0, map_url, utcnow()))
    conn.commit()
    return conn.execute(
        "SELECT id FROM boucles WHERE sector_id=? AND slug=?", (sector_id, slug)
    ).fetchone()["id"]


def save_sites(conn: sqlite3.Connection, boucle_id: int, sites: list[dict]):
    for s in sites:
        if not s.get("unit_id") or not s.get("site_name"):
            continue
        conn.execute("""
            INSERT INTO sites (boucle_id, unit_id, site_name, site_type, url, x_pct, y_pct, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(boucle_id, unit_id) DO UPDATE SET
                site_name  = excluded.site_name,
                site_type  = excluded.site_type,
                x_pct      = COALESCE(excluded.x_pct, sites.x_pct),
                y_pct      = COALESCE(excluded.y_pct, sites.y_pct),
                scraped_at = excluded.scraped_at
        """, (boucle_id, s["unit_id"], s["site_name"],
              s.get("site_type", ""), s.get("url", ""),
              s.get("x_pct"), s.get("y_pct"), utcnow()))
    conn.commit()


def aggregate_boucle_data(boucle_responses: list[list[dict]]) -> list[dict]:
    """
    Merge multiple per-boucle availabilities responses into one.
    For each (date, product_id) pair, sum nbDisponibleTotal across boucles
    and keep the minimum price.
    """
    merged: dict[str, dict[str, dict]] = {}  # date → pid → {total, price}
    for data in boucle_responses:
        for day in data:
            date = day.get("dateAsStandardString")
            if not date:
                continue
            if date not in merged:
                merged[date] = {}
            for pid, info in day.get("infoParProduit", {}).items():
                nb    = info.get("nbDisponibleTotal", 0)
                price = info.get("prixMinimumNuit")
                if pid not in merged[date]:
                    merged[date][pid] = {"nbDisponibleTotal": 0, "prixMinimumNuit": price}
                merged[date][pid]["nbDisponibleTotal"] += nb
                # Keep lowest non-null price
                if price is not None:
                    cur = merged[date][pid]["prixMinimumNuit"]
                    if cur is None or price < cur:
                        merged[date][pid]["prixMinimumNuit"] = price

    return [
        {"dateAsStandardString": date, "infoParProduit": pids}
        for date, pids in sorted(merged.items())
    ]


# ─── Range availability (HTML-based, accurate) ───────────────────────────────

def upcoming_nights(days: int = 30) -> list[tuple[str, str]]:
    """Return 1-night (checkin, checkout) pairs for the next `days` days."""
    from datetime import date as _d, timedelta as _td
    today = _d.today()
    return [
        ((today + _td(days=i)).isoformat(), (today + _td(days=i + 1)).isoformat())
        for i in range(days)
    ]


def _range_session_worker(args: tuple) -> list[tuple]:
    """
    Thread worker.  Creates one curl_cffi Session for a single boucle and
    iterates through all date pairs sequentially (no shared-session issues).
    Returns list of (sector_id, checkin, checkout, green_count).
    """
    boucle_url, sector_id, park_slug, date_pairs, cf_cookies = args

    sess = requests.Session(impersonate="chrome120")
    sess.headers.update(HEADERS)
    for name, value in cf_cookies.items():
        domain = ".sepaq.com" if name in CF_DOMAIN_COOKIES else "www.sepaq.com"
        sess.cookies.set(name, value, domain=domain)

    # Establish JSESSIONID by visiting the park page
    try:
        sess.get(f"{CAMPING_PAGE_URL}/{park_slug}", headers=HTML_HEADERS, timeout=20)
    except Exception:
        return []

    full_url = boucle_url if boucle_url.startswith("http") else f"{BASE_URL}{boucle_url}"
    results = []

    for checkin, checkout in date_pairs:
        try:
            sess.post(
                f"{BASE_URL}/en/reservation/search",
                data={
                    "arrivalDate":           checkin,
                    "departureDate":         checkout,
                    "booking.arrivalDate":   checkin,
                    "booking.departureDate": checkout,
                    "booking.adults":        "2",
                },
                headers=HTML_HEADERS,
                timeout=15,
            )
            r     = sess.get(full_url, headers=HTML_HEADERS, timeout=15)
            sites = parse_sites_from_html(r.text)
            count = sum(1 for s in sites if s["colour"] in ("green", "yellow"))
        except Exception:
            count = 0
        results.append((sector_id, checkin, checkout, count))

    return results


def scrape_range_availability(conn: sqlite3.Connection) -> None:
    """
    Populate range_availability using the proven boucle-page HTML approach.
    One curl_cffi session per boucle; up to 10 boucles run concurrently.
    Covers the next 30 nights so map filter and search show accurate data.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from collections import defaultdict

    # Load CF cookies (same logic as build_session)
    cf_cookies: dict[str, str] = {}
    for fpath in [COOKIE_FILE, COOKIE_FILE_PW]:
        if not os.path.exists(fpath):
            continue
        with open(fpath) as f:
            raw = json.load(f)
        if isinstance(raw, list):
            cf_cookies = {c["name"]: c["value"] for c in raw
                          if c.get("name") in ALLOWED_SAVED_COOKIES}
        elif isinstance(raw, dict):
            cf_cookies = {k: v for k, v in raw.items() if k in ALLOWED_SAVED_COOKIES}
        if cf_cookies:
            break

    if not cf_cookies:
        print("  ⚠ No cookies found — run: python get_cookie.py")
        return

    # Warn if cookies are stale (> 23 hours old)
    for fpath in [COOKIE_FILE, COOKIE_FILE_PW]:
        if os.path.exists(fpath):
            import time as _time
            age_h = (_time.time() - os.path.getmtime(fpath)) / 3600
            if age_h > 23:
                print(f"  ⚠ Cookies are {age_h:.1f}h old — they may have expired.")
                print(f"    Run: python get_cookie.py   then retry.")
                print(f"    Proceeding anyway; requests will fail if cf_clearance is stale.")
            break

    boucles = conn.execute("""
        SELECT b.url, b.sector_id, p.slug AS park_slug
        FROM   boucles b
        JOIN   sectors s ON s.id = b.sector_id
        JOIN   parks   p ON p.id = s.park_id
        WHERE  b.url IS NOT NULL
    """).fetchall()

    if not boucles:
        print("  ⚠ No boucles in DB — run full scrape first")
        return

    date_pairs = upcoming_nights(30)
    print(f"\n  Range availability: {len(boucles)} boucles × {len(date_pairs)} nights"
          f"  (10 concurrent workers)")

    conn.execute("DELETE FROM range_availability")
    conn.commit()

    worker_args = [
        (b["url"], b["sector_id"], b["park_slug"], date_pairs, cf_cookies)
        for b in boucles
    ]

    totals: dict[tuple, int] = defaultdict(int)
    done = 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(_range_session_worker, a): a for a in worker_args}
        for f in as_completed(futs):
            done += 1
            try:
                for sec_id, checkin, checkout, count in f.result():
                    totals[(sec_id, checkin, checkout)] += count
            except Exception:
                pass
            if done % 10 == 0 or done == len(futs):
                print(f"    {done}/{len(futs)} boucles done")

    for (sec_id, checkin, checkout), count in totals.items():
        conn.execute("""
            INSERT INTO range_availability
                (sector_id, checkin, checkout, sites_available, scraped_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sector_id, checkin, checkout) DO UPDATE SET
                sites_available = excluded.sites_available,
                scraped_at      = excluded.scraped_at
        """, (sec_id, checkin, checkout, count, utcnow()))
    conn.commit()
    print(f"  ✓ Stored {len(totals)} sector-night records in range_availability")


def sectors_for_park(park_slug: str, conn: sqlite3.Connection) -> list[dict]:
    """
    Return sector dicts for a park.  Checks DB first; falls back to KNOWN_SECTORS.
    Each dict has keys: slug, name, url.
    """
    rows = conn.execute(
        "SELECT slug, name, url FROM sectors WHERE park_id = "
        "(SELECT id FROM parks WHERE slug=?)", (park_slug,)
    ).fetchall()
    if rows:
        return [{"slug": r["slug"], "name": r["name"], "url": r["url"]} for r in rows]

    # Seed from KNOWN_SECTORS
    slugs = KNOWN_SECTORS.get(park_slug, [])
    return [
        {
            "slug": s,
            "name": s.replace("-", " ").title(),
            "url":  f"{CAMPING_PAGE_URL}/{park_slug}/{s}",
        }
        for s in slugs
    ]


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def visit_park(session: requests.Session, park_slug: str) -> bool:
    """GET the park camping page to establish a base JSESSIONID."""
    try:
        session.get(CAMPING_PAGE_URL, headers={**HTML_HEADERS, "Referer": BASE_URL}, timeout=15)
    except Exception:
        pass
    url = f"{CAMPING_PAGE_URL}/{park_slug}"
    try:
        r = session.get(url, headers={**HTML_HEADERS, "Referer": CAMPING_PAGE_URL}, timeout=15)
        return r.status_code == 200
    except Exception as e:
        print(f"  ⚠ Park page failed: {e}")
        return False


def fetch_sector_html(session: requests.Session, sector_url: str, park_url: str) -> str | None:
    """GET the sector page and return its HTML (server-rendered, contains unit links)."""
    try:
        r = session.get(sector_url, headers={**HTML_HEADERS, "Referer": park_url}, timeout=15)
        if r.status_code != 200:
            print(f"  ⚠ Sector page {r.status_code}: {sector_url}")
            return None
        return r.text or r.content.decode("utf-8", errors="replace") or None
    except Exception as e:
        print(f"  ⚠ Sector page failed: {e}")
        return None


def visit_unit(session: requests.Session, unit_url: str, sector_url: str) -> bool:
    """
    Visit a unit page to scope JSESSIONID_TRANSAC to that product.
    This is the critical step — /availabilities returns [] until this is done.
    """
    try:
        r = session.get(unit_url, headers={**HTML_HEADERS, "Referer": sector_url}, timeout=15)
        return r.status_code == 200
    except Exception as e:
        print(f"  ⚠ Unit visit failed: {e}")
        return False


def fetch_availabilities(session: requests.Session, context_url: str = "") -> list | None:
    """
    Fetch availability JSON for the current session context.
    context_url is used as the Referer so the server knows which park we're on.
    Returns the list or None on failure.
    """
    referer = context_url or CAMPING_PAGE_URL
    try:
        r = session.get(AVAILABILITIES_URL, headers={**XHR_HEADERS, "Referer": referer}, timeout=15)
        ct = r.headers.get("content-type", "")
        print(f"  Avail       → {r.status_code}  ({len(r.content)} bytes)  ct={ct!r}")

        if r.status_code != 200:
            print(f"  ⚠ Unexpected status: {r.text[:200]}")
            return None

        if not r.content:
            print(f"  ⚠ Empty response body — session likely not scoped to a park.")
            return None

        # Try JSON regardless of content-type (some Java backends omit it)
        try:
            data = r.json()
        except Exception:
            print(f"  ⚠ Response is not JSON. First 200 bytes: {r.text[:200]!r}")
            return None

        if isinstance(data, list) and len(data) == 0:
            print(f"  ⚠ API returned [] — session has no park context.")
            print(f"     If cookies are fresh, the init endpoint may need different parameters.")
            return None

        return data

    except Exception as e:
        print(f"  ⚠ Request failed: {e}")
        return None


# ─── DB write helpers ─────────────────────────────────────────────────────────

def save_park(conn: sqlite3.Connection, park: dict) -> int:
    conn.execute("""
        INSERT INTO parks (name, slug, scraped_at)
        VALUES (:name, :slug, :now)
        ON CONFLICT(slug) DO UPDATE SET
            name = excluded.name, scraped_at = excluded.scraped_at
    """, {**park, "now": utcnow()})
    conn.commit()
    return conn.execute("SELECT id FROM parks WHERE slug=?", (park["slug"],)).fetchone()["id"]


def save_sector(conn: sqlite3.Connection, park_id: int, sector: dict) -> int:
    conn.execute("""
        INSERT INTO sectors (park_id, name, slug, url, scraped_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(park_id, slug) DO UPDATE SET
            name = excluded.name, url = excluded.url,
            scraped_at = excluded.scraped_at
    """, (park_id, sector["name"], sector["slug"], sector["url"], utcnow()))
    conn.commit()
    return conn.execute(
        "SELECT id FROM sectors WHERE park_id=? AND slug=?",
        (park_id, sector["slug"])
    ).fetchone()["id"]


def save_availability_data(
    conn: sqlite3.Connection,
    park_id: int,
    sector_id: int | None,
    data: list,
) -> int:
    """
    Parse infoParProduit from the availabilities response and upsert
    campsites + availability rows.  Returns number of rows written.
    """
    if not isinstance(data, list):
        return 0

    # Collect all product IDs across all days
    product_ids: set[str] = set()
    for day in data:
        product_ids.update(str(k) for k in day.get("infoParProduit", {}).keys())

    if not product_ids:
        return 0

    # Known product ID → human-readable name/type
    PRODUCT_NAMES = {
        "76320": "Tent / Standard",
        "76321": "Serviced (Premium)",
        "76322": "Serviced (RV)",
        "76324": "Rustic",
        "76326": "Prêt-à-camper",
    }

    # Upsert one campsite row per product ID
    for pid in product_ids:
        label = PRODUCT_NAMES.get(str(pid), f"Product {pid}")
        conn.execute("""
            INSERT INTO campsites (park_id, sector_id, name, site_id, type, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(sector_id, site_id) DO UPDATE SET
                name       = excluded.name,
                type       = excluded.type,
                scraped_at = excluded.scraped_at
        """, (park_id, sector_id, label, pid, label, utcnow()))
    conn.commit()

    # Map product_id → campsite DB id (filter by sector if we have one)
    if sector_id is not None:
        rows = conn.execute(
            "SELECT id, site_id FROM campsites WHERE park_id=? AND sector_id=?",
            (park_id, sector_id)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, site_id FROM campsites WHERE park_id=?", (park_id,)
        ).fetchall()
    campsite_map = {r["site_id"]: r["id"] for r in rows}

    # Write one availability row per product per date
    records = 0
    for day in data:
        date_str = day.get("dateAsStandardString")
        if not date_str:
            continue
        for pid, info in day.get("infoParProduit", {}).items():
            cid = campsite_map.get(str(pid))
            if cid is None:
                continue
            sites_avail = info.get("nbDisponibleTotal", 0)
            conn.execute("""
                INSERT INTO availability
                    (campsite_id, date, available, sites_available, price, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(campsite_id, date) DO UPDATE SET
                    available       = excluded.available,
                    sites_available = excluded.sites_available,
                    price           = excluded.price,
                    scraped_at      = excluded.scraped_at
            """, (
                cid, date_str,
                1 if sites_avail > 0 else 0,
                sites_avail,
                info.get("prixMinimumNuit"),
                utcnow(),
            ))
            records += 1

    conn.commit()
    return records


# ─── Explore mode ─────────────────────────────────────────────────────────────

def explore(session: requests.Session, conn: sqlite3.Connection):
    """
    Test one park (Gaspésie): visit park → first sector → first unit → fetch availabilities.
    """
    test_park = SEPAQ_PARKS[3]  # Gaspésie
    print(f"\n🔍 Explore mode — {test_park['name']}\n")

    print("1. Visiting park page...")
    if not visit_park(session, test_park["slug"]):
        print("  ❌ Park page failed.")
        return

    print("\n2. Loading sectors...")
    sectors = sectors_for_park(test_park["slug"], conn)
    if not sectors:
        print("  ❌ No sectors found.")
        return
    sector = sectors[0]
    park_url   = f"{CAMPING_PAGE_URL}/{test_park['slug']}"
    sector_url = sector["url"]
    print(f"  Using sector: {sector['name']}  ({sector_url})")

    print("\n3. Fetching sector page for unit links...")
    html = fetch_sector_html(session, sector_url, park_url)
    if html is None:
        print("  ❌ Sector page failed.")
        return

    units, per_unit_mode = discover_units(html, sector_url)
    if not units:
        print("  ❌ No unit links found in sector HTML.")
        return
    mode_label = "per-boucle" if per_unit_mode else "standard"
    print(f"  Found {len(units)} unit(s), mode={mode_label}. Using first: {units[0]}")

    print("\n4. Visiting unit page to scope session...")
    if not visit_unit(session, units[0], sector_url):
        print("  ❌ Unit visit failed.")
        return

    print("\n5. Fetching availabilities...")
    data = fetch_availabilities(session, units[0])
    if data is None:
        return

    conn.execute(
        "INSERT INTO raw_responses (url, park_slug, status_code, body, captured_at)"
        " VALUES (?,?,?,?,?)",
        (AVAILABILITIES_URL, test_park["slug"], 200, json.dumps(data), utcnow())
    )
    conn.commit()

    print(f"\n✅ Response: {len(data)} day records")
    if data:
        products = {}
        for day in data:
            for pid, info in day.get("infoParProduit", {}).items():
                if pid not in products:
                    products[pid] = info
        print(f"   Products (accommodation types): {len(products)}")
        for pid, info in products.items():
            print(f"   • Product {pid}: "
                  f"${info.get('prixMinimumNuit', '?')}/night, "
                  f"capacity {info.get('nbCapaciteMin')}–{info.get('nbCapaciteMax')} people")
        avail_dates = [
            d["dateAsStandardString"]
            for d in data
            if d.get("available") and d.get("dateAsStandardString")
        ]
        print(f"   Available dates (sample): {avail_dates[:5]}")

    print(f"\n💾 Raw response saved. Run: python inspect_db.py")


# ─── Full scrape ──────────────────────────────────────────────────────────────

def scrape(session: requests.Session, conn: sqlite3.Connection):
    print(f"\n🏕  Scraping {len(SEPAQ_PARKS)} parks...\n")

    print("  Clearing old campsite and availability data...")
    conn.execute("DELETE FROM availability")
    conn.execute("DELETE FROM campsites")
    conn.execute("DELETE FROM sectors")
    conn.commit()

    parks_done = 0
    total_rows = 0

    for park in SEPAQ_PARKS:
        print(f"\n{'─'*50}")
        print(f"Park: {park['name']}")

        park_url = f"{CAMPING_PAGE_URL}/{park['slug']}"

        # Establish a base JSESSIONID by visiting the park page
        if not visit_park(session, park["slug"]):
            print(f"  ⚠ Skipping (park page failed — cookies may need refresh)")
            continue

        park_id = save_park(conn, park)

        sectors = sectors_for_park(park["slug"], conn)
        if not sectors:
            print(f"  ⚠ No sectors known for this park — skipping")
            continue

        print(f"  Sectors ({len(sectors)}): {[s['slug'] for s in sectors]}")

        for sector in sectors:
            sector_url = sector["url"]
            print(f"\n  → {sector['name']}")
            sector_id = save_sector(conn, park_id, sector)

            # Fetch sector HTML and extract a unit URL
            html = fetch_sector_html(session, sector_url, park_url)
            if html is None:
                print(f"    ⚠ Skipping sector (sector page failed)")
                continue

            units, per_unit_mode = discover_units(html, sector_url)
            if not units:
                print(f"    ⚠ No unit links found in sector HTML — skipping")
                continue

            if per_unit_mode:
                # Boucle-style parks (Oka, Frontenac).
                # Discover sites from each boucle page, then aggregate legacy stats.
                print(f"    Per-boucle mode: {len(units)} boucles")
                boucle_responses = []
                for unit_url in units:
                    boucle_slug = unit_url.split("/")[-1]
                    boucle_html = fetch_sector_html(session, unit_url, sector_url)
                    boucle_map  = extract_map_url(boucle_html) if boucle_html else None
                    boucle_id   = save_boucle(conn, sector_id, boucle_slug, unit_url, False, map_url=boucle_map)
                    if boucle_html:
                        boucle_sites = parse_sites_from_html(boucle_html)
                        save_sites(conn, boucle_id, boucle_sites)
                        print(f"      ✓ {boucle_slug}: {len(boucle_sites)} sites"
                              + (f"  [map ✓]" if boucle_map else ""))
                    if not visit_unit(session, unit_url, sector_url):
                        print(f"      ⚠ Boucle visit failed: {boucle_slug}")
                        continue
                    boucle_data = fetch_availabilities(session, unit_url)
                    if boucle_data:
                        boucle_responses.append(boucle_data)

                if not boucle_responses:
                    print(f"    ⚠ No boucle data — skipping sector")
                    continue

                combined = aggregate_boucle_data(boucle_responses)
                conn.execute(
                    "INSERT INTO raw_responses (url, park_slug, status_code, body, captured_at)"
                    " VALUES (?,?,?,?,?)",
                    (AVAILABILITIES_URL, park["slug"], 200, json.dumps(combined), utcnow())
                )
                conn.commit()
                n = save_availability_data(conn, park_id, sector_id, combined)

            else:
                # Standard parks: sector page has unit_ links directly.
                sector_map = extract_map_url(html)
                boucle_id  = save_boucle(conn, sector_id, sector["slug"], sector_url, True, map_url=sector_map)
                sec_sites  = parse_sites_from_html(html)
                save_sites(conn, boucle_id, sec_sites)
                print(f"    Sites discovered: {len(sec_sites)}"
                      + (f"  [map ✓]" if sector_map else ""))

                if not visit_unit(session, units[0], sector_url):
                    print(f"    ⚠ Skipping sector (unit visit failed)")
                    continue

                data = fetch_availabilities(session, units[0])
                if data is None:
                    continue

                conn.execute(
                    "INSERT INTO raw_responses (url, park_slug, status_code, body, captured_at)"
                    " VALUES (?,?,?,?,?)",
                    (AVAILABILITIES_URL, park["slug"], 200, json.dumps(data), utcnow())
                )
                conn.commit()
                n = save_availability_data(conn, park_id, sector_id, data)

            total_rows += n
            print(f"    ✓ {n} availability rows")

        parks_done += 1

    print(f"\n{'='*50}")
    print(f"Discovery scrape complete!")
    print(f"   Parks scraped:  {parks_done} / {len(SEPAQ_PARKS)}")
    print(f"   Rows written:   {total_rows}")

    print(f"\n{'─'*50}")
    print("Phase 2: range availability (HTML-based, accurate)...")
    scrape_range_availability(conn)
    print(f"\nRun: python inspect_db.py  to review what was captured")


# --- Entry point ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEPAQ Camping Scraper")
    parser.add_argument("--explore",    action="store_true",
                        help="Test one park and print structure (good first step)")
    parser.add_argument("--dates-only", action="store_true",
                        help="Skip discovery; just refresh range_availability for the next 30 nights")
    args = parser.parse_args()

    print("=" * 50)
    print("  SEPAQ Camping Scraper")
    print("=" * 50)

    session = build_session()
    conn    = init_db()

    if args.explore:
        explore(session, conn)
    elif args.dates_only:
        print("\nDates-only mode — refreshing range availability...")
        scrape_range_availability(conn)
    else:
        scrape(session, conn)

    conn.close()

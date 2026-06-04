"""
scrape_photos.py — Download per-site campsite photos into a SEPARATE photos.db.

Why separate: keeps the main sepaq.db (parks/sectors/boucles/sites + availability)
lean and safe — a long photo scrape can't corrupt your availability data.

How it works:
  - Reads every site (unit_id, url) from data/sepaq.db.
  - Skips unit_ids already stored in data/photos.db  → fully resumable.
  - For each site: curl_cffi GET the site page (full URL incl. boucle segment,
    which is what actually serves the gallery), regex the first
    `galleriemedia/.../*.jpg`, download the bytes, store URL + blob.
  - Polite throttling + retries. Progress every 25 sites. Stop/resume anytime.

Usage:
    python scrape_photos.py                # scrape all remaining
    python scrape_photos.py --limit 200    # do a first 200 as a test batch
    python scrape_photos.py --full-res     # prefer full-size over thumbnail

Requires fresh SEPAQ cookies (scraper/session_cookie.json) — Cloudflare cookies
expire fast, so if you see lots of 403s, re-run scraper/get_cookie.py.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time

from curl_cffi import requests

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
SEPAQ_DB    = os.path.join(BASE_DIR, "data", "sepaq.db")
PHOTOS_DB   = os.path.join(BASE_DIR, "data", "photos.db")
SCRAPER_DIR = os.path.join(BASE_DIR, "scraper")
COOKIE_FILES = [
    os.path.join(SCRAPER_DIR, "session_cookie.json"),
    os.path.join(SCRAPER_DIR, "cookies.json"),
]
SEPAQ_BASE = "https://www.sepaq.com"
CF_DOMAIN_COOKIES = {"cf_clearance", "__cf_bm", "__cflb"}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept-Language": "en-CA,en;q=0.9,fr;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# Gallery photo URLs look like:
#   //imagescloud.s3-accelerate.amazonaws.com/images/galleriemedia/24/05/16/trlacprovost7a_<id>.jpg
# The leading prefix can be tr (thumbnail-rect), th (thumbnail), or none (full).
_PHOTO_RE = re.compile(
    r'(?:https?:)?//[^"\'\s]*?/images/galleriemedia/[^"\'\s]+\.(?:jpg|jpeg|png|webp)',
    re.I,
)


def load_cookies() -> dict:
    for fpath in COOKIE_FILES:
        if not os.path.exists(fpath):
            continue
        with open(fpath, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, list):
            return {c["name"]: c["value"] for c in raw}
        if isinstance(raw, dict):
            return raw
    return {}


def make_session(cookies: dict) -> requests.Session:
    sess = requests.Session(impersonate="chrome120")
    sess.headers.update(HEADERS)
    for name, value in cookies.items():
        domain = ".sepaq.com" if name in CF_DOMAIN_COOKIES else "www.sepaq.com"
        sess.cookies.set(name, value, domain=domain)
    return sess


def ensure_photos_db() -> sqlite3.Connection:
    conn = sqlite3.connect(PHOTOS_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS site_photos (
            unit_id     TEXT PRIMARY KEY,
            photo_url   TEXT,
            photo_data  BLOB,
            fetched_at  TEXT
        )
    """)
    conn.commit()
    return conn


def _full_res(url: str) -> str:
    """Prefer a full-size image by dropping a th/tr thumbnail prefix on the filename.

    SEPAQ filenames are like '.../trlacprovost7a_<id>.jpg'. Stripping the leading
    'tr'/'th' usually yields the full-resolution variant. If that 404s, the caller
    falls back to the original thumbnail URL.
    """
    return re.sub(r'/(?:tr|th)([^/]+\.(?:jpg|jpeg|png|webp))$', r'/\1', url, flags=re.I)


def fetch_site_photo(sess: requests.Session, site_url: str, full_res: bool):
    """Return (photo_url, image_bytes) for a site, or (None, None)."""
    full = site_url if site_url.startswith("http") else SEPAQ_BASE + site_url
    r = sess.get(full, timeout=20)
    if r.status_code in (401, 403):
        raise PermissionError("403/401 — cookies likely expired")
    if r.status_code != 200:
        return None, None

    m = _PHOTO_RE.search(r.text)
    if not m:
        return None, None
    photo_url = m.group(0)
    if photo_url.startswith("//"):
        photo_url = "https:" + photo_url

    candidates = []
    if full_res:
        candidates.append(_full_res(photo_url))
    candidates.append(photo_url)

    for cand in candidates:
        try:
            ir = sess.get(cand, timeout=20)
            if ir.status_code == 200 and ir.content:
                return cand, ir.content
        except Exception:
            continue
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max sites this run (0 = all)")
    ap.add_argument("--full-res", action="store_true", help="prefer full-size over thumbnail")
    ap.add_argument("--delay", type=float, default=0.4, help="seconds between requests")
    ap.add_argument("--skip-empty", action="store_true",
                    help="treat any existing row as done (don't retry sites recorded with no image)")
    args = ap.parse_args()

    cookies = load_cookies()
    if not cookies.get("cf_clearance"):
        print("⚠ No cf_clearance cookie. Run: python scraper/get_cookie.py")
        sys.exit(1)

    src = sqlite3.connect(SEPAQ_DB)
    src.row_factory = sqlite3.Row
    sites = src.execute(
        "SELECT unit_id, url FROM sites WHERE url IS NOT NULL AND url != ''"
    ).fetchall()
    src.close()

    pdb = ensure_photos_db()
    # "done" = has an ACTUAL image blob. Rows that exist but have no blob were
    # failed/interrupted attempts (e.g. cookies expired mid-run) and should be
    # retried — unless --skip-empty is given (treat existing rows as final).
    if args.skip_empty:
        done = {r[0] for r in pdb.execute("SELECT unit_id FROM site_photos")}
    else:
        done = {r[0] for r in pdb.execute(
            "SELECT unit_id FROM site_photos WHERE photo_data IS NOT NULL")}
    todo = [s for s in sites if str(s["unit_id"]) not in done]
    if args.limit:
        todo = todo[: args.limit]

    have_blob = pdb.execute(
        "SELECT count(*) FROM site_photos WHERE photo_data IS NOT NULL").fetchone()[0]
    print(f"Sites total: {len(sites)} | with image: {have_blob} | "
          f"to do this run: {len(todo)}"
          + ("  (retrying rows with no image)" if not args.skip_empty else ""))
    if not todo:
        print("Nothing to do — all sites already have photos.")
        return

    sess = make_session(cookies)
    ok = miss = fail = 0
    consecutive_403 = 0

    for i, s in enumerate(todo, 1):
        uid = str(s["unit_id"])
        try:
            purl, data = fetch_site_photo(sess, s["url"], args.full_res)
            consecutive_403 = 0
            if data:
                pdb.execute(
                    "INSERT OR REPLACE INTO site_photos (unit_id, photo_url, photo_data, fetched_at)"
                    " VALUES (?,?,?,datetime('now'))",
                    (uid, purl, data),
                )
                ok += 1
            else:
                # record the miss with no blob so we don't retry endlessly
                pdb.execute(
                    "INSERT OR REPLACE INTO site_photos (unit_id, photo_url, photo_data, fetched_at)"
                    " VALUES (?,?,?,datetime('now'))",
                    (uid, None, None),
                )
                miss += 1
        except PermissionError:
            consecutive_403 += 1
            fail += 1
            # Brief Cloudflare hiccups happen; only give up after many in a row,
            # and back off a little to let a transient block clear.
            if consecutive_403 >= 15:
                pdb.commit()
                print(f"\n⚠ {consecutive_403} consecutive 403s — cookies have likely expired. "
                      "Re-run scraper/get_cookie.py and run this script again (it resumes).")
                break
            time.sleep(1.5)
        except Exception:
            # Transient (timeout / connection reset): don't record, so it retries
            # on the next run. Reset the 403 streak since this wasn't a block.
            consecutive_403 = 0
            fail += 1

        if i % 10 == 0:
            pdb.commit()
            print(f"  {i}/{len(todo)}  ok={ok} miss={miss} fail={fail}")
        time.sleep(args.delay)

    pdb.commit()
    pdb.close()
    print(f"\nDone this run. stored={ok}  no-photo={miss}  errors={fail}")
    print(f"photos.db: {PHOTOS_DB}")


if __name__ == "__main__":
    main()

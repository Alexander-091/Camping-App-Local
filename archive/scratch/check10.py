import sys
sys.path.insert(0, "app")
import app as A
import sqlite3
from datetime import date, timedelta

frm = date.today().isoformat()
to  = (date.today()+timedelta(days=2)).isoformat()

cookies = A._load_sepaq_cookies()
hdr = A._build_cookie_header(cookies)
print("cookie names:", list(cookies))

opener = A._make_sepaq_opener()
ok = A._set_session_dates(frm, to, hdr, opener)
print("set_session_dates ok:", ok)
# did the jar capture a session cookie?
proc = opener.handlers
jar = None
for h in opener.handlers:
    if hasattr(h, "cookiejar"):
        jar = h.cookiejar
print("jar cookies after set-dates:", [c.name for c in jar] if jar else "NO JAR")

# inspect sector 41's boucle rows exactly as the app sees them
con = sqlite3.connect("data/sepaq.db"); con.row_factory = sqlite3.Row
rows = con.execute("SELECT id, slug, url FROM boucles WHERE sector_id=24").fetchall()
for r in rows:
    print("boucle:", dict(r))
    sites = A._parse_boucle_page(r["url"], hdr, opener)
    print("   parsed sites:", len(sites))

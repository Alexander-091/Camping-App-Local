import urllib.request, urllib.parse, http.cookiejar, json, re, sqlite3

SEPAQ_BASE = "https://www.sepaq.com"
HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"}

# load the fresh cookies (Cloudflare ones)
raw = json.load(open("scraper/session_cookie.json", encoding="utf-8"))
cookies = raw if isinstance(raw, dict) else {c["name"]: c["value"] for c in raw}
cookie_hdr = "; ".join(f"{k}={v}" for k,v in cookies.items())
print("loaded cookie names:", list(cookies))

url = sqlite3.connect("data/sepaq.db").execute("SELECT url FROM boucles WHERE id=41").fetchone()[0]
if url.startswith("/"): url = SEPAQ_BASE + url
from datetime import date, timedelta
frm = date.today().isoformat(); to = (date.today()+timedelta(days=2)).isoformat()

# build an opener WITH a cookie jar, seeded with the Cloudflare cookies
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def req(u, data=None):
    r = urllib.request.Request(u, data=data, headers={**HDRS, "Cookie": cookie_hdr,
        **({"Content-Type":"application/x-www-form-urlencoded"} if data else {})})
    return opener.open(r, timeout=20)

# 1. set dates (jar captures any JSESSIONID returned)
body = urllib.parse.urlencode({"arrivalDate":frm,"departureDate":to,
    "booking.arrivalDate":frm,"booking.departureDate":to,"booking.adults":"2"}).encode()
resp = req(SEPAQ_BASE+"/en/reservation/search", body)
print("set-dates status:", resp.status)
print("jar now holds:", [c.name for c in jar])

# 2. fetch the boucle page through the SAME opener (jar cookies auto-sent)
html = req(url).read().decode("utf-8","replace")
print("page length:", len(html))
print("unit_ anchors found:", len(re.findall(r'id="unit_', html)))
print("data-couleur found:", len(re.findall(r'data-couleur', html)))

import json, re
from curl_cffi import requests
raw=json.load(open("scraper/session_cookie.json",encoding="utf-8"))
cookies=raw if isinstance(raw,dict) else {c["name"]:c["value"] for c in raw}
s=requests.Session(impersonate="chrome120")
for k,v in cookies.items():
    s.cookies.set(k,v,domain=".sepaq.com" if k in ("cf_clearance","__cf_bm","__cflb") else "www.sepaq.com")
url="https://www.sepaq.com/en/reservation/camping/parc-national-du-mont-tremblant/la-voliere-lac-provost/lac-provost-7"
html=s.get(url,timeout=20).text
open("site_page.html","w",encoding="utf-8").write(html)
print("saved site_page.html, len", len(html))
# every URL-ish token
urls=set(re.findall(r'https?://[^"\'\s<>()]+', html))
img=[u for u in urls if re.search(r'\.(jpg|jpeg|png|webp|gif)', u, re.I)]
print("--- all image-extension URLs ---")
for u in sorted(img): print(u[:140])
print("--- any 'galerie'/'gallery'/'photo'/'media'/'carousel' mentions ---")
for kw in ["galerie","gallery","photo","media","carousel","slide","vignette","figure"]:
    hits=[m.start() for m in re.finditer(kw, html, re.I)]
    if hits: print(f"  {kw}: {len(hits)} hits; first context:", html[hits[0]-40:hits[0]+80].replace(chr(10)," "))

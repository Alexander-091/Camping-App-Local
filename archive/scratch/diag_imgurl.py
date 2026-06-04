import sys; sys.path.insert(0,"scraper")
import json, re
from curl_cffi import requests
raw=json.load(open("scraper/session_cookie.json",encoding="utf-8"))
cookies=raw if isinstance(raw,dict) else {c["name"]:c["value"] for c in raw}
s=requests.Session(impersonate="chrome120")
for k,v in cookies.items():
    s.cookies.set(k,v,domain=".sepaq.com" if k in ("cf_clearance","__cf_bm","__cflb") else "www.sepaq.com")
url="https://www.sepaq.com/en/reservation/camping/parc-national-du-mont-tremblant/la-voliere-lac-provost/lac-provost-7"
r=s.get(url,timeout=20)
print("status",r.status_code,"len",len(r.text))
# find image-ish URLs
imgs=re.findall(r'(?:src|data-src|href|content)="([^"]*\.(?:jpg|jpeg|png|webp)[^"]*)"', r.text, re.I)
seen=set()
for u in imgs:
    if u not in seen:
        seen.add(u); print(u)

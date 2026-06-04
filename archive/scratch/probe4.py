import json, re
from curl_cffi import requests
raw=json.load(open("scraper/session_cookie.json",encoding="utf-8"))
cookies=raw if isinstance(raw,dict) else {c["name"]:c["value"] for c in raw}
s=requests.Session(impersonate="chrome120")
for k,v in cookies.items():
    s.cookies.set(k,v,domain=".sepaq.com" if k in ("cf_clearance","__cf_bm","__cflb") else "www.sepaq.com")
url="https://www.sepaq.com/en/reservation/camping/parc-national-du-mont-tremblant/la-voliere-lac-provost/aigle-pecheur/lac-provost-7"
r=s.get(url,timeout=20); html=r.text
open("site_page2.html","w",encoding="utf-8").write(html)
print("status",r.status_code,"len",len(html))
print("galleriemedia in html:", "galleriemedia" in html)
for m in set(re.findall(r'[a-z0-9/]*galleriemedia[^"\'\\\s]*', html)): print(m[:140])

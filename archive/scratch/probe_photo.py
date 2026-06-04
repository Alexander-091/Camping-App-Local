import json, re
from curl_cffi import requests
raw=json.load(open("scraper/session_cookie.json",encoding="utf-8"))
cookies=raw if isinstance(raw,dict) else {c["name"]:c["value"] for c in raw}
s=requests.Session(impersonate="chrome120")
for k,v in cookies.items():
    s.cookies.set(k,v,domain=".sepaq.com" if k in ("cf_clearance","__cf_bm","__cflb") else "www.sepaq.com")
url="https://www.sepaq.com/en/reservation/camping/parc-national-du-mont-tremblant/la-voliere-lac-provost/lac-provost-7"
r=s.get(url,timeout=20)
html=r.text
print("status",r.status_code,"len",len(html))
# broad hunt for any image-ish / cloud asset references
pats = [r'imagescloud[^"\')\s]+', r's3[.-][^"\')\s]*\.(?:jpg|jpeg|png|webp)[^"\')\s]*',
        r'background-image:\s*url\([^)]+\)', r'"[^"]*\.(?:jpg|jpeg|webp)[^"]*"',
        r'data-[a-z-]*photo[^=]*="[^"]+"', r'photo[A-Za-z]*"\s*:\s*"[^"]+"']
seen=set()
for p in pats:
    for m in re.findall(p, html, re.I):
        if m not in seen and "favicon" not in m and "apple-touch" not in m:
            seen.add(m); print(m[:160])
# also dump any <script> JSON that mentions "image" or "photo"
for m in re.findall(r'(\{[^{}]*(?:image|photo|media)[^{}]*\})', html, re.I)[:5]:
    print("JSON?:", m[:200])

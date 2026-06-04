import sys; sys.path.insert(0,"app")
import app as A, urllib.request, traceback, time, os
cookies=A._load_sepaq_cookies()
hdr=A._build_cookie_header(cookies)
age=(time.time()-os.path.getmtime("scraper/session_cookie.json"))/60
print("cookie file age: %.1f min" % age)
url="https://www.sepaq.com/en/reservation/camping/parc-national-de-la-jacques-cartier/des-alluvions"
try:
    r=urllib.request.Request(url,headers={**A._LIVE_HEADERS,"Cookie":hdr})
    resp=urllib.request.urlopen(r,timeout=20)
    print("plain GET status:",resp.status,"len:",len(resp.read()))
except Exception:
    traceback.print_exc()

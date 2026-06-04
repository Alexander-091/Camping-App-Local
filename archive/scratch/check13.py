import sys; sys.path.insert(0,"app")
import app as A, urllib.request, http.cookiejar, traceback
from datetime import date, timedelta
frm=date.today().isoformat(); to=(date.today()+timedelta(days=2)).isoformat()
cookies=A._load_sepaq_cookies()

jar=http.cookiejar.CookieJar()
# seed jar with cloudflare cookies for .sepaq.com
for k,v in cookies.items():
    c=http.cookiejar.Cookie(0,k,v,None,False,".sepaq.com",True,False,"/",True,True,None,False,None,None,{})
    jar.set_cookie(c)
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

# POST dates with NO manual Cookie header
import urllib.parse
body=urllib.parse.urlencode({"arrivalDate":frm,"departureDate":to,"booking.arrivalDate":frm,"booking.departureDate":to,"booking.adults":"2"}).encode()
H={k:v for k,v in A._LIVE_HEADERS.items()}
r=urllib.request.Request("https://www.sepaq.com/en/reservation/search",data=body,headers={**H,"Content-Type":"application/x-www-form-urlencoded"})
print("post status:",opener.open(r,timeout=20).status)
print("jar:",[c.name for c in jar])

# GET boucle page, NO manual Cookie header
url="https://www.sepaq.com/en/reservation/camping/parc-national-de-la-jacques-cartier/des-alluvions"
try:
    resp=opener.open(urllib.request.Request(url,headers=H),timeout=20)
    html=resp.read().decode("utf-8","replace")
    import re
    print("status:",resp.status,"len:",len(html),"units:",len(re.findall(r"id=\"unit_",html)))
except Exception:
    traceback.print_exc()

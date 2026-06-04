import sys; sys.path.insert(0,"app")
import app as A, urllib.request, traceback
from datetime import date, timedelta
frm=date.today().isoformat(); to=(date.today()+timedelta(days=2)).isoformat()
cookies=A._load_sepaq_cookies(); hdr=A._build_cookie_header(cookies)
opener=A._make_sepaq_opener()
A._set_session_dates(frm,to,hdr,opener)
url="https://www.sepaq.com/en/reservation/camping/parc-national-de-la-jacques-cartier/des-alluvions"
merged=A._merged_cookie_header(hdr,opener)
print("merged cookie header length:", len(merged))
req=urllib.request.Request(url, headers={**A._LIVE_HEADERS,"Cookie":merged})
try:
    resp=opener.open(req,timeout=15)
    print("status:",resp.status,"len:",len(resp.read()))
except Exception as e:
    traceback.print_exc()

import sys; sys.path.insert(0,"app")
import app as A, sqlite3
from datetime import date, timedelta
frm=date.today().isoformat(); to=(date.today()+timedelta(days=2)).isoformat()
cookies=A._load_sepaq_cookies(); hdr=A._build_cookie_header(cookies)
opener=A._make_sepaq_opener()
A._set_session_dates(frm,to,hdr,opener)
url="https://www.sepaq.com/en/reservation/camping/parc-national-de-la-jacques-cartier/des-alluvions"
html=A._sepaq_get(url,hdr,opener)
print("html len:",len(html) if html else None)
print("first 80 chars repr:", repr(html[:80]) if html else None)
print("has unit_:", ("unit_" in html) if html else None)
print("_LIVE_HEADERS Accept-Encoding:", A._LIVE_HEADERS.get("Accept-Encoding"))

import sys; sys.path.insert(0,"app")
import app as A
from datetime import date, timedelta
frm=date.today().isoformat(); to=(date.today()+timedelta(days=2)).isoformat()
r = A._fetch_live_sites(24, frm, to)
print("cookie_status:", r["cookie_status"])
print("sites:", len(r["sites"]))
if r["sites"]:
    s=r["sites"][0]
    print("sample:", {k:s.get(k) for k in ("site_name","available","partial","x_pct","y_pct")})

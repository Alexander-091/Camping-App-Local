import sys
sys.path.insert(0, 'app')
import app as A
from datetime import date, timedelta
frm = date.today().isoformat()
to  = (date.today()+timedelta(days=2)).isoformat()
print('cookies loaded:', bool(A._load_sepaq_cookies()))
sites = A._fetch_live_sites(41, frm, to)
print('live sites returned:', len(sites))
if sites:
    print('sample:', {k: sites[0].get(k) for k in ("site_name","available","partial","x_pct","y_pct")})

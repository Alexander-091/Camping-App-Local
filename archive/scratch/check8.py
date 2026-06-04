import sys
sys.path.insert(0, 'app')
import app as A
from datetime import date, timedelta
cookies = A._load_sepaq_cookies()
hdr = A._build_cookie_header(cookies)
frm = date.today().isoformat()
to  = (date.today()+timedelta(days=2)).isoformat()
ok = A._set_session_dates(frm, to, hdr)
print('set_session_dates returned:', ok)
# now fetch the SAME page again, after setting dates
import sqlite3
url = sqlite3.connect('data/sepaq.db').execute("SELECT url FROM boucles WHERE id=41").fetchone()[0]
html = A._sepaq_get(url, hdr)
low = (html or '').lower()
print('length:', len(html) if html else 0)
for m in ['left:','top:','disponible','available','data-unit','reservation']:
    print('  ', m, m in low)

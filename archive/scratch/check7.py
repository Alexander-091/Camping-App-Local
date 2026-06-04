import sys
sys.path.insert(0, 'app')
import app as A
cookies = A._load_sepaq_cookies()
hdr = A._build_cookie_header(cookies)
# fetch one boucle page raw and inspect
import sqlite3
c = sqlite3.connect('data/sepaq.db')
url = c.execute("SELECT url FROM boucles WHERE id=41").fetchone()[0]
print('URL:', url)
html = A._sepaq_get(url, hdr)
print('got HTML:', html is not None, '| length:', len(html) if html else 0)
if html:
    low = html.lower()
    for marker in ['connexion','login','se connecter','captcha','cloudflare','just a moment','left:','data-availability','disponible']:
        print('  contains %-18s' % marker, marker in low)

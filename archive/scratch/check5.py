import os, time, json
# 1. which cookie file does the app now load?
sc = 'scraper/session_cookie.json'
cj = 'scraper/cookies.json'
print('cookies.json exists:', os.path.exists(cj))
print('session_cookie.json exists:', os.path.exists(sc),
      '| age %.1f h' % ((time.time()-os.path.getmtime(sc))/3600) if os.path.exists(sc) else '')
# 2. peek at the fresh cookie contents (names only, not values)
if os.path.exists(sc):
    raw = json.load(open(sc, encoding='utf-8'))
    if isinstance(raw, list):
        print('format: list (playwright), cookie names:', [c.get("name") for c in raw][:15])
    elif isinstance(raw, dict):
        print('format: dict, keys:', list(raw)[:15])

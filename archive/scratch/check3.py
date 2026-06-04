import os, json, time
# common cookie locations - adjust if needed
for p in ['scraper/cookies.json','cookies.json','data/cookies.json','scraper/sepaq_cookies.json']:
    if os.path.exists(p):
        age = (time.time() - os.path.getmtime(p))/3600
        print(p, '-> exists, age %.1f h' % age)
    else:
        print(p, '-> missing')

import os, time, glob
for f in glob.glob('scraper/*.json') + glob.glob('*.json') + glob.glob('data/*.json'):
    age = (time.time()-os.path.getmtime(f))/3600
    print('%6.1f h  %s' % (age, f))

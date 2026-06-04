import sqlite3
c = sqlite3.connect('data/sepaq.db')
print('sites total', c.execute('SELECT count(*) FROM sites').fetchone()[0])
print('sites positioned', c.execute('SELECT count(*) FROM sites WHERE x_pct IS NOT NULL AND y_pct IS NOT NULL').fetchone()[0])
print('boucles with map_url', c.execute("SELECT count(*) FROM boucles WHERE map_url IS NOT NULL AND map_url != ''").fetchone()[0])

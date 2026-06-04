import sqlite3
c = sqlite3.connect('data/sepaq.db')
print('-- sites in sector 24 --', c.execute('SELECT count(*) FROM sites s JOIN boucles b ON s.boucle_id=b.id WHERE b.sector_id=24').fetchone()[0])
print('-- boucles in sector 24 --')
for r in c.execute("SELECT id, name, map_url IS NOT NULL FROM boucles WHERE sector_id=24"):
    print('  ', r)
print('-- sample sites cols --', [d[1] for d in c.execute('PRAGMA table_info(sites)')])

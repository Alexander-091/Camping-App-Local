import sqlite3
c=sqlite3.connect("data/sepaq.db"); c.row_factory=sqlite3.Row
for r in c.execute("SELECT unit_id, site_name, url FROM sites WHERE site_name LIKE 'lac-provost-7%' LIMIT 3"):
    print(dict(r))
print("--- how many sites have a url ---")
print(c.execute("SELECT COUNT(*) FROM sites WHERE url IS NOT NULL AND url!=''").fetchone()[0], "of", c.execute("SELECT COUNT(*) FROM sites").fetchone()[0])

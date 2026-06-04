import sqlite3
c=sqlite3.connect("data/sepaq.db"); c.row_factory=sqlite3.Row
print("boucle 41 map_url:", (c.execute("SELECT map_url FROM boucles WHERE id=41").fetchone()["map_url"] or "")[:90])
for r in c.execute("SELECT unit_id, site_name, x_pct, y_pct FROM sites WHERE boucle_id=41 LIMIT 5"):
    print(dict(r))

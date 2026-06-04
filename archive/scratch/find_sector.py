import sys; sys.path.insert(0,"app")
import app as A, sqlite3
c=sqlite3.connect("data/sepaq.db"); c.row_factory=sqlite3.Row
# find the sector
for r in c.execute("""SELECT s.id, s.name, s.slug, p.name AS park FROM sectors s
                      JOIN parks p ON p.id=s.park_id
                      WHERE p.name LIKE '%Tremblant%'"""):
    print(dict(r))

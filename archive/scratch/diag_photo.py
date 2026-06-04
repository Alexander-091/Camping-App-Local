import sqlite3
c=sqlite3.connect("data/sepaq.db"); c.row_factory=sqlite3.Row
tot=c.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
withurl=c.execute("SELECT COUNT(*) FROM sites WHERE photo_url IS NOT NULL AND photo_url!=''").fetchone()[0]
withblob=c.execute("SELECT COUNT(*) FROM sites WHERE photo_data IS NOT NULL").fetchone()[0]
print(f"sites total={tot}  with photo_url={withurl}  with photo_data(blob)={withblob}")
# sample a few unit_ids that the live fetch returns for Lac Provost
for r in c.execute("SELECT unit_id, site_name, photo_url IS NOT NULL AS u, photo_data IS NOT NULL AS b FROM sites WHERE site_name LIKE 'lac-provost%' LIMIT 8"):
    print(dict(r))

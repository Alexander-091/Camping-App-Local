import os, sqlite3, glob
for path in glob.glob("data/*.db") + glob.glob("data/*.db.*") + glob.glob("old.data/*"):
    if not os.path.isfile(path): continue
    size = os.path.getsize(path)/1e6
    try:
        c = sqlite3.connect(path)
        # does it have a sites table with photo columns?
        cols = [r[1] for r in c.execute("PRAGMA table_info(sites)")]
        if "photo_data" in cols:
            n = c.execute("SELECT COUNT(*) FROM sites WHERE photo_data IS NOT NULL").fetchone()[0]
            tot = c.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
            print(f"{path}  ({size:.1f} MB)  sites={tot}  with_blob={n}")
        else:
            print(f"{path}  ({size:.1f} MB)  sites table cols: {cols}")
        c.close()
    except Exception as e:
        print(f"{path}  ({size:.1f} MB)  ERROR: {e}")

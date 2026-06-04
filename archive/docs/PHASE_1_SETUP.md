# Phase 1: Fix Data Foundation — Setup Instructions

The database has been corrupted at the filesystem level. We're rebuilding from scratch with the correct schema (including `x_pct` and `y_pct` columns).

## Manual Steps (On Your Machine)

### Step 1: Clean Up Corrupted Files
Navigate to your Camping App folder and:
```
data/
├── sepaq.db.old           (already moved, can delete)
├── sepaq.db-journal       (DELETE THIS)
├── sepaq_new.db           (DELETE THIS)
└── sepaq_new.db-journal   (DELETE THIS)
```

**Delete all remaining files in the `data/` folder except any backups you want to keep.**

Once clean, the `data/` folder should be **completely empty**.

### Step 2: Initialize Fresh Database
From your Camping App root directory, run:
```bash
python setup_fresh_db.py
```

You should see:
```
============================================================
✓ Fresh database created successfully!
============================================================
Location: /path/to/data/sepaq.db
Size: 12288 bytes

Sites table columns (11):
  • boucle_id
  • id
  • photo_data
  • photo_url
  • scraped_at
  • site_name
  • site_type
  • unit_id
  • url
  • x_pct          ← NEW COLUMN
  • y_pct          ← NEW COLUMN

Position columns:
  • x_pct: ✓ READY
  • y_pct: ✓ READY
```

### Step 3: Refresh Cloudflare Cookies
The scraper needs fresh Cloudflare cookies to access SEPAQ's site. Run:
```bash
python scraper/get_cookie.py
```

This will:
1. Open SEPAQ in your browser
2. Prompt you to copy cookies from DevTools
3. Save them to `scraper/session_cookie.json`

**You must run this WITHIN 30 MINUTES of running the scraper**, as cookies expire.

### Step 4: Run the Scraper
```bash
python scraper/scraper.py
```

This will:
- Discover all parks, sectors, and boucles
- For each boucle, fetch the HTML page
- Extract map URLs (S3 GIF links)
- Extract individual site positions (x_pct, y_pct from `<li style="left:X%; top:Y%">`)
- Save everything to `data/sepaq.db`

**Time estimate:** 15-30 minutes for full scrape of all 15 parks.

Monitor output for:
- ✓ Parks discovered
- ✓ Sectors discovered
- ✓ Boucles discovered and map URLs extracted
- ✓ Sites discovered with positions

If the scraper returns empty arrays for all parks, it means cookies are stale — re-run `get_cookie.py` and retry.

## What Phase 1 Completion Looks Like

Run this to verify:
```bash
python3 << 'EOF'
import sqlite3
conn = sqlite3.connect("data/sepaq.db")
cursor = conn.cursor()

# Check structure
cursor.execute("PRAGMA table_info(sites)")
cols = [col[1] for col in cursor.fetchall()]
print(f"Sites columns: {sorted(cols)}")

# Check data
cursor.execute("SELECT COUNT(*) FROM parks")
print(f"Parks: {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) FROM sectors")
print(f"Sectors: {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) FROM boucles")
print(f"Boucles: {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) FROM boucles WHERE map_url IS NOT NULL")
print(f"Boucles with map_url: {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) FROM sites")
total_sites = cursor.fetchone()[0]
print(f"Total sites: {total_sites}")

cursor.execute("SELECT COUNT(*) FROM sites WHERE x_pct IS NOT NULL")
sites_with_pos = cursor.fetchone()[0]
print(f"Sites with positions: {sites_with_pos}")

conn.close()
EOF
```

**Phase 1 complete when:**
- ✓ `data/sepaq.db` exists and is readable
- ✓ `sites` table has `x_pct` and `y_pct` columns
- ✓ At least one park (e.g., Oka) has all boucles with `map_url`
- ✓ Oka's sites have non-null `x_pct`/`y_pct` values

Once complete, reply with the output and we'll move to **Phase 2: Rendering** to make the schematic overlay render reliably.

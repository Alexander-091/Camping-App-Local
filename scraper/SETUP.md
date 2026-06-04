# Scraper Setup Guide

## What changed
The scraper now uses SEPAQ's internal JSON API directly — no browser automation,
no Cloudflare fighting. Just lightweight HTTP requests.

---

## One-time setup

### 1. Install dependencies (much simpler now)
```
pip install -r requirements.txt
```

### 2. Test the API connection
```
python scraper.py --explore
```
This hits the API for one park (Gaspésie) and prints the raw response.
Two outcomes:

**✅ You see JSON data** — the API works without cookies. Run the full scrape:
```
python scraper.py
```

**⚠ You see "Expected JSON but got text/html"** — the API needs a session cookie.
Run the cookie helper and follow its instructions:
```
python get_cookie.py
```
Then run `scraper.py --explore` again to confirm it works.

---

## Running a full scrape
```
python scraper.py
```
Scrapes all ~20 SEPAQ national parks. Takes 2–5 minutes.

## Re-scrape any time
Same command. Existing records are updated, not duplicated.

## Inspect what was captured
```
python inspect_db.py
```

---

## Troubleshooting

**"Expected JSON but got text/html"**
The session cookie expired. Run `python get_cookie.py` to refresh it.

**A park returns no data**
SEPAQ may use a different slug for that park. Check the URL on
sepaq.com and update `SEPAQ_PARKS` in `scraper.py`.

**Connection error / timeout**
Check your internet connection. SEPAQ's servers occasionally have downtime.

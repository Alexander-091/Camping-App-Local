"""
get_cookie.py
-------------
Refreshes the SEPAQ session cookies needed by scraper.py.

Run this whenever the scraper returns [] for all parks — that means
__cf_bm has expired.  Run scraper.py within 30 minutes of finishing here.

Usage:
    python get_cookie.py
"""

import json
import os
import webbrowser

COOKIE_FILE = os.path.join(os.path.dirname(__file__), "session_cookie.json")
SEPAQ_URL   = "https://www.sepaq.com/en/reservation/camping"


def main():
    print("=" * 60)
    print("  SEPAQ Cookie Refresh")
    print("=" * 60)
    print("""
Steps:
  1. Press Enter — SEPAQ will open in your browser
  2. Click on any park and let the page fully load
  3. Open DevTools:  F12
  4. Go to:  Application  →  Cookies  →  https://www.sepaq.com
  5. Find and copy the cookies listed below
""")

    input("Press Enter to open SEPAQ in your browser...")
    webbrowser.open(SEPAQ_URL)
    print()
    print("Once the page loads, open DevTools (F12) →")
    print("Application → Cookies → https://www.sepaq.com")
    print()

    cookies = {}

    # cf_clearance is the only truly required cookie.
    # The server will issue fresh __cf_bm and JSESSIONID on the first page hit.
    print("── Required ─────────────────────────────────────────────────")
    cf = _prompt("cf_clearance  (required)")
    if cf:
        cookies["cf_clearance"] = cf

    print()
    print("── Optional — paste if visible, Enter to skip ───────────────")
    print("   (Do NOT paste JSESSIONID or JSESSIONID_TRANSAC — those are")
    print("    browser-specific and will break the scraper if loaded.)")
    for name in ["__cf_bm", "__cflb"]:
        val = _prompt(name)
        if val:
            cookies[name] = val

    print()

    if not cookies.get("cf_clearance"):
        print("⚠  cf_clearance is required. Exiting without saving.")
        return

    with open(COOKIE_FILE, "w") as f:
        json.dump(cookies, f, indent=2)

    print(f"✓ Saved {len(cookies)} cookie(s) to: {os.path.abspath(COOKIE_FILE)}")
    print()
    print("Run the scraper now (within 30 minutes):")
    print("  python scraper.py --explore    ← verify one park first")
    print("  python scraper.py              ← full scrape all 20 parks")


def _prompt(name: str) -> str:
    return input(f"  {name:<28}: ").strip()


if __name__ == "__main__":
    main()

import sys; sys.path.insert(0,"app")
import app as A
r = A._fetch_live_sites(101, "2026-06-05", "2026-06-07")
sites = r["sites"]
print("cookie_status:", r["cookie_status"], "| total sites:", len(sites))
colours = {}
for s in sites:
    c = s.get("colour") or "(empty)"
    colours[c] = colours.get(c,0)+1
print("colour breakdown:", colours)
for s in sites[:3]:
    print({k:s.get(k) for k in ("unit_id","site_name","colour","available","x_pct")})

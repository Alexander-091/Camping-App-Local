import sys; sys.path.insert(0,"app")
import app as A
r = A._fetch_live_sites(101, "2026-06-05", "2026-06-07")
from collections import Counter
by_colour_type = Counter()
for s in r["sites"]:
    by_colour_type[(s.get("colour"), s.get("site_type"))] += 1
for (col, typ), n in sorted(by_colour_type.items()):
    print(f"{col:8} | {typ!r:40} | {n}")

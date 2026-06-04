html = open("site_page.html", encoding="utf-8").read()
import re
print("len", len(html))
print("--- galleriemedia mentions ---")
for m in re.findall(r'galleriemedia[^"\'\\\s]*', html): print(m[:120])
print("--- 'gallerie' / 'media' / 'fiche' raw mentions ---")
for kw in ["galleriemedia","gallerie","fiche","unite","medias","photo"]:
    i = html.lower().find(kw.lower())
    print(f"  {kw}: idx={i}", repr(html[i-30:i+90]) if i>=0 else "")

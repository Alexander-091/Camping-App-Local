import os
root = "."
# 1. all .db files anywhere + blob check
import sqlite3, glob
print("=== databases ===")
for path in glob.glob("**/*.db", recursive=True) + glob.glob("**/*.sqlite*", recursive=True):
    if "browser_profile" in path or "chrome_profile" in path: continue
    try:
        sz = os.path.getsize(path)/1e6
        c = sqlite3.connect(path)
        cols = [r[1] for r in c.execute("PRAGMA table_info(sites)")]
        if "photo_data" in cols:
            n = c.execute("SELECT COUNT(*) FROM sites WHERE photo_data IS NOT NULL").fetchone()[0]
            print(f"  {path} ({sz:.1f}MB) photo_blobs={n}")
        else:
            print(f"  {path} ({sz:.1f}MB) no photo col")
        c.close()
    except Exception as e:
        print(f"  {path} ERR {e}")

print("=== image files (non-browser) ===")
cnt = 0
for dp,_,fs in os.walk(root):
    if "browser_profile" in dp or "chrome_profile" in dp or "GPUCache" in dp: continue
    for f in fs:
        if f.lower().endswith((".jpg",".jpeg",".webp",".png")):
            cnt += 1
            if cnt <= 15: print(" ", os.path.join(dp,f))
print(f"  total image files: {cnt}")

print("=== largest 10 files ===")
allf=[]
for dp,_,fs in os.walk(root):
    if "browser_profile" in dp or "chrome_profile" in dp: continue
    for f in fs:
        p=os.path.join(dp,f)
        try: allf.append((os.path.getsize(p),p))
        except: pass
for sz,p in sorted(allf,reverse=True)[:10]:
    print(f"  {sz/1e6:7.1f}MB  {p}")

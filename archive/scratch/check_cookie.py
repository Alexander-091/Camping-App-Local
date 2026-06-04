import os, json, sys
sys.path.insert(0, "app")
import app as A
print("COOKIE_FILE_PW (cookies.json):", A.COOKIE_FILE_PW, "exists:", os.path.exists(A.COOKIE_FILE_PW))
print("COOKIE_FILE (session_cookie.json):", A.COOKIE_FILE, "exists:", os.path.exists(A.COOKIE_FILE))
for p in [A.COOKIE_FILE_PW, A.COOKIE_FILE]:
    if os.path.exists(p):
        try:
            raw = json.load(open(p, encoding="utf-8"))
            print(f"  {os.path.basename(p)} -> type={type(raw).__name__}, keys/len={list(raw) if isinstance(raw,dict) else len(raw)}")
        except Exception as e:
            print(f"  {os.path.basename(p)} -> UNREADABLE: {e}")
print("_load_sepaq_cookies() returns:", list(A._load_sepaq_cookies()))

"""One-time: short-lived user token -> long-lived user token -> non-expiring Page token.
Usage: APP_ID=... APP_SECRET=... USER_TOKEN=... python scripts/auth.py
Get USER_TOKEN from Graph API Explorer with scopes:
  pages_show_list pages_read_engagement pages_manage_posts publish_video
"""
import os, sys, requests
G = "https://graph.facebook.com/v23.0"
app_id, secret, user_tok = (os.environ.get(k) for k in ("APP_ID", "APP_SECRET", "USER_TOKEN"))
if not all((app_id, secret, user_tok)):
    sys.exit("set APP_ID, APP_SECRET, USER_TOKEN")
ll = requests.get(f"{G}/oauth/access_token", params={"grant_type": "fb_exchange_token", "client_id": app_id,
                  "client_secret": secret, "fb_exchange_token": user_tok}, timeout=30).json()
if "access_token" not in ll:
    sys.exit(f"exchange failed: {ll}")
pages = requests.get(f"{G}/me/accounts", params={"fields": "id,name,access_token,tasks",
                     "access_token": ll["access_token"]}, timeout=30).json().get("data", [])
for p in pages:
    dbg = requests.get(f"{G}/debug_token", params={"input_token": p["access_token"],
                       "access_token": f"{app_id}|{secret}"}, timeout=30).json().get("data", {})
    ok = "CREATE_CONTENT" in p.get("tasks", []) and dbg.get("expires_at") == 0
    print(f"\n{p['name']}  id={p['id']}  create_content={'CREATE_CONTENT' in p.get('tasks', [])}  "
          f"expires_at={dbg.get('expires_at')}  {'OK' if ok else 'NOT OK'}")
    if ok:
        print("FB_PAGE_ID   =", p["id"])
        print("FB_PAGE_TOKEN=", p["access_token"])
print("\nPut FB_PAGE_ID and FB_PAGE_TOKEN into GitHub -> Settings -> Secrets -> Actions.")

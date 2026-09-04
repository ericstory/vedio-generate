"""Follow an existing task through the production API to a terminal state.

Usage: python3 follow_task.py <task-id> [--once]
"""
import json,os,sys,time,urllib.request,urllib.error,subprocess,http.cookiejar
APP="/Users/macmini/workspace/papa/apps/video-generator"
V=dict(l.split("=",1) for l in subprocess.run(["railway","variables","--kv"],capture_output=True,text=True,cwd=APP).stdout.splitlines() if "=" in l)
HOST=V["RAILWAY_PUBLIC_DOMAIN"].strip()
BASE=f"https://{HOST}/generate"
cj=http.cookiejar.CookieJar()
op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def post_json(path,body):
    req=urllib.request.Request(BASE+path,data=json.dumps(body).encode(),headers={"Content-Type":"application/json","User-Agent":"papa/1.0"})
    return json.load(op.open(req,timeout=60))
def get(path):
    return json.load(op.open(urllib.request.Request(BASE+path,headers={"User-Agent":"papa/1.0"}),timeout=60))
def login():
    post_json("/api/login",{"username":V["ADMIN_USERNAME"].strip(),"password":V["ADMIN_PASSWORD"].strip()})
tid=sys.argv[1]; once="--once" in sys.argv
login(); last=None; errs=0; start=time.time()
MAX=float(os.environ.get("MAX_SECONDS","3600"))
while time.time()-start<MAX:
    try: tasks=get("/api/tasks")
    except Exception as e:
        errs+=1
        if errs%6==0:
            try: login()
            except Exception: pass
        time.sleep(10); continue
    t=next((x for x in tasks["tasks"] if x["id"]==tid),None)
    if not t: print("task not found"); sys.exit(1)
    meta=t.get("provider_metadata") or {}; pr=meta.get("progress") if isinstance(meta.get("progress"),dict) else {}
    cur=(t["status"],pr.get("stage"),pr.get("attempts"),t.get("provider_task_id"))
    if cur!=last or once:
        print(f"  [{int(time.time()-start):>4}s] {t['status']:<10} stage={pr.get('stage')} at={pr.get('at')} pod={t.get('provider_task_id')} details={ {k:v for k,v in pr.items() if k not in ('stage','at')} }",flush=True); last=cur
    if t["status"] in ("succeeded","failed","cancelled","expired"):
        print(json.dumps({"status":t["status"],"video_url":t.get("video_url"),"error":(t.get("error") or "")[:3000],"metadata":{k:v for k,v in meta.items() if k!="progress"}},ensure_ascii=False,indent=1)[:5000])
        sys.exit(0 if t["status"]=="succeeded" else 1)
    if once: sys.exit(0)
    time.sleep(10)
print("TIMEOUT"); sys.exit(1)

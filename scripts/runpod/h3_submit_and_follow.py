"""Submit one H3 job through the real production API and follow it to a terminal state.

Since the queued-acquisition change the request returns as soon as the task is
stored; the control plane's guard loop then asks RunPod for a Pod every ~20 s
for up to 15 minutes, so there is nothing to retry from here.
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
def post_form(path,fields):
    boundary="----papa"+str(int(time.time()))
    parts=[f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n" for k,v in fields.items()]
    body=("".join(parts)+f"--{boundary}--\r\n").encode()
    req=urllib.request.Request(BASE+path,data=body,headers={"Content-Type":f"multipart/form-data; boundary={boundary}","User-Agent":"papa/1.0"})
    try: return json.load(op.open(req,timeout=120))
    except urllib.error.HTTPError as e: return {"err":e.code,"body":e.read().decode()[:400]}
def get(path):
    return json.load(op.open(urllib.request.Request(BASE+path,headers={"User-Agent":"papa/1.0"}),timeout=60))
def login():
    post_json("/api/login",{"username":V["ADMIN_USERNAME"].strip(),"password":V["ADMIN_PASSWORD"].strip()})

login()
fields={
    "prompt": sys.argv[1] if len(sys.argv)>1 else "A calm sunrise over the ocean, gentle waves rolling toward the shore, warm golden light, cinematic wide shot",
    # MODEL=pinkcherry-ltx-2.3-v1.8 RESOLUTION=480p follows the LTX Pod lane instead.
    "model":os.environ.get("MODEL","minimax-h3-pinkcherry"),"ratio":os.environ.get("RATIO","16:9"),
    "resolution":os.environ.get("RESOLUTION","768p"),"duration":sys.argv[2] if len(sys.argv)>2 else "5","generate_audio":"true",
}
t0=time.time(); r=post_form("/api/tasks",fields); dt=time.time()-t0
if "err" in r: print("SUBMIT FAILED:",r); sys.exit(1)
tid=r["task"]["id"]; print(f"task: {tid}  status={r['task']['status']}  pod={r['task'].get('provider_task_id')!r}  submit took {dt:.2f}s",flush=True)
if os.environ.get("FOLLOW","1")=="0": sys.exit(0)  # FOLLOW=0: submit only, follow with follow_task.py
last=None; errs=0; start=time.time()
while time.time()-start<3000:
    time.sleep(10)
    try: tasks=get("/api/tasks")
    except Exception as e:
        errs+=1
        if errs%6==0: 
            try: login()
            except Exception: pass
        continue
    t=next((x for x in tasks["tasks"] if x["id"]==tid),None)
    if not t: continue
    meta=t.get("provider_metadata") or {}; pr=meta.get("progress") if isinstance(meta.get("progress"),dict) else {}
    cur=(t["status"],pr.get("stage"),pr.get("attempts"),t.get("provider_task_id"))
    if cur!=last:
        print(f"  [{int(time.time()-start):>4}s] {t['status']:<10} stage={pr.get('stage')} attempts={pr.get('attempts')} pod={t.get('provider_task_id')}",flush=True); last=cur
    if t["status"] in ("succeeded","failed","cancelled","expired"):
        print(json.dumps({"status":t["status"],"video_url":t.get("video_url"),"error":(t.get("error") or "")[:3000],"metadata":{k:v for k,v in meta.items() if k!="progress"}},ensure_ascii=False,indent=1)[:4000])
        sys.exit(0 if t["status"]=="succeeded" else 1)
print("TIMEOUT waiting for terminal state"); sys.exit(1)

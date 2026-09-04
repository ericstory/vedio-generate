"""Run one H3 job on a Pod built from the live template WITHOUT result callbacks, stream the
container log, print stage lines as they appear, dump the full log to a file when the job ends
(stage=complete or a crash), then always delete the Pod. argv[1] = JSON of extra env overrides."""
import json,time,sys,socket,urllib.request,urllib.error,subprocess,re,os
APP="/Users/macmini/workspace/papa/apps/video-generator"
S=os.path.dirname(os.path.abspath(__file__))
V=dict(l.split("=",1) for l in subprocess.run(["railway","variables","--kv"],capture_output=True,text=True,cwd=APP).stdout.splitlines() if "=" in l)
K=V["RUNPOD_API_KEY"].strip(); TPL=V["RUNPOD_H3_POD_TEMPLATE_ID"].strip()
H={"Authorization":f"Bearer {K}","User-Agent":"papa/1.0","Content-Type":"application/json"}
def api(method,path,body=None):
    req=urllib.request.Request("https://api.runpod.io/v2"+path,data=json.dumps(body).encode() if body is not None else None,headers=H,method=method)
    try:
        r=urllib.request.urlopen(req,timeout=120); data=r.read().decode(); return json.loads(data) if data.strip() else {}
    except urllib.error.HTTPError as e: return {"err":e.code,"body":e.read().decode()[:400]}
def read_log(pid,idle=6.0):
    req=urllib.request.Request(f"https://api.runpod.io/v2/pods/{pid}/logs?tail=5000&source=container",headers={**H,"Accept":"text/event-stream"})
    lines=[]
    try:
        resp=urllib.request.urlopen(req,timeout=idle)
        while True:
            try: raw=resp.readline()
            except (socket.timeout,TimeoutError): break
            if not raw: break
            s=raw.decode(errors="replace").strip()
            if s.startswith("data:"):
                try: j=json.loads(s[5:].strip()); lines.append(str(j.get("line","")))
                except Exception: lines.append(s[5:].strip())
    except Exception as e: return "\n".join(lines) if lines else ""
    return "\n".join(lines)
extra=json.loads(sys.argv[1]) if len(sys.argv)>1 else {}
tag=sys.argv[2] if len(sys.argv)>2 else "diag"
tpl=api("GET",f"/templates/{TPL}"); env=dict(tpl["env"]); env.update(extra)
env["SMOKE_INPUT_JSON"]=json.dumps({"prompt":"A calm sunrise over the ocean, gentle waves rolling toward the shore, warm golden light, cinematic wide shot",
  "model_id":"MiniMaxAI/MiniMax-H3","model_version":"42ed227ee7df40d41602854ae760620d6eb651fe","workflow_version":"h3-fl2va-pinkcherry-turbo8-v1",
  "ratio":"16:9","resolution":"768p","duration":5,"generate_audio":True,
  "adult_model_id":"SexGod1979/PinkCherry_MiniMax-H3","adult_model_version":"bf2fef11d0e55e957f4af997e3beade3362f44b3"})
body={"name":f"papa-h3-{tag}-{int(time.time())%100000}","templateId":TPL,"cloud":"SECURE","gpu":{"id":"","count":1,"minCudaVersion":"13.0"},"disk":220,"env":env}
dcs=[d for d in os.getenv("DIAG_DCS","").split(",") if d]
if dcs: body["dataCenterIds"]=dcs
pid=None
for gpu in ["NVIDIA RTX PRO 6000 Blackwell Server Edition","NVIDIA RTX PRO 6000 Blackwell Workstation Edition"]:
    body["gpu"]["id"]=gpu; r=api("POST","/pods",body)
    if "err" in r: print("create miss:",gpu,r["body"][:120],flush=True); continue
    pid=r["id"]; print(f"DIAG POD {pid} gpu={gpu} dc={r.get('dataCenterId')} cost={r.get('cost')} template={TPL} overrides={extra}",flush=True); break
if not pid: print("NO CAPACITY"); sys.exit(2)
print('CREATED',pid,flush=True)

"""Watch an EXISTING diagnostic Pod: stream its container log, print stage/LoRA/error lines,
save the full log when the job ends (stage=complete or crash), then delete the Pod.
argv[1] = pod id, argv[2] = tag, argv[3] = ceiling seconds (default 2700)."""
import json,time,sys,socket,urllib.request,urllib.error,subprocess,re,os
APP="/Users/macmini/workspace/papa/apps/video-generator"; S=os.path.dirname(os.path.abspath(__file__))
V=dict(l.split("=",1) for l in subprocess.run(["railway","variables","--kv"],capture_output=True,text=True,cwd=APP).stdout.splitlines() if "=" in l)
K=V["RUNPOD_API_KEY"].strip(); H={"Authorization":f"Bearer {K}","User-Agent":"papa/1.0","Content-Type":"application/json"}
def api(method,path,body=None):
    req=urllib.request.Request("https://api.runpod.io/v2"+path,data=json.dumps(body).encode() if body is not None else None,headers=H,method=method)
    try:
        r=urllib.request.urlopen(req,timeout=120); data=r.read().decode(); return json.loads(data) if data.strip() else {}
    except urllib.error.HTTPError as e: return {"err":e.code,"body":e.read().decode()[:400]}
def read_log(pid,idle=8.0):
    req=urllib.request.Request(f"https://api.runpod.io/v2/pods/{pid}/logs?tail=5000&source=container",headers={**H,"Accept":"text/event-stream"})
    lines=[]
    try:
        resp=urllib.request.urlopen(req,timeout=idle)
        deadline=time.time()+20  # the stream sends keep-alives forever; cap by wall time
        while time.time()<deadline:
            try: raw=resp.readline()
            except (socket.timeout,TimeoutError): break
            if not raw: break
            s=raw.decode(errors="replace").strip()
            if s.startswith("data:"):
                try: j=json.loads(s[5:].strip()); lines.append(str(j.get("line","")))
                except Exception: lines.append(s[5:].strip())
    except Exception: return "\n".join(lines) if lines else ""
    return "\n".join(lines)
pid=sys.argv[1]; tag=sys.argv[2] if len(sys.argv)>2 else "watch"; ceiling=float(sys.argv[3]) if len(sys.argv)>3 else 2700
print(f"WATCHING POD {pid} tag={tag} ceiling={int(ceiling)}s",flush=True)
start=time.time(); seen=set(); text=""; last_status=None
try:
    while time.time()-start<ceiling:
        time.sleep(30)
        p=api("GET",f"/pods/{pid}")
        st=(p.get("status"),bool(p.get("runtime")))
        if st!=last_status: print(f"[{int(time.time()-start)}s] pod status={st[0]} runtime_up={st[1]} dc={p.get('dataCenterId')}",flush=True); last_status=st
        if "err" in p and p["err"]==404: print("POD GONE",flush=True); break
        text=read_log(pid)
        for line in text.splitlines():
            if re.search(r'"stage"|Traceback|EOFError|Killed|OutOfMemory|CUDA error|LoRA adapter|applied to \d+ layers|did not match|lora_alpha|alpha|NaN|video_url',line):
                key=line.strip()[:220]
                if key not in seen: seen.add(key); print(f"[{int(time.time()-start)}s] {key}",flush=True)
        if re.search(r'"stage": "complete"|Traceback|EOFError|Killed|OutOfMemory|CUDA error|pod_callback_complete|smoke_complete',text): break
    open(f"{S}/{tag}_pod_{pid}.log","w").write(text); print(f"LOG SAVED {S}/{tag}_pod_{pid}.log chars={len(text)}",flush=True)
finally:
    print("DELETE",pid,api("DELETE",f"/pods/{pid}"),flush=True)

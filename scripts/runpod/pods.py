import json,sys,urllib.request,urllib.error,subprocess
out=subprocess.run(["railway","variables","--kv"],capture_output=True,text=True,
                   cwd="/Users/macmini/workspace/papa/apps/video-generator").stdout
K=[l.split("=",1)[1] for l in out.splitlines() if l.startswith("RUNPOD_API_KEY=")][0].strip()
def api(method,path,body=None):
    req=urllib.request.Request("https://api.runpod.io/v2"+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization":f"Bearer {K}","User-Agent":"papa/1.0","Content-Type":"application/json"},method=method)
    try:
        r=urllib.request.urlopen(req,timeout=120); raw=r.read().decode()
        return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e: return {"err":e.code,"body":e.read().decode()[:300]}
if __name__=="__main__":
    cmd=sys.argv[1]
    if cmd=="list":
        d=api("GET","/pods"); rows=d.get("pods") if isinstance(d,dict) else d
        for p in (rows or []):
            print(f"  {p.get('id'):<16} {str(p.get('name'))[:34]:<36} {p.get('desiredStatus')}  cpu={bool(p.get('cpu'))} dc={p.get('dataCenterId')}")
        if not rows: print("  (no pods)")
    elif cmd=="delete":
        for pid in sys.argv[2:]: print(pid, api("DELETE",f"/pods/{pid}"))

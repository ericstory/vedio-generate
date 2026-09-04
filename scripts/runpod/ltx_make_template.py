"""Create a volume-free LTX Pod template for one image SHA.

Usage: python3 ltx_make_template.py <full-sha> [name-suffix]

Same shape as h3_make_template.py: the image is the ltx-worker CI build for
that commit, the container runs smoke.py (Pod mode: callbacks, keep-warm job
pulling) instead of the serverless handler, and the worker downloads the 79 GB
of weights to container disk at start, so the Pod may land in any data centre.
"""
import json,sys,urllib.request,urllib.error,subprocess
APP="/Users/macmini/workspace/papa/apps/video-generator"
V=dict(l.split("=",1) for l in subprocess.run(["railway","variables","--kv"],capture_output=True,text=True,cwd=APP).stdout.splitlines() if "=" in l)
K=V["RUNPOD_API_KEY"].strip()
HOST=V["RAILWAY_PUBLIC_DOMAIN"].strip()
ROOT="/models/PinkCherry-LTX-2.3-v1.8"
def api(method,path,body=None):
    req=urllib.request.Request("https://api.runpod.io/v2"+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization":f"Bearer {K}","User-Agent":"papa/1.0","Content-Type":"application/json"},method=method)
    try:
        r=urllib.request.urlopen(req,timeout=120); raw=r.read().decode()
        return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e: return {"err":e.code,"body":e.read().decode()[:400]}
if __name__=="__main__":
    sha=sys.argv[1]; suffix=sys.argv[2] if len(sys.argv)>2 else "defaults"
    env={
        "MODEL_ROOT": ROOT,
        "EAGER_LOAD_MODELS": "0",
        # Must stay on: the worker pulls its own weights at first use.
        "LTX_DOWNLOAD_ON_START": "1",
        # Gemma 3 is a gated repository; the token's account accepted its terms.
        "HF_TOKEN": V["HF_TOKEN"].strip(),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_XET_HIGH_PERFORMANCE": "1",
        "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
        "VIDEO_UPLOAD_URL": f"https://{HOST}/generate/api/internal/video-upload",
        "VIDEO_UPLOAD_TOKEN": V["VIDEO_UPLOAD_TOKEN"].strip(),
        "VIDEO_UPLOAD_TIMEOUT_SECONDS": "300",
        # Same knobs the serverless endpoint ran with.
        "LTX_QUANTIZATION": "auto",
        "LTX_OFFLOAD": "none",
        "LTX_INFERENCE_STEPS": "20",
        "LTX_DISTILLED_LORA_STRENGTH": "0.6",
        "SELF_HOSTED_MODEL_ID": "SexGod1979/PinkCherry_NSFW_LTX23",
        "SELF_HOSTED_MODEL_VERSION": "PinkCherry_FineTune_bf16_v1_8_LTX23",
        "SELF_HOSTED_WORKFLOW_VERSION": "pinkcherry-native-two-stage-v1",
    }
    body={
        "name": f"papa-ltx-volumefree-{sha[:7]}-{suffix}",
        "image": f"ghcr.io/ericstory/papa-ltx-video:{sha}",
        "args": json.dumps({"cmd":["python","/app/smoke.py"]}),
        "registry": "cmtgxws1c003d14njrtc07zd2",
        # ~79 GB of weights plus the image and working files.
        "disk": 120,
        "ports": ["8888/http","22/tcp"],
        "env": env,
        "category": "NVIDIA",
    }
    r=api("POST","/templates",body)
    if "err" in r: print("FAILED:", r); sys.exit(1)
    print("template id:", r.get("id"), "image:", r.get("image"))

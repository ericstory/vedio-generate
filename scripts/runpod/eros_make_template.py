"""Create a volume-free 10Eros Max Pod template for one H3 image SHA.

Usage: python3 eros_make_template.py <full-sha> [name-suffix]

Same image and Pod shape as h3_make_template.py; only the NSFW transformer pin
and the sampling recipe differ. The worker downloads the restored 10Eros Max
beta4 checkpoint (Andrew3453/10Eros-Max-h3-restored, see
workers/minimax-h3/restore_pruned_adaln.py) instead of PinkCherry, runs it
without the turbo LoRA because the TURBO distillation is merged into the
weights, and samples at the author's 6-8 step band.
"""
import json,sys,urllib.request,urllib.error,subprocess
APP="/Users/macmini/workspace/papa/apps/video-generator"
V=dict(l.split("=",1) for l in subprocess.run(["railway","variables","--kv"],capture_output=True,text=True,cwd=APP).stdout.splitlines() if "=" in l)
K=V["RUNPOD_API_KEY"].strip()
HOST=V["RAILWAY_PUBLIC_DOMAIN"].strip()
# Basename must be exactly "MiniMax-H3": sglang picks the native H3 pipeline
# config by matching the directory's short name against the HF repo id.
ROOT="/models/MiniMaxAI/MiniMax-H3"
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
        # Must stay online: the worker pulls its own weights at first use.
        "H3_DOWNLOAD_ON_START": "1",
        "HF_TOKEN": V["HF_TOKEN"].strip(),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
        "VIDEO_UPLOAD_URL": f"https://{HOST}/generate/api/internal/video-upload",
        "VIDEO_UPLOAD_TOKEN": V["VIDEO_UPLOAD_TOKEN"].strip(),
        "VIDEO_UPLOAD_TIMEOUT_SECONDS": "300",
        "H3_BASE_MODEL_ROOT": f"{ROOT}/FL2VA",
        "H3_MODEL_VARIANT": "fl2va",
        "H3_MODEL_ID": "MiniMaxAI/MiniMax-H3",
        "H3_MODEL_VERSION": "42ed227ee7df40d41602854ae760620d6eb651fe",
        "H3_NSFW_TRANSFORMER_ENABLED": "1",
        # download_models.py lays the checkpoint out under <root>/<profile subdir>/.
        "H3_NSFW_TRANSFORMER_PATH": f"{ROOT}/10eros/10Eros_Max_h3_TURBO-hybrid_beta4_sglang_bf16.safetensors",
        "H3_NSFW_MODEL_ID": "Andrew3453/10Eros-Max-h3-restored",
        "H3_NSFW_MODEL_VERSION": "a1e652bae8a8064e825741c30123feec39075640",
        # beta4 is a "TURBO-hybrid" merge: the distillation is already in the
        # weights, and the author says not to stack the turbo LoRA on top.
        "H3_TURBO_LORA_ENABLED": "0",
        # Author's recipe for beta4: euler/simple 6-8 steps. SGLang's H3 schedule
        # takes one more step than the denoise-interval count (the 8-step LoRA
        # runs at 9), so 8 here is 7 intervals, the middle of the band.
        "H3_INFERENCE_STEPS": "8",
        # No shift is published for beta4. ComfyUI, where it was tuned, runs H3
        # at sigma_shift_video=12 / sigma_shift_audio=3 by default
        # (comfy/ldm/minimax/model.py), so that is the setting the author's
        # step count was chosen against. The PinkCherry lane's 6/3 is the
        # turbo LoRA's training shift and does not apply without the LoRA.
        "H3_FLOW_SHIFT": "12.0",
        "H3_AUDIO_FLOW_SHIFT": "3.0",
        "H3_QUANTIZATION": "fp8",
        "H3_ATTENTION_BACKEND": "sage_attn",
        "H3_QUALITY": "lossless",
        "H3_SGLANG_VERSION": "0.5.18",
        "H3_WORKFLOW_VERSION": "h3-fl2va-10eros-beta4-v1",
        # Same DiT, same GPU, same attention kernel as the PinkCherry lane, so
        # the measured 0.097 s per (megapixel x frame x step) carries over.
        "H3_SECONDS_PER_MPIXEL_STEP": "0.1",
        "H3_DENOISE_BUDGET_SECONDS": "1500",
    }
    body={
        "name": f"papa-eros-volumefree-{sha[:7]}-{suffix}",
        "image": f"ghcr.io/ericstory/papa-minimax-h3:{sha}",
        "args": json.dumps({"cmd":["python","/app/smoke.py"]}),
        "registry": "cmtgxws1c003d14njrtc07zd2",
        # 78 GB of stock FL2VA plus the 66 GB restored DiT, the image and working files.
        "disk": 220,
        "ports": ["8888/http","22/tcp"],
        "env": env,
        "category": "NVIDIA",
    }
    r=api("POST","/templates",body)
    if "err" in r: print("FAILED:", r); sys.exit(1)
    print("template id:", r.get("id"), "image:", r.get("image"))

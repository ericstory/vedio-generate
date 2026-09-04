"""Create a volume-free H3 Pod template for one image SHA.

Usage: python3 mktemplate.py <full-sha> [name-suffix]

Every load-time knob now has its production value as the handler default
(commit 5a869e1), so this template carries no H3_EXTRA_SERVER_ARGS_JSON and no
H3_LORA_MERGE_MODE: what runs is exactly what the code says. The one value the
code cannot know is the measured timing constant for the pod-timeout guard.
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
        "H3_NSFW_TRANSFORMER_PATH": f"{ROOT}/pinkcherry/PinkCherry_fl2va_MiniMax_H3_bf16_beta-0.6.safetensors",
        "H3_NSFW_MODEL_ID": "SexGod1979/PinkCherry_MiniMax-H3",
        "H3_NSFW_MODEL_VERSION": "bf2fef11d0e55e957f4af997e3beade3362f44b3",
        "H3_TURBO_LORA_ENABLED": "1",
        "H3_TURBO_LORA_PATH": f"{ROOT}/turbo-lora/minimax_h3_fl2v_turbo_8step_v1.0_768p_bf16.safetensors",
        "H3_TURBO_LORA_STRENGTH": "1.0",
        "H3_LORA_TARGET": "all",
        "H3_INFERENCE_STEPS": "9",
        # The 8-step 768p export is rank 128 / alpha 8 and was distilled at
        # video shift 6, audio shift 3 (ModelTC/Minimax-H3-Turbo model specs,
        # lightx2v minimax_h3_fp8_8step.json). SGLang ignores the alpha in the
        # safetensors header and would run the LoRA 16x too strong; images built
        # from 79dec8f or earlier need the explicit alpha, newer ones read it.
        "H3_TURBO_LORA_ALPHA": "8",
        "H3_FLOW_SHIFT": "6.0",
        "H3_AUDIO_FLOW_SHIFT": "3.0",
        "H3_QUANTIZATION": "fp8",
        "H3_ATTENTION_BACKEND": "sage_attn",
        "H3_QUALITY": "lossless",
        "H3_SGLANG_VERSION": "0.5.18",
        "H3_WORKFLOW_VERSION": "h3-fl2va-pinkcherry-turbo8-v1",
        # Measured on the eighth real run (2026-09-03, RTX PRO 6000, 96 GB):
        # 108.4 s of denoise for 1344x768 x 120 frames x 9 steps
        # = 0.097 s per (megapixel x frame x step); rounded up.
        # 768p/15 s projects to 324 s, far inside the 1500 s budget.
        "H3_SECONDS_PER_MPIXEL_STEP": "0.1",
        "H3_DENOISE_BUDGET_SECONDS": "1500",
    }
    body={
        "name": f"papa-h3-volumefree-{sha[:7]}-{suffix}",
        "image": f"ghcr.io/ericstory/papa-minimax-h3:{sha}",
        "args": json.dumps({"cmd":["python","/app/smoke.py"]}),
        "registry": "cmtgxws1c003d14njrtc07zd2",
        # Holds ~145 GB of weights plus the image and working files.
        "disk": 220,
        "ports": ["8888/http","22/tcp"],
        "env": env,
        "category": "NVIDIA",
    }
    r=api("POST","/templates",body)
    if "err" in r: print("FAILED:", r); sys.exit(1)
    print("template id:", r.get("id"), "image:", r.get("image"))

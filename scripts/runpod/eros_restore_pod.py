"""Restore 10Eros Max's pruned AdaLN on a CPU Pod and publish the result to a private HF repo.

Usage:
  python3 eros_restore_pod.py create <git-sha> [vcpus]   # create the private repo if needed, launch the Pod
  python3 eros_restore_pod.py watch <pod-id> [seconds]   # print new container log lines for up to N seconds

The Pod pulls restore_pruned_adaln.py and regroup_qkv.py from this repository at
<git-sha> (public GitHub, raw), downloads the 40 GB curve-form export plus the
one stock shard carrying time_embedder.*, writes the 66 GB restored file,
verifies it against the curve table, uploads it, then deletes itself through
the RunPod API (the deletion must not depend on this machine: a stuck local
watcher once cost $3.7). A 5 hour timeout bounds a hung download or upload.

Reads RUNPOD_API_KEY and HF_TOKEN from `railway variables --kv`; run with
no_proxy='*' on a Mac whose system proxy is flaky.
"""
import base64, json, os, socket, subprocess, sys, time, urllib.error, urllib.request

APP = "/Users/macmini/workspace/papa/apps/video-generator"
V = dict(l.split("=", 1) for l in subprocess.run(["railway", "variables", "--kv"], capture_output=True, text=True, cwd=APP).stdout.splitlines() if "=" in l)
K = V.get("RUNPOD_API_KEY", "").strip()
HF = V.get("HF_TOKEN", "").strip()
H = {"Authorization": f"Bearer {K}", "User-Agent": "papa/1.0", "Content-Type": "application/json"}

SOURCE_REPO = "TenStrip/10Eros-Max"
SOURCE_REVISION = "3c071106f5b62c02b3cb0b7d831083cdb582b289"
SOURCE_FILE = "10Eros_Max_h3_TURBO-hybrid_beta4.safetensors"
DONOR_REPO = "MiniMaxAI/MiniMax-H3"
DONOR_REVISION = "42ed227ee7df40d41602854ae760620d6eb651fe"
DONOR_SHARD = "FL2VA/transformer/model-00001-of-00013.safetensors"
DONOR_INDEX = "FL2VA/transformer/model.safetensors.index.json"
TARGET_REPO = "Andrew3453/10Eros-Max-h3-restored"
TARGET_FILE = "beta4/10Eros_Max_h3_TURBO-hybrid_beta4_sglang_bf16.safetensors"
GITHUB_RAW = "https://raw.githubusercontent.com/ericstory/vedio-generate"

# Runs inside python:3.12-slim. Every step prints a STAGE line the watcher can grep for.
RUN_SH = r"""
set -euo pipefail
export HF_HUB_DISABLE_TELEMETRY=1 HF_XET_HIGH_PERFORMANCE=1 PYTHONUNBUFFERED=1
mkdir -p /work/in /work/out && cd /work
echo "STAGE bootstrap $(date -u +%FT%TZ) vcpus=$(nproc) mem=$(free -g | awk '/Mem/{print $2}')G disk_free=$(df -BG /work | awk 'NR==2{print $4}')"
pip install -q numpy "huggingface_hub[hf_xet]"
python3 - <<'PY'
import os, urllib.request
raw = os.environ["PAPA_RAW_BASE"]
for name in ("restore_pruned_adaln.py", "regroup_qkv.py"):
    urllib.request.urlretrieve(f"{raw}/workers/minimax-h3/{name}", f"/work/{name}")
print("STAGE scripts_fetched", flush=True)
from huggingface_hub import hf_hub_download
e = os.environ
for repo, rev, name in ((e["SOURCE_REPO"], e["SOURCE_REVISION"], e["SOURCE_FILE"]),
                        (e["DONOR_REPO"], e["DONOR_REVISION"], e["DONOR_SHARD"]),
                        (e["DONOR_REPO"], e["DONOR_REVISION"], e["DONOR_INDEX"])):
    path = hf_hub_download(repo, name, revision=rev, local_dir="/work/in")
    print("STAGE downloaded", name, os.path.getsize(path), flush=True)
PY
python3 /work/restore_pruned_adaln.py \
  --pruned "/work/in/$SOURCE_FILE" --donor "/work/in/$DONOR_SHARD" --index "/work/in/$DONOR_INDEX" \
  --output "/work/out/$(basename "$TARGET_FILE")" \
  --metadata "source_repo=$SOURCE_REPO" --metadata "source_revision=$SOURCE_REVISION" --metadata "source_file=$SOURCE_FILE" \
  --metadata "donor_repo=$DONOR_REPO" --metadata "donor_revision=$DONOR_REVISION" --metadata "restored_by=papa $PAPA_GIT_SHA"
echo "STAGE restored $(ls -l /work/out | tail -1)"
rm -rf /work/in
python3 - <<'PY'
import os
from huggingface_hub import HfApi
api = HfApi()
local = "/work/out/" + os.path.basename(os.environ["TARGET_FILE"])
info = api.upload_file(path_or_fileobj=local, path_in_repo=os.environ["TARGET_FILE"], repo_id=os.environ["TARGET_REPO"],
                       commit_message="Restore 10Eros Max beta4 full AdaLN + per-head QKV for SGLang (papa " + os.environ["PAPA_GIT_SHA"][:7] + ")")
print("STAGE uploaded", info.commit_url, flush=True)
print("STAGE revision", api.model_info(os.environ["TARGET_REPO"]).sha, flush=True)
PY
echo "STAGE done $(date -u +%FT%TZ)"
"""

# Outer shell: run the job under a timeout, then always delete this Pod.
START_CMD = (
    "echo \"$RESTORE_RUN_SH\" | base64 -d > /run.sh; "
    "timeout 18000 bash /run.sh; code=$?; echo \"STAGE exit code=$code\"; "
    "python3 -c \"import os,urllib.request;r=urllib.request.Request('https://rest.runpod.io/v1/pods/'+os.environ['RUNPOD_POD_ID'],method='DELETE',headers={'Authorization':'Bearer '+os.environ['RUNPOD_API_KEY']});print('STAGE self_delete',urllib.request.urlopen(r,timeout=60).status,flush=True)\" || echo 'STAGE self_delete failed'; "
    "sleep 60"
)


def api(method, url, body=None, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=H, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        raw = r.read().decode()
        return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        return {"err": e.code, "body": e.read().decode()[:600]}


def ensure_repo():
    from huggingface_hub import HfApi

    hf = HfApi(token=HF)
    url = hf.create_repo(TARGET_REPO, private=True, exist_ok=True, repo_type="model")
    info = hf.model_info(TARGET_REPO)
    print(f"target repo {url} private={info.private} sha={info.sha}")


def create(sha: str, vcpus: int):
    ensure_repo()
    env = {
        "PAPA_RAW_BASE": f"{GITHUB_RAW}/{sha}",
        "PAPA_GIT_SHA": sha,
        "SOURCE_REPO": SOURCE_REPO, "SOURCE_REVISION": SOURCE_REVISION, "SOURCE_FILE": SOURCE_FILE,
        "DONOR_REPO": DONOR_REPO, "DONOR_REVISION": DONOR_REVISION, "DONOR_SHARD": DONOR_SHARD, "DONOR_INDEX": DONOR_INDEX,
        "TARGET_REPO": TARGET_REPO, "TARGET_FILE": TARGET_FILE,
        "HF_TOKEN": HF,
        "RUNPOD_API_KEY": K,
        "RESTORE_RUN_SH": base64.b64encode(RUN_SH.encode()).decode(),
    }
    body = {
        "name": f"papa-eros-restore-{sha[:7]}",
        "computeType": "CPU",
        "cloudType": "SECURE",
        "cpuFlavorIds": ["cpu3c", "cpu3g", "cpu5c", "cpu5g"],
        "cpuFlavorPriority": "custom",
        "vcpuCount": vcpus,
        # 40 GB input + 5 GB donor + 66 GB output; the input is removed before the upload.
        "containerDiskInGb": 160,
        "volumeInGb": 0,
        "imageName": "python:3.12-slim",
        "dockerStartCmd": ["bash", "-c", START_CMD],
        "ports": [],
        "env": env,
    }
    r = api("POST", "https://rest.runpod.io/v1/pods", body)
    if "err" in r:
        print("FAILED:", r)
        sys.exit(1)
    print(f"POD {r.get('id')} name={r.get('name')} dc={r.get('dataCenterId')} cost/h={r.get('costPerHr')} status={r.get('desiredStatus')}")


def watch(pod_id: str, seconds: float):
    """Stream container log lines for a bounded wall-clock window; the SSE stream itself never ends."""
    req = urllib.request.Request(f"https://api.runpod.io/v2/pods/{pod_id}/logs?tail=2000&source=container", headers={**H, "Accept": "text/event-stream"})
    deadline = time.monotonic() + seconds
    seen = set()
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        while time.monotonic() < deadline:
            try:
                raw = resp.readline()
            except (socket.timeout, TimeoutError):
                continue
            if not raw:
                break
            s = raw.decode(errors="replace").strip()
            if s.startswith("data:"):
                try:
                    line = str(json.loads(s[5:].strip()).get("line", ""))
                except Exception:
                    line = s[5:].strip()
                if line and line not in seen:
                    seen.add(line)
                    print(line, flush=True)
    except urllib.error.HTTPError as e:
        print("log error", e.code, e.read().decode()[:200])
    except Exception as e:
        print("log stream ended:", type(e).__name__, e)
    r = api("GET", f"https://rest.runpod.io/v1/pods/{pod_id}")
    print("POD STATUS:", {k: r.get(k) for k in ("id", "desiredStatus", "lastStatusChange", "costPerHr", "dataCenterId")} if "err" not in r else r)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "create":
        create(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 8)
    elif cmd == "watch":
        watch(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 60)
    else:
        print(__doc__)

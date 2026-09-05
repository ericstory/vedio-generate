"""Step 8 of the handoff: delete the residual serverless endpoints and the
network volumes nothing references any more.

Usage: python3 cleanup_runpod.py            # dry run: show what would go
       python3 cleanup_runpod.py --yes      # actually delete
       python3 cleanup_runpod.py --retire-wan-ltx [--yes]   # 2026-09-04: also the Wan volumes and every Wan/LTX template

What stays, and why (checked against Railway variables on 2026-09-04):
  endpoint aoma1602mogius  LTX production (RUNPOD_ENDPOINT_ID), still serverless
  volume   fn6at7unxa      LTX weights in US-NE-1, mounted by that endpoint
  volume   3xl6dvrx0p      Wan Pod lane primary   (RUNPOD_WAN_POD_NETWORK_VOLUME_ID)
  volume   nv7g5aobqn      Wan Pod lane fallback  (RUNPOD_WAN_POD_FALLBACK_NETWORK_VOLUME_ID)
Everything else in the two lists below is a leftover: H3 went volume-free,
the Wan serverless chain was replaced by the Pod lane, and the validation
endpoints were one-offs. Volumes bill 7x24; the six below are 760 GB, about
$53 a month. Volume deletion cannot be undone -- the weights come back only by
downloading them again (CPU pod, ~5 minutes, see the handoff).

Deleting the Wan serverless endpoint 5mvpqm0pq8pvxs also means removing
RUNPOD_WAN_ENDPOINT_ID from Railway, otherwise the guard loop keeps pinging a
dead endpoint every tick; RUNPOD_H3_POD_FALLBACK_NETWORK_VOLUME_ID points at
qextiwmyla and should go for the same reason.
"""
import json, os, subprocess, sys, urllib.error, urllib.request

os.environ["no_proxy"] = "*"
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
V = dict(
    l.split("=", 1)
    for l in subprocess.run(
        ["railway", "variables", "--kv"], capture_output=True, text=True, cwd=APP
    ).stdout.splitlines()
    if "=" in l
)
K = V["RUNPOD_API_KEY"].strip()

ENDPOINTS = {
    "5mvpqm0pq8pvxs": "papa-wan22-adult-v3-av-h100-ne1-production (Wan moved to the Pod lane)",
    "h1q5llz6q5avv0": "papa-wan22-blackwell96-ne1-fallback",
    "jxcnfxecre8tk0": "papa-ltx-pinkcherry-ne1-production (older LTX endpoint, not RUNPOD_ENDPOINT_ID)",
    "czkujdsj8tcsi9": "papa-ltx-pinkcherry-l40-ks2-validation",
    "xl37iimj46ih7p": "papa-wan22-fp8-l40-ks2-validation",
}
VOLUMES = {
    "n7meo4oft2": "papa-h3-pinkcherry-models-nc2 170GB (H3 is volume-free)",
    "qextiwmyla": "papa-h3-pinkcherry-models-ks2 170GB (H3 is volume-free)",
    "mmw8n3z0t2": "pinkcherry-ltx23-models-nc2 100GB (no endpoint)",
    "i7seye8y8v": "pinkcherry-ltx23-models-ks2 100GB (validation endpoint only)",
    "p7dkzdmomf": "papa-wan22-adult-models-ne1 150GB (Wan serverless endpoints only)",
    "qqvyvldtu1": "papa-wan22-fp8-models-ne1 70GB (nothing)",
}
KEEP_ENDPOINTS = {V.get("RUNPOD_ENDPOINT_ID", "").strip(), "aoma1602mogius"} - {""}
KEEP_VOLUMES = {
    "fn6at7unxa",
    V.get("RUNPOD_WAN_POD_NETWORK_VOLUME_ID", "").strip(),
    V.get("RUNPOD_WAN_POD_FALLBACK_NETWORK_VOLUME_ID", "").strip(),
} - {""}
# Handoff step 9: once the LTX Pod lane (LTX_POD_ENABLED=1) has produced a real
# video, the serverless endpoint and its volume are the last legacy resources.
# Opt in explicitly; the default run still protects them.
if "--ltx-serverless" in sys.argv:
    ENDPOINTS["aoma1602mogius"] = "papa LTX serverless production (LTX moved to the Pod lane)"
    VOLUMES["fn6at7unxa"] = "LTX weights in US-NE-1 100GB (Pod lane is volume-free)"
    KEEP_ENDPOINTS -= {"aoma1602mogius", V.get("RUNPOD_ENDPOINT_ID", "").strip()}
    KEEP_VOLUMES -= {"fn6at7unxa"}

# 2026-09-04: Wan and LTX are retired (user decision; only the two H3 lanes
# stay). Their last billed resources are the two Wan Pod-lane volumes; their
# templates cost nothing but clutter the console. Opt in explicitly.
TEMPLATES: dict[str, str] = {}
RETIRE = "--retire-wan-ltx" in sys.argv
if RETIRE:
    VOLUMES["3xl6dvrx0p"] = "papa-wan22-fp8-models-ks2 70GB (Wan retired)"
    VOLUMES["nv7g5aobqn"] = "papa-wan22-fp8-models-nc2 70GB (Wan retired)"
    KEEP_VOLUMES -= {"3xl6dvrx0p", "nv7g5aobqn"}
    TEMPLATES.update({
        "kqwme3u0h9": "papa-ltx-pinkcherry-ne1-h100-production-template",
        "bohcaweu3r": "papa-ltx-video-0f114c3",
        "9xmzekz5td": "papa-ltx-video-1c29ff1",
        "qr0q0vgq6e": "papa-ltx-video-95a387a",
        "y37fgo6woo": "papa-ltx-video-eb1d706",
        "km9g8f4guq": "papa-ltx-video-pro6000-pod-87901bc",
        "cbj3cbaipw": "papa-ltx-volumefree-35ce3e0-defaults (LTX Pod lane production template)",
        "xsv2a2pbe5": "papa-wan22-adult-v3-av-h100-ne1-production-template",
        "hxisofun9i": "papa-wan22-fp8-pod-smoke-93f09e3",
        "wjxhc0dtid": "papa-wan22-fp8-resident96-pod-2d53dce",
        "kvpiykwb5d": "papa-wan22-fp8-sglang-93aeb9f",
    })
RETIRED_RAILWAY_VARS = [
    "WAN_V2_ENABLED", "WAN_MODEL_ID", "WAN_MODEL_VERSION", "WAN_WORKFLOW_VERSION",
    "WAN_ADULT_ADAPTER_ID", "WAN_ADULT_ADAPTER_VERSION", "WAN_ADULT_ADAPTER_STRENGTH",
    "RUNPOD_WAN_POD_TEMPLATE_ID", "RUNPOD_WAN_POD_NETWORK_VOLUME_ID", "RUNPOD_WAN_POD_FALLBACK_NETWORK_VOLUME_ID",
    "RUNPOD_WAN_POD_CALLBACK_URL", "RUNPOD_WAN_POD_GPU_ID", "RUNPOD_WAN_POD_DATA_CENTER_ID",
    "RUNPOD_WAN_POD_FALLBACK_DATA_CENTER_ID", "RUNPOD_WAN_POD_ADDITIONAL_REGION_VOLUMES",
    "RUNPOD_WAN_POD_MAX_PRICE_PER_HOUR", "RUNPOD_WAN_POD_MAX_RUNTIME_SECONDS",
    "LTX_POD_ENABLED", "RUNPOD_LTX_POD_TEMPLATE_ID", "RUNPOD_LTX_POD_CALLBACK_URL",
    "RUNPOD_LTX_POD_KEEP_WARM_SECONDS", "RUNPOD_LTX_POD_MAX_RUNTIME_SECONDS", "RUNPOD_LTX_POD_PREFERRED_DATA_CENTER_IDS",
]


def api(method, path):
    req = urllib.request.Request(
        "https://rest.runpod.io/v1" + path,
        headers={"Authorization": f"Bearer {K}", "User-Agent": "papa/1.0"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        return {"err": e.code, "body": e.read().decode()[:300]}


def main() -> int:
    assert not (set(ENDPOINTS) & KEEP_ENDPOINTS), "refusing to delete a production endpoint"
    assert not (set(VOLUMES) & KEEP_VOLUMES), "refusing to delete a volume a live lane mounts"
    live_endpoints = {e["id"]: e for e in api("GET", "/endpoints")}
    live_volumes = {v["id"]: v for v in api("GET", "/networkvolumes")}
    pods = api("GET", "/pods")
    if pods:
        print(f"{len(pods)} Pod(s) running; finish or delete them first:", [p.get("id") for p in pods])
        return 2
    do_it = "--yes" in sys.argv
    for eid, why in ENDPOINTS.items():
        if eid not in live_endpoints:
            print(f"endpoint {eid} already gone")
            continue
        print(("DELETE " if do_it else "would delete ") + f"endpoint {eid}: {why}")
        if do_it:
            print("   ->", api("DELETE", f"/endpoints/{eid}") or "ok")
    for vid, why in VOLUMES.items():
        if vid not in live_volumes:
            print(f"volume {vid} already gone")
            continue
        mounted_by = [e["id"] for e in live_endpoints.values() if e.get("networkVolumeId") == vid and e["id"] not in ENDPOINTS]
        if mounted_by:
            print(f"SKIP volume {vid}: still mounted by {mounted_by}")
            continue
        print(("DELETE " if do_it else "would delete ") + f"volume {vid}: {why}")
        if do_it:
            print("   ->", api("DELETE", f"/networkvolumes/{vid}") or "ok")
    live_templates = {t["id"]: t for t in api("GET", "/templates")}
    for tid, why in TEMPLATES.items():
        if tid not in live_templates:
            print(f"template {tid} already gone")
            continue
        if tid in {V.get("RUNPOD_H3_POD_TEMPLATE_ID", "").strip(), V.get("RUNPOD_EROS_POD_TEMPLATE_ID", "").strip()}:
            print(f"SKIP template {tid}: a live H3 lane uses it")
            continue
        print(("DELETE " if do_it else "would delete ") + f"template {tid}: {why}")
        if do_it:
            print("   ->", api("DELETE", f"/templates/{tid}") or "ok")
    if do_it:
        left = api("GET", "/networkvolumes")
        print(f"remaining volumes: {sum(v.get('size', 0) for v in left)} GB across {len(left)}; "
              f"remaining endpoints: {[e['id'] for e in api('GET', '/endpoints')]}")
        print("Now on Railway (one key per command, then redeploy):")
        print("  railway variable delete RUNPOD_WAN_ENDPOINT_ID")
        print("  railway variable delete RUNPOD_H3_POD_FALLBACK_NETWORK_VOLUME_ID")
        if "--ltx-serverless" in sys.argv:
            print("  railway variable delete RUNPOD_ENDPOINT_ID")
        if RETIRE:
            print("  # Wan/LTX variables (each delete redeploys; the code that read them is gone after the retirement commit):")
            for name in RETIRED_RAILWAY_VARS:
                if name in V:
                    print(f"  railway variable delete {name}")
        print("  railway up --detach")
    else:
        print("dry run only; re-run with --yes to delete")
    return 0


if __name__ == "__main__":
    sys.exit(main())

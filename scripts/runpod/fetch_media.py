"""Download one generated video with the admin login and print frame/audio stats.

Usage: python3 fetch_media.py <name> <video_url-or-media-uuid> [out-dir]

The media route needs a session cookie (a bare curl gets 25 bytes), which is
why fetch_and_inspect_video.sh needed a cookie file; this logs in itself,
downloads with resume through the flaky edge, writes frames at 0.5/2.5/4.5 s
and prints luma and loudness so a "succeeded" callback can be checked by eye.
"""
import http.cookiejar, json, os, subprocess, sys, time, urllib.request, urllib.error

APP = "/Users/macmini/workspace/papa/apps/video-generator"
V = dict(l.split("=", 1) for l in subprocess.run(["railway", "variables", "--kv"], capture_output=True, text=True, cwd=APP).stdout.splitlines() if "=" in l)
HOST = V["RAILWAY_PUBLIC_DOMAIN"].strip()
BASE = f"https://{HOST}/generate"
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
UA = {"User-Agent": "papa/1.0"}


def login():
    req = urllib.request.Request(BASE + "/api/login", data=json.dumps({"username": V["ADMIN_USERNAME"].strip(), "password": V["ADMIN_PASSWORD"].strip()}).encode(), headers={**UA, "Content-Type": "application/json"})
    op.open(req, timeout=60).read()


def download(url: str, path: str) -> int:
    have = os.path.getsize(path) if os.path.exists(path) else 0
    for attempt in range(60):
        try:
            req = urllib.request.Request(url, headers={**UA, **({"Range": f"bytes={have}-"} if have else {})})
            with op.open(req, timeout=120) as resp, open(path, "ab" if have else "wb") as out:
                total = resp.headers.get("Content-Range", "").rsplit("/", 1)[-1] or resp.headers.get("Content-Length", "?")
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    have += len(chunk)
            print(f"downloaded {have} bytes (server total {total})", flush=True)
            if total in ("?", str(have)):
                return have
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            print(f"retry {attempt}: {type(exc).__name__} {exc} (have {have})", flush=True)
            time.sleep(3)
    raise SystemExit("download incomplete")


if __name__ == "__main__":
    name, ref = sys.argv[1], sys.argv[2]
    out_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.environ.get("SCRATCH", "/tmp/papa-h3"), "frames")
    os.makedirs(out_dir, exist_ok=True)
    url = ref if ref.startswith("http") else (f"https://{HOST}{ref}" if ref.startswith("/") else f"{BASE}/media/{ref}.mp4")
    login()
    target = os.path.join(out_dir, f"{name}.mp4")
    download(url, target)
    print(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,nb_frames,sample_rate", "-show_entries", "format=duration,bit_rate", "-of", "compact", target], capture_output=True, text=True).stdout)
    for t in ("0.5", "2.5", "4.5"):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", t, "-i", target, "-frames:v", "1", "-vf", "scale=672:-1", os.path.join(out_dir, f"{name}_{t}s.png")])
    luma = subprocess.run(["ffmpeg", "-v", "info", "-i", target, "-vf", "select='not(mod(n,30))',signalstats,metadata=print:file=-", "-f", "null", "-"], capture_output=True, text=True).stdout
    vals = [line.split("=")[1] for line in luma.splitlines() if "YAVG=" in line or "YSTDEV=" in line]
    print("luma YAVG/YSTDEV per 30th frame:", " ".join(vals[:12]))
    audio = subprocess.run(["ffmpeg", "-v", "info", "-i", target, "-vn", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    print("audio:", " ".join(line.split("] ")[-1] for line in audio.splitlines() if "mean_volume" in line or "max_volume" in line))
    print("frames in", out_dir)

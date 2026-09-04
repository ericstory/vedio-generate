# RunPod ops scripts (H3 lane)

All of these read `RUNPOD_API_KEY`, the Railway host and the admin login from
`railway variables --kv` at run time; nothing secret is stored here. Run them
from anywhere, but with `no_proxy='*'` on a Mac that has a flaky system proxy,
otherwise urllib hangs on 127.0.0.1:8080.

| Script | What it does |
| --- | --- |
| `h3_make_template.py <sha> [suffix]` | Create a volume-free H3 Pod template for one GHCR image SHA. |
| `ltx_make_template.py <sha> [suffix]` | Same for the LTX lane (image `papa-ltx-video`, 120 GB disk, `smoke.py` entrypoint). |
| `eros_make_template.py <sha> [suffix]` | Same for the 10Eros Max lane: the H3 image with the restored 10Eros beta4 transformer pinned, no turbo LoRA, 8 steps, shift 12/3. |
| `eros_restore_pod.py create <sha> [vcpus]` / `watch <pod> [s]` | One-off: restore 10Eros Max's pruned AdaLN on a self-deleting CPU Pod (inputs streamed from the Hub by byte range, 66 GB output uploaded to the private `Andrew3453/10Eros-Max-h3-restored`). |
| `h3_diag_create.py '<json env overrides>' <tag>` | Create one diagnostic Pod from the live template with env overrides (no callbacks). `DIAG_DCS=US-NC-1,US-NC-2` pins data centres. |
| `h3_diag_watch.py <pod> <tag> [ceiling_s]` | Stream that Pod's log, print stage/LoRA/error lines, save the log, delete the Pod. The SSE log stream sends keep-alives forever, so reads are capped by wall time. |
| `pods.py list|delete <id>…` | List or delete Pods. |
| `cleanup_runpod.py [--yes] [--ltx-serverless]` | Handoff step 8: delete the residual serverless endpoints and the volumes nothing mounts. Dry run without `--yes`; refuses to touch the LTX production endpoint/volume or the Wan Pod lane volumes unless `--ltx-serverless` is passed (step 9, after the LTX Pod lane has produced a real video). |
| `h3_submit_and_follow.py [prompt] [seconds]` | Submit through the production API and follow the task to a terminal state. `MODEL=pinkcherry-ltx-2.3-v1.8 RESOLUTION=480p` targets the LTX lane. |
| `follow_task.py <task-id> [--once]` | Follow an existing task (`MAX_SECONDS` bounds the wait). Pair with `FOLLOW=0` on the submit script when the follower must run in bounded foreground chunks. |
| `fetch_and_inspect_video.sh <name> <media-uuid>` | Download a result with resume, then print luma/audio stats and write frames. **Always look at frames: a succeeded callback proved nothing on 2026-09-03.** |

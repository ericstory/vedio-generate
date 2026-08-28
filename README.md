# AI Vedio

统一 UI、三条独立生产链路的视频生成平台：BytePlus Seedance、自建 RunPod LTX 2.3 V1，
以及自建 RunPod Wan 2.2 A14B + 强制成人 LoRA V2。

> 目录名沿用 `ai-vedio`；Python 包名使用合法标识符 `ai_vedio`。

## 已验证能力

- 全球版 ModelArk：`ark.ap-southeast.bytepluses.com`
- Seedance 2.5、2.0 Mini、2.0 Fast、2.0 四个自定义推理端点
- PinkCherry LTX 2.3 v1.8 原生 Python two-stage 推理（不依赖 ComfyUI）
- Railway 控制面与 RunPod GPU 计算面独立部署，输出持久化到 R2/S3
- 异步创建、查询、轮询和下载视频任务
- ModelArk 虚拟资产库 AK/SK 签名调用
- 资产库 URI：`asset://<asset-id>`

四个端点已在 2026-08-25（America/Los_Angeles）以 4 秒、480p、16:9 文生视频完成端到端测试。

## 本地使用

```bash
cd /Users/anita/workspace/ai-vedio
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

配置已放在本地 `.env`，该文件已加入 `.gitignore`。提交代码时只提交 `.env.example`。

```python
from ai_vedio import SeedanceClient, load_settings

settings = load_settings()
client = SeedanceClient(settings)

task = client.create_text_video(
    prompt="A calm sunrise over the ocean, cinematic wide shot",
    model="seedance-2-fast",
    duration=4,
    resolution="480p",
    ratio="16:9",
    generate_audio=False,
)
result = client.wait_for_task(task["id"])
client.download_video(result, "outputs/demo.mp4")
```

只读连通性检查：

```bash
python scripts/smoke_test.py
```

执行真实、会产生费用的视频生成测试：

```bash
python scripts/smoke_test.py --generate --model seedance-2-fast
```

## 管理后台与视频生成器

应用提供带管理员登录的 `/generate` 页面，包括提示词、参考图、画幅、清晰度、时长、音效和任务清单。

先在 `.env` 配置以下项目（生产环境请使用平台的 Secret，不要提交 `.env`）：

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请设置高强度密码
SESSION_SECRET=请设置至少32位的随机字符串
APP_BASE_PATH=/generate
TASK_DATABASE_PATH=data/tasks.db
VIDEO_OUTPUT_DIR=data/generated-videos
VIDEO_UPLOAD_TOKEN=<long-random-shared-worker-token>
```

For the private self-hosted chain, the GPU worker uploads the finished MP4 to the authenticated
internal endpoint under `APP_BASE_PATH`. Railway writes it to the existing persistent volume and
only serves it to a valid admin session. Configure the same `VIDEO_UPLOAD_TOKEN` on Railway and in
the RunPod worker template; do not expose this token to frontend code.

Railway also acts as a cost guard for the private RunPod endpoint. It keeps `workersMax=0` while
idle, opens the endpoint to one worker immediately before submitting a self-hosted job, polls that
job in the background, and restores `workersMax=0` after a terminal result. Keep
`RUNPOD_COST_GUARD_ENABLED=1`; only one self-hosted job may be active at a time.

本地启动：

```bash
pip install -e '.[dev]'
COOKIE_SECURE=0 uvicorn ai_vedio.web:app --reload
```

打开 `http://127.0.0.1:8000/generate`。生产环境保持 `COOKIE_SECURE=1`；应用以
独立服务部署，不依赖或修改 whichdrama 主工程。`data/` 应挂载持久卷。

容器启动：

```bash
docker build -t ai-video-generator .
docker run --env-file .env -p 8000:8000 -v "$PWD/data:/app/data" ai-video-generator
```

也可以运行 `docker compose up -d --build`。Railway 部署使用独立的
`video-generator` 服务，并将持久卷挂载到 `/app/data`。健康检查地址为
`/generate/healthz`。

## 自建 PinkCherry LTX 2.3 链路

```text
浏览器 /generate
  → Railway FastAPI（本地 job_id、SQLite 任务记录）
  → RunPod Serverless /run（独立 provider_job_id）
  → PinkCherry LTX 2.3 v1.8 GPU Worker（提示词生成同步音视频）
  → Cloudflare R2 / S3（持久输出 URL）
```

GPU Worker 位于 `workers/ltx-video/`。Railway 只需配置 `RUNPOD_API_KEY` 与
`RUNPOD_ENDPOINT_ID`，不保存模型权重，也不承担视频推理。RunPod 异步结果只短期保留，
因此 Worker 在返回前必须先将 MP4 上传到 R2/S3。

默认使用 PinkCherry v1.8 BF16 checkpoint，并通过官方 native two-stage pipeline 加载
LTX 2.3 distilled LoRA、spatial upscaler 与 Gemma 3 文本编码器。所有仓库 revision、
关键文件大小和 SHA256 固定在 `workers/ltx-video/models.lock.json`。模型权重约 79.2GB，
不进入应用镜像或 Git。

该私有研究服务允许合规的成年人内容。

## V2 质量链路规划

当前 PinkCherry LTX 2.3 链路作为 V1 保留。第二条自建质量链路计划采用独立的 Wan 2.2
A14B Worker、Endpoint 和模型卷，不原地替换 V1。模型判断、近期成片质量诊断、GPU/存储
预算、固定 A/B 验收矩阵和付费边界见
[`docs/video-pipeline-v2.md`](docs/video-pipeline-v2.md)。

V2 Worker 已位于 `workers/wan-video/`：支持 4–15 秒 T2V、全部六种 UI 画幅和 480p/720p，
成人高/低噪声 LoRA 都是必选，并用 AudioLDM2 生成同长度环境声/音效后封装 AAC 音轨。
V1 保持 RunPod 96GB Pro GPU 池；V2 生产 Endpoint 使用已通过 15 秒音视频实测的 H100
80GB 池（96GB Pro 在本轮部署时没有 Serverless 库存）。生产开关 `WAN_V2_ENABLED` 默认
关闭，必须在独立 Endpoint 完成真实 GPU 冒烟测试后才开启。

## 安全

- 不要在日志、异常页面或前端代码中暴露 API Key、AK、SK。
- `.env` 仅用于本地开发；部署时使用 Secret Manager 或平台环境变量。
- 视频下载 URL 是临时签名 URL，应及时转存到自己的持久化对象存储。

# AI Vedio

全球版 BytePlus ModelArk / Seedance 视频生成平台基础工程。

> 目录名沿用 `ai-vedio`；Python 包名使用合法标识符 `ai_vedio`。

## 已验证能力

- 全球版 ModelArk：`ark.ap-southeast.bytepluses.com`
- Seedance 2.5、2.0 Mini、2.0 Fast、2.0 四个自定义推理端点
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
```

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

## 安全

- 不要在日志、异常页面或前端代码中暴露 API Key、AK、SK。
- `.env` 仅用于本地开发；部署时使用 Secret Manager 或平台环境变量。
- 视频下载 URL 是临时签名 URL，应及时转存到自己的持久化对象存储。

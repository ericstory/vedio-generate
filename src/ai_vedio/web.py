from __future__ import annotations

import base64
import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .capabilities import SELF_HOSTED_MODELS, SUPPORTED_MODELS
from .config import PROJECT_ROOT, load_runpod_settings, load_settings
from .runpod import RunPodClient
from .seedance import SeedanceClient, SeedanceError


PACKAGE_DIR = Path(__file__).resolve().parent
WEB_DIR = PACKAGE_DIR / "web_assets"
SESSION_COOKIE = "ai_video_session"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic"}
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_VIDEO_BYTES = 250 * 1024 * 1024
ALLOWED_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}
ALLOWED_RESOLUTIONS = {"480p", "720p", "1080p"}
ALLOWED_DURATIONS = set(range(4, 16))
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "expired"}


@dataclass(frozen=True)
class WebSettings:
    username: str
    password: str
    session_secret: str
    base_path: str
    database_path: Path
    secure_cookie: bool
    cookie_domain: str | None = None
    video_upload_token: str = ""
    video_output_dir: Path | None = None
    runpod_cost_guard_enabled: bool = False
    runpod_cost_guard_poll_seconds: float = 8.0


def load_web_settings() -> WebSettings:
    # load_settings handles .env, but the web app can still show the login page
    # before ModelArk credentials are validated.
    from .config import _load_dotenv

    _load_dotenv(PROJECT_ROOT / ".env")
    base_path = os.getenv("APP_BASE_PATH", "/generate").strip() or "/generate"
    base_path = "/" + base_path.strip("/")
    database = Path(os.getenv("TASK_DATABASE_PATH", "data/tasks.db"))
    if not database.is_absolute():
        database = PROJECT_ROOT / database
    output_dir = Path(
        os.getenv("VIDEO_OUTPUT_DIR")
        or str(
            Path(os.getenv("RAILWAY_VOLUME_MOUNT_PATH", database.parent))
            / "generated-videos"
        )
    )
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    return WebSettings(
        username=os.getenv("ADMIN_USERNAME", "admin"),
        password=os.getenv("ADMIN_PASSWORD", ""),
        session_secret=os.getenv("SESSION_SECRET", ""),
        base_path=base_path,
        database_path=database,
        secure_cookie=os.getenv("COOKIE_SECURE", "1") != "0",
        cookie_domain=os.getenv("COOKIE_DOMAIN") or None,
        video_upload_token=os.getenv("VIDEO_UPLOAD_TOKEN", ""),
        video_output_dir=output_dir,
        runpod_cost_guard_enabled=os.getenv("RUNPOD_COST_GUARD_ENABLED", "1") != "0",
        runpod_cost_guard_poll_seconds=float(
            os.getenv("RUNPOD_COST_GUARD_POLL_SECONDS", "8")
        ),
    )


class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL DEFAULT 'seedance',
                    provider_task_id TEXT,
                    prompt TEXT NOT NULL,
                    model TEXT NOT NULL,
                    ratio TEXT NOT NULL,
                    resolution TEXT NOT NULL,
                    duration INTEGER NOT NULL,
                    has_reference INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    video_url TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
            if "provider" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN provider TEXT NOT NULL DEFAULT 'seedance'")
            if "provider_task_id" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN provider_task_id TEXT")
            db.execute(
                "UPDATE tasks SET provider_task_id=id WHERE provider_task_id IS NULL OR provider_task_id=''"
            )

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def create(self, task: dict[str, Any]) -> None:
        task = {
            "provider": "seedance",
            "provider_task_id": task["id"],
            **task,
        }
        with self.connect() as db:
            db.execute(
                """INSERT INTO tasks
                (id, provider, provider_task_id, prompt, model, ratio, resolution, duration, has_reference,
                 status, video_url, error, created_at, updated_at)
                VALUES (:id, :provider, :provider_task_id, :prompt, :model, :ratio, :resolution, :duration,
                        :has_reference, :status, NULL, NULL, :created_at, :created_at)""",
                task,
            )

    def update_remote(self, task_id: str, remote: dict[str, Any]) -> None:
        status = str(remote.get("status") or "processing")
        content = remote.get("content") or {}
        error = remote.get("error")
        if isinstance(error, (dict, list)):
            error = json.dumps(error, ensure_ascii=False)
        with self.connect() as db:
            db.execute(
                """UPDATE tasks SET status=?, video_url=?, error=?, updated_at=? WHERE id=?""",
                (
                    status,
                    content.get("video_url"),
                    str(error) if error else None,
                    _now(),
                    task_id,
                ),
            )

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_public_task(dict(row)) for row in rows]

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _public_task(dict(row)) if row else None

    def active_runpod(self) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
        with self.connect() as db:
            rows = db.execute(
                f"""SELECT * FROM tasks
                WHERE provider='runpod' AND status NOT IN ({placeholders})
                ORDER BY created_at ASC""",
                tuple(sorted(TERMINAL_STATUSES)),
            ).fetchall()
        return [dict(row) for row in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    task["has_reference"] = bool(task.get("has_reference"))
    return task


def _generation_error(exc: SeedanceError) -> JSONResponse:
    upstream = exc.upstream_message
    searchable = f"{exc.code or ''} {upstream}".lower()
    code = exc.code or "GENERATION_REJECTED"
    status_code = 422

    if any(term in searchable for term in ("moderation", "safety", "sensitive", "policy", "risk", "blocked")):
        detail = "内容安全审核未通过"
        guidance = [
            "删除露骨、暴力、违法或可能侵犯他人权益的描述",
            "使用合规的服装、动作和镜头语言重新描述画面",
            "确认参考素材拥有合法授权",
        ]
    elif any(term in searchable for term in ("face", "portrait", "real person", "real-person", "human asset")):
        detail = "真人参考素材不能直接上传"
        guidance = [
            "先在 BytePlus 受信任真人素材库完成真人验证",
            "生成时使用素材库返回的 asset:// 素材 ID",
            "或改用不包含可识别真人面孔的参考图",
        ]
    elif any(term in searchable for term in ("image", "pixel", "resolution", "aspect", "format")):
        detail = "参考图片不符合模型要求"
        guidance = [
            "使用 JPG、PNG、WebP、GIF 或 HEIC 图片",
            "图片需小于 30MB，宽高均为 300–6000px",
            "图片宽高比需处于 0.4–2.5 之间",
        ]
    elif exc.status_code == 429 or any(term in searchable for term in ("rate", "burst", "overload", "concurrency")):
        detail = "生成服务当前繁忙"
        guidance = ["等待 30–60 秒后重试", "避免连续点击提交", "如持续发生，请检查模型端点配额"]
        status_code = 429
    else:
        detail = "生成服务拒绝了本次请求"
        guidance = [
            "检查提示词是否符合内容规范",
            "若包含真人参考图，请先加入受信任素材库",
            "移除参考图后可先测试纯文本生成",
        ]
        if exc.status_code and exc.status_code >= 500:
            status_code = 502

    return JSONResponse(
        status_code=status_code,
        content={
            "detail": detail,
            "code": code,
            "upstream_message": upstream[:500],
            "guidance": guidance,
        },
    )


def _sign_session(username: str, secret: str) -> str:
    nonce = secrets.token_urlsafe(12)
    issued_at = int(datetime.now(timezone.utc).timestamp())
    payload = base64.urlsafe_b64encode(f"{username}:{issued_at}:{nonce}".encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _valid_session(token: str | None, username: str, secret: str) -> bool:
    if not token or not secret or "." not in token:
        return False
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        token_username, issued_at, _ = decoded.split(":", 2)
        age = int(datetime.now(timezone.utc).timestamp()) - int(issued_at)
    except (ValueError, UnicodeDecodeError):
        return False
    return 0 <= age <= 12 * 60 * 60 and hmac.compare_digest(token_username, username)


def _require_auth(request: Request) -> None:
    settings: WebSettings = request.app.state.web_settings
    if not _valid_session(request.cookies.get(SESSION_COOKIE), settings.username, settings.session_secret):
        raise HTTPException(status_code=401, detail="请先登录")


def _client() -> Iterator[SeedanceClient]:
    client = SeedanceClient(load_settings())
    try:
        yield client
    finally:
        client.close()


def _runpod_client() -> Iterator[RunPodClient]:
    client = RunPodClient(load_runpod_settings())
    try:
        yield client
    finally:
        client.close()


def _provider_client(provider: str) -> Iterator[SeedanceClient | RunPodClient]:
    if provider == "runpod":
        return _runpod_client()
    return _client()


def _runpod_cost_guard_tick(store: TaskStore, *, shutdown_if_idle: bool) -> bool:
    """Poll active private jobs and explicitly close the GPU gate at terminal state."""
    active_before = store.active_runpod()
    with suppress(SeedanceError, ValueError):
        for client in _runpod_client():
            for task in active_before:
                remote_id = task.get("provider_task_id") or task["id"]
                store.update_remote(task["id"], client.get_task(remote_id))
            active_after = bool(store.active_runpod())
            if not active_after and (shutdown_if_idle or bool(active_before)):
                client.set_workers_max(0)
            return active_after
    # On a provider outage, retain the prior active state and never shut down a
    # worker that may still be processing a job.
    return bool(active_before)


def create_app(web_settings: WebSettings | None = None) -> FastAPI:
    settings = web_settings or load_web_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.web_settings = settings
        app.state.store = TaskStore(settings.database_path)
        app.state.video_output_dir = (
            settings.video_output_dir
            or settings.database_path.parent / "generated-videos"
        )
        app.state.video_output_dir.mkdir(parents=True, exist_ok=True)
        guard_task: asyncio.Task[None] | None = None
        if settings.runpod_cost_guard_enabled:
            async def guard_loop() -> None:
                shutdown_if_idle = True
                while True:
                    active = await asyncio.to_thread(
                        _runpod_cost_guard_tick,
                        app.state.store,
                        shutdown_if_idle=shutdown_if_idle,
                    )
                    shutdown_if_idle = active
                    await asyncio.sleep(settings.runpod_cost_guard_poll_seconds)

            guard_task = asyncio.create_task(guard_loop())
        try:
            yield
        finally:
            if guard_task is not None:
                guard_task.cancel()
                with suppress(asyncio.CancelledError):
                    await guard_task

    app = FastAPI(title="AI 视频生成", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.mount(f"{settings.base_path}/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    def html_page(name: str) -> HTMLResponse:
        markup = (WEB_DIR / name).read_text(encoding="utf-8")
        return HTMLResponse(markup.replace("__BASE_PATH__", settings.base_path))

    @app.get(f"{settings.base_path}/healthz")
    async def health():
        return {"status": "ok"}

    @app.get(settings.base_path, response_class=HTMLResponse)
    async def index(request: Request):
        if not _valid_session(request.cookies.get(SESSION_COOKIE), settings.username, settings.session_secret):
            return RedirectResponse(f"{settings.base_path}/login", status_code=303)
        return html_page("index.html")

    @app.get(f"{settings.base_path}/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if _valid_session(request.cookies.get(SESSION_COOKIE), settings.username, settings.session_secret):
            return RedirectResponse(settings.base_path, status_code=303)
        return html_page("login.html")

    @app.post(f"{settings.base_path}/api/login")
    async def login(request: Request):
        data = await request.json()
        if not settings.password or not settings.session_secret:
            raise HTTPException(status_code=503, detail="管理员账号尚未配置")
        username_ok = hmac.compare_digest(str(data.get("username", "")), settings.username)
        password_ok = hmac.compare_digest(str(data.get("password", "")), settings.password)
        if not username_ok or not password_ok:
            raise HTTPException(status_code=401, detail="账号或密码不正确")
        response = JSONResponse({"ok": True, "redirect": settings.base_path})
        response.set_cookie(
            SESSION_COOKIE,
            _sign_session(settings.username, settings.session_secret),
            httponly=True,
            secure=settings.secure_cookie,
            samesite="strict",
            max_age=12 * 60 * 60,
            path=settings.base_path,
            domain=settings.cookie_domain,
        )
        return response

    @app.post(f"{settings.base_path}/api/login-form")
    async def login_form(
        username: str = Form(...),
        password: str = Form(...),
    ):
        configured = bool(settings.password and settings.session_secret)
        username_ok = configured and hmac.compare_digest(username, settings.username)
        password_ok = configured and hmac.compare_digest(password, settings.password)
        if not username_ok or not password_ok:
            return RedirectResponse(
                f"{settings.base_path}/login?error=invalid", status_code=303
            )
        response = RedirectResponse(settings.base_path, status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            _sign_session(settings.username, settings.session_secret),
            httponly=True,
            secure=settings.secure_cookie,
            samesite="strict",
            max_age=12 * 60 * 60,
            path=settings.base_path,
            domain=settings.cookie_domain,
        )
        return response

    @app.post(f"{settings.base_path}/api/logout")
    async def logout(request: Request):
        _require_auth(request)
        response = JSONResponse({"ok": True})
        response.delete_cookie(
            SESSION_COOKIE, path=settings.base_path, domain=settings.cookie_domain
        )
        return response

    @app.post(f"{settings.base_path}/api/internal/video-upload")
    async def upload_generated_video(request: Request, video: UploadFile = File(...)):
        configured_token = settings.video_upload_token
        supplied = request.headers.get("authorization", "")
        expected = f"Bearer {configured_token}"
        if not configured_token:
            raise HTTPException(status_code=503, detail="视频上传通道尚未配置")
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="视频上传凭据无效")
        if video.content_type != "video/mp4":
            raise HTTPException(status_code=415, detail="仅接受 MP4 视频")

        output_dir: Path = request.app.state.video_output_dir
        filename = f"{uuid4()}.mp4"
        destination = output_dir / filename
        partial = output_dir / f".{filename}.part"
        written = 0
        try:
            with partial.open("wb") as target:
                while chunk := await video.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_VIDEO_BYTES:
                        raise HTTPException(status_code=413, detail="生成视频不能超过 250MB")
                    target.write(chunk)
            partial.replace(destination)
        finally:
            if partial.exists():
                partial.unlink()
            await video.close()
        return {"video_url": f"{settings.base_path}/media/{filename}"}

    @app.get(f"{settings.base_path}/media/{{filename}}")
    async def generated_video(request: Request, filename: str):
        _require_auth(request)
        if not filename.endswith(".mp4") or Path(filename).name != filename:
            raise HTTPException(status_code=404, detail="视频不存在")
        path: Path = request.app.state.video_output_dir / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="视频不存在")
        return FileResponse(path, media_type="video/mp4")

    @app.get(f"{settings.base_path}/api/tasks")
    async def list_tasks(request: Request):
        _require_auth(request)
        store: TaskStore = request.app.state.store
        tasks = store.list()
        active = [task for task in tasks if task["status"] not in TERMINAL_STATUSES]
        if active:
            for task in active[:10]:
                try:
                    for client in _provider_client(task.get("provider") or "seedance"):
                        remote_id = task.get("provider_task_id") or task["id"]
                        store.update_remote(task["id"], client.get_task(remote_id))
                except (SeedanceError, ValueError):
                    # A temporary provider outage must not make the whole task list unavailable.
                    continue
            tasks = store.list()
        return {"tasks": tasks}

    @app.post(f"{settings.base_path}/api/tasks", status_code=201)
    async def create_task(
        request: Request,
        prompt: str = Form(...),
        model: str = Form("seedance-2.0"),
        ratio: str = Form("16:9"),
        resolution: str = Form("720p"),
        duration: int = Form(6),
        generate_audio: bool = Form(True),
        reference: UploadFile | None = File(None),
    ):
        _require_auth(request)
        prompt = prompt.strip()
        if not prompt or len(prompt) > 3000:
            raise HTTPException(status_code=422, detail="提示词长度应为 1–3000 个字符")
        if model not in SUPPORTED_MODELS:
            raise HTTPException(status_code=422, detail="生成模型不受支持")
        if ratio not in ALLOWED_RATIOS or resolution not in ALLOWED_RESOLUTIONS or duration not in ALLOWED_DURATIONS:
            raise HTTPException(status_code=422, detail="视频参数不受支持")
        is_self_hosted = model in SELF_HOSTED_MODELS
        store: TaskStore = request.app.state.store
        if is_self_hosted and store.active_runpod():
            raise HTTPException(
                status_code=429,
                detail="自建 GPU 当前已有任务，请等待完成后再提交",
            )
        if is_self_hosted and resolution == "1080p":
            raise HTTPException(status_code=422, detail="自建模型首版仅支持 480p 或 720p")
        if is_self_hosted and reference and reference.filename:
            raise HTTPException(status_code=422, detail="自建模型首版暂不支持参考图")
        image_data_url = None
        if reference and reference.filename:
            if reference.content_type not in ALLOWED_IMAGE_TYPES:
                raise HTTPException(status_code=422, detail="参考图仅支持 JPG、PNG、WebP、GIF 或 HEIC")
            raw = await reference.read(MAX_IMAGE_BYTES + 1)
            if len(raw) > MAX_IMAGE_BYTES:
                raise HTTPException(status_code=413, detail="参考图不能超过 30MB")
            image_data_url = f"data:{reference.content_type};base64,{base64.b64encode(raw).decode()}"
        try:
            provider = "runpod" if is_self_hosted else "seedance"
            for client in _provider_client(provider):
                options = dict(
                    ratio=ratio,
                    resolution=resolution,
                    duration=duration,
                    generate_audio=generate_audio,
                    watermark=True,
                )
                if image_data_url and not is_self_hosted:
                    remote = client.create_reference_video(
                        prompt=prompt, image_url=image_data_url, model=model, **options
                    )
                else:
                    remote = client.create_text_video(prompt=prompt, model=model, **options)
        except SeedanceError as exc:
            return _generation_error(exc)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        provider_task_id = str(remote.get("id") or "")
        if not provider_task_id:
            raise HTTPException(status_code=502, detail="生成服务未返回任务编号")
        task_id = str(uuid4())
        created_at = _now()
        task = {
            "id": task_id,
            "provider": provider,
            "provider_task_id": provider_task_id,
            "prompt": prompt,
            "model": model,
            "ratio": ratio,
            "resolution": resolution,
            "duration": duration,
            "has_reference": int(bool(image_data_url)),
            "status": str(remote.get("status") or "queued"),
            "created_at": created_at,
        }
        store.create(task)
        return {"task": store.get(task_id)}

    return app


app = create_app()

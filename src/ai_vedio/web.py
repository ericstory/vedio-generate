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

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .capabilities import SELF_HOSTED_MODELS, SELF_HOSTED_PROVIDERS, SUPPORTED_MODELS
from .config import (
    PROJECT_ROOT,
    load_runpod_settings,
    load_settings,
    load_wan_pod_settings,
    load_wan_runpod_settings,
)
from .runpod import RunPodClient, RunPodError, RunPodPodClient
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
    wan_v2_enabled: bool = False


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
        wan_v2_enabled=os.getenv("WAN_V2_ENABLED", "0") == "1",
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
                    quality_vote INTEGER,
                    provider_metadata TEXT,
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
            if "quality_vote" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN quality_vote INTEGER")
            if "provider_metadata" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN provider_metadata TEXT")
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
        new_metadata = {
            key: value
            for key, value in content.items()
            if key != "video_url" and value is not None
        }
        if isinstance(error, (dict, list)):
            error = json.dumps(error, ensure_ascii=False)
        with self.connect() as db:
            row = db.execute(
                "SELECT provider_metadata FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            provider_metadata: dict[str, Any] = {}
            if row and row["provider_metadata"]:
                with suppress(ValueError, TypeError):
                    provider_metadata = json.loads(row["provider_metadata"])
            provider_metadata.update(new_metadata)
            db.execute(
                """UPDATE tasks SET status=?, video_url=?, error=?, provider_metadata=?,
                updated_at=? WHERE id=?""",
                (
                    status,
                    content.get("video_url"),
                    str(error) if error else None,
                    json.dumps(provider_metadata, ensure_ascii=False)
                    if provider_metadata
                    else None,
                    _now(),
                    task_id,
                ),
            )

    def update_progress(self, task_id: str, progress: dict[str, Any]) -> bool:
        """Merge a live worker stage into provider_metadata without touching
        status, video_url, or error; terminal tasks ignore stale reports."""
        with self.connect() as db:
            row = db.execute(
                "SELECT status, provider_metadata FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not row or row["status"] in TERMINAL_STATUSES:
                return False
            provider_metadata: dict[str, Any] = {}
            if row["provider_metadata"]:
                with suppress(ValueError, TypeError):
                    provider_metadata = json.loads(row["provider_metadata"])
            provider_metadata["progress"] = progress
            db.execute(
                "UPDATE tasks SET provider_metadata=?, updated_at=? WHERE id=?",
                (json.dumps(provider_metadata, ensure_ascii=False), _now(), task_id),
            )
            return True

    def vote(self, task_id: str, vote: int) -> dict[str, Any] | None:
        if vote not in {-1, 1}:
            raise ValueError("quality vote must be -1 or 1")
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE tasks SET quality_vote=?, updated_at=? WHERE id=? AND status='succeeded'",
                (vote, _now(), task_id),
            )
        return self.get(task_id) if cursor.rowcount else None

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

    def active_runpod(self, provider: str | None = None) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
        provider_clause = "provider LIKE 'runpod%'"
        values: tuple[Any, ...] = tuple(sorted(TERMINAL_STATUSES))
        if provider:
            provider_clause = "provider=?"
            values = (provider, *values)
        with self.connect() as db:
            rows = db.execute(
                f"""SELECT * FROM tasks
                WHERE {provider_clause} AND status NOT IN ({placeholders})
                ORDER BY created_at ASC""",
                values,
            ).fetchall()
        return [dict(row) for row in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    task["has_reference"] = bool(task.get("has_reference"))
    if task.get("provider_metadata"):
        with suppress(ValueError, TypeError):
            task["provider_metadata"] = json.loads(task["provider_metadata"])
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


def _wan_runpod_client() -> Iterator[RunPodClient]:
    client = RunPodClient(load_wan_runpod_settings())
    try:
        yield client
    finally:
        client.close()


def _wan_pod_client() -> Iterator[RunPodPodClient]:
    client = RunPodPodClient(load_wan_pod_settings())
    try:
        yield client
    finally:
        client.close()


def _provider_client(provider: str) -> Iterator[SeedanceClient | RunPodClient | RunPodPodClient]:
    if provider == "runpod":
        return _runpod_client()
    if provider == "runpod_wan":
        return _wan_runpod_client()
    if provider == "runpod_wan_pod":
        return _wan_pod_client()
    return _client()


def _runpod_provider_cost_guard_tick(
    store: TaskStore,
    *,
    provider: str,
    client_factory: Any,
    shutdown_if_idle: bool,
) -> bool:
    """Poll active private jobs and explicitly close the GPU gate at terminal state."""
    active_before = store.active_runpod(provider)
    with suppress(SeedanceError, ValueError):
        for client in client_factory():
            for task in active_before:
                remote_id = task.get("provider_task_id") or task["id"]
                try:
                    remote = client.get_task(remote_id)
                except RunPodError as exc:
                    if exc.status_code == 404:
                        # Jobs created against a retired endpoint cannot be queried
                        # through the current endpoint and must not block new work.
                        store.update_remote(
                            task["id"],
                            {
                                "status": "expired",
                                "content": {},
                                "error": "历史 RunPod Endpoint 任务已不可用",
                            },
                        )
                    continue
                store.update_remote(task["id"], remote)
            active_after = bool(store.active_runpod(provider))
            if not active_after and (shutdown_if_idle or bool(active_before)):
                client.set_workers_max(0)
            return active_after
    # On a provider outage, retain the prior active state and never shut down a
    # worker that may still be processing a job.
    return bool(active_before)


def _runpod_cost_guard_tick(store: TaskStore, *, shutdown_if_idle: bool) -> bool:
    ltx_active = _runpod_provider_cost_guard_tick(
        store,
        provider="runpod",
        client_factory=_runpod_client,
        shutdown_if_idle=shutdown_if_idle,
    )
    wan_active = False
    if os.getenv("RUNPOD_WAN_ENDPOINT_ID", "").strip():
        wan_active = _runpod_provider_cost_guard_tick(
            store,
            provider="runpod_wan",
            client_factory=_wan_runpod_client,
            shutdown_if_idle=shutdown_if_idle,
        )
    wan_pod_active = False
    if os.getenv("RUNPOD_WAN_POD_TEMPLATE_ID", "").strip():
        active_tasks = store.active_runpod("runpod_wan_pod")
        with suppress(SeedanceError, ValueError):
            for client in _wan_pod_client():
                for task in active_tasks:
                    pod_id = task.get("provider_task_id") or task["id"]
                    created_at = datetime.fromisoformat(task["created_at"])
                    age = (datetime.now(timezone.utc) - created_at).total_seconds()
                    if age > client.settings.maximum_runtime_seconds:
                        client.delete_pod(pod_id)
                        store.update_remote(
                            task["id"],
                            {
                                "status": "failed",
                                "content": {},
                                "error": "Wan GPU Pod exceeded the 30 minute cost limit",
                            },
                        )
                        continue
                    try:
                        client.get_task(pod_id)
                    except RunPodError as exc:
                        if exc.status_code == 404:
                            store.update_remote(
                                task["id"],
                                {
                                    "status": "expired",
                                    "content": {},
                                    "error": "Wan GPU Pod disappeared before returning a result",
                                },
                            )
                wan_pod_active = bool(store.active_runpod("runpod_wan_pod"))
    return ltx_active or wan_active or wan_pod_active


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
        markup = markup.replace("__BASE_PATH__", settings.base_path)
        markup = markup.replace(
            "__WAN_V2_OPTION_STATE__", "" if settings.wan_v2_enabled else "disabled"
        )
        return HTMLResponse(markup)

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

    def delete_wan_pod(pod_id: str) -> None:
        with suppress(RunPodError, ValueError):
            for client in _wan_pod_client():
                client.delete_pod(pod_id)

    @app.post(f"{settings.base_path}/api/internal/pod-result/{{task_id}}")
    async def receive_pod_result(
        request: Request,
        task_id: str,
        background_tasks: BackgroundTasks,
    ):
        configured_token = settings.video_upload_token
        supplied = request.headers.get("authorization", "")
        if not configured_token:
            raise HTTPException(status_code=503, detail="Pod 回调通道尚未配置")
        if not hmac.compare_digest(supplied, f"Bearer {configured_token}"):
            raise HTTPException(status_code=401, detail="Pod 回调凭据无效")
        store: TaskStore = request.app.state.store
        task = store.get(task_id)
        if not task or task.get("provider") != "runpod_wan_pod":
            raise HTTPException(status_code=404, detail="Pod 任务不存在")
        body = await request.json()
        status = str(body.get("status") or "")
        if status not in {"succeeded", "failed"}:
            raise HTTPException(status_code=422, detail="Pod 回调状态无效")
        store.update_remote(
            task_id,
            {
                "status": status,
                "content": body.get("content") if isinstance(body.get("content"), dict) else {},
                "error": body.get("error"),
            },
        )
        pod_id = str(task.get("provider_task_id") or "")
        if pod_id:
            background_tasks.add_task(delete_wan_pod, pod_id)
        return {"ok": True}

    @app.post(f"{settings.base_path}/api/internal/pod-progress/{{task_id}}")
    async def receive_pod_progress(request: Request, task_id: str):
        configured_token = settings.video_upload_token
        supplied = request.headers.get("authorization", "")
        if not configured_token:
            raise HTTPException(status_code=503, detail="Pod 回调通道尚未配置")
        if not hmac.compare_digest(supplied, f"Bearer {configured_token}"):
            raise HTTPException(status_code=401, detail="Pod 回调凭据无效")
        store: TaskStore = request.app.state.store
        task = store.get(task_id)
        if not task or task.get("provider") != "runpod_wan_pod":
            raise HTTPException(status_code=404, detail="Pod 任务不存在")
        body = await request.json()
        stage = str(body.get("stage") or "").strip()
        if not stage or len(stage) > 64:
            raise HTTPException(status_code=422, detail="进度阶段无效")
        progress: dict[str, Any] = {"stage": stage, "at": _now()}
        seconds = body.get("seconds")
        if isinstance(seconds, (int, float)):
            progress["seconds"] = round(float(seconds), 3)
        applied = store.update_progress(task_id, progress)
        return {"ok": True, "applied": applied}

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

    @app.post(f"{settings.base_path}/api/tasks/{{task_id}}/vote")
    async def vote_task(request: Request, task_id: str):
        _require_auth(request)
        data = await request.json()
        vote = {"up": 1, "down": -1}.get(str(data.get("vote") or ""))
        if vote is None:
            raise HTTPException(status_code=422, detail="评分只支持 up 或 down")
        store: TaskStore = request.app.state.store
        task = store.vote(task_id, vote)
        if task is None:
            raise HTTPException(status_code=404, detail="仅可评价已完成的视频")
        return {"task": task}

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
        provider = SELF_HOSTED_PROVIDERS.get(model, "seedance")
        if is_self_hosted and store.active_runpod(provider):
            raise HTTPException(
                status_code=429,
                detail="自建 GPU 当前已有任务，请等待完成后再提交",
            )
        if is_self_hosted and resolution == "1080p":
            raise HTTPException(status_code=422, detail="自建模型首版仅支持 480p 或 720p")
        if model == "pinkcherry-ltx-2.3-v1.8" and reference and reference.filename:
            raise HTTPException(status_code=422, detail="自建模型首版暂不支持参考图")
        if model == "wan-2.2-a14b-adult-v2":
            if not settings.wan_v2_enabled:
                raise HTTPException(status_code=503, detail="Wan V2 尚未启用")
            if reference and reference.filename:
                raise HTTPException(status_code=422, detail="Wan V2 文生视频首版暂不接收参考图")
        image_data_url = None
        if reference and reference.filename:
            if reference.content_type not in ALLOWED_IMAGE_TYPES:
                raise HTTPException(status_code=422, detail="参考图仅支持 JPG、PNG、WebP、GIF 或 HEIC")
            raw = await reference.read(MAX_IMAGE_BYTES + 1)
            if len(raw) > MAX_IMAGE_BYTES:
                raise HTTPException(status_code=413, detail="参考图不能超过 30MB")
            image_data_url = f"data:{reference.content_type};base64,{base64.b64encode(raw).decode()}"
        task_id = str(uuid4())
        try:
            for client in _provider_client(provider):
                options = dict(
                    ratio=ratio,
                    resolution=resolution,
                    duration=duration,
                    generate_audio=generate_audio,
                    watermark=True,
                )
                if provider == "runpod_wan_pod":
                    options["task_id"] = task_id
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
        try:
            store.create(task)
            store.update_remote(task_id, remote)
        except Exception:
            if provider == "runpod_wan_pod":
                delete_wan_pod(provider_task_id)
            raise
        return {"task": store.get(task_id)}

    return app


app = create_app()

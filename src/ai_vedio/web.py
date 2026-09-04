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
import re
import secrets
import sqlite3
import time
from typing import Any, Iterator
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .capabilities import (
    LTX_MODEL,
    LTX_POD_PROVIDER,
    POD_PROVIDERS,
    SELF_HOSTED_MODELS,
    SELF_HOSTED_PROVIDERS,
    SUPPORTED_MODELS,
)
from .config import (
    PROJECT_ROOT,
    RunPodPodSettings,
    load_runpod_settings,
    load_settings,
    load_h3_pod_settings,
    load_ltx_pod_settings,
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
ALLOWED_RESOLUTIONS = {"480p", "720p", "768p", "1080p"}
# MiniMax H3 resolves its canvas from a short edge, and 768 is the only value
# MiniMax and SGLang publish recipes and reference outputs for.
H3_MODEL = "minimax-h3-pinkcherry"
ALLOWED_DURATIONS = set(range(4, 16))
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "expired"}
# A Pod lane runs one GPU Pod at a time. While that Pod is busy or warm, further
# submissions queue behind it up to this depth: enough to line up a few prompts
# for a warm worker, small enough that a forgotten queue cannot keep a Pod
# billing for hours.
MAX_UNFINISHED_POD_TASKS = 3
# Production Pods are named "<lane prefix>-<first 12 hex chars of the task id>".
# Diagnostic Pods from scripts/runpod are "<prefix>-<tag>-<n>" and never match,
# so the orphan sweep cannot touch them.
_PRODUCTION_POD_NAME = re.compile(r"^(?P<prefix>.+)-[0-9a-f]{12}$")
_ORPHAN_SWEEP_INTERVAL_SECONDS = 60.0
_last_orphan_sweep: dict[str, float] = {}


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
    h3_enabled: bool = False
    # Route new LTX tasks to the on-demand Pod lane instead of the serverless
    # endpoint. Off keeps the legacy serverless path for a clean rollback.
    ltx_pod_enabled: bool = False


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
        h3_enabled=os.getenv("H3_ENABLED", "0") == "1",
        ltx_pod_enabled=os.getenv("LTX_POD_ENABLED", "0") == "1",
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
            if "generate_audio" not in columns:
                db.execute(
                    "ALTER TABLE tasks ADD COLUMN generate_audio INTEGER NOT NULL DEFAULT 1"
                )
            # Legacy Seedance rows used the task id as the provider id. Pod-lane
            # tasks legitimately carry no provider id while they wait for a GPU,
            # and backfilling those would turn "still queued" into "lost Pod".
            pod_placeholders = ",".join("?" for _ in POD_PROVIDERS)
            db.execute(
                "UPDATE tasks SET provider_task_id=id "
                "WHERE (provider_task_id IS NULL OR provider_task_id='') "
                f"AND provider NOT IN ({pod_placeholders})",
                tuple(sorted(POD_PROVIDERS)),
            )
            # One row per GPU Pod the control plane has created. A lane owns at
            # most one live (busy or idle) Pod; a warm idle Pod is reused by
            # the next queued task instead of paying another cold start.
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS pods (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    state TEXT NOT NULL,
                    current_task_id TEXT,
                    created_at TEXT NOT NULL,
                    idle_since TEXT,
                    jobs_completed INTEGER NOT NULL DEFAULT 0,
                    metadata TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def create(self, task: dict[str, Any]) -> None:
        task = {
            "provider": "seedance",
            "provider_task_id": task["id"],
            "generate_audio": 1,
            **task,
        }
        with self.connect() as db:
            db.execute(
                """INSERT INTO tasks
                (id, provider, provider_task_id, prompt, model, ratio, resolution, duration, has_reference,
                 generate_audio, status, video_url, error, created_at, updated_at)
                VALUES (:id, :provider, :provider_task_id, :prompt, :model, :ratio, :resolution, :duration,
                        :has_reference, :generate_audio, :status, NULL, NULL, :created_at, :created_at)""",
                task,
            )

    def attach_provider_task(self, task_id: str, provider_task_id: str) -> None:
        """Record the Pod a queued task finally got."""
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET provider_task_id=?, updated_at=? WHERE id=?",
                (provider_task_id, _now(), task_id),
            )

    def pending_pod_tasks(self, provider: str) -> list[dict[str, Any]]:
        """Queued Pod-lane tasks that have not been given a Pod yet."""
        with self.connect() as db:
            rows = db.execute(
                """SELECT * FROM tasks
                WHERE provider=? AND status='queued'
                  AND (provider_task_id IS NULL OR provider_task_id='')
                ORDER BY created_at ASC""",
                (provider,),
            ).fetchall()
        return [dict(row) for row in rows]

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

    # -- Pods: one live GPU Pod per lane, reused across jobs while warm --------

    def register_pod(
        self,
        provider: str,
        pod_id: str,
        *,
        task_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a freshly created Pod, busy with the task it was created for."""
        now = _now()
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO pods
                (id, provider, state, current_task_id, created_at, idle_since,
                 jobs_completed, metadata, updated_at)
                VALUES (?, ?, 'busy', ?, ?, NULL, 0, ?, ?)""",
                (
                    pod_id,
                    provider,
                    task_id,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                ),
            )

    def live_pod(self, provider: str) -> dict[str, Any] | None:
        """The lane's busy or idle Pod, if it has one."""
        with self.connect() as db:
            row = db.execute(
                """SELECT * FROM pods WHERE provider=? AND state IN ('busy', 'idle')
                ORDER BY created_at ASC LIMIT 1""",
                (provider,),
            ).fetchone()
        return _pod_row(row)

    def get_pod(self, pod_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM pods WHERE id=?", (pod_id,)).fetchone()
        return _pod_row(row)

    def known_pod_ids(self) -> set[str]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id FROM pods WHERE state IN ('busy', 'idle')"
            ).fetchall()
        return {str(row["id"]) for row in rows}

    def release_pod(self, pod_id: str, *, task_id: str) -> bool:
        """Park a busy Pod idle once the task it was running has finished."""
        now = _now()
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE pods SET state='idle', current_task_id=NULL, idle_since=?,
                jobs_completed=jobs_completed+1, updated_at=?
                WHERE id=? AND state='busy' AND current_task_id=?""",
                (now, now, pod_id, task_id),
            )
        return bool(cursor.rowcount)

    def retire_pod(self, pod_id: str, *, from_states: tuple[str, ...] = ("busy", "idle")) -> bool:
        """Mark a Pod deleted; True only when this call made the transition.

        Dispatch and deletion both go through this row, so whichever commits
        first wins: an idle-timeout delete asks for ``from_states=("idle",)``
        and finds nothing to change if a worker just claimed the Pod.
        """
        placeholders = ",".join("?" for _ in from_states)
        with self.connect() as db:
            cursor = db.execute(
                f"""UPDATE pods SET state='deleted', current_task_id=NULL, updated_at=?
                WHERE id=? AND state IN ({placeholders})""",
                (_now(), pod_id, *from_states),
            )
        return bool(cursor.rowcount)

    def claim_next_task(self, pod_id: str) -> dict[str, Any] | None:
        """Hand the lane's oldest queued task to this idle Pod, atomically."""
        with self.connect() as db:
            # A write lock up front makes the read-then-update one unit against
            # the guard thread, which may be retiring the same Pod right now.
            db.execute("BEGIN IMMEDIATE")
            pod = db.execute(
                "SELECT * FROM pods WHERE id=? AND state='idle'", (pod_id,)
            ).fetchone()
            if not pod:
                return None
            task = db.execute(
                """SELECT * FROM tasks
                WHERE provider=? AND status='queued'
                  AND (provider_task_id IS NULL OR provider_task_id='')
                ORDER BY created_at ASC LIMIT 1""",
                (pod["provider"],),
            ).fetchone()
            if not task:
                return None
            now = _now()
            db.execute(
                """UPDATE pods SET state='busy', current_task_id=?, idle_since=NULL,
                updated_at=? WHERE id=?""",
                (task["id"], now, pod_id),
            )
            db.execute(
                "UPDATE tasks SET provider_task_id=?, updated_at=? WHERE id=?",
                (pod_id, now, task["id"]),
            )
        return dict(task)


def _pod_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    pod = dict(row)
    metadata: dict[str, Any] = {}
    if pod.get("metadata"):
        with suppress(ValueError, TypeError):
            decoded = json.loads(pod["metadata"])
            if isinstance(decoded, dict):
                metadata = decoded
    pod["metadata"] = metadata
    return pod


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    task["has_reference"] = bool(task.get("has_reference"))
    task["generate_audio"] = bool(task.get("generate_audio", 1))
    if task.get("provider_metadata"):
        with suppress(ValueError, TypeError):
            task["provider_metadata"] = json.loads(task["provider_metadata"])
    return task


CAPACITY_PHRASES = (
    "no instances currently available",
    "no longer any instances available",
)
# RunPod answers a capacity miss with HTTP 400, so the phrase is the signal;
# these codes are the API itself having a bad moment and are just as free.
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_capacity_error(exc: SeedanceError) -> bool:
    message = exc.upstream_message.lower()
    return any(phrase in message for phrase in CAPACITY_PHRASES)


def _is_transient_provider_error(exc: SeedanceError) -> bool:
    return exc.status_code is None or exc.status_code in _TRANSIENT_STATUS_CODES


def _generation_error_text(exc: SeedanceError) -> str:
    """One line for the task record when the provider rejects a queued task."""
    _, detail, _ = _classify_generation_error(exc)
    upstream = exc.upstream_message.strip()
    return f"{detail}：{upstream[:300]}" if upstream else detail


def _generation_error(exc: SeedanceError) -> JSONResponse:
    status_code, detail, guidance = _classify_generation_error(exc)
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": detail,
            "code": exc.code or "GENERATION_REJECTED",
            "upstream_message": exc.upstream_message[:500],
            "guidance": guidance,
        },
    )


def _classify_generation_error(exc: SeedanceError) -> tuple[int, str, list[str]]:
    upstream = exc.upstream_message
    searchable = f"{exc.code or ''} {upstream}".lower()
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
    elif any(term in searchable for term in CAPACITY_PHRASES):
        # A capacity miss is not a content decision, and saying so sends people
        # off editing a prompt that was never the problem. No Pod is created and
        # nothing is billed when this happens.
        detail = "云 GPU 当前无可用机器"
        guidance = [
            "这与提示词无关：所选 GPU 型号在配置的数据中心暂时没有空闲机器",
            "等待 1–2 分钟后重试，容量通常很快回来",
            "本次未创建 Pod，不产生任何费用",
        ]
        status_code = 503
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
    return status_code, detail, guidance


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


def _h3_pod_client() -> Iterator[RunPodPodClient]:
    client = RunPodPodClient(load_h3_pod_settings())
    try:
        yield client
    finally:
        client.close()


def _ltx_pod_client() -> Iterator[RunPodPodClient]:
    client = RunPodPodClient(load_ltx_pod_settings())
    try:
        yield client
    finally:
        client.close()


# Wrapped rather than bound directly so the lookup resolves the module
# attribute at call time, which keeps the factories patchable in tests.
POD_CLIENT_FACTORIES = {
    "runpod_wan_pod": lambda: _wan_pod_client(),
    "runpod_h3_pod": lambda: _h3_pod_client(),
    LTX_POD_PROVIDER: lambda: _ltx_pod_client(),
}
POD_TEMPLATE_ENV_VARS = {
    "runpod_wan_pod": "RUNPOD_WAN_POD_TEMPLATE_ID",
    "runpod_h3_pod": "RUNPOD_H3_POD_TEMPLATE_ID",
    LTX_POD_PROVIDER: "RUNPOD_LTX_POD_TEMPLATE_ID",
}
POD_TIMEOUT_LABELS = {
    "runpod_wan_pod": "Wan",
    "runpod_h3_pod": "MiniMax H3",
    LTX_POD_PROVIDER: "LTX",
}


def _provider_for(model: str, settings: WebSettings) -> str:
    """Which backend a new task for this model goes to.

    LTX is the one model with two backends: the legacy serverless endpoint and
    the on-demand Pod lane. The flag decides for new tasks only; rows already
    stored keep the provider they were created with.
    """
    if model == LTX_MODEL and settings.ltx_pod_enabled:
        return LTX_POD_PROVIDER
    return SELF_HOSTED_PROVIDERS.get(model, "seedance")


def _provider_client(provider: str) -> Iterator[SeedanceClient | RunPodClient | RunPodPodClient]:
    if provider == "runpod":
        return _runpod_client()
    if provider == "runpod_wan":
        return _wan_runpod_client()
    pod_factory = POD_CLIENT_FACTORIES.get(provider)
    if pod_factory is not None:
        return pod_factory()
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


def _task_metadata(task: dict[str, Any]) -> dict[str, Any]:
    raw = task.get("provider_metadata")
    if isinstance(raw, dict):
        return raw
    if raw:
        with suppress(ValueError, TypeError):
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                return decoded
    return {}


def _progress_of(task: dict[str, Any]) -> dict[str, Any]:
    progress = _task_metadata(task).get("progress")
    return progress if isinstance(progress, dict) else {}


def _lane_settings(provider: str) -> RunPodPodSettings | None:
    """The Pod lane's settings, or None when the lane is not configured."""
    factory = POD_CLIENT_FACTORIES.get(provider)
    if factory is None:
        return None
    with suppress(SeedanceError, ValueError):
        for client in factory():
            settings = getattr(client, "settings", None)
            return settings if isinstance(settings, RunPodPodSettings) else None
    return None


def _acquire_pending_pods(
    store: TaskStore, *, provider: str, client: RunPodPodClient, label: str
) -> None:
    """Give queued Pod-lane tasks a GPU, one lane sweep per task per pass.

    Submitting from the browser used to race RunPod capacity inside the HTTP
    request: three sweeps, then a 503 the user could only answer by clicking
    again. A capacity miss creates no Pod and bills nothing, so the guard loop
    can keep asking for as long as the task is willing to wait, while the
    request itself returns the moment the task is queued.

    A lane runs one Pod at a time. While it has one -- busy, or warm and
    waiting -- nothing is asked of RunPod: the worker on that Pod pulls the
    next task itself the moment its current one is delivered.
    """
    now = datetime.now(timezone.utc)
    pending = store.pending_pod_tasks(provider)
    if not pending:
        return
    live = store.live_pod(provider)
    if live is None:
        # Tasks that got a Pod before the pods table existed still hold it.
        live_task_pods = [t for t in store.active_runpod(provider) if t.get("provider_task_id")]
        live = {"id": live_task_pods[0]["provider_task_id"]} if live_task_pods else None
    if live is not None:
        for position, task in enumerate(pending, start=1):
            progress = _progress_of(task)
            # The capacity deadline only runs while RunPod is actually being
            # asked; keep moving its start while the queue waits its turn.
            store.update_progress(
                task["id"],
                {
                    "stage": "awaiting_worker",
                    "position": position,
                    "pod": live["id"],
                    "attempts": int(progress.get("attempts") or 0),
                    "acquire_started_at": _now(),
                    "at": _now(),
                },
            )
        return
    for task in pending:
        task_id = task["id"]
        progress = _progress_of(task)
        attempts = int(progress.get("attempts") or 0)
        if attempts and progress.get("at"):
            with suppress(ValueError, TypeError):
                since_last = (now - datetime.fromisoformat(str(progress["at"]))).total_seconds()
                if since_last < client.settings.acquire_retry_seconds:
                    continue
        acquire_started_at = str(progress.get("acquire_started_at") or task["created_at"])
        waited = (now - datetime.fromisoformat(acquire_started_at)).total_seconds()
        if waited > client.settings.acquire_timeout_seconds:
            minutes = max(1, round(client.settings.acquire_timeout_seconds / 60))
            store.update_remote(
                task_id,
                {
                    "status": "failed",
                    "content": {},
                    "error": (
                        f"{label} 云 GPU 在 {minutes} 分钟内没有空闲机器（申请 {attempts} 次）；"
                        "本次未创建 Pod，不产生费用，请稍后重新提交"
                    ),
                },
            )
            continue
        attempts += 1
        try:
            remote = client.create_text_video(
                prompt=task["prompt"],
                model=task["model"],
                ratio=task["ratio"],
                resolution=task["resolution"],
                duration=int(task["duration"]),
                generate_audio=bool(task.get("generate_audio", 1)),
                watermark=True,
                task_id=task_id,
                capacity_retry_sweeps=1,
            )
        except SeedanceError as exc:
            if _is_capacity_error(exc) or _is_transient_provider_error(exc):
                waiting: dict[str, Any] = {
                    "stage": "awaiting_gpu",
                    "attempts": attempts,
                    "at": _now(),
                    "reason": "capacity" if _is_capacity_error(exc) else "provider",
                }
                if progress.get("acquire_started_at"):
                    waiting["acquire_started_at"] = progress["acquire_started_at"]
                store.update_remote(
                    task_id,
                    {"status": "queued", "content": {"progress": waiting}, "error": None},
                )
                continue
            store.update_remote(
                task_id,
                {"status": "failed", "content": {}, "error": _generation_error_text(exc)},
            )
            continue
        except ValueError as exc:
            store.update_remote(task_id, {"status": "failed", "content": {}, "error": str(exc)})
            continue
        pod_id = str(remote.get("id") or "")
        if not pod_id:
            store.update_remote(
                task_id,
                {"status": "failed", "content": {}, "error": "RunPod 创建 Pod 后未返回编号"},
            )
            continue
        content = dict(remote.get("content") or {})
        total_wait = (now - datetime.fromisoformat(task["created_at"])).total_seconds()
        content.update(
            {
                # The 30 minute runtime cap counts from here, not from the click.
                "pod_created_at": _now(),
                "job_started_at": _now(),
                "gpu_wait_seconds": round(total_wait, 1),
                "gpu_acquire_attempts": attempts,
                "progress": {"stage": "pod_created", "at": _now()},
            }
        )
        store.attach_provider_task(task_id, pod_id)
        store.register_pod(
            provider,
            pod_id,
            task_id=task_id,
            metadata={
                key: content[key]
                for key in ("gpu_name", "pod_price_per_hour", "pod_data_center_id")
                if content.get(key) is not None
            },
        )
        store.update_remote(
            task_id,
            {"status": str(remote.get("status") or "queued"), "content": content, "error": None},
        )
        # One Pod per lane: the rest of the queue waits for this worker.
        break


def _retire_and_delete(
    store: TaskStore,
    client: RunPodPodClient,
    pod_id: str,
    *,
    from_states: tuple[str, ...] = ("busy", "idle"),
    reason: str = "",
) -> bool:
    """Retire the Pod record, then delete the billed Pod. False if a claim won.

    A Pod the table never saw (created before the table existed, or by an
    older control plane) is deleted unconditionally: untracked is the one
    state that must never survive.
    """
    if store.get_pod(pod_id) is not None and not store.retire_pod(pod_id, from_states=from_states):
        return False
    print(json.dumps({"event": "pod_deleted", "pod": pod_id, "reason": reason}), flush=True)
    client.delete_pod(pod_id)
    return True


def _tend_pod_lane(
    store: TaskStore, *, provider: str, client: RunPodPodClient, label: str
) -> None:
    """Runtime cap per job, existence checks, and the idle window of a warm Pod."""
    settings = client.settings
    now = datetime.now(timezone.utc)
    for task in store.active_runpod(provider):
        pod_id = str(task.get("provider_task_id") or "")
        if not pod_id:
            # Still waiting for capacity: nothing is billed yet and the
            # acquisition pass owns that deadline.
            continue
        metadata = _task_metadata(task)
        # On a reused Pod the job started when it was handed over, not when the
        # Pod was created; a fresh Pod's first job starts with the Pod.
        started_at = str(
            metadata.get("job_started_at") or metadata.get("pod_created_at") or task["created_at"]
        )
        age = (now - datetime.fromisoformat(started_at)).total_seconds()
        if age > settings.maximum_runtime_seconds:
            _retire_and_delete(store, client, pod_id, reason="runtime_cap")
            store.update_remote(
                task["id"],
                {
                    "status": "failed",
                    "content": {},
                    "error": f"{label} GPU Pod exceeded the 30 minute cost limit",
                },
            )
            continue
        try:
            client.get_task(pod_id)
        except RunPodError as exc:
            if exc.status_code == 404:
                store.retire_pod(pod_id)
                store.update_remote(
                    task["id"],
                    {
                        "status": "expired",
                        "content": {},
                        "error": f"{label} GPU Pod disappeared before returning a result",
                    },
                )
    pod = store.live_pod(provider)
    if pod is None:
        return
    if pod["state"] == "busy":
        current = store.get(str(pod.get("current_task_id") or "")) if pod.get("current_task_id") else None
        if current is None or current["status"] in TERMINAL_STATUSES:
            # The terminal callback parks or deletes the Pod itself; this only
            # catches a crash between its two writes, and errs on the cheap side.
            _retire_and_delete(store, client, pod["id"], reason="stale_busy")
        return
    lived = (now - datetime.fromisoformat(pod["created_at"])).total_seconds()
    idle_since = datetime.fromisoformat(pod["idle_since"]) if pod.get("idle_since") else now
    idle_for = (now - idle_since).total_seconds()
    if settings.keep_warm_idle_seconds <= 0 or idle_for > settings.keep_warm_idle_seconds:
        _retire_and_delete(store, client, pod["id"], from_states=("idle",), reason="idle_timeout")
        return
    if lived > settings.max_pod_lifetime_seconds:
        _retire_and_delete(store, client, pod["id"], from_states=("idle",), reason="max_lifetime")
        return
    try:
        client.get_task(pod["id"])
    except RunPodError as exc:
        if exc.status_code == 404:
            store.retire_pod(pod["id"], from_states=("idle",))


def _sweep_orphan_pods(store: TaskStore, *, provider: str, client: RunPodPodClient) -> None:
    """Delete production-named Pods of this lane that nothing is tracking.

    A warm Pod the control plane forgot -- a lost database, a crash between
    creating and recording it -- would otherwise bill until someone noticed.
    """
    list_pods = getattr(client, "list_pods", None)
    if not callable(list_pods):
        return
    now = time.monotonic()
    if now - _last_orphan_sweep.get(provider, 0.0) < _ORPHAN_SWEEP_INTERVAL_SECONDS:
        return
    _last_orphan_sweep[provider] = now
    known = store.known_pod_ids()
    for pod in list_pods():
        if pod["id"] in known:
            continue
        match = _PRODUCTION_POD_NAME.match(pod["name"])
        if not match or match.group("prefix") != client.settings.name_prefix:
            continue
        print(
            json.dumps({"event": "orphan_pod_deleted", "pod": pod["id"], "name": pod["name"]}),
            flush=True,
        )
        client.delete_pod(pod["id"])


def _runpod_cost_guard_tick(store: TaskStore, *, shutdown_if_idle: bool) -> bool:
    ltx_active = False
    # Once LTX has moved to its Pod lane the serverless endpoint is deleted and
    # this variable with it; polling a missing endpoint every tick is noise.
    # Tasks still stored against the endpoint keep being polled regardless, so
    # an in-flight serverless job is not orphaned by flipping the lane.
    if os.getenv("RUNPOD_ENDPOINT_ID", "").strip() or store.active_runpod("runpod"):
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
    pod_active = False
    for provider, client_factory in POD_CLIENT_FACTORIES.items():
        if not os.getenv(POD_TEMPLATE_ENV_VARS[provider], "").strip():
            continue
        label = POD_TIMEOUT_LABELS[provider]
        with suppress(SeedanceError, ValueError):
            for client in client_factory():
                _acquire_pending_pods(store, provider=provider, client=client, label=label)
                _tend_pod_lane(store, provider=provider, client=client, label=label)
                pod_active = (
                    pod_active
                    or bool(store.active_runpod(provider))
                    or store.live_pod(provider) is not None
                )
                _sweep_orphan_pods(store, provider=provider, client=client)
    return ltx_active or wan_active or pod_active


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
        markup = markup.replace(
            "__H3_OPTION_STATE__", "" if settings.h3_enabled else "disabled"
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

    def delete_pod(provider: str, pod_id: str) -> None:
        client_factory = POD_CLIENT_FACTORIES.get(provider)
        if client_factory is None:
            return
        with suppress(RunPodError, ValueError):
            for client in client_factory():
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
        if not task or task.get("provider") not in POD_PROVIDERS:
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
            # A worker that will pull its next job keeps the Pod warm after a
            # success; a one-shot worker, or a failure (a bad host is the usual
            # cause), still ends with the Pod deleted the moment this commits.
            worker = body.get("worker") if isinstance(body.get("worker"), dict) else {}
            lane = _lane_settings(str(task["provider"]))
            kept_warm = (
                status == "succeeded"
                and bool(worker.get("pulls_jobs"))
                and lane is not None
                and lane.keep_warm_idle_seconds > 0
                and store.release_pod(pod_id, task_id=task_id)
            )
            if not kept_warm:
                store.retire_pod(pod_id)
                background_tasks.add_task(delete_pod, str(task["provider"]), pod_id)
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
        if not task or task.get("provider") not in POD_PROVIDERS:
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

    @app.post(f"{settings.base_path}/api/internal/pod-jobs/{{pod_id}}/next")
    async def next_pod_job(request: Request, pod_id: str):
        """A warm worker asks for its next job; 204 means keep waiting."""
        configured_token = settings.video_upload_token
        supplied = request.headers.get("authorization", "")
        if not configured_token:
            raise HTTPException(status_code=503, detail="Pod 回调通道尚未配置")
        if not hmac.compare_digest(supplied, f"Bearer {configured_token}"):
            raise HTTPException(status_code=401, detail="Pod 回调凭据无效")
        store: TaskStore = request.app.state.store
        pod = store.get_pod(pod_id)
        if not pod or pod["state"] == "deleted":
            # Retired: the worker stops asking and waits to be deleted.
            raise HTTPException(status_code=404, detail="Pod 未登记或已回收")
        provider = str(pod["provider"])
        lane = _lane_settings(provider)
        if lane is None or lane.keep_warm_idle_seconds <= 0:
            return Response(status_code=204)
        lived = (
            datetime.now(timezone.utc) - datetime.fromisoformat(pod["created_at"])
        ).total_seconds()
        if lived > lane.max_pod_lifetime_seconds:
            # Draining: no new work; the guard deletes it once idle.
            return Response(status_code=204)
        task = await asyncio.to_thread(store.claim_next_task, pod_id)
        if task is None:
            return Response(status_code=204)
        task_id = str(task["id"])
        job: dict[str, Any] | None = None
        result_url = progress_url = ""
        for client in POD_CLIENT_FACTORIES[provider]():
            job = client.job_input(
                prompt=task["prompt"],
                ratio=task["ratio"],
                resolution=task["resolution"],
                duration=int(task["duration"]),
                generate_audio=bool(task.get("generate_audio", 1)),
            )
            result_url, progress_url = client.callback_urls(task_id)
        if job is None:
            raise HTTPException(status_code=503, detail="Pod 链路客户端不可用")
        now = _now()
        waited = (
            datetime.now(timezone.utc) - datetime.fromisoformat(task["created_at"])
        ).total_seconds()
        store.update_remote(
            task_id,
            {
                "status": "queued",
                "content": {
                    **pod["metadata"],
                    "pod_created_at": pod["created_at"],
                    # The runtime cap counts from the hand-over on a reused Pod.
                    "job_started_at": now,
                    "gpu_wait_seconds": round(waited, 1),
                    "gpu_acquire_attempts": 0,
                    "pod_reused": True,
                    "pod_jobs_before": int(pod.get("jobs_completed") or 0),
                    "progress": {"stage": "pod_reused", "at": now},
                },
                "error": None,
            },
        )
        return {
            "job": {
                "task_id": task_id,
                "input": job,
                "result_url": result_url,
                "progress_url": progress_url,
            }
        }

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
            def refresh() -> None:
                for task in active[:10]:
                    if task.get("provider") in POD_PROVIDERS and not task.get("provider_task_id"):
                        # Queued for a GPU; the guard loop owns it until a Pod exists.
                        continue
                    try:
                        for client in _provider_client(task.get("provider") or "seedance"):
                            remote_id = task.get("provider_task_id") or task["id"]
                            store.update_remote(task["id"], client.get_task(remote_id))
                    except (SeedanceError, ValueError):
                        # A temporary provider outage must not make the whole task list unavailable.
                        continue

            # Provider polls are blocking HTTP calls; keep the event loop free
            # for the Pod callbacks and the other browser tabs.
            await asyncio.to_thread(refresh)
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
        provider = _provider_for(model, settings)
        if is_self_hosted:
            unfinished = store.active_runpod(provider)
            if provider in POD_PROVIDERS and settings.runpod_cost_guard_enabled:
                # The guard loop runs one Pod per lane and a warm worker pulls
                # the queue in order, so a few tasks may line up behind it.
                if len(unfinished) >= MAX_UNFINISHED_POD_TASKS:
                    raise HTTPException(
                        status_code=429,
                        detail=(
                            f"自建 GPU 队列已满（最多 {MAX_UNFINISHED_POD_TASKS} 个未完成任务），"
                            "请等待完成后再提交"
                        ),
                    )
            elif unfinished:
                raise HTTPException(
                    status_code=429,
                    detail="自建 GPU 当前已有任务，请等待完成后再提交",
                )
        if is_self_hosted and resolution == "1080p":
            supported = "480p、720p 或 768p" if model == H3_MODEL else "480p 或 720p"
            raise HTTPException(status_code=422, detail=f"自建模型首版仅支持 {supported}")
        if resolution == "768p" and model != H3_MODEL:
            raise HTTPException(
                status_code=422, detail="768p 只有 MiniMax H3 支持"
            )
        if model == H3_MODEL:
            if not settings.h3_enabled:
                raise HTTPException(status_code=503, detail="MiniMax H3 主线尚未启用")
            if reference and reference.filename:
                raise HTTPException(
                    status_code=422, detail="H3 文生视频首版暂不接收参考图"
                )
        if model == LTX_MODEL and reference and reference.filename:
            raise HTTPException(status_code=422, detail="自建模型首版暂不支持参考图")
        if model == "wan-2.2-a14b-adult-v2":
            if not settings.wan_v2_enabled:
                raise HTTPException(status_code=503, detail="Wan V2 尚未启用")
            if reference and reference.filename:
                raise HTTPException(status_code=422, detail="Wan V2 文生视频首版暂不接收参考图")
            # Beyond ~150k latent tokens an SGLang kernel overflows int32 and
            # renders pure black; 720p crosses that budget after 10 seconds.
            if resolution == "720p" and duration > 10:
                raise HTTPException(
                    status_code=422,
                    detail="Wan 720p 最长支持 10 秒：更长时长请改用 480p 或 LTX 模型",
                )
        image_data_url = None
        if reference and reference.filename:
            if reference.content_type not in ALLOWED_IMAGE_TYPES:
                raise HTTPException(status_code=422, detail="参考图仅支持 JPG、PNG、WebP、GIF 或 HEIC")
            raw = await reference.read(MAX_IMAGE_BYTES + 1)
            if len(raw) > MAX_IMAGE_BYTES:
                raise HTTPException(status_code=413, detail="参考图不能超过 30MB")
            image_data_url = f"data:{reference.content_type};base64,{base64.b64encode(raw).decode()}"
        task_id = str(uuid4())
        created_at = _now()
        task = {
            "id": task_id,
            "provider": provider,
            "provider_task_id": task_id,
            "prompt": prompt,
            "model": model,
            "ratio": ratio,
            "resolution": resolution,
            "duration": duration,
            "generate_audio": int(bool(generate_audio)),
            "has_reference": int(bool(image_data_url)),
            "status": "queued",
            "created_at": created_at,
        }
        if provider in POD_PROVIDERS and settings.runpod_cost_guard_enabled:
            # One-shot Pod lanes get their GPU from the guard loop, not inside
            # this request: a capacity miss then costs a short wait in the task
            # list instead of a 503 and another click, and the event loop never
            # blocks on RunPod's capacity race.
            task["provider_task_id"] = ""
            store.create(task)
            store.update_remote(
                task_id,
                {
                    "status": "queued",
                    "content": {
                        "progress": {"stage": "awaiting_gpu", "attempts": 0, "at": created_at}
                    },
                    "error": None,
                },
            )
            return {"task": store.get(task_id)}

        def submit() -> dict[str, Any]:
            for client in _provider_client(provider):
                options = dict(
                    ratio=ratio,
                    resolution=resolution,
                    duration=duration,
                    generate_audio=generate_audio,
                    watermark=True,
                )
                if provider in POD_PROVIDERS:
                    options["task_id"] = task_id
                if image_data_url and not is_self_hosted:
                    return client.create_reference_video(
                        prompt=prompt, image_url=image_data_url, model=model, **options
                    )
                return client.create_text_video(prompt=prompt, model=model, **options)
            raise RuntimeError("provider factory yielded no client")

        try:
            # Provider submission is blocking HTTP; run it off the event loop.
            remote = await asyncio.to_thread(submit)
        except SeedanceError as exc:
            return _generation_error(exc)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        provider_task_id = str(remote.get("id") or "")
        if not provider_task_id:
            raise HTTPException(status_code=502, detail="生成服务未返回任务编号")
        task["provider_task_id"] = provider_task_id
        task["status"] = str(remote.get("status") or "queued")
        try:
            store.create(task)
            store.update_remote(task_id, remote)
        except Exception:
            if provider in POD_PROVIDERS:
                delete_pod(provider, provider_task_id)
            raise
        return {"task": store.get(task_id)}

    return app


app = create_app()

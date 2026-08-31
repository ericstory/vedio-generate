from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from ai_vedio import web
from ai_vedio.web import (
    TaskStore,
    WebSettings,
    _runpod_cost_guard_tick,
    _sign_session,
    _valid_session,
    create_app,
)
from ai_vedio.web import _generation_error
from ai_vedio.seedance import SeedanceError
from ai_vedio.runpod import RunPodError


def web_settings(tmp_path: Path) -> WebSettings:
    return WebSettings(
        username="admin",
        password="correct horse battery staple",
        session_secret="a-long-test-session-secret",
        base_path="/generate",
        database_path=tmp_path / "tasks.db",
        secure_cookie=False,
    )


def test_session_signature_cannot_be_tampered() -> None:
    token = _sign_session("admin", "secret")
    assert _valid_session(token, "admin", "secret")
    assert not _valid_session(token + "x", "admin", "secret")
    assert not _valid_session(token, "someone-else", "secret")


def test_login_guards_generate_page(tmp_path: Path) -> None:
    app = create_app(web_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/generate/healthz").json() == {"status": "ok"}
        response = client.get("/generate", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/generate/login"

        response = client.post(
            "/generate/api/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert response.status_code == 401

        response = client.post(
            "/generate/api/login",
            json={"username": "admin", "password": "correct horse battery staple"},
        )
        assert response.status_code == 200
        assert response.cookies.get("ai_video_session")
        page = client.get("/generate")
        assert page.status_code == 200
        assert '<base href="/generate/">' in page.text
        assert client.get("/generate/static/app.css").status_code == 200

        client.cookies.clear()
        response = client.post(
            "/generate/api/login-form",
            data={"username": "admin", "password": "correct horse battery staple"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/generate"
        assert response.cookies.get("ai_video_session")


def test_worker_upload_is_private_and_saved_to_volume(tmp_path: Path) -> None:
    settings = replace(
        web_settings(tmp_path),
        video_upload_token="test-worker-token",
        video_output_dir=tmp_path / "generated-videos",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        unauthorized = client.post(
            "/generate/api/internal/video-upload",
            files={"video": ("output.mp4", b"video-bytes", "video/mp4")},
        )
        assert unauthorized.status_code == 401

        uploaded = client.post(
            "/generate/api/internal/video-upload",
            headers={"Authorization": "Bearer test-worker-token"},
            files={"video": ("output.mp4", b"video-bytes", "video/mp4")},
        )
        assert uploaded.status_code == 200
        video_url = uploaded.json()["video_url"]
        assert video_url.startswith("/generate/media/")
        assert list((tmp_path / "generated-videos").glob("*.mp4"))[0].read_bytes() == b"video-bytes"

        assert client.get(video_url).status_code == 401
        client.post(
            "/generate/api/login",
            json={"username": "admin", "password": "correct horse battery staple"},
        )
        response = client.get(video_url)
        assert response.status_code == 200
        assert response.content == b"video-bytes"
        assert response.headers["content-type"] == "video/mp4"


def test_wan_pod_callback_commits_result_and_deletes_billed_pod(
    tmp_path: Path, monkeypatch,
) -> None:
    settings = replace(web_settings(tmp_path), video_upload_token="callback-token")
    store = TaskStore(settings.database_path)
    store.create(
        {
            "id": "local-wan",
            "provider": "runpod_wan_pod",
            "provider_task_id": "pod-123",
            "prompt": "测试",
            "model": "wan-2.2-a14b-adult-v2",
            "ratio": "16:9",
            "resolution": "480p",
            "duration": 4,
            "has_reference": 0,
            "status": "processing",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    store.update_remote(
        "local-wan",
        {
            "status": "processing",
            "content": {"pod_price_per_hour": 2.09},
            "error": None,
        },
    )
    deleted = []

    class FakePodClient:
        def delete_pod(self, pod_id: str):
            deleted.append(pod_id)

    def fake_pod_client():
        yield FakePodClient()

    monkeypatch.setattr(web, "_wan_pod_client", fake_pod_client)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/generate/api/internal/pod-result/local-wan",
            headers={"Authorization": "Bearer callback-token"},
            json={
                "status": "succeeded",
                "content": {
                    "video_url": "/generate/media/result.mp4",
                    "gpu_name": "NVIDIA RTX PRO 6000 Blackwell Server Edition",
                    "video_inference_seconds": 274.2,
                },
                "error": None,
            },
        )
    assert response.status_code == 200
    task = store.get("local-wan")
    assert task and task["status"] == "succeeded"
    assert task["video_url"] == "/generate/media/result.mp4"
    assert task["provider_metadata"]["video_inference_seconds"] == 274.2
    assert task["provider_metadata"]["pod_price_per_hour"] == 2.09
    assert deleted == ["pod-123"]


def test_wan_pod_progress_updates_stage_without_touching_status(tmp_path: Path) -> None:
    settings = replace(web_settings(tmp_path), video_upload_token="callback-token")
    store = TaskStore(settings.database_path)
    store.create(
        {
            "id": "local-wan",
            "provider": "runpod_wan_pod",
            "provider_task_id": "pod-123",
            "prompt": "测试",
            "model": "wan-2.2-a14b-adult-v2",
            "ratio": "16:9",
            "resolution": "480p",
            "duration": 4,
            "has_reference": 0,
            "status": "processing",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    app = create_app(settings)
    with TestClient(app) as client:
        unauthorized = client.post(
            "/generate/api/internal/pod-progress/local-wan",
            headers={"Authorization": "Bearer wrong"},
            json={"stage": "video_start"},
        )
        assert unauthorized.status_code == 401
        response = client.post(
            "/generate/api/internal/pod-progress/local-wan",
            headers={"Authorization": "Bearer callback-token"},
            json={"stage": "video_start", "seconds": 12.5},
        )
    assert response.status_code == 200
    assert response.json()["applied"] is True
    task = store.get("local-wan")
    assert task and task["status"] == "processing"
    assert task["provider_metadata"]["progress"]["stage"] == "video_start"
    assert task["provider_metadata"]["progress"]["seconds"] == 12.5


def test_wan_pod_progress_ignores_terminal_tasks(tmp_path: Path) -> None:
    settings = replace(web_settings(tmp_path), video_upload_token="callback-token")
    store = TaskStore(settings.database_path)
    store.create(
        {
            "id": "done-wan",
            "provider": "runpod_wan_pod",
            "provider_task_id": "pod-456",
            "prompt": "测试",
            "model": "wan-2.2-a14b-adult-v2",
            "ratio": "16:9",
            "resolution": "480p",
            "duration": 4,
            "has_reference": 0,
            "status": "processing",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    store.update_remote("done-wan", {"status": "succeeded", "content": {}, "error": None})
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/generate/api/internal/pod-progress/done-wan",
            headers={"Authorization": "Bearer callback-token"},
            json={"stage": "upload_start"},
        )
    assert response.status_code == 200
    assert response.json()["applied"] is False
    task = store.get("done-wan")
    assert task and "progress" not in (task.get("provider_metadata") or {})


def test_task_store_orders_newest_first(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    base = {
        "prompt": "一段测试视频",
        "model": "seedance-2-fast",
        "ratio": "16:9",
        "resolution": "720p",
        "duration": 6,
        "has_reference": 0,
        "status": "queued",
    }
    store.create({**base, "id": "one", "created_at": "2026-01-01T00:00:00+00:00"})
    store.create({**base, "id": "two", "created_at": "2026-01-02T00:00:00+00:00"})
    assert [task["id"] for task in store.list()] == ["two", "one"]


def test_cost_guard_persists_terminal_task_and_closes_gpu_gate(
    tmp_path: Path, monkeypatch,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    store.create(
        {
            "id": "local-one",
            "provider": "runpod",
            "provider_task_id": "remote-one",
            "prompt": "测试",
            "model": "pinkcherry-ltx-2.3-v1.8",
            "ratio": "16:9",
            "resolution": "480p",
            "duration": 4,
            "has_reference": 0,
            "status": "processing",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )

    class FakeRunPod:
        workers_max = []

        def get_task(self, task_id: str):
            assert task_id == "remote-one"
            return {
                "status": "succeeded",
                "content": {"video_url": "/generate/media/result.mp4"},
                "error": None,
            }

        def set_workers_max(self, value: int):
            self.workers_max.append(value)

        def close(self):
            pass

    fake = FakeRunPod()

    def fake_client():
        try:
            yield fake
        finally:
            fake.close()

    monkeypatch.setattr(web, "_runpod_client", fake_client)
    assert _runpod_cost_guard_tick(store, shutdown_if_idle=False) is False
    task = store.get("local-one")
    assert task and task["status"] == "succeeded"
    assert task["video_url"] == "/generate/media/result.mp4"
    assert fake.workers_max == [0]


def test_cost_guard_expires_job_from_retired_endpoint(tmp_path: Path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    store.create(
        {
            "id": "stale-local",
            "provider": "runpod",
            "provider_task_id": "stale-remote",
            "prompt": "旧任务",
            "model": "pinkcherry-ltx-2.3-v1.8",
            "ratio": "16:9",
            "resolution": "480p",
            "duration": 4,
            "has_reference": 0,
            "status": "queued",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )

    class MissingJobRunPod:
        workers_max = []

        def get_task(self, task_id: str):
            raise RunPodError("job not found", status_code=404)

        def set_workers_max(self, value: int):
            self.workers_max.append(value)

        def close(self):
            pass

    fake = MissingJobRunPod()

    def fake_client():
        yield fake

    monkeypatch.setattr(web, "_runpod_client", fake_client)
    assert _runpod_cost_guard_tick(store, shutdown_if_idle=False) is False
    task = store.get("stale-local")
    assert task and task["status"] == "expired"
    assert fake.workers_max == [0]


def test_generation_error_explains_real_person_assets() -> None:
    error = SeedanceError(
        "request rejected",
        status_code=400,
        error={"code": "InvalidHumanAsset", "message": "real person face requires asset library"},
    )
    response = _generation_error(error)
    body = response.body.decode("utf-8")
    assert response.status_code == 422
    assert "真人参考素材不能直接上传" in body
    assert "asset://" in body


def test_generation_error_explains_moderation() -> None:
    error = SeedanceError(
        "request rejected",
        status_code=400,
        error={"code": "ContentModeration", "message": "input blocked by safety policy"},
    )
    response = _generation_error(error)
    assert response.status_code == 422
    assert "内容安全审核未通过" in response.body.decode("utf-8")


def test_task_creation_rejects_unknown_model_before_calling_provider(
    tmp_path: Path, monkeypatch,
) -> None:
    def provider_must_not_be_called():
        raise AssertionError("provider should not be called for an unsupported model")

    monkeypatch.setattr(web, "_client", provider_must_not_be_called)
    app = create_app(web_settings(tmp_path))
    with TestClient(app) as client:
        client.post(
            "/generate/api/login",
            json={"username": "admin", "password": "correct horse battery staple"},
        )
        response = client.post(
            "/generate/api/tasks",
            data={"prompt": "测试视频", "model": "unknown-model"},
        )
    assert response.status_code == 422
    assert response.json()["detail"] == "生成模型不受支持"


def test_self_hosted_task_uses_independent_provider_ids(tmp_path: Path, monkeypatch) -> None:
    class FakeRunPod:
        def create_text_video(self, **kwargs):
            assert kwargs["model"] == "pinkcherry-ltx-2.3-v1.8"
            return {"id": "runpod-job-123", "status": "queued"}

    def provider_client(provider: str):
        assert provider == "runpod"
        yield FakeRunPod()

    monkeypatch.setattr(web, "_provider_client", provider_client)
    app = create_app(web_settings(tmp_path))
    with TestClient(app) as client:
        client.post(
            "/generate/api/login",
            json={"username": "admin", "password": "correct horse battery staple"},
        )
        response = client.post(
            "/generate/api/tasks",
            data={"prompt": "电影感城市夜景", "model": "pinkcherry-ltx-2.3-v1.8"},
        )
    assert response.status_code == 201
    task = response.json()["task"]
    assert task["id"] != "runpod-job-123"
    assert task["provider"] == "runpod"
    assert task["provider_task_id"] == "runpod-job-123"


def test_self_hosted_task_rejects_reference_before_provider(tmp_path: Path, monkeypatch) -> None:
    def provider_must_not_be_called(provider: str):
        raise AssertionError(f"provider {provider} should not be called")

    monkeypatch.setattr(web, "_provider_client", provider_must_not_be_called)
    app = create_app(web_settings(tmp_path))
    with TestClient(app) as client:
        client.post(
            "/generate/api/login",
            json={"username": "admin", "password": "correct horse battery staple"},
        )
        response = client.post(
            "/generate/api/tasks",
            data={"prompt": "测试", "model": "pinkcherry-ltx-2.3-v1.8"},
            files={"reference": ("test.png", b"png", "image/png")},
        )
    assert response.status_code == 422
    assert response.json()["detail"] == "自建模型首版暂不支持参考图"


def test_wan_v2_uses_independent_provider_with_long_audio_video(
    tmp_path: Path, monkeypatch,
) -> None:
    class FakeWan:
        def create_text_video(self, **kwargs):
            assert kwargs["model"] == "wan-2.2-a14b-adult-v2"
            assert kwargs["duration"] == 15
            assert kwargs["ratio"] == "21:9"
            assert kwargs["generate_audio"] is True
            assert kwargs["task_id"]
            return {"id": "wan-job-123", "status": "queued"}

    def provider_client(provider: str):
        assert provider == "runpod_wan_pod"
        yield FakeWan()

    monkeypatch.setattr(web, "_provider_client", provider_client)
    settings = replace(web_settings(tmp_path), wan_v2_enabled=True)
    app = create_app(settings)
    with TestClient(app) as client:
        client.post(
            "/generate/api/login",
            json={"username": "admin", "password": "correct horse battery staple"},
        )
        response = client.post(
            "/generate/api/tasks",
            data={
                "prompt": "电影感测试",
                "model": "wan-2.2-a14b-adult-v2",
                "duration": "15",
                "resolution": "480p",
                "ratio": "21:9",
                "generate_audio": "true",
            },
        )
    assert response.status_code == 201
    assert response.json()["task"]["provider"] == "runpod_wan_pod"


def test_wan_v2_is_hidden_and_rejected_until_enabled(tmp_path: Path) -> None:
    app = create_app(web_settings(tmp_path))
    with TestClient(app) as client:
        client.post(
            "/generate/api/login",
            json={"username": "admin", "password": "correct horse battery staple"},
        )
        page = client.get("/generate")
        assert 'value="wan-2.2-a14b-adult-v2" disabled' in page.text
        response = client.post(
            "/generate/api/tasks",
            data={
                "prompt": "测试",
                "model": "wan-2.2-a14b-adult-v2",
                "duration": "5",
            },
        )
    assert response.status_code == 503


def test_completed_task_accepts_quality_vote(tmp_path: Path) -> None:
    settings = web_settings(tmp_path)
    store = TaskStore(settings.database_path)
    store.create(
        {
            "id": "finished",
            "prompt": "完成的视频",
            "model": "pinkcherry-ltx-2.3-v1.8",
            "ratio": "16:9",
            "resolution": "480p",
            "duration": 5,
            "has_reference": 0,
            "status": "succeeded",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    app = create_app(settings)
    with TestClient(app) as client:
        client.post(
            "/generate/api/login",
            json={"username": "admin", "password": "correct horse battery staple"},
        )
        response = client.post(
            "/generate/api/tasks/finished/vote", json={"vote": "up"}
        )
    assert response.status_code == 200
    assert response.json()["task"]["quality_vote"] == 1

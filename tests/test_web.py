from dataclasses import replace
from datetime import datetime, timedelta, timezone
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


def test_wan_720p_duration_cap_rejected_before_provider(tmp_path: Path, monkeypatch) -> None:
    settings = replace(web_settings(tmp_path), wan_v2_enabled=True)
    app = create_app(settings)

    def explode(*args, **kwargs):
        raise AssertionError("provider must not be called for an over-budget combo")

    monkeypatch.setattr(web, "_provider_client", explode)
    with TestClient(app) as client:
        client.post(
            "/generate/api/login",
            json={"username": settings.username, "password": settings.password},
        )
        response = client.post(
            "/generate/api/tasks",
            data={
                "prompt": "海边日出",
                "model": "wan-2.2-a14b-adult-v2",
                "ratio": "16:9",
                "resolution": "720p",
                "duration": "12",
                "generate_audio": "true",
            },
        )
    assert response.status_code == 422
    assert "10 秒" in response.json()["detail"]


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


def test_h3_pod_callback_commits_result_and_deletes_billed_pod(
    tmp_path: Path, monkeypatch,
) -> None:
    """The H3 main line shares the one-shot Pod contract, including deletion."""
    settings = replace(web_settings(tmp_path), video_upload_token="callback-token")
    store = TaskStore(settings.database_path)
    store.create(
        {
            "id": "local-h3",
            "provider": "runpod_h3_pod",
            "provider_task_id": "pod-h3-1",
            "prompt": "测试",
            "model": "minimax-h3-pinkcherry",
            "ratio": "16:9",
            "resolution": "768p",
            "duration": 5,
            "has_reference": 0,
            "status": "processing",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    deleted: list[str] = []

    class FakePodClient:
        def delete_pod(self, pod_id: str):
            deleted.append(pod_id)

    def fake_pod_client():
        yield FakePodClient()

    monkeypatch.setattr(web, "_h3_pod_client", fake_pod_client)
    app = create_app(settings)
    with TestClient(app) as client:
        progress = client.post(
            "/generate/api/internal/pod-progress/local-h3",
            headers={"Authorization": "Bearer callback-token"},
            json={"stage": "model_load_done", "seconds": 131.5},
        )
        assert progress.status_code == 200
        response = client.post(
            "/generate/api/internal/pod-result/local-h3",
            headers={"Authorization": "Bearer callback-token"},
            json={
                "status": "succeeded",
                "content": {
                    "video_url": "/generate/media/h3.mp4",
                    "gpu_name": "NVIDIA RTX PRO 6000 Blackwell Server Edition",
                    "peak_memory_mb": 61234,
                    "has_audio": True,
                    "audio_sample_rate": 32000,
                },
                "error": None,
            },
        )
    assert response.status_code == 200
    task = store.get("local-h3")
    assert task and task["status"] == "succeeded"
    assert task["video_url"] == "/generate/media/h3.mp4"
    # H3 produces its soundtrack in the same pass, so the metadata proves the
    # lane never needed a second audio model.
    assert task["provider_metadata"]["audio_sample_rate"] == 32000
    assert task["provider_metadata"]["peak_memory_mb"] == 61234
    assert deleted == ["pod-h3-1"]


def test_h3_lane_is_flag_gated_and_owns_768p(tmp_path: Path) -> None:
    settings = web_settings(tmp_path)
    assert settings.h3_enabled is False
    app = create_app(settings)
    with TestClient(app) as client:
        client.post(
            "/generate/api/login",
            json={"username": "admin", "password": "correct horse battery staple"},
        )
        blocked = client.post(
            "/generate/api/tasks",
            data={
                "prompt": "cinematic wide shot of a sunrise",
                "model": "minimax-h3-pinkcherry",
                "ratio": "16:9",
                "resolution": "768p",
                "duration": 5,
            },
        )
        assert blocked.status_code == 503
        # 768 is H3's short edge; no other lane resolves a canvas from it.
        wrong_lane = client.post(
            "/generate/api/tasks",
            data={
                "prompt": "cinematic wide shot of a sunrise",
                "model": "seedance-2.0",
                "ratio": "16:9",
                "resolution": "768p",
                "duration": 5,
            },
        )
        assert wrong_lane.status_code == 422
        page = client.get("/generate")
        assert 'value="minimax-h3-pinkcherry" disabled' in page.text


def _pod_settings(**overrides):
    from ai_vedio.config import RunPodPodSettings

    base = dict(
        api_key="k",
        template_id="tpl",
        network_volume_id="",
        callback_url="https://host.example/generate/api/internal/pod-result",
        callback_token="t",
        ui_model_id="minimax-h3-pinkcherry",
        acquire_retry_seconds=0,
    )
    base.update(overrides)
    return RunPodPodSettings(**base)


def _queued_h3_task(store: TaskStore, task_id: str = "queued-h3", created_at: str = "2026-01-01T00:00:00+00:00") -> None:
    store.create(
        {
            "id": task_id,
            "provider": "runpod_h3_pod",
            "provider_task_id": "",
            "prompt": "cinematic sunrise",
            "model": "minimax-h3-pinkcherry",
            "ratio": "16:9",
            "resolution": "768p",
            "duration": 5,
            "generate_audio": 0,
            "has_reference": 0,
            "status": "queued",
            "created_at": created_at,
        }
    )


def _capacity_error() -> RunPodError:
    body = {
        "detail": "There are no longer any instances available with the requested specifications.",
        "status": 400,
        "title": "Bad Request",
    }
    return RunPodError(f"RunPod Pod API HTTP 400: {body}", status_code=400, error=body)


def test_pod_task_is_queued_without_touching_the_provider(tmp_path: Path, monkeypatch) -> None:
    """With the guard loop running, submitting returns before any Pod exists."""
    def provider_must_not_be_called(provider: str):
        raise AssertionError(f"provider {provider} should not be called during the request")

    monkeypatch.setattr(web, "_provider_client", provider_must_not_be_called)
    # The guard loop itself must stay quiet in this test.
    monkeypatch.setattr(web, "_runpod_cost_guard_tick", lambda store, shutdown_if_idle: False)
    settings = replace(
        web_settings(tmp_path), h3_enabled=True, runpod_cost_guard_enabled=True,
        runpod_cost_guard_poll_seconds=3600,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        client.post(
            "/generate/api/login",
            json={"username": "admin", "password": "correct horse battery staple"},
        )
        response = client.post(
            "/generate/api/tasks",
            data={
                "prompt": "cinematic wide shot of a sunrise",
                "model": "minimax-h3-pinkcherry",
                "ratio": "16:9",
                "resolution": "768p",
                "duration": 5,
                "generate_audio": "false",
            },
        )
        assert response.status_code == 201
        task = response.json()["task"]
        assert task["status"] == "queued"
        assert task["provider_task_id"] == ""
        assert task["generate_audio"] is False
        assert task["provider_metadata"]["progress"]["stage"] == "awaiting_gpu"
        # A second submission waits behind the queued one exactly like a running Pod.
        again = client.post(
            "/generate/api/tasks",
            data={"prompt": "another", "model": "minimax-h3-pinkcherry", "resolution": "768p", "duration": 5},
        )
        assert again.status_code == 429
        # Listing must not try to poll a Pod that does not exist yet.
        listed = client.get("/generate/api/tasks").json()["tasks"]
        assert listed[0]["id"] == task["id"]


def test_guard_acquires_a_pod_for_a_queued_task(tmp_path: Path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    _queued_h3_task(store, created_at=datetime.now(timezone.utc).isoformat())
    seen: list[dict] = []

    class FakePodClient:
        settings = _pod_settings()

        def create_text_video(self, **kwargs):
            seen.append(kwargs)
            return {
                "id": "pod-new",
                "status": "queued",
                "content": {"video_url": None, "gpu_name": "NVIDIA RTX PRO 6000 Blackwell Server Edition", "pod_price_per_hour": 2.09},
                "error": None,
            }

        def get_task(self, pod_id: str):
            assert pod_id == "pod-new"
            return {"id": pod_id, "status": "processing", "content": {}, "error": None}

        def delete_pod(self, pod_id: str):
            raise AssertionError("a freshly acquired Pod must not be deleted")

    def fake_client():
        yield FakePodClient()

    monkeypatch.setenv("RUNPOD_H3_POD_TEMPLATE_ID", "tpl")
    monkeypatch.delenv("RUNPOD_WAN_POD_TEMPLATE_ID", raising=False)
    monkeypatch.setattr(web, "_h3_pod_client", fake_client)
    monkeypatch.setattr(web, "_runpod_provider_cost_guard_tick", lambda *a, **k: False)
    assert _runpod_cost_guard_tick(store, shutdown_if_idle=False) is True
    assert len(seen) == 1
    assert seen[0]["prompt"] == "cinematic sunrise"
    assert seen[0]["resolution"] == "768p"
    assert seen[0]["duration"] == 5
    assert seen[0]["generate_audio"] is False
    assert seen[0]["task_id"] == "queued-h3"
    # The loop is the retry mechanism, so each pass asks for a single sweep.
    assert seen[0]["capacity_retry_sweeps"] == 1
    task = store.get("queued-h3")
    assert task and task["provider_task_id"] == "pod-new"
    # The Pod exists but the worker has not reported yet; its callbacks move
    # the status from here on.
    assert task["status"] == "queued"
    metadata = task["provider_metadata"]
    assert metadata["gpu_name"].startswith("NVIDIA RTX PRO 6000")
    assert metadata["gpu_acquire_attempts"] == 1
    assert metadata["pod_created_at"]
    assert metadata["progress"]["stage"] == "pod_created"


def test_guard_keeps_waiting_on_capacity_then_fails_without_a_pod(tmp_path: Path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    _queued_h3_task(store, created_at=datetime.now(timezone.utc).isoformat())
    calls = 0

    class NoCapacity:
        settings = _pod_settings(acquire_timeout_seconds=600)

        def create_text_video(self, **kwargs):
            nonlocal calls
            calls += 1
            raise _capacity_error()

        def get_task(self, pod_id: str):
            raise AssertionError("nothing to poll while no Pod exists")

        def delete_pod(self, pod_id: str):
            raise AssertionError("no Pod was ever created")

    def fake_client():
        yield NoCapacity()

    monkeypatch.setenv("RUNPOD_H3_POD_TEMPLATE_ID", "tpl")
    monkeypatch.delenv("RUNPOD_WAN_POD_TEMPLATE_ID", raising=False)
    monkeypatch.setattr(web, "_h3_pod_client", fake_client)
    monkeypatch.setattr(web, "_runpod_provider_cost_guard_tick", lambda *a, **k: False)
    assert _runpod_cost_guard_tick(store, shutdown_if_idle=False) is True
    assert _runpod_cost_guard_tick(store, shutdown_if_idle=False) is True
    task = store.get("queued-h3")
    assert task and task["status"] == "queued"
    assert task["provider_task_id"] == ""
    assert task["provider_metadata"]["progress"] == {
        **task["provider_metadata"]["progress"], "stage": "awaiting_gpu", "attempts": 2, "reason": "capacity",
    }
    assert calls == 2
    # Past the acquisition window the task fails and says nothing was billed.
    with store.connect() as db:
        db.execute("UPDATE tasks SET created_at=? WHERE id=?", ("2026-01-01T00:00:00+00:00", "queued-h3"))
    assert _runpod_cost_guard_tick(store, shutdown_if_idle=False) is False
    task = store.get("queued-h3")
    assert task and task["status"] == "failed"
    assert "未创建 Pod" in task["error"]
    assert "10 分钟" in task["error"]
    assert calls == 2


def test_guard_fails_a_queued_task_on_a_non_transient_provider_error(tmp_path: Path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    _queued_h3_task(store, created_at=datetime.now(timezone.utc).isoformat())

    class PriceCap:
        settings = _pod_settings()

        def create_text_video(self, **kwargs):
            raise RunPodError("RunPod Pod price $4.50/h exceeds the configured cap", status_code=None, error={"message": "price $4.50/h exceeds the configured cap"})

        def delete_pod(self, pod_id: str):
            pass

    class Forbidden:
        settings = _pod_settings()

        def create_text_video(self, **kwargs):
            raise RunPodError("RunPod Pod API HTTP 401: bad key", status_code=401, error={"message": "invalid api key"})

    def price_client():
        yield PriceCap()

    def forbidden_client():
        yield Forbidden()

    monkeypatch.setenv("RUNPOD_H3_POD_TEMPLATE_ID", "tpl")
    monkeypatch.delenv("RUNPOD_WAN_POD_TEMPLATE_ID", raising=False)
    monkeypatch.setattr(web, "_runpod_provider_cost_guard_tick", lambda *a, **k: False)
    # A connection-shaped error (no status code) is transient: keep waiting.
    monkeypatch.setattr(web, "_h3_pod_client", price_client)
    _runpod_cost_guard_tick(store, shutdown_if_idle=False)
    task = store.get("queued-h3")
    assert task and task["status"] == "queued"
    assert task["provider_metadata"]["progress"]["reason"] == "provider"
    # A 401 will not fix itself; fail now with the readable explanation.
    monkeypatch.setattr(web, "_h3_pod_client", forbidden_client)
    _runpod_cost_guard_tick(store, shutdown_if_idle=False)
    task = store.get("queued-h3")
    assert task and task["status"] == "failed"
    assert task["error"].startswith("生成服务拒绝了本次请求")
    assert "invalid api key" in task["error"]


def test_pod_runtime_cap_counts_from_pod_creation_not_from_the_click(tmp_path: Path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    store.create(
        {
            "id": "waited-long",
            "provider": "runpod_h3_pod",
            "provider_task_id": "pod-live",
            "prompt": "x",
            "model": "minimax-h3-pinkcherry",
            "ratio": "16:9",
            "resolution": "768p",
            "duration": 5,
            "has_reference": 0,
            "status": "processing",
            # Clicked 40 minutes ago, but the Pod only came up a minute ago.
            "created_at": (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat(),
        }
    )
    store.update_remote(
        "waited-long",
        {"status": "processing", "content": {"pod_created_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}, "error": None},
    )
    deleted: list[str] = []

    class LivePod:
        settings = _pod_settings()

        def create_text_video(self, **kwargs):
            raise AssertionError("nothing is queued")

        def get_task(self, pod_id: str):
            return {"id": pod_id, "status": "processing", "content": {}, "error": None}

        def delete_pod(self, pod_id: str):
            deleted.append(pod_id)

    def fake_client():
        yield LivePod()

    monkeypatch.setenv("RUNPOD_H3_POD_TEMPLATE_ID", "tpl")
    monkeypatch.delenv("RUNPOD_WAN_POD_TEMPLATE_ID", raising=False)
    monkeypatch.setattr(web, "_h3_pod_client", fake_client)
    monkeypatch.setattr(web, "_runpod_provider_cost_guard_tick", lambda *a, **k: False)
    assert _runpod_cost_guard_tick(store, shutdown_if_idle=False) is True
    assert deleted == []
    task = store.get("waited-long")
    assert task and task["status"] == "processing"


def test_store_restart_keeps_queued_pod_tasks_without_a_pod(tmp_path: Path) -> None:
    """The legacy provider-id backfill must not invent a Pod id for a queued task."""
    store = TaskStore(tmp_path / "tasks.db")
    _queued_h3_task(store)
    store.create(
        {
            "id": "legacy-seedance",
            "provider": "seedance",
            "provider_task_id": "",
            "prompt": "x",
            "model": "seedance-2.0",
            "ratio": "16:9",
            "resolution": "720p",
            "duration": 6,
            "has_reference": 0,
            "status": "queued",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    reopened = TaskStore(tmp_path / "tasks.db")
    assert reopened.get("queued-h3")["provider_task_id"] == ""
    assert reopened.get("legacy-seedance")["provider_task_id"] == "legacy-seedance"
    assert [task["id"] for task in reopened.pending_pod_tasks("runpod_h3_pod")] == ["queued-h3"]
    assert reopened.pending_pod_tasks("runpod_wan_pod") == []

from pathlib import Path

from fastapi.testclient import TestClient

from ai_vedio import web
from ai_vedio.web import TaskStore, WebSettings, _sign_session, _valid_session, create_app
from ai_vedio.web import _generation_error
from ai_vedio.seedance import SeedanceError


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

from pathlib import Path


def test_async_submit_keeps_a_stable_form_reference() -> None:
    script = (
        Path(__file__).parents[1]
        / "src"
        / "ai_vedio"
        / "web_assets"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")
    assert "const form=event.currentTarget" in script
    assert "form.reset()" in script
    assert "event.currentTarget.reset()" not in script


def test_frontend_displays_structured_generation_errors() -> None:
    root = Path(__file__).parents[1] / "src" / "ai_vedio" / "web_assets"
    script = (root / "static" / "app.js").read_text(encoding="utf-8")
    markup = (root / "index.html").read_text(encoding="utf-8")
    assert "showRequestError(err)" in script
    assert "upstream_message" in script
    assert 'id="error-guidance"' in markup
    assert 'id="reference-guide"' in markup


def test_frontend_exposes_all_supported_models() -> None:
    markup = (
        Path(__file__).parents[1] / "src" / "ai_vedio" / "web_assets" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'select name="model" id="model"' in markup
    assert 'type="hidden" name="model"' not in markup
    for model in (
        "wan-2.2-a14b-adult-v2",
        "pinkcherry-ltx-2.3-v1.8",
        "seedance-2.5",
        "seedance-2-mini",
        "seedance-2-fast",
        "seedance-2.0",
    ):
        assert f'value="{model}"' in markup


def test_frontend_switches_self_hosted_capabilities() -> None:
    root = Path(__file__).parents[1] / "src" / "ai_vedio" / "web_assets"
    script = (root / "static" / "app.js").read_text(encoding="utf-8")
    markup = (root / "index.html").read_text(encoding="utf-8")
    assert "syncModelCapabilities" in script
    assert "selfHostedModels" in script
    assert 'id="model-hint"' in markup
    assert "独立云 GPU 链路" in markup


def test_frontend_has_quality_vote_controls() -> None:
    root = Path(__file__).parents[1] / "src" / "ai_vedio" / "web_assets"
    script = (root / "static" / "app.js").read_text(encoding="utf-8")
    markup = (root / "index.html").read_text(encoding="utf-8")
    assert 'id="quality-vote"' in markup
    assert 'data-vote="up"' in markup
    assert 'data-vote="down"' in markup
    assert "/vote" in script

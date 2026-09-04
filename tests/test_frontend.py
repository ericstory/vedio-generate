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
        "minimax-h3-pinkcherry",
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


def test_frontend_treats_h3_as_self_hosted_and_names_its_queue_stages() -> None:
    root = Path(__file__).parents[1] / "src" / "ai_vedio" / "web_assets"
    script = (root / "static" / "app.js").read_text(encoding="utf-8")
    assert "'minimax-h3-pinkcherry': '自建主线 · MiniMax H3 + PinkCherry'" in script
    assert "new Set(['minimax-h3-pinkcherry'" in script
    # The queue and the H3 worker both report stages the user has to be able to read.
    for stage in ("awaiting_gpu", "pod_created", "gpu_probe", "model_download_start", "model_download_done"):
        assert f"{stage}:" in script
    assert "已申请 ${progress.attempts} 次" in script
    # 768p is H3's short edge: enabled only there, and the H3 default.
    assert "option.textContent==='768p')option.disabled=!h3" in script
    assert "if(h3 && resolution.value!=='768p') resolution.value='768p'" in script


def test_frontend_offers_10eros_as_a_second_h3_lane() -> None:
    root = Path(__file__).parents[1] / "src" / "ai_vedio" / "web_assets"
    script = (root / "static" / "app.js").read_text(encoding="utf-8")
    markup = (root / "index.html").read_text(encoding="utf-8")
    assert 'value="minimax-h3-10eros" __EROS_OPTION_STATE__' in markup
    assert "'minimax-h3-10eros': '自建 · MiniMax H3 + 10Eros Max'" in script
    assert "new Set(['minimax-h3-pinkcherry', 'minimax-h3-10eros'" in script
    # Both checkpoints run the H3 worker: the 768p default and the reference-image rule follow the family.
    assert "const H3_MODELS = new Set([H3_MODEL, EROS_MODEL])" in script
    assert "const h3=H3_MODELS.has(model)" in script
    assert "10Eros Max（TURBO 蒸馏已烤进权重，不加 LoRA）" in script

from ai_vedio.capabilities import SEEDANCE_MODELS, VERIFIED_CAPABILITIES
from ai_vedio.config import load_settings


def test_local_configuration_has_all_verified_models() -> None:
    settings = load_settings()
    assert set(settings.endpoints) == set(SEEDANCE_MODELS)
    assert settings.modelark_base_url == "https://ark.ap-southeast.bytepluses.com/api/v3"
    assert settings.asset_library_region == VERIFIED_CAPABILITIES["region"]


def test_endpoint_lookup_rejects_unknown_model() -> None:
    settings = load_settings()
    try:
        settings.endpoint_for("unknown")
    except ValueError as exc:
        assert "Unknown Seedance model" in str(exc)
    else:
        raise AssertionError("unknown model should be rejected")


def test_ltx_pod_lane_reads_its_own_prefix_and_shares_the_serverless_pins(monkeypatch) -> None:
    from ai_vedio.config import load_ltx_pod_settings

    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    monkeypatch.setenv("RUNPOD_LTX_POD_TEMPLATE_ID", "tpl-ltx")
    monkeypatch.setenv("RUNPOD_LTX_POD_CALLBACK_URL", "https://host.example/generate/api/internal/pod-result")
    monkeypatch.setenv("VIDEO_UPLOAD_TOKEN", "tok")
    monkeypatch.setenv("RUNPOD_LTX_POD_KEEP_WARM_SECONDS", "600")
    monkeypatch.setenv("RUNPOD_LTX_POD_ADDITIONAL_GPU_IDS", "NVIDIA RTX PRO 6000 Blackwell Workstation Edition")
    monkeypatch.delenv("RUNPOD_LTX_POD_NETWORK_VOLUME_ID", raising=False)
    monkeypatch.setenv("SELF_HOSTED_MODEL_VERSION", "PinkCherry_FineTune_bf16_v1_8_LTX23")
    settings = load_ltx_pod_settings()
    assert settings.template_id == "tpl-ltx"
    # Volume-free by default: no data-centre pin, weights come down at start.
    assert settings.network_volume_id == ""
    assert settings.container_disk_gb == 120
    assert settings.keep_warm_idle_seconds == 600
    assert settings.additional_gpu_ids == ("NVIDIA RTX PRO 6000 Blackwell Workstation Edition",)
    assert settings.ui_model_id == "pinkcherry-ltx-2.3-v1.8"
    assert settings.name_prefix == "papa-ltx"
    # The Pod lane describes the same weights the serverless endpoint ran.
    assert settings.model_id == "SexGod1979/PinkCherry_NSFW_LTX23"
    assert settings.model_version == "PinkCherry_FineTune_bf16_v1_8_LTX23"
    assert settings.workflow_version == "pinkcherry-native-two-stage-v1"
    # PinkCherry LTX is the whole checkpoint: no adapter pin, no swapped transformer.
    assert settings.adult_adapter_id == "" and settings.adult_model_id == ""



def test_eros_pod_lane_is_the_h3_shape_with_its_own_prefix_and_checkpoint(monkeypatch) -> None:
    from ai_vedio.config import EROS_RESTORED_MODEL_ID, EROS_RESTORED_REVISION, load_eros_pod_settings

    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    monkeypatch.setenv("RUNPOD_EROS_POD_TEMPLATE_ID", "tpl-eros")
    monkeypatch.setenv("RUNPOD_EROS_POD_CALLBACK_URL", "https://host.example/generate/api/internal/pod-result")
    monkeypatch.setenv("VIDEO_UPLOAD_TOKEN", "tok")
    monkeypatch.setenv("RUNPOD_EROS_POD_KEEP_WARM_SECONDS", "600")
    monkeypatch.setenv("RUNPOD_EROS_POD_PREFERRED_DATA_CENTER_IDS", "US-NC-1,US-NC-2")
    monkeypatch.delenv("RUNPOD_EROS_POD_NETWORK_VOLUME_ID", raising=False)
    monkeypatch.delenv("EROS_NSFW_MODEL_ID", raising=False)
    monkeypatch.delenv("EROS_NSFW_MODEL_VERSION", raising=False)
    settings = load_eros_pod_settings()
    assert settings.template_id == "tpl-eros"
    assert settings.network_volume_id == ""
    assert settings.container_disk_gb == 220
    assert settings.keep_warm_idle_seconds == 600
    assert settings.preferred_data_center_ids == ("US-NC-1", "US-NC-2")
    assert settings.ui_model_id == "minimax-h3-10eros"
    assert settings.name_prefix == "papa-eros"
    # Same base model as the PinkCherry lane; the swapped transformer is what differs.
    assert settings.model_id == "MiniMaxAI/MiniMax-H3"
    assert settings.workflow_version == "h3-fl2va-10eros-beta4-v1"
    assert settings.adult_adapter_id == ""
    assert settings.adult_model_id == EROS_RESTORED_MODEL_ID == "Andrew3453/10Eros-Max-h3-restored"
    assert settings.adult_model_version == EROS_RESTORED_REVISION
    assert len(EROS_RESTORED_REVISION) == 40, "pin the restored checkpoint's Hub revision"

from ai_vedio.capabilities import SUPPORTED_MODELS, VERIFIED_CAPABILITIES
from ai_vedio.config import load_settings


def test_local_configuration_has_all_verified_models() -> None:
    settings = load_settings()
    assert set(settings.endpoints) == set(SUPPORTED_MODELS)
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

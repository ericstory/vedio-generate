from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    modelark_api_key: str
    modelark_base_url: str
    endpoints: dict[str, str]
    byteplus_ak: str
    byteplus_sk: str
    asset_library_project_name: str
    asset_library_region: str
    asset_library_api_host: str

    def endpoint_for(self, model: str) -> str:
        try:
            return self.endpoints[model]
        except KeyError as exc:
            choices = ", ".join(sorted(self.endpoints))
            raise ValueError(f"Unknown Seedance model {model!r}; choose one of: {choices}") from exc


@dataclass(frozen=True)
class RunPodSettings:
    api_key: str
    endpoint_id: str
    api_base_url: str = "https://api.runpod.ai/v2"
    management_api_base_url: str = "https://rest.runpod.io/v1"
    model_id: str = "SexGod1979/PinkCherry_NSFW_LTX23"
    model_version: str = "PinkCherry_FineTune_bf16_v1_8_LTX23"
    workflow_version: str = "pinkcherry-native-two-stage-v1"
    ui_model_id: str = "pinkcherry-ltx-2.3-v1.8"
    adult_adapter_id: str = ""
    adult_adapter_version: str = ""
    adult_adapter_strength: float = 1.0


def load_settings(env_file: str | Path | None = None) -> Settings:
    _load_dotenv(Path(env_file) if env_file else PROJECT_ROOT / ".env")
    return Settings(
        modelark_api_key=_required("MODELARK_API_KEY"),
        modelark_base_url=os.getenv(
            "MODELARK_BASE_URL", "https://ark.ap-southeast.bytepluses.com/api/v3"
        ).rstrip("/"),
        endpoints={
            "seedance-2.5": _required("SEEDANCE_25_ENDPOINT"),
            "seedance-2-mini": _required("SEEDANCE_2_MINI_ENDPOINT"),
            "seedance-2-fast": _required("SEEDANCE_2_FAST_ENDPOINT"),
            "seedance-2.0": _required("SEEDANCE_20_ENDPOINT"),
        },
        byteplus_ak=_required("BYTEPLUS_AK"),
        byteplus_sk=_required("BYTEPLUS_SK"),
        asset_library_project_name=os.getenv("ASSET_LIBRARY_PROJECT_NAME", "default"),
        asset_library_region=os.getenv("ASSET_LIBRARY_REGION", "ap-southeast-1"),
        asset_library_api_host=os.getenv(
            "ASSET_LIBRARY_API_HOST", "ark.ap-southeast-1.byteplusapi.com"
        ),
    )


def load_runpod_settings(env_file: str | Path | None = None) -> RunPodSettings:
    _load_dotenv(Path(env_file) if env_file else PROJECT_ROOT / ".env")
    return RunPodSettings(
        api_key=_required("RUNPOD_API_KEY"),
        endpoint_id=_required("RUNPOD_ENDPOINT_ID"),
        api_base_url=os.getenv("RUNPOD_API_BASE_URL", "https://api.runpod.ai/v2").rstrip("/"),
        management_api_base_url=os.getenv(
            "RUNPOD_MANAGEMENT_API_BASE_URL", "https://rest.runpod.io/v1"
        ).rstrip("/"),
        model_id=os.getenv(
            "SELF_HOSTED_MODEL_ID", "SexGod1979/PinkCherry_NSFW_LTX23"
        ),
        model_version=os.getenv(
            "SELF_HOSTED_MODEL_VERSION", "PinkCherry_FineTune_bf16_v1_8_LTX23"
        ),
        workflow_version=os.getenv(
            "SELF_HOSTED_WORKFLOW_VERSION", "pinkcherry-native-two-stage-v1"
        ),
    )


def load_wan_runpod_settings(env_file: str | Path | None = None) -> RunPodSettings:
    """Load the independent Wan V2 endpoint without changing the LTX V1 contract."""
    _load_dotenv(Path(env_file) if env_file else PROJECT_ROOT / ".env")
    return RunPodSettings(
        api_key=_required("RUNPOD_API_KEY"),
        endpoint_id=_required("RUNPOD_WAN_ENDPOINT_ID"),
        api_base_url=os.getenv("RUNPOD_API_BASE_URL", "https://api.runpod.ai/v2").rstrip("/"),
        management_api_base_url=os.getenv(
            "RUNPOD_MANAGEMENT_API_BASE_URL", "https://rest.runpod.io/v1"
        ).rstrip("/"),
        model_id=os.getenv("WAN_MODEL_ID", "nvidia/Wan2.2-T2V-A14B-Diffusers-FP8"),
        model_version=os.getenv(
            "WAN_MODEL_VERSION", "2c5a06469cd2255816eb2e46b8e11600ed435d52"
        ),
        workflow_version=os.getenv(
            "WAN_WORKFLOW_VERSION", "wan22-t2v-fp8-adult-lora-audio-v4"
        ),
        ui_model_id="wan-2.2-a14b-adult-v2",
        adult_adapter_id=os.getenv(
            "WAN_ADULT_ADAPTER_ID", "lopi999/Wan2.2-I2V_General-NSFW-LoRA"
        ),
        adult_adapter_version=os.getenv(
            "WAN_ADULT_ADAPTER_VERSION", "aeef17d7fa51d753ab7d1004ddb4f218a95d756d"
        ),
        adult_adapter_strength=float(os.getenv("WAN_ADULT_ADAPTER_STRENGTH", "0.9")),
    )

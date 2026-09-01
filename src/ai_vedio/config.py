from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
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
    management_api_base_url: str = "https://api.runpod.io/v2"
    use_management_api_v1: bool = False
    model_id: str = "SexGod1979/PinkCherry_NSFW_LTX23"
    model_version: str = "PinkCherry_FineTune_bf16_v1_8_LTX23"
    workflow_version: str = "pinkcherry-native-two-stage-v1"
    ui_model_id: str = "pinkcherry-ltx-2.3-v1.8"
    adult_adapter_id: str = ""
    adult_adapter_version: str = ""
    adult_adapter_strength: float = 1.0


@dataclass(frozen=True)
class RunPodPodSettings:
    api_key: str
    template_id: str
    network_volume_id: str
    callback_url: str
    callback_token: str
    api_base_url: str = "https://api.runpod.io/v2"
    use_management_api_v1: bool = False
    gpu_id: str = "NVIDIA RTX PRO 6000 Blackwell Server Edition"
    data_center_id: str = "US-KS-2"
    fallback_data_center_id: str = ""
    fallback_network_volume_id: str = ""
    additional_region_volumes: tuple[tuple[str, str], ...] = ()
    volume_mount_path: str = "/runpod-volume"
    maximum_price_per_hour: float = 3.0
    maximum_runtime_seconds: int = 1800
    model_id: str = "nvidia/Wan2.2-T2V-A14B-Diffusers-FP8"
    model_version: str = "2c5a06469cd2255816eb2e46b8e11600ed435d52"
    workflow_version: str = "wan22-t2v-fp8-resident96-adult-lora-audio-v5"
    ui_model_id: str = "wan-2.2-a14b-adult-v2"
    name_prefix: str = "papa-wan"
    adult_adapter_id: str = "lopi999/Wan2.2-I2V_General-NSFW-LoRA"
    adult_adapter_version: str = "aeef17d7fa51d753ab7d1004ddb4f218a95d756d"
    adult_adapter_strength: float = 0.9
    # H3's NSFW layer is a full fine-tuned checkpoint rather than an adapter, so
    # it pins a model id/revision instead of an adapter id/revision/strength.
    # Each lane sets exactly one of the two pairs.
    adult_model_id: str = ""
    adult_model_version: str = ""


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
    use_management_api_v1 = os.getenv("RUNPOD_API_V1", "0") == "1"
    return RunPodSettings(
        api_key=_required("RUNPOD_API_KEY"),
        endpoint_id=_required("RUNPOD_ENDPOINT_ID"),
        api_base_url=os.getenv("RUNPOD_API_BASE_URL", "https://api.runpod.ai/v2").rstrip("/"),
        management_api_base_url=os.getenv(
            "RUNPOD_MANAGEMENT_API_BASE_URL",
            "https://rest.runpod.io/v1" if use_management_api_v1 else "https://api.runpod.io/v2",  # rp-migrate: keep-v1
        ).rstrip("/"),
        use_management_api_v1=use_management_api_v1,
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
    use_management_api_v1 = os.getenv("RUNPOD_API_V1", "0") == "1"
    return RunPodSettings(
        api_key=_required("RUNPOD_API_KEY"),
        endpoint_id=_required("RUNPOD_WAN_ENDPOINT_ID"),
        api_base_url=os.getenv("RUNPOD_API_BASE_URL", "https://api.runpod.ai/v2").rstrip("/"),
        management_api_base_url=os.getenv(
            "RUNPOD_MANAGEMENT_API_BASE_URL",
            "https://rest.runpod.io/v1" if use_management_api_v1 else "https://api.runpod.io/v2",  # rp-migrate: keep-v1
        ).rstrip("/"),
        use_management_api_v1=use_management_api_v1,
        model_id=os.getenv("WAN_MODEL_ID", "nvidia/Wan2.2-T2V-A14B-Diffusers-FP8"),
        model_version=os.getenv(
            "WAN_MODEL_VERSION", "2c5a06469cd2255816eb2e46b8e11600ed435d52"
        ),
        workflow_version=os.getenv(
            "WAN_WORKFLOW_VERSION", "wan22-t2v-fp8-resident96-adult-lora-audio-v5"
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


def _parse_region_volumes(env_name: str) -> tuple[tuple[str, str], ...]:
    """Decode the optional extra (data centre, network volume) Pod lanes."""
    raw_region_volumes = os.getenv(env_name, "").strip()
    if not raw_region_volumes:
        return ()
    try:
        decoded_region_volumes = json.loads(raw_region_volumes)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{env_name} must be valid JSON") from exc
    if not isinstance(decoded_region_volumes, list):
        raise ValueError(f"{env_name} must be a JSON list")
    lanes: list[tuple[str, str]] = []
    for lane in decoded_region_volumes:
        if not isinstance(lane, dict):
            raise ValueError(f"Every {env_name} lane must be a JSON object")
        data_center_id = str(lane.get("data_center_id") or "").strip()
        network_volume_id = str(lane.get("network_volume_id") or "").strip()
        if not data_center_id or not network_volume_id:
            raise ValueError(
                f"Every {env_name} lane requires data_center_id and network_volume_id"
            )
        lanes.append((data_center_id, network_volume_id))
    return tuple(lanes)


def load_wan_pod_settings(env_file: str | Path | None = None) -> RunPodPodSettings:
    """Load the price-capped, exact-GPU Wan Pod lane."""
    _load_dotenv(Path(env_file) if env_file else PROJECT_ROOT / ".env")
    use_management_api_v1 = os.getenv("RUNPOD_API_V1", "0") == "1"
    additional_region_volumes = _parse_region_volumes(
        "RUNPOD_WAN_POD_ADDITIONAL_REGION_VOLUMES"
    )
    return RunPodPodSettings(
        api_key=_required("RUNPOD_API_KEY"),
        template_id=_required("RUNPOD_WAN_POD_TEMPLATE_ID"),
        network_volume_id=_required("RUNPOD_WAN_POD_NETWORK_VOLUME_ID"),
        callback_url=_required("RUNPOD_WAN_POD_CALLBACK_URL"),
        callback_token=_required("VIDEO_UPLOAD_TOKEN"),
        api_base_url=os.getenv(
            "RUNPOD_MANAGEMENT_API_BASE_URL",
            "https://rest.runpod.io/v1" if use_management_api_v1 else "https://api.runpod.io/v2",  # rp-migrate: keep-v1
        ).rstrip("/"),
        use_management_api_v1=use_management_api_v1,
        gpu_id=os.getenv(
            "RUNPOD_WAN_POD_GPU_ID",
            "NVIDIA RTX PRO 6000 Blackwell Server Edition",
        ),
        data_center_id=os.getenv("RUNPOD_WAN_POD_DATA_CENTER_ID", "US-KS-2"),
        fallback_data_center_id=os.getenv(
            "RUNPOD_WAN_POD_FALLBACK_DATA_CENTER_ID", ""
        ),
        fallback_network_volume_id=os.getenv(
            "RUNPOD_WAN_POD_FALLBACK_NETWORK_VOLUME_ID", ""
        ),
        additional_region_volumes=tuple(additional_region_volumes),
        maximum_price_per_hour=float(
            os.getenv("RUNPOD_WAN_POD_MAX_PRICE_PER_HOUR", "3.0")
        ),
        maximum_runtime_seconds=int(
            os.getenv("RUNPOD_WAN_POD_MAX_RUNTIME_SECONDS", "1800")
        ),
        model_id=os.getenv("WAN_MODEL_ID", "nvidia/Wan2.2-T2V-A14B-Diffusers-FP8"),
        model_version=os.getenv(
            "WAN_MODEL_VERSION", "2c5a06469cd2255816eb2e46b8e11600ed435d52"
        ),
        workflow_version=os.getenv(
            "WAN_WORKFLOW_VERSION", "wan22-t2v-fp8-resident96-adult-lora-audio-v5"
        ),
        adult_adapter_id=os.getenv(
            "WAN_ADULT_ADAPTER_ID", "lopi999/Wan2.2-I2V_General-NSFW-LoRA"
        ),
        adult_adapter_version=os.getenv(
            "WAN_ADULT_ADAPTER_VERSION", "aeef17d7fa51d753ab7d1004ddb4f218a95d756d"
        ),
        adult_adapter_strength=float(os.getenv("WAN_ADULT_ADAPTER_STRENGTH", "0.9")),
    )


def load_h3_pod_settings(env_file: str | Path | None = None) -> RunPodPodSettings:
    """Load the MiniMax H3 main line: same price-capped, exact-GPU Pod shape."""
    _load_dotenv(Path(env_file) if env_file else PROJECT_ROOT / ".env")
    use_management_api_v1 = os.getenv("RUNPOD_API_V1", "0") == "1"
    return RunPodPodSettings(
        api_key=_required("RUNPOD_API_KEY"),
        template_id=_required("RUNPOD_H3_POD_TEMPLATE_ID"),
        network_volume_id=_required("RUNPOD_H3_POD_NETWORK_VOLUME_ID"),
        callback_url=_required("RUNPOD_H3_POD_CALLBACK_URL"),
        callback_token=_required("VIDEO_UPLOAD_TOKEN"),
        api_base_url=os.getenv(
            "RUNPOD_MANAGEMENT_API_BASE_URL",
            "https://rest.runpod.io/v1" if use_management_api_v1 else "https://api.runpod.io/v2",  # rp-migrate: keep-v1
        ).rstrip("/"),
        use_management_api_v1=use_management_api_v1,
        gpu_id=os.getenv(
            "RUNPOD_H3_POD_GPU_ID",
            "NVIDIA RTX PRO 6000 Blackwell Server Edition",
        ),
        # US-NC-2 held the best RTX PRO 6000 secure stock across repeated
        # sampling on 2026-08-31; US-KS-2 was the weakest of the three lanes we
        # own volumes in, which is why it is no longer a primary.
        data_center_id=os.getenv("RUNPOD_H3_POD_DATA_CENTER_ID", "US-NC-2"),
        fallback_data_center_id=os.getenv("RUNPOD_H3_POD_FALLBACK_DATA_CENTER_ID", ""),
        fallback_network_volume_id=os.getenv(
            "RUNPOD_H3_POD_FALLBACK_NETWORK_VOLUME_ID", ""
        ),
        additional_region_volumes=_parse_region_volumes(
            "RUNPOD_H3_POD_ADDITIONAL_REGION_VOLUMES"
        ),
        maximum_price_per_hour=float(
            os.getenv("RUNPOD_H3_POD_MAX_PRICE_PER_HOUR", "3.0")
        ),
        maximum_runtime_seconds=int(
            os.getenv("RUNPOD_H3_POD_MAX_RUNTIME_SECONDS", "1800")
        ),
        model_id=os.getenv("H3_MODEL_ID", "MiniMaxAI/MiniMax-H3"),
        model_version=os.getenv(
            "H3_MODEL_VERSION", "42ed227ee7df40d41602854ae760620d6eb651fe"
        ),
        workflow_version=os.getenv(
            "H3_WORKFLOW_VERSION", "h3-fl2va-pinkcherry-turbo8-v1"
        ),
        ui_model_id="minimax-h3-pinkcherry",
        name_prefix="papa-h3",
        # H3's NSFW layer replaces the transformer instead of adapting it, so
        # the adapter triple stays empty and the checkpoint pair carries it.
        adult_adapter_id="",
        adult_adapter_version="",
        adult_adapter_strength=1.0,
        adult_model_id=os.getenv(
            "H3_NSFW_MODEL_ID", "SexGod1979/PinkCherry_MiniMax-H3"
        ),
        adult_model_version=os.getenv(
            "H3_NSFW_MODEL_VERSION", "bf2fef11d0e55e957f4af997e3beade3362f44b3"
        ),
    )

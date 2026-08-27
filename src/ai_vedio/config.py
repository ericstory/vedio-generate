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

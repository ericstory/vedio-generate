from __future__ import annotations

import json
from typing import Any

from byteplus_sdk.ApiInfo import ApiInfo
from byteplus_sdk.Credentials import Credentials
from byteplus_sdk.ServiceInfo import ServiceInfo
from byteplus_sdk.base.Service import Service

from .config import Settings


class AssetLibraryError(RuntimeError):
    pass


class AssetLibraryClient:
    ACTIONS = (
        "CreateAssetGroup",
        "GetAssetGroup",
        "ListAssetGroups",
        "CreateAsset",
        "GetAsset",
        "ListAssets",
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        credentials = Credentials(
            settings.byteplus_ak,
            settings.byteplus_sk,
            "ark",
            settings.asset_library_region,
        )
        service_info = ServiceInfo(
            host=settings.asset_library_api_host,
            header={},
            credentials=credentials,
            connection_timeout=30,
            socket_timeout=60,
            scheme="https",
        )
        api_info = {
            action: ApiInfo(
                method="POST",
                path="/",
                query={"Action": action, "Version": "2024-01-01"},
                form={},
                header={},
            )
            for action in self.ACTIONS
        }
        self._service = Service(service_info, api_info)

    def _call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload.setdefault("ProjectName", self.settings.asset_library_project_name)
        try:
            response = json.loads(self._service.json(action, {}, payload))
        except Exception as exc:
            raise AssetLibraryError(f"{action} failed: {exc}") from exc
        metadata = response.get("ResponseMetadata") or {}
        if metadata.get("Error"):
            raise AssetLibraryError(f"{action} failed: {metadata['Error']}")
        return response.get("Result") or {}

    def list_asset_groups(self, *, page_number: int = 1, page_size: int = 20) -> dict[str, Any]:
        return self._call(
            "ListAssetGroups",
            {
                "Filter": {"GroupType": "AIGC"},
                "PageNumber": page_number,
                "PageSize": page_size,
                "SortBy": "CreateTime",
                "SortOrder": "Desc",
            },
        )

    def list_assets(self, *, page_number: int = 1, page_size: int = 20) -> dict[str, Any]:
        return self._call(
            "ListAssets",
            {
                "Filter": {"GroupType": "AIGC"},
                "PageNumber": page_number,
                "PageSize": page_size,
                "SortBy": "CreateTime",
                "SortOrder": "Desc",
            },
        )

    def create_asset_group(self, name: str, *, description: str = "") -> str:
        payload: dict[str, Any] = {"Name": name, "GroupType": "AIGC"}
        if description:
            payload["Description"] = description
        result = self._call("CreateAssetGroup", payload)
        asset_group_id = result.get("Id")
        if not asset_group_id:
            raise AssetLibraryError("CreateAssetGroup returned no Id")
        return str(asset_group_id)

    def create_asset(
        self,
        *,
        url: str,
        name: str,
        asset_type: str,
        group_id: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "URL": url,
            "Name": name,
            "AssetType": asset_type,
        }
        if group_id:
            payload["GroupId"] = group_id
        result = self._call("CreateAsset", payload)
        asset_id = result.get("Id")
        if not asset_id:
            raise AssetLibraryError("CreateAsset returned no Id")
        return str(asset_id)

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        return self._call("GetAsset", {"Id": asset_id})

    @staticmethod
    def asset_uri(asset_id: str) -> str:
        return f"asset://{asset_id}"

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from ai_vedio import AssetLibraryClient, SeedanceClient, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Check global BytePlus Seedance connectivity")
    parser.add_argument("--generate", action="store_true", help="Create a billable 4-second video")
    parser.add_argument("--model", default="seedance-2-fast")
    args = parser.parse_args()

    settings = load_settings()
    with SeedanceClient(settings) as seedance:
        tasks = seedance.list_tasks(page_size=1)
        print(json.dumps({"modelark": "ok", "visible_tasks": len(tasks.get("items", []))}))
        if args.generate:
            created = seedance.create_text_video(
                prompt="A calm sunrise over ocean waves, cinematic wide shot, no text, no people.",
                model=args.model,
            )
            task_id = created["id"]
            print(json.dumps({"created_task": task_id, "model": args.model}))
            result = seedance.wait_for_task(task_id)
            print(json.dumps({"task": task_id, "status": result["status"], "usage": result.get("usage")}))

    assets = AssetLibraryClient(settings)
    groups = assets.list_asset_groups(page_size=1)
    items = assets.list_assets(page_size=1)
    print(
        json.dumps(
            {
                "asset_library": "ok",
                "project": settings.asset_library_project_name,
                "group_count": groups.get("TotalCount", 0),
                "asset_count": items.get("TotalCount", 0),
            }
        )
    )


if __name__ == "__main__":
    main()

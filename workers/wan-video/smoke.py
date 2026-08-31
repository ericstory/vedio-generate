from __future__ import annotations

import json
import os

from handler import handler


def main() -> None:
    raw_input = os.environ.get("SMOKE_INPUT_JSON", "").strip()
    if not raw_input:
        raise RuntimeError("SMOKE_INPUT_JSON is required")
    params = json.loads(raw_input)
    result = handler({"id": "wan-pod-smoke", "input": params})
    # The prompt and credentials are deliberately excluded from process logs.
    print(json.dumps({"event": "smoke_complete", "result": result}), flush=True)


if __name__ == "__main__":
    main()

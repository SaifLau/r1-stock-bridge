#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_provider_settings, openai_timeout_seconds
from app.openai_compat import OpenAICompatClient


def main() -> int:
    settings = load_provider_settings()
    client = OpenAICompatClient(settings=settings, timeout=openai_timeout_seconds())

    print("Provider:")
    print(json.dumps(settings.public_dict(), ensure_ascii=False, indent=2))

    print("\nGET /models")
    models_status, models_body = client.list_models()
    print(f"status={models_status}")
    if isinstance(models_body, dict):
        data = models_body.get("data")
        if isinstance(data, list):
            print(
                json.dumps(
                    {"models": [item.get("id") for item in data if isinstance(item, dict)]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(json.dumps(models_body, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"raw": str(models_body)}, ensure_ascii=False, indent=2))

    print("\nPOST generate")
    status, result = client.generate_text("请回复：R1 LAB OK")
    print(f"status={status}")
    print(json.dumps({"text": result["text"], "model": result["model"]}, ensure_ascii=False, indent=2))
    if status >= 400:
        print(json.dumps(result["raw"], ensure_ascii=False, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

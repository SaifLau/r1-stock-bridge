from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "config" / ".env"


@dataclass
class ProviderSettings:
    provider_name: str
    base_url: str
    api_key: str
    model: str
    wire_api: str = "responses"
    prefer_sse: bool = False

    def public_dict(self) -> dict[str, str]:
        return {
            "provider_name": self.provider_name,
            "base_url": self.base_url,
            "model": self.model,
            "wire_api": self.wire_api,
            "prefer_sse": "yes" if self.prefer_sse else "no",
            "api_key_present": "yes" if bool(self.api_key) else "no",
        }


def load_dotenv(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _load_codex_config() -> dict[str, Any]:
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.exists():
        return {}
    with config_path.open("rb") as handle:
        return tomllib.load(handle)


def _load_codex_auth() -> dict[str, Any]:
    auth_path = Path.home() / ".codex" / "auth.json"
    if not auth_path.exists():
        return {}
    return json.loads(auth_path.read_text())


def _provider_from_codex(cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    provider_name = str(cfg.get("model_provider") or "").strip()
    providers = cfg.get("model_providers", {})
    if provider_name and isinstance(providers, dict):
        provider = providers.get(provider_name)
        if isinstance(provider, dict):
            return provider_name, provider

    if isinstance(providers, dict):
        for name, provider in providers.items():
            if isinstance(provider, dict):
                return str(name), provider

    return "", {}


def env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def openai_timeout_seconds() -> float:
    return env_float("R1LAB_OPENAI_TIMEOUT_SECONDS", 20.0)


def env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def load_provider_settings() -> ProviderSettings:
    load_dotenv()

    cfg = _load_codex_config()
    auth = _load_codex_auth()
    codex_provider_name, codex_provider = _provider_from_codex(cfg)

    provider_name = (
        os.getenv("R1LAB_OPENAI_PROVIDER")
        or codex_provider_name
        or "default"
    ).strip()
    base_url = (
        os.getenv("R1LAB_OPENAI_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or str(codex_provider.get("base_url") or "").strip()
    ).rstrip("/")
    api_key = (
        os.getenv("R1LAB_OPENAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or str(auth.get("OPENAI_API_KEY") or "").strip()
    )
    model = (
        os.getenv("R1LAB_OPENAI_MODEL")
        or os.getenv("OPENAI_MODEL")
        or str(cfg.get("model") or "").strip()
        or "gpt-5.4"
    )
    wire_api = (
        os.getenv("R1LAB_WIRE_API")
        or str(codex_provider.get("wire_api") or "").strip()
        or "responses"
    )
    prefer_sse = (
        os.getenv("R1LAB_PREFER_SSE")
        or os.getenv("OPENAI_PREFER_SSE")
        or ""
    ).strip().lower() in {"1", "true", "yes", "on"}

    api_cfg = cfg.get("api")
    if not prefer_sse and isinstance(api_cfg, dict):
        prefer_sse = bool(api_cfg.get("prefer_server_sent_events"))

    if not base_url:
        raise RuntimeError("Missing base_url. Set R1LAB_OPENAI_BASE_URL or configure ~/.codex/config.toml")
    if not api_key:
        raise RuntimeError("Missing API key. Set R1LAB_OPENAI_API_KEY or configure ~/.codex/auth.json")

    return ProviderSettings(
        provider_name=provider_name,
        base_url=base_url,
        api_key=api_key,
        model=model,
        wire_api=wire_api,
        prefer_sse=prefer_sse,
    )

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .config import env_bool, env_float, load_dotenv


AUTH_STORAGE_CANDIDATES = (
    Path("/var/lib/homeassistant/.storage/auth"),
    Path("/root/.homeassistant/.storage/auth"),
    Path("/home/homeassistant/.homeassistant/.storage/auth"),
)
SUPPORTED_DOMAINS = {
    "binary_sensor",
    "climate",
    "cover",
    "fan",
    "input_boolean",
    "light",
    "media_player",
    "number",
    "script",
    "select",
    "sensor",
    "switch",
}
STATE_WORDS = (
    "状态",
    "现在",
    "多少",
    "几度",
    "几瓦",
    "功率",
    "电量",
    "温度",
    "湿度",
    "运行时间",
    "开着吗",
    "关着吗",
    "开了吗",
    "关了吗",
    "有没有开",
    "是不是开着",
    "是不是关着",
)
TURN_ON_WORDS = (
    "打开",
    "开启",
    "开一下",
    "开下",
    "启动",
)
TURN_OFF_WORDS = (
    "关闭",
    "关掉",
    "关上",
    "关一下",
    "关下",
    "关了",
    "停止",
)
IMPERATIVE_KEYWORDS = (
    "关机",
    "音量加",
    "音量减",
    "返回",
    "主页",
    "切换hdmi",
    "切hdmi",
    "切到hdmi",
    "hdmi",
    "本地",
    "播放",
    "暂停",
)
PUNCT_TRANSLATION = str.maketrans("", "", " \t\r\n，。！？；：、“”‘’（）()[]【】,.!?;:/\\-_'\"`")


@dataclass
class HomeAssistantSettings:
    enabled: bool
    base_url: str
    access_token: str
    refresh_token: str
    client_id: str
    timeout: float
    cache_seconds: float


@dataclass
class HomeAssistantResult:
    handled: bool
    route: str
    answer: str
    entity_id: str | None = None
    friendly_name: str | None = None
    action: str | None = None


def _normalize_text(text: str) -> str:
    cleaned = text.strip().lower()
    replacements = {
        "帮我": "",
        "请帮我": "",
        "请": "",
        "给我": "",
        "把": "",
        "将": "",
        "呢": "",
        "吧": "",
        "呀": "",
        "啊": "",
    }
    for src, dst in replacements.items():
        cleaned = cleaned.replace(src, dst)
    return cleaned.translate(PUNCT_TRANSLATION)


def _load_auth_storage() -> dict[str, Any]:
    for path in AUTH_STORAGE_CANDIDATES:
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text())
        except Exception:
            continue
    return {}


def discover_refresh_token() -> tuple[str, str]:
    body = _load_auth_storage()
    data = body.get("data")
    if not isinstance(data, dict):
        return "", ""

    admin_user_ids: set[str] = set()
    for user in data.get("users", []):
        if not isinstance(user, dict):
            continue
        if not user.get("is_active"):
            continue
        group_ids = user.get("group_ids")
        if isinstance(group_ids, list) and "system-admin" in group_ids:
            user_id = str(user.get("id") or "").strip()
            if user_id:
                admin_user_ids.add(user_id)

    candidates: list[tuple[float, str, str]] = []
    now = time.time()
    for token in data.get("refresh_tokens", []):
        if not isinstance(token, dict):
            continue
        if str(token.get("token_type") or "") != "normal":
            continue
        if admin_user_ids and str(token.get("user_id") or "") not in admin_user_ids:
            continue
        expire_at = token.get("expire_at")
        if isinstance(expire_at, (int, float)) and expire_at < now:
            continue
        token_value = str(token.get("token") or "").strip()
        client_id = str(token.get("client_id") or "").strip()
        if not token_value or not client_id:
            continue
        score = 0.0
        last_used = token.get("last_used_at")
        created = token.get("created_at")
        for raw in (last_used, created):
            if isinstance(raw, str):
                try:
                    score = max(score, time.mktime(time.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")))
                except Exception:
                    continue
        candidates.append((score, token_value, client_id))

    if not candidates:
        return "", ""
    candidates.sort(reverse=True)
    _, token_value, client_id = candidates[0]
    return token_value, client_id


def load_home_assistant_settings() -> HomeAssistantSettings:
    load_dotenv()
    enabled = env_bool("R1LAB_HA_ENABLED", False)
    base_url = str(os.getenv("R1LAB_HA_BASE_URL") or "http://127.0.0.1:8123").rstrip("/")
    access_token = str(os.getenv("R1LAB_HA_ACCESS_TOKEN") or "").strip()
    refresh_token = str(os.getenv("R1LAB_HA_REFRESH_TOKEN") or "").strip()
    client_id = str(os.getenv("R1LAB_HA_CLIENT_ID") or "").strip()
    timeout = env_float("R1LAB_HA_TIMEOUT_SECONDS", 8.0)
    cache_seconds = env_float("R1LAB_HA_CACHE_SECONDS", 15.0)

    if enabled and not access_token and not refresh_token:
        refresh_token, discovered_client_id = discover_refresh_token()
        if discovered_client_id and not client_id:
            client_id = discovered_client_id

    if not client_id:
        client_id = f"{base_url}/"

    if not access_token and not refresh_token:
        enabled = False

    return HomeAssistantSettings(
        enabled=enabled,
        base_url=base_url,
        access_token=access_token,
        refresh_token=refresh_token,
        client_id=client_id,
        timeout=timeout,
        cache_seconds=cache_seconds,
    )


class HomeAssistantClient:
    def __init__(self, settings: HomeAssistantSettings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._access_token = ""
        self._access_token_deadline = 0.0
        self._state_cache: list[dict[str, Any]] = []
        self._state_cache_deadline = 0.0

    def enabled(self) -> bool:
        return self.settings.enabled

    def _exchange_refresh_token(self) -> tuple[str, float]:
        response = requests.post(
            f"{self.settings.base_url}/auth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "client_id": self.settings.client_id,
                "refresh_token": self.settings.refresh_token,
            },
            timeout=self.settings.timeout,
        )
        response.raise_for_status()
        body = response.json()
        access_token = str(body.get("access_token") or "").strip()
        expires_in = float(body.get("expires_in") or 1800)
        if not access_token:
            raise RuntimeError("Home Assistant token exchange returned no access_token")
        # Refresh slightly early to avoid edge timing failures.
        return access_token, time.monotonic() + max(expires_in - 30.0, 60.0)

    def _bearer_token(self) -> str:
        if self.settings.access_token:
            return self.settings.access_token

        with self._lock:
            if self._access_token and time.monotonic() < self._access_token_deadline:
                return self._access_token
            if not self.settings.refresh_token:
                raise RuntimeError("Home Assistant refresh token is not configured")
            token, deadline = self._exchange_refresh_token()
            self._access_token = token
            self._access_token_deadline = deadline
            return token

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> Any:
        response = requests.request(
            method=method,
            url=f"{self.settings.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self._bearer_token()}",
                "Content-Type": "application/json",
            },
            json=data,
            timeout=self.settings.timeout,
        )
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    def states(self) -> list[dict[str, Any]]:
        with self._lock:
            if self._state_cache and time.monotonic() < self._state_cache_deadline:
                return list(self._state_cache)

        body = self._request_json("GET", "/api/states")
        if not isinstance(body, list):
            raise RuntimeError("Home Assistant states response is not a list")

        with self._lock:
            self._state_cache = [item for item in body if isinstance(item, dict)]
            self._state_cache_deadline = time.monotonic() + self.settings.cache_seconds
            return list(self._state_cache)

    def refresh_states(self) -> list[dict[str, Any]]:
        with self._lock:
            self._state_cache = []
            self._state_cache_deadline = 0.0
        return self.states()

    def call_service(self, service_domain: str, service: str, entity_id: str) -> Any:
        result = self._request_json(
            "POST",
            f"/api/services/{service_domain}/{service}",
            data={"entity_id": entity_id},
        )
        with self._lock:
            self._state_cache = []
            self._state_cache_deadline = 0.0
        return result

    def state(self, entity_id: str) -> dict[str, Any]:
        body = self._request_json("GET", f"/api/states/{entity_id}")
        if not isinstance(body, dict):
            raise RuntimeError(f"Home Assistant state response for {entity_id} is not a dict")
        return body


def _entity_aliases(state: dict[str, Any]) -> list[str]:
    entity_id = str(state.get("entity_id") or "")
    domain, _, object_id = entity_id.partition(".")
    attrs = state.get("attributes")
    friendly_name = ""
    if isinstance(attrs, dict):
        friendly_name = str(attrs.get("friendly_name") or "").strip()

    aliases: list[str] = []
    for raw in (friendly_name, object_id.replace("_", ""), object_id.replace("_", " ")):
        normalized = _normalize_text(raw)
        if normalized and normalized not in aliases:
            aliases.append(normalized)

    if friendly_name:
        normalized_name = _normalize_text(friendly_name)
        extra_aliases = _extra_aliases(friendly_name)
        for raw in extra_aliases:
            normalized = _normalize_text(raw)
            if normalized and normalized not in aliases:
                aliases.append(normalized)
        if normalized_name == "投影仪切换hdmi":
            for raw in ("投影仪切到hdmi", "投影仪hdmi", "投影仪切到hdmi1"):
                normalized = _normalize_text(raw)
                if normalized not in aliases:
                    aliases.append(normalized)

    if domain == "media_player" and friendly_name == "投影仪":
        for raw in ("投影仪状态", "投影仪现在状态", "投影仪开着吗"):
            normalized = _normalize_text(raw)
            if normalized not in aliases:
                aliases.append(normalized)

    return aliases


def _extra_aliases(friendly_name: str) -> list[str]:
    result: list[str] = []
    if friendly_name.endswith("音量加"):
        result.extend(
            (
                friendly_name.replace("音量加", "声音大一点"),
                friendly_name.replace("音量加", "加音量"),
                friendly_name.replace("音量加", "音量大一点"),
            )
        )
    if friendly_name.endswith("音量减"):
        result.extend(
            (
                friendly_name.replace("音量减", "声音小一点"),
                friendly_name.replace("音量减", "减音量"),
                friendly_name.replace("音量减", "音量小一点"),
            )
        )
    if friendly_name.endswith("关机"):
        result.extend((friendly_name.replace("关机", "关闭"), friendly_name.replace("关机", "关掉")))
    if friendly_name.endswith("切换HDMI"):
        result.extend((friendly_name.replace("切换HDMI", "切到HDMI"), friendly_name.replace("切换HDMI", "HDMI")))
    return result


def _pick_entity(text: str, states: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    normalized = _normalize_text(text)
    best_state: dict[str, Any] | None = None
    best_alias = ""
    best_score = 0

    for state in states:
        entity_id = str(state.get("entity_id") or "")
        domain = entity_id.split(".", 1)[0]
        if domain not in SUPPORTED_DOMAINS:
            continue
        aliases = _entity_aliases(state)
        if not aliases:
            continue
        for alias in aliases:
            if not alias:
                continue
            if alias in normalized:
                score = len(alias)
            elif normalized == alias:
                score = len(alias)
            else:
                continue
            if score > best_score:
                best_score = score
                best_state = state
                best_alias = alias

    return best_state, best_alias


def _is_state_query(text: str) -> bool:
    normalized = _normalize_text(text)
    return any(word in normalized for word in map(_normalize_text, STATE_WORDS))


def _turn_action(text: str) -> str | None:
    normalized = _normalize_text(text)
    for word in map(_normalize_text, TURN_OFF_WORDS):
        if word in normalized:
            return "turn_off"
    for word in map(_normalize_text, TURN_ON_WORDS):
        if word in normalized:
            return "turn_on"
    return None


def _is_imperative_entity(state: dict[str, Any]) -> bool:
    attrs = state.get("attributes")
    friendly_name = str(attrs.get("friendly_name") or "").strip() if isinstance(attrs, dict) else ""
    normalized = _normalize_text(friendly_name)
    return any(keyword in normalized for keyword in map(_normalize_text, IMPERATIVE_KEYWORDS))


def _service_for_entity(state: dict[str, Any], action: str) -> tuple[str, str] | None:
    entity_id = str(state.get("entity_id") or "")
    domain = entity_id.split(".", 1)[0]

    if action == "activate":
        if domain in {"switch", "light", "fan", "input_boolean", "script"}:
            return domain, "turn_on"
        if domain == "button":
            return "button", "press"
        if domain == "media_player":
            return "media_player", "turn_on"
        return None

    if action == "turn_on":
        if domain in {"switch", "light", "fan", "input_boolean", "script"}:
            return domain, "turn_on"
        if domain == "media_player":
            return "media_player", "turn_on"
        if domain == "cover":
            return "cover", "open_cover"
        return None

    if action == "turn_off":
        if domain in {"switch", "light", "fan", "input_boolean"}:
            return domain, "turn_off"
        if domain == "media_player":
            return "media_player", "turn_off"
        if domain == "cover":
            return "cover", "close_cover"
        return None

    return None


def _format_state_answer(state: dict[str, Any]) -> str:
    entity_id = str(state.get("entity_id") or "")
    domain = entity_id.split(".", 1)[0]
    raw_state = str(state.get("state") or "").strip()
    attrs = state.get("attributes")
    friendly_name = str(attrs.get("friendly_name") or entity_id).strip() if isinstance(attrs, dict) else entity_id

    if domain in {"switch", "light", "fan", "input_boolean"}:
        return f"{friendly_name}现在{'开着' if raw_state == 'on' else '关着'}。"
    if domain == "binary_sensor":
        return f"{friendly_name}现在{'开启' if raw_state == 'on' else '关闭'}。"
    if domain == "media_player":
        raw_state_lower = raw_state.lower()
        mapping = {
            "on": "已开启",
            "off": "已关闭",
            "playing": "正在播放",
            "paused": "已暂停",
            "idle": "空闲",
            "standby": "待机",
            "stopped": "已停止",
        }
        return f"{friendly_name}现在{mapping.get(raw_state_lower, raw_state)}。"
    if domain == "sensor":
        unit = ""
        if isinstance(attrs, dict):
            unit = str(attrs.get("unit_of_measurement") or "").strip()
        unit_mapping = {
            "W": "瓦",
            "kW": "千瓦",
            "V": "伏",
            "A": "安",
            "°C": "度",
            "℃": "度",
            "%": "%",
        }
        spoken_unit = unit_mapping.get(unit, unit)
        suffix = spoken_unit if spoken_unit else ""
        return f"{friendly_name}现在是{raw_state}{suffix}。"
    return f"{friendly_name}现在是{raw_state}。"


def _format_action_answer(state: dict[str, Any], action: str) -> str:
    entity_id = str(state.get("entity_id") or "")
    attrs = state.get("attributes")
    friendly_name = str(attrs.get("friendly_name") or entity_id).strip() if isinstance(attrs, dict) else entity_id
    if action == "turn_on":
        return f"已打开{friendly_name}。"
    if action == "turn_off":
        return f"已关闭{friendly_name}。"
    return f"已执行{friendly_name}。"


def handle_home_assistant(text: str, client: HomeAssistantClient | None = None) -> HomeAssistantResult | None:
    settings = load_home_assistant_settings()
    if not settings.enabled:
        return None

    ha_client = client or HomeAssistantClient(settings)
    if not ha_client.enabled():
        return None

    states = ha_client.states()
    state, matched_alias = _pick_entity(text, states)
    if state is None or not matched_alias:
        return None

    entity_id = str(state.get("entity_id") or "")
    attrs = state.get("attributes")
    friendly_name = str(attrs.get("friendly_name") or entity_id).strip() if isinstance(attrs, dict) else entity_id

    if _is_state_query(text):
        try:
            fresh_state = ha_client.state(entity_id)
        except Exception:
            fresh_state = state
        return HomeAssistantResult(
            handled=True,
            route="home_assistant_state",
            answer=_format_state_answer(fresh_state),
            entity_id=entity_id,
            friendly_name=friendly_name,
            action="query",
        )

    action = _turn_action(text)
    if action is None and _is_imperative_entity(state):
        action = "activate"
    if action is None:
        return None

    service = _service_for_entity(state, action)
    if service is None:
        return None
    service_domain, service_name = service
    ha_client.call_service(service_domain, service_name, entity_id)
    return HomeAssistantResult(
        handled=True,
        route="home_assistant_action",
        answer=_format_action_answer(state, action),
        entity_id=entity_id,
        friendly_name=friendly_name,
        action=action,
    )

from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .config import load_provider_settings, openai_timeout_seconds
from .home_assistant import handle_home_assistant
from .music import MusicSearchResult, handle_music_request
from .openai_compat import OpenAICompatClient
from .stock_intents import match_stock_intent, stock_response_is_actionable


SID_PATTERN = re.compile(r"\[(.*?)\]")
MULTISPACE_PATTERN = re.compile(r"\s+")
FORWARD_HEADERS = {
    "ci",
    "cryp",
    "i",
    "k",
    "p",
    "dt",
    "remote-addr",
    "ui",
    "http-client-ip",
    "t",
    "u",
    "host",
    "connection",
    "content-type",
    "tp",
    "sp",
    "accept-encoding",
    "user-agent",
}
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


@dataclass
class ProxyResponse:
    status: int
    headers: list[tuple[str, str]]
    body: bytes


@dataclass
class AssistantPayload:
    route: str
    answer: str
    payload: dict[str, Any]


class TTLMap:
    def __init__(self, ttl_seconds: int = 60) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, tuple[float, Any]] = {}

    def set(self, key: str, value: Any) -> None:
        self._items[key] = (time.time() + self.ttl_seconds, value)

    def get(self, key: str, default: Any = None) -> Any:
        self.cleanup()
        item = self._items.get(key)
        if not item:
            return default
        expires_at, value = item
        if expires_at < time.time():
            self._items.pop(key, None)
            return default
        return value

    def pop(self, key: str, default: Any = None) -> Any:
        self.cleanup()
        item = self._items.pop(key, None)
        if not item:
            return default
        expires_at, value = item
        if expires_at < time.time():
            return default
        return value

    def cleanup(self) -> None:
        now = time.time()
        expired = [key for key, (expires_at, _) in self._items.items() if expires_at < now]
        for key in expired:
            self._items.pop(key, None)

    def snapshot(self) -> dict[str, Any]:
        self.cleanup()
        return {key: value for key, (_, value) in self._items.items()}


class DebugRing:
    def __init__(self, max_events: int = 120) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=max_events)

    def add(self, event_type: str, **payload: Any) -> None:
        event = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "type": event_type,
        }
        for key, value in payload.items():
            event[key] = summarize_value(value)
        self._items.append(event)

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self._items)


class R1CompatState:
    def __init__(self) -> None:
        self.sid_to_device = TTLMap(ttl_seconds=50)
        self.sid_to_asr = TTLMap(ttl_seconds=50)
        self.device_ip = TTLMap(ttl_seconds=300)
        self.debug = DebugRing(max_events=int(os.getenv("R1LAB_DEBUG_MAX_EVENTS", "120")))

    def record(self, event_type: str, **payload: Any) -> None:
        self.debug.add(event_type, **payload)

    def bind_request(self, headers: dict[str, str], client_ip: str) -> None:
        device_id = headers.get("ui", "").strip()
        if not device_id:
            return
        self.device_ip.set(device_id, client_ip)
        sid_wrapper = headers.get("p", "")
        sid_list: list[str] = []
        for sid in SID_PATTERN.findall(sid_wrapper):
            self.sid_to_device.set(sid, device_id)
            sid_list.append(sid)
        self.record("bind_request", device_id=device_id, client_ip=client_ip, sid_list=sid_list)

    def append_asr(self, sid: str, text: str) -> None:
        current = self.sid_to_asr.get(sid, "")
        self.sid_to_asr.set(sid, f"{current}{text}")
        self.record("append_asr", sid=sid, appended=text, asr_text=f"{current}{text}")

    def snapshot(self) -> dict[str, Any]:
        return {
            "proxy_target": {
                "host": proxy_target()[0],
                "port": proxy_target()[1],
                "host_header": os.getenv("R1LAB_R1_REMOTE_HOST_HEADER", "127.0.0.1:18888"),
            },
            "sid_to_device": self.sid_to_device.snapshot(),
            "sid_to_asr": self.sid_to_asr.snapshot(),
            "device_ip": self.device_ip.snapshot(),
            "events": self.debug.snapshot(),
        }


STATE = R1CompatState()


def proxy_target() -> tuple[str, int]:
    host = os.getenv("R1LAB_R1_REMOTE_HOST", "39.105.252.245").strip()
    port = int(os.getenv("R1LAB_R1_REMOTE_PORT", "80"))
    return host, port


def _normalize_request_headers(raw_headers: list[tuple[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in raw_headers:
        result[name.lower()] = value
    return result


def summarize_value(value: Any, limit: int = 240) -> Any:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    elif isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...({len(text)} chars)"


def stock_passthrough_route(text: str) -> str | None:
    intent = match_stock_intent(text)
    if not intent:
        return None
    return intent.route


def build_forward_headers(raw_headers: list[tuple[str, str]]) -> dict[str, str]:
    request_headers = _normalize_request_headers(raw_headers)
    forwarded: dict[str, str] = {}
    for key, value in request_headers.items():
        if key not in FORWARD_HEADERS:
            continue
        if key == "host":
            forwarded["Host"] = os.getenv("R1LAB_R1_REMOTE_HOST_HEADER", "127.0.0.1:18888")
        else:
            forwarded[key] = value
    return forwarded


def build_generic_forward_headers(raw_headers: list[tuple[str, str]], host_header: str) -> dict[str, str]:
    request_headers = _normalize_request_headers(raw_headers)
    forwarded: dict[str, str] = {}
    for key, value in request_headers.items():
        if key in HOP_BY_HOP_HEADERS:
            continue
        if key == "host":
            forwarded["Host"] = host_header
            continue
        forwarded[key] = value
    forwarded.setdefault("Host", host_header)
    return forwarded


def proxy_to_remote(path: str, body: bytes, raw_headers: list[tuple[str, str]]) -> ProxyResponse:
    host, port = proxy_target()
    connection = http.client.HTTPConnection(host, port, timeout=30)
    try:
        headers = build_forward_headers(raw_headers)
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        return ProxyResponse(
            status=response.status,
            headers=response.getheaders(),
            body=data,
        )
    finally:
        connection.close()


def proxy_to_remote_raw(
    method: str,
    path: str,
    body: bytes,
    raw_headers: list[tuple[str, str]],
) -> ProxyResponse:
    host, port = proxy_target()
    connection = http.client.HTTPConnection(host, port, timeout=30)
    try:
        headers = build_generic_forward_headers(raw_headers, host)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        STATE.record(
            "r1_passthrough",
            method=method,
            path=path,
            status=response.status,
            body_bytes=len(data),
        )
        return ProxyResponse(
            status=response.status,
            headers=response.getheaders(),
            body=data,
        )
    finally:
        connection.close()


def proxy_absolute_request(
    method: str,
    target_url: str,
    body: bytes,
    raw_headers: list[tuple[str, str]],
) -> ProxyResponse:
    parts = urlsplit(target_url)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"absolute URL required, got {target_url}")
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"

    if parts.hostname == "log.hivoice.cn" and path.startswith("/trace/basicService/"):
        STATE.record("noop_trace_service", method=method, target_url=target_url, body_bytes=len(body))
        return ProxyResponse(
            status=200,
            headers=[
                ("Content-Length", "0"),
                ("Content-Type", "text/plain; charset=utf-8"),
            ],
            body=b"",
        )

    if parts.hostname == "aios-home.hivoice.cn" and path.startswith("/rest/v1/api/terminal_syslog"):
        STATE.record("noop_terminal_syslog", method=method, target_url=target_url, body_bytes=len(body))
        body_bytes = b'{"status":0}'
        return ProxyResponse(
            status=200,
            headers=[
                ("Content-Length", str(len(body_bytes))),
                ("Content-Type", "application/json; charset=utf-8"),
            ],
            body=body_bytes,
        )

    port = parts.port or (443 if parts.scheme == "https" else 80)
    if parts.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            parts.hostname,
            port,
            timeout=30,
            context=ssl.create_default_context(),
        )
    else:
        connection = http.client.HTTPConnection(parts.hostname, port, timeout=30)

    try:
        headers = build_generic_forward_headers(raw_headers, parts.netloc)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        STATE.record(
            "absolute_proxy",
            method=method,
            target_url=target_url,
            status=response.status,
            body_bytes=len(data),
        )
        return ProxyResponse(
            status=response.status,
            headers=response.getheaders(),
            body=data,
        )
    finally:
        connection.close()


def build_speaker_prompt(text: str) -> str:
    return (
        "你是智能音箱语音助手。"
        "请直接给最终答案，不要展示思考过程，不要慢慢分析。"
        "不要使用Markdown、编号、项目符号、星号、括号列表或代码块。"
        "优先只回答1句，最多2句，尽量不超过50个汉字。"
        "不要一次给出多个猜测方向，不要做长篇澄清。"
        "如果问题不清楚，只回答：我没听清，请再说一遍。"
        f"\n用户问题：{text}"
    )


def normalize_tts_answer(text: str) -> str:
    cleaned = text.replace("**", " ")
    cleaned = cleaned.replace("###", " ")
    cleaned = cleaned.replace("##", " ")
    cleaned = cleaned.replace("#", " ")
    cleaned = cleaned.replace("`", " ")
    cleaned = cleaned.replace("\r", " ")
    cleaned = cleaned.replace("\n", " ")
    cleaned = cleaned.replace("•", " ")
    cleaned = cleaned.replace("·", " ")
    cleaned = re.sub(r"\s*[-*]\s*", "；", cleaned)
    cleaned = MULTISPACE_PATTERN.sub(" ", cleaned).strip(" ；")
    max_chars = int(os.getenv("R1LAB_TTS_MAX_CHARS", "72"))
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 1].rstrip("，,、；;。.!?？")
        cleaned += "。"
    return cleaned


def fallback_tts_answer() -> str:
    return normalize_tts_answer(
        os.getenv("R1LAB_ERROR_FALLBACK_TEXT", "我现在暂时无法回答，请稍后再试。")
    )


def llm_answer(text: str) -> str:
    settings = load_provider_settings()
    client = OpenAICompatClient(settings=settings, timeout=openai_timeout_seconds())
    STATE.record("llm_request", text=text, model=settings.model, base_url=settings.base_url)
    status, result = client.generate_text(build_speaker_prompt(text))
    if status >= 400:
        STATE.record("llm_error", status=status, raw=result.get("raw", {}))
        raise RuntimeError(f"LLM request failed with status {status}")
    answer = normalize_tts_answer(result["text"].strip())
    STATE.record("llm_response", status=status, answer=answer, tried=result.get("tried", []))
    return answer


def assistant_answer(text: str) -> str:
    try:
        ha_result = handle_home_assistant(text)
    except Exception as exc:
        STATE.record("home_assistant_error", text=text, error=str(exc))
        ha_result = None

    if ha_result and ha_result.handled:
        STATE.record(
            "home_assistant_handled",
            text=text,
            route=ha_result.route,
            entity_id=ha_result.entity_id,
            friendly_name=ha_result.friendly_name,
            action=ha_result.action,
            answer=ha_result.answer,
        )
        return normalize_tts_answer(ha_result.answer)

    return llm_answer(text)


def build_chat_response(answer: str, asr_text: str, source: dict[str, Any] | None = None) -> dict[str, Any]:
    resource_id = "904757"
    audio_url = "http://asrv3.hivoice.cn/trafficRouter/r/TRdECS"
    nlu_process_time = "717"
    response_id = uuid.uuid4().hex

    if isinstance(source, dict):
        response_id = str(source.get("responseId") or response_id)
        nlu_process_time = str(source.get("nluProcessTime") or nlu_process_time)
        audio_url = str(source.get("audioUrl") or audio_url)
        general = source.get("general")
        if isinstance(general, dict) and general.get("resourceId"):
            resource_id = str(general["resourceId"])

    payload: dict[str, Any] = {
        "code": "ANSWER",
        "matchType": "NOT_UNDERSTAND",
        "confidence": 0.8,
        "history": "cn.yunzhisheng.chat",
        "source": "nlu",
        "asr_recongize": asr_text or "OK",
        "rc": 0,
        "general": {
            "style": "CQA_common_customized",
            "text": answer,
            "type": "T",
            "resourceId": resource_id,
        },
        "returnCode": 0,
        "audioUrl": audio_url,
        "retTag": "nlu",
        "service": "cn.yunzhisheng.chat",
        "nluProcessTime": nlu_process_time,
        "text": "OK",
        "responseId": response_id,
    }
    return payload


def build_music_response(
    result: MusicSearchResult,
    asr_text: str,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    audio_url = "http://asrv3.hivoice.cn/trafficRouter/r/TRdECS"
    nlu_process_time = "183"
    response_id = uuid.uuid4().hex

    if isinstance(source, dict):
        response_id = str(source.get("responseId") or response_id)
        nlu_process_time = str(source.get("nluProcessTime") or nlu_process_time)
        audio_url = str(source.get("audioUrl") or audio_url)

    return {
        "semantic": {
            "intent": {
                "operations": [
                    {"operator": "ACT_PLAY"},
                ]
            }
        },
        "code": "SETTING_EXEC",
        "matchType": "FUZZY",
        "originIntent": {"nluSlotInfos": []},
        "confidence": 0.8,
        "modelIntentClsScore": {},
        "history": "cn.yunzhisheng.music",
        "source": "nlu",
        "uniCarRet": {
            "result": {},
            "returnCode": 609,
            "message": "http post reuqest error",
        },
        "asr_recongize": asr_text,
        "rc": 0,
        "data": result.result_payload(),
        "returnCode": 0,
        "audioUrl": audio_url,
        "retTag": "nlu",
        "service": "cn.yunzhisheng.music",
        "nluProcessTime": nlu_process_time,
        "text": asr_text or result.keyword or "OK",
        "responseId": response_id,
        "general": {
            "text": result.answer or "好的，已为您播放",
            "type": "T",
        },
    }


def direct_r1_chat(text: str, serial: str) -> dict[str, Any]:
    try:
        assisted = assistant_payload(text=text, asr_text=text, source={"responseId": f"r1lab-{serial}"})
    except Exception as exc:
        answer = fallback_tts_answer()
        STATE.record("llm_fallback", text=text, answer=answer, error=str(exc))
        fallback_payload = build_chat_response(
            answer=answer,
            asr_text=text,
            source={"responseId": f"r1lab-{serial}"},
        )
        return fallback_payload
    STATE.record(
        "direct_chat",
        serial=serial,
        text=text,
        answer=assisted.answer,
        route=assisted.route,
    )
    return assisted.payload


def assistant_payload(
    text: str,
    asr_text: str,
    source: dict[str, Any] | None = None,
) -> AssistantPayload:
    try:
        ha_result = handle_home_assistant(text)
    except Exception as exc:
        STATE.record("home_assistant_error", text=text, error=str(exc))
        ha_result = None

    if ha_result and ha_result.handled:
        answer = normalize_tts_answer(ha_result.answer)
        STATE.record(
            "home_assistant_handled",
            text=text,
            route=ha_result.route,
            entity_id=ha_result.entity_id,
            friendly_name=ha_result.friendly_name,
            action=ha_result.action,
            answer=answer,
        )
        return AssistantPayload(
            route=f"home_assistant:{ha_result.route}",
            answer=answer,
            payload=build_chat_response(answer=answer, asr_text=asr_text, source=source),
        )

    music_result = handle_music_request(text)
    if music_result.handled:
        STATE.record(
            "music_handled",
            text=text,
            route=music_result.route,
            keyword=music_result.keyword,
            answer=music_result.answer,
            tracks=[track.public_dict() for track in music_result.tracks],
            error=music_result.error,
        )
        if music_result.tracks:
            return AssistantPayload(
                route=f"music:{music_result.route}",
                answer=music_result.answer,
                payload=build_music_response(result=music_result, asr_text=asr_text, source=source),
            )
        answer = normalize_tts_answer(music_result.answer or "音乐接口暂时不可用。")
        return AssistantPayload(
            route=f"music:{music_result.route}",
            answer=answer,
            payload=build_chat_response(answer=answer, asr_text=asr_text, source=source),
        )

    answer = llm_answer(text)
    return AssistantPayload(
        route="llm",
        answer=answer,
        payload=build_chat_response(answer=answer, asr_text=asr_text, source=source),
    )


def handle_r1_proxy_request(
    path: str,
    body: bytes,
    raw_headers: list[tuple[str, str]],
    client_ip: str,
) -> ProxyResponse:
    headers = _normalize_request_headers(raw_headers)
    STATE.bind_request(headers, client_ip)
    STATE.record("proxy_in", path=path, client_ip=client_ip, body_bytes=len(body), headers=headers)
    remote = proxy_to_remote(path=path, body=body, raw_headers=raw_headers)

    sid = ""
    for header_name, header_value in remote.headers:
        if header_name.lower() == "sid":
            sid = header_value
            break

    try:
        payload = json.loads(remote.body.decode("utf-8"))
    except Exception:
        STATE.record("proxy_passthrough_binary", path=path, status=remote.status, body_bytes=len(remote.body))
        return remote

    if isinstance(payload, dict) and payload.get("asr_recongize"):
        if sid:
            STATE.append_asr(sid, str(payload["asr_recongize"]))

    if not isinstance(payload, dict) or "responseId" not in payload:
        STATE.record(
            "proxy_passthrough_json",
            path=path,
            status=remote.status,
            sid=sid,
            payload=payload,
        )
        return remote

    device_id = STATE.sid_to_device.get(sid, headers.get("ui", "unknown-device"))
    asr_text = STATE.sid_to_asr.pop(sid, "") or str(payload.get("asr_recongize") or "").strip()
    if not asr_text:
        STATE.record("proxy_no_asr", sid=sid, device_id=device_id, payload=payload)
        return remote

    stock_intent = match_stock_intent(asr_text)
    if stock_intent:
        actionable = stock_response_is_actionable(stock_intent, payload)
        if actionable or not stock_intent.allow_llm_fallback:
            STATE.record(
                "proxy_stock_passthrough",
                sid=sid,
                device_id=device_id,
                asr_text=asr_text,
                route=stock_intent.route,
                category=stock_intent.category,
                normalized_text=stock_intent.normalized_text,
                actionable=actionable,
                allow_llm_fallback=stock_intent.allow_llm_fallback,
                response_id=payload.get("responseId"),
                stock_general=((payload.get("general") or {}).get("text") if isinstance(payload, dict) else ""),
            )
            return remote
        STATE.record(
            "proxy_stock_fallback",
            sid=sid,
            device_id=device_id,
            asr_text=asr_text,
            route=stock_intent.route,
            category=stock_intent.category,
            normalized_text=stock_intent.normalized_text,
            response_id=payload.get("responseId"),
            stock_general=((payload.get("general") or {}).get("text") if isinstance(payload, dict) else ""),
        )

    try:
        assisted = assistant_payload(text=asr_text, asr_text=asr_text, source=payload)
    except Exception as exc:
        answer = fallback_tts_answer()
        STATE.record("llm_fallback", sid=sid, device_id=device_id, asr_text=asr_text, answer=answer, error=str(exc))
        assisted = AssistantPayload(
            route="fallback",
            answer=answer,
            payload=build_chat_response(answer=answer, asr_text=asr_text, source=payload),
        )
    body_bytes = json.dumps(assisted.payload, ensure_ascii=False).encode("utf-8")

    response_headers: list[tuple[str, str]] = []
    for header_name, header_value in remote.headers:
        if header_name.lower() == "content-length":
            continue
        response_headers.append((header_name, header_value))
    response_headers.append(("Content-Length", str(len(body_bytes))))
    response_headers.append(("X-R1Lab-Device", device_id))
    STATE.record(
        "proxy_replace_answer",
        sid=sid,
        device_id=device_id,
        asr_text=asr_text,
        answer=assisted.answer,
        route=assisted.route,
        response_id=payload.get("responseId"),
    )
    return ProxyResponse(status=remote.status, headers=response_headers, body=body_bytes)


def debug_snapshot() -> dict[str, Any]:
    return STATE.snapshot()


def record_debug_event(event_type: str, **payload: Any) -> None:
    STATE.record(event_type, **payload)

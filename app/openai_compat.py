from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import requests

from .config import ProviderSettings


def extract_output_text(body: Any) -> str:
    if isinstance(body, dict):
        output_text = body.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        output = body.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for chunk in content:
                    if isinstance(chunk, dict) and chunk.get("type") == "output_text":
                        text = chunk.get("text")
                        if isinstance(text, str):
                            parts.append(text)
            if parts:
                return "\n".join(parts).strip()

        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content.strip()
                    if isinstance(content, list):
                        parts: list[str] = []
                        for part in content:
                            if isinstance(part, dict):
                                text = part.get("text")
                                if isinstance(text, str) and text.strip():
                                    parts.append(text)
                        if parts:
                            return "\n".join(parts).strip()
    return ""


@dataclass
class OpenAICompatClient:
    settings: ProviderSettings
    timeout: float = 120.0

    def _headers(self, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "User-Agent": "curl/8.7.1 r1-lab/0.1",
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        url = f"{self.settings.base_url}{path}"
        headers = self._headers(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                **(extra_headers or {}),
            }
        )
        attempts = 3
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
                raw = response.content.decode("utf-8", errors="replace")
                try:
                    body = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    body = {"raw": raw}
                return response.status_code, body
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                time.sleep(0.5 * attempt)

        return 599, {"raw": str(last_error) if last_error else "request_failed"}

    def list_models(self) -> tuple[int, Any]:
        return self._request("GET", "/models")

    def _request_sse(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        url = f"{self.settings.base_url}{path}"
        headers = self._headers(
            {
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
            }
        )
        attempts = 3
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            text_parts: list[str] = []
            events: list[dict[str, Any]] = []
            try:
                with requests.post(
                    url=url,
                    headers=headers,
                    json=payload,
                    stream=True,
                    timeout=self.timeout,
                ) as response:
                    status = response.status_code
                    if status >= 400:
                        raw = response.content.decode("utf-8", errors="replace")
                        try:
                            parsed = json.loads(raw) if raw else {"raw": raw}
                        except json.JSONDecodeError:
                            parsed = {"raw": raw}
                        return status, {"text": "", "events": [], "error": parsed}

                    for raw_line in response.iter_lines(decode_unicode=False):
                        if not raw_line:
                            continue
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line or not line.startswith("data: "):
                            continue
                        payload_str = line[6:]
                        if payload_str == "[DONE]":
                            events.append({"type": "done"})
                            break
                        try:
                            item = json.loads(payload_str)
                        except json.JSONDecodeError:
                            events.append({"type": "raw", "data": payload_str})
                            continue
                        events.append(item)

                        event_type = item.get("type")
                        if event_type == "response.output_text.delta":
                            delta = item.get("delta")
                            if isinstance(delta, str):
                                text_parts.append(delta)
                        elif item.get("object") == "chat.completion.chunk":
                            choices = item.get("choices")
                            if isinstance(choices, list):
                                for choice in choices:
                                    if not isinstance(choice, dict):
                                        continue
                                    delta = choice.get("delta")
                                    if isinstance(delta, dict):
                                        content = delta.get("content")
                                        if isinstance(content, str):
                                            text_parts.append(content)

                    return status, {
                        "text": "".join(text_parts).strip(),
                        "events": events,
                    }
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                time.sleep(0.5 * attempt)

        return 599, {"text": "", "events": [], "error": {"raw": str(last_error) if last_error else "request_failed"}}

    def _generate_via_chat(self, text: str, model_name: str) -> tuple[int, Any]:
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": text}],
        }
        if self.settings.prefer_sse:
            payload["stream"] = True
            status, sse = self._request_sse("/chat/completions", payload)
            return status, {"output_text": sse.get("text", ""), "sse": sse}
        return self._request("POST", "/chat/completions", payload)

    def _generate_via_responses(self, text: str, model_name: str) -> tuple[int, Any]:
        payload = {
            "model": model_name,
            "input": text,
            "store": False,
        }
        if self.settings.prefer_sse:
            payload["stream"] = True
            status, sse = self._request_sse("/responses", payload)
            return status, {"output_text": sse.get("text", ""), "sse": sse}
        return self._request("POST", "/responses", payload)

    def generate_text(self, text: str, model: str | None = None) -> tuple[int, dict[str, Any]]:
        model_name = model or self.settings.model
        wire_api = self.settings.wire_api
        tried: list[str] = []

        if wire_api == "chat":
            status, body = self._generate_via_chat(text, model_name)
            tried.append("chat")
            extracted = extract_output_text(body)
            if status < 400 and not extracted:
                fallback_status, fallback_body = self._generate_via_responses(text, model_name)
                tried.append("responses")
                if extract_output_text(fallback_body):
                    status, body = fallback_status, fallback_body
        else:
            status, body = self._generate_via_responses(text, model_name)
            tried.append("responses")
            extracted = extract_output_text(body)
            if status < 400 and not extracted:
                fallback_status, fallback_body = self._generate_via_chat(text, model_name)
                tried.append("chat")
                if extract_output_text(fallback_body):
                    status, body = fallback_status, fallback_body

        result = {
            "status": status,
            "model": model_name,
            "text": extract_output_text(body),
            "tried": tried,
            "raw": body,
        }
        return status, result

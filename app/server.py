from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

from .config import PROJECT_ROOT, load_dotenv, load_provider_settings, openai_timeout_seconds
from .music import (
    handle_music_request,
    load_music_cookie,
    login_status,
    music_enabled,
    open_track_stream,
    save_music_cookie,
)
from .openai_compat import OpenAICompatClient
from .r1_compat import (
    debug_snapshot,
    direct_r1_chat,
    handle_r1_proxy_request,
    proxy_absolute_request,
    record_debug_event,
)
from .stock_intents import match_stock_intent


LOG_PATH = PROJECT_ROOT / "logs" / "server.log"


def append_log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat()} {line}\n")


class LabHandler(BaseHTTPRequestHandler):
    server_version = "r1-lab/0.2"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        append_log(fmt % args)

    def handle_expect_100(self) -> bool:
        append_log(f"expect-100 target={self.path}")
        self.send_response_only(100)
        self.end_headers()
        return True

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"ok": True, "service": "r1_lab"})
            return
        if parsed.path == "/api/provider":
            settings = load_provider_settings()
            self._json(200, settings.public_dict())
            return
        if parsed.path == "/api/debug/r1":
            self._json(200, debug_snapshot())
            return
        if parsed.path == "/api/debug/stock-intent":
            self._handle_stock_intent_debug(parsed.query)
            return
        if parsed.path == "/api/music/search":
            self._handle_music_search(parsed.query)
            return
        if parsed.path == "/api/music/login-status":
            self._handle_music_login_status()
            return
        if parsed.path == "/api/music/save-cookie":
            self._handle_music_save_cookie(parsed.query)
            return
        if parsed.path == "/r1/ai/chat":
            self._handle_r1_ai_chat(parsed.query)
            return
        if parsed.path.startswith("/music/netease/"):
            self._handle_music_proxy(head_only=False)
            return
        if parsed.path.startswith("/trafficRouter/"):
            self._handle_r1_passthrough(self._path_with_query(parsed))
            return
        if parsed.path.startswith("/trace/basicService/"):
            record_debug_event(
                "stock_trace_service",
                target=self.path,
                client_ip=self.client_address[0],
                headers=dict(self.headers.items()),
            )
            self._plain(200, b"")
            return
        if parsed.scheme and parsed.netloc:
            self._handle_absolute_proxy(method="GET", body=b"")
            return
        self._json(404, {"error": "not_found"})

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/music/netease/"):
            self._handle_music_proxy(head_only=True)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/music/save-cookie":
            self._handle_music_save_cookie_post()
            return
        if parsed.path == "/api/chat":
            self._handle_chat()
            return
        if parsed.path == "/getUserInfo":
            body = self._read_body_bytes()
            if body is None:
                self._json(400, {"error": "invalid_content_length"})
                return
            record_debug_event(
                "stock_get_user_info",
                target=self.path,
                client_ip=self.client_address[0],
                headers=dict(self.headers.items()),
                body_bytes=len(body),
            )
            self._json(200, {"status": 0})
            return
        if parsed.path == "/trafficRouter/cs":
            self._handle_r1_proxy(self._path_with_query(parsed))
            return
        if parsed.path.startswith("/trafficRouter/"):
            body = self._read_body_bytes()
            if body is None:
                self._json(400, {"error": "invalid_content_length"})
                return
            self._handle_r1_passthrough(self._path_with_query(parsed), body=body)
            return
        if parsed.path.startswith("/trace/basicService/"):
            body = self._read_body_bytes()
            if body is None:
                self._json(400, {"error": "invalid_content_length"})
                return
            record_debug_event(
                "stock_trace_service",
                target=self.path,
                client_ip=self.client_address[0],
                headers=dict(self.headers.items()),
                body_bytes=len(body),
            )
            self._plain(200, b"")
            return
        if parsed.path == "/rest/v1/api/terminal_syslog":
            body = self._read_body_bytes()
            if body is None:
                self._json(400, {"error": "invalid_content_length"})
                return
            record_debug_event(
                "stock_terminal_syslog",
                target=self.path,
                client_ip=self.client_address[0],
                headers=dict(self.headers.items()),
                body_bytes=len(body),
            )
            self._json(200, {"status": 0})
            return
        if parsed.scheme and parsed.netloc:
            self._handle_absolute_proxy(method="POST", body=self._read_body_bytes())
            return
        self._json(404, {"error": "not_found"})

    def do_CONNECT(self) -> None:  # noqa: N802
        append_log(f"CONNECT not supported for target={self.path}")
        record_debug_event("connect_unsupported", target=self.path, client_ip=self.client_address[0])
        self.send_response(501, "CONNECT not supported")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_chat(self) -> None:
        body = self._read_json()
        if body is None:
            self._json(400, {"error": "invalid_json"})
            return

        text = str(body.get("text") or "").strip()
        model = str(body.get("model") or "").strip() or None
        if not text:
            self._json(400, {"error": "missing_text"})
            return

        settings = load_provider_settings()
        client = OpenAICompatClient(settings=settings, timeout=openai_timeout_seconds())
        status, result = client.generate_text(text=text, model=model)
        payload = {
            "ok": status < 400,
            "provider": settings.public_dict(),
            "reply": result["text"],
            "status": status,
            "raw": result["raw"],
        }
        self._json(status if status >= 400 else 200, payload)

    def _handle_r1_ai_chat(self, query_string: str) -> None:
        params = parse_qs(query_string, keep_blank_values=True)
        text = (params.get("text") or [""])[0].strip()
        serial = self.headers.get("r1-serial", "unknown-device").strip()
        if not text:
            self._json(400, {"error": "missing_text"})
            return
        payload = direct_r1_chat(text=text, serial=serial)
        self._json(200, payload)

    def _handle_music_search(self, query_string: str) -> None:
        params = parse_qs(query_string, keep_blank_values=True)
        text = (params.get("q") or [""])[0].strip()
        if not text:
            self._json(400, {"error": "missing_q"})
            return
        result = handle_music_request(text)
        self._json(
            200,
            {
                "ok": bool(result.tracks),
                "music_enabled": "yes" if music_enabled() else "no",
                "result": result.public_dict(),
            },
        )

    def _handle_stock_intent_debug(self, query_string: str) -> None:
        params = parse_qs(query_string, keep_blank_values=True)
        text = (params.get("q") or [""])[0].strip()
        if not text:
            self._json(400, {"error": "missing_q"})
            return
        matched = match_stock_intent(text)
        self._json(
            200,
            {
                "ok": bool(matched),
                "query": text,
                "result": matched.public_dict() if matched else None,
            },
        )

    def _handle_music_login_status(self) -> None:
        status, payload = login_status()
        self._json(
            200 if status < 400 else status,
            {
                "ok": status < 400,
                "music_enabled": "yes" if music_enabled() else "no",
                "cookie_present": "yes" if bool(load_music_cookie()) else "no",
                "status": status,
                "result": payload,
            },
        )

    def _handle_music_save_cookie(self, query_string: str) -> None:
        params = parse_qs(query_string, keep_blank_values=True)
        cookie = (params.get("cookie") or [""])[0].strip()
        if not cookie:
            self._json(400, {"error": "missing_cookie"})
            return
        save_music_cookie(cookie)
        append_log(f"music cookie saved by get len={len(cookie)}")
        self._json(
            200,
            {
                "ok": True,
                "cookie_present": "yes",
                "message": "music_cookie_saved",
            },
        )

    def _handle_music_save_cookie_post(self) -> None:
        body = self._read_body_bytes()
        if body is None:
            self._json(400, {"error": "invalid_content_length"})
            return

        cookie = ""
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type == "application/json":
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                cookie = str(payload.get("cookie") or "").strip()
        else:
            try:
                form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            except Exception:
                form = {}
            cookie = (form.get("cookie") or [""])[0].strip()
            if not cookie:
                cookie = body.decode("utf-8", errors="ignore").strip()

        if not cookie:
            self._json(400, {"error": "missing_cookie"})
            return

        save_music_cookie(cookie)
        append_log(f"music cookie saved by post len={len(cookie)}")
        self._json(
            200,
            {
                "ok": True,
                "cookie_present": "yes",
                "message": "music_cookie_saved",
            },
        )

    def _handle_music_proxy(self, head_only: bool) -> None:
        parsed = urlparse(self.path)
        filename = parsed.path.rsplit("/", 1)[-1]
        song_part = filename.rsplit(".", 1)[0]
        try:
            song_id = int(song_part)
        except ValueError:
            self._json(400, {"error": "invalid_song_id"})
            return

        range_header = self.headers.get("Range")
        try:
            upstream = open_track_stream(song_id, range_header=range_header)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else 502
            append_log(f"music proxy http error song_id={song_id} status={status_code} error={exc}")
            self._json(status_code, {"error": "music_upstream_http_error", "song_id": song_id})
            return
        except Exception as exc:
            append_log(f"music proxy error song_id={song_id} error={exc}")
            self._json(502, {"error": "music_proxy_error", "song_id": song_id, "message": str(exc)})
            return

        self.send_response(upstream.status_code)
        content_type = upstream.headers.get("Content-Type") or "audio/mpeg"
        self.send_header("Content-Type", content_type)
        for header_name in ("Content-Length", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified"):
            header_value = upstream.headers.get(header_name)
            if header_value:
                self.send_header(header_name, header_value)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if head_only:
            upstream.close()
            return

        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                self.wfile.write(chunk)
        finally:
            upstream.close()

    def _handle_r1_proxy(self, request_path: str) -> None:
        body = self._read_body_bytes()
        append_log(
            f"r1_proxy_request path={request_path} len={-1 if body is None else len(body)} "
            f"headers={dict(self.headers.items())}"
        )
        if body is None:
            self._json(400, {"error": "invalid_content_length"})
            return
        try:
            proxied = handle_r1_proxy_request(
                path=request_path,
                body=body,
                raw_headers=list(self.headers.items()),
                client_ip=self.client_address[0],
            )
        except Exception as exc:
            append_log(f"r1 proxy error: {exc}")
            self._json(502, {"error": "proxy_error", "message": str(exc)})
            return
        self._send_proxy_response(proxied)

    def _handle_absolute_proxy(self, method: str, body: bytes | None) -> None:
        if body is None:
            self._json(400, {"error": "invalid_content_length"})
            return
        try:
            proxied = proxy_absolute_request(
                method=method,
                target_url=self.path,
                body=body,
                raw_headers=list(self.headers.items()),
            )
        except Exception as exc:
            append_log(f"absolute proxy error: {exc}")
            self._json(502, {"error": "proxy_error", "message": str(exc)})
            return
        self._send_proxy_response(proxied)

    def _handle_r1_passthrough(self, request_path: str, body: bytes | None = None) -> None:
        try:
            from .r1_compat import proxy_to_remote_raw

            proxied = proxy_to_remote_raw(
                method=self.command,
                path=request_path,
                body=body or b"",
                raw_headers=list(self.headers.items()),
            )
        except Exception as exc:
            append_log(f"r1 passthrough error: {exc}")
            self._json(502, {"error": "proxy_error", "message": str(exc)})
            return
        self._send_proxy_response(proxied)

    def _send_proxy_response(self, proxied: object) -> None:
        if not hasattr(proxied, "status") or not hasattr(proxied, "headers") or not hasattr(proxied, "body"):
            self._json(500, {"error": "invalid_proxy_response"})
            return

        self.send_response(proxied.status)
        for header_name, header_value in proxied.headers:
            if header_name.lower() == "transfer-encoding":
                continue
            self.send_header(header_name, header_value)
        self.end_headers()
        self.wfile.write(proxied.body)

    def _read_body_bytes(self) -> bytes | None:
        transfer_encoding = (self.headers.get("Transfer-Encoding") or "").strip().lower()
        if "chunked" in transfer_encoding:
            chunks: list[bytes] = []
            while True:
                line = self.rfile.readline()
                if not line:
                    return None
                chunk_size_raw = line.strip().split(b";", 1)[0]
                try:
                    chunk_size = int(chunk_size_raw, 16)
                except ValueError:
                    return None
                if chunk_size == 0:
                    while True:
                        trailer = self.rfile.readline()
                        if trailer in {b"", b"\r\n", b"\n"}:
                            break
                    break
                chunk = self.rfile.read(chunk_size)
                if len(chunk) != chunk_size:
                    return None
                chunks.append(chunk)
                line_end = self.rfile.read(2)
                if line_end != b"\r\n":
                    return None
            return b"".join(chunks)

        length = self.headers.get("Content-Length")
        if not length:
            original_timeout = self.connection.gettimeout()
            chunks: list[bytes] = []
            started_at = time.time()
            last_data_at = started_at
            max_wait = float(os.getenv("R1LAB_STREAM_WAIT_SECONDS", "4.0"))
            idle_wait = float(os.getenv("R1LAB_STREAM_IDLE_SECONDS", "0.6"))
            try:
                self.connection.settimeout(0.5)
                while True:
                    now = time.time()
                    if now - started_at > max_wait:
                        break
                    if chunks and now - last_data_at > idle_wait:
                        break
                    try:
                        chunk = self.rfile.read1(65536)
                    except OSError:
                        continue
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    last_data_at = time.time()
            finally:
                self.connection.settimeout(original_timeout)
            return b"".join(chunks)

        length = length or "0"
        try:
            size = int(length)
        except ValueError:
            return None
        return self.rfile.read(size) if size > 0 else b""

    def _read_json(self) -> dict | None:
        raw = self._read_body_bytes()
        if raw is None:
            return None
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _plain(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _path_with_query(parsed: object) -> str:
        path = getattr(parsed, "path", "") or "/"
        query = getattr(parsed, "query", "")
        if query:
            return f"{path}?{query}"
        return path


def _parse_ports() -> list[int]:
    ports_env = os.getenv("R1LAB_PORTS", "").strip()
    if ports_env:
        ports: list[int] = []
        for raw in ports_env.split(","):
            raw = raw.strip()
            if not raw:
                continue
            ports.append(int(raw))
        if ports:
            return ports
    return [int(os.getenv("R1LAB_PORT", "18888"))]


def run_server() -> None:
    load_dotenv()
    host = os.getenv("R1LAB_HOST", "0.0.0.0")
    ports = _parse_ports()
    servers: list[ThreadingHTTPServer] = []
    threads: list[threading.Thread] = []

    for port in ports:
        append_log(f"starting host={host} port={port}")
        server = ThreadingHTTPServer((host, port), LabHandler)
        thread = threading.Thread(target=server.serve_forever, name=f"r1-lab-{port}", daemon=True)
        thread.start()
        servers.append(server)
        threads.append(thread)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        append_log("stopped by keyboard interrupt")
    finally:
        for server in servers:
            server.shutdown()
        for server in servers:
            server.server_close()

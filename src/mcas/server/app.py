from __future__ import annotations

import json
import os
import signal
import threading
import time
import urllib.parse
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from mcas.config import get_ui_json_path
from mcas.constants import APP_NAME, MAX_REQUEST_BODY_BYTES, VERSION
from mcas.errors import SyncError
from mcas.server.routes import GET_ROUTES, POST_ROUTES, ServerContext
from mcas.server.static_files import (
    get_default_ui_html_path,
    get_mime_type,
    get_static_dir,
    render_index_html,
    resolve_static_asset,
)
from mcas.sync.autosync import run_autosync_cycle
from mcas.sync.state import get_autosync_dict
from mcas.util.fsio import _atomic_write_json


class _McasHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


def _build_handler(
    *,
    ctx: ServerContext,
    ui_html_path: Path,
    static_dir: Path,
) -> type[BaseHTTPRequestHandler]:
    allowed_hosts = {f"127.0.0.1:{ctx.port}", f"localhost:{ctx.port}"}

    class McasRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_bytes(self, status_code: int, content_type: str, body: bytes) -> None:
            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Connection", "close")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _send_json(self, status_code: int, obj: Any) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send_bytes(status_code, "application/json; charset=utf-8", body)

        def _check_host(self) -> bool:
            host = (self.headers.get("Host") or "").strip().lower()
            if host not in allowed_hosts:
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
                return False
            return True

        def _check_api_token(self) -> bool:
            req_token = (self.headers.get("X-Mcas-Token") or "").strip()
            if req_token != ctx.token:
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "bad token"})
                return False
            return True

        def _read_json_body(self) -> dict[str, Any]:
            raw_len = self.headers.get("Content-Length", "0").strip()
            try:
                length = int(raw_len)
            except ValueError as exc:
                raise SyncError("Invalid Content-Length.") from exc
            if length < 0 or length > MAX_REQUEST_BODY_BYTES:
                raise SyncError("Request body size out of bounds.")
            if length == 0:
                return {}
            raw_bytes = self.rfile.read(length)
            if not raw_bytes:
                return {}
            try:
                parsed = json.loads(raw_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SyncError("Invalid JSON request body.") from exc
            if not isinstance(parsed, dict):
                raise SyncError("JSON request body must be an object.")
            return parsed

        def do_GET(self) -> None:
            if not self._check_host():
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path

            if path == "/":
                status_code, ctype, body = render_index_html(ui_html_path, ctx.token)
                self._send_bytes(status_code, ctype, body)
                return

            if path.startswith("/static/"):
                asset = resolve_static_asset(static_dir, path[len("/static/"):])
                if asset is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                self._send_bytes(HTTPStatus.OK, get_mime_type(asset), asset.read_bytes())
                return

            if path == "/health":
                self._send_json(HTTPStatus.OK, {"app": APP_NAME, "version": VERSION})
                return

            if not path.startswith("/api/"):
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return

            if not self._check_api_token():
                return

            action = path[5:]
            handler = GET_ROUTES.get(action)
            if handler is None:
                self._send_json(
                    HTTPStatus.NOT_FOUND, {"error": f"Unknown API endpoint '{action}'"}
                )
                return

            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            try:
                status_code, payload = handler(ctx, qs)
                self._send_json(status_code, payload)
            except SyncError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

        def do_POST(self) -> None:
            if not self._check_host():
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path

            if not path.startswith("/api/"):
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if not self._check_api_token():
                return

            action = path[5:]
            handler = POST_ROUTES.get(action)
            if handler is None:
                self._send_json(
                    HTTPStatus.NOT_FOUND, {"error": f"Unknown API endpoint '{action}'"}
                )
                return

            try:
                body = self._read_json_body()
                status_code, payload = handler(ctx, body)
                self._send_json(status_code, payload)
            except SyncError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

        def do_OPTIONS(self) -> None:
            self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Method not allowed"})

        def do_PUT(self) -> None:
            self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Method not allowed"})

        def do_DELETE(self) -> None:
            self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Method not allowed"})

    return McasRequestHandler


def serve_http(
    port: int,
    ui_html_path: Path | None = None,
    static_dir: Path | None = None,
) -> int:
    if port < 1024 or port > 65535:
        raise SyncError(f"Invalid port '{port}' (use 1024-65535).")

    if ui_html_path is not None and not ui_html_path.is_file():
        raise SyncError(f"ui.html not found: {ui_html_path}")

    resolved_static_dir = static_dir or get_static_dir()
    resolved_ui_html = ui_html_path or get_default_ui_html_path()

    token = uuid.uuid4().hex
    stop_event = threading.Event()
    wake_event = threading.Event()
    server_ref: list[ThreadingHTTPServer] = []
    ctx = ServerContext(
        port=port,
        token=token,
        stop_event=stop_event,
        wake_event=wake_event,
        server_ref=server_ref,
    )

    handler_cls = _build_handler(
        ctx=ctx,
        ui_html_path=resolved_ui_html,
        static_dir=resolved_static_dir,
    )

    try:
        httpd = _McasHTTPServer(("127.0.0.1", port), handler_cls)
    except OSError as exc:
        raise SyncError(f"Could not listen on 127.0.0.1:{port}: {exc}") from exc
    server_ref.append(httpd)

    ui_json_path = get_ui_json_path()
    _atomic_write_json(
        ui_json_path,
        {"port": port, "token": token, "pid": os.getpid()},
    )

    def _autosync_worker() -> None:
        last_tick = 0.0
        while not stop_event.is_set():
            try:
                cfg = get_autosync_dict()
                interval = max(5, int(cfg.get("intervalSec", 30)))
                enabled = bool(cfg.get("enabled", False))
            except Exception:
                interval = 30
                enabled = False

            now_mono = time.monotonic()
            if enabled and (now_mono - last_tick >= interval):
                try:
                    run_autosync_cycle(force_even_if_disabled=False)
                except Exception:
                    pass
                last_tick = time.monotonic()

            if wake_event.wait(timeout=1.0):
                wake_event.clear()
                last_tick = 0.0

    worker = threading.Thread(target=_autosync_worker, name="mcas-autosync", daemon=True)
    worker.start()

    def _handle_sig(_signum: int, _frame: Any) -> None:
        def _stop() -> None:
            stop_event.set()
            wake_event.set()
            httpd.shutdown()

        threading.Thread(target=_stop, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_sig)
        except (ValueError, OSError):
            pass

    try:
        httpd.serve_forever(poll_interval=0.1)
    finally:
        stop_event.set()
        wake_event.set()
        ui_json_path.unlink(missing_ok=True)
        try:
            httpd.server_close()
        except Exception:
            pass
        worker.join(timeout=2.0)

    return 0

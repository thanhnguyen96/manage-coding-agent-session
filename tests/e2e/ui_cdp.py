"""Opt-in headless Chrome CDP E2E test for the mcas static web UI."""

from __future__ import annotations

import base64
import json
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from typing import Any

import pytest

_BASE_ENV = os.environ.copy()

try:
    from tests.fixtures.seed import claude_slug
except ModuleNotFoundError:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from tests.fixtures.seed import claude_slug


def _find_chrome() -> pathlib.Path | None:
    env_path = os.environ.get("MCAS_CHROME_PATH", "").strip()
    if env_path and pathlib.Path(env_path).is_file():
        return pathlib.Path(env_path)
    candidates = [
        pathlib.Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        pathlib.Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    for name in ("chrome", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return pathlib.Path(found)
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class CDPClient:
    def __init__(self, ws_url: str) -> None:
        assert ws_url.startswith("ws://")
        rest = ws_url[5:]
        host_port, path = rest.split("/", 1)
        host, port_s = host_port.split(":")
        self.sock = socket.create_connection((host, int(port_s)), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {host}:{port_s}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket handshake closed")
            buf += chunk
        self._extra = buf.split(b"\r\n\r\n", 1)[1]
        self._seq = 0
        self.events: list[dict[str, Any]] = []

    def _recv_exact(self, n: int) -> bytes:
        out = bytearray()
        if self._extra:
            take = min(n, len(self._extra))
            out.extend(self._extra[:take])
            self._extra = self._extra[take:]
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise RuntimeError("WebSocket closed")
            out.extend(chunk)
        return bytes(out)

    def _recv_frame(self) -> tuple[int, bytes]:
        b1, b2 = self._recv_exact(2)
        opcode = b1 & 0x0F
        masked = (b2 & 0x80) != 0
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length)
        if masked:
            payload = bytes(payload[i] ^ mask[i % 4] for i in range(length))
        return opcode, payload

    def _send_text(self, text: str) -> None:
        data = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", n))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack(">Q", n))
        header.extend(mask)
        masked = bytes(data[i] ^ mask[i % 4] for i in range(n))
        self.sock.sendall(bytes(header) + masked)

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._seq += 1
        cid = self._seq
        self._send_text(json.dumps({"id": cid, "method": method, "params": params or {}}))
        while True:
            op, payload = self._recv_frame()
            if op == 8:
                raise RuntimeError("CDP connection closed")
            if op == 9:
                continue
            if op != 1:
                continue
            msg = json.loads(payload.decode("utf-8"))
            if msg.get("id") == cid:
                if "error" in msg:
                    raise RuntimeError(f"CDP error for {method}: {msg['error']}")
                return msg.get("result", {})
            self.events.append(msg)

    def eval_js(self, expr: str) -> Any:
        res = self.call(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": True},
        )
        if "exceptionDetails" in res:
            raise RuntimeError(f"JS exception: {res['exceptionDetails']}")
        return res.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


def _seed_sandbox(sb: pathlib.Path, mcas_port: int) -> tuple[dict[str, str], dict[str, Any]]:
    agy_cli = sb / "agy_cli"
    agy_ide = sb / "agy_ide"
    claude_dir = sb / "claude"
    mcas_root = sb / "mcas_root"
    ws = sb / "repo_ws"
    for d in (
        agy_cli / "conversations",
        agy_cli / "brain",
        agy_cli / "annotations",
        agy_cli / "implicit",
        agy_ide,
        claude_dir,
        mcas_root,
        ws,
        sb / "localappdata",
        sb / "appdata",
        sb / "userprofile",
    ):
        d.mkdir(parents=True, exist_ok=True)

    synced_id = "aaaaaaaa-1111-4111-8111-111111111111"
    heavy_id = "bbbbbbbb-2222-4222-8222-222222222222"
    empty_id = "cccccccc-3333-4333-8333-333333333333"

    env = os.environ.copy()
    env["MCAS_AGY_DIR"] = str(agy_cli)
    env["MCAS_AGY_IDE_DIR"] = str(agy_ide)
    env["MCAS_CLAUDE_DIR"] = str(claude_dir)
    env["MCAS_ROOT"] = str(mcas_root)
    env["MCAS_PORT"] = str(mcas_port)
    env["LOCALAPPDATA"] = str(sb / "localappdata")
    env["APPDATA"] = str(sb / "appdata")
    env["USERPROFILE"] = str(sb / "userprofile")

    proj = claude_dir / "projects" / claude_slug(ws)
    proj.mkdir(parents=True, exist_ok=True)

    synced_rows = [
        {
            "type": "user",
            "uuid": "su1",
            "parentUuid": None,
            "sessionId": synced_id,
            "isSidechain": False,
            "timestamp": "2026-11-14T09:00:00.000Z",
            "cwd": str(ws),
            "message": {"role": "user", "content": "Hello synced conversation"},
        },
        {
            "type": "assistant",
            "uuid": "sa1",
            "parentUuid": "su1",
            "sessionId": synced_id,
            "isSidechain": False,
            "timestamp": "2026-11-14T09:00:05.000Z",
            "cwd": str(ws),
            "message": {
                "id": "sm1",
                "role": "assistant",
                "content": [{"type": "text", "text": "Synced reply here"}],
            },
        },
    ]
    (proj / f"{synced_id}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in synced_rows) + "\n", encoding="utf-8"
    )
    (proj / synced_id / "custom-title.json").parent.mkdir(parents=True, exist_ok=True)
    (proj / synced_id / "custom-title.json").write_text(
        json.dumps({"customTitle": "Synced Pair Chat"}), encoding="utf-8"
    )

    heavy_rows = [
        {
            "type": "user",
            "uuid": "hu1",
            "parentUuid": None,
            "sessionId": heavy_id,
            "isSidechain": False,
            "timestamp": "2026-11-14T08:00:00.000Z",
            "cwd": str(ws),
            "message": {"role": "user", "content": "Heavy question 1"},
        },
        {
            "type": "assistant",
            "uuid": "ha1",
            "parentUuid": "hu1",
            "sessionId": heavy_id,
            "isSidechain": False,
            "timestamp": "2026-11-14T08:00:05.000Z",
            "cwd": str(ws),
            "message": {
                "id": "hm1",
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Thinking " * 50},
                    {"type": "text", "text": "Calling big tool"},
                    {"type": "tool_use", "id": "tu1", "name": "read", "input": {}},
                ],
            },
        },
        {
            "type": "user",
            "uuid": "htr1",
            "parentUuid": "ha1",
            "sessionId": heavy_id,
            "isSidechain": False,
            "timestamp": "2026-11-14T08:00:08.000Z",
            "cwd": str(ws),
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "Y" * 5000}],
            },
        },
        {
            "type": "user",
            "uuid": "hu2",
            "parentUuid": "htr1",
            "sessionId": heavy_id,
            "isSidechain": False,
            "timestamp": "2026-11-14T08:01:00.000Z",
            "cwd": str(ws),
            "message": {"role": "user", "content": "Followup 2"},
        },
        {
            "type": "assistant",
            "uuid": "ha2",
            "parentUuid": "hu2",
            "sessionId": heavy_id,
            "isSidechain": False,
            "timestamp": "2026-11-14T08:01:05.000Z",
            "cwd": str(ws),
            "message": {
                "id": "hm2",
                "role": "assistant",
                "content": [{"type": "text", "text": "Newest answer"}],
            },
        },
    ]
    (proj / f"{heavy_id}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in heavy_rows) + "\n", encoding="utf-8"
    )
    (proj / heavy_id / "subagents").mkdir(parents=True, exist_ok=True)
    (proj / heavy_id / "subagents" / "sub.jsonl").write_bytes(b"Z" * 80000)
    (proj / heavy_id / "custom-title.json").write_text(
        json.dumps({"customTitle": "Heavy UI Chat"}), encoding="utf-8"
    )

    (proj / f"{empty_id}.jsonl").write_text(
        json.dumps({"type": "file-history-snapshot", "messageId": "m1", "snapshot": {}}) + "\n",
        encoding="utf-8",
    )
    orphan_dir = agy_cli / "brain" / "orphan-e2e"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / "junk.bin").write_bytes(b"O" * 32768)

    cp = subprocess.run(
        [sys.executable, "-m", "mcas", "copy", synced_id, "--to", "agy", "--json"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    copy_info = json.loads(cp.stdout)
    return env, {
        "synced_claude_id": synced_id,
        "synced_agy_id": copy_info.get("targetId") or copy_info.get("id") or synced_id,
        "heavy_id": heavy_id,
        "empty_id": empty_id,
        "orphan_dir": orphan_dir,
        "empty_jsonl": proj / f"{empty_id}.jsonl",
        "proj_dir": proj,
    }


def _wait_cond(cdp: CDPClient, js_bool: str, timeout: float = 10.0, desc: str = "condition") -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cdp.eval_js(js_bool):
            return
        time.sleep(0.15)
    raise AssertionError(f"Timed out waiting for {desc}")


def run_cdp_e2e() -> None:
    chrome_exe = _find_chrome()
    if not chrome_exe:
        raise RuntimeError("Chrome binary not found for CDP E2E test")

    mcas_port = _free_port()
    cdp_port = _free_port()
    sb = pathlib.Path(tempfile.mkdtemp(prefix="mcas-cdp-"))
    srv = None
    chrome = None
    cdp = None
    chrome_log_fh = None
    try:
        env, meta = _seed_sandbox(sb, mcas_port)
        srv = subprocess.Popen(
            [sys.executable, "-m", "mcas", "__serve", "--port", str(mcas_port)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{mcas_port}/health", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.15)
        else:
            raise RuntimeError("mcas __serve failed to start")

        prof = sb / "chrome_prof"
        prof.mkdir()
        chrome_log = sb / "chrome_stderr.log"
        chrome_log_fh = chrome_log.open("wb")
        chrome = subprocess.Popen(
            [
                str(chrome_exe),
                "--headless=new",
                "--disable-gpu",
                f"--remote-debugging-port={cdp_port}",
                f"--user-data-dir={prof}",
                "--no-first-run",
                "--no-default-browser-check",
                f"http://127.0.0.1:{mcas_port}/",
            ],
            env=_BASE_ENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=chrome_log_fh,
        )

        targets = None
        for _ in range(60):
            if chrome.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=1) as r:
                    data = json.loads(r.read().decode("utf-8"))
                    pages = [
                        x
                        for x in data
                        if x.get("type") == "page" and x.get("webSocketDebuggerUrl")
                    ]
                    if pages:
                        targets = pages[0]
                        break
            except Exception:
                time.sleep(0.15)
        if not targets:
            chrome_log_fh.close()
            err_txt = chrome_log.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(
                f"Chrome CDP target did not appear (rc={chrome.poll()}): {err_txt[:500]}"
            )
        chrome_log_fh.close()

        cdp = CDPClient(targets["webSocketDebuggerUrl"])
        cdp.call("Runtime.enable")
        cdp.call("Network.enable")

        # 1. Grouped rows render with (claude, agy)
        _wait_cond(
            cdp,
            "document.querySelectorAll('#sessionList .session-row').length >= 2",
            desc="initial session rows",
        )
        state_snap = cdp.eval_js(
            """(() => {
            const rows = [...document.querySelectorAll('#sessionList .session-row')];
            return {
                rowCount: rows.length,
                tags: rows.map(r => r.querySelector('.prov-tag-btn')?.textContent?.trim()),
                titles: rows.map(r => r.querySelector('.session-title')?.textContent?.trim()),
            };
        })()"""
        )
        assert state_snap["rowCount"] == 2, f"Expected 2 grouped rows, got {state_snap}"
        assert "(claude, agy)" in state_snap["tags"], f"Missing (claude, agy) tag: {state_snap}"

        # 2. Sort by size descending -> Heavy UI Chat first
        cdp.eval_js(
            """(() => {
            const sel = document.getElementById('sortSelect');
            sel.value = 'size_desc';
            sel.dispatchEvent(new Event('change'));
        })()"""
        )
        _wait_cond(
            cdp,
            "document.querySelectorAll('#sessionList .session-row')[0]?.querySelector('.session-title')?.textContent?.includes('Heavy UI Chat')",
            desc="sort size_desc",
        )

        # 3. Provider dropdown untick agy -> Apply -> (claude)
        cdp.eval_js(
            """(() => {
            const row = [...document.querySelectorAll('#sessionList .session-row')].find(r => r.textContent.includes('Synced Pair Chat'));
            row.querySelector('.prov-tag-btn').click();
        })()"""
        )
        _wait_cond(cdp, "document.querySelector('.prov-dropdown-menu') !== null", desc="dropdown open")
        dd_checks = cdp.eval_js(
            """[...document.querySelectorAll('.prov-dropdown-menu input[type="checkbox"]:not([disabled])')].map(c => c.checked)"""
        )
        assert dd_checks[:2] == [True, True], f"Expected both checked initially, got {dd_checks}"

        cdp.eval_js(
            """(() => {
            const rows = [...document.querySelectorAll('.prov-dropdown-menu .prov-dd-row')];
            const agyCb = rows[1].querySelector('input[type="checkbox"]');
            agyCb.checked = false;
            agyCb.dispatchEvent(new Event('change'));
            const applyBtn = [...document.querySelectorAll('.prov-dropdown-menu button')].find(b => b.textContent.startsWith('Apply'));
            applyBtn.click();
        })()"""
        )
        _wait_cond(
            cdp,
            """(() => { const row = [...document.querySelectorAll('#sessionList .session-row')].find(r => r.textContent.includes('Synced Pair Chat')); return row && row.querySelector('.prov-tag-btn')?.textContent?.trim() === '(claude)'; })()""",
            desc="untick agy -> (claude)",
        )

        # 4. Tick agy back -> Apply -> (claude, agy)
        cdp.eval_js(
            """(() => {
            const row = [...document.querySelectorAll('#sessionList .session-row')].find(r => r.textContent.includes('Synced Pair Chat'));
            row.querySelector('.prov-tag-btn').click();
        })()"""
        )
        _wait_cond(
            cdp, "document.querySelector('.prov-dropdown-menu') !== null", desc="dropdown reopen"
        )
        cdp.eval_js(
            """(() => {
            const rows = [...document.querySelectorAll('.prov-dropdown-menu .prov-dd-row')];
            const agyCb = rows[1].querySelector('input[type="checkbox"]');
            agyCb.checked = true;
            agyCb.dispatchEvent(new Event('change'));
            const applyBtn = [...document.querySelectorAll('.prov-dropdown-menu button')].find(b => b.textContent.startsWith('Apply'));
            applyBtn.click();
        })()"""
        )
        _wait_cond(
            cdp,
            """(() => { const row = [...document.querySelectorAll('#sessionList .session-row')].find(r => r.textContent.includes('Synced Pair Chat')); return row && row.querySelector('.prov-tag-btn')?.textContent?.trim() === '(claude, agy)'; })()""",
            desc="tick agy back -> (claude, agy)",
        )

        # 5. Untick all -> Apply -> confirmModal -> Confirm deletes completely
        cdp.eval_js(
            """(() => {
            const row = [...document.querySelectorAll('#sessionList .session-row')].find(r => r.textContent.includes('Synced Pair Chat'));
            row.querySelector('.prov-tag-btn').click();
        })()"""
        )
        _wait_cond(
            cdp,
            "document.querySelector('.prov-dropdown-menu') !== null",
            desc="dropdown open for delete all",
        )
        cdp.eval_js(
            """(() => {
            const untickBtn = [...document.querySelectorAll('.prov-dropdown-menu button')].find(b => b.textContent === 'Untick all');
            untickBtn.click();
        })()"""
        )
        assert cdp.eval_js("!document.querySelector('.prov-dd-warn').hidden")
        cdp.eval_js(
            """(() => {
            const applyBtn = [...document.querySelectorAll('.prov-dropdown-menu button')].find(b => b.textContent.includes('Apply'));
            applyBtn.click();
        })()"""
        )
        _wait_cond(cdp, "!document.getElementById('confirmModal').hidden", desc="confirm modal open")
        cf_title = cdp.eval_js("document.getElementById('cfTitle').textContent")
        assert "Delete conversation completely" in cf_title
        cdp.eval_js("document.getElementById('cfOkBtn').click()")
        _wait_cond(
            cdp,
            "![...document.querySelectorAll('#sessionList .session-row')].some(r => r.textContent.includes('Synced Pair Chat'))",
            desc="complete delete finished",
        )

        # 6. Trim modal on Heavy UI Chat
        cdp.eval_js(
            """(() => {
            const row = [...document.querySelectorAll('#sessionList .session-row')].find(r => r.textContent.includes('Heavy UI Chat'));
            const btn = [...row.querySelectorAll('.row-actions button')].find(b => b.textContent === 'Lighten');
            btn.click();
        })()"""
        )
        _wait_cond(cdp, "!document.getElementById('trimModal').hidden", desc="trim modal open")
        cdp.eval_js("document.getElementById('tmSubmitBtn').click()")
        _wait_cond(
            cdp,
            "document.getElementById('trimModal').hidden && !document.getElementById('tmSubmitBtn').disabled",
            desc="trim completed",
        )
        heavy_size_after = (meta["proj_dir"] / f"{meta['heavy_id']}.jsonl").stat().st_size
        assert heavy_size_after < 5000, f"Expected trimmed jsonl < 5KB, got {heavy_size_after}"
        assert not (meta["proj_dir"] / meta["heavy_id"] / "subagents").exists()

        # 7. Cleanup modal: Preview (Dry Run) + Clean Up Now
        cdp.eval_js("document.getElementById('openCleanupBtn').click()")
        _wait_cond(cdp, "!document.getElementById('cleanupModal').hidden", desc="cleanup modal open")
        assert cdp.eval_js("document.querySelectorAll('#clStorageGrid .storage-stat').length === 4")
        cdp.eval_js(
            "document.getElementById('clEmpty').checked = true; document.getElementById('clPreviewBtn').click()"
        )
        _wait_cond(
            cdp,
            "!document.getElementById('clPreviewBox').hidden && !document.getElementById('clPreviewBtn').disabled",
            desc="cleanup dry run preview",
        )
        assert meta["orphan_dir"].exists() and meta["empty_jsonl"].exists()
        cdp.eval_js("document.getElementById('clRunBtn').click()")
        _wait_cond(cdp, "!document.getElementById('confirmModal').hidden", desc="cleanup confirm")
        cdp.eval_js("document.getElementById('cfOkBtn').click()")
        _wait_cond(
            cdp,
            "!document.getElementById('clRunBtn').disabled && document.getElementById('clPreviewBox').hidden",
            desc="cleanup finished",
        )
        assert not meta["orphan_dir"].exists() and not meta["empty_jsonl"].exists()

        # Verify zero JS/console/HTTP errors
        js_errs = []
        http_errs = []
        for ev in cdp.events:
            m = ev.get("method")
            p = ev.get("params", {})
            if m == "Runtime.exceptionThrown":
                js_errs.append(p)
            elif m == "Runtime.consoleAPICalled" and p.get("type") == "error":
                js_errs.append(p)
            elif m == "Network.responseReceived":
                st = p.get("response", {}).get("status", 200)
                url = p.get("response", {}).get("url", "")
                if st >= 400:
                    http_errs.append((st, url))
        assert not js_errs, f"JS/Console errors detected: {js_errs}"
        assert not http_errs, f"HTTP errors detected: {http_errs}"
    finally:
        if chrome_log_fh is not None:
            chrome_log_fh.close()
        if cdp:
            cdp.close()
        if chrome:
            chrome.terminate()
            try:
                chrome.wait(timeout=3)
            except Exception:
                chrome.kill()
        if srv:
            srv.terminate()
            try:
                srv.wait(timeout=3)
            except subprocess.TimeoutExpired:
                srv.kill()
        shutil.rmtree(sb, ignore_errors=True)


@pytest.mark.skipif(
    os.environ.get("MCAS_RUN_E2E") != "1" or _find_chrome() is None,
    reason="Opt-in CDP UI E2E requires MCAS_RUN_E2E=1 and Chrome installed",
)
def test_ui_cdp_e2e() -> None:
    run_cdp_e2e()


if __name__ == "__main__":
    run_cdp_e2e()
    print("ALL CDP E2E CHECKS PASSED WITH 0 JS/CONSOLE/HTTP ERRORS")

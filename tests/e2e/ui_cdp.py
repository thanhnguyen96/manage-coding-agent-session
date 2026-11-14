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
        cdp.call("Page.enable")
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

        # 1a. Top-right placement of #helpBtn, #langBtn, #themeBtn inside .topbar
        placement = cdp.eval_js(
            """(() => {
            const tb = document.querySelector('.topbar').getBoundingClientRect();
            const hb = document.getElementById('helpBtn').getBoundingClientRect();
            const lb = document.getElementById('langBtn').getBoundingClientRect();
            const th = document.getElementById('themeBtn').getBoundingClientRect();
            const aw = document.getElementById('autostartWrap')?.getBoundingClientRect();
            return {
                topbar: { top: tb.top, right: tb.right, bottom: tb.bottom, width: tb.width },
                help: { top: hb.top, right: hb.right, bottom: hb.bottom, width: hb.width, height: hb.height },
                lang: { top: lb.top, right: lb.right, bottom: lb.bottom, width: lb.width, height: lb.height },
                theme: { top: th.top, right: th.right, bottom: th.bottom, width: th.width, height: th.height },
                autostartVisible: !!(aw && aw.width > 0 && aw.height > 0),
                viewportHeight: window.innerHeight,
            };
        })()"""
        )
        assert placement["autostartVisible"], f"Missing visible #autostartWrap: {placement}"
        for key in ("help", "lang", "theme"):
            r = placement[key]
            assert r["width"] > 0 and r["height"] > 0, f"{key} has zero size: {placement}"
            assert r["top"] >= placement["topbar"]["top"] - 2, f"{key} above topbar: {placement}"
            assert r["bottom"] <= placement["topbar"]["bottom"] + 2, f"{key} below topbar: {placement}"
            assert r["right"] <= placement["topbar"]["right"] + 2, f"{key} outside topbar right: {placement}"
            assert r["top"] < placement["viewportHeight"] / 2, f"{key} not in top half: {placement}"
        assert (
            placement["theme"]["right"] >= placement["lang"]["right"] >= placement["help"]["right"]
        ), f"Expected #themeBtn rightmost, then #langBtn, then #helpBtn: {placement}"
        assert placement["theme"]["right"] >= placement["topbar"]["right"] - 32, (
            f"Expected #themeBtn at far top-right edge: {placement}"
        )

        # 1b. Language switcher (#langBtn & #langMenu) with SVG flags, VI switch, reload persistence, EN restore
        lang_init = cdp.eval_js(
            """(() => {
            const btnSvg = document.querySelector('#langBtn svg');
            const optEn = document.querySelector('#langMenu .lang-opt[data-lang="en"]');
            const optVi = document.querySelector('#langMenu .lang-opt[data-lang="vi"]');
            return {
                htmlLang: document.documentElement.lang,
                btnCode: document.getElementById('langBtnCode')?.textContent?.trim(),
                btnSvgShapes: btnSvg ? btnSvg.querySelectorAll('rect, path, polygon, circle').length : 0,
                optEnText: optEn?.querySelector('.lang-opt-label')?.textContent?.trim(),
                optViText: optVi?.querySelector('.lang-opt-label')?.textContent?.trim(),
                optEnSvg: optEn ? optEn.querySelectorAll('svg rect, svg path, svg polygon, svg circle').length : 0,
                optViSvg: optVi ? optVi.querySelectorAll('svg rect, svg path, svg polygon, svg circle').length : 0,
                helpText: document.getElementById('helpBtn')?.textContent?.trim(),
                tabSessions: document.getElementById('tabBtnSessions')?.textContent?.trim(),
            };
        })()"""
        )
        assert lang_init["htmlLang"] == "en", f"Expected initial lang='en', got {lang_init}"
        assert lang_init["btnCode"] == "EN", f"Expected EN badge, got {lang_init}"
        assert lang_init["btnSvgShapes"] >= 3, f"Expected crisp SVG flag on #langBtn: {lang_init}"
        assert lang_init["optEnText"] == "English" and lang_init["optViText"] == "Tiếng Việt", lang_init
        assert lang_init["optEnSvg"] >= 3 and lang_init["optViSvg"] >= 2, f"Missing menu SVG flags: {lang_init}"

        cdp.eval_js("document.getElementById('langBtn').click()")
        _wait_cond(cdp, "!document.getElementById('langMenu').hidden", desc="langMenu open")
        cdp.eval_js("""document.querySelector('#langMenu .lang-opt[data-lang="vi"]').click()""")
        _wait_cond(
            cdp,
            "document.documentElement.lang === 'vi' && document.getElementById('langMenu').hidden",
            desc="switch language to vi",
        )
        vi_snap = cdp.eval_js(
            """(() => ({
            htmlLang: document.documentElement.lang,
            stored: localStorage.getItem('mcas.lang'),
            btnCode: document.getElementById('langBtnCode')?.textContent?.trim(),
            helpText: document.getElementById('helpBtn')?.textContent?.trim(),
            tabSessions: document.getElementById('tabBtnSessions')?.textContent?.trim(),
            tabAutosync: document.getElementById('tabBtnAutosync')?.textContent?.trim(),
            cleanupBtn: document.getElementById('openCleanupBtn')?.textContent?.trim(),
            quitBtn: document.getElementById('quitBtn')?.textContent?.trim(),
        }))()"""
        )
        assert vi_snap["stored"] == "vi" and vi_snap["btnCode"] == "VI", vi_snap
        assert "Hướng dẫn" in (vi_snap["helpText"] or ""), vi_snap
        assert "Phiên" in (vi_snap["tabSessions"] or ""), vi_snap
        assert "Hoạt động" in (vi_snap["tabAutosync"] or ""), vi_snap
        assert "Dọn dẹp" in (vi_snap["cleanupBtn"] or ""), vi_snap
        assert "Dừng máy chủ" in (vi_snap["quitBtn"] or ""), vi_snap

        # Verify provider dropdown translates in Vietnamese too
        cdp.eval_js(
            """(() => {
            const row = [...document.querySelectorAll('#sessionList .session-row')].find(r => r.textContent.includes('Synced Pair Chat'));
            row.querySelector('.prov-tag-btn').click();
        })()"""
        )
        _wait_cond(cdp, "document.querySelector('.prov-dropdown-menu') !== null", desc="vi dropdown open")
        vi_dd_texts = cdp.eval_js(
            "[...document.querySelectorAll('.prov-dropdown-menu button')].map(b => b.textContent.trim())"
        )
        assert any("Bỏ chọn hết" in x for x in vi_dd_texts) and any("Áp dụng" in x for x in vi_dd_texts), (
            f"Expected Vietnamese dropdown buttons, got {vi_dd_texts}"
        )
        cdp.eval_js("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))")
        _wait_cond(cdp, "document.querySelector('.prov-dropdown-menu') === null", desc="vi dropdown closed")

        # Reload page and verify 'vi' persists from localStorage
        cdp.call("Page.reload")
        _wait_cond(
            cdp,
            "document.readyState === 'complete' && document.querySelectorAll('#sessionList .session-row').length >= 2",
            desc="page reload in vi",
        )
        vi_reload = cdp.eval_js(
            """(() => ({
            htmlLang: document.documentElement.lang,
            stored: localStorage.getItem('mcas.lang'),
            btnCode: document.getElementById('langBtnCode')?.textContent?.trim(),
            helpText: document.getElementById('helpBtn')?.textContent?.trim(),
            tabSessions: document.getElementById('tabBtnSessions')?.textContent?.trim(),
        }))()"""
        )
        assert vi_reload["htmlLang"] == "vi" and vi_reload["stored"] == "vi", vi_reload
        assert vi_reload["btnCode"] == "VI", vi_reload
        assert "Hướng dẫn" in (vi_reload["helpText"] or ""), vi_reload
        assert "Phiên" in (vi_reload["tabSessions"] or ""), vi_reload

        # Switch back to 'en' and verify English labels restore cleanly
        cdp.eval_js("document.getElementById('langBtn').click()")
        _wait_cond(cdp, "!document.getElementById('langMenu').hidden", desc="langMenu open for en restore")
        cdp.eval_js("""document.querySelector('#langMenu .lang-opt[data-lang="en"]').click()""")
        _wait_cond(
            cdp,
            "document.documentElement.lang === 'en' && document.getElementById('langMenu').hidden",
            desc="restore language to en",
        )
        en_restored = cdp.eval_js(
            """(() => ({
            htmlLang: document.documentElement.lang,
            stored: localStorage.getItem('mcas.lang'),
            btnCode: document.getElementById('langBtnCode')?.textContent?.trim(),
            helpText: document.getElementById('helpBtn')?.textContent?.trim(),
            tabSessions: document.getElementById('tabBtnSessions')?.textContent?.trim(),
            cleanupBtn: document.getElementById('openCleanupBtn')?.textContent?.trim(),
        }))()"""
        )
        assert en_restored["stored"] == "en" and en_restored["btnCode"] == "EN", en_restored
        assert en_restored["helpText"] == "Help", en_restored
        assert "Sessions" in (en_restored["tabSessions"] or ""), en_restored
        assert "Clean Up" in (en_restored["cleanupBtn"] or ""), en_restored

        # 1c. Help / Visual FAQ modal (#helpBtn -> #helpModal): all 5 tabs, 7 guide images, VI FAQ, Close & Escape
        cdp.eval_js("document.getElementById('helpBtn').click()")
        _wait_cond(cdp, "!document.getElementById('helpModal').hidden", desc="helpModal open")
        loaded_guide_srcs: set[str] = set()
        for tab_id in ("overview", "providers", "drawer", "cleanup", "autosync"):
            cdp.eval_js(
                f"""(() => {{
                const btn = document.querySelector('#helpTabs .help-tab-btn[data-help-tab="{tab_id}"]');
                if (!btn) throw new Error('Missing help tab: {tab_id}');
                btn.click();
            }})()"""
            )
            _wait_cond(
                cdp,
                f"""(() => {{
                    const active = document.querySelector('#helpTabs .help-tab-btn[data-help-tab="{tab_id}"].active');
                    const imgs = [...document.querySelectorAll('#helpBody img')];
                    return !!active && imgs.length > 0 && imgs.every(im => im.complete);
                }})()""",
                desc=f"help tab {tab_id} images complete",
            )
            tab_info = cdp.eval_js(
                """(() => {
                const imgs = [...document.querySelectorAll('#helpBody img')];
                return {
                    imgs: imgs.map(im => ({
                        src: im.getAttribute('src'),
                        complete: im.complete,
                        naturalWidth: im.naturalWidth,
                        hidden: im.hidden,
                    })),
                    legendCount: document.querySelectorAll('#helpBody .help-legend-item').length,
                    useCaseCount: document.querySelectorAll('#helpBody .help-usecase-card').length,
                };
            })()"""
            )
            assert tab_info["legendCount"] > 0, f"Expected legend items in {tab_id}: {tab_info}"
            if tab_id == "autosync":
                assert tab_info["useCaseCount"] >= 3, f"Expected use-case cards in autosync: {tab_info}"
            for im in tab_info["imgs"]:
                assert im["complete"] and im["naturalWidth"] > 0 and not im["hidden"], (
                    f"Guide image failed to load in tab {tab_id}: {im}"
                )
                loaded_guide_srcs.add(im["src"])

        expected_guide_imgs = {
            "/static/img/guide-topbar.png",
            "/static/img/guide-filters-row.png",
            "/static/img/guide-provider-dd.png",
            "/static/img/guide-drawer.png",
            "/static/img/guide-lighten.png",
            "/static/img/guide-cleanup.png",
            "/static/img/guide-autosync.png",
        }
        assert expected_guide_imgs <= loaded_guide_srcs, (
            f"Missing guide images across Help tabs: {expected_guide_imgs - loaded_guide_srcs}"
        )

        # Close Help with close button, switch to vi, reopen via '?' shortcut, verify VI FAQ, close with Escape
        cdp.eval_js("document.getElementById('helpCloseBtn').click()")
        _wait_cond(cdp, "document.getElementById('helpModal').hidden", desc="helpModal closed via closeBtn")

        cdp.eval_js("document.getElementById('langBtn').click()")
        _wait_cond(cdp, "!document.getElementById('langMenu').hidden", desc="langMenu open before vi FAQ")
        cdp.eval_js("""document.querySelector('#langMenu .lang-opt[data-lang="vi"]').click()""")
        _wait_cond(
            cdp,
            "document.documentElement.lang === 'vi' && document.getElementById('langMenu').hidden",
            desc="lang vi before helpModal",
        )
        cdp.eval_js("document.dispatchEvent(new KeyboardEvent('keydown', { key: '?', bubbles: true }))")
        _wait_cond(cdp, "!document.getElementById('helpModal').hidden", desc="helpModal opened via ? shortcut")
        vi_help = cdp.eval_js(
            """(() => ({
            title: document.getElementById('helpTitle')?.textContent?.trim(),
            tabTexts: [...document.querySelectorAll('#helpTabs .help-tab-btn')].map(b => b.textContent.trim()),
            secTitles: [...document.querySelectorAll('#helpBody .help-sec-title')].map(h => h.textContent.trim()),
            useWhen: document.querySelector('#helpBody .help-legend-when em')?.textContent?.trim(),
        }))()"""
        )
        assert "Hướng dẫn" in (vi_help["title"] or ""), vi_help
        assert any("Tổng quan" in x for x in vi_help["tabTexts"]), vi_help
        assert any(
            "Auto-Sync" in x or "tình huống" in x.lower() for x in vi_help["secTitles"]
        ), vi_help
        assert "Dùng khi:" in (vi_help["useWhen"] or ""), vi_help
        cdp.eval_js(
            """document.querySelector('#helpTabs .help-tab-btn[data-help-tab="overview"]').click()"""
        )
        _wait_cond(
            cdp,
            """document.querySelector('#helpBody .help-sec-title')?.textContent?.includes('Thanh tiêu đề')""",
            desc="vi overview heading in helpModal",
        )

        cdp.eval_js("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))")
        _wait_cond(cdp, "document.getElementById('helpModal').hidden", desc="helpModal closed via Escape")

        # Restore English before running the core workflow assertions
        cdp.eval_js("document.getElementById('langBtn').click()")
        _wait_cond(cdp, "!document.getElementById('langMenu').hidden", desc="langMenu open to restore en")
        cdp.eval_js("""document.querySelector('#langMenu .lang-opt[data-lang="en"]').click()""")
        _wait_cond(
            cdp,
            "document.documentElement.lang === 'en' && document.getElementById('langMenu').hidden",
            desc="lang restored to en",
        )

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

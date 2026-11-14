"""HTTP API and static asset tests against `python -m mcas __serve` in sandbox."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from tests.conftest import find_free_port
from tests.fixtures.seed import claude_slug, make_agy_db, seed_heavy_and_clean_fixtures


def _http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _http_post_json(
    url: str, headers: dict[str, str], payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8")
    merged = {"Content-Type": "application/json; charset=utf-8", **headers}
    req = urllib.request.Request(url, data=data, headers=merged, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_http_server_api_and_static_assets(seeded_sandbox) -> None:
    sb, ids = seeded_sandbox
    port = find_free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(sb.env)
    env["MCAS_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, "-m", "mcas", "__serve", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Wait for /health
        for _ in range(60):
            if proc.poll() is not None:
                stderr = proc.stderr.read() if proc.stderr else ""
                raise AssertionError(f"mcas __serve exited early ({proc.returncode}): {stderr}")
            try:
                status, _, body = _http_get(f"{base}/health")
                if status == 200:
                    health = json.loads(body.decode("utf-8"))
                    assert health["app"] == "mcas"
                    break
            except OSError:
                pass
            time.sleep(0.15)
        else:
            raise AssertionError("Timed out waiting for /health")

        # Root HTML + token extraction
        st, _, html_bytes = _http_get(f"{base}/")
        assert st == 200
        html = html_bytes.decode("utf-8")
        assert '<html lang="en">' in html
        m = re.search(r"const token = '([0-9a-f]{32})'", html)
        assert m is not None, "Missing 32-hex token in /"
        token = m.group(1)
        headers = {"X-Mcas-Token": token}

        # Static assets referenced in index.html must load without token and with proper MIME
        static_paths = re.findall(r"""(?:href|src)=["'](/static/[^"']+)["']""", html)
        assert static_paths, "Expected /static/ asset references in index.html"
        for rel in static_paths:
            asset_st, asset_hdrs, asset_body = _http_get(f"{base}{rel}")
            assert asset_st == 200, f"Static asset {rel} failed with {asset_st}"
            assert len(asset_body) > 0
            ctype = (asset_hdrs.get("Content-Type") or "").lower()
            if rel.endswith(".css"):
                assert "css" in ctype
            elif rel.endswith(".js"):
                assert "javascript" in ctype

        # Bilingual i18n and Help modal JS modules load 200
        for js_rel in ("/static/js/i18n.js", "/static/js/modals/helpModal.js"):
            js_st, js_hdrs, js_body = _http_get(f"{base}{js_rel}")
            assert js_st == 200, f"JS module {js_rel} failed with {js_st}"
            assert len(js_body) > 0
            assert "javascript" in (js_hdrs.get("Content-Type") or "").lower()

        # All 7 annotated visual guide screenshots load 200 as image/png
        for img_rel in (
            "/static/img/guide-topbar.png",
            "/static/img/guide-filters-row.png",
            "/static/img/guide-provider-dd.png",
            "/static/img/guide-drawer.png",
            "/static/img/guide-lighten.png",
            "/static/img/guide-cleanup.png",
            "/static/img/guide-autosync.png",
        ):
            img_st, img_hdrs, img_body = _http_get(f"{base}{img_rel}")
            assert img_st == 200, f"Guide image {img_rel} failed with {img_st}"
            assert (img_hdrs.get("Content-Type") or "").lower().startswith("image/png")
            assert len(img_body) > 100 and img_body.startswith(b"\x89PNG\r\n\x1a\n")

        # Path traversal protection on /static/
        trav_st, _, _ = _http_get(f"{base}/static/../../pyproject.toml")
        assert trav_st in (400, 403, 404)

        # Missing token -> 403
        unauth_st, _, _ = _http_get(f"{base}/api/status")
        assert unauth_st == 403

        # /api/status
        st_code, _, st_body = _http_get(f"{base}/api/status", headers=headers)
        assert st_code == 200
        status_json = json.loads(st_body.decode("utf-8"))
        assert any(p["id"] == "opencode" and p["comingSoon"] for p in status_json["providers"])
        assert len(status_json["workspaces"]) >= 2
        assert status_json["storage"]["totalBytes"] > 0
        assert status_json["storage"]["totalHuman"]

        # /api/sessions
        sess_code, _, sess_body = _http_get(
            f"{base}/api/sessions?source=all&limit=50", headers=headers
        )
        assert sess_code == 200
        sess_json = json.loads(sess_body.decode("utf-8"))
        assert sess_json["total"] >= 3
        assert len(sess_json["sessions"]) >= 3

        # /api/session
        one_code, _, one_body = _http_get(
            f"{base}/api/session?source=agy&id={ids['agy_id']}", headers=headers
        )
        assert one_code == 200
        one_json = json.loads(one_body.decode("utf-8"))
        assert one_json["id"] == ids["agy_id"]
        assert len(one_json["turns"]) >= 3

        # /api/copy
        cp_code, cp_json = _http_post_json(
            f"{base}/api/copy",
            headers,
            {
                "items": [{"source": "agy", "id": ids["agy_id"]}],
                "toProvider": "claude",
                "newId": True,
                "overwrite": False,
                "includeThinking": False,
            },
        )
        assert cp_code == 200
        assert cp_json["results"][0]["ok"] is True
        assert "claude --resume" in cp_json["results"][0]["resumeCmd"]
        copied_target_id = cp_json["results"][0]["targetId"]

        # /api/sync-dir
        sd_code, sd_json = _http_post_json(
            f"{base}/api/sync-dir",
            headers,
            {
                "fromProvider": "claude",
                "toProvider": "agy",
                "cwd": str(sb.ws_a),
                "overwrite": False,
                "includeThinking": False,
            },
        )
        assert sd_code == 200
        assert len(sd_json["results"]) >= 1

        # /api/autosync loop-free roundtrip on ws_c
        agy_auto_id = "44444444-4444-4444-8444-444444444444"
        make_agy_db(
            sb.agy_dir,
            agy_auto_id,
            sb.ws_c,
            "AutoSync HTTP Test",
            "AutoSync user message 1",
            "",
            "AutoSync reply 1",
            1763112000,
        )
        as1_code, as1 = _http_post_json(
            f"{base}/api/autosync",
            headers,
            {
                "enabled": False,
                "direction": "both",
                "scope": "watched",
                "watchedFolders": [str(sb.ws_c)],
                "includeThinking": False,
                "runNow": True,
            },
        )
        assert as1_code == 200
        ws_c_files = list((sb.claude_dir / "projects" / claude_slug(sb.ws_c)).glob("*.jsonl"))
        assert len(ws_c_files) == 1
        target_file = ws_c_files[0]
        hist1 = len(as1["autosync"]["history"])

        as2_code, as2 = _http_post_json(f"{base}/api/autosync", headers, {"runNow": True})
        assert as2_code == 200
        assert as2["runResult"]["synced"] == 0
        assert len(as2["autosync"]["history"]) == hist1

        # Enriched syncedTo check
        wsc_q = urllib.parse.quote(str(sb.ws_c), safe="")
        wsc_code, _, wsc_body = _http_get(
            f"{base}/api/sessions?source=agy&cwd={wsc_q}&limit=50", headers=headers
        )
        assert wsc_code == 200
        wsc_sessions = json.loads(wsc_body.decode("utf-8"))["sessions"]
        auto_entry = next(s for s in wsc_sessions if s["id"] == agy_auto_id)
        assert len(auto_entry["syncedTo"]) >= 1
        assert auto_entry["syncedTo"][0]["sizeBytes"] > 0
        assert auto_entry["syncedTo"][0]["title"]

        # Seed heavy + empty + orphans for /api/trim, /api/clean, /api/delete
        heavy_id = "66666666-6666-4666-8666-666666666666"
        empty_id = "77777777-7777-4777-8777-777777777777"
        fx = seed_heavy_and_clean_fixtures(
            sb.agy_dir,
            sb.claude_dir,
            sb.ws_a,
            heavy_id=heavy_id,
            empty_id=empty_id,
            agy_seed_id=ids["agy_id"],
        )

        # Sort by size_desc
        sz_code, _, sz_body = _http_get(
            f"{base}/api/sessions?source=all&sort=size_desc&limit=50", headers=headers
        )
        assert sz_code == 200
        sz_list = json.loads(sz_body.decode("utf-8"))["sessions"]
        assert sz_list[0]["sizeBytes"] >= sz_list[1]["sizeBytes"]

        # /api/trim
        tr_code, tr_json = _http_post_json(
            f"{base}/api/trim",
            headers,
            {
                "source": "agy",
                "id": ids["agy_id"],
                "keepLastTurns": 2,
                "purgeSidecars": True,
                "stripThinking": True,
            },
        )
        assert tr_code == 200
        assert tr_json["ok"] is True and tr_json["freedBytes"] > 20000
        assert not fx["agy_seed_brain"].exists()

        # /api/clean dryRun
        cl_code, cl_json = _http_post_json(
            f"{base}/api/clean",
            headers,
            {
                "source": "all",
                "cleanOrphans": True,
                "deleteEmpty": True,
                "dryRun": True,
            },
        )
        assert cl_code == 200
        assert cl_json["dryRun"] is True
        assert cl_json["orphansCleaned"] >= 2
        assert cl_json["sessionsDeleted"] >= 1
        assert fx["orphan_brain"].exists() and fx["orphan_fh"].exists()

        # /api/delete single + deleteSyncedPeers
        d1_code, d1 = _http_post_json(
            f"{base}/api/delete", headers, {"source": "claude", "id": copied_target_id}
        )
        assert d1_code == 200 and d1["ok"] is True

        d2_code, d2 = _http_post_json(
            f"{base}/api/delete",
            headers,
            {
                "items": [{"source": "agy", "id": agy_auto_id}],
                "deleteSyncedPeers": True,
            },
        )
        assert d2_code == 200
        assert d2["ok"] is True and d2["deletedCount"] >= 2
        assert not Path(target_file).exists()

        # /api/autostart on & off (sandboxed APPDATA)
        startup_lnk = (
            sb.appdata
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
            / "mcas.lnk"
        )
        as_on_code, as_on = _http_post_json(f"{base}/api/autostart", headers, {"on": True})
        assert as_on_code == 200 and as_on["autostart"] is True and startup_lnk.is_file()
        as_off_code, as_off = _http_post_json(f"{base}/api/autostart", headers, {"on": False})
        assert as_off_code == 200 and as_off["autostart"] is False and not startup_lnk.exists()

        # /api/quit
        q_code, q_json = _http_post_json(f"{base}/api/quit", headers, {})
        assert q_code == 200
        assert q_json["message"] == "bye"
        rc = proc.wait(timeout=5)
        assert rc == 0
        assert not (sb.mcas_root / "ui.json").exists()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

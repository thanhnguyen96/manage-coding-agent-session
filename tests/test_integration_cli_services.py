"""Integration tests exercising mcas CLI commands and core service/sync pipelines."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from mcas import __version__
from mcas.cli import main as cli_main
from mcas.services import clean as clean_svc
from mcas.services import delete as delete_svc
from mcas.services import query as query_svc
from mcas.services import trim as trim_svc
from mcas.sync import autosync as autosync_svc
from tests.fixtures.seed import (
    claude_slug,
    make_agy_db,
    make_claude_jsonl,
    make_codex_empty_jsonl,
    make_codex_jsonl,
    seed_heavy_and_clean_fixtures,
)


def _run_cli_json(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, object]:
    rc = cli_main(argv)
    captured = capsys.readouterr()
    return rc, json.loads(captured.out)


def test_empty_sandbox_list_and_version(sandbox, capsys: pytest.CaptureFixture[str]) -> None:
    rc, payload = _run_cli_json(["list", "--json"], capsys)
    assert rc == 0
    assert payload == []

    rc_plain = cli_main(["list"])
    assert rc_plain == 0
    assert "No conversations found" in capsys.readouterr().out

    rc_ver = cli_main(["version"])
    assert rc_ver == 0
    assert capsys.readouterr().out.strip() == __version__


def test_cli_list_inspect_copy_and_sync_dir(
    seeded_sandbox, capsys: pytest.CaptureFixture[str]
) -> None:
    sb, ids = seeded_sandbox

    rc, listed = _run_cli_json(["list", "--json"], capsys)
    assert rc == 0
    assert isinstance(listed, list) and len(listed) == 3
    agy_row = next(r for r in listed if r["id"] == ids["agy_id"])
    assert "Bánh mì Hà Nội" in agy_row["title"]

    rc, insp = _run_cli_json(["inspect", ids["agy_id"], "--source", "agy", "--json"], capsys)
    assert rc == 0
    assert len(insp["turns"]) >= 3

    rc, c2a = _run_cli_json(["claude2agy", ids["claude_a_id"], "--new-id", "--json"], capsys)
    assert rc == 0
    assert c2a["ok"] is True
    assert Path(c2a["path"]).is_file()

    rc, a2c = _run_cli_json(
        ["agy2claude", ids["agy_id"], "--new-id", "--include-thinking", "--json"],
        capsys,
    )
    assert rc == 0
    assert a2c["ok"] is True
    a2c_path = Path(a2c["path"])
    assert a2c_path.is_file()
    assert "<thought>" in a2c_path.read_text(encoding="utf-8")

    rc, copied = _run_cli_json(["copy", ids["agy_id"], "--to", "claude", "--new-id", "--json"], capsys)
    assert rc == 0
    assert copied["ok"] is True
    assert Path(copied["path"]).is_file()

    rc, sync_res = _run_cli_json(
        ["sync-dir", "--from", "claude", "--to", "agy", "--cwd", str(sb.ws_b), "--json"],
        capsys,
    )
    assert rc == 0
    assert len(sync_res["results"]) >= 1

    rc, ws_b_agy = _run_cli_json(
        ["list", "--cwd", str(sb.ws_b), "--source", "agy", "--json"],
        capsys,
    )
    assert rc == 0
    assert len(ws_b_agy) >= 1


def test_cli_unknown_command_and_unsupported_provider(
    seeded_sandbox, capsys: pytest.CaptureFixture[str]
) -> None:
    _sb, ids = seeded_sandbox

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["nope"])
    assert exc_info.value.code != 0

    rc = cli_main(["copy", ids["agy_id"], "--to", "opencode"])
    assert rc == 1
    err_out = capsys.readouterr().err.lower()
    assert "opencode" in err_out or "coming soon" in err_out or "supported" in err_out


def test_autosync_cli_and_loop_free_reverse_propagation(
    sandbox, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, st1 = _run_cli_json(["autosync", "status", "--json"], capsys)
    assert rc == 0
    assert st1["autosync"]["enabled"] is False

    rc, st2 = _run_cli_json(
        ["autosync", "on", "--direction", "both", "--scope", "all", "--interval", "15", "--json"],
        capsys,
    )
    assert rc == 0
    assert st2["autosync"]["enabled"] is True
    assert st2["autosync"]["intervalSec"] == 15

    rc, st3 = _run_cli_json(["autosync", "off", "--json"], capsys)
    assert rc == 0
    assert st3["autosync"]["enabled"] is False

    # Seed isolated ws_c session for loop-free roundtrip verification
    agy_auto_id = "44444444-4444-4444-8444-444444444444"
    make_agy_db(
        sandbox.agy_dir,
        agy_auto_id,
        sandbox.ws_c,
        "AutoSync Loop Test",
        "AutoSync user message 1",
        "",
        "AutoSync reply 1",
        1763112000,
    )

    autosync_svc.update_autosync_config(
        enabled=False,
        direction="both",
        scope="watched",
        watched_folders=[str(sandbox.ws_c)],
        include_thinking=False,
    )
    run1 = autosync_svc.run_autosync_cycle(force_even_if_disabled=True)
    assert run1["synced"] == 2

    ws_c_claude_dir = sandbox.claude_dir / "projects" / claude_slug(sandbox.ws_c)
    files1 = list(ws_c_claude_dir.glob("*.jsonl"))
    assert len(files1) == 1
    target_file = files1[0]
    stat1 = target_file.stat()

    codex_files1 = list((sandbox.codex_dir / "sessions").rglob("*.jsonl"))
    assert len(codex_files1) == 1
    codex_target = codex_files1[0]
    codex_stat1 = codex_target.stat()
    hist1 = len(autosync_svc.get_autosync_dict()["history"])

    # Second immediate cycle MUST be a complete no-op across all 3 providers
    time.sleep(0.05)
    run2 = autosync_svc.run_autosync_cycle(force_even_if_disabled=True)
    stat2 = target_file.stat()
    codex_stat2 = codex_target.stat()
    assert run2["synced"] == 0
    assert stat2.st_mtime_ns == stat1.st_mtime_ns
    assert stat2.st_size == stat1.st_size
    assert codex_stat2.st_mtime_ns == codex_stat1.st_mtime_ns
    assert codex_stat2.st_size == codex_stat1.st_size
    assert len(autosync_svc.get_autosync_dict()["history"]) == hist1

    # Append a new turn on the Claude side and confirm reverse propagation to AGY and Codex
    extra_turns = [
        {
            "type": "user",
            "uuid": "u-auto-2",
            "parentUuid": None,
            "sessionId": target_file.stem,
            "isSidechain": False,
            "timestamp": "2026-11-14T09:50:00.000Z",
            "cwd": str(sandbox.ws_c),
            "message": {"role": "user", "content": "Follow-up turn from Claude side"},
        },
        {
            "type": "assistant",
            "uuid": "a-auto-2",
            "parentUuid": "u-auto-2",
            "sessionId": target_file.stem,
            "isSidechain": False,
            "timestamp": "2026-11-14T09:50:05.000Z",
            "cwd": str(sandbox.ws_c),
            "message": {
                "id": "msg_auto_2",
                "role": "assistant",
                "content": [{"type": "text", "text": "Reply echoed cleanly back to AGY"}],
            },
        },
    ]
    with target_file.open("a", encoding="utf-8") as fh:
        for row in extra_turns:
            fh.write(json.dumps(row) + "\n")

    run3 = autosync_svc.run_autosync_cycle(force_even_if_disabled=True)
    assert run3["synced"] >= 2
    agy_detail = query_svc.get_session_detail("agy", agy_auto_id)
    assert any("Follow-up turn from Claude side" in t["text"] for t in agy_detail["turns"])
    codex_list = query_svc.query_sessions_api(source="codex", cwd=str(sandbox.ws_c))["sessions"]
    assert len(codex_list) == 1
    codex_detail = query_svc.get_session_detail("codex", codex_list[0]["id"])
    assert any("Follow-up turn from Claude side" in t["text"] for t in codex_detail["turns"])

    # Fourth cycle after reverse update must also be loop-free
    hist3 = len(autosync_svc.get_autosync_dict()["history"])
    run4 = autosync_svc.run_autosync_cycle(force_even_if_disabled=True)
    assert run4["synced"] == 0
    assert len(autosync_svc.get_autosync_dict()["history"]) == hist3


def test_size_sort_trim_clean_and_delete_synced_peers(
    seeded_sandbox, capsys: pytest.CaptureFixture[str]
) -> None:
    sb, ids = seeded_sandbox
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

    rc, by_size = _run_cli_json(["list", "--sort", "size", "--json"], capsys)
    assert rc == 0
    assert len(by_size) >= 3
    assert by_size[0]["size_bytes"] >= by_size[1]["size_bytes"]
    assert by_size[0]["size_human"].endswith("B")

    # Trim heavy Claude session via CLI
    rc, trim_res = _run_cli_json(
        ["trim", heavy_id, "--source", "claude", "--keep-ratio", "0.5", "--yes", "--json"],
        capsys,
    )
    assert rc == 0
    assert trim_res["ok"] is True
    assert trim_res["turnsAfter"] < trim_res["turnsBefore"]
    assert trim_res["afterBytes"] < trim_res["beforeBytes"]
    assert trim_res["freedBytes"] > 50000
    assert not fx["heavy_subagents"].exists()
    assert fx["heavy_custom_title"].is_file()

    rc, heavy_insp = _run_cli_json(["inspect", heavy_id, "--source", "claude", "--json"], capsys)
    assert rc == 0
    assert heavy_insp["id"] == heavy_id and len(heavy_insp["turns"]) >= 1

    # Trim AGY session via service
    agy_trim = trim_svc.trim_session(
        source="agy",
        session_id=ids["agy_id"],
        keep_last_turns=2,
        purge_sidecars=True,
        strip_thinking=True,
    )
    assert agy_trim["ok"] is True
    assert agy_trim["freedBytes"] > 20000
    assert not fx["agy_seed_brain"].exists()

    # Clean dry-run vs real clean
    dry_res = clean_svc.clean_storage(
        source="all",
        clean_orphans=True,
        delete_empty=True,
        dry_run=True,
    )
    assert dry_res["dryRun"] is True
    assert dry_res["orphansCleaned"] >= 2
    assert dry_res["sessionsDeleted"] >= 1
    assert fx["orphan_brain"].exists() and fx["orphan_fh"].exists()

    rc, real_clean = _run_cli_json(["clean", "--empty", "--yes", "--json"], capsys)
    assert rc == 0
    assert real_clean["ok"] is True and real_clean["dryRun"] is False
    assert real_clean["orphansCleaned"] >= 2
    assert real_clean["sessionsDeleted"] >= 1
    assert not fx["orphan_brain"].exists()
    assert not fx["orphan_fh"].exists()
    assert not fx["empty_jsonl"].exists()

    # Paired deletion removes both copies
    rc, copy_pair = _run_cli_json(
        ["copy", ids["agy_id"], "--to", "claude", "--new-id", "--json"],
        capsys,
    )
    assert rc == 0
    paired_claude_path = Path(copy_pair["path"])
    assert paired_claude_path.is_file()

    del_res = delete_svc.delete_sessions_batch(
        [{"source": "agy", "id": ids["agy_id"]}],
        delete_synced_peers=True,
    )
    assert del_res["ok"] is True
    assert del_res["deletedCount"] >= 2
    assert not paired_claude_path.exists()


def test_codex_cli_cross_provider_sync_and_3way_shared_autosync(
    seeded_sandbox, capsys: pytest.CaptureFixture[str]
) -> None:
    sb, ids = seeded_sandbox
    codex_id = "91111111-1111-4111-8111-111111111111"
    codex_empty_id = "92222222-2222-4222-8222-222222222222"
    make_codex_jsonl(
        sb.codex_dir,
        codex_id,
        sb.ws_a,
        "Phở bò Codex 2026 prompt",
        "Codex internal thought",
        "Codex reply TOKEN_CODEX_7755",
        "2026-11-15T09:00:00.000Z",
        include_tools=True,
        upsert_sqlite=True,
    )
    make_codex_empty_jsonl(sb.codex_dir, codex_empty_id, sb.ws_a, "2026-11-15T09:01:00.000Z")

    # CLI list & inspect for codex
    rc, codex_rows = _run_cli_json(["list", "--source", "codex", "--json"], capsys)
    assert rc == 0
    assert len(codex_rows) == 1
    assert codex_rows[0]["id"] == codex_id

    rc, codex_insp = _run_cli_json(["inspect", codex_id, "--source", "codex", "--json"], capsys)
    assert rc == 0
    assert codex_insp["resumeCmd"] == f"codex resume {codex_id}"
    assert any("TOKEN_CODEX_7755" in t["text"] for t in codex_insp["turns"])

    # Cross-provider copy: claude <-> codex and agy <-> codex
    rc, cl2cx = _run_cli_json(
        ["copy", ids["claude_a_id"], "--from", "claude", "--to", "codex", "--new-id", "--json"],
        capsys,
    )
    assert rc == 0 and cl2cx["ok"] is True
    assert Path(cl2cx["path"]).is_file()
    assert cl2cx["resumeCmd"].startswith("codex resume ")

    rc, cx2cl = _run_cli_json(
        ["copy", codex_id, "--from", "codex", "--to", "claude", "--new-id", "--include-thinking", "--json"],
        capsys,
    )
    assert rc == 0 and cx2cl["ok"] is True
    assert "<thought>" in Path(cx2cl["path"]).read_text(encoding="utf-8")

    rc, ag2cx = _run_cli_json(
        ["copy", ids["agy_id"], "--from", "agy", "--to", "codex", "--new-id", "--include-thinking", "--json"],
        capsys,
    )
    assert rc == 0 and ag2cx["ok"] is True
    assert "<thought>" in Path(ag2cx["path"]).read_text(encoding="utf-8")

    rc, cx2ag = _run_cli_json(
        ["copy", codex_id, "--from", "codex", "--to", "agy", "--new-id", "--json"],
        capsys,
    )
    assert rc == 0 and cx2ag["ok"] is True
    assert Path(cx2ag["path"]).is_file()

    # Sync-dir codex -> claude on ws_a
    rc, sd_res = _run_cli_json(
        ["sync-dir", "--from", "codex", "--to", "claude", "--cwd", str(sb.ws_a), "--json"],
        capsys,
    )
    assert rc == 0 and len(sd_res["results"]) >= 1

    # Trim codex session & clean empty codex session via CLI
    rc, tr_res = _run_cli_json(
        ["trim", codex_id, "--source", "codex", "--keep-turns", "2", "--yes", "--json"],
        capsys,
    )
    assert rc == 0 and tr_res["ok"] is True

    rc, cl_res = _run_cli_json(
        ["clean", "--source", "codex", "--empty", "--yes", "--json"],
        capsys,
    )
    assert rc == 0 and cl_res["ok"] is True and cl_res["sessionsDeleted"] >= 1

    # Paired delete across codex -> claude/agy peers
    del_res = delete_svc.delete_sessions_batch(
        [{"source": "codex", "id": codex_id}],
        delete_synced_peers=True,
    )
    assert del_res["ok"] is True
    assert del_res["deletedCount"] >= 2
    assert not Path(cx2cl["path"]).exists()

    # 3-way shared_unpaired autosync on ws_c (same UUID across agy, claude, codex)
    shared_id = "93333333-3333-4333-8333-333333333333"
    make_agy_db(
        sb.agy_dir,
        shared_id,
        sb.ws_c,
        "Shared 3-way",
        "Shared prompt",
        "",
        "Shared AGY reply",
        1763113000,
    )
    make_claude_jsonl(
        sb.claude_dir,
        shared_id,
        sb.ws_c,
        "Shared prompt",
        "Shared Claude reply",
        "2026-11-14T09:30:00.000Z",
    )
    make_codex_jsonl(
        sb.codex_dir,
        shared_id,
        sb.ws_c,
        "Shared prompt",
        "",
        "Shared Codex reply",
        "2026-11-14T09:29:00.000Z",
        include_tools=False,
    )
    autosync_svc.update_autosync_config(
        enabled=False,
        direction="both",
        scope="watched",
        watched_folders=[str(sb.ws_c)],
        include_thinking=False,
    )
    r1 = autosync_svc.run_autosync_cycle(force_even_if_disabled=True)
    assert r1["errors"] == 0
    r2 = autosync_svc.run_autosync_cycle(force_even_if_disabled=True)
    assert r2["synced"] == 0 and r2["errors"] == 0

    # Verify pair-specific and 1-way Codex autosync routes normalize and run cleanly
    cfg_pair = autosync_svc.update_autosync_config(direction="agy<->codex")
    assert cfg_pair["direction"] == "agy_codex"
    r_pair = autosync_svc.run_autosync_cycle(force_even_if_disabled=True)
    assert r_pair["synced"] == 0 and r_pair["errors"] == 0

    cfg_oneway = autosync_svc.update_autosync_config(direction="codex2claude")
    assert cfg_oneway["direction"] == "codex2claude"
    r_oneway = autosync_svc.run_autosync_cycle(force_even_if_disabled=True)
    assert r_oneway["synced"] == 0 and r_oneway["errors"] == 0

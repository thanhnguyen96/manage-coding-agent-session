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
from tests.fixtures.seed import claude_slug, make_agy_db, seed_heavy_and_clean_fixtures


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
    assert run1["synced"] == 1

    ws_c_claude_dir = sandbox.claude_dir / "projects" / claude_slug(sandbox.ws_c)
    files1 = list(ws_c_claude_dir.glob("*.jsonl"))
    assert len(files1) == 1
    target_file = files1[0]
    stat1 = target_file.stat()
    hist1 = len(autosync_svc.get_autosync_dict()["history"])

    # Second immediate cycle MUST be a complete no-op
    time.sleep(0.05)
    run2 = autosync_svc.run_autosync_cycle(force_even_if_disabled=True)
    stat2 = target_file.stat()
    assert run2["synced"] == 0
    assert stat2.st_mtime_ns == stat1.st_mtime_ns
    assert stat2.st_size == stat1.st_size
    assert len(autosync_svc.get_autosync_dict()["history"]) == hist1

    # Append a new turn on the Claude side and confirm reverse propagation
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
    assert run3["synced"] >= 1
    agy_detail = query_svc.get_session_detail("agy", agy_auto_id)
    assert any("Follow-up turn from Claude side" in t["text"] for t in agy_detail["turns"])

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

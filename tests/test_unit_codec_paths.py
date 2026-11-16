"""Unit tests for protobuf codec, path/slug conversion, helpers, and canonical hash."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from mcas import config
from mcas.errors import SyncError
from mcas.models import (
    CanonicalSession,
    CanonicalToolCall,
    CanonicalToolResult,
    CanonicalTurn,
    SessionSummary,
    canonical_session_hash,
)
from mcas.providers import get_provider, get_supported_providers
from mcas.services import clean as clean_svc
from mcas.services import delete as delete_svc
from mcas.services import query as query_svc
from mcas.services import trim as trim_svc
from mcas.sync import state as sync_state
from mcas.util import pathconv, protobuf, text, timefmt
from tests.fixtures.seed import (
    make_agy_db,
    make_claude_jsonl,
    make_codex_empty_jsonl,
    make_codex_jsonl,
    make_codex_state_db,
)


def test_varint_and_wire_codec_roundtrip() -> None:
    for v in (0, 1, 127, 128, 300, 2**31 - 1, 2**48 + 7):
        raw = protobuf.pb_varint(1, v)
        fields = protobuf.decode_pb(raw)
        assert fields == [(1, 0, v)]

    neg = protobuf.pb_varint(2, -1)
    assert protobuf.decode_pb(neg) == [(2, 0, (1 << 64) - 1)]

    ts = datetime(2026, 11, 14, 9, 30, 15, 123456, tzinfo=timezone.utc)
    nested = protobuf.pb_str(1, "hello") + protobuf.pb_varint(2, 42)
    blob = (
        protobuf.pb_str(1, "Xin chào")
        + protobuf.pb_bytes(2, b"\x00\x01\x02")
        + protobuf.pb_msg(3, nested)
        + protobuf.pb_timestamp(4, ts)
        + protobuf.pb_str(1, "world")
    )
    fields = protobuf.decode_pb(blob)
    assert protobuf.pb_str_val(fields, 1) == "Xin chào"
    assert protobuf.pb_all(fields, 1, 2) == ["Xin chào".encode("utf-8"), b"world"]
    assert protobuf.pb_first(fields, 2, 2) == b"\x00\x01\x02"

    sub_bytes = protobuf.pb_first(fields, 3, 2)
    assert isinstance(sub_bytes, bytes)
    sub_fields = protobuf.decode_pb(sub_bytes)
    assert protobuf.pb_str_val(sub_fields, 1) == "hello"
    assert protobuf.pb_first(sub_fields, 2, 0) == 42

    decoded_ts = protobuf.pb_timestamp_val(fields, 4)
    assert decoded_ts is not None
    assert int(decoded_ts.timestamp()) == int(ts.timestamp())
    assert abs(decoded_ts.microsecond - ts.microsecond) <= 1


def test_decode_pb_handles_fixed64_fixed32_and_corrupt_data() -> None:
    import struct

    data = (
        protobuf.encode_varint((1 << 3) | 1)
        + struct.pack("<Q", 0x1122334455667788)
        + protobuf.encode_varint((2 << 3) | 5)
        + struct.pack("<I", 0xAABBCCDD)
    )
    assert protobuf.decode_pb(data) == [
        (1, 1, 0x1122334455667788),
        (2, 5, 0xAABBCCDD),
    ]

    # Truncated length-delimited payload should stop gracefully without raising
    truncated = protobuf.pb_str(1, "valid") + b"\x12\x64abc"
    partial = protobuf.decode_pb(truncated)
    assert protobuf.pb_str_val(partial, 1) == "valid"

    # Unknown wire type 7 stops gracefully
    assert protobuf.decode_pb(b"\x0f\x01") == []


def test_path_and_slug_conversion() -> None:
    assert pathconv.windows_path_to_slug(r"C:\_personal\_empty") == "C---personal--empty"
    assert pathconv.windows_path_to_slug("c:\\") == "C--"
    assert pathconv.windows_path_to_slug("c:") == "C--"
    assert pathconv.normalize_windows_path("d:") == "D:\\"
    assert pathconv.normalize_windows_path(r"\\?\D:\Work\ProjA") == pathconv.normalize_windows_path(
        r"D:\Work\ProjA"
    )
    assert pathconv.normalize_windows_path(r"\\?\d:") == "D:\\"
    assert pathconv.normalize_windows_path(r"\\?\UNC\server\share\proj") == r"\\server\share\proj"

    assert pathconv.paths_match(r"c:\Work\ProjA\\", r"C:\work\proja")
    assert not pathconv.paths_match(r"C:\Work\ProjA", r"C:\Work\ProjB")
    assert not pathconv.paths_match(None, r"C:\Work\ProjA")

    uri = pathconv.windows_path_to_file_uri(r"C:\_personal\my project")
    assert uri.startswith("file:///C:/")
    roundtrip = pathconv.file_uri_to_windows_path(uri)
    assert roundtrip is not None
    assert pathconv.paths_match(roundtrip, r"C:\_personal\my project")

    assert pathconv.file_uri_to_windows_path("file:///c%3A/foo%20bar/baz") == pathconv.normalize_windows_path(
        r"C:\foo bar\baz"
    )
    assert pathconv.file_uri_to_windows_path("https://example.com") is None
    assert pathconv.parse_workspace_uris('["file:///C:/a", "file:///C:/b"]') == [
        "file:///C:/a",
        "file:///C:/b",
    ]
    assert pathconv.parse_workspace_uris("") == []


def test_time_and_text_helpers() -> None:
    dt_z = timefmt.parse_ts("2026-11-14T09:10:00.123456789Z")
    assert dt_z.year == 2026 and dt_z.tzinfo == timezone.utc
    assert timefmt.format_iso_utc(dt_z) == "2026-11-14T09:10:00.123Z"
    assert timefmt.format_agy_sql_ts(dt_z) == "2026-11-14 09:10:00.123456+00:00"
    assert timefmt.dt_to_ns(dt_z) > 0

    dt_ms = timefmt.parse_ts(1763110800123)
    assert int(dt_ms.timestamp()) == 1763110800

    assert text.is_valid_uuid("11111111-1111-4111-8111-111111111111")
    assert not text.is_valid_uuid("not-a-uuid")

    assert text.sanitize_tool_name("my.tool/name!") == "my_tool_name"
    assert text.sanitize_tool_name("...") == "unknown_tool"

    mapper = text.ToolIdMapper()
    assert mapper.allocate("toolu_1") == "toolu_1"
    # Collision gets remapped uniquely
    remapped = mapper.allocate("toolu_1")
    assert remapped != "toolu_1"
    assert len(remapped) <= 64
    # Invalid characters get sanitized
    san = mapper.allocate("bad:id/with spaces")
    assert ":" not in san and " " not in san

    assert text.parse_tool_input_dict('{"a": 1}') == {"a": 1}
    assert text.parse_tool_input_dict("[1, 2]") == {"value": [1, 2]}
    assert text.parse_tool_input_dict("{bad") == {"raw": "{bad"}

    assert text.truncate_preview("  hello   world  ", 20) == "hello world"
    assert text.truncate_preview("a" * 30, 10) == "aaaaaaa..."
    assert text.derive_title("", "First line") == "First line"
    assert text.derive_title("", "") == "Imported Conversation"
    assert text.format_bytes(512) == "512 B"
    assert text.format_bytes(2048) == "2.0 KB"


def test_canonical_session_hash_determinism_and_sensitivity() -> None:
    t1 = datetime(2026, 11, 14, 9, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 14, 10, 0, 0, tzinfo=timezone.utc)
    base = CanonicalSession(
        session_id="11111111-1111-4111-8111-111111111111",
        source="agy",
        title="Test Session",
        preview="Hi",
        cwd=r"C:\Work\ProjA",
        created_at=t1,
        updated_at=t1,
        turns=[
            CanonicalTurn(role="user", timestamp=t1, text="Hi"),
            CanonicalTurn(
                role="assistant",
                timestamp=t1,
                thinking="think",
                text="Answer",
                tool_calls=[CanonicalToolCall(id="c1", name="read", arguments_json="{}")],
            ),
            CanonicalTurn(
                role="tool_result",
                timestamp=t1,
                tool_results=[
                    CanonicalToolResult(
                        tool_use_id="c1",
                        name="read",
                        arguments_json="{}",
                        content="ok",
                        is_error=False,
                    )
                ],
            ),
        ],
    )
    # Different session_id, source, and timestamps produce identical content hash
    clone = CanonicalSession(
        session_id="22222222-2222-4222-8222-222222222222",
        source="claude",
        title="Test Session",
        preview="Different preview",
        cwd=r"c:\work\proja\\",
        created_at=t2,
        updated_at=t2,
        turns=[
            CanonicalTurn(role="user", timestamp=t2, text="Hi"),
            CanonicalTurn(
                role="assistant",
                timestamp=t2,
                thinking="think",
                text="Answer",
                tool_calls=[CanonicalToolCall(id="c1", name="read", arguments_json="{}")],
            ),
            CanonicalTurn(
                role="tool_result",
                timestamp=t2,
                tool_results=[
                    CanonicalToolResult(
                        tool_use_id="c1",
                        name="read",
                        arguments_json="{}",
                        content="ok",
                        is_error=False,
                    )
                ],
            ),
        ],
    )
    assert canonical_session_hash(base) == canonical_session_hash(clone)

    # Mutating text changes the hash
    mutated = CanonicalSession(
        session_id=clone.session_id,
        source=clone.source,
        title=clone.title,
        preview=clone.preview,
        cwd=clone.cwd,
        created_at=clone.created_at,
        updated_at=clone.updated_at,
        turns=[CanonicalTurn(role="user", timestamp=t2, text="Changed"), *clone.turns[1:]],
    )
    assert canonical_session_hash(base) != canonical_session_hash(mutated)


def test_config_env_overrides_and_providers(sandbox) -> None:
    assert config.get_mcas_root() == sandbox.mcas_root
    assert config.get_agy_cli_dir() == sandbox.agy_dir
    assert config.get_agy_ide_dir() == sandbox.agy_ide_dir
    assert config.get_claude_dir() == sandbox.claude_dir
    assert config.get_claude_projects_dir() == sandbox.claude_dir / "projects"
    assert config.get_codex_dir() == sandbox.codex_dir
    assert config.get_codex_sessions_dir() == sandbox.codex_dir / "sessions"
    assert config.get_codex_archived_dir() == sandbox.codex_dir / "archived_sessions"
    assert config.get_sync_state_path() == sandbox.mcas_root / "sync_state.json"
    assert config.get_ui_json_path() == sandbox.mcas_root / "ui.json"
    assert config.get_default_port() == int(sandbox.env["MCAS_PORT"])

    supported_ids = {p.id for p in get_supported_providers()}
    assert supported_ids == {"agy", "claude", "codex"}
    assert get_provider("agy").supported is True
    assert get_provider("claude").supported is True
    codex = get_provider("codex")
    assert codex.supported is True and codex.coming_soon is False
    assert codex.resume_command("abc-123") == "codex resume abc-123"
    opencode = get_provider("opencode", require_supported=False)
    assert opencode.coming_soon is True
    with pytest.raises(SyncError):
        get_provider("opencode", require_supported=True)


def test_grouping_agnostic_synced_lookup_and_sort(sandbox) -> None:
    shared_id = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
    make_agy_db(
        sandbox.agy_dir,
        shared_id,
        sandbox.ws_a,
        "Shared AGY",
        "Hi",
        "",
        "Bye",
        1763110800,
    )
    make_claude_jsonl(
        sandbox.claude_dir,
        shared_id,
        sandbox.ws_a,
        "Hi",
        "Bye",
        "2026-11-14T09:05:00.000Z",
    )

    # Also test explicit ledger pairing between distinct IDs
    agy_other = "bbbbbbbb-1111-4111-8111-bbbbbbbbbbbb"
    claude_other = "cccccccc-1111-4111-8111-cccccccccccc"
    make_agy_db(
        sandbox.agy_dir,
        agy_other,
        sandbox.ws_b,
        "Paired AGY",
        "Hello",
        "",
        "World",
        1763110900,
    )
    make_claude_jsonl(
        sandbox.claude_dir,
        claude_other,
        sandbox.ws_b,
        "Hello",
        "World",
        "2026-11-14T09:15:00.000Z",
    )
    state = sync_state._load_state()
    sync_state._upsert_pair(
        state,
        source="agy",
        source_id=agy_other,
        target="claude",
        target_id=claude_other,
        src_fp=(1, 2),
        src_hash="h",
        tgt_fp=(1, 2),
        tgt_hash="h",
        cwd=str(sandbox.ws_b),
        title="Paired",
        now=datetime(2026, 11, 14, 9, 15, tzinfo=timezone.utc),
    )
    sync_state._save_state(state)

    peers_shared = delete_svc._resolve_synced_peers("agy", shared_id)
    assert ("claude", shared_id) in peers_shared

    peers_paired = delete_svc._resolve_synced_peers("agy", agy_other)
    assert ("claude", claude_other) in peers_paired

    api_res = query_svc.query_sessions_api(source="all", limit=50, sort="size_desc")
    sessions = api_res["sessions"]
    assert len(sessions) == 4
    by_pair = {(s["source"], s["id"]): s for s in sessions}
    assert any(
        link["provider"] == "claude" and link["id"] == shared_id and link["sizeBytes"] > 0
        for link in by_pair[("agy", shared_id)]["syncedTo"]
    )
    assert any(
        link["provider"] == "claude" and link["id"] == claude_other
        for link in by_pair[("agy", agy_other)]["syncedTo"]
    )

    # Sort helper checks
    t1 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    summaries = [
        SessionSummary("1", "agy", t1, "small-old", "", None, Path("a"), 2, size_bytes=100),
        SessionSummary("2", "claude", t2, "big-new", "", None, Path("b"), 10, size_bytes=5000),
    ]
    query_svc._sort_sessions(summaries, "size_asc")
    assert [s.session_id for s in summaries] == ["1", "2"]
    query_svc._sort_sessions(summaries, "turns")
    assert [s.session_id for s in summaries] == ["2", "1"]


def test_codex_codec_injected_context_tools_thinking_and_event_fallback(sandbox) -> None:
    codex = get_provider("codex")
    sid_main = "12345678-1234-4123-8123-1234567890ab"
    ws_str = str(sandbox.ws_a)
    unc_ws = (r"\\?\\"[:-1] + ws_str) if len(ws_str) >= 2 and ws_str[1] == ":" else ws_str
    jpath = make_codex_jsonl(
        sandbox.codex_dir,
        sid_main,
        sandbox.ws_a,
        "Explain Codex rollout parsing",
        "Inspecting rollout records",
        "Codex final answer",
        "2026-11-15T10:00:00.000Z",
        include_tools=True,
        include_injected=True,
        raw_cwd=unc_ws,
    )
    sess = codex.read_session(jpath)
    assert sess.session_id == sid_main
    assert sess.source == "codex"
    assert pathconv.paths_match(sess.cwd, str(sandbox.ws_a))

    user_turns = [t for t in sess.turns if t.role == "user"]
    assert len(user_turns) == 1
    assert user_turns[0].text == "Explain Codex rollout parsing"
    assert "AGENTS.md" not in user_turns[0].text
    assert "<environment_context>" not in user_turns[0].text
    assert not any("System developer prompt" in t.text for t in sess.turns)

    asst_turns = [t for t in sess.turns if t.role == "assistant"]
    assert any(t.thinking == "Inspecting rollout records" for t in asst_turns)
    assert not any("OPAQUE_SECRET_BLOB" in (t.thinking + t.text) for t in asst_turns)
    all_calls = [tc for t in asst_turns for tc in t.tool_calls]
    assert [tc.name for tc in all_calls] == ["read_file", "apply_patch", "shell"]
    assert json.loads(all_calls[1].arguments_json) == {"raw": "*** Begin Patch\n*** End Patch"}
    tool_results = [tr for t in sess.turns if t.role == "tool_result" for tr in t.tool_results]
    assert len(tool_results) == 3
    assert tool_results[1].content == "Patched"
    assert any(t.text == "Codex final answer" for t in asst_turns)

    # Event-only fallback when rollout has no response_item messages
    sid_ev = "22345678-1234-4123-8123-1234567890ab"
    ev_path = make_codex_jsonl(
        sandbox.codex_dir,
        sid_ev,
        sandbox.ws_b,
        "Event fallback prompt",
        "Event reasoning trace",
        "Event agent reply",
        "2026-11-15T10:05:00.000Z",
        event_only=True,
    )
    ev_sess = codex.read_session(ev_path)
    assert [t.role for t in ev_sess.turns] == ["user", "assistant", "assistant"]
    assert ev_sess.turns[0].text == "Event fallback prompt"
    assert ev_sess.turns[1].thinking == "Event reasoning trace"
    assert ev_sess.turns[2].text == "Event agent reply"


def test_codex_subagent_archived_title_overrides_and_find_session(sandbox) -> None:
    codex = get_provider("codex")
    sid_user = "32345678-1234-4123-8123-1234567890ab"
    sid_sub = "42345678-1234-4123-8123-1234567890ab"
    sid_arch = "52345678-1234-4123-8123-1234567890ab"

    make_codex_jsonl(
        sandbox.codex_dir,
        sid_user,
        sandbox.ws_a,
        "Primary Codex chat",
        "",
        "Primary reply",
        "2026-11-15T11:00:00.000Z",
        include_tools=False,
    )
    make_codex_jsonl(
        sandbox.codex_dir,
        sid_sub,
        sandbox.ws_a,
        "Subagent task",
        "",
        "Subagent reply",
        "2026-11-15T11:01:00.000Z",
        subagent=True,
        subagent_depth=2,
        include_tools=False,
    )
    make_codex_jsonl(
        sandbox.codex_dir,
        sid_arch,
        sandbox.ws_a,
        "Archived Codex chat",
        "",
        "Archived reply",
        "2026-11-15T11:02:00.000Z",
        archived=True,
        include_tools=False,
    )

    # JSON-string subagent source vs user fork (forked_from_id alone is NOT a subagent)
    sid_str_sub = "55345678-1234-4123-8123-1234567890ab"
    sid_fork = "56345678-1234-4123-8123-1234567890ab"
    str_sub_path = (
        sandbox.codex_dir / "sessions" / "2026" / "11" / "15"
        / f"rollout-2026-11-15T11-03-00-{sid_str_sub}.jsonl"
    )
    str_sub_path.parent.mkdir(parents=True, exist_ok=True)
    str_sub_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-11-15T11:03:00.000Z",
                        "type": "session_meta",
                        "payload": {
                            "id": sid_str_sub,
                            "cwd": str(sandbox.ws_a),
                            "source": json.dumps({"subagent": {"other": "guardian"}}),
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-11-15T11:03:01.000Z",
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "Guardian check"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fork_path = (
        sandbox.codex_dir / "sessions" / "2026" / "11" / "15"
        / f"rollout-2026-11-15T11-04-00-{sid_fork}.jsonl"
    )
    fork_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-11-15T11:04:00.000Z",
                        "type": "session_meta",
                        "payload": {
                            "id": sid_fork,
                            "cwd": str(sandbox.ws_a),
                            "source": "cli",
                            "forked_from_id": sid_user,
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-11-15T11:04:01.000Z",
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "User forked chat"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # session_index.jsonl title override
    (sandbox.codex_dir / "session_index.jsonl").write_text(
        json.dumps({"id": sid_user, "thread_name": "Custom Codex Index Title"}) + "\n",
        encoding="utf-8",
    )

    default_list = codex.list_sessions(include_all=False)
    assert {s.session_id for s in default_list} == {sid_user, sid_fork}
    user_sum = next(s for s in default_list if s.session_id == sid_user)
    assert user_sum.title == "Custom Codex Index Title"

    all_list = codex.list_sessions(include_all=True)
    by_id = {s.session_id: s for s in all_list}
    assert set(by_id) == {sid_user, sid_sub, sid_arch, sid_str_sub, sid_fork}
    assert "depth=2" in by_id[sid_sub].extra
    assert "depth=1" in by_id[sid_str_sub].extra
    assert "depth=" not in by_id[sid_fork].extra
    assert "archived" in by_id[sid_arch].extra

    # Prefix lookup & path traversal guard
    found_path, found_sum = codex.find_session("32345678")
    assert found_path.is_file() and found_sum is not None and found_sum.session_id == sid_user
    with pytest.raises(SyncError):
        codex.find_session("../escape")


def test_codex_write_sqlite_cascade_trim_clean_and_delete(sandbox) -> None:
    codex = get_provider("codex")
    db_path = make_codex_state_db(sandbox.codex_dir)

    sid = "62345678-1234-4123-8123-1234567890ab"
    paginated_id = "72345678-1234-4123-8123-1234567890ab"
    empty_id = "82345678-1234-4123-8123-1234567890ab"

    make_codex_jsonl(
        sandbox.codex_dir,
        sid,
        sandbox.ws_a,
        "Initial prompt",
        "Long thinking " * 25,
        "Initial answer",
        "2026-11-15T12:00:00.000Z",
        include_tools=True,
        upsert_sqlite=True,
    )
    make_codex_empty_jsonl(sandbox.codex_dir, empty_id, sandbox.ws_a, "2026-11-15T12:05:00.000Z")

    # Add FK child row under threads(sid) and a paginated thread without rollout_path on disk
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(
        "INSERT INTO thread_dynamic_tools (thread_id, position, name) VALUES (?, 0, 'my_dyn_tool')",
        (sid,),
    )
    con.execute(
        """
        INSERT INTO threads (
            id, rollout_path, created_at, updated_at, source, model_provider,
            cwd, title, archived, first_user_message, preview, thread_source,
            created_at_ms, updated_at_ms, recency_at, recency_at_ms, history_mode
        )
        VALUES (?, ?, 1763110800, 1763110800, 'cli', 'openai', ?, 'Paginated Thread', 0, 'Hi', 'Hi', 'user', 1763110800000, 1763110800000, 1763110800, 1763110800000, 'paginated')
        """,
        (paginated_id, str(sandbox.codex_dir / "missing_paginated.jsonl"), str(sandbox.ws_a)),
    )
    con.commit()
    con.close()

    # Overwrite with include_thinking=True -> folds <thought> into output_text and preserves FK child row
    sess = codex.read_session(codex.find_session(sid)[0])
    written_id, out_path, regenerated = codex.write_session(
        sess,
        target_cwd=str(sandbox.ws_a),
        overwrite=True,
        include_thinking=True,
    )
    assert written_id == sid and not regenerated and out_path.is_file()
    raw_written = out_path.read_text(encoding="utf-8")
    assert "<thought>" in raw_written

    con = sqlite3.connect(str(db_path))
    dyn_rows = con.execute(
        "SELECT name FROM thread_dynamic_tools WHERE thread_id = ?", (sid,)
    ).fetchall()
    con.close()
    assert dyn_rows == [("my_dyn_tool",)], "FK child row must survive write_session upsert"

    # Trim in-place
    tr = trim_svc.trim_session(
        source="codex",
        session_id=sid,
        keep_last_turns=2,
        strip_thinking=True,
        max_tool_output_chars=128,
    )
    assert tr["ok"] is True

    # Clean empty session; paginated row without rollout_path MUST NOT be deleted
    cl = clean_svc.clean_storage(
        source="codex",
        clean_orphans=True,
        delete_empty=True,
        dry_run=False,
    )
    assert cl["sessionsDeleted"] >= 1
    con = sqlite3.connect(str(db_path))
    pag_row = con.execute(
        "SELECT id, history_mode FROM threads WHERE id = ?", (paginated_id,)
    ).fetchone()
    con.close()
    assert pag_row == (paginated_id, "paginated")

    # Delete removes rollout file, prunes empty YYYY/MM/DD dirs, and deletes threads row
    del_res = codex.delete_session(sid)
    assert del_res["deleted"] is True
    assert not out_path.exists()
    assert not out_path.parent.exists()
    con = sqlite3.connect(str(db_path))
    assert con.execute("SELECT 1 FROM threads WHERE id = ?", (sid,)).fetchone() is None
    con.close()

"""Unit tests for protobuf codec, path/slug conversion, helpers, and canonical hash."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
from mcas.services import delete as delete_svc
from mcas.services import query as query_svc
from mcas.sync import state as sync_state
from mcas.util import pathconv, protobuf, text, timefmt
from tests.fixtures.seed import make_agy_db, make_claude_jsonl


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
    assert config.get_sync_state_path() == sandbox.mcas_root / "sync_state.json"
    assert config.get_ui_json_path() == sandbox.mcas_root / "ui.json"
    assert config.get_default_port() == int(sandbox.env["MCAS_PORT"])

    supported_ids = {p.id for p in get_supported_providers()}
    assert supported_ids == {"agy", "claude"}
    assert get_provider("agy").supported is True
    assert get_provider("claude").supported is True
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

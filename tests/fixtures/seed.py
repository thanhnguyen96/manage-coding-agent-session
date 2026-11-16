"""Reusable sandbox seed helpers for unit, integration, and E2E tests."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path


def enc_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def pb_varint(fn: int, value: int) -> bytes:
    return enc_varint((fn << 3) | 0) + enc_varint(value)


def pb_bytes(fn: int, data: bytes) -> bytes:
    return enc_varint((fn << 3) | 2) + enc_varint(len(data)) + data


def pb_str(fn: int, text: str) -> bytes:
    return pb_bytes(fn, text.encode("utf-8"))


def pb_ts(fn: int, sec: int) -> bytes:
    return pb_bytes(fn, pb_varint(1, sec) + pb_varint(2, 0))


def win_uri(path: str | Path) -> str:
    return Path(os.path.abspath(str(path))).as_uri()


def claude_slug(path: str | Path) -> str:
    norm = os.path.normpath(os.path.abspath(str(path)))
    if len(norm) >= 2 and norm[1] == ":" and norm[0].isalpha():
        norm = norm[0].upper() + norm[1:]
    return re.sub(r"[^a-zA-Z0-9]", "-", norm)


def make_agy_db(
    agy_dir: Path,
    conv_id: str,
    ws_path: str | Path,
    title: str,
    user_text: str,
    thought_text: str,
    reply_text: str,
    sec: int,
    *,
    app_data_dir: str = "antigravity-cli",
    summary_db_dir: Path | None = None,
) -> Path:
    """Create a realistic agy conversation SQLite DB and conversation_summaries row."""
    conv_dir = agy_dir / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    db_path = conv_dir / f"{conv_id}.db"
    traj_id = "traj-" + conv_id[:8]
    uri = win_uri(ws_path)
    traj_meta = pb_bytes(1, pb_str(1, uri)) + pb_ts(2, sec) + pb_str(7, uri) + pb_varint(17, 0)

    m0 = pb_ts(1, sec) + pb_varint(2, 4) + pb_str(5, traj_id) + pb_varint(6, 0) + pb_str(7, conv_id)
    ui = pb_str(2, user_text) + pb_bytes(3, pb_str(1, user_text))
    s0 = pb_varint(1, 14) + pb_varint(4, 3) + pb_bytes(5, m0) + pb_bytes(19, ui)

    m1 = (
        pb_ts(1, sec + 2)
        + pb_varint(2, 2)
        + pb_str(5, traj_id)
        + pb_varint(6, 1)
        + pb_str(7, conv_id)
    )
    tc = pb_str(1, "call_seed_1") + pb_str(2, "read_file") + pb_str(3, '{"path":"README.md"}')
    pr1 = pb_str(3, thought_text) + pb_str(6, "bot-1") + pb_bytes(7, tc) + pb_varint(12, 2)
    s1 = pb_varint(1, 15) + pb_varint(4, 3) + pb_bytes(5, m1) + pb_bytes(20, pr1)

    m2 = (
        pb_ts(1, sec + 3)
        + pb_varint(2, 5)
        + pb_bytes(4, tc)
        + pb_str(5, traj_id)
        + pb_varint(6, 2)
        + pb_str(7, conv_id)
    )
    gen = pb_bytes(2, pb_str(1, "README content ok"))
    s2 = pb_varint(1, 132) + pb_varint(4, 3) + pb_bytes(5, m2) + pb_bytes(140, gen)

    m3 = (
        pb_ts(1, sec + 5)
        + pb_varint(2, 2)
        + pb_str(5, traj_id)
        + pb_varint(6, 3)
        + pb_str(7, conv_id)
    )
    pr2 = pb_str(6, "bot-2") + pb_str(8, reply_text) + pb_varint(12, 2)
    s3 = pb_varint(1, 15) + pb_varint(4, 3) + pb_bytes(5, m3) + pb_bytes(20, pr2)

    con = sqlite3.connect(str(db_path))
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS trajectory_meta (
            trajectory_id text PRIMARY KEY,
            cascade_id text,
            trajectory_type integer,
            source integer
        );
        CREATE TABLE IF NOT EXISTS steps (
            idx integer PRIMARY KEY,
            step_type integer NOT NULL DEFAULT 0,
            status integer NOT NULL DEFAULT 0,
            has_subtrajectory numeric NOT NULL DEFAULT false,
            metadata blob,
            error_details blob,
            permissions blob,
            task_details blob,
            render_info blob,
            step_payload blob,
            step_format integer NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS trajectory_metadata_blob (
            id text PRIMARY KEY,
            data blob
        );
        """
    )
    con.execute("INSERT INTO trajectory_meta VALUES (?, ?, 4, 17)", (traj_id, conv_id))
    con.execute("INSERT INTO trajectory_metadata_blob VALUES ('main', ?)", (traj_meta,))
    con.executemany(
        "INSERT INTO steps (idx, step_type, status, has_subtrajectory, metadata, step_payload, step_format) VALUES (?, ?, 3, 0, ?, ?, 0)",
        [(0, 14, m0, s0), (1, 15, m1, s1), (2, 132, m2, s2), (3, 15, m3, s3)],
    )
    con.commit()
    con.close()

    sum_base = summary_db_dir if summary_db_dir is not None else agy_dir
    sum_db = sum_base / "conversation_summaries.db"
    sc = sqlite3.connect(str(sum_db))
    sc.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            conversation_id text PRIMARY KEY,
            title text NOT NULL DEFAULT "",
            preview text NOT NULL DEFAULT "",
            step_count integer NOT NULL DEFAULT 0,
            last_modified_time datetime NOT NULL,
            workspace_uris text NOT NULL,
            status text NOT NULL DEFAULT "",
            source text NOT NULL DEFAULT "",
            project_id text NOT NULL DEFAULT "",
            agent_name text NOT NULL DEFAULT "",
            parent_conversation_id text NOT NULL DEFAULT "",
            nesting_depth integer NOT NULL DEFAULT 0,
            battle_id text NOT NULL DEFAULT "",
            winning_conversation_id text NOT NULL DEFAULT "",
            not_fully_idle numeric NOT NULL DEFAULT false,
            killed numeric NOT NULL DEFAULT false,
            last_user_input_time datetime NOT NULL,
            last_user_input_step_index integer NOT NULL DEFAULT -1,
            app_data_dir text NOT NULL DEFAULT "",
            raw_summary blob,
            group_id TEXT NOT NULL DEFAULT ''
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
            conversation_id UNINDEXED, chunk_index UNINDEXED, chunk_first_step UNINDEXED, chunk_last_step UNINDEXED,
            last_modified_time UNINDEXED, step_count UNINDEXED, last_step_created_at UNINDEXED, archived UNINDEXED,
            user_step_byte_offsets UNINDEXED, agent_step_byte_offsets UNINDEXED,
            title, workspace, project, user_body, agent_body
        );
        """
    )
    ts_str = "2026-11-14 09:00:00.000000+00:00"
    sc.execute(
        "INSERT OR REPLACE INTO conversation_summaries VALUES (?, ?, ?, 4, ?, ?, 'CASCADE_RUN_STATUS_IDLE', '', 'default-cli-project', '', '', 0, '', '', 0, 0, ?, 0, ?, ?, '')",
        (
            conv_id,
            title,
            user_text[:80],
            ts_str,
            json.dumps([uri]),
            ts_str,
            app_data_dir,
            pb_str(1, user_text[:80]) + pb_varint(2, 4),
        ),
    )
    sc.commit()
    sc.close()
    return db_path


def make_claude_jsonl(
    claude_dir: Path,
    sid: str,
    ws_path: str | Path,
    user_text: str,
    reply_text: str,
    ts_iso: str,
) -> Path:
    """Create a realistic Claude Code JSONL session file with tool use and thinking."""
    pdir = claude_dir / "projects" / claude_slug(ws_path)
    pdir.mkdir(parents=True, exist_ok=True)
    jpath = pdir / f"{sid}.jsonl"
    ws_str = str(ws_path)
    recs = [
        {
            "type": "user",
            "uuid": "u-" + sid[:8],
            "parentUuid": None,
            "sessionId": sid,
            "isSidechain": False,
            "timestamp": ts_iso,
            "cwd": ws_str,
            "message": {"role": "user", "content": user_text},
        },
        {
            "type": "assistant",
            "uuid": "a1-" + sid[:8],
            "parentUuid": "u-" + sid[:8],
            "sessionId": sid,
            "isSidechain": False,
            "timestamp": ts_iso,
            "cwd": ws_str,
            "message": {
                "id": "msg_1",
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Checking tools"},
                    {
                        "type": "tool_use",
                        "id": "toolu_seed_1",
                        "name": "Read",
                        "input": {"file_path": "app.py"},
                    },
                ],
            },
        },
        {
            "type": "user",
            "uuid": "u2-" + sid[:8],
            "parentUuid": "a1-" + sid[:8],
            "sessionId": sid,
            "isSidechain": False,
            "timestamp": ts_iso,
            "cwd": ws_str,
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_seed_1",
                        "content": "print('ok')",
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "uuid": "a2-" + sid[:8],
            "parentUuid": "u2-" + sid[:8],
            "sessionId": sid,
            "isSidechain": False,
            "timestamp": ts_iso,
            "cwd": ws_str,
            "message": {
                "id": "msg_2",
                "role": "assistant",
                "content": [{"type": "text", "text": reply_text}],
            },
        },
    ]
    jpath.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
        encoding="utf-8",
    )
    return jpath


def make_codex_state_db(codex_dir: Path) -> Path:
    """Create a realistic Codex `state_5.sqlite` with `threads` and CASCADE child table."""
    codex_dir.mkdir(parents=True, exist_ok=True)
    db_path = codex_dir / "state_5.sqlite"
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            rollout_path TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'cli',
            model_provider TEXT NOT NULL DEFAULT 'openai',
            cwd TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            sandbox_policy TEXT NOT NULL DEFAULT '{"type":"read-only"}',
            approval_mode TEXT NOT NULL DEFAULT 'on-request',
            tokens_used INTEGER NOT NULL DEFAULT 0,
            has_user_event INTEGER NOT NULL DEFAULT 1,
            archived INTEGER NOT NULL DEFAULT 0,
            archived_at INTEGER,
            git_sha TEXT,
            git_branch TEXT,
            git_origin_url TEXT,
            cli_version TEXT NOT NULL DEFAULT '',
            first_user_message TEXT NOT NULL DEFAULT '',
            agent_nickname TEXT,
            agent_role TEXT,
            memory_mode TEXT NOT NULL DEFAULT 'enabled',
            model TEXT,
            reasoning_effort TEXT,
            agent_path TEXT,
            forked_from_id TEXT,
            name TEXT,
            preview TEXT NOT NULL DEFAULT '',
            thread_source TEXT NOT NULL DEFAULT 'user',
            created_at_ms INTEGER NOT NULL DEFAULT 0,
            updated_at_ms INTEGER NOT NULL DEFAULT 0,
            recency_at INTEGER NOT NULL DEFAULT 0,
            recency_at_ms INTEGER NOT NULL DEFAULT 0,
            history_mode TEXT NOT NULL DEFAULT 'legacy'
        );
        CREATE TABLE IF NOT EXISTS thread_dynamic_tools (
            thread_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            input_schema TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (thread_id, position),
            FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
        );
        """
    )
    con.commit()
    con.close()
    return db_path


def make_codex_jsonl(
    codex_dir: Path,
    sid: str,
    ws_path: str | Path,
    user_text: str,
    thought_text: str,
    reply_text: str,
    ts_iso: str,
    *,
    archived: bool = False,
    subagent: bool = False,
    subagent_depth: int = 1,
    include_tools: bool = True,
    include_injected: bool = True,
    event_only: bool = False,
    raw_cwd: str | None = None,
    upsert_sqlite: bool = False,
    custom_name: str = "",
) -> Path:
    """Create a realistic Codex rollout JSONL file under `sessions/` or `archived_sessions/`."""
    date_part = ts_iso[:10]
    yyyy, mm, dd = date_part.split("-")
    ts_slug = ts_iso[:19].replace(":", "-")
    if archived:
        out_dir = codex_dir / "archived_sessions"
    else:
        out_dir = codex_dir / "sessions" / yyyy / mm / dd
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / f"rollout-{ts_slug}-{sid}.jsonl"

    cwd_str = raw_cwd if raw_cwd is not None else str(ws_path)
    meta_payload: dict[str, object] = {
        "id": sid,
        "timestamp": ts_iso,
        "cwd": cwd_str,
        "originator": "codex_cli_rs",
        "model_provider": "openai",
    }
    if subagent:
        meta_payload["source"] = {"subagent": {"thread_spawn": {"depth": subagent_depth}}}
        meta_payload["thread_source"] = "subagent"
    else:
        meta_payload["source"] = "cli"
        meta_payload["thread_source"] = "user"

    recs: list[dict[str, object]] = [
        {"timestamp": ts_iso, "type": "session_meta", "payload": meta_payload},
        {
            "timestamp": ts_iso,
            "type": "turn_context",
            "payload": {
                "cwd": cwd_str,
                "approval_policy": "on-request",
                "sandbox_policy": {"type": "read-only"},
            },
        },
    ]

    if event_only:
        recs.extend(
            [
                {
                    "timestamp": ts_iso,
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": user_text},
                },
                {
                    "timestamp": ts_iso,
                    "type": "event_msg",
                    "payload": {"type": "agent_reasoning", "text": thought_text},
                },
                {
                    "timestamp": ts_iso,
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": reply_text},
                },
            ]
        )
    else:
        if include_injected:
            recs.append(
                {
                    "timestamp": ts_iso,
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "System developer prompt"}],
                    },
                }
            )
            recs.append(
                {
                    "timestamp": ts_iso,
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "# AGENTS.md instructions for repo\nFollow rules.",
                            },
                            {"type": "input_text", "text": "<environment_context>\nOS: win\n</environment_context>"},
                        ],
                        "internal_chat_message_metadata_passthrough": {
                            "content_item_kinds": [
                                "agents_md.instructions",
                                "environments.environment_context",
                            ]
                        },
                    },
                }
            )

        recs.append(
            {
                "timestamp": ts_iso,
                "type": "event_msg",
                "payload": {"type": "user_message", "message": user_text},
            }
        )
        recs.append(
            {
                "timestamp": ts_iso,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_text}],
                    "internal_chat_message_metadata_passthrough": {
                        "content_item_kinds": ["user.text"]
                    },
                },
            }
        )

        if thought_text:
            recs.append(
                {
                    "timestamp": ts_iso,
                    "type": "response_item",
                    "payload": {
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": thought_text}],
                        "encrypted_content": "gAAAAAB_OPAQUE_SECRET_BLOB",
                    },
                }
            )

        if include_tools:
            recs.extend(
                [
                    {
                        "timestamp": ts_iso,
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "call_id": "call_fn_1",
                            "name": "read_file",
                            "arguments": '{"path":"README.md"}',
                        },
                    },
                    {
                        "timestamp": ts_iso,
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "call_fn_1",
                            "output": "README ok",
                        },
                    },
                    {
                        "timestamp": ts_iso,
                        "type": "response_item",
                        "payload": {
                            "type": "custom_tool_call",
                            "call_id": "call_custom_1",
                            "name": "apply_patch",
                            "input": "*** Begin Patch\n*** End Patch",
                        },
                    },
                    {
                        "timestamp": ts_iso,
                        "type": "response_item",
                        "payload": {
                            "type": "custom_tool_call_output",
                            "call_id": "call_custom_1",
                            "output": [{"type": "text", "text": "Patched"}],
                        },
                    },
                    {
                        "timestamp": ts_iso,
                        "type": "response_item",
                        "payload": {
                            "type": "local_shell_call",
                            "call_id": "call_sh_1",
                            "action": {"command": ["echo", "hi"]},
                        },
                    },
                    {
                        "timestamp": ts_iso,
                        "type": "response_item",
                        "payload": {
                            "type": "local_shell_call_output",
                            "call_id": "call_sh_1",
                            "output": "hi",
                        },
                    },
                ]
            )

        recs.append(
            {
                "timestamp": ts_iso,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": reply_text}],
                },
            }
        )

    jpath.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
        encoding="utf-8",
    )

    if upsert_sqlite:
        db_path = make_codex_state_db(codex_dir)
        con = sqlite3.connect(str(db_path))
        con.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider,
                cwd, title, archived, first_user_message, name, preview,
                thread_source, created_at_ms, updated_at_ms, recency_at, recency_at_ms, history_mode
            )
            VALUES (?, ?, 1763110800, 1763110800, 'cli', 'openai', ?, ?, ?, ?, ?, ?, ?, 1763110800000, 1763110800000, 1763110800, 1763110800000, 'legacy')
            ON CONFLICT(id) DO UPDATE SET rollout_path=excluded.rollout_path, cwd=excluded.cwd, title=excluded.title
            """,
            (
                sid,
                str(jpath),
                cwd_str,
                user_text[:80],
                1 if archived else 0,
                user_text,
                custom_name or None,
                user_text[:120],
                "subagent" if subagent else "user",
            ),
        )
        con.commit()
        con.close()

    return jpath


def make_codex_empty_jsonl(
    codex_dir: Path,
    sid: str,
    ws_path: str | Path,
    ts_iso: str,
) -> Path:
    """Create an empty Codex rollout JSONL containing only `session_meta` and `turn_context`."""
    date_part = ts_iso[:10]
    yyyy, mm, dd = date_part.split("-")
    ts_slug = ts_iso[:19].replace(":", "-")
    out_dir = codex_dir / "sessions" / yyyy / mm / dd
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / f"rollout-{ts_slug}-{sid}.jsonl"
    recs = [
        {
            "timestamp": ts_iso,
            "type": "session_meta",
            "payload": {
                "id": sid,
                "timestamp": ts_iso,
                "cwd": str(ws_path),
                "originator": "codex_cli_rs",
                "source": "cli",
                "thread_source": "user",
                "model_provider": "openai",
            },
        },
        {
            "timestamp": ts_iso,
            "type": "turn_context",
            "payload": {"cwd": str(ws_path)},
        },
    ]
    jpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return jpath


def seed_heavy_and_clean_fixtures(
    agy_dir: Path,
    claude_dir: Path,
    ws_a: str | Path,
    *,
    heavy_id: str,
    empty_id: str,
    agy_seed_id: str,
) -> dict[str, Path]:
    """Seed heavy Claude session (with sidecars), empty session, and orphan sidecars."""
    ws_str = str(ws_a)
    proj_a = claude_dir / "projects" / claude_slug(ws_str)
    proj_a.mkdir(parents=True, exist_ok=True)

    heavy_rows = [
        {
            "type": "user",
            "uuid": "h-u1",
            "parentUuid": None,
            "sessionId": heavy_id,
            "isSidechain": False,
            "timestamp": "2026-11-14T08:00:00.000Z",
            "cwd": ws_str,
            "message": {"role": "user", "content": "Heavy question 1"},
        },
        {
            "type": "assistant",
            "uuid": "h-a1",
            "parentUuid": "h-u1",
            "sessionId": heavy_id,
            "isSidechain": False,
            "timestamp": "2026-11-14T08:00:05.000Z",
            "cwd": ws_str,
            "message": {
                "id": "msg_h1",
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Long internal thinking " * 20},
                    {"type": "text", "text": "Running heavy tool"},
                    {
                        "type": "tool_use",
                        "id": "tu_h1",
                        "name": "read_file",
                        "input": {"path": "big.txt"},
                    },
                ],
            },
        },
        {
            "type": "user",
            "uuid": "h-tr1",
            "parentUuid": "h-a1",
            "sessionId": heavy_id,
            "isSidechain": False,
            "timestamp": "2026-11-14T08:00:08.000Z",
            "cwd": ws_str,
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_h1", "content": "X" * 4500}
                ],
            },
        },
        {
            "type": "user",
            "uuid": "h-u2",
            "parentUuid": "h-tr1",
            "sessionId": heavy_id,
            "isSidechain": False,
            "timestamp": "2026-11-14T08:01:00.000Z",
            "cwd": ws_str,
            "message": {"role": "user", "content": "Followup question 2"},
        },
        {
            "type": "assistant",
            "uuid": "h-a2",
            "parentUuid": "h-u2",
            "sessionId": heavy_id,
            "isSidechain": False,
            "timestamp": "2026-11-14T08:01:05.000Z",
            "cwd": ws_str,
            "message": {
                "id": "msg_h2",
                "role": "assistant",
                "content": [{"type": "text", "text": "Newest final answer"}],
            },
        },
    ]
    heavy_jsonl = proj_a / f"{heavy_id}.jsonl"
    heavy_jsonl.write_text(
        "\n".join(json.dumps(r) for r in heavy_rows) + "\n",
        encoding="utf-8",
    )
    sidecar = proj_a / heavy_id
    subagents_dir = sidecar / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    (subagents_dir / "agent-heavy.jsonl").write_bytes(b"S" * 40000)
    custom_title = sidecar / "custom-title.json"
    custom_title.write_text('{"customTitle": "Heavy Test Chat"}', encoding="utf-8")

    fh_heavy = claude_dir / "file-history" / heavy_id
    fh_heavy.mkdir(parents=True, exist_ok=True)
    (fh_heavy / "snap.bin").write_bytes(b"F" * 60000)

    empty_row = {
        "type": "file-history-snapshot",
        "messageId": "m-empty-1",
        "snapshot": {
            "messageId": "m-empty-1",
            "trackedFileBackups": {},
            "timestamp": "2026-11-14T07:00:00.000Z",
        },
    }
    empty_jsonl = proj_a / f"{empty_id}.jsonl"
    empty_jsonl.write_text(json.dumps(empty_row) + "\n", encoding="utf-8")

    agy_seed_brain = agy_dir / "brain" / agy_seed_id
    agy_seed_brain.mkdir(parents=True, exist_ok=True)
    (agy_seed_brain / "artifact.bin").write_bytes(b"A" * 50000)

    orphan_brain = agy_dir / "brain" / "orphan-brain-123"
    orphan_brain.mkdir(parents=True, exist_ok=True)
    (orphan_brain / "junk.bin").write_bytes(b"O" * 16384)

    orphan_fh = claude_dir / "file-history" / "orphan-fh-123"
    orphan_fh.mkdir(parents=True, exist_ok=True)
    (orphan_fh / "old.bak").write_bytes(b"O" * 16384)

    return {
        "heavy_jsonl": heavy_jsonl,
        "heavy_subagents": subagents_dir,
        "heavy_custom_title": custom_title,
        "heavy_fh": fh_heavy,
        "empty_jsonl": empty_jsonl,
        "agy_seed_brain": agy_seed_brain,
        "orphan_brain": orphan_brain,
        "orphan_fh": orphan_fh,
    }


def seed_standard_trio(
    agy_dir: Path,
    claude_dir: Path,
    ws_a: str | Path,
    ws_b: str | Path,
) -> dict[str, str]:
    """Seed the standard 3 sessions (1 AGY in wsA, 2 Claude in wsA & wsB)."""
    agy_id = "11111111-1111-4111-8111-111111111111"
    claude_a_id = "22222222-2222-4222-8222-222222222222"
    claude_b_id = "33333333-3333-4333-8333-333333333333"

    make_agy_db(
        agy_dir,
        agy_id,
        ws_a,
        "AGY Seed: Bánh mì Hà Nội 2026",
        "Xin chào Bánh mì Hà Nội 2026 from AGY",
        "Internal reasoning trace",
        "Ack AGY response TOKEN_AGY_8811",
        1763110800,
    )
    make_claude_jsonl(
        claude_dir,
        claude_a_id,
        ws_a,
        "Cà phê sữa đá 2026 from Claude",
        "Claude reply TOKEN_CLAUDE_9922",
        "2026-11-14T09:10:00.000Z",
    )
    make_claude_jsonl(
        claude_dir,
        claude_b_id,
        ws_b,
        "Workspace B Claude session",
        "Workspace B reply TOKEN_WSB_3344",
        "2026-11-14T09:20:00.000Z",
    )
    return {
        "agy_id": agy_id,
        "claude_a_id": claude_a_id,
        "claude_b_id": claude_b_id,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3:
        ws_a_arg, ws_b_arg = sys.argv[1], sys.argv[2]
        seed_standard_trio(
            Path(os.environ["MCAS_AGY_DIR"]),
            Path(os.environ["MCAS_CLAUDE_DIR"]),
            ws_a_arg,
            ws_b_arg,
        )

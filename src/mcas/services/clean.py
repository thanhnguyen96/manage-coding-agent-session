from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import shutil
import sqlite3

from mcas.cache import (
    _fast_dir_size,
    _fast_file_size,
    _invalidate_size_cache,
)
from mcas.config import (
    get_agy_cli_dir,
    get_agy_ide_dir,
    get_claude_dir,
    get_claude_projects_dir,
    get_codex_dir,
)
from mcas.models import SessionSummary
from mcas.providers import _collect_sessions_by_provider, get_provider
from mcas.sync.state import (
    _SYNC_LOCK,
    _append_history,
    _load_state,
    _remove_pairs_for,
    _save_state,
)
from mcas.util.fsio import _connect_sqlite
from mcas.util.pathconv import (
    normalize_windows_path,
    paths_match,
    windows_path_to_slug,
)
from mcas.util.text import (
    format_bytes,
    truncate_preview,
)

def _scan_storage_orphans(
    source: str = "all", cwd_filter: str | None = None
) -> dict[str, Any]:
    """Identify orphaned sidecars and stale DB rows without touching active sessions."""
    src = (source or "all").strip().lower()
    cli_dir = get_agy_cli_dir()
    ide_dir = get_agy_ide_dir()
    claude_dir = get_claude_dir()
    proj_dir = get_claude_projects_dir()

    orphan_paths: list[tuple[str, Path, int, str]] = []  # (provider, path, bytes, label)
    stale_summary_ids: list[str] = []

    if src in ("all", "agy"):
        cli_conv_ids = (
            {p.stem for p in (cli_dir / "conversations").glob("*.db") if p.is_file()}
            if (cli_dir / "conversations").is_dir()
            else set()
        )
        ide_conv_ids = (
            {p.stem for p in (ide_dir / "conversations").glob("*.db") if p.is_file()}
            if (ide_dir / "conversations").is_dir()
            else set()
        )
        if not cwd_filter:
            br_root = cli_dir / "brain"
            if br_root.is_dir():
                for d in br_root.iterdir():
                    if d.is_dir() and d.name not in cli_conv_ids:
                        sz = _fast_dir_size(d)
                        orphan_paths.append(("agy", d, sz, f"orphan brain/{d.name}"))
            ann_root = cli_dir / "annotations"
            if ann_root.is_dir():
                for f in ann_root.glob("*.pbtxt"):
                    if f.is_file() and f.stem not in cli_conv_ids:
                        orphan_paths.append(("agy", f, _fast_file_size(f), f"orphan annotations/{f.name}"))
            imp_root = cli_dir / "implicit"
            if imp_root.is_dir():
                for f in imp_root.glob("*.pb"):
                    if f.is_file() and f.stem not in cli_conv_ids:
                        orphan_paths.append(("agy", f, _fast_file_size(f), f"orphan implicit/{f.name}"))

            sum_db = cli_dir / "conversation_summaries.db"
            if sum_db.is_file():
                con = _connect_sqlite(sum_db)
                try:
                    for row in con.execute(
                        "SELECT conversation_id, app_data_dir FROM conversation_summaries"
                    ):
                        cid = str(row[0] or "")
                        app_d = str(row[1] or "antigravity-cli")
                        if not cid:
                            continue
                        if app_d == "antigravity":
                            if cid not in ide_conv_ids and cid not in cli_conv_ids:
                                stale_summary_ids.append(cid)
                        elif cid not in cli_conv_ids and cid not in ide_conv_ids:
                            stale_summary_ids.append(cid)
                except sqlite3.Error:
                    pass
                finally:
                    con.close()

    if src in ("all", "claude"):
        target_slug = windows_path_to_slug(cwd_filter).lower() if cwd_filter else None
        all_claude_ids: set[str] = set()
        if proj_dir.is_dir():
            for pdir in proj_dir.iterdir():
                if not pdir.is_dir():
                    continue
                jsonl_stems: set[str] = set()
                for jf in pdir.glob("*.jsonl"):
                    if jf.is_file():
                        jsonl_stems.add(jf.stem)
                        all_claude_ids.add(jf.stem)
                if target_slug and pdir.name.lower() != target_slug:
                    continue
                for sub in pdir.iterdir():
                    if sub.is_dir() and sub.name not in jsonl_stems:
                        sz = _fast_dir_size(sub)
                        orphan_paths.append(("claude", sub, sz, f"orphan claude/{pdir.name}/{sub.name}"))

        if not cwd_filter:
            fh_root = claude_dir / "file-history"
            if fh_root.is_dir():
                for d in fh_root.iterdir():
                    if d.is_dir() and d.name not in all_claude_ids:
                        sz = _fast_dir_size(d)
                        orphan_paths.append(("claude", d, sz, f"orphan file-history/{d.name}"))

    orphan_bytes = sum(x[2] for x in orphan_paths)
    return {
        "orphanPaths": orphan_paths,
        "staleSummaryIds": stale_summary_ids,
        "orphanCount": len(orphan_paths) + len(stale_summary_ids),
        "orphanBytes": orphan_bytes,
    }

def get_storage_summary(
    by_prov_all: dict[str, list[SessionSummary]] | None = None
) -> dict[str, Any]:
    cli_dir = get_agy_cli_dir()
    claude_dir = get_claude_dir()
    codex_dir = get_codex_dir()

    sum_db = cli_dir / "conversation_summaries.db"
    agy_bytes = (
        _fast_dir_size(cli_dir / "conversations")
        + _fast_dir_size(cli_dir / "brain")
        + _fast_dir_size(cli_dir / "annotations")
        + _fast_dir_size(cli_dir / "implicit")
        + _fast_file_size(sum_db)
        + _fast_file_size(Path(str(sum_db) + "-wal"))
        + _fast_file_size(Path(str(sum_db) + "-shm"))
    )
    claude_bytes = (
        _fast_dir_size(claude_dir / "projects")
        + _fast_dir_size(claude_dir / "file-history")
    )
    codex_state_db = codex_dir / "state_5.sqlite"
    codex_bytes = (
        _fast_dir_size(codex_dir / "sessions")
        + _fast_dir_size(codex_dir / "archived_sessions")
        + _fast_file_size(codex_dir / "session_index.jsonl")
        + _fast_file_size(codex_state_db)
        + _fast_file_size(Path(str(codex_state_db) + "-wal"))
        + _fast_file_size(Path(str(codex_state_db) + "-shm"))
    )
    total_bytes = agy_bytes + claude_bytes + codex_bytes

    orphans = _scan_storage_orphans("all")
    empty_count = 0
    subagent_count = 0
    if by_prov_all:
        for s in by_prov_all.get("agy", []):
            extras = [x.strip() for x in s.extra.split(",") if x.strip()]
            if "antigravity" in extras:
                continue
            if s.step_or_msg_count == 0:
                empty_count += 1
            if any(x.startswith("depth=") and x != "depth=0" for x in extras):
                subagent_count += 1
        for s in by_prov_all.get("claude", []):
            if s.step_or_msg_count == 0:
                empty_count += 1
            sub_dir = s.location.with_suffix("") / "subagents"
            if sub_dir.is_dir():
                subagent_count += 1
        for s in by_prov_all.get("codex", []):
            extras = [x.strip() for x in s.extra.split(",") if x.strip()]
            if s.step_or_msg_count == 0:
                empty_count += 1
            if any(x.startswith("depth=") and x != "depth=0" for x in extras):
                subagent_count += 1

    return {
        "agyBytes": agy_bytes,
        "agyHuman": format_bytes(agy_bytes),
        "claudeBytes": claude_bytes,
        "claudeHuman": format_bytes(claude_bytes),
        "codexBytes": codex_bytes,
        "codexHuman": format_bytes(codex_bytes),
        "totalBytes": total_bytes,
        "totalHuman": format_bytes(total_bytes),
        "orphanBytes": orphans["orphanBytes"],
        "orphanHuman": format_bytes(orphans["orphanBytes"]),
        "orphanCount": orphans["orphanCount"],
        "emptyCount": empty_count,
        "subagentCount": subagent_count,
    }

def clean_storage(
    *,
    source: str = "all",
    cwd: str | None = None,
    clean_orphans: bool = True,
    delete_empty: bool = False,
    delete_subagents: bool = False,
    older_than_days: int | None = None,
    larger_than_mb: float | None = None,
    vacuum_db: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Reclaim disk space by removing orphans, empty/subagent/old/large sessions, and vacuuming SQLite."""
    src = (source or "all").strip().lower()
    if src != "all":
        get_provider(src, require_supported=True)
    cwd_filter = normalize_windows_path(cwd) if cwd and cwd.strip() else None

    now = datetime.now(timezone.utc)
    min_age_sec = older_than_days * 86400.0 if (older_than_days is not None and older_than_days >= 1) else None
    min_size_bytes = int(larger_than_mb * 1024 * 1024) if (larger_than_mb is not None and larger_than_mb > 0) else None

    orphan_info = (
        _scan_storage_orphans(source=src, cwd_filter=cwd_filter)
        if clean_orphans
        else {"orphanPaths": [], "staleSummaryIds": [], "orphanCount": 0, "orphanBytes": 0}
    )

    sessions_to_delete: dict[tuple[str, str], tuple[SessionSummary, str]] = {}
    claude_subagent_dirs: list[tuple[Path, int, str]] = []

    all_by_prov = _collect_sessions_by_provider(include_all=True)
    cwd_slug_lower = windows_path_to_slug(cwd_filter).lower() if cwd_filter else None

    def matches_cwd(s: SessionSummary) -> bool:
        if not cwd_filter:
            return True
        if s.cwd:
            return paths_match(s.cwd, cwd_filter)
        if s.source == "claude" and cwd_slug_lower:
            return s.location.parent.name.lower() == cwd_slug_lower
        return False

    for pid, slist in all_by_prov.items():
        if src != "all" and pid != src:
            continue
        for s in slist:
            extras = [x.strip() for x in s.extra.split(",") if x.strip()]
            if "antigravity" in extras:
                continue
            if not matches_cwd(s):
                continue

            is_subagent = any(x.startswith("depth=") and x != "depth=0" for x in extras)
            reason = ""
            if delete_empty and s.step_or_msg_count == 0:
                reason = "empty (0 turns)"
            elif delete_subagents and is_subagent:
                reason = "subagent"
            elif (min_age_sec is not None or min_size_bytes is not None) and not is_subagent and s.step_or_msg_count > 0:
                age_ok = True
                size_ok = True
                if min_age_sec is not None:
                    age_ok = (now - s.updated_at).total_seconds() >= min_age_sec
                if min_size_bytes is not None:
                    size_ok = s.size_bytes >= min_size_bytes
                if age_ok and size_ok:
                    parts = []
                    if min_age_sec is not None:
                        parts.append(f">{older_than_days}d")
                    if min_size_bytes is not None:
                        parts.append(f">={larger_than_mb}MB")
                    reason = " & ".join(parts)

            if reason:
                sessions_to_delete[(s.source, s.session_id)] = (s, reason)
            elif delete_subagents and pid == "claude":
                sub_dir = s.location.with_suffix("") / "subagents"
                if sub_dir.is_dir():
                    sz = _fast_dir_size(sub_dir)
                    claude_subagent_dirs.append((sub_dir, sz, f"claude/{s.session_id[:8]}/subagents"))

    would_free = (
        orphan_info["orphanBytes"]
        + sum(s.size_bytes for s, _ in sessions_to_delete.values())
        + sum(sz for _, sz, _ in claude_subagent_dirs)
    )

    preview_items: list[dict[str, Any]] = []
    for _prov, p, sz, label in orphan_info["orphanPaths"][:25]:
        preview_items.append({"kind": "orphan", "label": label, "bytes": sz, "human": format_bytes(sz)})
    for sid_stale in orphan_info["staleSummaryIds"][:10]:
        preview_items.append({"kind": "db-stale", "label": f"stale index row {sid_stale[:12]}", "bytes": 0, "human": "0 B"})
    for _dir, sz, label in claude_subagent_dirs[:20]:
        preview_items.append({"kind": "subagents", "label": label, "bytes": sz, "human": format_bytes(sz)})
    for (_src, _sid), (s, reason) in list(sessions_to_delete.items())[:30]:
        preview_items.append(
            {
                "kind": "session",
                "source": s.source,
                "id": s.session_id,
                "label": f"{s.source}:{s.session_id[:8]} {truncate_preview(s.title, 48)} ({reason})",
                "bytes": s.size_bytes,
                "human": format_bytes(s.size_bytes),
            }
        )

    if dry_run:
        msg = (
            f"Dry run: would delete {len(sessions_to_delete)} session(s), "
            f"{orphan_info['orphanCount']} orphan(s), "
            f"{len(claude_subagent_dirs)} Claude subagent dir(s) -> "
            f"would free {format_bytes(would_free)}."
        )
        return {
            "ok": True,
            "dryRun": True,
            "sessionsDeleted": len(sessions_to_delete),
            "orphansCleaned": orphan_info["orphanCount"],
            "subagentDirsCleaned": len(claude_subagent_dirs),
            "freedBytes": would_free,
            "freedHuman": format_bytes(would_free),
            "vacuumSavedBytes": 0,
            "vacuumSavedHuman": "0 B",
            "previewItems": preview_items,
            "message": msg,
        }

    freed_bytes = 0
    orphans_cleaned = 0
    sessions_deleted = 0
    subagent_dirs_cleaned = 0

    for _prov, p, sz, _label in orphan_info["orphanPaths"]:
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                freed_bytes += sz
                orphans_cleaned += 1
            elif p.is_file():
                p.unlink(missing_ok=True)
                freed_bytes += sz
                orphans_cleaned += 1
        except OSError:
            pass

    sum_db = get_agy_cli_dir() / "conversation_summaries.db"
    if orphan_info["staleSummaryIds"] and sum_db.is_file():
        con = _connect_sqlite(sum_db)
        try:
            for cid in orphan_info["staleSummaryIds"]:
                for tbl in ("conversation_summaries", "conversations_fts", "conversation_parents"):
                    try:
                        con.execute(f"DELETE FROM {tbl} WHERE conversation_id = ?", (cid,))
                    except sqlite3.Error:
                        pass
                orphans_cleaned += 1
            con.commit()
        finally:
            con.close()

    for sub_dir, sz, _label in claude_subagent_dirs:
        if sub_dir.is_dir():
            shutil.rmtree(sub_dir, ignore_errors=True)
            freed_bytes += sz
            subagent_dirs_cleaned += 1
            parent_dir = sub_dir.parent
            try:
                if parent_dir.is_dir() and not any(parent_dir.iterdir()):
                    parent_dir.rmdir()
            except OSError:
                pass

    for (_src, _sid), (s, _reason) in sessions_to_delete.items():
        try:
            prov = get_provider(s.source, require_supported=True)
            dres = prov.delete_session(s.session_id)
            _remove_pairs_for(prov.id, dres["id"])
            freed_bytes += int(dres.get("freedBytes") or 0)
            sessions_deleted += 1
        except Exception:
            pass

    vacuum_saved = 0
    if vacuum_db and src in ("all", "agy") and sum_db.is_file():
        before_sum = (
            _fast_file_size(sum_db)
            + _fast_file_size(Path(str(sum_db) + "-wal"))
            + _fast_file_size(Path(str(sum_db) + "-shm"))
        )
        try:
            con = _connect_sqlite(sum_db)
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.execute("VACUUM")
                con.commit()
            finally:
                con.close()
            after_sum = (
                _fast_file_size(sum_db)
                + _fast_file_size(Path(str(sum_db) + "-wal"))
                + _fast_file_size(Path(str(sum_db) + "-shm"))
            )
            vacuum_saved = max(0, before_sum - after_sum)
            freed_bytes += vacuum_saved
        except sqlite3.Error:
            pass

    _invalidate_size_cache()

    if sessions_deleted or orphans_cleaned or subagent_dirs_cleaned or vacuum_saved:
        with _SYNC_LOCK:
            state = _load_state()
            _append_history(
                state,
                kind="clean",
                direction=src,
                source=src,
                target="",
                source_id="",
                target_id="",
                title="Storage cleanup",
                cwd=cwd_filter or "",
                status="ok",
                message=(
                    f"Freed {format_bytes(freed_bytes)} "
                    f"({sessions_deleted} sessions, {orphans_cleaned} orphans, "
                    f"{subagent_dirs_cleaned} subagent dirs)"
                ),
            )
            _save_state(state)

    msg = (
        f"Cleanup complete: freed {format_bytes(freed_bytes)} "
        f"({sessions_deleted} session(s), {orphans_cleaned} orphan(s), "
        f"{subagent_dirs_cleaned} Claude subagent dir(s))."
    )
    return {
        "ok": True,
        "dryRun": False,
        "sessionsDeleted": sessions_deleted,
        "orphansCleaned": orphans_cleaned,
        "subagentDirsCleaned": subagent_dirs_cleaned,
        "freedBytes": freed_bytes,
        "freedHuman": format_bytes(freed_bytes),
        "vacuumSavedBytes": vacuum_saved,
        "vacuumSavedHuman": format_bytes(vacuum_saved),
        "previewItems": preview_items,
        "message": msg,
    }

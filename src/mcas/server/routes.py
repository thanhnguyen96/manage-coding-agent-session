from __future__ import annotations

import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from typing import Any, Callable

from mcas.errors import SyncError
from mcas.providers import get_provider
from mcas.services.clean import clean_storage
from mcas.services.delete import delete_sessions_batch
from mcas.services.query import get_session_detail, get_status_dict, query_sessions_api
from mcas.services.trim import trim_session
from mcas.sync.autosync import run_autosync_cycle
from mcas.sync.copy import copy_one_session, sync_directory_sessions
from mcas.sync.state import get_autosync_dict, update_autosync_config


@dataclass
class ServerContext:
    port: int
    token: str
    stop_event: threading.Event
    wake_event: threading.Event
    server_ref: list[ThreadingHTTPServer]


GetRouteHandler = Callable[[ServerContext, dict[str, list[str]]], tuple[int, Any]]
PostRouteHandler = Callable[[ServerContext, dict[str, Any]], tuple[int, Any]]


def _parse_optional_int(raw: Any, field: str) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise SyncError(f"Invalid {field} '{raw}'.") from exc


def _parse_optional_float(raw: Any, field: str) -> float | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise SyncError(f"Invalid {field} '{raw}'.") from exc


def _opt_str(val: Any) -> str | None:
    return str(val).strip() if isinstance(val, str) and val.strip() else None


def handle_get_status(ctx: ServerContext, _qs: dict[str, list[str]]) -> tuple[int, Any]:
    return HTTPStatus.OK, get_status_dict(port=ctx.port)


def handle_get_sessions(_ctx: ServerContext, qs: dict[str, list[str]]) -> tuple[int, Any]:
    src = (qs.get("source", ["all"])[0] or "all").strip()
    cwd_val = qs.get("cwd", [""])[0] or None
    q_val = qs.get("q", [""])[0] or None
    sort_val = (qs.get("sort", ["updated_desc"])[0] or "updated_desc").strip()
    all_raw = (qs.get("all", ["0"])[0] or "0").strip().lower()
    include_all = all_raw in ("1", "true", "yes", "on")
    limit_raw = (qs.get("limit", ["200"])[0] or "200").strip()
    try:
        limit_val = int(limit_raw)
    except ValueError as exc:
        raise SyncError(f"Invalid limit '{limit_raw}'.") from exc
    res = query_sessions_api(
        source=src,
        cwd=cwd_val,
        q=q_val,
        include_all=include_all,
        limit=limit_val,
        sort=sort_val,
    )
    return HTTPStatus.OK, res


def handle_get_session(_ctx: ServerContext, qs: dict[str, list[str]]) -> tuple[int, Any]:
    src = (qs.get("source", [""])[0] or "").strip() or None
    sid = (qs.get("id", [""])[0] or "").strip()
    return HTTPStatus.OK, get_session_detail(src, sid)


def handle_post_quit(ctx: ServerContext, _body: dict[str, Any]) -> tuple[int, Any]:
    def _shutdown_async() -> None:
        ctx.stop_event.set()
        ctx.wake_event.set()
        if ctx.server_ref:
            ctx.server_ref[0].shutdown()

    threading.Thread(target=_shutdown_async, daemon=True).start()
    return HTTPStatus.OK, {"message": "bye"}


def handle_post_copy(ctx: ServerContext, body: dict[str, Any]) -> tuple[int, Any]:
    to_provider = str(body.get("toProvider") or "").strip()
    if not to_provider:
        raise SyncError("toProvider is required.")
    get_provider(to_provider, require_supported=True)

    items = body.get("items")
    if items is None and body.get("id"):
        items = [{"source": body.get("source"), "id": body.get("id")}]
    if not isinstance(items, list) or not items:
        raise SyncError("items must be a non-empty list.")

    target_cwd_str = _opt_str(body.get("targetCwd"))
    new_id = bool(body.get("newId", False))
    overwrite = bool(body.get("overwrite", False))
    include_thinking = bool(body.get("includeThinking", False))

    results: list[dict[str, Any]] = []
    ok_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        item_src = str(item.get("source") or "").strip() or None
        item_id = str(item.get("id") or "").strip()
        try:
            r = copy_one_session(
                id_or_latest=item_id,
                from_provider_id=item_src,
                to_provider_id=to_provider,
                target_cwd=target_cwd_str,
                all_cwd=True,
                new_id=new_id,
                overwrite=overwrite,
                include_thinking=include_thinking,
                kind="manual",
            )
            results.append(r)
            ok_count += 1
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "status": "error",
                    "source": item_src or "",
                    "sourceId": item_id,
                    "toProvider": to_provider,
                    "targetId": None,
                    "targetCwd": target_cwd_str,
                    "title": "",
                    "path": None,
                    "regenerated": False,
                    "resumeCmd": None,
                    "resumeDir": target_cwd_str,
                    "message": str(exc),
                    "error": str(exc),
                }
            )

    if ok_count == 0:
        err_msg = results[0]["error"] if results else "No sessions could be copied."
        return HTTPStatus.BAD_REQUEST, {"error": err_msg, "results": results}

    err_count = len(results) - ok_count
    msg = f"Copied {ok_count} session(s) to {to_provider}" + (
        f" ({err_count} failed)." if err_count else "."
    )
    return HTTPStatus.OK, {
        "message": msg,
        "results": results,
        "status": get_status_dict(port=ctx.port),
    }


def handle_post_sync_dir(ctx: ServerContext, body: dict[str, Any]) -> tuple[int, Any]:
    from_provider = str(body.get("fromProvider") or "").strip()
    to_provider = str(body.get("toProvider") or "").strip()
    cwd_val = str(body.get("cwd") or "").strip()
    if not from_provider or not to_provider:
        raise SyncError("fromProvider and toProvider are required.")
    if not cwd_val:
        raise SyncError("Workspace folder (cwd) is required.")

    results = sync_directory_sessions(
        from_provider_id=from_provider,
        to_provider_id=to_provider,
        cwd=cwd_val,
        target_cwd=_opt_str(body.get("targetCwd")),
        overwrite=bool(body.get("overwrite", False)),
        include_thinking=bool(body.get("includeThinking", False)),
    )
    copied = sum(1 for r in results if r["status"] == "ok")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    errors = sum(1 for r in results if r["status"] == "error")
    if results and copied == 0 and skipped == 0 and errors > 0:
        return HTTPStatus.BAD_REQUEST, {"error": results[0]["error"], "results": results}

    msg = (
        f"Folder sync {from_provider} -> {to_provider}: "
        f"{copied} copied, {skipped} skipped"
        + (f", {errors} error(s)." if errors else ".")
    )
    return HTTPStatus.OK, {
        "message": msg,
        "results": results,
        "status": get_status_dict(port=ctx.port),
    }


def handle_post_delete(ctx: ServerContext, body: dict[str, Any]) -> tuple[int, Any]:
    raw_items = body.get("items")
    if isinstance(raw_items, list):
        items = raw_items
    else:
        src = str(body.get("source") or "").strip()
        sid = str(body.get("id") or "").strip()
        if not src or not sid:
            raise SyncError("source and id (or items) are required.")
        items = [{"source": src, "id": sid}]
    del_res = delete_sessions_batch(
        items,
        delete_synced_peers=bool(body.get("deleteSyncedPeers", False)),
    )
    return HTTPStatus.OK, {**del_res, "status": get_status_dict(port=ctx.port)}


def handle_post_trim(ctx: ServerContext, body: dict[str, Any]) -> tuple[int, Any]:
    src = str(body.get("source") or "").strip()
    sid = str(body.get("id") or "").strip()
    if not src or not sid:
        raise SyncError("source and id are required.")
    keep_ratio = _parse_optional_float(body.get("keepRatio"), "keepRatio")
    keep_turns = _parse_optional_int(body.get("keepLastTurns"), "keepLastTurns")
    max_chars_raw = body.get("maxToolOutputChars", 2000)
    max_chars = (
        _parse_optional_int(max_chars_raw, "maxToolOutputChars")
        if max_chars_raw is not None
        else 2000
    )
    trim_res = trim_session(
        source=src,
        session_id=sid,
        keep_ratio=keep_ratio,
        keep_last_turns=keep_turns,
        max_tool_output_chars=2000 if max_chars is None else max_chars,
        strip_thinking=bool(body.get("stripThinking", True)),
        purge_sidecars=bool(body.get("purgeSidecars", True)),
    )
    return HTTPStatus.OK, {**trim_res, "status": get_status_dict(port=ctx.port)}


def handle_post_clean(ctx: ServerContext, body: dict[str, Any]) -> tuple[int, Any]:
    src = str(body.get("source") or "all").strip() or "all"
    cwd_val = str(body.get("cwd") or "").strip() or None
    older_days = _parse_optional_int(body.get("olderThanDays"), "olderThanDays")
    larger_mb = _parse_optional_float(body.get("largerThanMb"), "largerThanMb")
    clean_res = clean_storage(
        source=src,
        cwd=cwd_val,
        clean_orphans=bool(body.get("cleanOrphans", True)),
        delete_empty=bool(body.get("deleteEmpty", False)),
        delete_subagents=bool(body.get("deleteSubagents", False)),
        older_than_days=older_days,
        larger_than_mb=larger_mb,
        vacuum_db=bool(body.get("vacuumDb", True)),
        dry_run=bool(body.get("dryRun", False)),
    )
    return HTTPStatus.OK, {**clean_res, "status": get_status_dict(port=ctx.port)}


def handle_post_autosync(ctx: ServerContext, body: dict[str, Any]) -> tuple[int, Any]:
    prev_enabled = get_autosync_dict()["enabled"]
    updated = update_autosync_config(
        enabled=body["enabled"] if "enabled" in body else None,
        interval_sec=body["intervalSec"] if "intervalSec" in body else None,
        direction=body["direction"] if "direction" in body else None,
        scope=body["scope"] if "scope" in body else None,
        watched_folders=body["watchedFolders"] if "watchedFolders" in body else None,
        include_thinking=body["includeThinking"] if "includeThinking" in body else None,
    )
    if bool(body.get("runNow", False)):
        run_res = run_autosync_cycle(force_even_if_disabled=True)
        ctx.wake_event.set()
        return HTTPStatus.OK, {
            "message": run_res["message"],
            "autosync": get_autosync_dict(),
            "runResult": run_res,
            "status": get_status_dict(port=ctx.port),
        }

    if not prev_enabled and updated["enabled"]:
        ctx.wake_event.set()
    msg = (
        f"Auto-sync {'enabled' if updated['enabled'] else 'disabled'} "
        f"({updated['direction']}, {updated['intervalSec']}s, scope={updated['scope']})."
    )
    return HTTPStatus.OK, {
        "message": msg,
        "autosync": updated,
        "status": get_status_dict(port=ctx.port),
    }


def handle_post_autostart(ctx: ServerContext, body: dict[str, Any]) -> tuple[int, Any]:
    try:
        from mcas.desktop.autostart import set_autostart, test_autostart
    except ImportError as exc:
        raise SyncError("Desktop autostart support is not installed.") from exc

    on = bool(body.get("on", False))
    msg = set_autostart(on, ctx.port)
    return HTTPStatus.OK, {
        "message": msg,
        "autostart": test_autostart(),
        "status": get_status_dict(port=ctx.port),
    }


GET_ROUTES: dict[str, GetRouteHandler] = {
    "status": handle_get_status,
    "sessions": handle_get_sessions,
    "session": handle_get_session,
}

POST_ROUTES: dict[str, PostRouteHandler] = {
    "quit": handle_post_quit,
    "copy": handle_post_copy,
    "sync-dir": handle_post_sync_dir,
    "delete": handle_post_delete,
    "trim": handle_post_trim,
    "clean": handle_post_clean,
    "autosync": handle_post_autosync,
    "autostart": handle_post_autostart,
}

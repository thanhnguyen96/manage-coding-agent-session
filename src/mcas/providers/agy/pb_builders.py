from __future__ import annotations

from datetime import datetime

from mcas.models import CanonicalToolCall
from mcas.providers.agy.schema import SOURCE_MODEL
from mcas.util.protobuf import (
    pb_msg,
    pb_str,
    pb_timestamp,
    pb_varint,
)

def _build_chat_tool_call_pb(tc: CanonicalToolCall) -> bytes:
    """Encode ChatToolCall {1: id, 2: name, 3: arguments_json}."""
    return b"".join(
        [
            pb_str(1, tc.id),
            pb_str(2, tc.name),
            pb_str(3, tc.arguments_json or "{}"),
        ]
    )

def _build_step_metadata_pb(
    ts: datetime,
    source: int,
    traj_id: str,
    step_idx: int,
    conv_id: str,
    execution_id: str,
    tool_call: CanonicalToolCall | None = None,
) -> bytes:
    """Encode CortexStepMetadata protobuf bytes."""
    src_info_parts = [pb_str(1, traj_id)]
    if step_idx != 0:
        src_info_parts.append(pb_varint(2, step_idx))
    src_info_parts.append(pb_str(4, conv_id))

    parts = [
        pb_timestamp(1, ts),
        pb_varint(3, source),
    ]
    if tool_call is not None:
        parts.append(pb_msg(4, _build_chat_tool_call_pb(tool_call)))
    if source == SOURCE_MODEL:
        parts.append(pb_timestamp(8, ts))
    parts.append(pb_str(12, execution_id))
    parts.append(pb_msg(20, b"".join(src_info_parts)))
    if source == SOURCE_MODEL:
        parts.append(pb_timestamp(32, ts))
    return b"".join(parts)

def _build_trajectory_metadata_pb(
    workspace_uri: str | None,
    created_at: datetime,
    init_state_id: str,
    conv_id: str,
) -> bytes:
    """Encode CortexTrajectoryMetadata protobuf bytes."""
    parts: list[bytes] = []
    if workspace_uri:
        parts.append(pb_msg(1, pb_str(1, workspace_uri)))
    parts.append(pb_timestamp(2, created_at))
    parts.append(pb_str(3, init_state_id))
    parts.append(pb_str(6, conv_id))
    if workspace_uri:
        parts.append(pb_str(7, workspace_uri))
    parts.append(pb_str(18, "default-cli-project"))
    return b"".join(parts)

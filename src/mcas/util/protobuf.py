from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import struct

def encode_varint(value: int) -> bytes:
    """Encode an integer as a base-128 protobuf varint (two's complement for <0)."""
    if value < 0:
        value &= (1 << 64) - 1
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)

def pb_varint(field_num: int, value: int) -> bytes:
    """Encode a protobuf varint field (wire type 0)."""
    return encode_varint((field_num << 3) | 0) + encode_varint(value)

def pb_bytes(field_num: int, data: bytes) -> bytes:
    """Encode a protobuf length-delimited field (wire type 2)."""
    return encode_varint((field_num << 3) | 2) + encode_varint(len(data)) + data

def pb_str(field_num: int, text: str) -> bytes:
    """Encode a non-empty UTF-8 string field."""
    return pb_bytes(field_num, text.encode("utf-8"))

def pb_msg(field_num: int, payload: bytes) -> bytes:
    """Encode an embedded protobuf sub-message field."""
    return pb_bytes(field_num, payload)

def pb_timestamp(field_num: int, dt: datetime) -> bytes:
    """Encode google.protobuf.Timestamp {int64 seconds = 1; int32 nanos = 2;}."""
    try:
        utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        epoch = utc.timestamp()
    except (OSError, OverflowError, ValueError):
        utc = datetime(1970, 1, 1, tzinfo=timezone.utc)
        epoch = 0.0
    if epoch < 0:
        epoch = 0.0
        nanos = 0
    else:
        nanos = utc.microsecond * 1000
    secs = int(epoch)
    parts = [pb_varint(1, secs)]
    if nanos:
        parts.append(pb_varint(2, nanos))
    return pb_msg(field_num, b"".join(parts))

def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a varint from `data` starting at `pos`."""
    n = len(data)
    result = 0
    shift = 0
    while pos < n:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 70:
            raise ValueError("Malformed varint")
    raise ValueError("Truncated varint")

def decode_pb(data: bytes) -> list[tuple[int, int, Any]]:
    """Decode raw protobuf wire bytes into [(field_num, wire_type, value), ...].

    Stops gracefully on truncated or unknown wire types so corrupt payloads
    never crash the tool.
    """
    fields: list[tuple[int, int, Any]] = []
    pos = 0
    n = len(data)
    while pos < n:
        try:
            key, pos = _read_varint(data, pos)
            field_num = key >> 3
            wire_type = key & 0x07
            if field_num <= 0:
                break
            if wire_type == 0:
                val, pos = _read_varint(data, pos)
                fields.append((field_num, 0, val))
            elif wire_type == 1:
                if pos + 8 > n:
                    break
                val = struct.unpack("<Q", data[pos : pos + 8])[0]
                pos += 8
                fields.append((field_num, 1, val))
            elif wire_type == 2:
                length, pos = _read_varint(data, pos)
                if pos + length > n:
                    break
                val = data[pos : pos + length]
                pos += length
                fields.append((field_num, 2, val))
            elif wire_type == 5:
                if pos + 4 > n:
                    break
                val = struct.unpack("<I", data[pos : pos + 4])[0]
                pos += 4
                fields.append((field_num, 5, val))
            else:
                break
        except ValueError:
            break
    return fields

def pb_first(
    fields: list[tuple[int, int, Any]], field_num: int, wire_type: int | None = None
) -> Any:
    """Return the first matching field value or None."""
    for fn, wt, val in fields:
        if fn == field_num and (wire_type is None or wt == wire_type):
            return val
    return None

def pb_all(
    fields: list[tuple[int, int, Any]], field_num: int, wire_type: int | None = None
) -> list[Any]:
    """Return all matching field values in order."""
    return [
        val
        for fn, wt, val in fields
        if fn == field_num and (wire_type is None or wt == wire_type)
    ]

def pb_str_val(fields: list[tuple[int, int, Any]], field_num: int) -> str:
    """Return the first UTF-8 string value for `field_num`, or ''."""
    raw = pb_first(fields, field_num, 2)
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", errors="replace")
    return ""

def pb_timestamp_val(fields: list[tuple[int, int, Any]], field_num: int) -> datetime | None:
    """Decode google.protobuf.Timestamp sub-message into a UTC datetime."""
    raw = pb_first(fields, field_num, 2)
    if not isinstance(raw, (bytes, bytearray)):
        return None
    sub = decode_pb(raw)
    secs = pb_first(sub, 1, 0)
    if secs is None:
        return None
    nanos = pb_first(sub, 2, 0) or 0
    try:
        dt = datetime.fromtimestamp(secs + nanos / 1_000_000_000, tz=timezone.utc)
        if dt.year < 1970:
            return None
        return dt
    except (OverflowError, OSError, ValueError):
        return None

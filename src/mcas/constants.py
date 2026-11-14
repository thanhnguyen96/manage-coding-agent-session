from __future__ import annotations

import re

APP_NAME = "mcas"

VERSION = "0.2.0"

DEFAULT_PORT = 47831

MAX_REQUEST_BODY_BYTES = 4 * 1024 * 1024

MAX_HISTORY_ENTRIES = 100

TOOL_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

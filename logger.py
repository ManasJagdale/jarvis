"""
logger.py

Writes one JSON object per line to a local log file, recording every tool
call Jarvis makes and its result. JSON lines was chosen over plain text
because you'll likely want to filter/query this later (e.g. "show me every
destructive action this week") once real tools are wired up — and it's
barely more code than plain text.

Each line looks like:
{"timestamp": "2026-07-12T14:32:01", "tool": "list_files",
 "args": {"path": "C:\\Users\\Manas"}, "result_summary": "...", "error": null}
"""

import json
from datetime import datetime, timezone

from config import LOG_FILE


def log_action(tool_name: str, args: dict, result: str, error: str | None = None) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": tool_name,
        "args": args,
        # Truncate long results so the log file stays readable/scannable.
        "result_summary": result[:300],
        "error": error,
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

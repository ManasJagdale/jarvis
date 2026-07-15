"""
memory.py

Long-term memory store for Jarvis -- facts and standing instructions that
persist ACROSS conversations and process restarts, independent of any one
chat session. Deliberately separate from:

  - jarvis_log.jsonl (logger.py) -- a log of every tool call ever made,
    not a curated store of things to remember.
  - The in-RAM `messages` list in main.py/gui.py -- gone the moment the
    process exits.

Storage is a flat JSON file (a list of {"id", "text", "timestamp"}
objects) -- plenty for the scale of "a few dozen personal facts," no
need for SQLite here. Every read re-loads from disk (no in-memory cache)
so the current process, a restarted one, or a future background task all
see the latest state rather than a stale copy from whenever they started.
"""

import json
import os
from datetime import datetime, timezone

from config import MEMORY_FILE


def _load() -> list[dict]:
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save(entries: list[dict]) -> None:
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def _next_id(entries: list[dict]) -> int:
    return max((e["id"] for e in entries), default=0) + 1


def remember_fact(fact: str) -> str:
    entries = _load()
    new_id = _next_id(entries)
    entries.append(
        {
            "id": new_id,
            "text": fact,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    _save(entries)
    return f"Remembered (#{new_id}): {fact}"


def recall_memory(query: str = "") -> str:
    entries = _load()
    if not entries:
        return "No memories stored yet."

    if query:
        query_lower = query.lower()
        entries = [e for e in entries if query_lower in e["text"].lower()]
        if not entries:
            return f"No memories match '{query}'."

    return "\n".join(f"#{e['id']}: {e['text']}" for e in entries)


def forget_fact(query: str) -> str:
    entries = _load()
    if not entries:
        return "No memories stored yet -- nothing to forget."

    stripped = query.strip()
    if stripped.isdigit():
        matches = [e for e in entries if e["id"] == int(stripped)]
    else:
        query_lower = stripped.lower()
        matches = [e for e in entries if query_lower in e["text"].lower()]

    if not matches:
        return f"No memory found matching '{query}'."

    if len(matches) > 1:
        listing = "\n".join(f"#{e['id']}: {e['text']}" for e in matches)
        return (
            f"'{query}' matches {len(matches)} memories -- be more specific "
            f"(e.g. use the exact id number) before I delete anything:\n{listing}"
        )

    match = matches[0]
    _save([e for e in entries if e["id"] != match["id"]])
    return f"Forgot #{match['id']}: {match['text']}"


def get_memory_context() -> str:
    entries = _load()
    if not entries:
        return ""
    lines = "\n".join(f"- {e['text']}" for e in entries)
    return f"Known facts about the user (from memory):\n{lines}"

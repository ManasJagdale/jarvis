"""
tools.py

Every tool has two parts:
  1. A schema (name, description, parameters) so the LLM knows the tool
     exists and how to call it.
  2. A Python function that actually does the work when called.

For step 1 of the build (proving the agent loop works end-to-end), there
is exactly one tool: list_files. Real tools (move/rename/read, run script)
get added one at a time after this loop is proven solid — see the spec's
build order.

No path restriction is applied here (per your choice of unrestricted
access) — the LLM can ask to list any directory on the machine. This tool
is read-only and non-destructive, so it doesn't go through the
confirmation gate (that gate gets added when we wire up destructive tools).
"""

import fnmatch
import os
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime
from urllib.parse import urlparse

import psutil
import pyperclip
import requests
import send2trash
from bs4 import BeautifulSoup

import calendar_client
import file_analysis
import memory
from config import GOOGLE_SEARCH_API_KEY, GOOGLE_SEARCH_ENGINE_ID, MAX_FETCH_CHARS

TOOL_SCHEMAS = [
    {
        "name": "list_files",
        "description": (
            "List the files and folders in a given directory on the local "
            "filesystem. Use this to see what's in a folder before deciding "
            "on further action."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the directory to list.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": "Read and return the text contents of a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to read.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "create_file",
        "description": (
            "Create a new text file at the given path with the given "
            "content, creating any missing parent folders along the way. "
            "Use this directly for any 'make/create/write a file that "
            "says...' request -- do not run a script or shell command to "
            "write the file instead. If the file already exists, this "
            "overwrites it and is confirmation-gated."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Full path for the new file, including filename and extension.",
                },
                "content": {
                    "type": "string",
                    "description": "Text content to write into the file. Defaults to empty.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "move_file",
        "description": (
            "Move a file from one path to another, optionally into a "
            "different directory. Overwrites the destination if it already "
            "exists. This is destructive — not yet confirmation-gated."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Path to the file to move."},
                "destination": {"type": "string", "description": "Target path."},
            },
            "required": ["source", "destination"],
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search recursively under a root folder for files OR folders "
            "whose name matches a pattern (results are tagged [DIR]/[FILE]). "
            "Use this when the user doesn't give an exact path — e.g. 'find "
            "the budget spreadsheet in Downloads', or 'find my mamma "
            "project folder'. If the user mentions a folder name you don't "
            "know the exact path for, search for that folder FIRST and use "
            "the result to narrow your next search or action, rather than "
            "searching an entire drive. Pattern can be a plain substring "
            "('budget') or a glob ('*.xlsx')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Substring or glob pattern to match against filenames.",
                },
                "root": {
                    "type": "string",
                    "description": "Directory to search under, recursively.",
                },
            },
            "required": ["pattern", "root"],
        },
    },
    {
        "name": "rename_file",
        "description": (
            "Rename a file in place (same directory, new filename). "
            "Overwrites the destination if it already exists. This is "
            "destructive — not yet confirmation-gated."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to rename."},
                "new_name": {
                    "type": "string",
                    "description": "New filename only (not a full path).",
                },
            },
            "required": ["path", "new_name"],
        },
    },
    {
        "name": "run_script",
        "description": (
            "Run a local script and capture its output. Infer script_path "
            "and any needed command-line args from what the user says in "
            "plain language, using context from the conversation (e.g. "
            "earlier search_files results) to resolve an exact path. This "
            "can run scripts that modify real data, so it is "
            "confirmation-gated unless the script's exact path has been "
            "explicitly added to TRUSTED_SCRIPTS."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script_path": {
                    "type": "string",
                    "description": "Absolute or relative path to the script to run.",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Command-line arguments to pass to the script, if any.",
                },
            },
            "required": ["script_path"],
        },
    },
    {
        "name": "open_path_or_url",
        "description": (
            "Open a file, folder, or URL using whatever the OS considers "
            "the default application for it (e.g. a .pdf opens in the PDF "
            "viewer, a folder opens in File Explorer, a URL opens in the "
            "default browser). Use for any 'open X' request."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "A file path, folder path, or URL (http/https) to open.",
                }
            },
            "required": ["target"],
        },
    },
    {
        "name": "copy_file",
        "description": (
            "Copy a file to a new location, leaving the original in place. "
            "Overwrites the destination if it already exists."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Path to the file to copy."},
                "destination": {"type": "string", "description": "Target path for the copy."},
            },
            "required": ["source", "destination"],
        },
    },
    {
        "name": "delete_file",
        "description": (
            "Delete a SINGLE FILE (not a folder -- use delete_folder for "
            "that). By default sends it to the Recycle Bin (recoverable -- "
            "does NOT require confirmation). Only set permanent=true if the "
            "user explicitly asks for permanent, unrecoverable deletion "
            "(e.g. 'delete forever', 'permanently delete', 'don't send it "
            "to the recycle bin') -- that mode bypasses the Recycle Bin "
            "entirely and IS confirmation-gated."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to delete."},
                "permanent": {
                    "type": "boolean",
                    "description": "If true, bypass the Recycle Bin. Defaults to false.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "delete_folder",
        "description": (
            "Delete an entire folder and everything inside it. By default "
            "sends it to the Recycle Bin (recoverable -- does NOT require "
            "confirmation). Only set permanent=true if the user explicitly "
            "asks for permanent, unrecoverable deletion (e.g. 'delete "
            "forever', 'permanently delete', 'don't send it to the recycle "
            "bin') -- that mode bypasses the Recycle Bin entirely and IS "
            "confirmation-gated. Use this instead of delete_file whenever "
            "the target is a folder/directory, not a single file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the folder to delete."},
                "permanent": {
                    "type": "boolean",
                    "description": "If true, bypass the Recycle Bin. Defaults to false.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_current_datetime",
        "description": "Get the current local date and time. Use this whenever 'today', 'now', or a relative date/time reference comes up.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "read_clipboard",
        "description": "Read the current text contents of the system clipboard.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "write_clipboard",
        "description": "Write text to the system clipboard, replacing whatever's there.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to copy to the clipboard."}},
            "required": ["text"],
        },
    },
    {
        "name": "system_info",
        "description": "Get basic system status: free/total disk space on a drive, and battery level if applicable.",
        "parameters": {
            "type": "object",
            "properties": {
                "drive": {
                    "type": "string",
                    "description": "Drive letter/root to check disk space for, e.g. 'C:\\\\'. Defaults to C:\\\\.",
                }
            },
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the web for current information using Google. Use "
            "this for anything you wouldn't already know -- current "
            "events, prices, recent releases, facts about things after "
            "your training cutoff, etc. Returns titles, snippets, and "
            "URLs for the top results -- use fetch_url on a specific "
            "result afterward if you need the full page content rather "
            "than just the snippet."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "num_results": {
                    "type": "integer",
                    "description": "How many results to return (1-10). Defaults to 5.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Fetch a web page and return its readable text content with "
            "HTML stripped out. Use this after web_search when a snippet "
            "isn't enough detail, or whenever the user gives you a "
            "direct URL to look at."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL, including http:// or https://.",
                }
            },
            "required": ["url"],
        },
    },
    {
        "name": "list_processes",
        "description": (
            "List running processes on the machine (PID, name, memory "
            "use), sorted by memory use. Optionally filter by a name "
            "substring, e.g. 'chrome'. Always call this before "
            "kill_process to confirm you have the right PID -- never "
            "guess a PID."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name_filter": {
                    "type": "string",
                    "description": "Only show processes whose name contains this substring (case-insensitive). Omit to list the top processes by memory use.",
                }
            },
        },
    },
    {
        "name": "kill_process",
        "description": (
            "Terminate a running process by PID. This is destructive -- "
            "it can lose unsaved work in that program -- so ALWAYS call "
            "list_processes first in the same turn to confirm the exact "
            "PID rather than guessing one from context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pid": {"type": "integer", "description": "Process ID to terminate."}
            },
            "required": ["pid"],
        },
    },
    {
        "name": "create_reminder",
        "description": (
            "Create a Google Calendar event as a reminder. Use for any "
            "'remind me to X at/on Y' request. Resolve start_datetime "
            "into a concrete ISO 8601 local datetime (e.g. "
            "'2026-07-14T09:00:00') from whatever the user says -- call "
            "get_current_datetime first if you need to work out a "
            "relative time like 'in 20 minutes' or 'tomorrow at 9am'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Short title for the reminder/event.",
                },
                "start_datetime": {
                    "type": "string",
                    "description": "ISO 8601 local datetime, e.g. '2026-07-14T09:00:00'.",
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Event duration in minutes. Defaults to 30.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional longer note for the event.",
                },
            },
            "required": ["summary", "start_datetime"],
        },
    },
    {
        "name": "remember_fact",
        "description": (
            "Save a fact or standing instruction to long-term memory, so "
            "it's still known in future conversations and after a restart "
            "-- not just for the rest of this chat. Use this when the user "
            "tells you something worth remembering about themselves, their "
            "preferences, or how they want you to behave. Don't use this "
            "for one-off task details that only matter for the current "
            "request."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "The fact or instruction to remember, written as a standalone statement.",
                }
            },
            "required": ["fact"],
        },
    },
    {
        "name": "recall_memory",
        "description": (
            "List facts previously saved with remember_fact. Optionally "
            "filter with a substring query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional substring to filter remembered facts by. Omit to list everything.",
                }
            },
        },
    },
    {
        "name": "forget_fact",
        "description": (
            "Delete a previously remembered fact. query can be the exact "
            "id number shown by recall_memory, or a substring of the fact "
            "text. Destructive and confirmation-gated."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The id number or a substring identifying which memory to delete.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "analyze_file",
        "description": (
            "Extract raw text/structure from a PDF or Excel (.xlsx) file. "
            "Returns the extracted content as-is -- it does NOT summarize "
            "or decide what matters; that's your job based on what the "
            "user actually asked for. For PDFs: extracts the real text "
            "layer, or falls back to OCR automatically if the PDF is "
            "scanned (no text layer) -- the result says which happened. "
            "For Excel: extracts every sheet's data plus any cell formulas "
            "(shown as 'C4: =B4*1.05 -> 47.25'), not just computed values. "
            "Set extract_visuals=True (PDF only) to ALSO crop and save "
            "diagrams and formula-looking regions as image files -- only "
            "do this when the user actually asks for diagrams/formulas to "
            "be pulled out, since it adds real processing time; it requires "
            "output_dir to be set."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the PDF or .xlsx file.",
                },
                "extract_visuals": {
                    "type": "boolean",
                    "description": "PDF only. If true, also save diagrams/formula regions as images to output_dir. Defaults to false.",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Folder to save extracted diagram/formula images to. Required if extract_visuals is true. Use an existing folder the user names, or a new subfolder they ask you to create.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "edit_excel_cells",
        "description": (
            "Apply in-place cell/formula edits to an EXISTING Excel file "
            "-- modifies the real file on disk, unlike analyze_file which "
            "is read-only. Use this for direct instructions like 'change "
            "cell C4 to 50' or 'fix the formula in column C' -- not for "
            "'summarize this sheet' (that's analyze_file). Destructive, "
            "confirmation-gated."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the existing .xlsx file.",
                },
                "edits": {
                    "type": "object",
                    "description": (
                        "Map of cell coordinate -> new value or formula string, e.g. "
                        "{'C4': '=B4*1.05'}. Prefix with 'SheetName!' to target a "
                        "sheet other than the first, e.g. {'Sheet2!C4': 50}."
                    ),
                },
            },
            "required": ["path", "edits"],
        },
    },
    {
        "name": "edit_file_text",
        "description": (
            "Make a targeted edit to a text file (e.g. a .py file) by "
            "exact find-and-replace -- old_text must appear exactly once "
            "in the file, or this fails with an error rather than "
            "guessing which occurrence was meant. Use this instead of "
            "rewriting the whole file with create_file when only part of "
            "a file needs to change. Destructive, confirmation-gated -- "
            "including for Jarvis's own source files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the text file to edit.",
                },
                "old_text": {
                    "type": "string",
                    "description": "Exact text to find. Must be unique in the file.",
                },
                "new_text": {
                    "type": "string",
                    "description": "Text to replace it with.",
                },
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
]


def list_files(path: str) -> str:
    """Return a plain-text listing of a directory, or a clear error message."""
    if not os.path.exists(path):
        return f"Error: the path '{path}' does not exist."
    if not os.path.isdir(path):
        return f"Error: '{path}' exists but is not a directory."

    try:
        entries = os.listdir(path)
    except PermissionError:
        return f"Error: permission denied reading '{path}'."

    if not entries:
        return f"'{path}' is empty."

    lines = []
    for entry in sorted(entries):
        full = os.path.join(path, entry)
        kind = "DIR " if os.path.isdir(full) else "FILE"
        lines.append(f"[{kind}] {entry}")
    return "\n".join(lines)


def read_file(path: str) -> str:
    """Return a file's text contents, or a clear error message."""
    if not os.path.exists(path):
        return f"Error: the path '{path}' does not exist."
    if not os.path.isfile(path):
        return f"Error: '{path}' exists but is not a file."

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except PermissionError:
        return f"Error: permission denied reading '{path}'."


def create_file(path: str, content: str = "") -> str:
    """
    Create a new text file with the given content, creating any missing
    parent directories along the way. If the file already exists, this
    OVERWRITES it -- whether that requires confirmation first is decided
    in jarvis_core.py's needs_confirmation() (gated only when the target
    path already exists; a brand-new file is harmless).
    """
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except PermissionError:
        return f"Error: permission denied creating '{path}'."
    except OSError as exc:
        return f"Error creating '{path}': {exc}"

    return f"Created '{path}' ({len(content)} characters)."


def move_file(source: str, destination: str) -> str:
    """Move a file to a new path. Overwrites the destination if present."""
    if not os.path.exists(source):
        return f"Error: source path '{source}' does not exist."
    if not os.path.isfile(source):
        return f"Error: source '{source}' is not a file."

    try:
        shutil.move(source, destination)
    except PermissionError:
        return f"Error: permission denied moving '{source}' to '{destination}'."
    except OSError as exc:
        return f"Error moving '{source}' to '{destination}': {exc}"

    return f"Moved '{source}' to '{destination}'."


def search_files(pattern: str, root: str, max_results: int = 20, max_scanned: int = 50000) -> str:
    """
    Walk `root` recursively, matching filenames against `pattern`.
    Glob characters (* or ?) trigger glob matching; otherwise it's a
    case-insensitive substring match. Capped on both results and total
    files scanned so a broad root (like a whole drive) can't hang.
    """
    if not os.path.exists(root):
        return f"Error: the root path '{root}' does not exist."
    if not os.path.isdir(root):
        return f"Error: '{root}' is not a directory."

    is_glob = any(ch in pattern for ch in "*?[]")
    pattern_lower = pattern.lower()

    matches = []
    scanned = 0
    truncated_scan = False

    def on_error(exc):
        pass  # skip directories we can't read (e.g. permission denied) silently

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        # Check directory names first, so a folder like "mamma project" can
        # itself be found and used to narrow a follow-up search.
        for name in dirnames:
            scanned += 1
            if scanned > max_scanned:
                truncated_scan = True
                break
            is_match = fnmatch.fnmatch(name.lower(), pattern_lower) if is_glob \
                else pattern_lower in name.lower()
            if is_match:
                matches.append(f"[DIR ] {os.path.join(dirpath, name)}")
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results or truncated_scan:
            break

        for name in filenames:
            scanned += 1
            if scanned > max_scanned:
                truncated_scan = True
                break

            is_match = fnmatch.fnmatch(name.lower(), pattern_lower) if is_glob \
                else pattern_lower in name.lower()

            if is_match:
                matches.append(f"[FILE] {os.path.join(dirpath, name)}")
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results or truncated_scan:
            break

    if not matches:
        note = " (search was capped before finishing)" if truncated_scan else ""
        return f"No files matching '{pattern}' found under '{root}'{note}."

    header = f"Found {len(matches)} match(es) for '{pattern}' under '{root}'"
    if len(matches) >= max_results:
        header += f" (showing first {max_results}, there may be more)"
    if truncated_scan:
        header += " [search was capped before scanning everything]"

    return header + ":\n" + "\n".join(matches)


def rename_file(path: str, new_name: str) -> str:
    """Rename a file in place. Overwrites the destination if present."""
    if not os.path.exists(path):
        return f"Error: the path '{path}' does not exist."
    if not os.path.isfile(path):
        return f"Error: '{path}' is not a file."
    if os.sep in new_name or (os.altsep and os.altsep in new_name):
        return f"Error: '{new_name}' must be a filename only, not a path."

    new_path = os.path.join(os.path.dirname(path), new_name)
    try:
        os.replace(path, new_path)
    except PermissionError:
        return f"Error: permission denied renaming '{path}'."
    except OSError as exc:
        return f"Error renaming '{path}' to '{new_name}': {exc}"

    return f"Renamed '{path}' to '{new_path}'."


def run_script(script_path: str, args: list[str] | None = None) -> str:
    """
    Execute a local script and capture its output. Supports .py (via the
    same Python interpreter running Jarvis), .bat/.cmd, .ps1, and directly
    executable files (e.g. .exe). No timeout is applied -- a hung script
    will hang this call too.
    """
    args = args or []

    if not os.path.exists(script_path):
        return f"Error: the script '{script_path}' does not exist."
    if not os.path.isfile(script_path):
        return f"Error: '{script_path}' is not a file."

    lower = script_path.lower()
    if lower.endswith(".py"):
        cmd = [sys.executable, script_path, *args]
    elif lower.endswith((".bat", ".cmd")):
        cmd = ["cmd", "/c", script_path, *args]
    elif lower.endswith(".ps1"):
        cmd = ["powershell", "-File", script_path, *args]
    else:
        cmd = [script_path, *args]

    try:
        completed = subprocess.run(cmd, capture_output=True, text=True)
    except PermissionError:
        return f"Error: permission denied running '{script_path}'."
    except OSError as exc:
        return f"Error running '{script_path}': {exc}"

    parts = [f"Exit code: {completed.returncode}"]
    if completed.stdout:
        parts.append(f"--- stdout ---\n{completed.stdout.strip()}")
    if completed.stderr:
        parts.append(f"--- stderr ---\n{completed.stderr.strip()}")
    return "\n".join(parts)


def open_path_or_url(target: str) -> str:
    """Open a file/folder with its default app, or a URL in the default browser."""
    parsed = urlparse(target)
    if parsed.scheme in ("http", "https"):
        webbrowser.open(target)
        return f"Opened URL: {target}"

    if not os.path.exists(target):
        return f"Error: '{target}' does not exist and isn't a recognized URL."

    try:
        os.startfile(target)  # Windows-only; matches the spec's target OS
    except OSError as exc:
        return f"Error opening '{target}': {exc}"
    return f"Opened: {target}"


def copy_file(source: str, destination: str) -> str:
    """Copy a file, leaving the original untouched. Overwrites destination if present."""
    if not os.path.exists(source):
        return f"Error: source path '{source}' does not exist."
    if not os.path.isfile(source):
        return f"Error: source '{source}' is not a file."

    try:
        shutil.copy2(source, destination)
    except PermissionError:
        return f"Error: permission denied copying '{source}' to '{destination}'."
    except OSError as exc:
        return f"Error copying '{source}' to '{destination}': {exc}"

    return f"Copied '{source}' to '{destination}'."


def delete_file(path: str, permanent: bool = False) -> str:
    """
    Delete a file. Default: send to Recycle Bin (recoverable). If
    permanent=True: bypass the bin entirely via os.remove (not recoverable).
    Whether this needs confirmation is decided in main.py, based on the
    `permanent` flag -- not decided here.
    """
    if not os.path.exists(path):
        return f"Error: the path '{path}' does not exist."
    if os.path.isdir(path):
        return (
            f"Error: '{path}' is a folder, not a file. Use the "
            f"delete_folder tool instead."
        )
    if not os.path.isfile(path):
        return f"Error: '{path}' is not a file."

    if permanent:
        try:
            os.remove(path)
        except PermissionError:
            return f"Error: permission denied deleting '{path}'."
        except OSError as exc:
            return f"Error permanently deleting '{path}': {exc}"
        return f"Permanently deleted '{path}' (bypassed Recycle Bin -- not recoverable)."

    try:
        send2trash.send2trash(path)
    except Exception as exc:  # noqa: BLE001 - send2trash raises various OS-specific errors
        return f"Error sending '{path}' to the Recycle Bin: {exc}"
    return f"Sent '{path}' to the Recycle Bin (recoverable)."


def delete_folder(path: str, permanent: bool = False) -> str:
    """
    Delete an entire folder and its contents. Default: send to Recycle Bin
    (recoverable, via send2trash which handles folders natively). If
    permanent=True: bypass the bin via shutil.rmtree (not recoverable).
    Whether this needs confirmation is decided in main.py, based on the
    `permanent` flag -- not decided here.
    """
    if not os.path.exists(path):
        return f"Error: the path '{path}' does not exist."
    if not os.path.isdir(path):
        return (
            f"Error: '{path}' is not a folder. Use the delete_file tool "
            f"instead."
        )

    if permanent:
        try:
            shutil.rmtree(path)
        except PermissionError:
            return f"Error: permission denied deleting '{path}'."
        except OSError as exc:
            return f"Error permanently deleting '{path}': {exc}"
        return f"Permanently deleted folder '{path}' (bypassed Recycle Bin -- not recoverable)."

    try:
        send2trash.send2trash(path)
    except Exception as exc:  # noqa: BLE001 - send2trash raises various OS-specific errors
        return f"Error sending folder '{path}' to the Recycle Bin: {exc}"
    return f"Sent folder '{path}' to the Recycle Bin (recoverable)."


def get_current_datetime() -> str:
    return datetime.now().strftime("%A, %Y-%m-%d %H:%M:%S")


def read_clipboard() -> str:
    try:
        content = pyperclip.paste()
    except Exception as exc:  # noqa: BLE001
        return f"Error reading clipboard: {exc}"
    return content if content else "Clipboard is empty."


def write_clipboard(text: str) -> str:
    try:
        pyperclip.copy(text)
    except Exception as exc:  # noqa: BLE001
        return f"Error writing to clipboard: {exc}"
    return "Copied to clipboard."


def system_info(drive: str = "C:\\") -> str:
    lines = []
    try:
        total, _used, free = shutil.disk_usage(drive)
        lines.append(
            f"Disk {drive}: {free / (1024**3):.1f} GB free of {total / (1024**3):.1f} GB total"
        )
    except OSError as exc:
        lines.append(f"Error reading disk usage for '{drive}': {exc}")

    try:
        battery = psutil.sensors_battery()
        if battery is not None:
            status = "plugged in" if battery.power_plugged else "on battery"
            lines.append(f"Battery: {battery.percent}% ({status})")
        else:
            lines.append("Battery: no battery detected (desktop machine?).")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"Error reading battery status: {exc}")

    return "\n".join(lines)


def web_search(query: str, num_results: int = 5) -> str:
    """Search the web via Google Custom Search JSON API."""
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_ENGINE_ID:
        return (
            "Error: web search isn't configured yet. Set "
            "GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_ENGINE_ID as "
            "environment variables (see config.py's comments for where "
            "to get them)."
        )

    num_results = max(1, min(num_results, 10))
    try:
        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": GOOGLE_SEARCH_API_KEY,
                "cx": GOOGLE_SEARCH_ENGINE_ID,
                "q": query,
                "num": num_results,
            },
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return f"Error performing web search: {exc}"

    items = response.json().get("items", [])
    if not items:
        return f"No search results found for '{query}'."

    lines = [f"Search results for '{query}':"]
    for i, item in enumerate(items, 1):
        title = item.get("title", "(no title)")
        link = item.get("link", "")
        snippet = item.get("snippet", "").replace("\n", " ")
        lines.append(f"{i}. {title}\n   {link}\n   {snippet}")
    return "\n".join(lines)


def fetch_url(url: str) -> str:
    """Fetch a URL and return its stripped, readable text content."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Error: '{url}' doesn't look like a valid http(s) URL."

    try:
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except requests.RequestException as exc:
        return f"Error fetching '{url}': {exc}"

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    if len(text) > MAX_FETCH_CHARS:
        text = text[:MAX_FETCH_CHARS] + "\n...[truncated]"
    return text


def list_processes(name_filter: str = None, max_results: int = 30) -> str:
    """List running processes, optionally filtered by name substring, sorted by memory use."""
    procs = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            info = proc.info
            name = info.get("name") or ""
            if name_filter and name_filter.lower() not in name.lower():
                continue
            mem_info = info.get("memory_info")
            mem_mb = (mem_info.rss / (1024 ** 2)) if mem_info else 0.0
            procs.append((info["pid"], name, mem_mb))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue  # process exited or is inaccessible mid-scan -- skip it, don't crash the whole listing

    if not procs:
        note = f" matching '{name_filter}'" if name_filter else ""
        return f"No running processes found{note}."

    procs.sort(key=lambda p: p[2], reverse=True)
    truncated = len(procs) > max_results
    procs = procs[:max_results]

    header = f"{len(procs)} process(es)"
    if name_filter:
        header += f" matching '{name_filter}'"
    if truncated:
        header += f" (showing top {max_results} by memory use)"

    lines = [header + ":"]
    for pid, name, mem_mb in procs:
        lines.append(f"  PID {pid}: {name} -- {mem_mb:.1f} MB")
    return "\n".join(lines)


def kill_process(pid: int) -> str:
    """
    Terminate a process by PID. Tries a graceful terminate() first, falls
    back to a forceful kill() if it doesn't exit within a few seconds.
    Whether this needs confirmation is decided in jarvis_core.py, not here.
    """
    try:
        proc = psutil.Process(pid)
        name = proc.name()
    except psutil.NoSuchProcess:
        return f"Error: no process with PID {pid} exists."
    except psutil.AccessDenied:
        return f"Error: permission denied accessing PID {pid}."

    try:
        proc.terminate()
        proc.wait(timeout=3)
    except psutil.TimeoutExpired:
        try:
            proc.kill()
        except psutil.AccessDenied:
            return f"Error: permission denied force-killing PID {pid} ({name})."
    except psutil.NoSuchProcess:
        pass  # already gone by the time we checked -- treat as success
    except psutil.AccessDenied:
        return f"Error: permission denied terminating PID {pid} ({name})."

    return f"Terminated process PID {pid} ({name})."


def create_reminder(
    summary: str, start_datetime: str, duration_minutes: int = 30, description: str = ""
) -> str:
    """Create a Google Calendar event via calendar_client.py."""
    try:
        return calendar_client.create_event(summary, start_datetime, duration_minutes, description)
    except ValueError as exc:
        return f"Error: couldn't parse start_datetime '{start_datetime}': {exc}"
    except Exception as exc:  # noqa: BLE001 - surfaces OAuth/setup errors clearly rather than crashing the turn
        return f"Error creating calendar event: {exc}"


def remember_fact(fact: str) -> str:
    """Thin wrapper so TOOL_REGISTRY's shape matches every other tool -- see memory.py for the real logic."""
    return memory.remember_fact(fact)


def recall_memory(query: str = "") -> str:
    return memory.recall_memory(query)


def forget_fact(query: str) -> str:
    return memory.forget_fact(query)


def analyze_file(path: str, extract_visuals: bool = False, output_dir: str = "") -> str:
    """Thin wrapper -- see file_analysis.py for the real logic."""
    return file_analysis.analyze_file(path, extract_visuals, output_dir)


def edit_excel_cells(path: str, edits: dict) -> str:
    return file_analysis.edit_excel_cells(path, edits)


def edit_file_text(path: str, old_text: str, new_text: str) -> str:
    return file_analysis.edit_file_text(path, old_text, new_text)


# Maps tool name -> the actual function to call.
# When you add a new tool: add its schema above and its function+entry here.
TOOL_REGISTRY = {
    "list_files": list_files,
    "read_file": read_file,
    "create_file": create_file,
    "move_file": move_file,
    "rename_file": rename_file,
    "search_files": search_files,
    "run_script": run_script,
    "open_path_or_url": open_path_or_url,
    "copy_file": copy_file,
    "delete_file": delete_file,
    "delete_folder": delete_folder,
    "get_current_datetime": get_current_datetime,
    "read_clipboard": read_clipboard,
    "write_clipboard": write_clipboard,
    "system_info": system_info,
    "web_search": web_search,
    "fetch_url": fetch_url,
    "list_processes": list_processes,
    "kill_process": kill_process,
    "create_reminder": create_reminder,
    "remember_fact": remember_fact,
    "recall_memory": recall_memory,
    "forget_fact": forget_fact,
    "analyze_file": analyze_file,
    "edit_excel_cells": edit_excel_cells,
    "edit_file_text": edit_file_text,
}

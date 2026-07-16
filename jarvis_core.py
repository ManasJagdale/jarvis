"""
jarvis_core.py

The agent loop logic, extracted out of main.py so both the CLI (main.py),
the desktop GUI (gui.py), and now the remote web server (server.py) can
share it without duplicating code.

A caller only has to supply:
  - an LLMClient instance (e.g. GeminiClient())
  - confirm_fn(description: str) -> bool, used to gate destructive tool
    calls. main.py passes the terminal-based confirm_action from
    confirm.py; server.py passes a WebSocket-based one that waits for a
    Confirm/Cancel tap from the browser (with a timeout). This module
    doesn't care which -- that's the whole point of the swap.
  - optionally, on_tool_start/on_tool_end callbacks, so a UI can show
    "Running list_files..." instead of just freezing while a tool runs.
  - optionally, a stop_event (threading.Event) -- if it gets set while a
    turn is running, the loop bails out at the next safe checkpoint
    (before the next LLM call, and before each queued tool call) rather
    than running to completion. NOTE: this is cooperative cancellation
    only -- a tool call already in progress (e.g. a hung run_script or
    fetch_url) will still run to completion; there's no way to kill a
    blocking call from the outside without deeper changes to each tool.
    main.py doesn't pass a stop_event and behaves exactly as before.

Nothing here is Gemini-specific or Tkinter-specific.
"""

import os
import threading
from typing import Callable, Optional

import memory
from config import MAX_TOOL_ITERATIONS, TRUSTED_SCRIPTS
from logger import log_action
from tools import TOOL_REGISTRY, TOOL_SCHEMAS

# Tools that ALWAYS require confirmation before running, no exceptions.
# edited by Jarvis test
ALWAYS_CONFIRM_TOOLS = {
    "move_file",
    "rename_file",
    "kill_process",
    "forget_fact",
    "edit_excel_cells",
    "edit_file_text",
}

STOPPED_MESSAGE = "Stopped by user."


def _windows_username() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "<unknown>"


_BASE_SYSTEM_PROMPT = (
    f"You are Jarvis, a local assistant running on Manas's computer "
    f"(Windows username: {_windows_username()}). "
    "You can call tools to inspect the filesystem. Be concise. If a request "
    "is ambiguous (e.g. 'clean up my downloads'), ask a clarifying question "
    "instead of guessing what the user means. "
    "For standard Windows user folders (Desktop, Downloads, Documents, "
    f"Pictures, etc.), use the path C:\\Users\\{_windows_username()}\\<folder "
    "name> directly rather than searching for it -- these are fixed, "
    "well-known locations, not something to look up with search_files. Only "
    "search if that direct path turns out to be wrong. "
    "To create a new file with specific text content, call create_file "
    "directly with the path and content -- do NOT write or run a script "
    "(e.g. a PowerShell one-liner via run_script) to do this, and do NOT "
    "read through the codebase (tools.py, etc.) to figure out how -- "
    "create_file is always available for exactly this. "
    "You have long-term memory via remember_fact/recall_memory/forget_fact "
    "-- proactively save durable facts or standing preferences the user "
    "shares (not one-off task details), and check the 'Known facts' block "
    "below before asking the user something you might already know. "
    "For PDF or Excel files, use analyze_file to extract their content -- "
    "it returns raw text/data, not a summary, so apply whatever the user "
    "actually asked for on top of that. Use edit_excel_cells for direct "
    "instructions to change specific cells/formulas in an existing Excel "
    "file, not for 'summarize this sheet' requests. Use edit_file_text for "
    "targeted changes to part of a text/code file rather than rewriting "
    "the whole file with create_file."
)


def build_system_prompt() -> str:
    """
    Assemble the system prompt fresh for every LLM call (not once at
    import time), so memory is always current -- including a fact
    remembered earlier in this same conversation, or one saved by a
    process that has since restarted.
    """
    memory_context = memory.get_memory_context()
    if not memory_context:
        return _BASE_SYSTEM_PROMPT
    return f"{_BASE_SYSTEM_PROMPT}\n\n{memory_context}"


def needs_confirmation(name: str, args: dict) -> bool:
    """
    Whether a tool call must be confirmed before running.

    move_file/rename_file always require it. delete_file only requires it
    when permanent=True (Recycle Bin deletes are recoverable). create_file
    only requires it when it would overwrite a file that already exists
    (a brand-new file is harmless). run_script requires confirmation
    UNLESS its exact path is in TRUSTED_SCRIPTS, an allowlist maintained
    by hand in config.py.
    """
    if name in ALWAYS_CONFIRM_TOOLS:
        return True
    if name == "delete_file":
        return bool(args.get("permanent", False))
    if name == "create_file":
        return os.path.exists(os.path.abspath(args.get("path", "")))
    if name == "run_script":
        script_path = os.path.abspath(args.get("script_path", ""))
        return script_path not in TRUSTED_SCRIPTS
    return False


def describe_call(name: str, args: dict) -> str:
    """Plain-English description of a tool call, shown in the confirmation prompt."""
    if name == "move_file":
        return f"Move file:\n    {args.get('source')}\n  to:\n    {args.get('destination')}"
    if name == "rename_file":
        return f"Rename file:\n    {args.get('path')}\n  to filename:\n    {args.get('new_name')}"
    if name == "run_script":
        arg_str = " ".join(args.get("args", []) or []) or "(none)"
        return f"Run script:\n    {args.get('script_path')}\n  with args: {arg_str}"
    if name == "delete_file":
        return f"PERMANENTLY delete (bypassing Recycle Bin, NOT recoverable):\n    {args.get('path')}"
    if name == "kill_process":
        return f"Terminate process PID {args.get('pid')} -- this can lose any unsaved work in that program."
    if name == "forget_fact":
        return f"Delete remembered fact matching:\n    {args.get('query')}"
    if name == "edit_excel_cells":
        edits = args.get("edits", {}) or {}
        lines = "\n".join(f"    {coord} -> {value}" for coord, value in edits.items())
        return f"Edit Excel file:\n    {args.get('path')}\n  Cell changes:\n{lines}"
    if name == "edit_file_text":
        return (
            f"Edit file:\n    {args.get('path')}\n"
            f"  Replace:\n    {args.get('old_text')!r}\n"
            f"  With:\n    {args.get('new_text')!r}"
        )
    if name == "create_file":
        content_len = len(args.get("content", "") or "")
        return (
            f"Overwrite existing file:\n    {args.get('path')}\n"
            f"  with new content ({content_len} characters) -- the current "
            "contents will be replaced."
        )
    return f"Run '{name}' with arguments: {args}"


def execute_tool(
    name: str,
    args: dict,
    confirm_fn: Callable[[str], bool],
    on_tool_start: Optional[Callable[[str, dict], None]] = None,
    on_tool_end: Optional[Callable[[str, str], None]] = None,
) -> str:
    """
    Run a tool by name with the given args, catching any error so a bad
    tool call never crashes the caller. Every call -- success, failure, or
    cancellation -- gets logged.
    """
    if name not in TOOL_REGISTRY:
        result = f"Error: unknown tool '{name}'."
        log_action(name, args, result, error="unknown_tool")
        if on_tool_end:
            on_tool_end(name, result)
        return result

    if on_tool_start:
        on_tool_start(name, args)

    if needs_confirmation(name, args):
        if not confirm_fn(describe_call(name, args)):
            result = "Cancelled by user."
            log_action(name, args, result, error="cancelled_by_user")
            if on_tool_end:
                on_tool_end(name, result)
            return result

    try:
        result = TOOL_REGISTRY[name](**args)
        log_action(name, args, result)
    except Exception as exc:  # noqa: BLE001 - we want to catch anything here
        result = f"Error running tool '{name}': {exc}"
        log_action(name, args, result, error=str(exc))

    if on_tool_end:
        on_tool_end(name, result)
    return result


def run_turn(
    llm,
    messages: list[dict],
    confirm_fn: Callable[[str], bool],
    on_tool_start: Optional[Callable[[str, dict], None]] = None,
    on_tool_end: Optional[Callable[[str, str], None]] = None,
    stop_event: Optional[threading.Event] = None,
    on_assistant_text: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Handle one full user turn: call the LLM, execute any requested tools,
    feed results back, and repeat until the LLM gives a final text answer
    or we hit the max-iteration safety cap.

    If stop_event is provided and gets set from another thread (e.g. the
    web server handling a Stop button tap), the loop bails out at the
    next checkpoint -- before starting another LLM call, and before each
    individual queued tool call -- rather than running to completion.
    A tool call already in flight when Stop is pressed still finishes
    (see the module docstring); this is intentionally cooperative, not
    forceful, cancellation.
    """

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    for _iteration in range(MAX_TOOL_ITERATIONS):
        if stopped():
            messages.append({"role": "assistant", "content": STOPPED_MESSAGE})
            return STOPPED_MESSAGE

        response = llm.generate(messages, TOOL_SCHEMAS, system_prompt=build_system_prompt())

        if not response.tool_calls:
            messages.append({"role": "assistant", "content": response.text})
            return response.text

        messages.append(
            {
                "role": "assistant",
                "content": response.text,
                "tool_calls": response.tool_calls,
                "raw": response.raw,
            }
        )

        if response.text and on_assistant_text:
            on_assistant_text(response.text)

        for call in response.tool_calls:
            if stopped():
                messages.append({"role": "tool", "name": call["name"], "content": STOPPED_MESSAGE})
                return STOPPED_MESSAGE

            tool_result = execute_tool(
                call["name"], call["args"], confirm_fn, on_tool_start, on_tool_end
            )
            messages.append({"role": "tool", "name": call["name"], "content": tool_result})

    return (
        "I wasn't able to finish that request within the allowed number of "
        "tool calls -- something may be going wrong repeatedly. Check "
        "jarvis_log.jsonl for details, or try rephrasing."
    )

"""
config.py

Central place for settings: provider API keys, which provider is active,
model names, log file location, and the trusted-scripts allowlist.

Two providers are supported: Gemini and NVIDIA NIM (any OpenAI-compatible
model hosted on build.nvidia.com). ACTIVE_PROVIDER below is the ONE
setting that decides which one Jarvis actually talks to -- main.py and
gui.py both go through providers.get_llm_client(), so this is the only
line you need to edit to switch, no code changes needed elsewhere.

Neither key is required to be set just to import this file -- each
client checks its own key only when it's actually instantiated (see
gemini_client.py / nvidia_client.py), so having only one key set (or
testing with both, switching ACTIVE_PROVIDER back and forth) works fine.

On Windows, set keys once, PERMANENTLY -- so double-click launches see
them too, not just terminal sessions where you set $env:... by hand:
    setx GEMINI_API_KEY "your-key-here"
    setx NVIDIA_API_KEY "your-key-here"
Then close and reopen any terminal (and any running Jarvis) once for it
to take effect.
"""

import os

# "gemini" or "nvidia" -- switch providers by changing this one line.
ACTIVE_PROVIDER = "auto"  # "gemini" / "nvidia" force one, "auto" = Gemini-first with NVIDIA fallback

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")

# Google Custom Search JSON API, used by the web_search tool. Needs TWO
# separate things from https://programmablesearchengine.google.com/ and
# https://console.cloud.google.com/ -- a search engine ID (cx) AND an API
# key with the Custom Search API enabled. Neither is required just to
# import this file -- web_search checks for both only when it's called
# (same pattern as the LLM keys above).
GOOGLE_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY")
GOOGLE_SEARCH_ENGINE_ID = os.environ.get("GOOGLE_SEARCH_ENGINE_ID")

# Cap on how much text fetch_url returns, so one huge page doesn't blow
# past the model's context or dump an unusable wall of text into the chat.
MAX_FETCH_CHARS = 6000

# Max number of tool-call round-trips allowed per single user turn.
# Prevents an infinite loop if the LLM keeps requesting a failing tool.
MAX_TOOL_ITERATIONS = 100

# How long (seconds) to skip Gemini and stick with NVIDIA after a failed
# Gemini call, before trying Gemini again. Only used when ACTIVE_PROVIDER = "auto".
GEMINI_COOLDOWN_SECONDS = 300

# Which Gemini model to use for function calling. Model names churn fast —
# gemini-2.0-flash was retired March 2026, and gemini-2.5-flash is now
# restricted for new project signups even though it still shows up in
# list_models.py. gemini-3.5-flash is the current GA (non-preview) model.
# If this breaks again, run list_models.py and pick a Flash-family model
# from that list rather than trusting an old value here.
GEMINI_MODEL = "gemini-3.5-flash"

# Which NVIDIA NIM model to use (build.nvidia.com). Must support
# OpenAI-format function calling -- not every model in the catalog does.
# nemotron-3-nano-30b-a3b is confirmed to (NVIDIA's own model page lists
# "tool calling" explicitly) and is small/fast, which matters for an
# interactive agent. If you want more capability at the cost of latency,
# "nvidia/nemotron-3-ultra-550b-a55b" and "meta/llama-3_1-70b-instruct"
# are both confirmed tool-calling models too -- check a model's page on
# build.nvidia.com for ACTIVE_PROVIDER = "nvidia" "Tool calling" tag before swapping this.
NVIDIA_MODEL = "nvidia/nemotron-3-nano-30b-a3b"

# Where the action log gets written (JSON lines format).
LOG_FILE = "jarvis_log.jsonl"

# Where long-term memory (facts/preferences that persist ACROSS
# conversations and restarts, not just within one) is stored. Deliberately
# separate from LOG_FILE above -- that's a record of every tool call ever
# made, this is a small, curated list of things Jarvis should just know.
MEMORY_FILE = "jarvis_memory.json"

# Cap on how much extracted text analyze_file returns from a single PDF or
# Excel file, so one huge document doesn't blow past the model's context.
# Same pattern as MAX_FETCH_CHARS above, just a separate knob since document
# analysis is a different use case than web-page fetching.
MAX_ANALYZE_CHARS = 12000

# Absolute paths of scripts you've reviewed and trust to run WITHOUT a
# confirmation prompt each time. Jarvis cannot tell what a script does
# internally (including whether it permanently deletes files) -- this is
# an explicit opt-in list you maintain by hand, not something inferred.
# Anything not listed here requires confirmation before running, every
# time, even if you've run it before.
TRUSTED_SCRIPTS: set[str] = set()

# --- Remote web server settings (server.py) ---------------------------

# Bind to localhost only. The server is reached from the phone/GUI via
# Tailscale + `tailscale serve`, which forwards from the Tailscale
# interface to this local port -- the server itself never needs to (and
# should not) listen on 0.0.0.0 or any public interface directly.
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8756

# Where the single ongoing conversation is persisted (list of messages),
# so it survives a server restart. Separate from MEMORY_FILE above --
# this is the actual chat history, not curated long-term facts.
CONVERSATION_FILE = "jarvis_conversation.json"

# How long (seconds) an unanswered confirmation prompt waits before it's
# treated as cancelled. Prevents a stale "delete this file?" from firing
# hours later if you got distracted and never answered it.
CONFIRMATION_TIMEOUT_SECONDS = 300

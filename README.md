# Jarvis — Personal Local Agent (v1, Step 1)

## What this is
The hand-rolled agent loop skeleton, proven end-to-end with one dummy tool
(`list_files`). Per the project spec's build order, this is step 1 of:

  1. Agent loop skeleton + dummy tool  <-- YOU ARE HERE
  2. Add real tools one at a time (move/rename/read files, run script)
  3. Add the confirmation gate for destructive actions
  4. Test against the MVP bar

## Setup
1. Get a free Gemini API key at https://ai.google.dev (Google AI Studio).
2. In PowerShell, from D:\Projects\Jarvis:
       $env:GEMINI_API_KEY = "your-key-here"
       pip install -r requirements.txt
3. Run it:
       python main.py

## File map
- `main.py`          — the agent loop itself (read this first)
- `llm_client.py`     — provider-agnostic interface (the swap point)
- `gemini_client.py`  — Gemini-specific implementation (uses google-genai,
                         the current SDK — the old google-generativeai
                         package is fully deprecated)
- `tools.py`          — tool schemas + functions (currently: list_files only)
- `logger.py`         — writes jarvis_log.jsonl
- `config.py`         — settings + API key loading

## Try it
    You: what's in my current folder?
    Jarvis: [calls list_files, reports back]

## Notes on the model
`config.py` uses `gemini-2.5-flash`. Gemini model names get retired
periodically (gemini-2.0-flash was retired March 2026) — if you hit a
"quota limit: 0" error, that usually means the model name is stale, not
that you're actually out of quota. Check the live rate-limit panel in
Google AI Studio for your project's current free-tier model names.

## Not yet built (next steps)
- File move/rename/read tools
- Script-runner tool
- Confirmation gate before destructive actions
- Re-check that a path/file still exists at execution time, not just at
  confirmation time (per the spec's edge cases)
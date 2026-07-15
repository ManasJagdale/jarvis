# Project Spec: Personal Local Agent ("Jarvis")

## Problem Statement
Manas wants a personal AI assistant that runs on his own computer and can take real
actions — organizing files, running his existing scripts, and eventually handling
calendar tasks — by simply telling it what to do in a chat window. Rather than
configuring an existing agent framework (Open Interpreter, OpenClaw), the goal is to
**hand-build the agent loop from scratch** in Python, to deepen genuine coding fluency
(this is explicitly tied to closing his "vibe-coding vs. real coding" gap for
finance/data roles) rather than relying on a framework as a black box.

## Success Criteria
A working local Python program that:
- Runs as a persistent chat loop in the terminal (text in, text out)
- Can call an LLM API, receive a tool-call request, execute the corresponding local
  Python function, and feed the result back to the LLM for a final answer
- Has at least 3 working tools on day one: file listing/organizing, running a
  specified local script, and reading file contents
- Asks for explicit confirmation before any destructive or irreversible action
  (delete, overwrite, run a script that modifies real data)
- Manas can read and explain every line of the orchestration loop himself —
  no unexplained framework magic

## Scope

**In scope (v1):**
- Hand-rolled agent loop (LLM call → parse tool request → execute → feed back → repeat)
- Text/chat interface only (terminal-based to start)
- Core tools: file operations (list/move/rename/read), running local scripts,
  basic web search (optional, stretch)
- Confirm-before-destructive safety gate on file deletion, overwrite, and any
  script execution that touches real project data (GST tool, trading journal, etc.)
- Simple conversation memory within a session (not persistent across restarts yet)

**Out of scope (v1):**
- Voice input/output (explicitly deferred)
- GUI/screen control (clicking, driving other apps) — flagged as a v2+ stretch,
  known to be the least reliable part of comparable tools
- Calendar integration — deferred to v2, will use Google Calendar API
- Persistent long-term memory across sessions — deferred to v2
- Multi-agent orchestration (e.g. sub-agents per domain) — not needed yet

**MVP (minimum useful version):**
A terminal chat loop with 2–3 working tools (file ops + script runner) and a working
confirmation gate. Everything else builds on top of this once it's solid.

## Users and Inputs
- Single user (Manas), technical, comfortable in Python
- Input: natural language typed into a terminal chat prompt
- Edge cases to handle: ambiguous requests ("clean up my downloads" — needs to ask
  what "clean up" means before acting), requests referencing files/paths that don't
  exist, requests for actions outside the tool's defined scope (should decline
  gracefully, not attempt something unsupported)

## Outputs and Behaviour
- Output: text responses in the terminal, plus actual file-system/script side effects
- Before any destructive action: explicit confirmation prompt describing exactly
  what will happen, requiring a yes/no before proceeding
- Errors (failed script run, missing file, permission issue): surfaced clearly to
  the user in plain language, not a raw stack trace
- Logging: simple local log file recording what actions were taken and when
  (useful both for debugging and for Manas reviewing what the agent has done)

## Tech Stack
- **Language:** Python
- **LLM Provider:** Google Gemini API (free tier, sufficient function-calling
  support) for initial build and learning phase. Code should be structured so the
  API client is a swappable module — switching to Claude API later (small paid
  usage) should require changing one file, not rewriting the agent loop.
- **Platform:** Manas's local machine (OS to be confirmed — Windows or Mac)
- **Key dependencies:** Gemini Python SDK (or `requests` for raw API calls),
  standard library (`os`, `shutil`, `pathlib`, `subprocess`) for tool execution —
  deliberately avoiding heavyweight agent frameworks (LangChain, etc.) per the
  decision to hand-roll the orchestration loop
- **Integrations:** none in v1; Google Calendar API planned for v2

## Constraints
- Must remain fully free to run in v1 (Gemini free tier, no paid API spend required)
- Confirm-before-destructive is a hard requirement, not a nice-to-have
- Manas must understand every part of the orchestration loop — no unexplained
  framework abstractions in the core loop
- No GUI/screen control in v1 — deliberately scoped out due to reliability issues
  seen in comparable tools

## Edge Cases and Failure Modes
- LLM returns a malformed or unparseable tool-call request → catch and ask the
  LLM to retry rather than crashing
- Tool execution fails (e.g. script errors out) → capture the error, report it
  back to the LLM and the user, don't silently continue
- User confirms an action but then the underlying file/path has changed or is
  gone by execution time → re-check before executing, not just at confirmation time
- Infinite tool-call loops (LLM keeps requesting the same failing tool) → need a
  max-iteration cap per user turn

## Open Questions (resolve at the start of the new Project)
1. **OS confirmed?** (Windows vs Mac — affects file path handling and any
   OS-specific tooling)
2. **Which specific scripts/folders should the "run script" and "file ops" tools
   be scoped to initially?** (e.g. a single designated project folder to start,
   rather than full filesystem access, as an extra safety boundary)
3. **Session memory**: even though persistent memory is v2, should v1 at least
   remember context within a single running session (yes/no — likely yes, trivial
   to add)
4. **Logging format/location**: plain text file, JSON lines, or something else?
5. **Gemini API key setup**: confirm Manas has generated a key at
   ai.google.dev / Google AI Studio before the build session starts

---
*Once this spec is confirmed in the new Claude Project, work should proceed in order:
agent loop skeleton → single dummy tool (e.g. "list files") to prove the loop works
end-to-end → add real tools one at a time → add the confirmation gate → test against
the MVP definition above before adding anything from the "out of scope" list.*

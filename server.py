"""
server.py

The remote-access server. Runs on the laptop (bound to localhost only --
see SERVER_HOST/SERVER_PORT in config.py), reached from anywhere via
Tailscale + `tailscale serve` (see SETUP_REMOTE.md for that setup).

This is now the ONE place that holds conversation state and calls
jarvis_core.run_turn(). Both the phone web page (static/index.html) and
gui.py talk to this server over the same WebSocket protocol -- neither
calls jarvis_core directly anymore, so there's never a race between two
processes writing the same conversation/memory files at once.

Only one turn can run at a time (matches "one continuous conversation").
If a turn is already in progress and another message arrives, it's
rejected with an error rather than silently queued or run concurrently.

WebSocket protocol (JSON messages), all with a "type" field:

  Client -> Server:
    {"type": "message", "text": "..."}          start a new turn
    {"type": "confirm", "id": "...", "approved": true|false}
    {"type": "stop"}                            abort the current turn

  Server -> Client:
    {"type": "history", "messages": [...]}      sent once, right after connect
    {"type": "tool_start", "name": "...", "args": {...}}
    {"type": "tool_end", "name": "...", "result": "..."}
    {"type": "confirm_request", "id": "...", "description": "..."}
    {"type": "answer", "text": "..."}           final text for this turn
    {"type": "stopped"}
    {"type": "busy"}                             a turn is already running
    {"type": "error", "message": "..."}
"""

import asyncio
import json
import os
import threading
import time
import uuid
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
from config import CONFIRMATION_TIMEOUT_SECONDS, CONVERSATION_FILE
from confirm import confirm_action  # still used by main.py; unused here but kept for parity
from jarvis_core import run_turn
from providers import get_llm_client

app = FastAPI()


# --- Conversation persistence -------------------------------------------

def _load_conversation() -> list[dict]:
    if not os.path.exists(CONVERSATION_FILE):
        return []
    try:
        with open(CONVERSATION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_conversation(messages: list[dict]) -> None:
    # "raw" holds a provider-specific SDK object (e.g. Gemini's Content,
    # carrying a thought_signature) that isn't JSON-serializable. It only
    # needs to survive within a single running process anyway (it's
    # replayed on the very next turn, not read back after a restart), so
    # we persist everything except that field. After a server restart,
    # older turns lose that internal replay object -- Gemini still works
    # fine, it just isn't handed its own prior "thinking" verbatim for
    # turns from before the restart.
    serializable = [{k: v for k, v in msg.items() if k != "raw"} for msg in messages]
    with open(CONVERSATION_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)


# --- Single shared session (one conversation, one turn at a time) -------

class JarvisSession:
    def __init__(self):
        self.messages: list[dict] = _load_conversation()
        self.llm = get_llm_client()
        self.lock = threading.Lock()  # guards "is a turn currently running"
        self.turn_running = False
        self.stop_event: Optional[threading.Event] = None
        self.pending_confirmations: dict[str, dict] = {}  # id -> {"event": Event, "result": bool}

    def start_turn(self, text: str, loop: asyncio.AbstractEventLoop, ws: WebSocket) -> bool:
        """Kick off a turn in a background thread. Returns False if one's already running."""
        with self.lock:
            if self.turn_running:
                return False
            self.turn_running = True
            self.stop_event = threading.Event()

        self.messages.append({"role": "user", "content": text})

        def send(event: dict):
            asyncio.run_coroutine_threadsafe(ws.send_json(event), loop)

        def on_tool_start(name: str, args: dict):
            send({"type": "tool_start", "name": name, "args": args})

        def on_tool_end(name: str, result: str):
            send({"type": "tool_end", "name": name, "result": result})

        def on_assistant_text(text: str):
            send({"type": "assistant_text", "text": text})

        # Mirror the terminal's fallback/error diagnostics (e.g. "Gemini
        # call failed... falling back to NVIDIA") to the browser too, if
        # the active LLM client is FallbackLLMClient (the only one with
        # this hook). Gemini/NVIDIA-only modes have nothing to mirror.
        if hasattr(self.llm, "on_diagnostic"):
            self.llm.on_diagnostic = lambda msg: send({"type": "diagnostic", "message": msg})

        def confirm_fn(description: str) -> bool:
            confirm_id = str(uuid.uuid4())
            event = threading.Event()
            self.pending_confirmations[confirm_id] = {"event": event, "result": False}
            send({"type": "confirm_request", "id": confirm_id, "description": description})

            answered = event.wait(timeout=CONFIRMATION_TIMEOUT_SECONDS)
            result = self.pending_confirmations.pop(confirm_id, {}).get("result", False)
            if not answered:
                return False  # timed out -- treat as cancelled
            return result

        def worker():
            try:
                answer = run_turn(
                    self.llm,
                    self.messages,
                    confirm_fn,
                    on_tool_start,
                    on_tool_end,
                    stop_event=self.stop_event,
                    on_assistant_text=on_assistant_text,
                )
                _save_conversation(self.messages)
                if self.stop_event.is_set():
                    send({"type": "stopped"})
                else:
                    send({"type": "answer", "text": answer})
            except Exception as exc:  # noqa: BLE001 - never let the thread die silently
                send({"type": "error", "message": f"Something went wrong: {exc}"})
            finally:
                with self.lock:
                    self.turn_running = False
                    self.stop_event = None

        threading.Thread(target=worker, daemon=True).start()
        return True

    def request_stop(self):
        with self.lock:
            if self.stop_event is not None:
                self.stop_event.set()

    def answer_confirmation(self, confirm_id: str, approved: bool):
        pending = self.pending_confirmations.get(confirm_id)
        if pending is None:
            return  # already timed out, or an id from a stale/other session
        pending["result"] = approved
        pending["event"].set()


session = JarvisSession()


# --- HTTP: login ----------------------------------------------------------

class LoginRequest(BaseModel):
    password: str
    device_label: str = ""


@app.post("/login")
def login(req: LoginRequest):
    if not auth.verify_password(req.password):
        return JSONResponse(status_code=401, content={"error": "Incorrect password."})
    token = auth.create_session(req.device_label)
    return {"token": token}


class ClearRequest(BaseModel):
    token: str


@app.post("/clear")
def clear_conversation(req: ClearRequest):
    if not auth.is_valid_session(req.token):
        return JSONResponse(status_code=401, content={"error": "Not logged in."})
    with session.lock:
        if session.turn_running:
            return JSONResponse(
                status_code=409, content={"error": "Can't clear while a turn is running."}
            )
        session.messages = []
        _save_conversation(session.messages)
    return {"ok": True}


# --- WebSocket: chat --------------------------------------------------------

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str = ""):
    if not auth.is_valid_session(token):
        await websocket.close(code=4401)  # custom close code: unauthorized
        return

    await websocket.accept()
    loop = asyncio.get_event_loop()

    # Restore history immediately so a reconnecting client (or a fresh
    # login on a new device) sees where the conversation stands.
    await websocket.send_json({"type": "history", "messages": session.messages})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Malformed message."})
                continue

            msg_type = data.get("type")

            if msg_type == "message":
                text = (data.get("text") or "").strip()
                if not text:
                    continue
                started = session.start_turn(text, loop, websocket)
                if not started:
                    await websocket.send_json({"type": "busy"})

            elif msg_type == "confirm":
                session.answer_confirmation(data.get("id", ""), bool(data.get("approved")))

            elif msg_type == "stop":
                session.request_stop()

            else:
                await websocket.send_json({"type": "error", "message": f"Unknown message type '{msg_type}'."})

    except WebSocketDisconnect:
        # Per the spec: a dropped connection does NOT cancel an in-progress
        # turn. The background thread keeps running and will just have
        # nobody listening for its send() calls until a client reconnects
        # (asyncio.run_coroutine_threadsafe on a closed socket is a no-op
        # failure we don't need to handle specially -- the next reconnect
        # gets the full history, including this turn's eventual result,
        # since _save_conversation() runs regardless of whether anyone's
        # still connected to hear about it).
        pass


# --- Static files: the mobile web page --------------------------------------

app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    from config import SERVER_HOST, SERVER_PORT

    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)

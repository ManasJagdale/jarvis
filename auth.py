"""
auth.py

Single-user password auth for the remote server. There's exactly one
password (bcrypt-hashed, set once via hash_password.py and stored in the
JARVIS_PASSWORD_HASH environment variable -- never in a file, never in
plaintext).

Sessions are persistent tokens (not short-lived JWTs with expiry) --
per the spec, once you log in on a device, that device stays logged in
until you explicitly log out. Each device that logs in gets its own
token, so your phone and any other browser can each have an independent,
persistent session. Tokens are stored in a small JSON file so they
survive a server restart too (you shouldn't get logged out just because
the laptop rebooted).

This is deliberately NOT a general-purpose auth system: no usernames, no
lockouts, no expiry. The real security boundary is Tailscale -- only
devices already on your private tailnet can even reach this server, so
this password is a second, cheap layer on top, not the primary defense.
"""

import json
import os
import secrets
from datetime import datetime, timezone

import bcrypt

SESSIONS_FILE = "jarvis_sessions.json"


def _load_sessions() -> dict:
    if not os.path.exists(SESSIONS_FILE):
        return {}
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_sessions(sessions: dict) -> None:
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=2)


def verify_password(password: str) -> bool:
    """Check a plaintext password attempt against the stored bcrypt hash."""
    password_hash = os.environ.get("JARVIS_PASSWORD_HASH")
    if not password_hash:
        raise RuntimeError(
            "JARVIS_PASSWORD_HASH environment variable is not set. Run "
            "hash_password.py once to generate it, then:\n"
            '  setx JARVIS_PASSWORD_HASH "<the hash it prints>"\n'
            "and reopen your terminal."
        )
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash in the env var -- treat as a config error, not a
        # successful login, but don't leak details to the caller.
        return False


def create_session(device_label: str = "") -> str:
    """Issue a new persistent session token for a device that just logged in."""
    token = secrets.token_urlsafe(32)
    sessions = _load_sessions()
    sessions[token] = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "device_label": device_label or "unknown device",
    }
    _save_sessions(sessions)
    return token


def is_valid_session(token: str) -> bool:
    if not token:
        return False
    return token in _load_sessions()


def revoke_session(token: str) -> bool:
    """Log a specific device out. Returns False if the token wasn't found."""
    sessions = _load_sessions()
    if token not in sessions:
        return False
    del sessions[token]
    _save_sessions(sessions)
    return True


def list_sessions() -> dict:
    """For a future 'manage logged-in devices' view -- not wired to a route yet."""
    return _load_sessions()

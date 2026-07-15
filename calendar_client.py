"""
calendar_client.py

Thin wrapper around the Google Calendar API, used by the create_reminder
tool in tools.py. This is NOT an LLMClient (see llm_client.py) -- it's a
tool backend, same category as gemini_client.py's job is to talk to
Gemini, this file's job is to talk to Calendar. Nothing here is
LLM-specific.

------------------------------------------------------------------------
ONE-TIME SETUP (you do this once by hand, not per-run):

  1. Go to https://console.cloud.google.com/, create a project (or reuse
     an existing one), then enable the "Google Calendar API" for it
     (APIs & Services -> Library -> search "Google Calendar API" -> Enable).

  2. APIs & Services -> Credentials -> Create Credentials -> OAuth client
     ID. Application type: "Desktop app". Name it whatever you like.

  3. Download the resulting JSON and save it as credentials.json directly
     in the Jarvis project root (same folder as config.py). This file is
     your app's identity with Google -- keep it out of git/version control.

  4. The FIRST time create_reminder actually runs, a browser window will
     pop open asking you to log in and approve calendar access. After you
     approve, token.json is written next to credentials.json and reused
     silently on every future call -- you won't see that browser prompt
     again unless token.json is deleted or the grant gets revoked on
     Google's end.

Install with:
    pip install google-auth-oauthlib google-api-python-client tzlocal
------------------------------------------------------------------------
"""

import datetime
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from tzlocal import get_localzone_name

# calendar.events (not the broader "calendar" scope) -- Jarvis can create/
# edit/delete events but can't read your full calendar settings or other
# calendars you don't own. Narrowest scope that covers "set a reminder".
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(_PROJECT_ROOT, "credentials.json")
TOKEN_FILE = os.path.join(_PROJECT_ROOT, "token.json")

# Cached across calls within one Jarvis process so we don't re-check/
# re-refresh the token on every single reminder in a session.
_service = None


def _get_service():
    global _service
    if _service is not None:
        return _service

    if not os.path.exists(CREDENTIALS_FILE):
        raise RuntimeError(
            "credentials.json not found in the project root. See the "
            "setup steps in calendar_client.py's docstring -- you need to "
            "create an OAuth client ID in Google Cloud Console and save "
            "it there first."
        )

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # First-ever run (or a revoked/deleted token): opens a browser
            # for one-time manual consent.
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    _service = build("calendar", "v3", credentials=creds)
    return _service


def create_event(
    summary: str,
    start_iso: str,
    duration_minutes: int = 30,
    description: str = "",
) -> str:
    """
    Create a Google Calendar event on the user's primary calendar.

    start_iso is a local (no-offset) ISO 8601 datetime string, e.g.
    "2026-07-14T09:00:00" -- tools.py's create_reminder is responsible
    for having the LLM resolve any relative time ("in 20 minutes",
    "tomorrow at 9am") into this format before calling here.
    """
    service = _get_service()

    start_dt = datetime.datetime.fromisoformat(start_iso)
    end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)

    # Calendar's API wants a real IANA zone name (e.g. "Asia/Kolkata"),
    # not an OS-specific display name like Windows gives you -- tzlocal
    # is what correctly bridges that gap cross-platform.
    tz_name = get_localzone_name()

    event_body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": tz_name},
        "reminders": {"useDefault": True},
    }

    created = service.events().insert(calendarId="primary", body=event_body).execute()
    when = start_dt.strftime("%a %Y-%m-%d %H:%M")
    return f"Created calendar event '{summary}' at {when} -- {created.get('htmlLink')}"

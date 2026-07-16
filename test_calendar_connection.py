"""
Standalone Google Calendar API connectivity test.

Purpose: isolate whether the WinError 10060 you're seeing in Jarvis is:
  (a) a network-layer issue (firewall/VPN/campus wifi blocking outbound HTTPS)
  (b) an OAuth token issue (stale/invalid token.json)
  (c) an actual Google API error (quota, auth scope, etc.)

This script does NOT go through Jarvis's create_reminder / calendar_client.py.
It talks to Google directly using the same credentials.json / token.json,
so we can see exactly which layer is failing.

Usage:
    Place this file in the same folder as credentials.json and token.json
    (or update the paths below), then run:

        python test_calendar_connection.py

Run it in a few different network conditions to narrow things down, e.g.:
  1. On BITS wifi
  2. On mobile hotspot / different network
  3. With any VPN off
  4. With antivirus/firewall temporarily paused (if you control the machine)
"""

import socket
import sys
import time
import traceback

CREDENTIALS_PATH = "credentials.json"
TOKEN_PATH = "token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def step(msg):
    print(f"\n{'=' * 60}\n{msg}\n{'=' * 60}")


def test_raw_tcp_connectivity():
    """Step 1: Can we even open a raw TCP socket to Google at all?
    This isolates pure network/firewall blocking from anything API-related.
    If this hangs or fails, it's almost certainly your network/firewall/VPN,
    not Jarvis, not quota, not OAuth."""
    step("STEP 1: Raw TCP connection test to www.googleapis.com:443")
    host = "www.googleapis.com"
    port = 443
    try:
        start = time.time()
        sock = socket.create_connection((host, port), timeout=10)
        elapsed = time.time() - start
        sock.close()
        print(f"SUCCESS: Connected to {host}:{port} in {elapsed:.2f}s")
        return True
    except socket.timeout:
        print(f"FAILED: Timed out connecting to {host}:{port}")
        print("-> This matches WinError 10060 exactly. This is a network-layer")
        print("   block (firewall / VPN / campus wifi / ISP), not a Jarvis or")
        print("   Google quota issue.")
        return False
    except OSError as e:
        print(f"FAILED: {e}")
        print("-> Network/DNS/firewall issue reaching Google's servers.")
        return False


def test_dns_resolution():
    """Step 2: Can we resolve Google's hostname at all?"""
    step("STEP 2: DNS resolution test")
    host = "www.googleapis.com"
    try:
        ip = socket.gethostbyname(host)
        print(f"SUCCESS: {host} resolved to {ip}")
        return True
    except socket.gaierror as e:
        print(f"FAILED: Could not resolve {host}: {e}")
        print("-> DNS issue. Check your network's DNS settings.")
        return False


def test_calendar_api_call():
    """Step 3: Make an actual authenticated Calendar API call.
    This will surface the REAL error type from Google if we get that far:
      - 401/403 = auth/scope problem
      - 403 quotaExceeded / 429 = actual quota/rate limit issue
      - WinError 10060 again = network layer, confirmed reproducible
      - Success = the original issue may have been transient
    """
    step("STEP 3: Authenticated Google Calendar API call (list next 1 event)")
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        print("SKIPPED: Missing packages. Install with:")
        print("  pip install google-auth google-auth-oauthlib google-api-python-client")
        return None

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    except FileNotFoundError:
        print(f"SKIPPED: {TOKEN_PATH} not found in current directory.")
        print("Copy this script into your Jarvis project root, or update TOKEN_PATH.")
        return None
    except Exception as e:
        print(f"FAILED to load token: {e}")
        return None

    try:
        if creds.expired and creds.refresh_token:
            print("Token expired, attempting refresh...")
            creds.refresh(Request())
            print("Token refreshed successfully.")
    except Exception as e:
        print(f"FAILED during token refresh: {e}")
        print("-> This could be the actual point of failure if the refresh")
        print("   request itself times out (same WinError 10060 pattern).")
        traceback.print_exc()
        return False

    try:
        service = build("calendar", "v3", credentials=creds)
        start = time.time()
        events_result = service.events().list(
            calendarId="primary",
            maxResults=1,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        elapsed = time.time() - start
        events = events_result.get("items", [])
        print(f"SUCCESS in {elapsed:.2f}s. Retrieved {len(events)} event(s).")
        print("-> Calendar API is reachable and working RIGHT NOW.")
        print("   If Jarvis still fails, the issue is likely intermittent")
        print("   network flakiness rather than a persistent block or quota cap.")
        return True
    except HttpError as e:
        status = e.resp.status if hasattr(e, "resp") else "unknown"
        print(f"FAILED: Google API returned HTTP {status}")
        print(f"Details: {e}")
        if status == 403:
            print("-> Check the error body above for 'quotaExceeded',")
            print("   'rateLimitExceeded', or 'insufficientPermissions'.")
            print("   This IS a genuine API-level rejection, not a network issue.")
        elif status == 401:
            print("-> Auth problem: token invalid/expired/wrong scopes.")
            print("   Try deleting token.json and re-running the OAuth flow.")
        return False
    except OSError as e:
        print(f"FAILED with OSError: {e}")
        if "10060" in str(e):
            print("-> Reproduced WinError 10060 with a direct, minimal API call.")
            print("   This confirms it's NOT specific to Jarvis's code —")
            print("   it's your network/firewall/VPN blocking or dropping the")
            print("   connection to Google's servers.")
        return False
    except Exception as e:
        print(f"FAILED with unexpected error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


def main():
    print("Google Calendar API Connectivity Diagnostic")
    print(f"Python: {sys.version}")

    dns_ok = test_dns_resolution()
    tcp_ok = test_raw_tcp_connectivity()
    api_result = test_calendar_api_call()

    step("SUMMARY")
    print(f"DNS resolution:        {'OK' if dns_ok else 'FAILED'}")
    print(f"Raw TCP to Google:     {'OK' if tcp_ok else 'FAILED'}")
    if api_result is True:
        print("Calendar API call:     OK")
        print("\nConclusion: Google Calendar API is reachable and NOT quota-blocked.")
        print("The original WinError 10060 in Jarvis was likely a transient")
        print("network hiccup. Consider adding a retry-with-backoff wrapper")
        print("around create_reminder's API call.")
    elif api_result is False:
        print("Calendar API call:     FAILED (see details above)")
        if not tcp_ok:
            print("\nConclusion: Network/firewall is blocking the connection at the")
            print("TCP level, before any Google API logic even runs. This is a")
            print("network configuration issue (firewall/VPN/campus wifi), not")
            print("a quota problem and not a Jarvis code bug.")
        else:
            print("\nConclusion: TCP connectivity works, so check the specific error")
            print("above (401 = auth, 403 quotaExceeded = actual quota issue,")
            print("10060 during the API call itself = intermittent network flake).")
    else:
        print("Calendar API call:     SKIPPED (see message above)")


if __name__ == "__main__":
    main()
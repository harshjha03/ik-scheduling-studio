"""Mint a GOOGLE_OAUTH_JSON blob whose refresh token covers Calendar AND Sheets.

The refresh token currently in .env was consented for the calendar scope only, and Google refuses a
token request that adds a scope the user never consented (`invalid_scope`). Re-consenting once with both
scopes fixes Sheets pull/push and keeps calendar publish and freebusy working on the same identity.

    .venv/bin/python scripts/google_consent.py

Reuses the Desktop-app client_id/client_secret already in GOOGLE_OAUTH_JSON. Opens a browser; you sign
in as the publishing account and approve; the loopback redirect lands here. Prints the new blob to paste
into .env (and into the Vercel project env). Stdlib only; nothing is written anywhere.
"""
from __future__ import annotations

import base64
import http.server
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import dotenv  # noqa: E402

dotenv.load(os.path.join(ROOT, ".env.local"))
dotenv.load(os.path.join(ROOT, ".env"))

SCOPES = ["https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/spreadsheets"]
PORT = 8765


def main() -> int:
    raw = os.environ.get("GOOGLE_OAUTH_JSON", "")
    if not raw:
        print("GOOGLE_OAUTH_JSON is not set — need the Desktop client's client_id and client_secret")
        return 1
    cur = json.loads(raw if raw.strip().startswith("{") else base64.b64decode(raw).decode())
    client_id, client_secret = cur["client_id"], cur["client_secret"]
    redirect = f"http://127.0.0.1:{PORT}/"
    state = secrets.token_urlsafe(16)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "state": state})
    print("Opening the consent page. Sign in as the account that owns the calendar / spreadsheet.\n", url, "\n")
    webbrowser.open(url)

    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Consent received - you can close this tab.")
        def log_message(self, *a): pass

    with http.server.HTTPServer(("127.0.0.1", PORT), H) as srv:
        while "code" not in got and "error" not in got:
            srv.handle_request()
    if got.get("error") or got.get("state") != state:
        print("consent failed:", got); return 1

    body = urllib.parse.urlencode({"code": got["code"], "client_id": client_id, "client_secret": client_secret,
                                   "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
    tok = json.load(urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", body)))
    if "refresh_token" not in tok:
        print("no refresh_token in the response (was prompt=consent honoured?):", tok); return 1
    blob = {"client_id": client_id, "client_secret": client_secret, "refresh_token": tok["refresh_token"]}
    print("granted scopes:", tok.get("scope"))
    print("\nGOOGLE_OAUTH_JSON=" + base64.b64encode(json.dumps(blob).encode()).decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())

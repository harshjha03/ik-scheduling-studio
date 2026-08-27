"""Outbound channels for publishing an approved week: Google Calendar, e-mail, SMS.

Each channel is live only when its credentials are present; otherwise it reports `simulated` and
changes nothing — the same graceful-degradation contract the LLM layer uses, so the demo always
runs. Payload builders are pure and unit-tested; the HTTP call is injectable.

Contact data lives on the records themselves:
  * SME     -> `email`, `phone`      (absent in the synthetic dataset — senders say so plainly)
  * Batch   -> `calendar_id`         (the cohort calendar students subscribe to)
             -> `contact_email`      (a distribution list for the batch)
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
TIMEOUT = float(os.environ.get("CHANNEL_TIMEOUT", "20"))


class Result(dict):
    """status: sent | simulated | skipped | error."""

    def __init__(self, status: str, detail: str, count: int = 0, live: bool = False, **extra):
        super().__init__(status=status, detail=detail, count=count, live=live, **extra)


def merge(results: list[Result]) -> Result:
    """Collapse a fan-out (e.g. one calendar per cohort) back into one leaf result."""
    if len(results) == 1:
        return results[0]
    if not results:
        return Result("skipped", "Nothing to send.", 0)
    status = ("sent" if any(r["status"] == "sent" for r in results)
              else "error" if any(r["status"] == "error" for r in results)
              else "skipped" if any(r["status"] == "skipped" for r in results)
              else "simulated")
    return Result(status, "; ".join(r["detail"] for r in results),
                  sum(r["count"] for r in results), any(r["live"] for r in results))


#     RFC 2606 / 6761 reserved — an address here can never be delivered to.
UNDELIVERABLE = (".example", ".invalid", ".test", ".localhost")


def deliverable(email: str | None) -> bool:
    """Now that we can invite attendees, a whole-week publish would otherwise attach the seed
    roster's placeholder addresses to every event and fire off invitations that only bounce."""
    return bool(email) and "@" in email and not email.lower().endswith(UNDELIVERABLE)


def _redirect(kind: str, targets: list[str]) -> tuple[list[str], str]:
    """Staging guard. The seed dataset carries `.example` addresses and unroutable numbers, so a
    live send would bounce off a real provider. Point PUBLISH_REDIRECT_TO / PUBLISH_REDIRECT_SMS_TO
    at one inbox you own and every message goes there instead — one message, not one per fake."""
    to = os.environ.get("PUBLISH_REDIRECT_SMS_TO" if kind == "sms" else "PUBLISH_REDIRECT_TO")
    if not to or not targets:
        return targets, ""
    return [to], f" (redirected to {to}; {len(targets)} real recipient(s) suppressed)"


def _post(url: str, body: dict | str, headers: dict, method: str = "POST") -> dict:
    data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "ik-scheduler/1.0", **headers}, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read().decode() or "{}"
    return json.loads(raw) if raw.strip().startswith(("{", "[")) else {"raw": raw}


# ---------------------------------------------------------------- payload builders (pure)

def _span(row: dict) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(row["start_utc"].replace("Z", "+00:00"))
    if not start.tzinfo:
        start = start.replace(tzinfo=timezone.utc)
    return start, start + timedelta(minutes=int(row.get("duration_min", 60)))


def title_of(row: dict) -> str:
    topic = row.get("sub_specialty") or row.get("type", "class").title()
    return f"{row['batch_id']} · {topic}"


def event_body(row: dict, sme_email: str | None = None) -> dict:
    """A Google Calendar event for one class. Deterministic, so re-publishing is a clean update."""
    start, end = _span(row)
    teacher = row.get("sme_name") or "To be confirmed"
    lines = [
        f"Batch: {row['batch_id']}",
        f"Course: {row.get('subject', '')}",
        f"Topic: {row.get('sub_specialty') or row.get('type')}",
        f"Teacher: {teacher}",
    ]
    if row.get("flags"):
        lines.append("Flags: " + ", ".join(f["code"] for f in row["flags"]))
    body: dict = {
        "summary": title_of(row),
        "description": "\n".join(lines),
        "start": {"dateTime": start.astimezone(IST).isoformat(), "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": end.astimezone(IST).isoformat(), "timeZone": "Asia/Kolkata"},
        # our own id, so a second publish updates the same event instead of duplicating it
        "extendedProperties": {"private": {"ikSessionId": row["session_id"]}},
    }
    if sme_email:
        body["attendees"] = [{"email": sme_email, "responseStatus": "needsAction"}]
    return body


def digest_html(week_label: str, rows: list[dict], for_teacher: str | None = None) -> str:
    """One readable digest of the published week; scoped to a teacher when given."""
    mine = sorted([r for r in rows if not for_teacher or r.get("sme_id") == for_teacher], key=lambda r: r["start_utc"])
    items = []
    for r in mine:
        start, _ = _span(r)
        local = start.astimezone(IST).strftime("%a %d %b, %H:%M")
        who = "" if for_teacher else f" · {r.get('sme_name') or 'unfilled'}"
        items.append(f"<li><b>{local} IST</b> — {title_of(r)}{who}</li>")
    return (f"<p>Here is the schedule for <b>{week_label}</b>.</p>"
            f"<ul>{''.join(items)}</ul>"
            f"<p>{len(mine)} class(es). Reply to this mail if anything looks wrong.</p>")


def sms_text(week_label: str, rows: list[dict], for_teacher: str | None = None) -> str:
    mine = sorted([r for r in rows if not for_teacher or r.get("sme_id") == for_teacher], key=lambda r: r["start_utc"])
    if not mine:
        return f"{week_label}: nothing scheduled for you."
    first = mine[0]
    start, _ = _span(first)
    return (f"{week_label}: {len(mine)} class(es). Next: {title_of(first)} "
            f"on {start.astimezone(IST):%a %H:%M} IST.")


# ---------------------------------------------------------------- configuration

def _disabled() -> str | None:
    """A single kill switch that forces every channel to simulate, credentials or not. The browser
    flow suite publishes an entire week, so it must never run against a live calendar."""
    on = os.environ.get("PUBLISH_DISABLED", "").strip().lower()
    return "PUBLISH_DISABLED is set" if on and on not in ("0", "false", "no") else None


CAL_SCOPE = "https://www.googleapis.com/auth/calendar"


def publishes_as_user() -> bool:
    """Whether we act as a person rather than as a bot.

    It decides whether teachers can be invited at all: Google answers
    `403 forbiddenForServiceAccounts` when a bare service account adds an attendee, while a user
    account may, and an invitation lands on the attendee's own calendar with nothing to click.
    """
    return bool(os.environ.get("GOOGLE_OAUTH_JSON"))


def can_invite() -> bool:
    return publishes_as_user() or bool(os.environ.get("GOOGLE_IMPERSONATE"))


def google_ready() -> tuple[bool, str]:
    if off := _disabled():
        return False, off
    if not (os.environ.get("GOOGLE_OAUTH_JSON") or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")):
        return False, "GOOGLE_OAUTH_JSON or GOOGLE_SERVICE_ACCOUNT_JSON not set"
    if not os.environ.get("GOOGLE_CALENDAR_ID"):
        return False, "GOOGLE_CALENDAR_ID not set (the cohort calendar to write into)"
    who = "user account" if publishes_as_user() else "service account"
    return True, f"{who}, {'invites teachers' if can_invite() else 'writes events only'}"


def email_ready() -> tuple[bool, str]:
    if off := _disabled():
        return False, off
    if not os.environ.get("RESEND_API_KEY"):
        return False, "RESEND_API_KEY not set"
    if not os.environ.get("MAIL_FROM"):
        return False, "MAIL_FROM not set (a verified sender)"
    return True, "Resend"


def sms_ready() -> tuple[bool, str]:
    if off := _disabled():
        return False, off
    need = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")
    missing = [k for k in need if not os.environ.get(k)]
    return (not missing), ("Twilio" if not missing else f"{', '.join(missing)} not set")


def status() -> dict:
    g, gw = google_ready()
    e, ew = email_ready()
    s, sw = sms_ready()
    return {
        "cal": {"live": g, "detail": gw, "name": "Google Calendar"},
        "email": {"live": e, "detail": ew, "name": "e-mail"},
        "sms": {"live": s, "detail": sw, "name": "SMS"},
    }


# ---------------------------------------------------------------- senders

def _blob(name: str) -> dict:
    raw = os.environ[name]
    if not raw.strip().startswith("{"):                  # allow a base64 blob, easier in env vars
        raw = base64.b64decode(raw).decode()
    return json.loads(raw)


def _google_token() -> str:
    """Access token, as a person if GOOGLE_OAUTH_JSON is set, else as the service account.
    Requires `google-auth` (in requirements.txt)."""
    from google.auth.transport.requests import Request   # type: ignore

    if publishes_as_user():
        from google.oauth2.credentials import Credentials  # type: ignore
        d = _blob("GOOGLE_OAUTH_JSON")                   # {client_id, client_secret, refresh_token}
        creds = Credentials(None, refresh_token=d["refresh_token"], client_id=d["client_id"],
                            client_secret=d["client_secret"], scopes=[CAL_SCOPE],
                            token_uri=d.get("token_uri", "https://oauth2.googleapis.com/token"))
    else:
        from google.oauth2 import service_account        # type: ignore
        creds = service_account.Credentials.from_service_account_info(
            _blob("GOOGLE_SERVICE_ACCOUNT_JSON"), scopes=[CAL_SCOPE])
        if subject := os.environ.get("GOOGLE_IMPERSONATE"):   # domain-wide delegation, if configured
            creds = creds.with_subject(subject)
    creds.refresh(Request())
    return creds.token


def send_calendar(rows: list[dict], audience: str, store=None, api=None, calendar_id: str | None = None) -> Result:
    """Create or update one event per class on one calendar. `api(method, path, body)` is injectable.

    `calendar_id` overrides GOOGLE_CALENDAR_ID so students can be published onto their own cohort
    calendar; the caller fans out per calendar and `merge()`s the results.
    """
    ready, why = google_ready()
    if not ready:
        return Result("simulated", f"Not sent — {why}.", len(rows))
    cal = calendar_id or os.environ["GOOGLE_CALENDAR_ID"]
    if api is None:
        token = _google_token()
        base = "https://www.googleapis.com/calendar/v3/calendars"
        def api(method: str, path: str, body: dict | None):   # noqa: E306
            url = f"{base}/{urllib.parse.quote(cal)}/events{path}"
            return _post(url, body or {}, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method)
    # Only a user account or a domain-delegated service account may add attendees; a bare service
    # account gets 403 forbiddenForServiceAccounts, so the teacher is named in the description only.
    invite = audience == "sme" and can_invite()
    q = "?sendUpdates=all" if invite else "?sendUpdates=none"
    sent, failed = 0, []
    for row in rows:
        known = store.event_for(row["session_id"], cal) if store else None
        email = row.get("sme_email")
        body = event_body(row, email if invite and deliverable(email) else None)
        try:
            if known:
                try:
                    api("PUT", f"/{known}{q}", body)
                except Exception as exc:
                    if getattr(exc, "code", None) not in (404, 410):
                        raise
                    if store:                                   # deleted in Google — re-create it
                        store.forget_event(row["session_id"], cal)
                    known = None
            if not known:
                created = api("POST", q, body)
                if store and created.get("id"):
                    store.remember_event(row["session_id"], cal, created["id"])
            sent += 1
        except Exception as exc:                                # one bad row must not sink the batch
            failed.append(f"{row['session_id']}: {type(exc).__name__}")
    if failed and not sent:
        return Result("error", f"Calendar rejected every event ({failed[0]}).", 0, True)
    detail = f"{sent} event(s) written to {cal}" + (f"; {len(failed)} failed" if failed else "")
    return Result("sent" if sent else "skipped", detail, sent, True)


def send_email(rows: list[dict], audience: str, recipients: list[str], week_label: str, api=None) -> Result:
    ready, why = email_ready()
    if not ready:
        return Result("simulated", f"Not sent — {why}.", len(recipients))
    if not recipients:
        return Result("skipped", "No e-mail addresses on record for that audience.", 0, True)
    recipients, note = _redirect("email", recipients)
    poster = api or (lambda body: _post("https://api.resend.com/emails", body, {
        "Authorization": f"Bearer {os.environ['RESEND_API_KEY']}", "Content-Type": "application/json"}))
    sent, failed = 0, []
    for to in recipients:
        try:
            poster({"from": os.environ["MAIL_FROM"], "to": [to],
                    "subject": f"Your schedule — {week_label}", "html": digest_html(week_label, rows)})
            sent += 1
        except Exception as exc:
            failed.append(f"{to}: {type(exc).__name__}")
    if failed and not sent:
        return Result("error", f"Provider rejected every message ({failed[0]}).", 0, True)
    return Result("sent" if sent else "skipped",
                  f"{sent} digest(s) sent{note}" + (f"; {len(failed)} failed" if failed else ""), sent, True)


def send_sms(rows: list[dict], audience: str, numbers: list[str], week_label: str, api=None) -> Result:
    ready, why = sms_ready()
    if not ready:
        return Result("simulated", f"Not sent — {why}.", len(numbers))
    if not numbers:
        return Result("skipped", "No phone numbers on record for that audience.", 0, True)
    numbers, note = _redirect("sms", numbers)
    sid, token = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    poster = api or (lambda form: _post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", form,
        {"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"}))
    sent, failed = 0, []
    for to in numbers:
        try:
            poster(urllib.parse.urlencode({"From": os.environ["TWILIO_FROM"], "To": to,
                                           "Body": sms_text(week_label, rows)}))
            sent += 1
        except Exception as exc:
            failed.append(f"{to}: {type(exc).__name__}")
    if failed and not sent:
        return Result("error", f"Provider rejected every message ({failed[0]}).", 0, True)
    return Result("sent" if sent else "skipped",
                  f"{sent} message(s) sent{note}" + (f"; {len(failed)} failed" if failed else ""), sent, True)


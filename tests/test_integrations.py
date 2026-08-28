"""Storage and outbound channels. No credentials and no network: the HTTP call is injected."""
import os
import sys
import urllib.error

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import channels as C  # noqa: E402
from engine import sheets as SH  # noqa: E402
from engine.store import Store  # noqa: E402

ROW = {
    "session_id": "W37-DSA-01-0", "batch_id": "DSA-01", "subject": "DSA", "sub_specialty": "Arrays & Strings",
    "type": "class", "start_utc": "2026-09-07T04:30:00Z", "duration_min": 60,
    "sme_id": "T03", "sme_name": "Kavya Nair", "flags": [],
}


@pytest.fixture
def db(tmp_path):
    return Store(url=None, path=str(tmp_path / "t.db"))


@pytest.fixture
def live(monkeypatch):
    """Pretend every channel is configured, without ever leaving the process."""
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", '{"type":"service_account"}')
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "cohort@group.calendar.google.com")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("MAIL_FROM", "ops@ik.test")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM", "+10000000000")


# ---------------- storage ----------------

def test_schedule_round_trips(db):
    db.save_schedule("2026-W37", {"draft": [ROW], "published": True})
    got = db.load_schedule("2026-W37")
    assert got["draft"][0]["session_id"] == ROW["session_id"] and got["published"] is True
    assert got["updated_at"] and db.weeks() == ["2026-W37"]


def test_schedule_save_is_idempotent(db):
    db.save_schedule("2026-W37", {"draft": [ROW]})
    db.save_schedule("2026-W37", {"draft": []})
    assert db.load_schedule("2026-W37")["draft"] == [] and db.weeks() == ["2026-W37"]


def test_missing_week_is_none(db):
    assert db.load_schedule("2026-W99") is None


def test_publish_log_records_live_flag(db):
    db.record_publish("2026-W37", "cal", "sme", "sent", "3 events", True)
    db.record_publish("2026-W37", "sms", "stu", "simulated", "no key", False)
    log = db.publish_log("2026-W37")
    assert len(log) == 2 and log[0]["channel"] == "sms" and log[0]["live"] is False
    assert log[1]["live"] is True and log[1]["status"] == "sent"


def test_store_rebuilds_a_database_deleted_under_it(db):
    """A long-lived process must not be poisoned by someone clearing .data/ (or resetting the db)."""
    db.save_schedule("2026-W37", {"draft": [ROW]})
    os.remove(db.path)
    assert db.load_schedule("2026-W37") is None      # gone, but the schema is back
    db.save_schedule("2026-W37", {"draft": []})
    assert db.load_schedule("2026-W37")["draft"] == []


def test_sqlite_is_reported_as_not_durable(db):
    assert db.info() == {"driver": "sqlite", "location": db.path, "durable": False}


# ---------------- payload builders ----------------

def test_event_body_is_ist_and_carries_our_id():
    body = C.event_body(ROW, "kavya@ik-real.com")
    assert body["summary"] == "DSA-01 · Arrays & Strings"
    assert body["start"]["dateTime"] == "2026-09-07T10:00:00+05:30"   # 04:30Z in IST
    assert body["end"]["dateTime"] == "2026-09-07T11:00:00+05:30"
    assert body["start"]["timeZone"] == "Asia/Kolkata"
    # the id is what makes a re-publish an update rather than a duplicate
    assert body["extendedProperties"]["private"]["ikSessionId"] == ROW["session_id"]
    assert body["attendees"] == [{"email": "kavya@ik-real.com", "responseStatus": "needsAction"}]
    assert "Kavya Nair" in body["description"]


def test_event_body_without_an_address_invites_nobody():
    assert "attendees" not in C.event_body(ROW)


def test_digest_and_sms_scope_to_one_teacher():
    other = {**ROW, "session_id": "x", "sme_id": "T99", "sme_name": "Someone Else"}
    html = C.digest_html("Next week", [ROW, other], for_teacher="T03")
    assert "Arrays & Strings" in html and "Someone Else" not in html and "1 class(es)" in html
    assert C.sms_text("Next week", [ROW, other], for_teacher="T03").startswith("Next week: 1 class(es).")


def test_sms_handles_an_empty_week():
    assert "nothing scheduled" in C.sms_text("Next week", [], for_teacher="T03")


# ---------------- senders: not configured ----------------

def test_kill_switch_simulates_every_channel_even_when_configured(live, monkeypatch, db):
    monkeypatch.setenv("PUBLISH_DISABLED", "1")
    assert not any(c["live"] for c in C.status().values())
    boom = lambda *a, **k: pytest.fail("a disabled channel must not send")
    assert C.send_calendar([ROW], "sme", store=db, api=boom)["status"] == "simulated"
    assert C.send_email([ROW], "sme", ["a@b.c"], "Next week", api=boom)["status"] == "simulated"
    assert C.send_sms([ROW], "sme", ["+1"], "Next week", api=boom)["status"] == "simulated"
    monkeypatch.setenv("PUBLISH_DISABLED", "0")               # an explicit off is still off
    assert C.status()["cal"]["live"] is True


def test_channels_report_what_is_missing(monkeypatch):
    for k in ("GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_CALENDAR_ID", "RESEND_API_KEY", "MAIL_FROM",
              "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"):
        monkeypatch.delenv(k, raising=False)
    st = C.status()
    assert not any(c["live"] for c in st.values())
    assert "GOOGLE_SERVICE_ACCOUNT_JSON" in st["cal"]["detail"] and "RESEND_API_KEY" in st["email"]["detail"]
    # and nothing is sent
    assert C.send_calendar([ROW], "sme")["status"] == "simulated"
    assert C.send_email([ROW], "sme", ["a@b.c"], "Next week")["status"] == "simulated"
    assert C.send_sms([ROW], "sme", ["+1"], "Next week")["status"] == "simulated"


# ---------------- senders: configured, transport injected ----------------

CAL = "cohort@group.calendar.google.com"


def test_calendar_creates_then_updates_the_same_event(db, live):
    calls = []
    def api(method, path, body):
        calls.append((method, path, body["summary"]))
        return {"id": "evt-1"}
    first = C.send_calendar([ROW], "sme", store=db, api=api)
    assert first["status"] == "sent" and first["live"] is True and first["count"] == 1
    assert calls[0][0] == "POST" and calls[0][1] == "?sendUpdates=none"
    assert db.event_for(ROW["session_id"], CAL) == "evt-1"
    # publishing again must update the event we already own, never create a second one
    second = C.send_calendar([ROW], "sme", store=db, api=api)
    assert second["status"] == "sent"
    assert calls[1][0] == "PUT" and calls[1][1] == "/evt-1?sendUpdates=none"


def test_each_calendar_gets_its_own_event(db, live):
    """The same class published to ops and to a cohort calendar is two events, not one moved one."""
    calls = []
    def api(method, path, body):
        calls.append((method, path))
        return {"id": f"evt-{len(calls)}"}
    C.send_calendar([ROW], "sme", store=db, api=api)
    C.send_calendar([ROW], "stu", store=db, api=api, calendar_id="dsa-01@group.calendar.google.com")
    assert [m for m, _ in calls] == ["POST", "POST"]            # never a PUT over the first event
    assert db.event_for(ROW["session_id"], CAL) == "evt-1"
    assert db.event_for(ROW["session_id"], "dsa-01@group.calendar.google.com") == "evt-2"


def test_calendar_recreates_an_event_deleted_in_google(db, live):
    db.remember_event(ROW["session_id"], CAL, "gone")
    calls = []
    def api(method, path, body):
        calls.append(method)
        if method == "PUT":
            raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)
        return {"id": "evt-new"}
    res = C.send_calendar([ROW], "sme", store=db, api=api)
    assert res["status"] == "sent" and calls == ["PUT", "POST"]
    assert db.event_for(ROW["session_id"], CAL) == "evt-new"


def test_calendar_invites_the_teacher_only_when_allowed(db, live, monkeypatch):
    """A bare service account cannot add attendees — Google 403s. A delegated one, or a user
    account (GOOGLE_OAUTH_JSON), may."""
    row = {**ROW, "sme_email": "kavya@ik-real.com"}
    seen = []
    api = lambda method, path, body: (seen.append((path, body)), {"id": "e"})[1]
    invited = lambda i: seen[i][1].get("attendees") == [{"email": "kavya@ik-real.com", "responseStatus": "needsAction"}]

    C.send_calendar([row], "sme", store=db, api=api)
    assert not invited(0) and "sendUpdates=none" in seen[0][0]

    monkeypatch.setenv("GOOGLE_IMPERSONATE", "ops@ik.test")          # domain-wide delegation
    C.send_calendar([{**row, "session_id": "b"}], "sme", store=db, api=api)
    assert invited(1) and "sendUpdates=all" in seen[1][0]

    monkeypatch.delenv("GOOGLE_IMPERSONATE")
    monkeypatch.setenv("GOOGLE_OAUTH_JSON", '{"refresh_token":"x"}')  # publishing as a person
    C.send_calendar([{**row, "session_id": "c"}], "sme", store=db, api=api)
    assert invited(2) and "sendUpdates=all" in seen[2][0]
    assert C.publishes_as_user() and "user account" in C.status()["cal"]["detail"]

    # students are never invited — a cohort calendar is subscribed to, not attended
    C.send_calendar([{**row, "session_id": "d"}], "stu", store=db, api=api)
    assert not invited(3)


def test_placeholder_addresses_are_never_invited(db, live, monkeypatch):
    """The seed roster is `.example`; inviting it would fire off a bounce per class."""
    monkeypatch.setenv("GOOGLE_OAUTH_JSON", '{"refresh_token":"x"}')
    seen = []
    api = lambda method, path, body: (seen.append(body), {"id": "e"})[1]
    rows = [{**ROW, "session_id": "fake", "sme_email": "ananya.iyer@ik.example"},
            {**ROW, "session_id": "real", "sme_email": "someone@gmail.com"}]
    C.send_calendar(rows, "sme", store=db, api=api)
    assert "attendees" not in seen[0]
    assert seen[1]["attendees"] == [{"email": "someone@gmail.com", "responseStatus": "needsAction"}]
    assert not any(map(C.deliverable, ["a@b.example", "a@b.invalid", "a@b.test", "", None]))
    assert C.deliverable("a@gmail.com")


def test_user_credentials_alone_are_enough_to_be_live(monkeypatch):
    """GOOGLE_SERVICE_ACCOUNT_JSON is no longer required once we publish as a person."""
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("PUBLISH_DISABLED", raising=False)
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "cohort@group.calendar.google.com")
    monkeypatch.setenv("GOOGLE_OAUTH_JSON", '{"refresh_token":"x"}')
    ok, why = C.google_ready()
    assert ok and "user account" in why and "invites teachers" in why


def test_merge_collapses_a_per_calendar_fan_out():
    one = C.Result("sent", "1 event(s) written to a", 1, True)
    two = C.Result("skipped", "nothing", 0, True)
    merged = C.merge([one, two])
    assert merged["status"] == "sent" and merged["count"] == 1 and merged["live"] is True
    assert C.merge([two, two])["status"] == "skipped"
    assert C.merge([one])is one and C.merge([])["status"] == "skipped"


def test_calendar_one_bad_row_does_not_sink_the_batch(db, live):
    def api(method, path, body):
        if "DSA-02" in body["summary"]:
            raise RuntimeError("boom")
        return {"id": "evt-ok"}
    rows = [ROW, {**ROW, "session_id": "b", "batch_id": "DSA-02"}]
    res = C.send_calendar(rows, "sme", store=db, api=api)
    assert res["status"] == "sent" and res["count"] == 1 and "1 failed" in res["detail"]


def test_calendar_reports_error_when_everything_fails(db, live):
    def api(method, path, body):
        raise RuntimeError("nope")
    res = C.send_calendar([ROW], "sme", store=db, api=api)
    assert res["status"] == "error" and res["live"] is True and res["count"] == 0


def test_email_sends_one_digest_per_recipient(live):
    sent = []
    res = C.send_email([ROW], "sme", ["a@ik.test", "b@ik.test"], "Next week", api=lambda body: sent.append(body))
    assert res["status"] == "sent" and res["count"] == 2
    assert sent[0]["from"] == "ops@ik.test" and sent[0]["to"] == ["a@ik.test"]
    assert "Next week" in sent[0]["subject"] and "Arrays & Strings" in sent[0]["html"]


def test_email_says_so_when_nobody_has_an_address(live):
    res = C.send_email([ROW], "stu", [], "Next week", api=lambda body: None)
    assert res["status"] == "skipped" and "No e-mail addresses" in res["detail"] and res["live"] is True


def test_sms_posts_twilio_form_encoded(live):
    sent = []
    res = C.send_sms([ROW], "sme", ["+919000000000"], "Next week", api=lambda form: sent.append(form))
    assert res["status"] == "sent" and res["count"] == 1
    assert "To=%2B919000000000" in sent[0] and "From=%2B10000000000" in sent[0] and "Body=" in sent[0]


def test_sms_says_so_when_no_numbers(live):
    assert C.send_sms([ROW], "stu", [], "Next week", api=lambda f: None)["status"] == "skipped"


# ---------------- the real transport (only the socket is stubbed) ----------------

def test_live_calls_hit_the_right_endpoints(db, live, monkeypatch):
    """Covers what the injected `api` never does: URL building, auth headers, HTTP method."""
    calls = []
    monkeypatch.setattr(C, "_post", lambda url, body, headers, method="POST": (
        calls.append((method, url, headers.get("Authorization"), body)), {"id": "e1"})[1])
    monkeypatch.setattr(C, "_google_token", lambda: "ya29.test")

    C.send_calendar([ROW], "sme", store=db, calendar_id="a b@group.calendar.google.com")
    method, url, auth, _ = calls[0]
    assert method == "POST" and auth == "Bearer ya29.test"
    assert url == ("https://www.googleapis.com/calendar/v3/calendars/"
                   "a%20b%40group.calendar.google.com/events?sendUpdates=none")

    C.send_email([ROW], "sme", ["a@ik.example"], "Next week")
    assert calls[1][1] == "https://api.resend.com/emails" and calls[1][2] == "Bearer re_test"
    assert calls[1][3]["to"] == ["a@ik.example"]

    C.send_sms([ROW], "sme", ["+919900000001"], "Next week")
    assert calls[2][1] == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert calls[2][2].startswith("Basic ") and "To=%2B919900000001" in calls[2][3]


# ---------------- staging guard ----------------

def test_redirect_collapses_every_recipient_onto_one_inbox(live, monkeypatch):
    """The seed roster is `.example` addresses; a live send must not spray a real provider."""
    monkeypatch.setenv("PUBLISH_REDIRECT_TO", "me@real.test")
    sent = []
    res = C.send_email([ROW], "sme", ["a@ik.example", "b@ik.example"], "Next week", api=lambda b: sent.append(b))
    assert len(sent) == 1 and sent[0]["to"] == ["me@real.test"]
    assert res["count"] == 1 and "redirected to me@real.test" in res["detail"] and "2 real recipient" in res["detail"]


def test_sms_redirect_is_a_separate_switch(live, monkeypatch):
    monkeypatch.setenv("PUBLISH_REDIRECT_SMS_TO", "+919812345678")
    monkeypatch.setenv("PUBLISH_REDIRECT_TO", "me@real.test")     # must not leak into SMS
    sent = []
    C.send_sms([ROW], "sme", ["+919900000001", "+919900000002"], "Next week", api=lambda f: sent.append(f))
    assert len(sent) == 1 and "To=%2B919812345678" in sent[0]


def test_no_redirect_configured_sends_to_everyone(live):
    sent = []
    C.send_email([ROW], "sme", ["a@ik.example", "b@ik.example"], "Next week", api=lambda b: sent.append(b))
    assert [s["to"][0] for s in sent] == ["a@ik.example", "b@ik.example"]


# ---------------- Google Sheets ----------------

@pytest.fixture
def sheet(monkeypatch, live):
    monkeypatch.setenv("SHEET_ID", "1AbCsheetid")
    monkeypatch.delenv("PUBLISH_DISABLED", raising=False)


def test_sheets_columns_match_the_typescript_contract():
    """lib/export.ts owns the column order; this asserts the Python side has not drifted from it,
    which is the whole reason there is no second parser here."""
    src = open(os.path.join(ROOT, "lib", "export.ts")).read()
    listed = src.split("EXPORT_COLUMNS: (keyof ExportRow)[] = [", 1)[1].split("];", 1)[0]
    ts = [c.strip().strip('"') for c in listed.replace("\n", " ").split(",") if c.strip().strip('"')]
    assert list(SH.EXPORT_COLUMNS) == ts
    # and the engine's own export row emits exactly those keys, in that order
    from engine.run import _export_row
    row = dict(ROW, start_utc="2026-09-07T04:30:00Z", batch_id="DSA-01", type="class")
    assert list(_export_row(row, "approved")) == ts


def test_sheets_degrade_to_simulated_without_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_JSON", raising=False)
    monkeypatch.delenv("SHEET_ID", raising=False)
    read = SH.read_tab(None, "Sessions")
    assert read["status"] == "simulated" and read["live"] is False and read["csv"] == ""
    assert "GOOGLE_OAUTH_JSON" in read["detail"]
    write = SH.write_draft(None, [{"week": "2026-W37"}])
    assert write["status"] == "simulated" and write["live"] is False
    assert SH.status()["live"] is False


def test_sheets_says_which_piece_is_missing(monkeypatch, live):
    monkeypatch.delenv("SHEET_ID", raising=False)
    ok, why = SH.sheets_ready()
    assert not ok and "SHEET_ID not set" in why


def test_sheets_kill_switch_beats_credentials(monkeypatch, sheet):
    monkeypatch.setenv("PUBLISH_DISABLED", "1")
    assert SH.read_tab(None, "Sessions")["status"] == "simulated"
    assert SH.sheets_ready()[1] == "PUBLISH_DISABLED is set"


def test_read_tab_returns_csv_the_typescript_parser_can_split(sheet):
    calls = []

    def api(method, path, body=None):
        calls.append((method, path, body))
        return {"values": [["batch_id", "course", "sme_name"],
                           ["DSA-01", "DSA", "Nair, Kavya"],          # comma -> must be quoted
                           ["DSA-02", "DSA", 'He said "hi"'],         # quote -> must be doubled
                           ["", "", ""]]}                             # blank row -> dropped
    res = SH.read_tab(None, "Sessions", api=api)
    assert res["status"] == "sent" and res["live"] is True and res["count"] == 2
    assert calls == [("GET", "/values/Sessions", None)]
    assert res["csv"] == ('batch_id,course,sme_name\r\n'
                          'DSA-01,DSA,"Nair, Kavya"\r\n'
                          'DSA-02,DSA,"He said ""hi"""\r\n')
    # the quoting rule is lib/export.ts's, so lib/import.ts::splitCsv reads it back unchanged
    assert SH.to_csv([["a", "b,c", 'd"e']]) == 'a,"b,c","d""e"\r\n'


def test_read_tab_reports_an_empty_tab_rather_than_pretending(sheet):
    res = SH.read_tab(None, "SMEs", api=lambda *a, **k: {"values": []})
    assert res["status"] == "skipped" and res["csv"] == "" and "empty" in res["detail"]


def test_read_tab_survives_an_api_error(sheet):
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    res = SH.read_tab(None, "Sessions", api=boom)
    assert res["status"] == "error" and res["live"] is True and res["csv"] == ""


def test_write_draft_emits_export_columns_and_clears_first(sheet):
    calls = []

    def api(method, path, body=None):
        calls.append((method, path, body))
        return {}
    rows = [{"week": "2026-W37", "date": "2026-09-07", "time_ist": "10:00", "batch": "DSA-01",
             "subject": "DSA", "sub_specialty": "Arrays & Strings", "session_type": "class",
             "sme_name": "Kavya Nair", "status": "approved", "flags": ""}]
    res = SH.write_draft(None, rows, api=api)
    assert res["status"] == "sent" and res["count"] == 1 and res["live"] is True
    # stale rows from a longer week must not survive a shorter one
    assert calls[0][0] == "POST" and calls[0][1].endswith(":clear")
    assert calls[1][0] == "PUT" and "valueInputOption=RAW" in calls[1][1]
    values = calls[1][2]["values"]
    assert values[0] == list(SH.EXPORT_COLUMNS)
    assert values[1] == ["2026-W37", "2026-09-07", "10:00", "DSA-01", "DSA", "Arrays & Strings",
                         "class", "Kavya Nair", "approved", ""]


def test_write_draft_fills_missing_cells_rather_than_shifting_columns(sheet):
    calls = []
    SH.write_draft(None, [{"week": "2026-W37"}], api=lambda m, p, b=None: calls.append((m, p, b)) or {})
    assert calls[1][2]["values"][1] == ["2026-W37"] + [""] * (len(SH.EXPORT_COLUMNS) - 1)

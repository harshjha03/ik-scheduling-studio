"""Storage and outbound channels. No credentials and no network: the HTTP call is injected."""
import json
import os
import re
import sys
import threading
import time
import urllib.error
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import channels as C  # noqa: E402
from engine import ingest as IN  # noqa: E402
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

    # publishing the same week again sends nothing at all: the stored body hash still matches
    second = C.send_calendar([ROW], "sme", store=db, api=api)
    assert second["status"] == "sent" and second["count"] == 1
    assert len(calls) == 1, "an unchanged row must not be re-sent"
    assert "unchanged and skipped" in second["detail"]

    # change the class and it must UPDATE the event we already own, never create a second one
    moved = {**ROW, "sme_name": "Rahul Desai"}
    third = C.send_calendar([moved], "sme", store=db, api=api)
    assert third["status"] == "sent"
    assert calls[1][0] == "PUT" and calls[1][1] == "/evt-1?sendUpdates=none"
    assert db.event_for(ROW["session_id"], CAL) == "evt-1"


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


# ---------------- ingest: one path, two sources ----------------

def test_seed_source_serialises_every_dataset_into_its_import_contract():
    """The seed writer must emit exactly the headers lib/import.ts checks for, or the bundled data
    cannot travel the same path a Sheet does."""
    src = IN.SeedSource()
    heads = {
        "sessions": "batch_id,course,level,learners,topic,class_type,day,time,sme_name",
        "smes": "sme_id,name,email,phone,city,courses,topics,level,preferred_per_week,work_days,work_hours",
        "history": "sme_id,week,sessions_taught,batches,per_topic_rating",
    }
    for dataset, head in heads.items():
        res = src.fetch(dataset)
        assert res["status"] == "sent" and res["live"] is False, dataset
        assert res["source"] == "seed data" and res["count"] > 0
        lines = res["csv"].split("\r\n")
        assert lines[0] == head, dataset
        assert len(lines[1].split(",")) >= len(head.split(",")) - 2   # quoted cells may merge on a naive split
    # the header rows must match the TypeScript templates, which own the contract
    ts = open(os.path.join(ROOT, "lib", "import.ts")).read()
    for head in heads.values():
        assert f'"{head}"' in ts or f"const head = \"{head}\"" in ts, head


def test_seed_source_needs_no_credentials_and_is_the_default(monkeypatch):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_JSON", raising=False)
    monkeypatch.delenv("SHEET_ID", raising=False)
    assert isinstance(IN.pick_source(), IN.SeedSource)
    assert IN.pick_source().fetch("sessions")["count"] == 41


def test_a_configured_sheet_wins_and_seed_can_be_forced(sheet):
    assert isinstance(IN.pick_source(), IN.SheetSource)
    assert isinstance(IN.pick_source(prefer="seed"), IN.SeedSource)


def test_sheet_source_tags_its_rows_with_where_they_came_from(sheet):
    src = IN.SheetSource()
    src.tabs["smes"] = "Roster2026"
    calls = []
    monkey = lambda sid, tab, api=None: calls.append(tab) or C.Result("sent", "ok", 1, True, csv="a\r\n")  # noqa: E731
    SH_read, SH.read_tab = SH.read_tab, monkey
    try:
        res = src.fetch("smes")
    finally:
        SH.read_tab = SH_read
    assert calls == ["Roster2026"] and res["source"] == "Google Sheet"


def test_unknown_dataset_is_named_not_guessed():
    assert IN.SeedSource().fetch("payroll")["status"] == "error"
    assert IN.SheetSource().fetch("payroll")["status"] == "error"


def test_seed_roster_hours_are_converted_back_to_ist():
    """The roster contract is IST hours; the engine stores UTC windows. 03:30Z is 09:00 IST."""
    assert IN._to_ist_hhmm("03:30") == "09:00"
    assert IN._to_ist_hhmm("02:30") == "08:00"
    assert IN._to_ist_hhmm("14:30") == "20:00"


# ---------------- the override log (the trust metric) ----------------

def test_override_log_records_who_changed_what(db):
    entries = [{"session_id": "S1", "batch_id": "B1", "from_sme_id": "T01", "to_sme_id": "T02", "rule_risk": None},
               {"session_id": "S2", "batch_id": "B1", "from_sme_id": None, "to_sme_id": "T03",
                "rule_risk": "outside subject expertise"}]
    assert db.record_overrides("2026-W37", entries) == 2
    got = db.overrides("2026-W37")
    assert [o["session_id"] for o in got] == ["S2", "S1"]          # newest first
    assert {o["actor"] for o in got} == {"human"}
    assert got[0]["rule_risk"] == "outside subject expertise"
    assert db.record_overrides("2026-W37", [{"session_id": "S3", "batch_id": "B1", "to_sme_id": "T04"}],
                               actor="agent") == 1
    assert next(o for o in db.overrides() if o["session_id"] == "S3")["actor"] == "agent"


def test_an_entry_with_no_target_is_not_an_override(db):
    """A 'change requested' event carries no to_sme_id; logging it would inflate the rate."""
    assert db.record_overrides("2026-W37", [{"session_id": "S1", "batch_id": "B1", "to_sme_id": ""}]) == 0
    assert db.overrides() == []


def test_override_counts_are_per_week_and_distinct(db):
    for to in ("T02", "T03"):                                   # same class overridden twice
        db.record_overrides("2026-W37", [{"session_id": "S1", "batch_id": "B1", "to_sme_id": to}])
    db.record_overrides("2026-W36", [{"session_id": "S9", "batch_id": "B1", "to_sme_id": "T04"}])
    assert db.override_counts() == {"2026-W36": 1, "2026-W37": 1}, "one class changed twice is one disagreement"


# ---------------- the dataset table ----------------

def test_datasets_round_trip_and_reset(db):
    assert db.load_datasets() == {}
    db.save_dataset("smes", [{"id": "T99"}], "sheet")
    got = db.load_datasets()["smes"]
    assert got["payload"] == [{"id": "T99"}] and got["source"] == "sheet" and got["updated_at"]
    db.save_dataset("smes", [{"id": "T98"}], "csv")              # same name replaces, not duplicates
    assert db.load_datasets()["smes"]["payload"] == [{"id": "T98"}]
    assert db.reset_datasets() == 1 and db.load_datasets() == {}


# ---------------- batched calendar writes ----------------

def test_remember_events_writes_the_batch_in_one_call(db):
    db.remember_events("cal", [("S1", "e1", "h1"), ("S2", "e2", None)])
    assert db.owned_on("cal") == {"S1": ("e1", "h1"), "S2": ("e2", None)}
    assert db.events_on("cal") == {"S1": "e1", "S2": "e2"}       # the older view still works
    db.remember_events("cal", [("S1", "e1b", "h2")])             # upsert, not a duplicate row
    assert db.owned_on("cal")["S1"] == ("e1b", "h2")
    db.remember_events("cal", [])                                # nothing to do, no query
    assert len(db.owned_on("cal")) == 2


def test_body_hash_survives_a_database_that_predates_it(tmp_path):
    """The column was added after calendar_event shipped; SQLite cannot ADD COLUMN IF NOT EXISTS, so
    the migration runs every boot and a duplicate-column error must be the expected no-op."""
    path = str(tmp_path / "old.db")
    import sqlite3
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE calendar_event (session_id TEXT NOT NULL, calendar_id TEXT NOT NULL,
                        event_id TEXT NOT NULL, updated_at TEXT NOT NULL,
                        PRIMARY KEY (session_id, calendar_id))""")
        conn.execute("INSERT INTO calendar_event VALUES ('S1','cal','e1','2026-01-01')")
    old = Store(url=None, path=path)                             # _init runs the migration
    assert old.owned_on("cal") == {"S1": ("e1", None)}
    old.remember_events("cal", [("S2", "e2", "h2")])
    assert old.owned_on("cal")["S2"] == ("e2", "h2")
    Store(url=None, path=path)                                   # booting twice must not raise


def test_the_literal_data_route_is_declared_before_the_parameterised_one():
    """FastAPI matches routes in declaration order, so /api/data/{name} declared first swallowed
    /api/data/reset and answered 'unknown dataset `reset`'."""
    from engine import dotenv as _dotenv
    real, _dotenv.load = _dotenv.load, lambda path: 0
    os.environ["DATABASE_URL"] = ""
    os.environ.setdefault("IK_DB_PATH", "/tmp/ik-route-test.db")
    try:
        from api import index
    finally:
        _dotenv.load = real
    paths = [r.path for r in index.app.routes if "/api/data" in getattr(r, "path", "")]
    assert paths.index("/api/data/reset") < paths.index("/api/data/{name}")


def test_only_datasets_the_page_boots_from_are_writable():
    """Accepting `courses` would store something the dashboard then ignores — a silent no-op."""
    from api import index
    assert set(index.DATASETS) == {"sessions_next", "sessions_current", "smes", "smes_current",
                                   "history", "batches"}
    for structural in ("courses", "weeks", "meta"):
        assert structural not in index.DATASETS


# ---------------- publish performance ----------------

def test_the_row_loop_runs_in_parallel(db, live):
    """41 serial writes were most of a 16s publish. With a latency-stubbed api the pool must finish in
    roughly one batch of round trips, not 41 of them."""
    rows = [{**ROW, "session_id": f"S{i:02d}"} for i in range(24)]
    order = []

    def slow_api(method, path, body):
        time.sleep(0.05)                       # stand in for a real TCP+TLS round trip
        order.append(threading.current_thread().name)
        return {"id": f"evt-{len(order)}"}

    t0 = time.perf_counter()
    res = C.send_calendar(rows, "sme", store=db, api=slow_api)
    took = time.perf_counter() - t0
    assert res["count"] == 24 and res["status"] == "sent"
    serial = 24 * 0.05
    assert took < serial / 3, f"{took:.2f}s for 24 rows against a {serial:.2f}s serial floor"
    assert len(set(order)) > 1, "the writes must actually be spread across workers"
    # ...and every row still landed exactly once, in one batched DB write
    assert len(db.owned_on(CAL)) == 24


def test_one_bad_row_still_does_not_sink_the_parallel_batch(db, live):
    """Per-row failure isolation has to survive the thread pool, and the count in `detail` with it."""
    rows = [{**ROW, "session_id": f"S{i}"} for i in range(4)]
    lock = threading.Lock()
    seen = []

    def flaky(method, path, body):
        with lock:
            seen.append(body["description"])
            n = len(seen)
        if n == 2:
            raise RuntimeError("boom")
        return {"id": f"e{n}"}

    res = C.send_calendar(rows, "sme", store=db, api=flaky)
    assert res["status"] == "sent" and res["count"] == 3
    assert "1 failed" in res["detail"] and len(db.owned_on(CAL)) == 3


def test_the_publish_reports_how_long_it_took(db, live):
    res = C.send_calendar([ROW], "sme", store=db, api=lambda m, p, b: {"id": "e1"})
    assert re.search(r"in \d+\.\d+s", res["detail"]), res["detail"]


def test_the_access_token_is_cached_per_scope(monkeypatch, live):
    """It refreshed on every send_calendar call, and the student fan-out calls that once per cohort
    calendar — several OAuth round trips before a single event was written."""
    refreshes = []

    class FakeCreds:
        token = "ya29.fake"
        expiry = datetime(2099, 1, 1)

        def refresh(self, _request):
            refreshes.append(1)

    class FakeSA:                                   # stands in for service_account.Credentials
        @staticmethod
        def from_service_account_info(info, scopes=None):
            return FakeCreds()

    C.forget_tokens()
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests",
                        type(sys)("google.auth.transport.requests"))
    sys.modules["google.auth.transport.requests"].Request = lambda: object()
    sa_module = type(sys)("google.oauth2.service_account")
    sa_module.Credentials = FakeSA
    oauth2 = type(sys)("google.oauth2")
    oauth2.service_account = sa_module
    monkeypatch.setitem(sys.modules, "google.oauth2", oauth2)
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", sa_module)
    try:
        assert C._google_token() == "ya29.fake"
        assert C._google_token() == "ya29.fake"
        assert C._google_token([C.CAL_SCOPE]) == "ya29.fake"
        assert len(refreshes) == 1, "the same scope set must not refresh twice"
        C._google_token([C.SHEETS_SCOPE])
        assert len(refreshes) == 2, "a different scope set needs its own token"
    finally:
        C.forget_tokens()


def test_the_http_session_is_pooled_and_reused():
    """urllib opened a fresh TCP+TLS connection per event; the pooled session is what removes that."""
    a, b = C._http(), C._http()
    assert a is b
    adapter = a.get_adapter("https://www.googleapis.com/")
    assert adapter._pool_maxsize == 16 and adapter._pool_connections == 4


def test_a_failing_status_still_carries_its_code(monkeypatch):
    """send_calendar's re-create path keys off 404/410, and did so when this was urllib."""
    class FakeResp:
        status_code = 404
        text = "gone"

    class FakeSession:
        def request(self, *a, **k):
            return FakeResp()

        def get(self, *a, **k):
            return FakeResp()
    monkeypatch.setattr(C, "_http", lambda: FakeSession())
    for call in (lambda: C._post("https://x/y", {}, {}), lambda: C._get("https://x/y", {})):
        with pytest.raises(Exception) as e:
            call()
        assert getattr(e.value, "code", None) == 404

"""QA pass: time, timezone and DST. The area with the most latent-bug surface.

Every session is stored in UTC and displayed in IST; availability windows are authored in an SME's
local timezone and converted to UTC weekday + HH:MM at generation time. Three conversions, so three
chances for a day to slip.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import stages as S  # noqa: E402
from engine.run import run_pipeline  # noqa: E402
from tests.test_engine import rd, session, sme  # noqa: E402

UTC = ZoneInfo("UTC")


def to_utc_window(local_day: int, start: str, end: str, tz: str, monday=datetime(2026, 8, 31)):
    """The generator's conversion, reproduced here so a round trip can be checked independently."""
    day = (monday + timedelta(days=local_day)).date()
    s = datetime.combine(day, dtime.fromisoformat(start), ZoneInfo(tz)).astimezone(UTC)
    e = datetime.combine(day, dtime.fromisoformat(end), ZoneInfo(tz)).astimezone(UTC)
    return S.WEEKDAYS[s.weekday()], s.strftime("%H:%M"), e.strftime("%H:%M")


# ---------------- the round trip ----------------

def test_a_window_authored_locally_round_trips_through_utc_to_ist():
    day, start, end = to_utc_window(0, "09:00", "18:00", "Asia/Kolkata")
    assert (day, start, end) == ("Mon", "03:30", "12:30"), "IST is UTC+5:30"
    back = S.parse_utc("2026-08-31T03:30:00Z").astimezone(S.IST)
    assert back.strftime("%H:%M") == "09:00" and S.fmt_ist(S.parse_utc("2026-08-31T03:30:00Z")) == "Mon 09:00 IST"


def test_the_shipped_roster_converts_the_way_its_local_note_claims():
    """Divya Pillai's Tuesday-local 18:00–23:00 in Los Angeles must be *Wednesday* 01:00–06:00 UTC —
    the weekday has to roll over, not stay put."""
    roster = {s["name"]: s for s in rd("smes")}
    divya = roster["Divya Pillai"]
    assert divya["timezone"] == "America/Los_Angeles"
    day, start, end = to_utc_window(1, "18:00", "23:00", "America/Los_Angeles")
    assert (day, start, end) == ("Wed", "01:00", "06:00")
    assert any(w["weekday"] == "Wed" and w["start_utc"] == "01:00" for w in divya["weekly_availability"])

    for name, tz in (("Meera Joshi", "Europe/London"), ("Farhan Sheikh", "Asia/Dubai")):
        if name not in roster:
            pytest.skip(f"{name} not on the seeded roster")
        who = roster[name]
        assert who["timezone"] == tz
        for w in who["weekly_availability"]:
            local_start = w["local"].split("–")[0]
            hits = [d for d in range(7) if to_utc_window(d, local_start, local_start, tz)[0] == w["weekday"]]
            assert hits, f"{name}: {w} does not correspond to any local day"


def test_every_shipped_window_lands_on_the_weekday_its_local_note_implies():
    for s in rd("smes"):
        tz = s["timezone"]
        for w in s["weekly_availability"]:
            start_local, end_local = w["local"].split(" ")[0].split("–")
            hits = [d for d in range(7)
                    if to_utc_window(d, start_local, end_local, tz) == (w["weekday"], w["start_utc"], w["end_utc"])]
            assert hits, f"{s['id']} {s['name']}: {w} matches no local day in {tz}"


# ---------------- the edges ----------------

def test_a_window_crossing_utc_midnight_matches_both_sides():
    us = sme("A", windows=[{"weekday": "Mon", "start_utc": "22:00", "end_utc": "02:00"}])
    for start, expected in [("2026-08-31T22:30:00Z", "A"),      # Monday night
                            ("2026-09-01T00:30:00Z", "A"),      # Tuesday morning, same window
                            ("2026-08-31T21:00:00Z", None)]:    # before it opens
        res = run_pipeline([session("S1", start=start)], [us], [], [], llm_enabled=False)
        assert res["draft"][0]["sme_id"] == expected, f"{start} -> {res['draft'][0]['sme_id']}"


def test_a_sunday_utc_window_is_labelled_sunday_not_monday():
    """A Saturday evening in Los Angeles is Sunday UTC. Relabelling it Monday moved a teacher's hours
    to the far end of the week, and it had already shipped that way."""
    assert to_utc_window(5, "18:00", "23:00", "America/Los_Angeles")[0] == "Sun"
    assert not any(w["weekday"] == "Mon" and w["start_utc"] == "01:00"
                   for s in rd("smes") if s["timezone"] == "America/Los_Angeles"
                   for w in s["weekly_availability"]), "the mislabelled Monday window is gone"


def test_a_ninety_minute_session_starting_thirty_minutes_before_close_is_rejected():
    who = sme("A", windows=[{"weekday": "Mon", "start_utc": "09:00", "end_utc": "15:00"}])
    fits = {**session("S1", start="2026-08-31T13:30:00Z"), "duration_min": 90}      # ends 15:00 exactly
    over = {**session("S2", start="2026-08-31T14:30:00Z"), "duration_min": 90}      # ends 16:00
    assert run_pipeline([fits], [who], [], [], llm_enabled=False)["draft"][0]["sme_id"] == "A"
    res = run_pipeline([over], [who], [], [], llm_enabled=False)
    assert res["draft"][0]["sme_id"] is None
    assert res["draft"][0]["eliminated"][0]["rule"] == "availability"


# ---------------- DST ----------------

def test_us_spring_forward_and_autumn_back_shift_the_utc_window_by_an_hour():
    """America/Los_Angeles is UTC-8 in winter and UTC-7 in summer. A window authored as local 09:00
    must therefore be a *different* UTC time either side of the transition."""
    winter = datetime(2026, 3, 2)      # the Monday before spring-forward (2026-03-08)
    summer = datetime(2026, 3, 16)     # the Monday after
    _, w_start, _ = to_utc_window(0, "09:00", "17:00", "America/Los_Angeles", monday=winter)
    _, s_start, _ = to_utc_window(0, "09:00", "17:00", "America/Los_Angeles", monday=summer)
    assert (w_start, s_start) == ("17:00", "16:00"), "spring-forward must move the UTC window an hour earlier"

    autumn_before = datetime(2026, 10, 26)     # before autumn-back (2026-11-01)
    autumn_after = datetime(2026, 11, 2)
    _, a_start, _ = to_utc_window(0, "09:00", "17:00", "America/Los_Angeles", monday=autumn_before)
    _, b_start, _ = to_utc_window(0, "09:00", "17:00", "America/Los_Angeles", monday=autumn_after)
    assert (a_start, b_start) == ("16:00", "17:00"), "autumn-back must move it an hour later"


def test_the_engine_itself_is_dst_free_because_it_only_ever_sees_utc():
    """Stage A compares UTC instants against UTC windows, so a DST transition inside the scheduled
    week cannot shift an assignment. The conversion is the generator's job, done once."""
    who = sme("A", windows=[{"weekday": "Sun", "start_utc": "16:00", "end_utc": "20:00"}])
    across = session("S1", start="2026-11-01T17:30:00Z")       # US autumn-back Sunday
    res = run_pipeline([across], [who], [], [], llm_enabled=False)
    assert res["draft"][0]["sme_id"] == "A"
    assert S.fmt_ist(S.parse_utc("2026-11-01T17:30:00Z")) == "Sun 23:00 IST"


def test_every_timestamp_the_engine_emits_is_utc_and_every_label_is_ist():
    res = run_pipeline(rd("sessions_next"), rd("smes"), rd("history"), [], llm_enabled=False)
    for row in res["draft"]:
        assert row["start_utc"].endswith("Z"), f"{row['session_id']} is not stored as UTC"
        S.parse_utc(row["start_utc"])                       # parses, or raises
    labelled = [f["reason"] for f in res["flags"] if "IST" in f["reason"]]
    assert labelled, "unfilled reasons quote the time, and quote it in IST"
    for reason in labelled:
        assert "UTC" not in reason, f"a third time form leaked into the UI: {reason}"

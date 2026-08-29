"""QA pass: the stage behaviours the existing suite does not pin down.

Written during a QA review, so each test states the claim it is checking rather than the code path.
Nothing here changes application behaviour; where a test documents a defect it says so and asserts the
*current* behaviour, so the report and the suite cannot drift apart.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import stages as S  # noqa: E402
from engine.run import run_pipeline  # noqa: E402
from tests.test_engine import FULL, rd, session, sme, weeks  # noqa: E402
from tests.test_invariants import assert_no_hard_rule_violation  # noqa: E402


# ---------------- Stage A: every rule, and the order they fire in ----------------

def test_each_elimination_rule_fires_in_isolation():
    sess = session("S1", subject="Chemistry", ss="organic", typ="class", level=2)
    cases = {
        "subject": sme("A", subject="Maths", ss=None),
        "sub_specialty": sme("B", ss="physical"),
        "training_level": sme("C", level=1),
        "availability": sme("D", windows=[{"weekday": "Mon", "start_utc": "20:00", "end_utc": "23:00"}]),
        "calendar_busy": {**sme("E"), "external_busy": [{"start_utc": "2026-08-31T04:00:00Z",
                                                          "end_utc": "2026-08-31T05:00:00Z"}]},
    }
    for rule, who in cases.items():
        _, elim = S.stage_a_hard_filter(sess, [who], [])
        assert [e["rule"] for e in elim] == [rule], f"{rule}: got {elim}"
        assert S.rule_label(rule) and S.rule_label(rule) != rule, f"{rule} has no plain-language label"
    # overlap needs a draft to collide with
    busy = sme("F")
    held = {**session("S0"), "session_id": "S0", "sme_id": "F"}
    _, elim = S.stage_a_hard_filter(sess, [busy], [held])
    assert elim[0]["rule"] == "overlap:S0"
    assert "time overlap with S0" == S.rule_label("overlap:S0")


def test_the_first_applicable_rule_is_the_one_recorded():
    """An SME failing several rules is reported by the first in if/elif order, so the reason a
    coordinator reads is stable rather than whichever check happened to run."""
    sess = session("S1", subject="Chemistry", ss="organic", level=3)
    hopeless = sme("A", subject="Maths", ss="physical", level=1,
                   windows=[{"weekday": "Sun", "start_utc": "01:00", "end_utc": "02:00"}])
    _, elim = S.stage_a_hard_filter(sess, [hopeless], [])
    assert elim[0]["rule"] == "subject", "subject is checked first"
    same_subject = sme("B", subject="Chemistry", ss="physical", level=1,
                       windows=[{"weekday": "Sun", "start_utc": "01:00", "end_utc": "02:00"}])
    _, elim = S.stage_a_hard_filter(sess, [same_subject], [])
    assert elim[0]["rule"] == "sub_specialty", "then topic, before level or availability"


def test_unfilled_reason_names_every_rule_in_plain_language():
    sess = session("S1", subject="Chemistry", ss="organic", level=2)
    roster = [sme("A", subject="Maths", ss=None), sme("B", ss="physical"), sme("C", level=1),
              sme("D", windows=[{"weekday": "Mon", "start_utc": "20:00", "end_utc": "23:00"}]),
              {**sme("E"), "external_busy": [{"start_utc": "2026-08-31T04:00:00Z",
                                              "end_utc": "2026-08-31T05:00:00Z"}]}]
    _, elim = S.stage_a_hard_filter(sess, roster, [])
    reason = S.unfilled_reason(sess, elim)
    assert reason.startswith("No eligible SME:")
    for fragment in ("unavailable at", "below required training level", "calendar conflict", "not carrying"):
        assert fragment in reason, f"{fragment!r} missing from: {reason}"
    assert "Name A" not in reason, "an SME of another subject is not worth naming"


# ---------------- Stage B: the formula, the window, the margin ----------------

def test_a_perfect_candidate_scores_exactly_one():
    """0.5·fairness + 0.3·continuity + 0.2·performance, with nothing else in the way."""
    perfect = sme("A", history=weeks(0, ["B01"], rating=5.0))
    heavier = sme("B", history=weeks(9))
    hist = S.build_hist([], [perfect, heavier])
    scored = S.stage_b_score(session("S1"), [perfect], [perfect, heavier], hist, {})
    assert scored[0]["score"] == pytest.approx(1.0, abs=1e-6)
    c = scored[0]["components"]
    assert (c["fairness"], c["continuity"], c["adjustment"], c["fairness_penalty"]) == (1.0, 1.0, 0.0, 0.0)


def test_a_single_sme_pool_does_not_divide_by_zero():
    """lo == hi, so the EPS guard is the only thing standing between this and a ZeroDivisionError."""
    only = sme("A", history=weeks(4))
    scored = S.stage_b_score(session("S1"), [only], [only], S.build_hist([], [only]), {})
    assert scored[0]["components"]["fairness"] == pytest.approx(1.0, abs=1e-3)
    assert scored[0]["score"] == pytest.approx(0.5 + 0.2 * (4.0 / 5), abs=1e-3)


def test_past_load_uses_the_last_three_weeks_only():
    six = [{"week": f"2026-W{30 + i}", "sessions_taught": 100 if i < 3 else 1,
            "batches": [], "per_topic_rating": {}, "post_session_rating": None} for i in range(6)]
    assert S.past_load(six) == 3, "the oldest three weeks must be ignored"
    who = sme("A", history=six)
    hist = S.build_hist([], [who])
    assert S.projected_load("A", hist, {}) == 3
    assert S.projected_load("A", hist, {"A": 2}) == 5, "the draft is the fourth week"


def test_the_tie_margin_boundary():
    """is_clear_winner uses MARGIN - 1e-9, so exactly 0.15 counts as clear."""
    def pair(gap):
        return [{"sme_id": "A", "score": 0.5 + gap}, {"sme_id": "B", "score": 0.5}]
    assert S.is_clear_winner(pair(0.1501)) is True
    assert S.is_clear_winner(pair(0.15)) is True, "exactly MARGIN is a clear winner"
    assert S.is_clear_winner(pair(0.1499)) is False
    assert S.is_clear_winner([{"sme_id": "A", "score": 0.2}]) is True, "one candidate is always clear"


def test_scoring_ties_break_on_sme_id_so_results_never_flap():
    a, b = sme("A", history=weeks(3)), sme("B", history=weeks(3))
    hist = S.build_hist([], [a, b])
    scored = S.stage_b_score(session("S1"), [a, b], [a, b], hist, {})
    assert scored[0]["score"] == scored[1]["score"]
    assert [c["sme_id"] for c in scored] == ["A", "B"], "identical scores sort by sme_id"


def test_the_pipeline_is_deterministic():
    sessions, smes, history = rd("sessions_next"), rd("smes"), rd("history")
    a = run_pipeline(sessions, smes, history, [], llm_enabled=False)
    b = run_pipeline(sessions, smes, history, [], llm_enabled=False)
    assert [(r["session_id"], r["sme_id"], r["score"]) for r in a["draft"]] == \
           [(r["session_id"], r["sme_id"], r["score"]) for r in b["draft"]]
    assert a["stats"] == b["stats"]


# ---------------- Stage D: what it rejects and what it keeps ----------------

def test_stage_d_rejects_each_hard_rule_and_names_it():
    for rule, who, sess in [
        ("subject expertise", sme("A", subject="Maths", ss=None), session("S1")),
        ("sub-specialty expertise", sme("A", ss="physical"), session("S1")),
        ("training level requirement", sme("A", level=1), session("S1", level=3)),
        ("availability window", sme("A", windows=[]), session("S1")),
        ("calendar conflict", {**sme("A"), "external_busy": [{"start_utc": "2026-08-31T04:00:00Z",
                                                              "end_utc": "2026-08-31T05:00:00Z"}]},
         session("S1")),
    ]:
        row = {**sess, "session_id": sess["id"], "sme_id": "A", "sme_name": "Name A", "flags": [],
               "stage": "auto", "score": 1.0}
        S.stage_d_validate([row], [who], S.build_hist([], [who]))
        assert row["sme_id"] is None and row["rejected_sme_id"] == "A"
        flag = next(f for f in row["flags"] if f["code"] == "UNFILLED")
        assert rule in flag["reason"], f"{rule} not named in {flag['reason']}"


def test_an_overlap_flags_hard_conflict_and_clears_the_assignment():
    who = sme("A")
    rows = [{**session(f"S{i}"), "session_id": f"S{i}", "sme_id": "A", "sme_name": "Name A",
             "flags": [], "stage": "auto", "score": 1.0} for i in (1, 2)]
    S.stage_d_validate(rows, [who], S.build_hist([], [who]))
    assert rows[0]["sme_id"] == "A", "the first one keeps it"
    assert rows[1]["sme_id"] is None, "the second is cleared, not left double-booked"
    codes = [f["code"] for f in rows[1]["flags"]]
    assert "HARD_CONFLICT" in codes and "UNFILLED" in codes


def test_a_row_is_flagged_once_even_when_its_sme_carries_two_subjects():
    """The fairness pass walks every subject pool; a multi-subject SME must not collect one flag per
    pool for the same class."""
    multi = {**sme("A", history=weeks(20)), "subjects": ["Chemistry", "Physics"]}
    others = [sme("B", history=weeks(1)), sme("C", history=weeks(1)),
              {**sme("D", subject="Physics", history=weeks(1)), "subjects": ["Physics"]}]
    rows = [{**session("S1"), "session_id": "S1", "sme_id": "A", "sme_name": "Name A",
             "flags": [], "stage": "auto", "score": 1.0}]
    S.stage_d_validate(rows, [multi, *others], S.build_hist([], [multi, *others]))
    fairness = [f for f in rows[0]["flags"] if f["code"] == "FAIRNESS_VIOLATION"]
    assert len(fairness) == 1, f"flagged {len(fairness)} times: {[f['reason'] for f in fairness]}"


# ---------------- Stage E: the human loop ----------------

def test_override_nudges_are_minus_two_tenths_and_plus_one_tenth():
    adj = S.stage_e_adjustments([{"session_id": "S1", "batch_id": "B01",
                                  "from_sme_id": "A", "to_sme_id": "B"}])
    assert adj == {("A", "B01"): -0.2, ("B", "B01"): 0.1}
    twice = S.stage_e_adjustments([{"session_id": "S1", "batch_id": "B01", "from_sme_id": "A", "to_sme_id": "B"}] * 2)
    assert twice[("A", "B01")] == -0.4, "repeated overrides accumulate"


def test_an_override_that_breaks_a_hard_rule_cannot_survive_a_re_run():
    sessions, smes, history = rd("sessions_next"), rd("smes"), rd("history")
    base = run_pipeline(sessions, smes, history, [], llm_enabled=False)
    row = next(r for r in base["draft"] if r["sme_id"] and r["eliminated"])
    blocked = next(e for e in row["eliminated"] if e["rule"] == "subject")
    ov = [{"session_id": row["session_id"], "batch_id": row["batch_id"],
           "from_sme_id": row["sme_id"], "to_sme_id": blocked["sme_id"]}]
    res = run_pipeline(sessions, smes, history, ov, llm_enabled=False)
    after = next(r for r in res["draft"] if r["session_id"] == row["session_id"])
    assert after["sme_id"] != blocked["sme_id"], "Stage A must not let the pick back in"
    assert_no_hard_rule_violation(res["draft"], smes, "override-into-a-hard-rule")


# ---------------- boundaries ----------------

@pytest.mark.parametrize("sessions,label", [([], "zero sessions")])
def test_zero_sessions(sessions, label):
    smes = [sme("A")]
    res = run_pipeline(sessions, smes, [], [], llm_enabled=False)
    assert res["draft"] == [] and res["stats"]["total_sessions"] == 0


def test_zero_smes_leaves_everything_unfilled_with_a_reason():
    res = run_pipeline([session("S1")], [], [], [], llm_enabled=False)
    row = res["draft"][0]
    assert row["sme_id"] is None
    assert "no Chemistry SMEs in the pool" in next(f["reason"] for f in row["flags"] if f["code"] == "UNFILLED")


def test_a_session_that_overruns_its_window_is_not_staffed():
    """The window is 01:30-15:30 UTC. 8 hours from 04:30 still fits (12:30); 90 minutes from 14:30
    does not (16:00), and that is the case that matters — the overrun, not the length."""
    who = sme("A")
    fits = {**session("S1", start="2026-08-31T04:30:00Z"), "duration_min": 480}
    assert run_pipeline([fits], [who], [], [], llm_enabled=False)["draft"][0]["sme_id"] == "A"

    overruns = {**session("S2", start="2026-08-31T14:30:00Z"), "duration_min": 90}
    res = run_pipeline([overruns], [who], [], [], llm_enabled=False)
    assert res["draft"][0]["sme_id"] is None, "a session running past the window's end must not be staffed"
    assert res["draft"][0]["eliminated"][0]["rule"] == "availability"


def test_a_session_at_2330_crossing_midnight():
    late = sme("A", windows=[{"weekday": "Mon", "start_utc": "22:00", "end_utc": "02:00"}])
    crossing = {**session("S1", start="2026-08-31T23:30:00Z"), "duration_min": 60}
    res = run_pipeline([crossing], [late], [], [], llm_enabled=False)
    assert res["draft"][0]["sme_id"] == "A", "a crossing window must cover a crossing session"


def test_an_sme_with_no_windows_and_one_with_no_topics():
    nowhere = sme("A", windows=[])
    generalist = {**sme("B"), "topics": [], "sub_specialty": None}
    res = run_pipeline([session("S1")], [nowhere, generalist], [], [], llm_enabled=False)
    assert res["draft"][0]["sme_id"] == "B", "no declared topics means generalist for the subject"


def test_history_for_an_sme_who_is_not_on_the_roster_is_ignored():
    who = sme("A")
    ghost = [{"sme_id": "GHOST", "week": "2026-W35", "sessions_taught": 99, "batches": [],
              "per_topic_rating": {}, "post_session_rating": None}]
    res = run_pipeline([session("S1")], [who], ghost, [], llm_enabled=False)
    assert res["draft"][0]["sme_id"] == "A"


def test_a_batch_nobody_has_taught_scores_zero_continuity():
    who = sme("A", history=weeks(2, ["OTHER"]))
    scored = S.stage_b_score(session("S1", batch="BRAND-NEW"), [who], [who], S.build_hist([], [who]), {})
    assert scored[0]["components"]["continuity"] == 0.0

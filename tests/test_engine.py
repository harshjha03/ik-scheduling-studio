import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import stages as S  # noqa: E402
from engine.llm import stage_c_llm_adjudicate  # noqa: E402
from engine.run import apply_approvals, run_pipeline  # noqa: E402

FULL = [{"weekday": d, "start_utc": "01:30", "end_utc": "15:30"} for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat")]


def sme(id, subject="Chemistry", ss="organic", level=2, windows=FULL, history=None):
    return {"id": id, "name": f"Name {id}", "subject": subject, "sub_specialty": ss, "training_level": level,
            "timezone": "Asia/Kolkata", "weekly_availability": windows, "preference_notes": "",
            "history": history or []}


def session(id, batch="B01", subject="Chemistry", ss="organic", typ="class", start="2026-08-31T04:30:00Z", level=1):
    return {"id": id, "batch_id": batch, "subject": subject, "sub_specialty": ss, "type": typ, "start_utc": start,
            "duration_min": 60, "mode": "online", "required_training_level": level}


def weeks(load, batches=(), rating=4.0, topic="organic"):
    return [{"week": f"2026-W3{i}", "sessions_taught": load, "batches": list(batches),
             "per_topic_rating": {topic: rating, "doubt": rating}, "post_session_rating": None} for i in range(2, 6)]


def rd(name):
    with open(os.path.join(ROOT, "data", f"{name}.json")) as f:
        return json.load(f)


# ---------------- Stage A ----------------

def rules(sess, smes, draft=()):
    _, elim = S.stage_a_hard_filter(sess, smes, list(draft))
    return {e["sme_id"]: e["rule"] for e in elim}


def test_stage_a_subject_and_sub_specialty():
    r = rules(session("S1"), [sme("A", subject="Maths", ss=None), sme("B", ss="physical_inorganic"), sme("C")])
    assert r == {"A": "subject", "B": "sub_specialty"}


def test_stage_a_doubt_accepts_any_sub_specialty():
    surv, _ = S.stage_a_hard_filter(session("S1", ss=None, typ="doubt"), [sme("B", ss="physical_inorganic"), sme("C")], [])
    assert {s["id"] for s in surv} == {"B", "C"}


def test_stage_a_training_level():
    r = rules(session("S1", level=2), [sme("A", level=1), sme("B", level=2)])
    assert r == {"A": "training_level"}


def test_stage_a_availability_window():
    late = [{"weekday": "Mon", "start_utc": "05:00", "end_utc": "15:30"}]
    r = rules(session("S1", start="2026-08-31T04:30:00Z"), [sme("A", windows=late), sme("B")])
    assert r == {"A": "availability"}
    # a window ending exactly at session end is fine; ending one minute earlier is not
    tight = [{"weekday": "Mon", "start_utc": "04:30", "end_utc": "05:30"}]
    short = [{"weekday": "Mon", "start_utc": "04:30", "end_utc": "05:29"}]
    assert rules(session("S1"), [sme("A", windows=tight), sme("B", windows=short)]) == {"B": "availability"}


def test_stage_a_overlap_with_draft():
    draft = [{**session("S0", batch="B02"), "session_id": "S0", "sme_id": "A"}]
    r = rules(session("S1"), [sme("A"), sme("B")], draft)
    assert r == {"A": "overlap:S0"}
    # non-overlapping slot is fine
    assert rules(session("S2", start="2026-08-31T05:30:00Z"), [sme("A")], draft) == {}


def test_unfilled_reason_names_training_rule():
    smes = [sme("A", level=1), sme("B", level=2, windows=[]), sme("C", subject="Maths", ss=None)]
    surv, elim = S.stage_a_hard_filter(session("S1", level=2), smes, [])
    assert not surv
    reason = S.unfilled_reason(session("S1", level=2), elim)
    assert reason.startswith("No eligible SME:")
    assert "Name A below required training level 2" in reason and "unavailable" in reason
    assert "Maths" not in reason  # other-subject SMEs are not listed


# ---------------- Stage B ----------------

def test_stage_b_formula_and_order():
    smes = [sme("A", history=weeks(12, ["B01"], rating=4.0)), sme("B", history=weeks(10, [], rating=5.0))]
    hist = S.build_hist([], smes)
    scored = S.stage_b_score(session("S1"), smes, smes, hist, {})
    by = {c["sme_id"]: c for c in scored}
    # loads 36 vs 30 -> fairness A≈0, B=1; continuity A=1; performance .8 / 1.0
    assert by["A"]["score"] == pytest.approx(0.5 * 0 + 0.3 * 1 + 0.2 * 0.8, abs=1e-3)
    assert by["B"]["score"] == pytest.approx(0.5 * 1 + 0 + 0.2 * 1.0, abs=1e-3)
    assert scored[0]["sme_id"] == "B"


def test_stage_b_draft_counts_feed_fairness():
    smes = [sme("A", history=weeks(12)), sme("B", history=weeks(12))]
    hist = S.build_hist([], smes)
    scored = S.stage_b_score(session("S1"), smes, smes, hist, {"A": 1})
    assert scored[0]["sme_id"] == "B" and scored[0]["components"]["fairness"] == 1.0
    assert scored[1]["components"]["fairness"] == pytest.approx(0.0, abs=1e-5)


def test_margin_threshold():
    assert S.is_clear_winner([{"score": 0.70}, {"score": 0.55}])
    assert not S.is_clear_winner([{"score": 0.70}, {"score": 0.56}])
    assert S.is_clear_winner([{"score": 0.1}])


def test_stage_e_adjustments_shift_scores():
    adj = S.stage_e_adjustments([{"session_id": "S1", "batch_id": "B01", "from_sme_id": "A", "to_sme_id": "B"}])
    assert adj == {("A", "B01"): -0.2, ("B", "B01"): 0.1}
    smes = [sme("A", history=weeks(12)), sme("B", history=weeks(12))]
    hist = S.build_hist([], smes)
    base = {c["sme_id"]: c["score"] for c in S.stage_b_score(session("S1"), smes, smes, hist, {})}
    adjusted = {c["sme_id"]: c["score"] for c in S.stage_b_score(session("S1"), smes, smes, hist, {}, adj)}
    assert adjusted["A"] == pytest.approx(base["A"] - 0.2) and adjusted["B"] == pytest.approx(base["B"] + 0.1)


# ---------------- Stage C ----------------

def items_for(session_id="S1", cands=("A", "B")):
    return [{"session_id": session_id, "session": {}, "candidates": [{"sme_id": c, "name": c, "score": 0.5} for c in cands]}]


def test_stage_c_without_key_falls_back(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = stage_c_llm_adjudicate(items_for(), [], llm_call=None)
    assert out["fallback_ids"] == ["S1"] and out["decisions"] == {}


def test_stage_c_openai_compatible_provider(monkeypatch):
    import io
    import engine.llm as L
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1/")
    monkeypatch.setenv("LLM_MODEL", "some-model")
    seen = {}
    def fake_urlopen(req, timeout):
        seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
        seen["body"] = json.loads(req.data)
        content = json.dumps({"decisions": [{"session_id": "S1", "chosen_sme_id": "A", "reason": "ok", "confidence": 1}], "flag_reasons": []})
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    monkeypatch.setattr(L.urllib.request, "urlopen", fake_urlopen)
    assert L.llm_configured() and L.llm_provider() == "openai"
    out = stage_c_llm_adjudicate(items_for(), [], llm_call=None)
    assert out["decisions"]["S1"]["sme_id"] == "A" and out["fallback_ids"] == []
    assert seen["url"] == "https://example.test/v1/chat/completions" and seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "some-model" and seen["body"]["response_format"] == {"type": "json_object"}


def test_openai_daily_quota_429_fails_fast(monkeypatch):
    import io
    import urllib.error
    import engine.llm as L
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "k")
    calls = []
    def fake_urlopen(req, timeout):
        calls.append(1)
        body = io.BytesIO(b'{"error":{"message":"Quota exceeded ... GenerateRequestsPerDayPerProjectPerModel-FreeTier"}}')
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": "49"}, body)
    monkeypatch.setattr(L.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(L.time, "sleep", lambda s: pytest.fail("must not sleep on a per-day quota"))
    out = stage_c_llm_adjudicate(items_for(), [], llm_call=None)
    assert len(calls) == 1 and out["fallback_ids"] == ["S1"]
    assert out["error_kind"] == "daily_quota_exhausted" and "PerDay" in out["error"]
    msg = L.explain(out["error_kind"], "gemini-x", 1)
    assert msg.startswith("LLM daily request limit reached for gemini-x") and "Switch LLM_MODEL" in msg


def test_openai_per_minute_429_retries_then_rate_limited(monkeypatch):
    import io
    import urllib.error
    import engine.llm as L
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "k")
    calls, slept = [], []
    def fake_urlopen(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": "2"},
                                     io.BytesIO(b'{"error":{"message":"GenerateRequestsPerMinute"}}'))
    monkeypatch.setattr(L.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(L.time, "sleep", lambda s: slept.append(s))
    out = stage_c_llm_adjudicate(items_for(), [], llm_call=None)
    assert len(calls) == 2 and slept == [2.0] and out["error_kind"] == "rate_limited"


def test_failover_rescues_chunk_when_primary_quota_exhausted():
    import engine.llm as L
    def primary(payload):
        raise L.LLMQuotaExhausted("daily request quota exhausted for gemini: quota: ...PerDay...")
    def fallback(payload):
        return {"decisions": [{"session_id": it["session_id"], "chosen_sme_id": it["candidates"][0]["sme_id"],
                               "reason": "groq pick", "confidence": 0.8} for it in payload["queued_sessions"]],
                "flag_reasons": []}
    items = items_for("S1") + items_for("S2", cands=("C", "D"))
    out = stage_c_llm_adjudicate(items, [], llm_call=primary, fallback_call=fallback)
    assert out["fallback_ids"] == [] and out["error_kind"] is None
    assert {d["via"] for d in out["decisions"].values()} == {"fallback"}
    assert out["failover"] == {"kind": "daily_quota_exhausted", "resolved": 2, "error_kind": None, "error": None}
    msg = L.explain(out["failover"]["kind"], "gemini", 0, {**out["failover"], "model": "llama"})
    assert msg == "LLM daily request limit reached for gemini. 2 queued row(s) were adjudicated by the fallback provider (llama) instead."


def test_failover_also_limited_falls_back_deterministically():
    import engine.llm as L
    calls = []
    def primary(payload):
        raise L.LLMQuotaExhausted("gemini daily")
    def fallback(payload):
        calls.append(1)
        raise L.LLMRateLimited("groq per-minute")
    n = L.FALLBACK_CHUNK + 3  # one primary chunk (< CHUNK), two fallback sub-chunks
    assert n < L.CHUNK
    items = [items_for(f"S{i}")[0] for i in range(n)]
    out = stage_c_llm_adjudicate(items, [], llm_call=primary, fallback_call=fallback)
    assert len(calls) == 1  # stops hammering a limited fallback after the first sub-chunk
    assert len(out["fallback_ids"]) == n and out["error_kind"] == "daily_quota_exhausted"
    assert out["failover"]["resolved"] == 0 and out["failover"]["error_kind"] == "rate_limited"
    msg = L.explain(out["error_kind"], "gemini", n, {**out["failover"], "model": "llama"})
    assert "Fallback provider (llama) also failed: LLM rate limit hit for llama" in msg
    assert f"{n} queued row(s) were resolved by the deterministic score" in msg and "Switch LLM_MODEL" in msg


def test_malformed_json_env_does_not_crash(monkeypatch, tmp_path):
    import engine.llm as L
    from engine import dotenv
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_EXTRA_BODY", "{reasoning_effort:low}")  # what `sh` makes of the quoted JSON
    assert L.primary_cfg()["extra"] == {}
    # the Python loader keeps quotes intact and never overrides existing vars
    p = tmp_path / ".env"
    p.write_text('# c\nX_JSON={"a":"b"}\nX_QUOTED="v v"\nLLM_API_KEY=other\n\n')
    monkeypatch.delenv("X_JSON", raising=False)
    monkeypatch.delenv("X_QUOTED", raising=False)
    assert dotenv.load(str(p)) == 2
    assert json.loads(os.environ["X_JSON"]) == {"a": "b"} and os.environ["X_QUOTED"] == "v v"
    assert os.environ["LLM_API_KEY"] == "k"
    assert dotenv.load(str(tmp_path / "missing")) == 0


def test_fallback_cfg_reuses_primary_key_when_only_model_set(monkeypatch):
    import engine.llm as L
    for v in ("LLM_FALLBACK_API_KEY", "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    assert L.fallback_cfg() is None
    monkeypatch.setenv("LLM_API_KEY", "gem")
    monkeypatch.setenv("LLM_BASE_URL", "https://g.test/v1beta/openai")
    assert L.fallback_cfg() is None  # model not set → no failover
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "gemini-3.7-flash")
    cfg = L.fallback_cfg()
    assert (cfg["api_key"], cfg["base_url"], cfg["model"]) == ("gem", "https://g.test/v1beta/openai", "gemini-3.7-flash")
    monkeypatch.setenv("LLM_FALLBACK_API_KEY", "gsk")  # separate key → defaults to Groq base
    assert (L.fallback_cfg()["api_key"], L.fallback_cfg()["base_url"]) == ("gsk", L.DEFAULT_OPENAI_BASE_URL)


def test_failover_not_used_for_invalid_picks():
    def primary(payload):
        return {"decisions": [{"session_id": "S1", "chosen_sme_id": "ZZZ", "reason": "x", "confidence": 1}], "flag_reasons": []}
    def fallback(payload):
        pytest.fail("provider_error must not trigger failover")
    out = stage_c_llm_adjudicate(items_for(), [], llm_call=primary, fallback_call=fallback)
    assert out["fallback_ids"] == ["S1"] and out["error_kind"] == "provider_error" and out["failover"] is None


def test_openai_5xx_retries_once_then_provider_unavailable(monkeypatch):
    import io
    import urllib.error
    import engine.llm as L
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.delenv("LLM_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_API_KEY", raising=False)
    calls, slept = [], []
    def fake_urlopen(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {},
                                     io.BytesIO(b'{"error":{"message":"high demand","status":"UNAVAILABLE"}}'))
    monkeypatch.setattr(L.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(L.time, "sleep", lambda s: slept.append(s))
    out = stage_c_llm_adjudicate(items_for(), [], llm_call=None)
    assert len(calls) == 2 and slept == [3]
    assert out["error_kind"] == "provider_unavailable" and out["fallback_ids"] == ["S1"]
    assert "temporarily unavailable" in L.explain(out["error_kind"], "m", 1)


def test_stage_c_error_kind_not_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    out = stage_c_llm_adjudicate(items_for(), [], llm_call=None)
    assert out["error_kind"] == "not_configured"


def test_stage_c_ineligible_pick_retries_once_then_falls_back():
    calls = []
    def fake(payload):
        calls.append(payload)
        return {"decisions": [{"session_id": "S1", "chosen_sme_id": "ZZZ", "reason": "nope", "confidence": 0.9}],
                "flag_reasons": []}
    out = stage_c_llm_adjudicate(items_for(), [], llm_call=fake)
    assert len(calls) == 2 and "note" in calls[1]
    assert out["fallback_ids"] == ["S1"] and out["error"]


def test_stage_c_invalid_json_falls_back():
    def fake(payload):
        raise ValueError("Expecting value: line 1 column 1")
    out = stage_c_llm_adjudicate(items_for(), [], llm_call=fake)
    assert out["fallback_ids"] == ["S1"] and "ValueError" in out["error"]


def test_stage_c_valid_pick_and_flag_reasons():
    def fake(payload):
        return {"decisions": [{"session_id": "S1", "chosen_sme_id": "B", "reason": "B prefers mornings.", "confidence": 0.8}],
                "flag_reasons": [{"session_id": "S9", "code": "UNFILLED", "reason": "Nobody organic is free Wed 2pm."}]}
    out = stage_c_llm_adjudicate(items_for(), [{"session_id": "S9", "code": "UNFILLED", "template_reason": "x"}], llm_call=fake)
    assert out["decisions"]["S1"]["sme_id"] == "B" and out["fallback_ids"] == []
    assert out["reasons"][("S9", "UNFILLED")].startswith("Nobody")


# ---------------- Stage D ----------------

def row_for(sess, sme_id, name="x"):
    return {**sess, "session_id": sess["id"], "sme_id": sme_id, "sme_name": name, "score": 0.5, "stage": "llm", "flags": []}


def test_stage_d_rejects_llm_pick_outside_sub_specialty():
    smes = [sme("A"), sme("B", ss="physical_inorganic")]
    rows = [row_for(session("S1"), "B")]  # injected: LLM "picked" a physical-chem SME for an organic class
    S.stage_d_validate(rows, smes, S.build_hist([], smes))
    assert rows[0]["sme_id"] is None and rows[0]["rejected_sme_id"] == "B"
    assert rows[0]["flags"][0]["code"] == "UNFILLED" and "sub-specialty" in rows[0]["flags"][0]["reason"]


def test_stage_d_rejects_double_booking_with_hard_conflict():
    smes = [sme("A")]
    rows = [row_for(session("S1", batch="B01"), "A"), row_for(session("S2", batch="B02"), "A")]
    S.stage_d_validate(rows, smes, S.build_hist([], smes))
    assert rows[0]["sme_id"] == "A" and rows[1]["sme_id"] is None
    codes = {f["code"] for f in rows[1]["flags"]}
    assert codes == {"HARD_CONFLICT", "UNFILLED"}
    assert "already assigned to S1" in next(f["reason"] for f in rows[1]["flags"] if f["code"] == "HARD_CONFLICT")


def test_stage_d_rejects_training_and_availability():
    smes = [sme("A", level=1), sme("B", windows=[])]
    rows = [row_for(session("S1", level=2), "A"), row_for(session("S2", batch="B02", start="2026-08-31T06:30:00Z"), "B")]
    S.stage_d_validate(rows, smes, S.build_hist([], smes))
    assert rows[0]["sme_id"] is None and "training level" in rows[0]["flags"][0]["reason"]
    assert rows[1]["sme_id"] is None and "availability" in rows[1]["flags"][0]["reason"]


def test_stage_d_fairness_band_flags_but_keeps_assignment():
    smes = [sme("A", history=weeks(18)), sme("B", history=weeks(12)), sme("C", history=weeks(12))]
    rows = [row_for(session("S1"), "A")]
    S.stage_d_validate(rows, smes, S.build_hist([], smes))
    assert rows[0]["sme_id"] == "A"
    f = rows[0]["flags"][0]
    assert f["code"] == "FAIRNESS_VIOLATION" and "Name A at 55 sessions over 4 weeks vs. pool mean 42.3." == f["reason"]



# ---------------- multi-course / multi-topic SMEs ----------------

def multi(id, subjects, topics, level=2, windows=FULL):
    return {"id": id, "name": f"Name {id}", "subject": subjects[0], "subjects": subjects,
            "sub_specialty": None, "topics": topics, "training_level": level,
            "timezone": "Asia/Kolkata", "weekly_availability": windows, "preference_notes": "", "history": []}


def test_multi_course_sme_passes_both_subjects():
    both = multi("X", ["PM", "DSA"], ["Behavioral & Leadership", "Arrays & Strings"])
    only = multi("Y", ["PM"], ["Product Sense"])
    dsa = session("S1", subject="DSA", ss="Arrays & Strings")
    pm = session("S2", subject="PM", ss="Behavioral & Leadership")
    assert rules(dsa, [both, only]) == {"Y": "subject"}
    assert rules(pm, [both, only]) == {"Y": "sub_specialty"}
    assert S.sme_subjects(both) == ["PM", "DSA"] and S.sme_topics(both)[0] == "Behavioral & Leadership"


def test_topicless_session_accepts_any_sme_of_the_course():
    a = multi("A", ["AI"], ["RAG & Retrieval"])
    b = multi("B", ["AI"], ["Agentic Patterns"])
    c = multi("C", ["ML"], ["MLOps"])
    doubt = session("S1", subject="AI", ss=None, typ="doubt")
    surv, _ = S.stage_a_hard_filter(doubt, [a, b, c], [])
    assert {s["id"] for s in surv} == {"A", "B"}


def test_sme_without_topics_is_a_generalist():
    gen = {**multi("G", ["DSA"], []), "topics": []}
    assert S.carries_topic(gen, "Anything") and S.carries_topic(gen, None)
    assert rules(session("S1", subject="DSA", ss="Graphs & Trees"), [gen]) == {}


def test_stage_d_rejects_topic_the_sme_does_not_carry():
    a = multi("A", ["AI"], ["RAG & Retrieval"])
    rows = [row_for(session("S1", subject="AI", ss="Agentic Patterns"), "A")]
    S.stage_d_validate(rows, [a], S.build_hist([], [a]))
    assert rows[0]["sme_id"] is None and "sub-specialty" in rows[0]["flags"][0]["reason"]


def test_subject_pool_and_fairness_use_every_course_of_a_multi_course_sme():
    shared = multi("X", ["PM", "DSA"], ["Product Sense", "Arrays & Strings"], level=3)
    pm_only = multi("Y", ["PM"], ["Product Sense"], level=3)
    smes = [shared, pm_only]
    assert {s["id"] for s in S.subject_pool(smes, "PM")} == {"X", "Y"}
    assert {s["id"] for s in S.subject_pool(smes, "DSA")} == {"X"}


# ---------------- llm_enabled switch ----------------

def test_llm_disabled_skips_stage_c_without_fallback_flags():
    smes = [sme("A", history=weeks(12)), sme("B", history=weeks(12))]  # identical -> a tie
    sessions = [session("S1")]
    res = run_pipeline(sessions, smes, [], [], llm_enabled=False)
    row = res["draft"][0]
    assert row["sme_id"] and row["stage"] == "auto" and row["flags"] == []
    assert res["stats"]["llm"]["skipped"] is True and res["stats"]["llm"]["queued"] == 1
    assert res["stats"]["llm"]["error_kind"] is None and res["stats"]["llm"]["message"] is None
    # the same run with Stage C on (no key) marks the fallback
    on = run_pipeline(sessions, smes, [], [], llm_enabled=True)
    assert [f["code"] for f in on["draft"][0]["flags"]] == ["LLM_FALLBACK"]


# ---------------- Full runs on the IK seed data ----------------

def assert_no_hard_rule_violations(draft, smes):
    by = {s["id"]: s for s in smes}
    per_sme = {}
    for r in draft:
        if not r["sme_id"]:
            continue
        s = by[r["sme_id"]]
        assert S.teaches_subject(s, r["subject"]), r["session_id"]
        assert S.carries_topic(s, r["sub_specialty"]), r["session_id"]
        assert s["training_level"] >= r["required_training_level"], r["session_id"]
        start, end = S.session_span(r)
        assert S.is_available(s, start, end), r["session_id"]
        for other in per_sme.setdefault(s["id"], []):
            assert not S.overlaps(other, r), (r["session_id"], other["session_id"])
        per_sme[s["id"]].append(r)


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def seed(monkeypatch_module):
    monkeypatch_module.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch_module.delenv("LLM_API_KEY", raising=False)
    smes, history = rd("smes"), rd("history")
    nxt = run_pipeline(rd("sessions_next"), smes, history, [])
    return nxt, rd("sessions_next"), smes, history


def test_seed_shape_and_no_violations(seed):
    res, sessions, smes, _ = seed
    assert len(res["draft"]) == len(sessions) == 41
    assert len({r["session_id"] for r in res["draft"]}) == 41
    assert_no_hard_rule_violations(res["draft"], smes)
    assert res["stats"]["assigned"] + res["stats"]["unfilled"] == 41
    assert set(res["stats"]["fairness_spread_per_subject"]) == {"DSA", "ML", "AI", "PM"}
    for r in res["draft"]:
        assert [f["priority"] for f in r["flags"]] == sorted(f["priority"] for f in r["flags"])
        assert all(f["reason"] for f in r["flags"])


def test_seed_current_week_is_fully_staffed():
    smes, history = rd("smes_current"), rd("history")
    res = run_pipeline(rd("sessions_current"), smes, history, [], llm_enabled=False)
    assert res["stats"]["unfilled"] == 0 and res["stats"]["total_sessions"] == 41
    assert not any(f["code"] in ("UNFILLED", "HARD_CONFLICT", "LLM_FALLBACK") for f in res["flags"])
    assert_no_hard_rule_violations(res["draft"], smes)


def test_seed_override_candidates_are_all_assignable(seed):
    """The class sheet offers `candidates` as the override choices, so anyone listed must really be
    assignable: right course and topic, high enough level, free at that hour, and not already
    teaching something else then."""
    res, _, smes, _ = seed
    by = {s["id"]: s for s in smes}
    live = [r for r in res["draft"] if r["sme_id"]]
    for r in res["draft"]:
        start, end = S.session_span(r)
        for c in r["candidates"]:
            s = by[c["sme_id"]]
            who = (r["session_id"], c["sme_id"])
            assert S.teaches_subject(s, r["subject"]), who
            assert S.carries_topic(s, r["sub_specialty"]), who
            assert s["training_level"] >= r["required_training_level"], who
            assert S.is_available(s, start, end), who
            clash = [o["session_id"] for o in live
                     if o["sme_id"] == c["sme_id"] and o["session_id"] != r["session_id"] and S.overlaps(o, r)]
            assert not clash, (who, clash)


def test_seed_session_ids_unique_across_weeks():
    """The UI keys approvals, diffs, decisions and pending changes by session id alone, so an id
    shared by both weeks would leak state between them (a settled week marking a draft approved)."""
    cur, nxt = {s["id"] for s in rd("sessions_current")}, {s["id"] for s in rd("sessions_next")}
    assert len(cur) == len(rd("sessions_current")) and len(nxt) == len(rd("sessions_next"))
    assert not (cur & nxt), sorted(cur & nxt)[:5]


def test_seed_edge_cases(seed):
    res, _, smes, _ = seed
    rows = {r["session_id"]: r for r in res["draft"]}
    N = lambda batch_id, k: f"W37-{batch_id}-{k}"  # noqa: E731 — next week's id namespace
    # E1 — concurrent ML System Design classes: T07 takes one, nobody is left for the other
    assert rows[N("ML-01", 0)]["sme_id"] == "T07"
    e1 = rows[N("ML-02", 0)]
    assert not e1["sme_id"] and "unavailable" in e1["flags"][0]["reason"]
    assert N("ML-01", 0) in e1["flags"][0]["reason"]       # names the overlapping session
    # E2 — advanced Saturday DP class: the only free carrier is a beginner
    e2 = rows[N("DSA-01", 1)]
    assert not e2["sme_id"] and "training level" in e2["flags"][0]["reason"]
    assert "Vikram Rao" in e2["flags"][0]["reason"]
    # E3 — two concurrent Arrays classes cannot share one SME
    a, b = rows[N("DSA-02", 0)], rows[N("DSA-04", 0)]
    assert a["sme_id"] and b["sme_id"] and a["sme_id"] != b["sme_id"]
    assert any(e["rule"].startswith("overlap:") for e in a["eliminated"] + b["eliminated"])
    # E4 — with no key every queued row carries LLM_FALLBACK and its fixed reason
    ties = [r for r in rows.values() if any(f["code"] == "LLM_FALLBACK" for f in r["flags"])]
    assert len(ties) >= 5 and res["stats"]["llm_resolved"] == 0
    assert all(f["reason"] == S.LLM_FALLBACK_REASON for r in ties for f in r["flags"] if f["code"] == "LLM_FALLBACK")
    # E5 — the overloaded SME is forced onto one slot and flagged, but diverted overall
    e5 = rows[N("DSA-01", 0)]
    assert e5["sme_id"] == "T01" and any(f["code"] == "FAIRNESS_VIOLATION" for f in e5["flags"])
    dsa = {s["id"]: sum(1 for r in rows.values() if r["sme_id"] == s["id"])
           for s in smes if S.teaches_subject(s, "DSA")}
    assert dsa["T01"] < max(dsa.values())
    # E6 — non-IST availability converted correctly
    by = {s["id"]: s for s in smes}
    assert by["T09"]["timezone"] == "America/Los_Angeles" and by["T09"]["weekly_availability"][0]["start_utc"] == "01:00"
    assert by["T11"]["timezone"] == "Europe/London" and by["T11"]["weekly_availability"][0]["start_utc"] == "08:00"
    assert by["T16"]["timezone"] == "Asia/Dubai" and by["T16"]["weekly_availability"][0]["start_utc"] == "06:00"
    assert {sid for sid, r in rows.items() if not r["sme_id"]} == {N("ML-02", 0), N("DSA-01", 1)}


def test_seed_with_fake_llm_marks_tie_escalated(seed):
    _, sessions, smes, history = seed
    def fake(payload):
        return {"decisions": [{"session_id": it["session_id"], "chosen_sme_id": it["candidates"][-1]["sme_id"],
                               "reason": f"Picked {it['candidates'][-1]['name']} for balance.", "confidence": 0.7}
                              for it in payload["queued_sessions"]],
                "flag_reasons": []}
    res = run_pipeline(sessions, smes, history, [], llm_call=fake)
    assert_no_hard_rule_violations(res["draft"], smes)
    codes = res["stats"]["flags_by_code"]
    assert res["stats"]["llm_resolved"] >= 5 and res["stats"]["llm_resolved"] == codes["TIE_ESCALATED"]
    assert res["stats"]["llm_resolved"] + codes.get("LLM_FALLBACK", 0) == res["stats"]["llm"]["queued"]
    assert res["stats"]["unfilled"] == 2


def test_seed_override_then_rerun_changes_and_labels_row(seed):
    res, sessions, smes, history = seed
    row = next(r for r in res["draft"] if r["stage"] == "auto" and len(r["candidates"]) >= 2
               and r["candidates"][1]["sme_id"] != r["sme_id"])
    target = row["candidates"][1]["sme_id"]
    overrides = [{"session_id": row["session_id"], "batch_id": row["batch_id"],
                  "from_sme_id": row["sme_id"], "to_sme_id": target}]
    res2 = run_pipeline(sessions, smes, history, overrides)
    row2 = next(r for r in res2["draft"] if r["session_id"] == row["session_id"])
    assert row2["adjusted_from_override"] is True
    assert row2["sme_id"] != row["sme_id"] or row2["score"] != row["score"]
    assert_no_hard_rule_violations(res2["draft"], smes)


def test_seed_dropout_rerun_produces_changes(seed):
    res, sessions, smes, history = seed
    busiest = max({s["id"] for s in smes},
                  key=lambda i: sum(1 for r in res["draft"] if r["sme_id"] == i))
    without = [{**s, "weekly_availability": []} if s["id"] == busiest else s for s in smes]
    res2 = run_pipeline(sessions, without, history, [])
    assert not any(r["sme_id"] == busiest for r in res2["draft"])
    before = {r["session_id"]: r["sme_id"] for r in res["draft"]}
    changed = [r for r in res2["draft"] if before[r["session_id"]] != r["sme_id"]]
    assert changed, "dropping the busiest SME must change the draft"
    assert_no_hard_rule_violations(res2["draft"], without)


def test_seed_approvals_override_risk_and_export_columns(seed):
    res, *_ = seed
    row = next(r for r in res["draft"] if r["sme_id"] and r["candidates"])
    bad = next(e for e in row["eliminated"] if e["rule"] == "subject")
    other = next(r for r in res["draft"] if r["session_id"] != row["session_id"] and r["sme_id"])
    out = apply_approvals(res["draft"], [
        {"session_id": row["session_id"], "action": "override", "override_sme_id": bad["sme_id"]},
        {"session_id": other["session_id"], "action": "approve"},
    ])
    final = {r["session_id"]: r for r in out["final_schedule"]}
    risk = [f for f in final[row["session_id"]]["flags"] if f["code"] == "RULE_OVERRIDE_RISK"]
    assert risk and risk[0]["reason"] == f"Override assigns {bad['name']} outside subject expertise."
    assert final[row["session_id"]]["status"] == "overridden"
    assert final[other["session_id"]]["status"] == "approved"
    assert out["override_log"][0]["to_sme_id"] == bad["sme_id"]
    assert list(out["export_rows"][0]) == ["week", "date", "time_ist", "batch", "subject", "sub_specialty",
                                            "session_type", "sme_name", "status", "flags"]
    assert out["export_rows"][0]["week"] == "2026-W37"       # next week


def _api():
    """The FastAPI module, imported without letting its dotenv.load leak real credentials into the
    rest of the suite (test_malformed_json_env_does_not_crash asserts on dotenv behaviour)."""
    from engine import dotenv as _dotenv
    real, _dotenv.load = _dotenv.load, lambda path: 0
    os.environ["DATABASE_URL"] = ""
    os.environ.setdefault("IK_DB_PATH", "/tmp/ik-engine-test.db")
    try:
        from api import index
        return index
    finally:
        _dotenv.load = real


def test_unknown_subject_returns_unfilled_not_crash():
    """A session for a subject nobody in the pool teaches is the limit form of "no qualified SME".
    Stage A always caught it; the final candidate-recompute loop then crashed on an empty subject pool."""
    smes = rd("smes")
    sess = dict(rd("sessions_next")[0], id="X1", subject="QUANTUM", sub_specialty=None)

    # the scorer itself: an empty pool is no candidates, not a ValueError
    assert S.stage_b_score(sess, [], smes, S.build_hist([], smes), {}) == []

    res = run_pipeline([sess], smes, [], [], llm_enabled=False)
    row = res["draft"][0]
    assert row["sme_id"] is None and row["candidates"] == []
    unfilled = [f for f in row["flags"] if f["code"] == "UNFILLED"]
    assert len(unfilled) == 1 and "no QUANTUM SMEs in the pool" in unfilled[0]["reason"]
    assert res["stats"]["unfilled"] == 1

    # and the route that used to 500 answers normally
    out = _api().run({"sessions": [sess], "smes": smes, "history": [], "overrides": [], "llm": False})
    assert out["stats"]["unfilled"] == 1
    assert out["draft"][0]["flags"][0]["code"] == "UNFILLED"


# ---------------- calendar availability (external busy blocks) ----------------

def busy(start="2026-08-31T04:00:00Z", end="2026-08-31T05:00:00Z"):
    return {"start_utc": start, "end_utc": end}


def test_stage_a_eliminates_a_teacher_whose_calendar_is_busy():
    """A synced busy block is an additive hard rule, separate from the declared working pattern."""
    free, booked = sme("A"), {**sme("B"), "external_busy": [busy()]}
    surv, elim = S.stage_a_hard_filter(session("S1"), [free, booked], [])
    assert [s["id"] for s in surv] == ["A"]
    assert {e["sme_id"]: e["rule"] for e in elim} == {"B": "calendar_busy"}
    assert S.rule_label("calendar_busy") == "calendar conflict"


def test_a_block_outside_the_session_does_not_eliminate():
    early = {**sme("B"), "external_busy": [busy("2026-08-31T02:00:00Z", "2026-08-31T03:00:00Z")]}
    surv, _ = S.stage_a_hard_filter(session("S1"), [early], [])          # session is 04:30–05:30Z
    assert [s["id"] for s in surv] == ["B"]


def test_a_malformed_block_is_ignored_rather_than_eliminating_everyone():
    junk = {**sme("B"), "external_busy": [{"start_utc": "not-a-time", "end_utc": None}, {}]}
    surv, _ = S.stage_a_hard_filter(session("S1"), [junk], [])
    assert [s["id"] for s in surv] == ["B"]


def test_unfilled_reason_names_the_calendar_conflict():
    booked = {**sme("B"), "external_busy": [busy()]}
    sess = session("S1")
    _, elim = S.stage_a_hard_filter(sess, [booked], [])
    reason = S.unfilled_reason(sess, elim)
    assert "Name B has a calendar conflict at Mon 10:00 IST" in reason


def test_stage_d_rejects_an_assignment_a_later_sync_made_conflicting():
    """An LLM pick or an ops override must not survive a calendar conflict silently."""
    smes = [sme("A")]
    rows = run_pipeline([session("S1")], smes, [], [], llm_enabled=False)["draft"]
    assert rows[0]["sme_id"] == "A"                                   # staffed before the sync

    synced = [{**smes[0], "external_busy": [busy()]}]                # then the calendar says otherwise
    rows[0]["flags"] = []
    S.stage_d_validate(rows, synced, S.build_hist([], synced))
    assert rows[0]["sme_id"] is None and rows[0]["rejected_sme_id"] == "A"
    unfilled = next(f for f in rows[0]["flags"] if f["code"] == "UNFILLED")
    assert "calendar conflict" in unfilled["reason"]


def test_sync_availability_degrades_to_simulated_without_credentials(monkeypatch):
    import engine.channels as C
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_JSON", raising=False)
    roster, res = C.sync_availability([{**sme("A"), "email": "a@ik.example"}], "2026-08-31T00:00:00Z",
                                      "2026-09-06T00:00:00Z")
    assert res["status"] == "simulated" and res["live"] is False and res["count"] == 0
    assert roster[0]["external_busy"] == []          # nothing synced, and it says so rather than "free"


def test_sync_availability_fills_external_busy_from_one_freebusy_call(monkeypatch):
    import engine.channels as C
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", '{"type":"service_account"}')
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "cohort@group.calendar.google.com")
    monkeypatch.delenv("PUBLISH_DISABLED", raising=False)
    seen = {}

    def api(bodyDict):
        seen.update(bodyDict)
        return {"calendars": {
            "a@ik.example": {"busy": [{"start": "2026-08-31T04:00:00Z", "end": "2026-08-31T05:00:00Z"}]},
            "b@ik.example": {"busy": []},
        }}
    smes = [{**sme("A"), "email": "a@ik.example"}, {**sme("B"), "email": "b@ik.example"},
            {**sme("C"), "email": None}]
    roster, res = C.sync_availability(smes, "2026-08-31T00:00:00Z", "2026-09-06T00:00:00Z", api=api)
    assert res["status"] == "sent" and res["live"] is True and res["count"] == 1
    assert res["per_sme"] == {"A": 1, "B": 0, "C": 0}
    assert [{"id": "a@ik.example"}, {"id": "b@ik.example"}] == seen["items"]   # one call, no address twice
    assert roster[0]["external_busy"] == [busy()]
    # and the synced roster changes what Stage A allows
    surv, _ = S.stage_a_hard_filter(session("S1"), roster, [])
    assert "A" not in [s["id"] for s in surv] and "B" in [s["id"] for s in surv]


# ---------------- is chronological greedy leaving rows on the table? ----------------

def _max_matching(sessions, smes):
    """Most sessions any assignment could fill, ignoring the order they are considered in.

    Eligibility is Stage A against an *empty* draft, so the only coupling left is that one SME
    cannot teach two sessions at once. Every seed session is 60 minutes starting on the half hour,
    so two sessions overlap exactly when they share a start — which makes (sme, start) the resource
    being matched, and plain augmenting paths exact rather than a heuristic. The precondition is
    asserted, so a future data change fails the test instead of quietly proving a weaker claim.
    """
    spans = {s["id"]: S.session_span(s) for s in sessions}
    assert {int(s["duration_min"]) for s in sessions} == {60}, "reduction assumes one session length"
    assert {S.parse_utc(s["start_utc"]).minute for s in sessions} == {30}, "reduction assumes aligned starts"
    for a in sessions:                       # and therefore: overlap iff identical start
        for b in sessions:
            if a["id"] < b["id"]:
                assert S.overlaps(a, b) == (spans[a["id"]][0] == spans[b["id"]][0])

    eligible = {s["id"]: [m["id"] for m in S.stage_a_hard_filter(s, smes, [])[0]] for s in sessions}
    by_id = {s["id"]: s for s in sessions}
    match: dict[tuple[str, object], str] = {}

    def assign(sid, seen):
        for sme_id in eligible[sid]:
            key = (sme_id, spans[sid][0])
            if key not in match:
                match[key] = sid
                return True
            if key not in seen:
                seen.add(key)
                if assign(match[key], seen):
                    match[key] = sid
                    return True
        return False

    filled = sum(1 for s in sessions if assign(s["id"], set()))
    return filled, eligible, by_id


def test_greedy_is_matching_optimal():
    """The sharpest question this design invites is "why not an optimiser instead of chronological
    greedy?". On this week the answer is provable: a maximum matching fills no more sessions."""
    sessions, smes, history = rd("sessions_next"), rd("smes"), rd("history")
    res = run_pipeline(sessions, smes, history, [], llm_enabled=False)
    greedy_filled = sum(1 for r in res["draft"] if r["sme_id"])

    optimal, eligible, by_id = _max_matching(sessions, smes)
    assert optimal <= greedy_filled, (
        f"a maximum matching fills {optimal} of {len(sessions)} but the pipeline fills {greedy_filled} — "
        "greedy is leaving rows on the table and the design comment is wrong")
    assert greedy_filled == len(sessions) - 2 == 39

    # and the two it cannot fill are genuinely unfillable, not an artefact of the order
    unfilled = sorted(r["session_id"] for r in res["draft"] if not r["sme_id"])
    assert unfilled == ["W37-DSA-01-1", "W37-ML-02-0"]
    assert eligible["W37-DSA-01-1"] == [], "nobody at all can teach advanced DP on Saturday afternoon"
    assert eligible["W37-ML-02-0"] == ["T07"], "exactly one eligible SME"
    # ...who is also the only one eligible for the session running at the same time
    assert eligible["W37-ML-01-0"] == ["T07"]
    assert S.overlaps(by_id["W37-ML-02-0"], by_id["W37-ML-01-0"])


def test_an_override_marks_every_row_it_re_scored_including_other_subjects():
    """One override moved six rows and only one carried an explanation. Two of those six are PM rows:
    Rahul Desai carries PM and DSA, so loading him with a DSA class re-normalises the PM pool's
    fairness. That coupling is correct, and it used to read as random churn."""
    sessions, smes, history = rd("sessions_next"), rd("smes"), rd("history")
    ov = [{"session_id": "W37-DSA-04-1", "batch_id": "DSA-04", "from_sme_id": "T03", "to_sme_id": "T14"}]
    before = {r["session_id"]: r for r in run_pipeline(sessions, smes, history, [], llm_enabled=False)["draft"]}
    after = run_pipeline(sessions, smes, history, ov, llm_enabled=False)["draft"]

    changed = [r for r in after if before[r["session_id"]]["sme_id"] != r["sme_id"]]
    assert len(changed) == 6, [r["session_id"] for r in changed]
    assert all(r["adjusted_from_override"] for r in changed), \
        [r["session_id"] for r in changed if not r["adjusted_from_override"]]

    # the row ops actually touched says so directly...
    direct = next(r for r in after if r["session_id"] == "W37-DSA-04-1")
    assert direct["override_effect"]["kind"] == "direct"
    assert "Rahul Desai" in direct["override_effect"]["smes"]

    # ...and the PM rows explain themselves as a ripple, naming who moved
    pm = [r for r in changed if r["subject"] == "PM"]
    assert len(pm) == 2
    for r in pm:
        assert r["override_effect"] == {"kind": "ripple", "smes": ["Rahul Desai"]}

    # a subject the override cannot reach is left alone
    untouched = [r for r in after if r["subject"] not in ("DSA", "PM")]
    assert untouched and all(r["override_effect"] is None for r in untouched)


def test_the_assigned_score_and_the_candidate_list_are_on_stated_scales(seed):
    """The UI showed Kavya Nair assigned at 0.684 while the list had her second at 0.6126 and Rahul
    top at 0.6363 — the number-two candidate at a score matching neither entry. The two are different
    snapshots, so the row now carries both and the UI names which is which."""
    res, *_ = seed
    row = next(r for r in res["draft"] if r["session_id"] == "W37-DSA-04-1")
    mine = next(c for c in row["candidates"] if c["sme_id"] == row["sme_id"])
    assert row["score_now"] == mine["score"], "the row's 'now' score must agree with its own list entry"
    assert row["score"] != row["score_now"], "and the assign-time score is kept, not overwritten"

    # every staffed row agrees with its own entry, so the contradiction cannot come back anywhere
    for r in res["draft"]:
        if not r["sme_id"]:
            assert r["score_now"] is None
            continue
        entry = [c["score"] for c in r["candidates"] if c["sme_id"] == r["sme_id"]]
        assert entry == [r["score_now"]], r["session_id"]

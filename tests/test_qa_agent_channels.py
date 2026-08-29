"""QA pass: the copilot's guardrails, and every outbound channel under injected failure.

The agent half asserts the three things that make it safe to put in front of ops — budgets enforced
server-side, provenance on every proposed move, and no returned plan that breaks a hard rule — plus the
grounding of what it says. The channel half fires HTTP failures at each sender through the `api=`
injection point the codebase already provides.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import agent as A  # noqa: E402
from engine import channels as C  # noqa: E402
from engine import tools as T  # noqa: E402
from engine.run import run_pipeline  # noqa: E402
from engine.store import Store  # noqa: E402
from tests.test_engine import rd  # noqa: E402
from tests.test_invariants import assert_no_hard_rule_violation  # noqa: E402
from tests.test_qa_llm import ungrounded  # noqa: E402

ROW = {"session_id": "W37-DSA-01-0", "batch_id": "DSA-01", "subject": "DSA", "sub_specialty": "Arrays",
       "type": "class", "start_utc": "2026-09-07T04:30:00Z", "duration_min": 60,
       "sme_id": "T03", "sme_name": "Kavya Nair", "flags": []}


@pytest.fixture(scope="module")
def ctx():
    sessions, smes, history = rd("sessions_next"), rd("smes"), rd("history")
    draft = run_pipeline(sessions, smes, history, [], llm_enabled=False)["draft"]
    return T.make_ctx("2026-W37", draft, smes, history)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", '{"type":"service_account"}')
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "cohort@group.calendar.google.com")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("MAIL_FROM", "ops@ik.test")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM", "+10000000000")
    monkeypatch.delenv("PUBLISH_DISABLED", raising=False)


@pytest.fixture
def db(tmp_path):
    return Store(url=None, path=str(tmp_path / "qa.db"))


def scripted(*steps):
    it = iter(steps)
    seen = []

    def call(system, messages):
        seen.append(messages[0]["content"])
        return next(it)
    call.seen = seen
    return call


# ---------------- agent budgets ----------------

def test_budgets_are_server_side_and_the_model_cannot_raise_them(ctx):
    """A model that asks for more turns gets the same ceiling: the limits live in the loop."""
    # vary the args: identical repeats are rejected by the duplicate-call guard before the budget
    greedy = scripted(*[{"thought": "I need 50 more tool calls",
                         "action": {"tool": "get_sme", "args": {"sme_id": f"T{i:02d}"}}} for i in range(1, 30)])
    out = A.run_agent(ctx, "review", question="who is overloaded?", llm_call=greedy)
    assert out["status"] == "budget_exhausted"
    assert out["meta"]["llm_turns"] <= A.MAX_LLM_TURNS
    assert out["meta"]["tool_calls"] <= A.MAX_TOOL_CALLS
    assert out["answer"] and out["plan"] is None, "partial findings, not an error page"
    assert "What it did establish" in out["answer"]


def test_the_wall_clock_is_enforced(ctx):
    ticks = iter([0, 0, 999, 999, 999, 999])
    out = A.run_agent(ctx, "review", question="anything?",
                      llm_call=scripted({"thought": "x", "action": {"tool": "get_draft_summary", "args": {}}}),
                      clock=lambda: next(ticks))
    assert out["status"] == "budget_exhausted"


def test_the_final_turn_nudge_lets_a_normal_question_answer(ctx):
    """Without it, a question needing five lookups burned every turn and came back budget_exhausted."""
    prompts = []

    def spy(system, messages):
        prompts.append(messages[0]["content"])
        if len(prompts) < A.MAX_LLM_TURNS:
            return {"thought": "looking", "action": {"tool": "get_sme", "args": {"sme_id": "T0%d" % len(prompts)}}}
        return {"thought": "done", "final": {"answer": "Arjun Sharma has the heaviest week.", "plan": None}}
    out = A.run_agent(ctx, "review", question="who is overloaded?", llm_call=spy)
    assert out["status"] == "ok"
    assert "LAST step" in prompts[-1] and "LAST step" not in prompts[0]
    assert "start a new session" not in prompts[-1].lower() or "NEVER tell them" in prompts[-1]


def test_the_model_is_never_told_the_budget_is_per_session(ctx):
    prompts = []

    def spy(system, messages):
        prompts.append(system + "\n" + messages[0]["content"])
        return {"thought": "d", "final": {"answer": "ok", "plan": None}}
    A.run_agent(ctx, "chat", question="hi", llm_call=spy)
    joined = " ".join(prompts).lower()
    assert "per session" not in joined
    assert "budget is per message" in " ".join(prompts) or "at most 8 tool calls" in " ".join(prompts)


# ---------------- agent safety invariants ----------------

def test_no_returned_plan_ever_breaks_a_hard_rule(ctx):
    """Every scenario, including hostile ones, re-simulated after the fact."""
    row = next(r for r in ctx["draft"] if r["sme_id"] and r["candidates"])
    blocked = next((e for e in row["eliminated"] if e["rule"] != "subject"), None)
    scenarios = [
        [{"thought": "d", "final": {"answer": "made up", "plan": [
            {"session_id": row["session_id"], "from_sme": row["sme_id"], "to_sme": "T99", "reason": "x"}]}}],
        [{"thought": "d", "final": {"answer": "blocked pick", "plan": [
            {"session_id": row["session_id"], "from_sme": row["sme_id"],
             "to_sme": (blocked or {"sme_id": "T01"})["sme_id"], "reason": "x"}]}}],
        [{"thought": "d", "final": {"answer": "junk", "plan": ["not a move", 42, None]}}],
        [{"thought": "d", "final": {"answer": "self", "plan": [
            {"session_id": row["session_id"], "from_sme": row["sme_id"], "to_sme": row["sme_id"], "reason": "x"}]}}],
    ]
    for steps in scenarios:
        out = A.run_agent(ctx, "chat", question="fix it", llm_call=scripted(*steps))
        assert out["status"] in ("ok", "fallback")
        if out["plan"]:
            sim = T.simulate_plan(ctx, out["plan"])
            assert sim["all_ok"], f"{steps}: {sim['verdicts']}"


def test_provenance_holds_for_every_move_in_every_returned_plan(ctx):
    """A move may only name a teacher the run was actually shown."""
    row = next(r for r in ctx["draft"] if r["sme_id"] and r["candidates"])
    unseen = next(s["id"] for s in ctx["smes"]
                  if s["id"] not in {c["sme_id"] for c in row["candidates"]} and s["id"] != row["sme_id"])
    out = A.run_agent(ctx, "chat", question="reassign it", llm_call=scripted(
        {"thought": "d", "final": {"answer": "take it", "plan": [
            {"session_id": row["session_id"], "from_sme": row["sme_id"], "to_sme": unseen, "reason": "x"}]}}))
    assert out["plan"] is None
    assert "never appeared as a candidate" in out["answer"]

    shown = A.run_agent(ctx, "chat", question="reassign it", llm_call=scripted(
        {"thought": "look", "action": {"tool": "get_candidates", "args": {"session_id": row["session_id"]}}},
        {"thought": "d", "final": {"answer": "take it", "plan": [
            {"session_id": row["session_id"], "from_sme": row["sme_id"],
             "to_sme": row["candidates"][0]["sme_id"], "reason": "x"}]}}))
    assert shown["plan"] and shown["plan"][0]["to_sme"] == row["candidates"][0]["sme_id"]


@pytest.mark.parametrize("bad", [
    {"thought": "x", "action": {"tool": "teleport", "args": {}}},
    {"thought": "x", "action": {"tool": "get_row", "args": {"nope": 1}}},
    {"thought": "x", "action": {"tool": "get_row", "args": "not an object"}},
    {"thought": "x", "action": {"tool": "simulate_plan", "args": {"plan": "not a list"}}},
])
def test_a_broken_tool_call_retries_once_then_takes_the_labelled_floor(ctx, bad):
    out = A.run_agent(ctx, "recovery", "T14", ["Wed"], llm_call=scripted(bad, bad))
    assert out["status"] == "fallback"
    assert out["plan"] is None or all(m.get("flag") == "AGENT_FALLBACK" for m in out["plan"])
    assert "fallback" in out["answer"].lower() or "could not finish" in out["answer"]


def test_report_unavailable_binds_for_the_rest_of_the_run(ctx):
    """After a drop-out is reported, no later move may hand work back to that teacher."""
    held = next(r for r in ctx["draft"] if r["sme_id"] == "T14")
    out = A.run_agent(ctx, "chat", question="Rahul is out, can he keep his class?", llm_call=scripted(
        {"thought": "note it", "action": {"tool": "report_unavailable", "args": {"sme_id": "T14"}}},
        {"thought": "check", "action": {"tool": "simulate_plan", "args": {"plan": [
            {"session_id": held["session_id"], "from_sme": "T14", "to_sme": "T14", "reason": "keep"}]}}},
        {"thought": "d", "final": {"answer": "No, he is out.", "plan": None}}))
    assert "breaks:unavailable" in out["transcript"][1]["result_digest"]
    assert out["plan"] is None


def test_modes_carry_state_only_where_they_should(ctx):
    """Chat is multi-turn; recovery and review are single shots.

    FINDING (QA-09): the loop replays `turns` for *any* mode, so review would carry a conversation if a
    caller passed one. Harmless today because the frontend only sends turns for chat and the API does
    not synthesise them, but the mode contract is not actually enforced. Asserting current behaviour so
    the report and the suite agree.
    """
    prompts = []

    def spy(system, messages):
        prompts.append(messages[0]["content"])
        return {"thought": "d", "final": {"answer": "fine", "plan": None}}
    A.run_agent(ctx, "chat", question="and then?", turns=[{"role": "user", "content": "earlier question"}],
                llm_call=spy)
    assert "earlier question" in prompts[-1], "chat carries the conversation"

    prompts.clear()
    A.run_agent(ctx, "review", question="one-off", llm_call=spy)
    assert "earlier question" not in prompts[-1], "review with no turns is a single shot"
    assert "Conversation so far" not in prompts[-1]

    prompts.clear()
    A.run_agent(ctx, "review", question="one-off", turns=[{"role": "user", "content": "earlier question"}],
                llm_call=spy)
    assert "earlier question" in prompts[-1], "QA-09: turns are replayed even in review mode"


def test_without_a_key_the_copilot_labels_itself_fallback(ctx, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "")
    out = A.run_agent(ctx, "review", question="what is unfilled?")
    assert out["status"] == "fallback"
    assert "could not finish" in out["answer"]
    assert out["meta"]["error"], "the cause is recorded for ops"


def test_everything_the_agent_says_is_grounded(ctx):
    """The floor answer is engine-generated, so it must name only real rows — this is the path a
    reviewer sees when no key is configured."""
    out = A.run_agent(ctx, "review", question="what is unfilled?",
                      llm_call=lambda s, m: (_ for _ in ()).throw(A.LLMError("down")))
    assert ungrounded(out["answer"], ctx["smes"], ctx["draft"]) == [], out["answer"]
    for step in out["transcript"]:
        assert ungrounded(step["result_digest"], ctx["smes"], ctx["draft"]) == []


# ---------------- channels under failure ----------------

def http(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, None)


@pytest.mark.parametrize("failure", [
    http(401), http(403), http(404), http(409), http(429), http(500), http(503),
    TimeoutError("timed out"), ValueError("Expecting value"), RuntimeError("<html>502 Bad Gateway</html>"),
])
def test_a_calendar_failure_never_raises_and_is_reported(db, live, failure):
    def api(method, path, body):
        raise failure
    res = C.send_calendar([ROW], "sme", store=db, api=api)
    assert res["status"] == "error" and res["live"] is True
    assert res["count"] == 0 and "rejected every event" in res["detail"]
    assert db.owned_on("cohort@group.calendar.google.com") == {}, "nothing recorded for a failed write"


@pytest.mark.parametrize("sender,args", [
    ("send_email", (["a@ik.example"], "Next week")),
    ("send_sms", (["+919900000001"], "Next week")),
])
@pytest.mark.parametrize("failure", [http(401), http(429), http(500), TimeoutError("t")])
def test_email_and_sms_failures_are_reported_not_raised(live, sender, args, failure):
    def api(_body):
        raise failure
    res = getattr(C, sender)([ROW], "sme", *args, api=api)
    assert res["status"] == "error" and "rejected every message" in res["detail"]


def test_a_partial_failure_does_not_sink_the_batch(db, live):
    rows = [{**ROW, "session_id": f"S{i}"} for i in range(5)]
    seen = []

    def api(method, path, body):
        seen.append(body)
        if len(seen) in (2, 4):
            raise http(500)
        return {"id": f"e{len(seen)}"}
    res = C.send_calendar(rows, "sme", store=db, api=api)
    assert res["status"] == "sent" and res["count"] == 3
    assert "2 failed" in res["detail"]
    assert len(db.owned_on("cohort@group.calendar.google.com")) == 3


def test_a_malformed_or_empty_body_is_not_treated_as_an_event(db, live):
    for body in ({}, {"raw": "<html>oops</html>"}, {"id": None}):
        store = Store(url=None, path=str(db.path) + json.dumps(body).replace("/", "_")[:8])
        res = C.send_calendar([ROW], "sme", store=store, api=lambda m, p, b: body)
        assert res["status"] in ("sent", "skipped")
        assert store.owned_on("cohort@group.calendar.google.com") == {}, f"{body} recorded as an event id"


def test_reserved_domains_are_never_invited(live, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_JSON", '{"client_id":"c","client_secret":"s","refresh_token":"r"}')
    bodies = []
    for domain in ("ik.example", "x.invalid", "y.test", "real.com"):
        row = {**ROW, "sme_email": f"a@{domain}"}
        C.send_calendar([row], "sme", store=None, api=lambda m, p, b: bodies.append(b) or {"id": "e"})
    invited = [b.get("attendees") for b in bodies]
    assert invited[:3] == [None, None, None], f"a reserved domain was invited: {invited}"
    assert invited[3], "a real domain still gets the invite"


def test_a_service_account_names_the_teacher_instead_of_inviting(db, live):
    bodies = []
    C.send_calendar([{**ROW, "sme_email": "real@company.com"}], "sme", store=db,
                    api=lambda m, p, b: bodies.append(b) or {"id": "e"})
    assert bodies[0].get("attendees") is None, "a bare service account cannot add attendees"
    assert "Kavya Nair" in bodies[0]["description"], "so the teacher is named in the description"


def test_publish_redirect_suppresses_real_recipients_and_says_so(live, monkeypatch):
    monkeypatch.setenv("PUBLISH_REDIRECT_TO", "qa@ik.test")
    sent = []
    res = C.send_email([ROW], "sme", ["a@real.com", "b@real.com"], "Next week",
                       api=lambda body: sent.append(body["to"]) or {})
    assert sent == [["qa@ik.test"]], f"redirect did not hold: {sent}"
    assert "redirected" in res["detail"] or "qa@ik.test" in res["detail"], res["detail"]


def test_the_store_retries_a_dropped_connection(tmp_path):
    """The reconnect path is what keeps a suspended Neon/Supavisor socket from failing a publish."""
    store = Store(url=None, path=str(tmp_path / "r.db"))
    store.save_schedule("2026-W37", {"draft": []})
    os.remove(str(tmp_path / "r.db"))                     # the database vanishes under a live process
    store.save_schedule("2026-W37", {"draft": [ROW]})     # schema rebuilt and retried, not raised
    assert store.load_schedule("2026-W37")["draft"] == [ROW]


def test_store_info_reports_durability_honestly(tmp_path, monkeypatch):
    assert Store(url=None, path=str(tmp_path / "s.db")).info()["durable"] is False
    # constructing a Postgres Store connects, so skip schema creation to inspect the reporting alone
    monkeypatch.setattr(Store, "_init", lambda self: None)
    info = Store(url="postgres://user:pw@host/db").info()
    assert info == {"driver": "postgres", "location": "postgres", "durable": True}
    assert "pw" not in json.dumps(info), "the connection string must never be echoed back"

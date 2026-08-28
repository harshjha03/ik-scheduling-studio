"""Recovery & Review Copilot — scripted mock LLM, never the network."""
import json
import os
import sys

import pytest
from fastapi import HTTPException

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import dotenv as _dotenv  # noqa: E402

_real_load = _dotenv.load
_dotenv.load = lambda path: 0          # api.index would otherwise pull the real .env keys into every later test
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("IK_DB_PATH", "/tmp/ik-agent-test.db")
from api import index as api  # noqa: E402
_dotenv.load = _real_load
from engine import agent as A  # noqa: E402
from engine import tools as T  # noqa: E402
from engine.run import run_pipeline  # noqa: E402
from tests.test_engine import FULL, session, sme, weeks  # noqa: E402

# Wed 2026-09-09 10:00 IST = 04:30Z. Three DSA teachers:
#   X (T14) — the one dropping out, holds a class and a doubt session
#   Y       — carries the class topic but is busy with a doubt session at the class hour (swap-chain target)
#   Z       — level 1: can take doubt sessions, not the level-2 class
CLASS = session("W37-DSA-01-1", batch="DSA-01", subject="DSA", ss="graphs", typ="class", start="2026-09-09T04:30:00Z", level=2)
Y_DOUBT = session("W37-DSA-02-1", batch="DSA-02", subject="DSA", ss=None, typ="doubt", start="2026-09-09T04:30:00Z", level=1)
X_DOUBT = session("W37-DSA-01-2", batch="DSA-01", subject="DSA", ss=None, typ="doubt", start="2026-09-09T07:30:00Z", level=1)
OTHER = session("W37-ML-01-1", batch="ML-01", subject="ML", ss="nlp", typ="class", start="2026-09-10T04:30:00Z", level=1)


def _sme(id, name, ss, level, windows=FULL):
    s = sme(id, subject="DSA", ss=ss, level=level, windows=windows)
    s["name"] = name
    return s


def make(swap_only=False):
    smes = [_sme("T14", "Xavier", "graphs", 2), _sme("Y", "Yamini", "graphs", 2), _sme("Z", "Zed", None, 1),
            {**sme("M", subject="ML", ss="nlp"), "name": "Mira"}]
    if not swap_only:
        smes.append(_sme("W", "Wen", "graphs", 2))     # a free direct replacement
    sessions = [CLASS, Y_DOUBT, X_DOUBT, OTHER]
    ov = [{"session_id": CLASS["id"], "batch_id": "DSA-01", "from_sme_id": None, "to_sme_id": "T14"},
          {"session_id": Y_DOUBT["id"], "batch_id": "DSA-02", "from_sme_id": None, "to_sme_id": "Y"}]
    res = run_pipeline(sessions, smes, [], ov, llm_enabled=False)
    draft = res["draft"]
    # pin the layout the scenario needs (Stage B ties may order otherwise); Stage D-valid by construction
    for r in draft:
        want = {CLASS["id"]: "T14", Y_DOUBT["id"]: "Y", X_DOUBT["id"]: "T14", OTHER["id"]: "M"}[r["session_id"]]
        r["sme_id"], r["sme_name"], r["stage"] = want, next(s["name"] for s in smes if s["id"] == want), "override"
        r["flags"] = []
    return T.make_ctx("2026-W37", draft, smes, [], {"sme_id": "T14", "days": ["wed"]})


def scripted(*steps):
    it = iter(steps)
    calls = []

    def call(system, messages):
        calls.append(messages)
        return next(it)
    call.calls = calls
    return call


def act(tool, **args):
    return {"thought": tool, "action": {"tool": tool, "args": args}}


def final(answer, plan):
    return {"thought": "done", "final": {"answer": answer, "plan": plan}}


def move(sid, to, frm="T14"):
    return {"session_id": sid, "from_sme": frm, "to_sme": to, "reason": "test"}


def slots_note_invites_a_plan_entry():
    return True


# ---------------- tools ----------------

def test_tools_never_mutate_the_draft():
    ctx = make()
    snapshot = [dict(r) for r in ctx["draft"]]
    T.simulate_plan(ctx, [move(CLASS["id"], "W"), move(X_DOUBT["id"], "Z")])
    T.find_freeable(ctx, CLASS["id"])
    assert [dict(r) for r in ctx["draft"]] == snapshot


def test_candidates_exclude_the_unavailable_teacher_and_the_busy_one():
    c = T.get_candidates(ctx := make(), CLASS["id"])
    ids = {x["sme_id"] for x in c["candidates"]}
    assert ids == {"W"}
    rules = {e["sme_id"]: e["rule"] for e in c["eliminated"]}
    assert rules["Y"] == f"overlap:{Y_DOUBT['id']}" and rules["Z"] == "training_level"
    assert T.get_affected_rows(ctx, "T14", ["Wed"])["count"] == 2
    assert T.get_affected_rows(ctx, "T14", ["Thu"])["count"] == 0


def test_find_freeable_offers_the_doubt_swap_with_a_replacement():
    f = T.find_freeable(make(swap_only=True), CLASS["id"])["freeable"]
    assert [x["sme_id"] for x in f] == ["Y"]
    assert f[0]["frees_session"]["session_id"] == Y_DOUBT["id"] and f[0]["has_replacement"]
    assert {c["sme_id"] for c in f[0]["replacement_candidates"]} == {"Z"}


def test_simulate_verdicts():
    ctx = make(swap_only=True)
    v = {x["session_id"]: x["verdict"] for x in T.simulate_plan(ctx, [
        move(CLASS["id"], "Z"),                    # level rule
        move(X_DOUBT["id"], "T14", frm="T14"),     # the drop-out themself
    ])["verdicts"]}
    assert v[CLASS["id"]] == "breaks:training_level" and v[X_DOUBT["id"]] == "breaks:unavailable"
    # the class to Y without freeing Y's doubt session is an overlap; with the chain it is ok
    alone = T.simulate_plan(ctx, [move(CLASS["id"], "Y")])["verdicts"][0]["verdict"]
    assert alone.startswith("breaks:overlap")
    chain = T.simulate_plan(ctx, [move(CLASS["id"], "Y"), move(Y_DOUBT["id"], "Z", frm="Y")])
    assert chain["all_ok"] and [x["verdict"] for x in chain["verdicts"]] == ["ok", "ok"]


def test_blocked_teachers_carry_what_would_unblock_them():
    """A dead end is only useful if it names the nearest miss — 'no' with no next step is what made
    the copilot answer 'unfortunately, nothing' to a question a human could act on."""
    c = T.get_candidates(make(swap_only=True), CLASS["id"])
    detail = {e["sme_id"]: e["detail"] for e in c["eliminated"]}
    assert "already teaching" in detail["Y"] and Y_DOUBT["batch_id"] in detail["Y"]
    assert "training-level upgrade to 2" in detail["Z"]


def test_find_slots_offers_another_hour_and_prefers_the_same_day():
    ctx = make(swap_only=True)
    # Y is free all week except the hour her doubt session sits in, so the class can move to any other hour
    out = T.find_slots(ctx, CLASS["id"], limit=4)
    assert out["current"] == {"day": "Wed", "hour_ist": "10:00"}
    assert out["slots"], "the class can run at other hours — saying it cannot would be wrong"
    assert out["slots"][0]["day"] == "Wed", "least disruption is the same day"
    assert abs(int(out["slots"][0]["hour_ist"][:2]) - 10) <= 2, "and the nearest hour to the original"
    assert all(any(p["sme_id"] in ("Y", "Z", "W", "T14") for p in s["eligible"]) for s in out["slots"])
    assert "reschedule" in out["note"], "the note must invite the action, not deny it exists"


def test_find_slots_never_double_books_the_batch_or_offers_the_drop_out():
    ctx = make(swap_only=True)          # T14 unavailable on Wed; X_DOUBT is DSA-01 at Wed 13:00
    out = T.find_slots(ctx, CLASS["id"], limit=40)
    taken = {(s["day"], s["hour_ist"]) for s in out["slots"]}
    assert ("Wed", "13:00") not in taken, "DSA-01 already has a session then — learners cannot attend both"
    wed = [s for s in out["slots"] if s["day"] == "Wed"]
    assert wed and all("T14" not in [p["sme_id"] for p in s["eligible"]] for s in wed), \
        "the teacher reported unavailable on Wednesday must never be offered"


def test_find_slots_says_so_plainly_when_no_hour_works():
    ctx = make(swap_only=True)
    narrow = [{**s, "weekly_availability": []} for s in ctx["smes"]]      # nobody works at all
    out = T.find_slots({**ctx, "smes": narrow}, CLASS["id"])
    assert out["slots"] == [] and "No slot this week" in out["note"]


def test_options_rules_are_appended_not_edited_into_the_spec_prompt():
    assert A.SYSTEM.startswith("You are the scheduling copilot")
    assert A.OPTIONS_ADDENDUM not in A.SYSTEM and "O2." in A.OPTIONS_ADDENDUM
    assert "find_slots" in A.TOOL_DOC


def test_bad_tool_args_raise_tool_error():
    ctx = make()
    with pytest.raises(T.ToolError):
        T.call_tool(ctx, "get_row", {"sid": "x"})
    with pytest.raises(T.ToolError):
        T.call_tool(ctx, "nope", {})
    with pytest.raises(T.ToolError):
        T.get_affected_rows(ctx, "T14", ["someday"])


# ---------------- agent loop ----------------

def test_happy_path_direct_replacement():
    ctx = make()
    llm = scripted(act("get_affected_rows", sme_id="T14", days=["Wed"]),
                   act("get_candidates", session_id=CLASS["id"]),
                   act("get_candidates", session_id=X_DOUBT["id"]),
                   act("simulate_plan", moves=[move(CLASS["id"], "W"), move(X_DOUBT["id"], "Z")]),
                   final("Wen takes the class, Zed the doubt session.", [move(CLASS["id"], "W"), move(X_DOUBT["id"], "Z")]))
    out = A.run_agent(ctx, "recovery", "T14", ["Wed"], llm_call=llm)
    assert out["status"] == "ok"
    assert [(m["session_id"], m["to_sme"], m["verdict"]) for m in out["plan"]] == [(CLASS["id"], "W", "ok"), (X_DOUBT["id"], "Z", "ok")]
    assert [s["tool"] for s in out["transcript"]] == ["get_affected_rows", "get_candidates", "get_candidates", "simulate_plan"]
    assert out["simulation"]["all_ok"] and out["meta"]["tool_calls"] == 4


def test_depth_two_swap_chain():
    ctx = make(swap_only=True)
    plan = [move(CLASS["id"], "Y"), move(Y_DOUBT["id"], "Z", frm="Y"), move(X_DOUBT["id"], "Z")]
    llm = scripted(act("get_affected_rows", sme_id="T14"),
                   act("get_candidates", session_id=CLASS["id"]),       # nobody direct
                   act("find_freeable", session_id=CLASS["id"]),        # Y via her doubt session, Z replaces
                   act("get_candidates", session_id=X_DOUBT["id"]),
                   act("simulate_plan", moves=plan),
                   final("Free Yamini by moving her doubt session to Zed.", plan))
    out = A.run_agent(ctx, "recovery", "T14", ["Wed"], llm_call=llm)
    assert out["status"] == "ok"
    verdicts = {m["session_id"]: m["verdict"] for m in out["plan"]}
    assert verdicts[CLASS["id"]] == "ok" and verdicts[Y_DOUBT["id"]] == "ok"
    # Zed cannot hold both doubt sessions if they overlap — here they do not (07:30Z vs 04:30Z)
    assert verdicts[X_DOUBT["id"]] == "ok"


def test_invalid_tool_retries_once_then_falls_back():
    ctx = make()
    llm = scripted(act("teleport", sme_id="T14"), {"thought": "?", "garbage": True}, final("never reached", None))
    out = A.run_agent(ctx, "recovery", "T14", ["Wed"], llm_call=llm)
    assert out["status"] == "fallback" and len(llm.calls) == 2
    assert "error" in llm.calls[1][-1]["content"]             # the error was fed back before the retry
    assert out["plan"] and all(m["flag"] == "AGENT_FALLBACK" for m in out["plan"])
    assert {m["session_id"] for m in out["plan"]} == {CLASS["id"], X_DOUBT["id"]}
    assert "fallback" in out["answer"].lower()


def test_empty_provider_response_retries_once_then_falls_back():
    """A 200 with no text is a bad answer, not an outage (thinking models do this) — spend the retry."""
    import engine.llm as L
    ctx = make()
    calls = []

    def empty(system, messages):
        calls.append(messages[-1]["content"])
        raise L.LLMEmptyResponse("gemini returned no text (finish_reason='stop')")
    out = A.run_agent(ctx, "recovery", "T14", ["Wed"], llm_call=empty)
    assert len(calls) == 2 and "empty or unparseable" in calls[1]
    assert out["status"] == "fallback" and out["transcript"][0]["error"]
    assert out["plan"] and all(m["flag"] == "AGENT_FALLBACK" for m in out["plan"])

    # and it recovers when the retry succeeds
    seq = iter([None, final("Wen takes it.", [move(CLASS["id"], "W")])])

    def flaky(system, messages):
        nxt = next(seq)
        if nxt is None:
            raise L.LLMEmptyResponse("no text")
        return nxt
    out = A.run_agent(ctx, "recovery", "T14", ["Wed"], llm_call=flaky)
    assert out["status"] == "ok" and out["plan"] is None      # W was never shown as a candidate this run


def test_missing_content_is_a_named_error_not_a_keyerror():
    import engine.llm as L
    with pytest.raises(L.LLMEmptyResponse) as e:
        L._text_of({"choices": [{"finish_reason": "MAX_TOKENS", "message": {"extra_content": {}}}],
                    "usage": {"total_tokens": 9}}, "some-model")
    assert "MAX_TOKENS" in str(e.value) and "some-model" in str(e.value)
    assert L._text_of({"choices": [{"message": {"content": " hi "}}]}, "m") == "hi"


def test_one_rolling_user_message_no_assistant_replay():
    """Thinking models return an opaque signature with their answer; replaying our own assistant JSON
    instead makes them answer with empty content. The loop must send exactly one user message."""
    ctx = make()
    seen = []

    def spy(system, messages):
        seen.append(messages)
        return final("no plan", None) if len(seen) > 1 else act("get_draft_summary")
    A.run_agent(ctx, "review", question="how does the week look?", llm_call=spy)
    assert all(len(m) == 1 and m[0]["role"] == "user" for m in seen)
    assert "get_draft_summary({}) returned" in seen[1][0]["content"]


def test_options_written_into_plan_are_kept_as_text_not_lost():
    """Seen live: the model filed its reschedule options as `plan` entries. They are not applyable, but
    losing them left the coordinator with six 'Dropped move' notes and no answer."""
    ctx = make()
    out = A.run_agent(ctx, "chat", question="whom can I assign the two unstaffed classes?", llm_call=scripted(
        final("Neither class can be staffed as scheduled. Here are the options:", [
            "- Move DSA-01 to Sat 13:00 — Ananya Iyer can take it",
            {"option": "- Or upgrade Rohan Mehta to level 3"},
        ])))
    assert out["plan"] is None
    assert "Move DSA-01 to Sat 13:00" in out["answer"] and "upgrade Rohan Mehta" in out["answer"]
    assert out["answer"].count("were not staffing moves") == 1, "one note, not one per item"
    assert "plan entry #" not in out["answer"]


def test_the_plan_contract_is_spelled_out():
    flat = " ".join(A.OPTIONS_ADDENDUM.split())
    assert "O4c." in A.OPTIONS_ADDENDUM and "never a plan entry" in flat
    for kind in ("move", "reschedule", "upgrade"):          # all three applyable kinds are documented
        assert f'"kind":"{kind}"' in A.OPTIONS_ADDENDUM


def test_ineligible_pick_is_stripped_and_explained():
    ctx = make()
    llm = scripted(act("get_candidates", session_id=CLASS["id"]),
                   final("Give it to Zed.", [move(CLASS["id"], "Z")]))    # Z was shown as *eliminated*, never eligible
    out = A.run_agent(ctx, "recovery", "T14", ["Wed"], llm_call=llm)
    assert out["status"] == "ok" and out["plan"] is None
    assert "Zed never appeared as a candidate" in out["answer"] and "no plan to apply" in out["answer"]


def test_hard_rule_break_is_stripped_even_if_the_model_skipped_simulate():
    ctx = make(swap_only=True)
    llm = scripted(act("find_freeable", session_id=CLASS["id"]),
                   final("Yamini takes it.", [move(CLASS["id"], "Y")]))   # Y appeared, but without the freeing move she overlaps
    out = A.run_agent(ctx, "recovery", "T14", ["Wed"], llm_call=llm)
    assert out["plan"] is None and "Yamini fails time overlap" in out["answer"]


def test_last_turn_is_spent_on_an_answer_not_another_tool_call():
    """MAX_LLM_TURNS < MAX_TOOL_CALLS, so a run that keeps exploring could never answer. The loop
    warns the model on its final turn — a real question stopped coming back budget_exhausted."""
    ctx = make()
    prompts = []

    def spy(system, messages):
        prompts.append(messages[0]["content"])
        n = len(prompts)
        if n < A.MAX_LLM_TURNS:
            return act("get_sme", sme_id=["Y", "Z", "W", "M", "T14"][n - 1])
        return final("Wen has the lightest week.", None)
    out = A.run_agent(ctx, "review", question="who is overloaded?", llm_call=spy)
    assert out["status"] == "ok" and out["answer"] == "Wen has the lightest week."
    assert "LAST step" not in prompts[0]
    assert "LAST step" in prompts[-1]                      # warned exactly when it mattered
    # it told a coordinator to "start a new session" once; the budget is per message, so that is a lie
    assert "NEVER tell them to start a new session" in prompts[-1]


def test_the_floor_answer_is_structured_not_a_wall_of_text():
    """Structure must not be something only the model can produce — the deterministic answer uses the
    same label / '- option' layout the UI renders."""
    ctx = make()

    def down(system, messages):
        raise A.LLMError("boom")
    out = A.run_agent(ctx, "review", question=f"what is wrong with {CLASS['id']}?", llm_call=down)
    lines = out["answer"].splitlines()
    assert any(" · " in ln for ln in lines), "each subject gets a label line"
    assert any(ln.startswith("- ") for ln in lines), "each fact gets its own option line"
    assert "" in lines, "a blank line separates subjects"


def test_chat_rules_now_allow_structure():
    assert "no bullet lists" not in A.CHAT_ADDENDUM
    assert 'one option per line starting with "- "' in A.CHAT_ADDENDUM
    assert "No markdown" in A.CHAT_ADDENDUM       # the UI renders plain text, not markdown


def test_budget_exhausted_never_blames_the_llm():
    """An exhausted budget is not an outage; saying 'LLM unavailable' would be a false status."""
    ctx = make()
    out = A.run_agent(ctx, "review", question="what is going on?",
                      llm_call=scripted(*[act("get_sme", sme_id=s) for s in ("Y", "Z", "W", "M", "T14", "Y")]))
    assert out["status"] == "budget_exhausted"
    assert "unavailable" not in out["answer"].lower()
    assert "whole budget" in out["answer"] and "What it did establish" in out["answer"]


def test_budget_exhaustion_returns_partial_findings():
    ctx = make()
    llm = scripted(*[act("get_row", session_id=sid) for sid in [CLASS["id"], X_DOUBT["id"], Y_DOUBT["id"], OTHER["id"]]],
                   act("get_sme", sme_id="Y"), act("get_sme", sme_id="Z"), act("get_sme", sme_id="W"))
    out = A.run_agent(ctx, "recovery", "T14", ["Wed"], llm_call=llm)
    assert out["status"] == "budget_exhausted"
    assert out["meta"]["llm_turns"] == A.MAX_LLM_TURNS and len(out["transcript"]) == A.MAX_LLM_TURNS
    assert "budget" in out["answer"].lower() and out["plan"]        # the floor still offers the formula's answer


def test_tool_call_cap_and_wall_clock():
    ctx = make()
    many = [act("get_sme", sme_id=s) for s in ("Y", "Z", "W", "M", "T14")] * 3
    out = A.run_agent(ctx, "recovery", "T14", ["Wed"], llm_call=scripted(*many))
    assert out["status"] == "budget_exhausted" and out["meta"]["tool_calls"] <= A.MAX_TOOL_CALLS
    ticks = iter([0, 0, 100, 100, 100])
    out = A.run_agent(ctx, "review", question="who is overloaded?", llm_call=scripted(act("get_draft_summary")), clock=lambda: next(ticks))
    assert out["status"] == "budget_exhausted" and out["plan"] is None


def test_provenance_invariant_holds_under_any_script():
    ctx = make(swap_only=True)
    scripts = [
        [final("made up", [move(CLASS["id"], "Y"), move(X_DOUBT["id"], "Z")])],
        [act("get_candidates", session_id=X_DOUBT["id"]), final("half", [move(CLASS["id"], "Y"), move(X_DOUBT["id"], "Z")])],
        [act("find_freeable", session_id=CLASS["id"]), final("chain", [move(CLASS["id"], "Y"), move(Y_DOUBT["id"], "Z", frm="Y")])],
    ]
    for steps in scripts:
        shown = {}
        for st in steps:
            a = st.get("action")
            if a and a["tool"] in ("get_candidates", "find_freeable"):
                res = T.call_tool(ctx, a["tool"], a["args"])
                shown.setdefault(a["args"]["session_id"], set()).update(c["sme_id"] for c in res.get("candidates", []))
                for f in res.get("freeable", []):
                    shown.setdefault(a["args"]["session_id"], set()).add(f["sme_id"])
                    shown.setdefault(f["frees_session"]["session_id"], set()).update(c["sme_id"] for c in f["replacement_candidates"])
        out = A.run_agent(ctx, "recovery", "T14", ["Wed"], llm_call=scripted(*steps))
        for m in out["plan"] or []:
            assert m["to_sme"] in shown.get(m["session_id"], set()), (steps, m)
        # and nothing in a returned plan breaks a hard rule
        if out["plan"]:
            assert T.simulate_plan(ctx, out["plan"])["all_ok"]


def test_a_quota_error_reaches_the_coordinator_in_plain_language():
    """The raw 429 body belongs in meta.error, never in the sentence a coordinator reads."""
    import engine.llm as L
    ctx = make()

    def quota(system, messages):
        raise L.LLMQuotaExhausted('daily request quota exhausted for m: [{"error": {"code": 429, "message": "…"}}]')
    out = A.run_agent(ctx, "chat", question="who is free on Friday?", llm_call=quota)
    assert out["status"] == "fallback"
    assert "daily request limit reached" in out["answer"] and "429" not in out["answer"]
    assert "429" in out["meta"]["error"] and out["meta"]["error_plain"]


def test_review_mode_answers_and_llm_outage_falls_back_to_engine_text():
    ctx = make()
    out = A.run_agent(ctx, "review", question=f"why is {CLASS['id']} flagged?", llm_call=scripted(final("It is fine.", None)))
    assert out["status"] == "ok" and out["plan"] is None and out["answer"] == "It is fine."

    def down(system, messages):
        raise A.LLMError("boom")
    out = A.run_agent(ctx, "review", question=f"tell me about {CLASS['id']}", llm_call=down)
    assert out["status"] == "fallback" and out["plan"] is None and "could not finish" in out["answer"]
    # the row is named the way a coordinator reads it — batch and time, not the session id (rule 6)
    assert f"{CLASS['batch_id']} · " in out["answer"] and "Wed 10:00" in out["answer"]


def test_system_prompt_is_the_spec_text_verbatim():
    assert A.SYSTEM.startswith("You are the scheduling copilot for IK Scheduling Studio.")
    assert "8. Budget: you have at most 8 tool calls." in A.SYSTEM


# ---------------- reschedule and upgrade ----------------

def _blocked_ctx():
    """A class only a reschedule or an upgrade can save: Wed 10:00 needs level 2, and the only
    level-2 teacher of the topic (Y) is busy at that hour with a class of equal severity.
    V is level 1 AND never free on Wednesday — the teacher an upgrade would not help."""
    ctx = make(swap_only=True)
    v = _sme("V", "Vera", "graphs", 1, windows=[{"weekday": d, "start_utc": "01:30", "end_utc": "15:30"}
                                                for d in ("Mon", "Tue", "Thu", "Fri")])
    draft = [dict(r, flags=list(r["flags"])) for r in ctx["draft"]]
    for r in draft:
        if r["session_id"] == Y_DOUBT["id"]:
            r.update(type="class")            # equal severity -> find_freeable will not offer it
        if r["session_id"] == CLASS["id"]:
            r.update(sme_id=None, sme_name=None, stage=None)   # the class nobody can take
    # T14 stays the reported drop-out, so the class really is unstaffable at its own hour
    return {**ctx, "draft": draft, "smes": [*ctx["smes"], v]}


def test_simulate_a_reschedule():
    ctx = _blocked_ctx()
    v = T.simulate_plan(ctx, [{"kind": "reschedule", "session_id": CLASS["id"], "to_day": "Wed",
                               "to_hour_ist": "12:00", "reason": "Yamini is free then"}])["verdicts"][0]
    assert v["kind"] == "reschedule" and v["verdict"] == "ok"
    assert v["from_hour_ist"] == "10:00" and v["to_hour_ist"] == "12:00"
    assert "can take it at Wed 12:00" in v["detail"]
    assert [c["sme_id"] for c in v["eligible_after"]]


def test_simulate_rejects_a_reschedule_that_helps_nobody():
    ctx = _blocked_ctx()
    bad = T.simulate_plan(ctx, [{"kind": "reschedule", "session_id": CLASS["id"], "to_day": "Sun", "to_hour_ist": "23:00"}])
    assert bad["verdicts"][0]["verdict"] == "breaks:outside_the_week"
    # a slot the batch already occupies would double-book the learners
    clash = T.simulate_plan(ctx, [{"kind": "reschedule", "session_id": CLASS["id"], "to_day": "Wed", "to_hour_ist": "13:00"}])
    assert clash["verdicts"][0]["verdict"] == "breaks:batch_clash" and "cannot attend both" in clash["verdicts"][0]["detail"]


def test_simulate_an_upgrade_and_the_move_it_unblocks():
    ctx = _blocked_ctx()
    plan = [{"kind": "upgrade", "sme_id": "Z", "to_level": 2, "reason": "Zed only lacks the level"},
            move(CLASS["id"], "Z", frm=None)]
    sim = T.simulate_plan(ctx, plan)
    up, mv = sim["verdicts"][0], sim["verdicts"][1]
    assert up["kind"] == "upgrade" and up["from_level"] == 1 and up["to_level"] == 2
    assert up["verdict"] == "ok" and CLASS["id"] in up["unblocks"]
    assert mv["verdict"] == "ok", "the move is judged against the upgraded roster, not the current one"
    assert sim["all_ok"]
    assert next(x for x in ctx["smes"] if x["id"] == "Z")["training_level"] == 1, "the real roster is never touched"


def test_an_upgrade_must_help_a_class_that_needs_a_teacher():
    """Seen live: an upgrade came back `ok` because it qualified the teacher for classes that already had
    one. It has to unblock a class that actually needs somebody, or it is not worth a level change."""
    ctx = _blocked_ctx()
    staffed = [dict(r, flags=list(r["flags"])) for r in ctx["draft"]]
    for r in staffed:
        if r["session_id"] == CLASS["id"]:
            r.update(sme_id="Y", sme_name="Yamini", stage="override")     # nothing needs cover any more
    v = T.simulate_plan({**ctx, "draft": staffed}, [{"kind": "upgrade", "sme_id": "Z", "to_level": 2}])["verdicts"][0]
    assert v["verdict"] == "breaks:changes_nothing" and "already have a teacher" in v["detail"]


def test_simulate_rejects_an_upgrade_that_changes_nothing():
    """The copilot told a coordinator to upgrade Rohan Mehta; he also failed availability, so the
    upgrade was useless advice. Simulation is what catches that."""
    ctx = _blocked_ctx()
    v = T.simulate_plan(ctx, [{"kind": "upgrade", "sme_id": "V", "to_level": 2}])["verdicts"][0]
    assert v["verdict"] == "breaks:changes_nothing" and "not make them eligible" in v["detail"]
    for bad, expect in (({"sme_id": "Y", "to_level": 2}, "breaks:not_an_upgrade"),
                        ({"sme_id": "Z", "to_level": 9}, "breaks:level_out_of_range")):
        assert T.simulate_plan(ctx, [{"kind": "upgrade", **bad}])["verdicts"][0]["verdict"] == expect


def test_simulate_accepts_the_names_a_model_reaches_for():
    """Live: the model called simulate_plan with {reschedules, upgrades} and burned its retry on the
    key name. Every spelling of "the plan" means the same list."""
    ctx = _blocked_ctx()
    entry = {"kind": "reschedule", "session_id": CLASS["id"], "to_day": "Wed", "to_hour_ist": "12:00"}
    up = {"kind": "upgrade", "sme_id": "Z", "to_level": 2}
    for args in ({"plan": [entry]}, {"moves": [entry]}, {"actions": [entry]}, {"entries": [entry]},
                 {"changes": [entry]}, {"reschedules": [entry]}):
        assert T.call_tool(ctx, "simulate_plan", args)["verdicts"][0]["verdict"] == "ok"
    split = T.call_tool(ctx, "simulate_plan", {"reschedules": [entry], "upgrades": [up]})
    assert [v["kind"] for v in split["verdicts"]] == ["upgrade", "reschedule"] or \
           {v["kind"] for v in split["verdicts"]} == {"upgrade", "reschedule"}
    assert T.call_tool(ctx, "simulate_plan", {"plan": []})["verdicts"] == []
    with pytest.raises(T.ToolError) as e:
        T.call_tool(ctx, "simulate_plan", {"plan": [entry], "stuff": 1})
    assert "unexpected" in str(e.value)
    for empty in ({}, {"stuff": [entry]}):          # nothing plan-shaped at all: say what it needs
        with pytest.raises(T.ToolError) as e2:
            T.call_tool(ctx, "simulate_plan", empty)
        assert "needs `plan`" in str(e2.value)


def test_bad_action_shapes_are_named():
    ctx = _blocked_ctx()
    for bad in ({"kind": "reschedule", "session_id": CLASS["id"], "to_day": "Wed"},
                {"kind": "upgrade", "sme_id": "Z"},
                {"kind": "teleport", "session_id": CLASS["id"]},
                {"kind": "reschedule", "session_id": CLASS["id"], "to_day": "Wed", "to_hour_ist": "noon"}):
        with pytest.raises(T.ToolError):
            T.simulate_plan(ctx, [bad])


def test_every_verdict_kind_survives_the_transcript_digest():
    """A 500 in the wild: the digest assumed every verdict was a staffing move, so the first simulated
    reschedule crashed the run. Each kind must render as one readable line."""
    ctx = _blocked_ctx()
    sim = T.simulate_plan(ctx, [
        {"kind": "upgrade", "sme_id": "Z", "to_level": 2},
        {"kind": "reschedule", "session_id": CLASS["id"], "to_day": "Wed", "to_hour_ist": "12:00"},
        move(X_DOUBT["id"], "Z"),
    ])
    line = A._digest(sim)
    assert "Zed to level 2" in line and "Wed 12:00" in line and X_DOUBT["id"] in line
    assert "KeyError" not in line and line.count(";") == 2


def test_agent_can_return_a_reschedule_it_actually_checked():
    ctx = _blocked_ctx()
    plan = [{"kind": "reschedule", "session_id": CLASS["id"], "to_day": "Wed", "to_hour_ist": "12:00",
             "reason": "Yamini is free at noon"}]
    llm = scripted(act("get_candidates", session_id=CLASS["id"]),
                   act("find_slots", session_id=CLASS["id"]),
                   final("Move it to Wednesday noon and Yamini can take it.", plan))
    out = A.run_agent(ctx, "chat", question="nobody can teach the Wednesday class — fix it", llm_call=llm)
    assert out["status"] == "ok" and len(out["plan"]) == 1
    assert out["plan"][0]["kind"] == "reschedule" and out["plan"][0]["verdict"] == "ok"


def test_a_reschedule_to_an_unchecked_slot_is_dropped():
    """Provenance covers slots too: the copilot may only offer an hour find_slots returned."""
    ctx = _blocked_ctx()
    llm = scripted(final("Move it to Friday at 08:00.", [
        {"kind": "reschedule", "session_id": CLASS["id"], "to_day": "Fri", "to_hour_ist": "08:00"}]))
    out = A.run_agent(ctx, "chat", question="fix the Wednesday class", llm_call=llm)
    assert out["plan"] is None and "never returned by find_slots" in out["answer"]


def test_an_upgrade_is_bounded_by_what_a_class_requires():
    ctx = _blocked_ctx()
    llm = scripted(act("get_candidates", session_id=CLASS["id"]),
                   final("Take Zed to level 3.", [{"kind": "upgrade", "sme_id": "Z", "to_level": 3}]))
    out = A.run_agent(ctx, "chat", question="can we upgrade someone?", llm_call=llm)
    assert out["plan"] is None and "higher than the 2 the class requires" in out["answer"]

    # and a teacher never shown as level-blocked is not the copilot's to promote
    llm2 = scripted(final("Promote Mira.", [{"kind": "upgrade", "sme_id": "M", "to_level": 2}]))
    out2 = A.run_agent(ctx, "chat", question="upgrade somebody", llm_call=llm2)
    assert out2["plan"] is None and "never shown as blocked by training level" in out2["answer"]


def test_other_actions_are_capped():
    ctx = _blocked_ctx()
    slots = [("Wed", f"{h:02d}:00") for h in (11, 12, 14, 15, 16, 17)]
    llm = scripted(act("find_slots", session_id=CLASS["id"], limit=40),
                   final("Lots of options.", [{"kind": "reschedule", "session_id": CLASS["id"],
                                               "to_day": d, "to_hour_ist": h} for d, h in slots]))
    out = A.run_agent(ctx, "chat", question="move it", llm_call=llm)
    # duplicate session_ids are refused outright by simulate, so the cap note is what must appear
    assert out["plan"] is None or len(out["plan"]) <= A.MAX_OTHER_ACTIONS
    assert "trimmed" in out["answer"] or "same session_id" in out["answer"]


# ---------------- sweeping every issue in one go ----------------

def test_get_issues_returns_every_flag_with_its_fix_material():
    ctx = _blocked_ctx()
    out = T.get_issues(ctx)
    assert out["issues"][0]["session_id"] == CLASS["id"]
    ids = [i["session_id"] for i in out["issues"]]
    assert CLASS["id"] in ids and out["shown"] == out["total_flagged"]
    unfixable = next(i for i in out["issues"] if i["session_id"] == CLASS["id"])
    # nothing is eligible, so the expensive material is included for exactly that row
    assert unfixable["candidates"] == [] and unfixable["slots"] and "blocked" in unfixable
    assert unfixable["required_training_level"] == 2 and unfixable["sme_id"] is None
    assert T.get_issues(ctx, codes=["nothing_matches_this"])["total_flagged"] == 0


def test_get_issues_is_ordered_by_severity_and_capped():
    ctx = _blocked_ctx()
    out = T.get_issues(ctx, limit=1)
    assert out["shown"] == 1 and out["total_flagged"] >= 1
    assert out["issues"][0]["session_id"] == CLASS["id"], "the unstaffed class outranks everything else"


def test_get_issues_fits_the_context_it_is_fed_back_in():
    """It is one call precisely so a sweep fits the budget — it must also fit the prompt intact."""
    ctx = _blocked_ctx()
    assert len(json.dumps(T.get_issues(ctx))) < A.RESULT_CHARS


def test_a_whole_week_sweep_fits_in_the_budget():
    """The failure this replaced: asked to solve seven issues, the copilot spent all eight tool calls
    on get_candidates and told the coordinator to start a new session."""
    ctx = _blocked_ctx()
    seen_tools = []

    def spy(system, messages):
        seen_tools.append(len(seen_tools))
        if not seen_tools[:-1]:
            return act("get_issues")
        if len(seen_tools) == 2:
            return act("simulate_plan", plan=[{"kind": "reschedule", "session_id": CLASS["id"],
                                               "to_day": "Wed", "to_hour_ist": "12:00"}])
        return final("Moved the Wednesday class to noon; the rest are fairness warnings, not blockers.",
                     [{"kind": "reschedule", "session_id": CLASS["id"], "to_day": "Wed", "to_hour_ist": "12:00"}])
    out = A.run_agent(ctx, "chat", question="solve all the pending issues", llm_call=spy)
    assert out["status"] == "ok" and out["meta"]["tool_calls"] == 2 < A.MAX_TOOL_CALLS
    assert out["plan"] and out["plan"][0]["verdict"] == "ok"
    # provenance came from get_issues, not from a per-row get_candidates
    assert "start a new session" not in out["answer"]


def test_get_issues_includes_a_reported_drop_outs_classes():
    """They carry no flag yet — but a class whose teacher just dropped out is the week's top issue."""
    ctx = make()                                  # T14 unavailable, holding CLASS, nothing flagged
    out = T.get_issues(ctx)
    top = out["issues"][0]
    assert top["session_id"] == CLASS["id"] and "reported unavailable" in top["needs_cover"]
    assert [c["sme_id"] for c in top["candidates"]] == ["W"], "and its replacements come with it"


def test_provenance_accepts_what_the_bulk_tool_showed():
    """A candidate named by get_issues is as legitimate as one from get_candidates."""
    ctx = make()
    llm = scripted(act("get_issues"), final("Wen takes it.", [move(CLASS["id"], "W")]))
    out = A.run_agent(ctx, "recovery", "T14", ["Wed"], llm_call=llm)
    assert out["plan"] and out["plan"][0]["to_sme"] == "W", out["answer"]


# ---------------- chat mode ----------------

def test_chat_turns_free_text_into_a_plan_via_report_unavailable():
    ctx = make()
    plan = [move(CLASS["id"], "W")]
    llm = scripted(act("list_teachers"),
                   act("report_unavailable", sme_id="T14", days=["Wed"]),
                   act("get_candidates", session_id=CLASS["id"]),
                   final("Wen can cover Wednesday.", plan))
    out = A.run_agent(ctx, "chat", question="Xavier is out Wednesday, sort it out", llm_call=llm)
    assert out["status"] == "ok" and [m["to_sme"] for m in out["plan"]] == ["W"]
    assert [s["tool"] for s in out["transcript"]][:2] == ["list_teachers", "report_unavailable"]
    assert ctx["unavailable"] == {"sme_id": "T14", "days": ["Wed"]}      # the caller's ctx is untouched


def test_reported_drop_out_binds_every_later_call_in_the_run():
    """After report_unavailable, moving work back to that teacher must fail — even mid-run."""
    ctx = T.make_ctx("2026-W37", make()["draft"], make()["smes"], [])     # no unavailability to begin with
    llm = scripted(act("report_unavailable", sme_id="T14"),
                   act("simulate_plan", moves=[move(X_DOUBT["id"], "T14", frm="Z")]),
                   final("Cannot put it back to Xavier.", None))
    out = A.run_agent(ctx, "chat", question="Xavier is out; can he still take the doubt session?", llm_call=llm)
    assert "breaks:unavailable" in out["transcript"][1]["result_digest"]
    assert out["plan"] is None


def test_chat_replays_the_conversation_and_keeps_one_user_message():
    ctx = make()
    seen = []

    def spy(system, messages):
        seen.append(messages)
        return final("Rohan has four sessions.", None)
    turns = [{"role": "user", "content": "who is busiest?"}, {"role": "assistant", "content": "Rohan Mehta."}]
    A.run_agent(ctx, "chat", question="and after him?", turns=turns, llm_call=spy)
    prompt = seen[0][0]["content"]
    assert len(seen[0]) == 1 and "Conversation so far" in prompt
    assert "Coordinator: who is busiest?" in prompt and "You: Rohan Mehta." in prompt and "and after him?" in prompt


def test_chat_history_is_bounded():
    ctx = make()
    seen = []

    def spy(system, messages):
        seen.append(messages[0]["content"])
        return final("ok", None)
    turns = [{"role": "user", "content": f"turn {i} " + "x" * 4000} for i in range(30)]
    A.run_agent(ctx, "chat", question="now what?", turns=turns, llm_call=spy)
    assert "turn 29" in seen[0] and "turn 5" not in seen[0]          # only the last CHAT_HISTORY turns
    assert len(seen[0]) < A.CHAT_HISTORY * (A.TURN_CHARS + 200) + 4000


def test_chat_addendum_bounds_what_it_claims_it_can_do():
    flat = " ".join(A.CHAT_ADDENDUM.split())
    assert "C4." in A.CHAT_ADDENDUM and A.CHAT_ADDENDUM not in A.SYSTEM   # appended, never edited in
    assert "cannot publish the week" in flat and "export a CSV" in flat
    # the model invented "the Upgrade button" once; it cannot see the app, so it must never name one
    assert "NEVER name a button" in A.CHAT_ADDENDUM


def test_a_plain_answer_is_an_answer_not_a_protocol_error():
    """Live: "how do I do it?" came back as a fallback banner because the model replied {"answer": ...}
    with no `final` wrapper. Being strict about acting is right; being strict about answering is not."""
    ctx = make()
    for raw, expect in (({"answer": "Move Wen onto the Wednesday class."}, "Move Wen onto the Wednesday class."),
                        ({"thought": "chatty", "message": "You are welcome."}, "You are welcome."),
                        ({"final": "Two classes are unstaffed."}, "Two classes are unstaffed."),
                        ("Just text.", "Just text.")):
        out = A.run_agent(ctx, "chat", question="how do I do it?", llm_call=scripted(raw))
        assert out["status"] == "ok" and out["answer"] == expect, raw
        assert out["plan"] is None

    # an unwrapped action still acts, and a reply that says nothing at all is still an error
    out = A.run_agent(ctx, "chat", question="what is unfilled?",
                      llm_call=scripted({"tool": "get_draft_summary", "args": {}}, final("Two.", None)))
    assert [s["tool"] for s in out["transcript"]] == ["get_draft_summary"]
    out = A.run_agent(ctx, "chat", question="hm", llm_call=scripted({"thought": "…"}, {"thought": "…"}))
    assert out["status"] == "fallback"


def test_chat_answer_with_no_plan_is_a_success():
    ctx = make()
    out = A.run_agent(ctx, "chat", question="thanks!", llm_call=scripted(final("Any time.", None)))
    assert out["status"] == "ok" and out["plan"] is None and out["answer"] == "Any time."


def test_chat_falls_back_to_the_engine_when_the_llm_is_down():
    ctx = make()

    def down(system, messages):
        raise A.LLMError("boom")
    out = A.run_agent(ctx, "chat", question="who is unfilled?", llm_call=down)
    assert out["status"] == "fallback" and out["plan"] is None and "could not finish" in out["answer"]


def test_a_blocked_class_comes_back_with_options_not_just_no():
    """The shape the coordinator asked for: nearest miss, an alternative slot, and who can do what."""
    ctx = make(swap_only=True)
    llm = scripted(act("get_candidates", session_id=CLASS["id"]),
                   act("find_freeable", session_id=CLASS["id"]),
                   act("find_slots", session_id=CLASS["id"]),
                   final("Nobody can take it at 10:00 Wednesday. Two options: move Yamini's doubt session to "
                         "Zed and she covers the class, or keep the class Wednesday and run it at 12:00, when "
                         "Yamini is free. Rescheduling is yours to do — I can only hand you the staffing swap.",
                         [move(CLASS["id"], "Y"), move(Y_DOUBT["id"], "Z", frm="Y")]))
    out = A.run_agent(ctx, "chat", question="whom can I assign to the class nobody is on?", llm_call=llm)
    assert out["status"] == "ok"
    assert [s["tool"] for s in out["transcript"]] == ["get_candidates", "find_freeable", "find_slots"]
    assert len(out["plan"]) == 2 and all(m["verdict"] == "ok" for m in out["plan"])
    digest = out["transcript"][2]["result_digest"]
    assert "Wed" in digest and "12:00" in digest or "slots" in digest


# ---------------- API ----------------

def test_run_rejects_a_bad_mode_and_an_empty_chat_question():
    ctx = make()
    body = {"week": "2026-W37", "draft": ctx["draft"], "smes": ctx["smes"], "history": []}
    for bad in ({"mode": "wander"}, {"mode": "chat", "question": "  "}, {"mode": "chat", "question": "hi", "turns": "nope"}):
        with pytest.raises(HTTPException) as e:
            api.agent_run({**body, **bad})
        assert e.value.status_code == 422


def test_apply_refuses_auto_without_server_flag(monkeypatch):
    monkeypatch.delenv("AGENT_AUTO_APPLY", raising=False)
    ctx = make()
    body = {"week": "2026-W37", "draft": ctx["draft"], "smes": ctx["smes"], "history": [], "plan": [move(CLASS["id"], "W")]}
    with pytest.raises(HTTPException) as e:
        api.agent_apply({**body, "auto": True})
    assert e.value.status_code == 403
    out = api.agent_apply(body)                       # a human click still works
    assert out["diff"] == 1 and out["override_log"][0]["actor"] == "agent"
    row = next(r for r in out["draft"] if r["session_id"] == CLASS["id"])
    assert row["sme_id"] == "W" and row["stage"] == "override"
    # invariant: apply introduced no hard flag the simulation did not predict
    assert not [f for f in out["flags"] if f["code"] in ("HARD_CONFLICT", "UNFILLED")]
    monkeypatch.setenv("AGENT_AUTO_APPLY", "1")
    assert api.agent_apply({**body, "auto": True})["diff"] == 1


def test_apply_never_loses_an_unfilled_blocker():
    """Regression: apply strips Stage-D flags before re-validating, and Stage D does not re-flag a class
    that never had a candidate — so an unrelated unfilled class silently stopped blocking the week."""
    ctx = make()
    draft = [dict(r, flags=list(r["flags"])) for r in ctx["draft"]]
    orphan = next(r for r in draft if r["session_id"] == OTHER["id"])
    orphan.update(sme_id=None, sme_name=None, stage=None)                  # an ML class with nobody on it
    orphan["flags"] = [T.S.make_flag("UNFILLED", orphan["session_id"], "No eligible SME: seeded.")]
    body = {"week": "2026-W37", "draft": draft, "smes": ctx["smes"], "history": [],
            "plan": [move(CLASS["id"], "W")]}
    out = api.agent_apply(body)
    kept = next(r for r in out["draft"] if r["session_id"] == OTHER["id"])
    flag = next((f for f in kept["flags"] if f["code"] == "UNFILLED"), None)
    assert flag is not None, "applying a plan must not erase another class's UNFILLED flag"
    assert flag["reason"] and "No eligible SME" in flag["reason"]
    assert out["stats"]["flags_by_code"].get("UNFILLED") == 1
    assert next(r for r in out["draft"] if r["session_id"] == CLASS["id"])["sme_id"] == "W"


def test_apply_route_refuses_actions_it_cannot_persist():
    """Reschedules and upgrades change the sessions and the roster, which the client owns and re-runs the
    pipeline over. Silently ignoring them here would report success for a change that never happened."""
    ctx = make()
    body = {"week": "2026-W37", "draft": ctx["draft"], "smes": ctx["smes"], "history": []}
    for entry in ({"kind": "reschedule", "session_id": CLASS["id"], "to_day": "Thu", "to_hour_ist": "12:00"},
                  {"kind": "upgrade", "sme_id": "Z", "to_level": 2}):
        with pytest.raises(HTTPException) as e:
            api.agent_apply({**body, "plan": [entry]})
        assert e.value.status_code == 422 and "staffing moves only" in str(e.value.detail)
    assert api.agent_apply({**body, "plan": [move(CLASS["id"], "W")]})["diff"] == 1


def test_apply_refuses_a_stale_plan():
    ctx = make(swap_only=True)
    body = {"week": "2026-W37", "draft": ctx["draft"], "smes": ctx["smes"], "history": [], "plan": [move(CLASS["id"], "Y")]}
    with pytest.raises(HTTPException) as e:
        api.agent_apply(body)
    assert e.value.status_code == 409

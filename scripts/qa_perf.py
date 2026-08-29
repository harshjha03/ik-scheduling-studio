"""QA harness: measured performance, not estimates.

Every number here comes from running the thing. Latency-bound work uses an injected API that sleeps a
fixed amount, so the figures are reproducible and provider-independent. DB round trips are counted by
wrapping Store._run, because a wall-clock win that hides 41 cross-region queries is not a win.

    DATABASE_URL= .venv/bin/python scripts/qa_perf.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATABASE_URL", "")

from engine import channels as C  # noqa: E402
from engine import tools as T  # noqa: E402
from engine.agent import run_agent  # noqa: E402
from engine.run import run_pipeline  # noqa: E402
from engine.store import Store  # noqa: E402

RUNS = int(os.environ.get("QA_RUNS", "5"))
LATENCY = float(os.environ.get("QA_LATENCY", "0.3"))      # the brief's 300ms per call
ROW = {"session_id": "X", "batch_id": "DSA-01", "subject": "DSA", "sub_specialty": "Arrays",
       "type": "class", "start_utc": "2026-09-07T04:30:00Z", "duration_min": 60,
       "sme_id": "T03", "sme_name": "Kavya Nair", "flags": []}


def rd(name):
    with open(os.path.join(ROOT, "data", f"{name}.json")) as f:
        return json.load(f)


def median_of(fn, runs=RUNS):
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times), min(times), max(times)


class CountingStore(Store):
    """Store that records every SQL round trip, so a publish can be judged on queries as well as time."""

    def __init__(self, *a, **kw):
        self.queries: list[str] = []
        super().__init__(*a, **kw)

    def _run(self, q, args=(), fetch=None, retry=True):
        self.queries.append(q.strip().split()[0].upper() + " " + q.strip().split()[1:3][-1])
        return super()._run(q, args, fetch, retry)


def fresh_store():
    d = tempfile.mkdtemp()
    s = CountingStore(url=None, path=os.path.join(d, "perf.db"))
    s.queries.clear()                    # ignore schema creation
    return s


def header(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main():
    sessions, smes, history = rd("sessions_next"), rd("smes"), rd("history")

    header(f"1. POST /api/run — pipeline scaling (in-process, llm disabled, {RUNS} runs each)")
    print(f"{'sessions':>9} {'median':>9} {'min':>9} {'max':>9}   assigned/unfilled")
    for n in (41, 100, 250, 500, 1000):
        pool = [dict(sessions[i % len(sessions)], id=f"P{i:05d}") for i in range(n)]
        med, lo, hi = median_of(lambda: run_pipeline(pool, smes, history, [], llm_enabled=False))
        res = run_pipeline(pool, smes, history, [], llm_enabled=False)
        print(f"{n:>9} {med:>8.3f}s {lo:>8.3f}s {hi:>8.3f}s   "
              f"{res['stats']['assigned']}/{res['stats']['unfilled']}")

    header(f"2. Calendar publish — 41 rows, injected API sleeping {LATENCY * 1000:.0f}ms per call")
    os.environ.update({"GOOGLE_SERVICE_ACCOUNT_JSON": '{"type":"service_account"}',
                       "GOOGLE_CALENDAR_ID": "cohort@group.calendar.google.com"})
    os.environ.pop("PUBLISH_DISABLED", None)
    rows = [{**ROW, "session_id": f"S{i:02d}"} for i in range(41)]

    def slow(method, path, body):
        time.sleep(LATENCY)
        return {"id": f"evt-{path}-{hash(json.dumps(body, sort_keys=True)) % 99999}"}

    print(f"serial floor for 41 calls: {41 * LATENCY:.1f}s\n")
    print(f"{'scenario':<44} {'wall':>8} {'HTTP':>6} {'DB':>4}")
    scenarios = []

    # first publish, parallel (the shipped path)
    store = fresh_store()
    calls = {"n": 0}

    def counting(method, path, body):
        calls["n"] += 1
        return slow(method, path, body)
    t0 = time.perf_counter()
    r1 = C.send_calendar(rows, "sme", store=store, api=counting)
    par = time.perf_counter() - t0
    scenarios.append(("first publish, parallel (shipped)", par, calls["n"], len(store.queries)))

    # re-publish, unchanged rows
    calls["n"] = 0
    store.queries.clear()
    t0 = time.perf_counter()
    r2 = C.send_calendar(rows, "sme", store=store, api=counting)
    rep = time.perf_counter() - t0
    scenarios.append(("re-publish, nothing changed", rep, calls["n"], len(store.queries)))

    # re-publish after two classes were re-staffed
    changed = [dict(r) for r in rows]
    changed[0]["sme_name"] = "Someone Else"
    changed[1]["sme_name"] = "Another Teacher"
    calls["n"] = 0
    store.queries.clear()
    t0 = time.perf_counter()
    r3 = C.send_calendar(changed, "sme", store=store, api=counting)
    two = time.perf_counter() - t0
    scenarios.append(("re-publish, 2 of 41 changed", two, calls["n"], len(store.queries)))

    # serial, for the comparison the brief asks for
    old_workers = C.CAL_WORKERS
    C.CAL_WORKERS = 1
    store2 = fresh_store()
    calls["n"] = 0
    t0 = time.perf_counter()
    C.send_calendar(rows, "sme", store=store2, api=counting)
    ser = time.perf_counter() - t0
    C.CAL_WORKERS = old_workers
    scenarios.append(("first publish, serial (CAL_WORKERS=1)", ser, calls["n"], len(store2.queries)))

    for name, secs, http, db in scenarios:
        print(f"{name:<44} {secs:>7.2f}s {http:>6} {db:>4}")
    print(f"\nspeedup parallel vs serial: {ser / par:.1f}x")
    print(f"detail line, first publish : {r1['detail']}")
    print(f"detail line, re-publish    : {r2['detail']}")
    print(f"detail line, 2 changed     : {r3['detail']}")

    header("3. DB round trips per publish (why the batched write matters)")
    print(f"first publish : {scenarios[0][3]} queries for 41 rows")
    print(f"re-publish    : {scenarios[1][3]} queries")
    print("a per-row write would be 1 lookup + 41 writes = 42; measured above.")

    header(f"4. Agent — latency per mode ({RUNS} runs, scripted model, no network)")

    def one_step(answer):
        steps = iter([{"thought": "d", "final": {"answer": answer, "plan": None}}])
        return lambda system, messages: next(steps)

    draft = run_pipeline(sessions, smes, history, [], llm_enabled=False)["draft"]
    ctx = T.make_ctx("2026-W37", draft, smes, history)
    for mode, kw in (("review", {"question": "who is overloaded?"}),
                     ("chat", {"question": "who is free on Friday?"}),
                     ("recovery", {"sme_id": "T14", "days": ["Wed"]})):
        med, lo, hi = median_of(lambda: run_agent(ctx, mode, llm_call=one_step("fine"), **kw))
        print(f"{mode:<10} median {med * 1000:7.1f}ms   (min {lo * 1000:.1f} max {hi * 1000:.1f}) "
              f"— wall-clock budget is {os.environ.get('AGENT_WALL_CLOCK', '60')}s")

    header(f"5. Agent tool cost ({RUNS} runs each) — what one turn actually costs")
    for tool, args in (("get_draft_summary", {}), ("get_issues", {}), ("get_candidates", {"session_id": draft[0]["session_id"]}),
                       ("find_slots", {"session_id": draft[0]["session_id"]}),
                       ("find_freeable", {"session_id": draft[0]["session_id"]})):
        med, _, _ = median_of(lambda: T.call_tool(ctx, tool, dict(args)))
        payload = len(json.dumps(T.call_tool(ctx, tool, dict(args)), default=str))
        print(f"{tool:<20} median {med * 1000:7.1f}ms   payload {payload:>6} chars")

    header("6. Fuzz + suite timing")
    print("see pytest output in the appendix; recorded separately")
    return 0


if __name__ == "__main__":
    sys.exit(main())

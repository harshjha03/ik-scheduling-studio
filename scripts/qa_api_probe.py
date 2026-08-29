"""QA harness: hit every API endpoint with happy, malformed, empty and hostile input.

Records the status code and the first of the body for each case. Run against an ISOLATED api:

    DATABASE_URL= IK_DB_PATH=/tmp/qa.db PUBLISH_DISABLED=1 .venv/bin/uvicorn api.index:app --port 8000
    .venv/bin/python scripts/qa_api_probe.py

Nothing here is a unit test; it is an evidence generator for the QA report.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("QA_BASE", "http://127.0.0.1:8000")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rd(name):
    with open(os.path.join(ROOT, "data", f"{name}.json")) as f:
        return json.load(f)


def call(method, path, body=None, ctype="application/json", raw=None):
    """-> (status, body_text). Never raises; a transport error is reported as status 0."""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": ctype} if data else {})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, r.read().decode()[:400], time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400], time.perf_counter() - t0
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", time.perf_counter() - t0


RESULTS = []


def probe(label, method, path, expect, **kw):
    status, body, secs = call(method, path, **kw)
    ok = status in (expect if isinstance(expect, (list, tuple)) else [expect])
    RESULTS.append((ok, label, method, path, expect, status, secs, body))
    print(f"{'ok  ' if ok else 'FAIL'} {status:<4} (want {expect})  {secs:6.2f}s  {label}")
    if not ok:
        print(f"       body: {body[:200]}")
    return status, body


def main():
    S, M, H = rd("sessions_next"), rd("smes"), rd("history")
    good = {"sessions": S, "smes": M, "history": H, "overrides": [], "llm": False}

    print("\n--- /api/health, /api/integrations ---")
    probe("health", "GET", "/api/health", 200)
    probe("integrations", "GET", "/api/integrations", 200)

    print("\n--- /api/draft before any run ---")
    probe("draft before run -> 404", "GET", "/api/draft", 404)

    print("\n--- /api/run ---")
    st, body = probe("run happy path", "POST", "/api/run", 200, body=good)
    draft = json.loads(body_full("/api/run", good))["draft"] if st == 200 else []
    probe("draft after run -> 200", "GET", "/api/draft", 200)
    probe("run missing sessions", "POST", "/api/run", 422, body={"smes": M})
    probe("run missing smes", "POST", "/api/run", 422, body={"sessions": S})
    probe("run sessions is a string", "POST", "/api/run", 422, body={"sessions": "nope", "smes": M})
    probe("run smes is null", "POST", "/api/run", 422, body={"sessions": S, "smes": None})
    probe("run empty sessions", "POST", "/api/run", 422, body={"sessions": [], "smes": M})
    probe("run empty smes", "POST", "/api/run", 422, body={"sessions": S, "smes": []})
    probe("run history is a string", "POST", "/api/run", [200, 422, 500],
          body={**good, "history": "nope"})
    probe("run overrides is a dict", "POST", "/api/run", [200, 422, 500],
          body={**good, "overrides": {"a": 1}})
    probe("run malformed JSON", "POST", "/api/run", [400, 422], raw="{not json")
    probe("run wrong content-type", "POST", "/api/run", [200, 400, 422],
          raw=json.dumps(good), ctype="text/plain")
    probe("run duplicate session ids", "POST", "/api/run", [200, 422, 500],
          body={**good, "sessions": S + [dict(S[0])]})
    probe("run duplicate sme ids", "POST", "/api/run", [200, 422, 500],
          body={**good, "smes": M + [dict(M[0])]})
    probe("run unknown subject (was a 500)", "POST", "/api/run", 200,
          body={**good, "sessions": [dict(S[0], id="X1", subject="QUANTUM", sub_specialty=None)]})
    probe("run session missing start_utc", "POST", "/api/run", [200, 422, 500],
          body={**good, "sessions": [{k: v for k, v in S[0].items() if k != "start_utc"}]})
    probe("run sme missing training_level", "POST", "/api/run", [200, 422, 500],
          body={**good, "smes": [{k: v for k, v in M[0].items() if k != "training_level"}]})

    print("\n--- /api/approvals ---")
    probe("approvals happy", "POST", "/api/approvals", 200, body={"draft": draft, "decisions": []})
    probe("approvals missing draft", "POST", "/api/approvals", 422, body={"decisions": []})
    probe("approvals draft is a string", "POST", "/api/approvals", 422, body={"draft": "x"})
    probe("approvals empty draft", "POST", "/api/approvals", 200, body={"draft": [], "decisions": []})
    probe("approvals unknown session_id", "POST", "/api/approvals", 200,
          body={"draft": draft, "decisions": [{"session_id": "NOPE", "action": "approve"}]})
    probe("approvals unknown override sme", "POST", "/api/approvals", 200,
          body={"draft": draft, "decisions": [{"session_id": draft[0]["session_id"],
                                               "action": "override", "override_sme_id": "T99"}]})
    probe("approvals unknown action", "POST", "/api/approvals", 200,
          body={"draft": draft, "decisions": [{"session_id": draft[0]["session_id"], "action": "explode"}]})

    print("\n--- /api/schedule ---")
    probe("schedule GET unsaved week -> null", "GET", "/api/schedule?week=2099-W01", 200)
    probe("schedule POST happy", "POST", "/api/schedule", 200, body={"week": "2026-W37", "draft": draft})
    probe("schedule POST missing week", "POST", "/api/schedule", 422, body={"draft": draft})
    probe("schedule POST draft not a list", "POST", "/api/schedule", 422,
          body={"week": "2026-W37", "draft": "x"})

    print("\n--- /api/publish (isolated: must be simulated) ---")
    probe("publish missing channel", "POST", "/api/publish", 422, body={"week": "2026-W37"})
    probe("publish unknown channel", "POST", "/api/publish", 422,
          body={"week": "2026-W37", "channel": "carrier-pigeon", "audience": "sme", "rows": draft[:2]})
    probe("publish happy (simulated)", "POST", "/api/publish", 200,
          body={"week": "2026-W37", "week_label": "Next week", "channel": "cal", "audience": "sme",
                "rows": draft[:2], "smes": M, "batches": rd("batches")})
    probe("publish empty rows", "POST", "/api/publish", 200,
          body={"week": "2026-W37", "week_label": "Next week", "channel": "email", "audience": "sme",
                "rows": [], "smes": M, "batches": []})
    probe("publish log", "GET", "/api/publish/log?week=2026-W37", 200)

    print("\n--- /api/sheets ---")
    probe("sheets pull no dataset/tab", "POST", "/api/sheets/pull", 422, body={})
    probe("sheets pull unknown dataset", "POST", "/api/sheets/pull", 422, body={"dataset": "payroll"})
    probe("sheets pull sessions (seed source)", "POST", "/api/sheets/pull", 200, body={"dataset": "sessions"})
    probe("sheets push missing week", "POST", "/api/sheets/push", 422, body={"rows": []})
    probe("sheets push rows not a list", "POST", "/api/sheets/push", 422,
          body={"week": "2026-W37", "rows": "x"})
    probe("sheets push (simulated)", "POST", "/api/sheets/push", 200,
          body={"week": "2026-W37", "rows": [{"week": "2026-W37"}]})

    print("\n--- /api/availability/sync ---")
    probe("availability missing smes", "POST", "/api/availability/sync", 422,
          body={"week_start_utc": "2026-09-07T00:00:00Z", "week_end_utc": "2026-09-14T00:00:00Z"})
    probe("availability missing window", "POST", "/api/availability/sync", 422, body={"smes": M})
    probe("availability happy (simulated)", "POST", "/api/availability/sync", 200,
          body={"smes": M, "week_start_utc": "2026-09-07T00:00:00Z", "week_end_utc": "2026-09-14T00:00:00Z"})

    print("\n--- /api/data ---")
    probe("data GET", "GET", "/api/data", 200)
    probe("data POST unknown name", "POST", "/api/data/payroll", 422, body={"payload": [{"a": 1}]})
    probe("data POST structural (courses)", "POST", "/api/data/courses", 422, body={"payload": {"a": 1}})
    probe("data POST empty payload", "POST", "/api/data/smes", 422, body={"payload": []})
    probe("data POST payload not a list", "POST", "/api/data/smes", 422, body={"payload": "x"})
    probe("data POST happy", "POST", "/api/data/smes", 200, body={"payload": M, "source": "csv"})
    probe("data reset", "POST", "/api/data/reset", 200, body={})

    print("\n--- /api/overrides ---")
    probe("overrides", "GET", "/api/overrides", 200)
    probe("overrides for a week", "GET", "/api/overrides?week=2026-W37", 200)

    print("\n--- /api/agent ---")
    ctx = {"week": "2026-W37", "draft": draft, "smes": M, "history": H}
    probe("agent bad mode", "POST", "/api/agent/run", 422, body={**ctx, "mode": "wander"})
    probe("agent review without question", "POST", "/api/agent/run", 422, body={**ctx, "mode": "review"})
    probe("agent chat blank question", "POST", "/api/agent/run", 422,
          body={**ctx, "mode": "chat", "question": "   "})
    probe("agent turns not a list", "POST", "/api/agent/run", 422,
          body={**ctx, "mode": "chat", "question": "hi", "turns": "nope"})
    probe("agent recovery without sme_id", "POST", "/api/agent/run", 422, body={**ctx, "mode": "recovery"})
    probe("agent missing draft", "POST", "/api/agent/run", 422,
          body={"week": "2026-W37", "smes": M, "mode": "review", "question": "x"})
    probe("agent run review (no LLM -> fallback)", "POST", "/api/agent/run", 200,
          body={**ctx, "mode": "review", "question": "why is W37-DSA-01-1 unfilled?"})

    move = {"session_id": draft[0]["session_id"], "from_sme": draft[0]["sme_id"],
            "to_sme": (draft[0]["candidates"] or [{"sme_id": "T02"}])[0]["sme_id"], "reason": "qa"}
    probe("agent apply auto without flag -> 403", "POST", "/api/agent/apply", 403,
          body={**ctx, "plan": [move], "auto": True})
    probe("agent apply empty plan", "POST", "/api/agent/apply", 422, body={**ctx, "plan": []})
    probe("agent apply plan not a list", "POST", "/api/agent/apply", 422, body={**ctx, "plan": "x"})
    probe("agent apply reschedule entry -> 422", "POST", "/api/agent/apply", 422,
          body={**ctx, "plan": [{"kind": "reschedule", "session_id": draft[0]["session_id"],
                                 "to_day": "Thu", "to_hour_ist": "12:00"}]})
    probe("agent apply upgrade entry -> 422", "POST", "/api/agent/apply", 422,
          body={**ctx, "plan": [{"kind": "upgrade", "sme_id": "T02", "to_level": 3}]})
    probe("agent apply unknown session", "POST", "/api/agent/apply", 422,
          body={**ctx, "plan": [{"session_id": "NOPE", "to_sme": "T02", "reason": "x"}]})
    probe("agent apply unknown sme", "POST", "/api/agent/apply", 422,
          body={**ctx, "plan": [{"session_id": draft[0]["session_id"], "to_sme": "T99", "reason": "x"}]})

    print("\n--- oversized payloads ---")
    big = []
    for i in range(1000):
        src = S[i % len(S)]
        big.append(dict(src, id=f"BIG-{i:04d}"))
    st, body, secs = call("POST", "/api/run", {"sessions": big, "smes": M, "history": H,
                                               "overrides": [], "llm": False})
    print(f"{'ok  ' if st == 200 else 'FAIL'} {st} 1000 sessions in {secs:.1f}s")
    RESULTS.append((st == 200, "run 1000 sessions", "POST", "/api/run", 200, st, secs, body[:200]))

    print("\n=== summary ===")
    bad = [r for r in RESULTS if not r[0]]
    print(f"{len(RESULTS) - len(bad)} as expected, {len(bad)} unexpected")
    for r in bad:
        print(f"  UNEXPECTED  {r[1]}: wanted {r[4]}, got {r[5]} — {r[7][:160]}")
    return 0


def body_full(path, body):
    _, b, _ = call("POST", path, body)
    return b


if __name__ == "__main__":
    # the probe posts the full draft around, so read it once at full length
    def call_full(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.load(r)

    globals()["body_full"] = lambda path, body: json.dumps(call_full("POST", path, body))
    sys.exit(main())

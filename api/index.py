"""FastAPI app served as a Vercel Python function. Stateless: every call carries the state it needs."""
from __future__ import annotations

import os
import sys
from collections import Counter

from fastapi import Body, FastAPI, HTTPException

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import dotenv  # noqa: E402
from engine.llm import llm_configured, llm_provider  # noqa: E402

# Local dev only; both are git-ignored and no-op on Vercel, where the variables come from project
# settings. `.env.local` is what `vercel env pull` writes, so it wins — same precedence as Next.js.
dotenv.load(os.path.join(ROOT, ".env.local"))
dotenv.load(os.path.join(ROOT, ".env"))
from engine import channels  # noqa: E402
from engine import ingest  # noqa: E402
from engine import sheets  # noqa: E402
from engine import stages as S  # noqa: E402
from engine import tools  # noqa: E402
from engine.agent import run_agent  # noqa: E402
from engine.run import apply_approvals, run_pipeline  # noqa: E402
from engine.store import store  # noqa: E402

app = FastAPI(title="SME Scheduler API")
# What the dashboard boots from. A stored row wins over the bundled data/<name>.json.
# `courses`, `weeks` and `meta` are deliberately absent: they are structural (the calendar grid, the
# week labels, the course->topic map) and no flow in the app produces a new one, so accepting a write
# for them would store something the page then ignores.
DATASETS = ("sessions_next", "sessions_current", "smes", "smes_current", "history", "batches")
_last_run: dict | None = None  # warm-instance cache only; the frontend is the source of truth


REQUIRED = {"sessions": ("id", "start_utc", "duration_min", "subject"), "smes": ("id", "name", "training_level")}


def _require_shape(body: dict) -> None:
    """QA-04: the keys the engine indexes without .get(). Anything else missing degrades inside the
    engine (an UNFILLED row, an empty pool); these four would have been a bare 500."""
    for key in ("history", "overrides"):
        if body.get(key) is not None and not isinstance(body[key], list):
            raise HTTPException(422, f"`{key}` must be a list")
    for key, fields in REQUIRED.items():
        for i, item in enumerate(body[key]):
            if not isinstance(item, dict):
                raise HTTPException(422, f"`{key}[{i}]` must be an object")
            missing = [f for f in fields if f not in item]
            if missing:
                raise HTTPException(422, f"`{key}[{i}]` is missing {missing}")


@app.post("/api/run")
def run(body: dict = Body(...)):
    global _last_run
    for key in ("sessions", "smes"):
        if not isinstance(body.get(key), list) or not body[key]:
            raise HTTPException(422, f"`{key}` must be a non-empty list")
        # QA-03: the engine keys its working set by id, so two rows sharing one would collapse into a
        # single shared object — 42 counted, 41 scheduled, and one calendar event for two classes.
        dupes = sorted(k for k, n in Counter(x.get("id") for x in body[key] if isinstance(x, dict)).items() if n > 1)
        if dupes:
            raise HTTPException(422, f"duplicate {'session' if key == 'sessions' else 'SME'} id(s): {dupes}")
    _require_shape(body)
    _last_run = run_pipeline(body["sessions"], body["smes"], body.get("history") or [],
                             body.get("overrides") or [], llm_enabled=body.get("llm", True) is not False)
    return _last_run


@app.get("/api/draft")
def draft():
    if _last_run is None:
        raise HTTPException(404, "no cached draft — POST /api/run")
    return _last_run


@app.post("/api/approvals")
def approvals(body: dict = Body(...)):
    if not isinstance(body.get("draft"), list):
        raise HTTPException(422, "`draft` must be a list of draft rows")
    out = apply_approvals(body["draft"], body.get("decisions") or [])
    # apply_approvals is a pure function in the engine and stays that way: it returns what it did, and
    # the route that owns a database is the one that writes it down.
    if body.get("week") and out["override_log"]:
        store().record_overrides(body["week"], out["override_log"], actor=body.get("actor") or "human")
    return out


@app.get("/api/overrides")
def overrides(week: str | None = None, limit: int = 100):
    """Every override, and the rate per week. An override is a labelled disagreement with the matcher,
    so the rate over time is the trust metric — it is the one number worth watching go down."""
    st = store()
    counts = st.override_counts()
    rates = {}
    for w, n in counts.items():
        saved = st.load_schedule(w) or {}
        assigned = ((saved.get("stats") or {}).get("assigned")
                    or sum(1 for r in (saved.get("draft") or []) if r.get("sme_id")))
        rates[w] = {"overridden": n, "assigned": assigned,
                    "rate": round(n / assigned, 4) if assigned else None}
    return {"entries": st.overrides(week, limit), "by_week": rates}


@app.get("/api/health")
def health():
    return {"ok": True, "llm_configured": llm_configured(),
            "llm_provider": llm_provider() if llm_configured() else None,
            "llm_model": os.environ.get("LLM_MODEL")}


@app.get("/api/integrations")
def integrations():
    """What is actually wired up right now — the UI labels every channel live or simulated."""
    return {"channels": channels.status(), "storage": store().info(), "sheets": sheets.status(),
            # QA-10: no key configured means no model in play — do not echo LLM_MODEL as if it were live
            "llm": {"live": llm_configured(), "provider": llm_provider() if llm_configured() else None,
                    "model": os.environ.get("LLM_MODEL") if llm_configured() else None}}


@app.post("/api/availability/sync")
def availability_sync(body: dict = Body(...)):
    """Read each teacher's calendar for the week and hand back the roster with `external_busy` set.
    Stage A then eliminates a busy teacher with rule `calendar_busy`, and Stage D re-checks it."""
    smes = body.get("smes")
    if not isinstance(smes, list) or not smes:
        raise HTTPException(422, "`smes` must be a non-empty list")
    for key in ("week_start_utc", "week_end_utc"):
        if not body.get(key):
            raise HTTPException(422, f"`{key}` is required")
    roster, res = channels.sync_availability(smes, body["week_start_utc"], body["week_end_utc"])
    return {**res, "smes": roster, "synced_at": store_now()}


@app.post("/api/sheets/pull")
def sheets_pull(body: dict = Body(...)):
    """One dataset as CSV text, from whichever source is configured — a Google Sheet tab if there is
    one, the bundled seed data otherwise. The frontend parses it with the same validator a file
    upload uses, so there is exactly one column contract and one place row errors are worded.

    `dataset` is sessions | smes | history. `tab` still works and pins the sheet tab directly.
    """
    dataset = (body.get("dataset") or "").strip().lower()
    tab = (body.get("tab") or "").strip()
    if not dataset and not tab:
        raise HTTPException(422, "`dataset` (sessions | smes | history) or `tab` is required")
    if dataset and dataset not in ingest.DATASETS:
        raise HTTPException(422, f"unknown dataset `{dataset}`; use one of {', '.join(ingest.DATASETS)}")
    if not dataset:
        return sheets.read_tab(body.get("spreadsheet_id"), tab)
    src = ingest.pick_source(body.get("spreadsheet_id"), prefer=body.get("source"))
    if tab and isinstance(src, ingest.SheetSource):
        src.tabs[dataset] = tab
    res = src.fetch(dataset)
    res["dataset"] = dataset
    res["synced_at"] = store_now()
    return res


@app.post("/api/sheets/push")
def sheets_push(body: dict = Body(...)):
    """The approved week into the draft tab, in the same column order as the CSV export."""
    if not body.get("week"):
        raise HTTPException(422, "`week` is required")
    rows = body.get("rows")
    if not isinstance(rows, list):
        raise HTTPException(422, "`rows` must be a list of export rows")
    res = sheets.write_draft(body.get("spreadsheet_id"), rows, body.get("tab"))
    store().record_publish(body["week"], "sheet", "ops", res["status"], res["detail"], res["live"])
    return res


@app.post("/api/publish")
def publish(body: dict = Body(...)):
    """Publish one channel/audience leaf. Sends for real when that channel has credentials,
    reports `simulated` when it does not, and records the outcome either way."""
    for key in ("week", "channel", "audience"):
        if not body.get(key):
            raise HTTPException(422, f"`{key}` is required")
    rows = [r for r in (body.get("rows") or []) if r.get("sme_id")]
    smes = {s["id"]: s for s in (body.get("smes") or [])}
    batches = body.get("batches") or []
    week, channel, audience = body["week"], body["channel"], body["audience"]
    label = body.get("week_label") or week
    st = store()

    # attach the teacher's address to each row so the senders stay dumb
    for r in rows:
        s = smes.get(r.get("sme_id") or "")
        if s:
            r["sme_email"], r["sme_phone"] = s.get("email"), s.get("phone")

    if channel == "cal":
        if audience == "sme":
            res = channels.send_calendar(rows, audience, store=st)
        else:
            # students get their cohort calendar when the batch names one, else the shared calendar
            cal_of = {b["id"]: (b.get("calendar_id") or None) for b in batches}
            groups: dict[str | None, list] = {}
            for r in rows:
                groups.setdefault(cal_of.get(r.get("batch_id")), []).append(r)
            res = channels.merge([channels.send_calendar(g, audience, store=st, calendar_id=cal)
                                  for cal, g in groups.items()])
    elif channel == "email":
        to = ([s.get("email") for s in smes.values() if s.get("email")] if audience == "sme"
              else [b.get("contact_email") for b in batches if b.get("contact_email")])
        res = channels.send_email(rows, audience, [t for t in to if t], label)
    elif channel == "sms":
        to = ([s.get("phone") for s in smes.values() if s.get("phone")] if audience == "sme"
              else [b.get("contact_phone") for b in batches if b.get("contact_phone")])
        res = channels.send_sms(rows, audience, [t for t in to if t], label)
    else:
        raise HTTPException(422, f"unknown channel `{channel}`")

    st.record_publish(week, channel, audience, res["status"], res["detail"], res["live"])
    return res


@app.get("/api/publish/log")
def publish_log(week: str | None = None):
    return {"entries": store().publish_log(week)}


@app.get("/api/schedule")
def get_schedule(week: str):
    """The last saved week, so a refresh does not lose the coordinator's work.

    `null` (not 404) when nothing is saved: a first visit is the normal state, and a 404 here only
    filled the browser console with red that looked like a bug.
    """
    return store().load_schedule(week)


@app.post("/api/schedule")
def put_schedule(body: dict = Body(...)):
    if not body.get("week") or not isinstance(body.get("draft"), list):
        raise HTTPException(422, "`week` and `draft` are required")
    st = store()
    payload = {k: v for k, v in body.items() if k not in ("week", "expected_updated_at")}
    existing = st.load_schedule(body["week"]) or {}
    # Two coordinators used to overwrite each other in silence. The client sends back the updated_at it
    # last saw; a mismatch means someone else saved in between, and the loser is told instead of winning.
    expected = body.get("expected_updated_at")
    if expected and existing and existing.get("updated_at") != expected:
        raise HTTPException(409, f"someone else saved this week at {existing['updated_at']} — reload to see their version before saving again")
    # A partial save must never drop what an earlier full save wrote. Publish sends {draft, published}
    # without stats, and the page refuses to restore a week that has no stats — so a plain replace
    # meant every reload after a publish discarded the coordinator's day and re-drafted.
    merged = {**{k: v for k, v in existing.items() if k not in ("week", "updated_at")}, **payload}
    stamp = st.save_schedule(body["week"], merged)
    return {"saved": body["week"], "rows": len(body["draft"]), "updated_at": stamp, "storage": st.info()}


@app.get("/api/data")
def data():
    """The bundle the app boots from: the stored row for each dataset where one exists, the bundled
    seed JSON where it does not, each tagged with where it came from. One place decides."""
    stored = store().load_datasets()
    out = {}
    for name in DATASETS:
        if name in stored:
            row = stored[name]
            out[name] = {"payload": row["payload"], "source": row["source"], "updated_at": row["updated_at"]}
        else:
            out[name] = {"payload": ingest.seed(name), "source": "seed", "updated_at": None}
    return {"datasets": out}


@app.post("/api/data/reset")
def reset_data():
    """Back to the bundled seed week. Demo safety, and the honest way to undo a bad import."""
    return {"cleared": store().reset_datasets()}


@app.post("/api/data/{name}")
def put_data(name: str, body: dict = Body(...)):
    """Persist what a CSV upload or a Sheets pull produced — called from the confirm step, so nothing
    is stored until the coordinator has seen the check."""
    if name not in DATASETS:
        raise HTTPException(422, f"unknown dataset `{name}`; one of {', '.join(DATASETS)}")
    payload = body.get("payload")
    if not isinstance(payload, (list, dict)) or not payload:
        raise HTTPException(422, "`payload` must be a non-empty list or object")
    store().save_dataset(name, payload, body.get("source") or "csv")
    return {"saved": name, "source": body.get("source") or "csv", "rows": len(payload)}


def store_now() -> str:
    from engine.store import now
    return now()


# ---------- Recovery & Review Copilot ----------

def _agent_ctx(body: dict) -> dict:
    if not isinstance(body.get("draft"), list) or not body["draft"]:
        raise HTTPException(422, "`draft` must be the current non-empty list of draft rows")
    if not isinstance(body.get("smes"), list) or not body["smes"]:
        raise HTTPException(422, "`smes` must be a non-empty list")
    unavailable = None
    if body.get("mode") == "recovery":
        if not body.get("sme_id"):
            raise HTTPException(422, "`sme_id` is required in recovery mode")
        unavailable = {"sme_id": body["sme_id"], "days": body.get("days") or None}
    try:
        return tools.make_ctx(body.get("week") or "this week", body["draft"], body["smes"], body.get("history") or [], unavailable)
    except tools.ToolError as e:
        raise HTTPException(422, str(e))


@app.post("/api/agent/run")
def agent_run(body: dict = Body(...)):
    """Run the copilot. The state rides in the body like every other call; nothing is applied here."""
    mode = body.get("mode")
    if mode not in ("recovery", "review", "chat"):
        raise HTTPException(422, "`mode` must be `recovery`, `review` or `chat`")
    if mode in ("review", "chat") and not (body.get("question") or "").strip():
        raise HTTPException(422, "`question` is required in review and chat mode")
    turns = body.get("turns")
    if turns is not None and not isinstance(turns, list):
        raise HTTPException(422, "`turns` must be a list of {role, content}")
    ctx = _agent_ctx(body)
    return run_agent(ctx, mode, sme_id=body.get("sme_id"), days=body.get("days") or None,
                     question=body.get("question"), turns=turns)


@app.post("/api/agent/apply")
def agent_apply(body: dict = Body(...)):
    """Apply a plan through the existing override path (apply_approvals, actor `agent`), then Stage D
    re-validates the result — the same guarantee every other path gets. Returns the draft payload."""
    if body.get("auto") and os.environ.get("AGENT_AUTO_APPLY") != "1":
        raise HTTPException(403, "autonomous apply is disabled on this server (AGENT_AUTO_APPLY is not 1)")
    plan = body.get("plan")
    if not isinstance(plan, list) or not plan:
        raise HTTPException(422, "`plan` must be a non-empty list of moves")
    # Reschedules and upgrades change the *source* data (the session's hour, the roster's levels), which
    # the client owns and re-runs the whole pipeline over. This route only ever writes staffing moves.
    other = [a for a in plan if isinstance(a, dict) and tools._kind_of(a) != "move"]
    if other:
        raise HTTPException(422, {"message": "this route applies staffing moves only; apply reschedule/upgrade "
                                             "entries to the draft's sessions and roster, then re-run the pipeline",
                                  "kinds": sorted({tools._kind_of(a) for a in other})})
    ctx = _agent_ctx({**body, "mode": "apply"})
    try:
        sim = tools.simulate_plan(ctx, plan)
    except tools.ToolError as e:
        raise HTTPException(422, str(e))
    bad = [v for v in sim["verdicts"] if v["verdict"].startswith("breaks")]
    if bad:   # the plan is stale against this draft — refuse rather than write a known violation
        raise HTTPException(409, {"message": "plan no longer valid against the current draft", "verdicts": bad})
    decisions = [{"session_id": v["session_id"], "action": "override", "override_sme_id": v["to_sme"]} for v in sim["verdicts"]]
    out = apply_approvals(body["draft"], decisions)
    rows = out["final_schedule"]
    for r in rows:   # Stage D re-checks the whole week; strip its old verdicts first so it speaks fresh
        r["flags"] = [f for f in r["flags"] if f["code"] not in tools.STAGE_D_CODES]
    S.stage_d_validate(rows, body["smes"], ctx["hist"])
    tools.reflag_unfilled(rows, body["smes"])   # Stage D skips never-staffed rows; their blocker must survive
    for r in rows:
        r["flags"] = S.sort_flags(r["flags"])
    flags = S.sort_flags([f for r in rows for f in r["flags"]])
    actor = body.get("actor") or "agent"
    log = [{**e, "actor": actor, "reason": next((m.get("reason") for m in plan if m.get("session_id") == e["session_id"]), None)}
           for e in out["override_log"]]
    if log:
        store().record_overrides(body.get("week") or "", log, actor=actor)
    return {"draft": rows, "flags": flags,
            "stats": {"total_sessions": len(rows), "assigned": sum(1 for r in rows if r["sme_id"]),
                      "unfilled": sum(1 for r in rows if not r["sme_id"]),
                      "flags_by_severity": dict(Counter(f["severity"] for f in flags)),
                      "flags_by_code": dict(Counter(f["code"] for f in flags))},
            "override_log": log, "applied": [e["session_id"] for e in log], "diff": len(log), "actor": actor}

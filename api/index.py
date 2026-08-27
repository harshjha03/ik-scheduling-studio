"""FastAPI app served as a Vercel Python function. Stateless: every call carries the state it needs."""
from __future__ import annotations

import os
import sys

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
from engine.run import apply_approvals, run_pipeline  # noqa: E402
from engine.store import store  # noqa: E402

app = FastAPI(title="SME Scheduler API")
_last_run: dict | None = None  # ponytail: warm-instance cache only; frontend is the source of truth


@app.post("/api/run")
def run(body: dict = Body(...)):
    global _last_run
    for key in ("sessions", "smes"):
        if not isinstance(body.get(key), list) or not body[key]:
            raise HTTPException(422, f"`{key}` must be a non-empty list")
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
    return apply_approvals(body["draft"], body.get("decisions") or [])


@app.get("/api/health")
def health():
    return {"ok": True, "llm_configured": llm_configured(),
            "llm_provider": llm_provider() if llm_configured() else None,
            "llm_model": os.environ.get("LLM_MODEL")}


@app.get("/api/integrations")
def integrations():
    """What is actually wired up right now — the UI labels every channel live or simulated."""
    return {"channels": channels.status(), "storage": store().info(),
            "llm": {"live": llm_configured(), "provider": llm_provider() if llm_configured() else None,
                    "model": os.environ.get("LLM_MODEL")}}


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
    payload = {k: v for k, v in body.items() if k != "week"}
    store().save_schedule(body["week"], payload)
    return {"saved": body["week"], "rows": len(body["draft"]), "storage": store().info()}

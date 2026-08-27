"""FastAPI app served as a Vercel Python function. Stateless: every call carries the state it needs."""
from __future__ import annotations

import os
import sys

from fastapi import Body, FastAPI, HTTPException

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import dotenv  # noqa: E402
from engine.llm import llm_configured, llm_provider  # noqa: E402

dotenv.load(os.path.join(ROOT, ".env"))  # local dev only; no-op on Vercel (file is git-ignored)
from engine.run import apply_approvals, run_pipeline  # noqa: E402

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

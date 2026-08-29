"""API-boundary checks that need no server: the FastAPI handlers are plain functions."""
from __future__ import annotations

import json
import os

import pytest

from fastapi import HTTPException  # noqa: E402

# Importing the app loads .env into os.environ; keep that out of every other test module's view.
_env = dict(os.environ)
os.environ["DATABASE_URL"] = ""            # never let .env point a test at a real database
from api import index as api  # noqa: E402
os.environ.clear()
os.environ.update(_env)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rd(name):
    with open(os.path.join(ROOT, "data", f"{name}.json")) as f:
        return json.load(f)


def test_duplicate_session_ids_are_rejected_with_422():
    """QA-03. Two sessions sharing an id collapsed into one shared row object; now the boundary names them."""
    sessions, smes = rd("sessions_next"), rd("smes")
    with pytest.raises(HTTPException) as e:
        api.run({"sessions": sessions + [dict(sessions[0])], "smes": smes, "llm": False})
    assert e.value.status_code == 422
    assert e.value.detail == f"duplicate session id(s): ['{sessions[0]['id']}']"


def test_duplicate_sme_ids_are_rejected_with_422():
    sessions, smes = rd("sessions_next"), rd("smes")
    with pytest.raises(HTTPException) as e:
        api.run({"sessions": sessions, "smes": smes + [dict(smes[3])], "llm": False})
    assert e.value.status_code == 422
    assert e.value.detail == f"duplicate SME id(s): ['{smes[3]['id']}']"


def test_a_clean_payload_still_runs_and_every_draft_row_has_its_own_id():
    sessions, smes = rd("sessions_next"), rd("smes")
    out = api.run({"sessions": sessions, "smes": smes, "history": rd("history"), "llm": False})
    ids = [r["session_id"] for r in out["draft"]]
    assert len(set(ids)) == len(ids) == len(sessions)


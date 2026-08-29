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
    # QA-14: the spread travels with its decomposition, next to the number a reviewer reads first
    st = out["stats"]
    assert st["fairness_note_per_subject"]["DSA"] == (
        f"DSA spread {st['fairness_spread_per_subject']['DSA']} — "
        f"{st['fairness_inherited_per_subject']['DSA']} inherited from prior weeks, "
        f"this week's assignments spread {st['fairness_assigned_per_subject']['DSA']}")
    assert st["fairness_inherited_per_subject"]["DSA"] == 12 and st["fairness_spread_per_subject"]["DSA"] == 14


def test_integrations_reports_no_model_when_no_llm_is_configured(monkeypatch):
    """QA-10. `model` used to echo LLM_MODEL with no key configured."""
    for k in ("LLM_API_KEY", "ANTHROPIC_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_MODEL", "some-model")
    monkeypatch.setattr(api, "store", lambda: type("S", (), {"info": staticmethod(lambda: {"driver": "none"})})())
    llm = api.integrations()["llm"]
    assert llm == {"live": False, "provider": None, "model": None}


@pytest.mark.parametrize("label,mutate,detail", [
    ("history is a string", lambda b: b.update(history="oops"), "`history` must be a list"),
    ("overrides is a dict", lambda b: b.update(overrides={"a": 1}), "`overrides` must be a list"),
    ("session missing start_utc", lambda b: b["sessions"][0].pop("start_utc"), "`sessions[0]` is missing ['start_utc']"),
    ("sme missing training_level", lambda b: b["smes"][2].pop("training_level"), "`smes[2]` is missing ['training_level']"),
])
def test_malformed_shapes_are_422_not_500(label, mutate, detail):
    """QA-04. The four shapes scripts/qa_api_probe.py found returning a bare 500."""
    body = {"sessions": [dict(s) for s in rd("sessions_next")], "smes": [dict(s) for s in rd("smes")], "llm": False}
    mutate(body)
    with pytest.raises(HTTPException) as e:
        api.run(body)
    assert (e.value.status_code, e.value.detail) == (422, detail), label


def test_a_partial_schedule_save_keeps_what_the_full_save_wrote(tmp_path, monkeypatch):
    """A publish saves {draft, published} without stats; the page only restores a week that has stats.
    The save must merge, or every reload after a publish re-drafts from scratch."""
    monkeypatch.setenv("IK_DB_PATH", str(tmp_path / "t.db"))
    from engine import store as store_mod
    monkeypatch.setattr(store_mod, "_store", None)       # fresh sqlite store at the temp path
    api.put_schedule({"week": "2099-W01", "draft": [{"session_id": "x"}], "stats": {"total_sessions": 1}, "flags": []})
    api.put_schedule({"week": "2099-W01", "draft": [{"session_id": "x"}], "published": True})
    saved = api.get_schedule("2099-W01")
    assert saved["published"] is True and saved["stats"] == {"total_sessions": 1} and saved["flags"] == []


def test_a_stale_client_is_refused_instead_of_overwriting(tmp_path, monkeypatch):
    """Two coordinators: the second save must carry the updated_at it last saw, or be told (409)."""
    monkeypatch.setenv("IK_DB_PATH", str(tmp_path / "t.db"))
    from engine import store as store_mod
    monkeypatch.setattr(store_mod, "_store", None)
    first = api.put_schedule({"week": "2099-W02", "draft": [{"session_id": "a"}], "stats": {}})
    assert first["updated_at"]
    # tab 1 saves again on top of what it saw — fine
    second = api.put_schedule({"week": "2099-W02", "draft": [{"session_id": "b"}], "expected_updated_at": first["updated_at"]})
    # tab 2 still holds the first stamp: refused, and told when the other save happened
    monkeypatch.setattr(store_mod, "now", lambda: "2099-01-01T00:00:00+00:00")      # a distinct stamp for the check
    third = api.put_schedule({"week": "2099-W02", "draft": [{"session_id": "c"}], "expected_updated_at": second["updated_at"]})
    with pytest.raises(HTTPException) as e:
        api.put_schedule({"week": "2099-W02", "draft": [{"session_id": "z"}], "expected_updated_at": first["updated_at"]})
    assert e.value.status_code == 409 and third["updated_at"] in e.value.detail
    assert api.get_schedule("2099-W02")["draft"] == [{"session_id": "c"}]
    # a client that never loaded the week (no stamp) is a first writer, not a stale one
    assert api.put_schedule({"week": "2099-W02", "draft": [{"session_id": "d"}]})["updated_at"]

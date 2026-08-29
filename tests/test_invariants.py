"""Property tests: randomised rosters and weeks, asserting what must hold for every draft.

Seeded, so a failure reproduces exactly — the seed is printed in the assertion message. These are the
claims the product rests on, checked against inputs nobody hand-picked:

  1. no SME holds two overlapping sessions
  2. no assignment breaks subject, topic, training level, availability or an external busy block
  3. every unstaffed row carries an UNFILLED flag with a real reason
  4. every flag code is in FLAG_TABLE, and flags are sorted by (priority, session_id)
  5. assigned + unfilled == total_sessions
  6. the run does not raise
"""
from __future__ import annotations

import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import stages as S  # noqa: E402
from engine.run import run_pipeline  # noqa: E402

CASES = int(os.environ.get("QA_FUZZ_CASES", "200"))
SUBJECTS = ["DSA", "ML", "AI", "PM"]
TOPICS = {"DSA": ["Arrays", "Graphs", "DP"], "ML": ["ML Coding", "ML System Design"],
          "AI": ["RAG", "Agents"], "PM": ["Product Sense", "Metrics"]}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def make_world(seed: int):
    """A random but internally coherent week: roster, sessions, history."""
    rng = random.Random(seed)
    n_smes = rng.randint(1, 9)
    smes = []
    for i in range(n_smes):
        subjects = rng.sample(SUBJECTS, rng.randint(1, 2))
        topics = [t for s in subjects for t in TOPICS[s] if rng.random() < 0.7]
        windows = []
        for d in rng.sample(DAYS, rng.randint(0, len(DAYS))):
            start = rng.randint(0, 20)
            windows.append({"weekday": d, "start_utc": f"{start:02d}:00",
                            "end_utc": f"{min(start + rng.randint(1, 10), 23):02d}:30"})
        sme = {"id": f"T{i:02d}", "name": f"SME {i}", "subject": subjects[0], "subjects": subjects,
               "sub_specialty": None, "topics": topics, "training_level": rng.randint(1, 3),
               "timezone": "Asia/Kolkata", "weekly_availability": windows, "preference_notes": ""}
        if rng.random() < 0.3:      # a third of the roster has calendar blocks synced
            sme["external_busy"] = [{"start_utc": f"2026-09-0{rng.randint(7, 9)}T{rng.randint(0, 20):02d}:00:00Z",
                                     "end_utc": f"2026-09-0{rng.randint(7, 9)}T{rng.randint(0, 23):02d}:30:00Z"}
                                    for _ in range(rng.randint(1, 3))]
        smes.append(sme)

    sessions = []
    for i in range(rng.randint(0, 30)):
        subject = rng.choice(SUBJECTS)
        typ = rng.choice(["class", "doubt", "mock"])
        sessions.append({
            "id": f"S{i:03d}", "batch_id": f"{subject}-{rng.randint(1, 3):02d}", "subject": subject,
            "sub_specialty": None if typ == "doubt" else rng.choice(TOPICS[subject]),
            "type": typ,
            "start_utc": f"2026-09-{rng.randint(7, 12):02d}T{rng.randint(0, 22):02d}:30:00Z",
            "duration_min": rng.choice([30, 60, 90]), "mode": "online",
            "required_training_level": rng.randint(1, 3),
        })

    history = [{"sme_id": s["id"], "week": f"2026-W3{w}", "sessions_taught": rng.randint(0, 8),
                "batches": [f"{rng.choice(SUBJECTS)}-01"],
                "per_topic_rating": {t: round(rng.uniform(3, 5), 1) for t in s["topics"]},
                "post_session_rating": None}
               for s in smes for w in range(2, 6) if rng.random() < 0.8]
    return sessions, smes, history


def assert_no_hard_rule_violation(draft: list[dict], smes: list[dict], seed: int | str = "-"):
    """The invariant every stage exists to protect. Reusable — call it after any scenario."""
    by_id = {s["id"]: s for s in smes}
    held: dict[str, list[dict]] = {}
    for row in draft:
        sid = row.get("sme_id")
        if not sid:
            continue
        sme = by_id.get(sid)
        assert sme is not None, f"seed={seed}: {row['session_id']} assigned to unknown SME {sid}"
        start, end = S.session_span(row)
        assert S.teaches_subject(sme, row["subject"]), f"seed={seed}: {row['session_id']} subject"
        assert S.carries_topic(sme, row.get("sub_specialty")), f"seed={seed}: {row['session_id']} topic"
        assert int(sme["training_level"]) >= int(row.get("required_training_level", 1)), \
            f"seed={seed}: {row['session_id']} training level"
        assert S.is_available(sme, start, end), f"seed={seed}: {row['session_id']} availability"
        assert S.busy_at(sme, start, end) is None, f"seed={seed}: {row['session_id']} calendar_busy"
        for other in held.setdefault(sid, []):
            assert not S.overlaps(other, row), \
                f"seed={seed}: {sid} holds {row['session_id']} and {other['session_id']} at once"
        held[sid].append(row)


def assert_result_shape(res: dict, sessions: list[dict], seed: int | str = "-"):
    draft, stats = res["draft"], res["stats"]
    assert len(draft) == len(sessions), f"seed={seed}: {len(draft)} rows for {len(sessions)} sessions"
    assert stats["assigned"] + stats["unfilled"] == stats["total_sessions"], f"seed={seed}: counts"
    assert stats["total_sessions"] == len(sessions), f"seed={seed}: total"
    for row in draft:
        if not row.get("sme_id"):
            unfilled = [f for f in row["flags"] if f["code"] == "UNFILLED"]
            assert unfilled, f"seed={seed}: {row['session_id']} unstaffed with no UNFILLED flag"
            assert unfilled[0]["reason"].strip(), f"seed={seed}: {row['session_id']} empty reason"
        for f in row["flags"]:
            assert f["code"] in S.FLAG_TABLE, f"seed={seed}: unknown flag {f['code']}"
    order = [(f["priority"], f["session_id"]) for f in res["flags"]]
    assert order == sorted(order), f"seed={seed}: flags not sorted by (priority, session_id)"


@pytest.mark.parametrize("seed", range(CASES))
def test_random_weeks_hold_every_invariant(seed):
    sessions, smes, history = make_world(seed)
    res = run_pipeline(sessions, smes, history, [], llm_enabled=False)      # must not raise
    assert_result_shape(res, sessions, seed)
    assert_no_hard_rule_violation(res["draft"], smes, seed)


@pytest.mark.parametrize("seed", range(40))
def test_random_weeks_survive_an_override(seed):
    """Stage E feedback must not be able to push a hard-rule violation into the draft either."""
    sessions, smes, history = make_world(seed + 1000)
    if not sessions or not smes:
        pytest.skip("empty world")
    base = run_pipeline(sessions, smes, history, [], llm_enabled=False)
    staffed = [r for r in base["draft"] if r["sme_id"]]
    if not staffed:
        pytest.skip("nothing staffed to override")
    row = staffed[0]
    other = next((s["id"] for s in smes if s["id"] != row["sme_id"]), None)
    if other is None:
        pytest.skip("single-SME roster")
    ov = [{"session_id": row["session_id"], "batch_id": row["batch_id"],
           "from_sme_id": row["sme_id"], "to_sme_id": other}]
    res = run_pipeline(sessions, smes, history, ov, llm_enabled=False)
    assert_result_shape(res, sessions, f"{seed}+override")
    assert_no_hard_rule_violation(res["draft"], smes, f"{seed}+override")

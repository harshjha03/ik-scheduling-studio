"""Interview Kickstart dataset: 4 courses, 10 batches, 16 SMEs, two weeks (current + next), 4 weeks history.

Run: .venv/bin/python scripts/generate_data.py
Writes data/*.json and asserts the seeded edge cases E1–E6 by running the real engine.
Availability is authored in each SME's LOCAL timezone and converted to UTC for the week's dates.
"""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import stages as S  # noqa: E402
from engine.run import run_pipeline  # noqa: E402

rng = random.Random(11)
UTC = timezone.utc
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]      # the teaching week: no classes run on Sunday
# ...but a UTC availability window can legitimately land on Sunday. A Saturday evening in Los Angeles
# is Sunday morning UTC, and labelling that "Mon" (the old wrap) moved the teacher's hours to the far
# end of the week. The engine's WEEKDAYS includes Sun, so emit the real day.
UTC_DAYS = DAYS + ["Sun"]
HOURS = list(range(8, 20))          # IST teaching hours 08:00–19:00 start
LEVELS = ["beginner", "intermediate", "advanced"]
LEVEL_NUM = {name: i + 1 for i, name in enumerate(LEVELS)}
WEEK_START = {"current": date(2026, 8, 31), "next": date(2026, 9, 7)}
PRIOR_WEEKS = ["2026-W32", "2026-W33", "2026-W34", "2026-W35"]
# W36 (the "current" week) starts 31 Aug, so the four prior weeks walk back from there.
WEEK_START.update({wk: date(2026, 8, 3) + timedelta(days=7 * i) for i, wk in enumerate(PRIOR_WEEKS)})

COURSES = {
    "DSA": {"id": "DSA", "name": "Data Structures & Algorithms", "accent": "#2f5fd0", "tint": "#e9eff8", "deep": "#1f3c78",
            "topics": ["Arrays & Strings", "Graphs & Trees", "Dynamic Programming", "System Design — HLD"]},
    "ML": {"id": "ML", "name": "Machine Learning", "accent": "#5568c4", "tint": "#eceff9", "deep": "#31418f",
           "topics": ["ML System Design", "ML Coding", "Statistics & Probability", "MLOps"]},
    "AI": {"id": "AI", "name": "Applied AI & LLMs", "accent": "#2b8f83", "tint": "#e6f2f0", "deep": "#186e63",
           "topics": ["LLM App Design", "RAG & Retrieval", "Prompt & Eval Systems", "Agentic Patterns"]},
    "PM": {"id": "PM", "name": "Product & Program Management", "accent": "#7d8aa8", "tint": "#eff1f7", "deep": "#4a5670",
           "topics": ["Product Sense", "Execution & Metrics", "Behavioral & Leadership", "Program Strategy"]},
}

# id, name, courses, topics, level, to_upgrade, rating, preferred, city, timezone
SME_DEFS = [
    ("T01", "Ananya Iyer", ["DSA"], ["Arrays & Strings", "Graphs & Trees", "Dynamic Programming"], "advanced", 0, 4.8, 6, "Bengaluru", "Asia/Kolkata"),
    ("T02", "Rohan Mehta", ["DSA"], ["Arrays & Strings", "Dynamic Programming"], "intermediate", 8, 4.5, 5, "Pune", "Asia/Kolkata"),
    ("T03", "Kavya Nair", ["DSA"], ["Graphs & Trees", "Arrays & Strings"], "advanced", 0, 4.7, 4, "Kochi", "Asia/Kolkata"),
    ("T04", "Arjun Sharma", ["DSA"], ["System Design — HLD", "Graphs & Trees"], "advanced", 0, 4.9, 5, "Gurugram", "Asia/Kolkata"),
    ("T05", "Neha Kulkarni", ["DSA"], ["System Design — HLD"], "intermediate", 4, 4.4, 4, "Mumbai", "Asia/Kolkata"),
    ("T06", "Vikram Rao", ["DSA"], ["Dynamic Programming", "Arrays & Strings"], "beginner", 3, 4.2, 3, "Hyderabad", "Asia/Kolkata"),
    ("T07", "Priya Menon", ["ML"], ["ML System Design", "ML Coding"], "advanced", 0, 4.8, 5, "Bengaluru", "Asia/Kolkata"),
    ("T08", "Sameer Khan", ["ML"], ["ML Coding", "Statistics & Probability"], "intermediate", 6, 4.5, 5, "Delhi", "Asia/Kolkata"),
    ("T09", "Divya Pillai", ["ML"], ["MLOps", "ML System Design"], "advanced", 0, 4.6, 3, "San Francisco", "America/Los_Angeles"),
    ("T10", "Aditya Verma", ["AI"], ["LLM App Design", "Agentic Patterns"], "advanced", 0, 4.9, 5, "Bengaluru", "Asia/Kolkata"),
    ("T11", "Meera Joshi", ["AI"], ["RAG & Retrieval", "Prompt & Eval Systems"], "intermediate", 2, 4.6, 4, "London", "Europe/London"),
    ("T12", "Karan Bose", ["AI"], ["Prompt & Eval Systems", "LLM App Design"], "intermediate", 5, 4.3, 4, "Kolkata", "Asia/Kolkata"),
    ("T13", "Sneha Reddy", ["PM"], ["Product Sense", "Execution & Metrics"], "advanced", 0, 4.7, 5, "Chennai", "Asia/Kolkata"),
    ("T14", "Rahul Desai", ["PM", "DSA"], ["Behavioral & Leadership", "Arrays & Strings"], "intermediate", 7, 4.4, 4, "Pune", "Asia/Kolkata"),
    ("T15", "Ishita Ghosh", ["PM"], ["Program Strategy", "Product Sense"], "advanced", 0, 4.8, 4, "Bengaluru", "Asia/Kolkata"),
    ("T16", "Farhan Sheikh", ["ML", "AI"], ["ML Coding", "RAG & Retrieval"], "intermediate", 3, 4.5, 5, "Dubai", "Asia/Dubai"),
]

# local windows per weekday index (0=Mon .. 5=Sat): (from, to) in the SME's own timezone
LOCAL_AVAIL = {
    "T01": {0: ("08:00", "20:00"), 1: ("08:00", "20:00"), 2: ("08:00", "20:00"), 3: ("08:00", "20:00"), 4: ("08:00", "20:00"), 5: ("08:00", "14:00")},
    "T02": {0: ("10:00", "20:00"), 1: ("10:00", "20:00"), 2: ("10:00", "20:00"), 3: ("10:00", "20:00"), 4: ("10:00", "20:00")},
    "T03": {0: ("08:00", "18:00"), 1: ("08:00", "18:00"), 2: ("08:00", "18:00"), 4: ("08:00", "18:00"), 5: ("08:00", "16:00")},
    "T04": {0: ("08:00", "20:00"), 1: ("08:00", "20:00"), 2: ("08:00", "20:00"), 3: ("08:00", "20:00"), 4: ("08:00", "20:00"), 5: ("10:00", "18:00")},
    "T05": {1: ("12:00", "20:00"), 2: ("12:00", "20:00"), 3: ("12:00", "20:00"), 4: ("12:00", "20:00"), 5: ("12:00", "18:00")},
    "T06": {0: ("14:00", "20:00"), 2: ("14:00", "20:00"), 4: ("14:00", "20:00"), 5: ("10:00", "18:00")},
    "T07": {0: ("08:00", "20:00"), 1: ("08:00", "20:00"), 2: ("08:00", "20:00"), 3: ("08:00", "20:00"), 4: ("08:00", "20:00")},
    "T08": {0: ("12:00", "20:00"), 1: ("12:00", "20:00"), 2: ("12:00", "20:00"), 3: ("12:00", "20:00"), 4: ("12:00", "20:00")},
    # E6: San Francisco evening shift -> IST 06:30–11:30
    "T09": {1: ("18:00", "23:00"), 2: ("18:00", "23:00"), 3: ("18:00", "23:00"), 4: ("18:00", "23:00"), 5: ("18:00", "23:00")},
    "T10": {0: ("08:00", "20:00"), 1: ("08:00", "20:00"), 2: ("08:00", "20:00"), 3: ("08:00", "20:00"), 4: ("08:00", "20:00")},
    # E6: London -> IST 13:30–20:30
    "T11": {0: ("09:00", "16:00"), 1: ("09:00", "16:00"), 2: ("09:00", "16:00"), 3: ("09:00", "16:00"), 4: ("09:00", "16:00")},
    "T12": {0: ("10:00", "20:00"), 1: ("10:00", "20:00"), 2: ("10:00", "20:00"), 3: ("10:00", "20:00"), 4: ("10:00", "20:00"), 5: ("10:00", "16:00")},
    "T13": {0: ("08:00", "18:00"), 1: ("08:00", "18:00"), 2: ("08:00", "18:00"), 3: ("08:00", "18:00"), 4: ("08:00", "18:00")},
    "T14": {0: ("08:00", "18:00"), 1: ("08:00", "18:00"), 2: ("08:00", "18:00"), 3: ("08:00", "18:00"), 4: ("08:00", "18:00"), 5: ("08:00", "14:00")},
    "T15": {0: ("09:00", "19:00"), 1: ("09:00", "19:00"), 2: ("09:00", "19:00"), 3: ("09:00", "19:00"), 4: ("09:00", "19:00")},
    # E6: Dubai -> IST 11:30–20:30
    "T16": {0: ("10:00", "19:00"), 1: ("10:00", "19:00"), 2: ("10:00", "19:00"), 3: ("10:00", "19:00"), 4: ("10:00", "19:00"), 5: ("10:00", "17:00")},
}

BATCHES = [
    {"id": "DSA-01", "course": "DSA", "level": "advanced", "learners": 42, "per_week": 5, "weeks_done": 9, "weeks_total": 14, "started": "15 Jun 2026"},
    {"id": "DSA-02", "course": "DSA", "level": "intermediate", "learners": 38, "per_week": 5, "weeks_done": 6, "weeks_total": 14, "started": "13 Jul 2026"},
    {"id": "DSA-03", "course": "DSA", "level": "beginner", "learners": 44, "per_week": 4, "weeks_done": 4, "weeks_total": 16, "started": "27 Jul 2026"},
    {"id": "DSA-04", "course": "DSA", "level": "intermediate", "learners": 36, "per_week": 4, "weeks_done": 2, "weeks_total": 14, "started": "10 Aug 2026"},
    {"id": "ML-01", "course": "ML", "level": "advanced", "learners": 31, "per_week": 5, "weeks_done": 8, "weeks_total": 12, "started": "22 Jun 2026"},
    {"id": "ML-02", "course": "ML", "level": "intermediate", "learners": 28, "per_week": 4, "weeks_done": 3, "weeks_total": 12, "started": "3 Aug 2026"},
    {"id": "AI-01", "course": "AI", "level": "intermediate", "learners": 26, "per_week": 4, "weeks_done": 5, "weeks_total": 10, "started": "20 Jul 2026"},
    {"id": "AI-02", "course": "AI", "level": "beginner", "learners": 22, "per_week": 3, "weeks_done": 1, "weeks_total": 10, "started": "17 Aug 2026"},
    {"id": "PM-01", "course": "PM", "level": "advanced", "learners": 19, "per_week": 4, "weeks_done": 7, "weeks_total": 12, "started": "6 Jul 2026"},
    {"id": "PM-02", "course": "PM", "level": "beginner", "learners": 21, "per_week": 3, "weeks_done": 2, "weeks_total": 12, "started": "10 Aug 2026"},
]

WEEKS = {
    "current": {"key": "current", "label": "This week", "range": "31 Aug – 5 Sep 2026", "locked": True, "iso": "2026-W36"},
    "next": {"key": "next", "label": "Next week", "range": "7 Sep – 12 Sep 2026", "locked": False, "iso": "2026-W37"},
}
TYPE_LABEL = {"class": "Class", "doubt": "Doubt session", "mock": "Mock interview"}


def to_utc_windows(local: dict, tz: str, week: str) -> list[dict]:
    """Convert local weekday windows to UTC weekday + HH:MM windows for that week's real dates."""
    out = []
    for d in range(6):
        win = local.get(d)
        if not win:
            continue
        day = WEEK_START[week] + timedelta(days=d)
        s = datetime.combine(day, time.fromisoformat(win[0]), ZoneInfo(tz)).astimezone(UTC)
        e = datetime.combine(day, time.fromisoformat(win[1]), ZoneInfo(tz)).astimezone(UTC)
        out.append({"weekday": UTC_DAYS[s.weekday()],
                    "start_utc": s.strftime("%H:%M"), "end_utc": e.strftime("%H:%M"),
                    "local": f"{win[0]}–{win[1]} {tz}"})
    return out


# Real inboxes for end-to-end testing, by SME id. Everyone else gets an undeliverable placeholder,
# so a stray live send can only ever reach someone who opted in here. T14 is the SME persona the UI
# logs in as (`meta.me`), so it is the one whose schedule you can actually check in a real inbox.
REAL_CONTACTS = {"T14": "tushartimes112@gmail.com"}


def contact_for(sid: str, name: str) -> tuple[str, str]:
    """Deterministic contacts. Anything not in REAL_CONTACTS is deliberately undeliverable:
    `.example` is reserved (RFC 2606) and the numbers are placeholders. Publishing for real needs
    PUBLISH_REDIRECT_TO / PUBLISH_REDIRECT_SMS_TO, or these fields replaced with the real roster."""
    email = REAL_CONTACTS.get(sid) or f"{name.lower().replace(' ', '.')}@ik.example"
    return email, f"+9199{int(sid[1:]):08d}"


def build_batches() -> list[dict]:
    """Where a cohort is reachable: `contact_email` is the batch distribution list, `calendar_id`
    the cohort calendar to publish into (null = the shared GOOGLE_CALENDAR_ID). No group phone
    number exists for students, so batch SMS honestly reports no recipients."""
    return [{**b, "contact_email": f"{b['id'].lower()}@ik.example",
             "contact_phone": None, "calendar_id": None} for b in BATCHES]


def build_smes(week: str = "next") -> list[dict]:
    smes = []
    for sid, name, courses, topics, level, to_up, rating, preferred, city, tz in SME_DEFS:
        email, phone = contact_for(sid, name)
        smes.append({
            "id": sid, "name": name, "email": email, "phone": phone,
            "subject": courses[0], "subjects": courses,           # engine: multi-course
            "sub_specialty": None, "topics": topics,               # engine: multi-topic
            "training_level": LEVEL_NUM[level], "level": level, "to_upgrade": to_up,
            "timezone": tz, "city": city, "rating": rating, "preferred": preferred, "leave": None,
            "weekly_availability": to_utc_windows(LOCAL_AVAIL[sid], tz, week),
            "preference_notes": PREF_NOTES[sid], "history": [],
        })
    return smes


PREF_NOTES = {
    "T01": "Happy to take extra load; prefers evening slots and advanced batches.",
    "T02": "Prefers morning slots; finds DSA-04 challenging.",
    "T03": "Enjoys graph-heavy sessions; would rather not teach two classes back to back.",
    "T04": "Strong on system design; prefers afternoons and mock interviews.",
    "T05": "Prefers a steady weekly load; enjoys DSA-02.",
    "T06": "New to advanced material; keen on more doubt sessions.",
    "T07": "Prefers ML system design over coding drills; enjoys continuity with ML-01.",
    "T08": "Prefers late slots; asked for fewer doubt sessions.",
    "T09": "Works from San Francisco; only overlaps IST mornings.",
    "T10": "Enjoys agentic patterns; prefers online mode and AI-01.",
    "T11": "Works from London; only overlaps IST afternoons and evenings.",
    "T12": "Flexible on timing; likes prompt and evaluation topics.",
    "T13": "Prefers product sense sessions; strong with PM-01.",
    "T14": "Teaches behavioural PM and DSA arrays; prefers a lighter week.",
    "T15": "Prefers programme strategy; finds early mornings hard.",
    "T16": "Based in Dubai; available IST late morning to evening only.",
}

# ---------------- session placement ----------------

def cell_dt(week: str, day: int, hour: int) -> datetime:
    """IST day+hour -> UTC datetime for that week's date."""
    d = WEEK_START[week] + timedelta(days=day)
    return datetime.combine(d, time(hour, 0), ZoneInfo("Asia/Kolkata")).astimezone(UTC)


def eligible_ids(smes: list[dict], course: str, topic: str | None, level: int, week: str, day: int, hour: int) -> list[str]:
    start = cell_dt(week, day, hour)
    end = start + timedelta(minutes=60)
    out = []
    for s in smes:
        if not S.teaches_subject(s, course) or not S.carries_topic(s, topic):
            continue
        if s["training_level"] < level or not S.is_available(s, start, end):
            continue
        out.append(s["id"])
    return out


def session_id(week: str, batch_id: str, k: int) -> str:
    """Globally unique across weeks: the same batch runs in both, and the UI keys per-row state
    (approved / changed / decisions / pending) by session id alone."""
    return f"{WEEKS[week]['iso'].split('-')[1]}-{batch_id}-{k}"


def make_session(week: str, batch: dict, k: int, topic: str | None, typ: str, day: int, hour: int, level: int) -> dict:
    return {
        "id": session_id(week, batch["id"], k), "batch_id": batch["id"], "subject": batch["course"],
        "sub_specialty": topic, "type": typ,
        "start_utc": cell_dt(week, day, hour).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_min": 60, "mode": "online", "required_training_level": level,
        "day": day, "hour": hour,           # display convenience; engine ignores extras
    }


# Forced placements for the NEXT week (the seeded edge cases). (batch, k) -> (topic, type, day, hour, level)
FORCED_NEXT = {
    # E1 unfilled: two ML System Design classes in one hour; T07 takes one, T09 (SF) is outside her window
    ("ML-01", 0): ("ML System Design", "class", 0, 16, 3),
    ("ML-02", 0): ("ML System Design", "class", 0, 16, 2),
    # E2 training gap: advanced DP class on Saturday afternoon — only Vikram Rao (beginner) is free
    ("DSA-01", 1): ("Dynamic Programming", "class", 5, 15, 3),
    # E3 conflict pressure: two intermediate Arrays classes at the same hour
    ("DSA-02", 0): ("Arrays & Strings", "class", 1, 11, 2),
    ("DSA-04", 0): ("Arrays & Strings", "class", 1, 11, 2),
    # E5 fairness: advanced DP at Mon 19:00 — only Ananya Iyer qualifies
    ("DSA-01", 0): ("Dynamic Programming", "class", 0, 19, 3),
}


def plan_week(week: str, smes: list[dict], batches: list[dict]) -> list[dict]:
    """Place every batch's sessions so a greedy assignment exists (with slack), honouring FORCED_NEXT."""
    forced = FORCED_NEXT if week == "next" else {}
    sessions: list[dict] = []
    committed: dict[tuple[int, int], set[str]] = {}      # cell -> sme ids reserved by placement
    per_cell_batches: dict[tuple[int, int], set[str]] = {}
    per_day_batch: dict[tuple[int, str], int] = {}

    def place(batch, k, topic, typ, level, day, hour, slack) -> bool:
        cell = (day, hour)
        if batch["id"] in per_cell_batches.get(cell, set()):
            return False
        if per_day_batch.get((day, batch["id"]), 0) >= 2:
            return False
        free = [i for i in eligible_ids(smes, batch["course"], topic, level, week, day, hour)
                if i not in committed.get(cell, set())]
        if len(free) < 1 + slack:
            return False
        committed.setdefault(cell, set()).add(free[0])
        per_cell_batches.setdefault(cell, set()).add(batch["id"])
        per_day_batch[(day, batch["id"])] = per_day_batch.get((day, batch["id"]), 0) + 1
        sessions.append(make_session(week, batch, k, topic, typ, day, hour, level))
        return True

    # 1) forced edge-case sessions (no slack requirement — that is the point)
    for (bid, k), (topic, typ, day, hour, level) in forced.items():
        batch = next(b for b in batches if b["id"] == bid)
        cell = (day, hour)
        free = [i for i in eligible_ids(smes, batch["course"], topic, level, week, day, hour)
                if i not in committed.get(cell, set())]
        if free:
            committed.setdefault(cell, set()).add(free[0])
        per_cell_batches.setdefault(cell, set()).add(batch["id"])
        per_day_batch[(day, batch["id"])] = per_day_batch.get((day, batch["id"]), 0) + 1
        sessions.append(make_session(week, batch, k, topic, typ, day, hour, level))

    # 2) everything else, shuffled cells, requiring one spare eligible SME so the engine has a choice
    todo = []
    for batch in batches:
        level = LEVEL_NUM[batch["level"]]
        # a batch can only run topics somebody at its level actually carries
        topics = [t for t in COURSES[batch["course"]]["topics"]
                  if any(S.teaches_subject(s, batch["course"]) and S.carries_topic(s, t) and s["training_level"] >= level
                         for s in smes)]
        assert topics, f"{batch['id']} has no teachable topic at level {batch['level']}"
        # rotate topics per class so every topic of the course gets taught across the batches
        offset = [b["id"] for b in batches if b["course"] == batch["course"]].index(batch["id"])
        c = 0
        for k in range(batch["per_week"]):
            typ = "mock" if k == batch["per_week"] - 1 else "doubt" if k == batch["per_week"] - 2 else "class"
            topic = None if typ in ("doubt", "mock") else topics[(offset + c) % len(topics)]
            if typ == "class":
                c += 1
            if (batch["id"], k) in forced:
                continue
            todo.append((batch, k, topic, typ, level))
    rng.shuffle(todo)
    for batch, k, topic, typ, level in todo:
        cells = [(d, h) for d in range(6) for h in HOURS]
        rng.shuffle(cells)
        for slack in (1, 0):                     # prefer cells with a spare SME; accept tight ones last
            if any(place(batch, k, topic, typ, level, d, h, slack) for d, h in cells):
                break
        else:
            raise SystemExit(f"could not place {batch['id']}-{k} ({topic or typ})")
    sessions.sort(key=lambda s: (s["start_utc"], s["id"]))
    return sessions


# ---------------- history ----------------

# Prior weekly load. Uniform inside every pool so the fairness band starts neutral and the current
# draft is what moves it — except T01, the deliberate overload of E5.
BASE_LOAD = 4
HISTORY_LOAD = {"T01": 8}


def build_history(smes: list[dict], batches: list[dict]) -> list[dict]:
    """4 prior weeks. Home batches drive continuity; T01 is deliberately overloaded (E5)."""
    home: dict[str, set[str]] = {s["id"]: set() for s in smes}
    for b in batches:
        pool = [s for s in smes if S.teaches_subject(s, b["course"])]
        for i in range(min(2, len(pool))):
            home[pool[(batches.index(b) + i) % len(pool)]["id"]].add(b["id"])
    home["T01"] |= {"DSA-01", "DSA-02"}          # continuity with both E3 batches
    records = []
    for s in smes:
        base = {t: round(min(5.0, max(1.0, s["rating"] + rng.choice([-0.2, -0.1, 0, 0.1]))), 1) for t in s["topics"]}
        base["doubt"] = round(min(5.0, s["rating"] - 0.1), 1)
        for c in s["subjects"]:
            base[c.lower()] = round(min(5.0, s["rating"]), 1)     # mock sessions score on the course
        for wk in PRIOR_WEEKS:
            taught = HISTORY_LOAD.get(s["id"], BASE_LOAD)
            rec = {"week": wk, "sessions_taught": taught, "batches": sorted(home[s["id"]]),
                   "per_topic_rating": dict(base), "post_session_rating": None}
            s["history"].append(rec)
            records.append({"sme_id": s["id"], **rec})
    return records


# ---------------- past weeks as real class rows ----------------

# One deliberate drop per past week, so the history module has real cancellations and merges to show
# and the new paths have data behind them before anyone touches the live week.
PAST_DROPS = {
    "2026-W33": ("cancel", "DSA-03", "Teacher ill and no cover was free at that hour."),
    "2026-W35": ("merge", "ML-02", None),
}


def build_past_week(wk: str, smes: list[dict], batches: list[dict], history: list[dict]) -> list[dict]:
    """The real class rows for one past week, laid out so they reproduce `history` exactly.

    History is the source of truth here, not the rows. The seed's fairness edge cases are calibrated
    on those counts (T01's deliberate overload is the whole of E5), and deriving them from freshly
    placed rows would move the numbers the demo is tuned against. So the rows are built to match, and
    test_past_weeks_reconcile_with_history is what holds the two together from here on.
    """
    recs = {r["sme_id"]: r for r in history if r["week"] == wk}
    by_batch = {b["id"]: b for b in batches}
    by_sme = {s["id"]: s for s in smes}
    sme_busy: dict[tuple[int, int], set[str]] = {}
    batch_busy: dict[tuple[int, int], set[str]] = {}
    cells = [(d, h) for d in range(len(DAYS)) for h in HOURS]
    sessions: list[dict] = []
    seq: dict[str, int] = {}

    def place(sid: str, batch_id: str, k: int) -> bool:
        sme, batch = by_sme[sid], by_batch[batch_id]
        topics = [t for t in COURSES[batch["course"]]["topics"] if S.carries_topic(sme, t)]
        typ = ("mock" if k % 4 == 3 else "doubt" if k % 4 == 2 else "class")
        topic = None if typ == "doubt" else (topics[k % len(topics)] if topics else None)
        level = min(LEVEL_NUM[batch["level"]], sme["training_level"])
        for d, h in cells:
            if sid in sme_busy.get((d, h), set()) or batch_id in batch_busy.get((d, h), set()):
                continue
            start = cell_dt(wk, d, h)
            if not S.is_available(sme, start, start + timedelta(minutes=60)):
                continue
            sme_busy.setdefault((d, h), set()).add(sid)
            batch_busy.setdefault((d, h), set()).add(batch_id)
            n = seq[batch_id] = seq.get(batch_id, -1) + 1
            sessions.append({
                "id": f"{wk.split('-')[1]}-{batch_id}-{n}", "batch_id": batch_id, "subject": batch["course"],
                "sub_specialty": topic, "type": typ,
                "start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "duration_min": 60, "mode": "online",
                "required_training_level": level, "day": d, "hour": h,
                "sme_id": sid, "sme_name": sme["name"],       # a past week is settled, not drafted
            })
            return True
        return False

    # heaviest teacher first: T01 carries twice everyone else's load and needs the pick of the grid
    for sid in sorted(recs, key=lambda x: (-recs[x]["sessions_taught"], x)):
        rec = recs[sid]
        homes = [b for b in rec["batches"] if b in by_batch
                 and S.teaches_subject(by_sme[sid], by_batch[b]["course"])]
        if not homes:
            homes = [b["id"] for b in batches if S.teaches_subject(by_sme[sid], b["course"])][:2]
        for k in range(int(rec["sessions_taught"])):
            if not place(sid, homes[k % len(homes)], k):
                raise AssertionError(f"{wk}: no free cell for {sid} class {k + 1}")

    kind_batch = PAST_DROPS.get(wk)
    if kind_batch:
        kind, batch_id, why = kind_batch
        batch = by_batch[batch_id]
        # the dropped class is extra: nobody taught it, so it is not in anyone's sessions_taught
        host = next((x for x in sessions if x["batch_id"] != batch_id and x["subject"] == batch["course"]), None)
        n = seq[batch_id] = seq.get(batch_id, -1) + 1
        d, h = (host["day"], host["hour"]) if kind == "merge" and host else (4, 18)
        row = {"id": f"{wk.split('-')[1]}-{batch_id}-{n}", "batch_id": batch_id, "subject": batch["course"],
               "sub_specialty": host["sub_specialty"] if kind == "merge" and host else COURSES[batch["course"]]["topics"][0],
               "type": "class", "start_utc": cell_dt(wk, d, h).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "duration_min": 60, "mode": "online", "required_training_level": 1, "day": d, "hour": h,
               "sme_id": None, "sme_name": None}
        if kind == "cancel":
            row["cancelled"] = {"reason": why, "by": "Ops", "at": f"{WEEK_START[wk]}T09:00:00Z"}
        elif host:
            row["merged_into"] = host["id"]
        sessions.append(row)
    sessions.sort(key=lambda x: (x["start_utc"], x["id"]))
    return sessions


def reconcile_history(past: dict[str, list[dict]], history: list[dict], smes: list[dict]) -> None:
    """Write back the batches the past rows actually show.

    `build_history` seeds a home-batch set for continuity and leaves a few teachers with none — but a
    class always belongs to a batch, so a record saying "4 sessions, no batches" cannot be true once
    the rows exist. The rows are the concrete thing; the record follows them. Mutates in place.
    """
    per_sme = {s["id"]: s for s in smes}
    for wk, rows in past.items():
        taught: dict[str, set[str]] = {}
        for r in rows:
            if S.is_live(r) and r.get("sme_id"):
                taught.setdefault(r["sme_id"], set()).add(r["batch_id"])
        for rec in history:
            if rec["week"] != wk:
                continue
            rec["batches"] = sorted(set(rec["batches"]) | taught.get(rec["sme_id"], set()))
            for own in per_sme[rec["sme_id"]]["history"]:
                if own["week"] == wk:
                    own["batches"] = list(rec["batches"])


# ---------------- self check ----------------

def self_check(cur, nxt, smes, history):
    N = lambda batch_id, k: session_id("next", batch_id, k)  # noqa: E731
    cur_res = run_pipeline(cur, smes, history, [], llm_enabled=False)
    cur_rows = {r["session_id"]: r for r in cur_res["draft"]}
    assert not ({s["id"] for s in cur} & {s["id"] for s in nxt}), "session ids must not collide across weeks"
    unfilled_cur = [sid for sid, r in cur_rows.items() if not r["sme_id"]]
    assert not unfilled_cur, f"current week must be fully staffed, unfilled: {unfilled_cur}"
    assert not any(f["code"] == "LLM_FALLBACK" for f in cur_res["flags"]), "llm_enabled=False must not flag fallbacks"

    # next week is drafted on top of the settled current week's load
    res = run_pipeline(nxt, smes, history, [], llm_enabled=True)
    rows = {r["session_id"]: r for r in res["draft"]}
    unfilled = {sid for sid, r in rows.items() if not r["sme_id"]}

    # E1 — ML-02 has no eligible SME (T07 booked on ML-01, T09 outside her San Francisco window)
    e1 = rows[N("ML-02", 0)]
    assert not e1["sme_id"], "E1 should be unfilled"
    assert rows[N("ML-01", 0)]["sme_id"] == "T07", rows[N("ML-01", 0)]["sme_id"]
    r1 = e1["flags"][0]["reason"]
    assert "unavailable" in r1 and N("ML-01", 0) in r1, r1
    # E2 — advanced DP on Saturday: only a beginner carries it
    e2 = rows[N("DSA-01", 1)]
    assert not e2["sme_id"], "E2 should be unfilled"
    assert "training level" in e2["flags"][0]["reason"], e2["flags"][0]["reason"]
    assert "Vikram Rao" in e2["flags"][0]["reason"], e2["flags"][0]["reason"]
    # E3 — concurrent Arrays classes: one SME cannot take both; the second is contested
    a, b = rows[N("DSA-02", 0)], rows[N("DSA-04", 0)]
    assert a["sme_id"] and b["sme_id"] and a["sme_id"] != b["sme_id"], (a["sme_id"], b["sme_id"])
    assert any(e["rule"].startswith("overlap:") for e in b["eliminated"] + a["eliminated"]), "expected an overlap elimination"
    # E4 — at least five sessions went to the exception queue
    ties = [r for r in rows.values() if any(f["code"] in ("TIE_ESCALATED", "LLM_FALLBACK") for f in r["flags"])]
    assert len(ties) >= 5, f"only {len(ties)} ties"
    # E5 — Ananya Iyer forced onto the Monday 19:00 advanced DP class, and flagged for load
    e5 = rows[N("DSA-01", 0)]
    assert e5["sme_id"] == "T01", e5["sme_id"]
    assert any(f["code"] == "FAIRNESS_VIOLATION" for f in e5["flags"]), [f["code"] for f in e5["flags"]]
    dsa_counts = {s["id"]: sum(1 for r in rows.values() if r["sme_id"] == s["id"])
                  for s in smes if S.teaches_subject(s, "DSA")}
    # fairness visibly diverts load: despite being the most available advanced DSA SME, the
    # already-overloaded T01 is not the busiest this week and stays under her stated preference
    assert dsa_counts["T01"] < max(dsa_counts.values()), dsa_counts
    assert dsa_counts["T01"] <= next(s["preferred"] for s in smes if s["id"] == "T01"), dsa_counts
    # E6 — the three non-IST SMEs convert correctly and are actually used
    by_id = {s["id"]: s for s in smes}
    assert by_id["T09"]["weekly_availability"][0]["start_utc"] == "01:00"   # 18:00 PDT
    assert by_id["T11"]["weekly_availability"][0]["start_utc"] == "08:00"   # 09:00 BST
    assert by_id["T16"]["weekly_availability"][0]["start_utc"] == "06:00"   # 10:00 GST
    for sid in ("T09", "T11", "T16"):
        assert any(r["sme_id"] == sid for r in list(rows.values()) + list(cur_rows.values())), f"{sid} never used"
    # hard rules hold everywhere
    assert not any(f["code"] == "HARD_CONFLICT" for f in res["flags"] + cur_res["flags"])
    assert unfilled == {N("ML-02", 0), N("DSA-01", 1)}, unfilled

    print(json.dumps({k: v for k, v in res["stats"].items() if k != "llm"}, indent=1))
    print(f"current week: {len(cur)} sessions, 0 unfilled | next week: {len(nxt)} sessions, "
          f"{len(unfilled)} unfilled, {len(ties)} queued | DSA load {dsa_counts}")


def main():
    smes = build_smes("next")
    history = build_history(smes, BATCHES)
    nxt = plan_week("next", smes, BATCHES)
    # the current week uses the same people with that week's dates for their availability
    smes_cur = build_smes("current")
    for s, sc in zip(smes, smes_cur):
        sc["history"] = s["history"]
    cur = plan_week("current", smes_cur, BATCHES)
    past = {wk: build_past_week(wk, build_smes(wk), BATCHES, history) for wk in PRIOR_WEEKS}
    reconcile_history(past, history, smes)
    for s, sc in zip(smes, smes_cur):
        sc["history"] = s["history"]
    self_check(cur, nxt, smes, history)

    out = os.path.join(ROOT, "data")
    os.makedirs(out, exist_ok=True)
    past_meta = []
    for wk in PRIOR_WEEKS:
        a = WEEK_START[wk]
        b = a + timedelta(days=5)
        past_meta.append({"iso": wk, "label": f"Week {wk.split('-W')[1]}",
                          "range": f"{a.day} {a:%b} – {b.day} {b:%b %Y}"})
    files = {
        **{f"sessions_{wk.split('-')[1].lower()}": rows for wk, rows in past.items()},
        "past_weeks": past_meta,
        "sessions_current": cur, "sessions_next": nxt, "smes": smes, "smes_current": smes_cur,
        "history": history, "batches": build_batches(), "courses": COURSES, "weeks": WEEKS,
        "meta": {"days": DAYS, "hours": [HOURS[0], HOURS[-1] + 1], "levels": LEVELS,
                 "type_label": TYPE_LABEL, "me": "T14", "my_batch": "DSA-01"},
    }
    for name, obj in files.items():
        with open(os.path.join(out, f"{name}.json"), "w") as f:
            json.dump(obj, f, indent=1)
    for stale in ("sessions.json", "edge_cases.json"):
        p = os.path.join(out, stale)
        if os.path.exists(p):
            os.remove(p)
    print("wrote data/*.json")


if __name__ == "__main__":
    main()

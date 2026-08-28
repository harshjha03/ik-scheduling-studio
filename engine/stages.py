"""Deterministic stages A, B, D, E plus shared helpers. Pure functions, no I/O."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MARGIN = 0.15
EPS = 1e-6
FAIRNESS_BAND = 2
PAST_WEEKS_FOR_LOAD = 3
OVERRIDE_PENALTY = -0.2
OVERRIDE_BONUS = 0.1

# code -> (priority, severity). Sort/color by priority.
FLAG_TABLE = {
    "UNFILLED": (1, "critical"),
    "HARD_CONFLICT": (2, "critical"),
    "RULE_OVERRIDE_RISK": (3, "high"),
    "FAIRNESS_VIOLATION": (4, "medium"),
    "TIE_ESCALATED": (5, "info"),
    "LLM_FALLBACK": (6, "info"),
}
LLM_FALLBACK_REASON = "LLM unavailable — resolved by deterministic score."

RULE_LABELS = {
    "subject": "subject expertise",
    "sub_specialty": "sub-specialty expertise",
    "training_level": "training level requirement",
    "availability": "availability window",
    "calendar_busy": "calendar conflict",
}


def make_flag(code: str, session_id: str, reason: str, sme_id: str | None = None) -> dict:
    priority, severity = FLAG_TABLE[code]
    return {"code": code, "priority": priority, "severity": severity,
            "session_id": session_id, "sme_id": sme_id, "reason": reason}


def sort_flags(flags: list[dict]) -> list[dict]:
    return sorted(flags, key=lambda f: (f["priority"], f["session_id"]))


def rule_label(rule: str) -> str:
    if rule.startswith("overlap:"):
        return f"time overlap with {rule.split(':', 1)[1]}"
    return RULE_LABELS.get(rule, rule)


# ---------- time helpers ----------

def parse_utc(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def session_span(session: dict) -> tuple[datetime, datetime]:
    start = parse_utc(session["start_utc"])
    return start, start + timedelta(minutes=int(session["duration_min"]))


def fmt_ist(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%a %H:%M IST")


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def is_available(sme: dict, start: datetime, end: datetime) -> bool:
    wd = WEEKDAYS[start.weekday()]
    s_min = start.hour * 60 + start.minute
    e_min = s_min + int((end - start).total_seconds() // 60)
    for w in sme.get("weekly_availability", []):
        if w["weekday"] == wd and _minutes(w["start_utc"]) <= s_min and e_min <= _minutes(w["end_utc"]):
            return True
    return False


def busy_overlap(block: dict, start: datetime, end: datetime) -> bool:
    """Does an external calendar block collide with this session's span?

    `weekly_availability` stays the SME's declared working pattern; a synced busy block is a separate,
    additive hard rule. Rewriting the pattern from freebusy would conflate "when I work" with "what
    is already on my calendar this week", and the two answer different questions.
    """
    try:
        b0, b1 = parse_utc(block["start_utc"]), parse_utc(block["end_utc"])
    except (KeyError, TypeError, ValueError):
        return False                       # a malformed block must never silently eliminate everyone
    return b0 < end and start < b1


def busy_at(sme: dict, start: datetime, end: datetime) -> dict | None:
    return next((b for b in (sme.get("external_busy") or []) if busy_overlap(b, start, end)), None)


def overlaps(a: dict, b: dict) -> bool:
    a0, a1 = session_span(a)
    b0, b1 = session_span(b)
    return a0 < b1 and b0 < a1


def topic_of(session: dict) -> str:
    if session["type"] == "doubt":
        return "doubt"
    return session.get("sub_specialty") or session["subject"].lower()


# An SME may carry several courses/topics: `subjects`/`topics` lists win, singular fields are the fallback.
def sme_subjects(sme: dict) -> list[str]:
    return list(sme.get("subjects") or [sme["subject"]])


def sme_topics(sme: dict) -> list[str]:
    topics = sme.get("topics")
    if topics:
        return list(topics)
    return [sme["sub_specialty"]] if sme.get("sub_specialty") else []


def teaches_subject(sme: dict, subject: str) -> bool:
    return subject in sme_subjects(sme)


def carries_topic(sme: dict, topic: str | None) -> bool:
    """No topic required -> any SME of the subject qualifies (doubt sessions).
    An SME with no declared topics is a generalist for their subject."""
    if not topic:
        return True
    topics = sme_topics(sme)
    return not topics or topic in topics


# ---------- history helpers ----------

def build_hist(history: list[dict], smes: list[dict]) -> dict[str, list[dict]]:
    """sme_id -> week records sorted ascending. Falls back to sme['history'] if `history` is empty."""
    hist: dict[str, list[dict]] = {s["id"]: [] for s in smes}
    if history:
        for rec in history:
            hist.setdefault(rec["sme_id"], []).append(rec)
    else:
        for s in smes:
            hist[s["id"]] = list(s.get("history", []))
    for recs in hist.values():
        recs.sort(key=lambda r: r["week"])
    return hist


def past_load(weeks: list[dict]) -> int:
    return sum(int(w["sessions_taught"]) for w in weeks[-PAST_WEEKS_FOR_LOAD:])


def taught_batches(weeks: list[dict]) -> set[str]:
    return {b for w in weeks for b in w.get("batches", [])}


def topic_rating(weeks: list[dict], topic: str) -> float:
    vals = [w["per_topic_rating"][topic] for w in weeks
            if w.get("per_topic_rating") and topic in w["per_topic_rating"]]
    return float(mean(vals)) if vals else 3.0


# ---------- Stage A ----------

def stage_a_hard_filter(session: dict, smes: list[dict], draft: list[dict],
                        exclude_session_id: str | None = None) -> tuple[list[dict], list[dict]]:
    """Return (survivors, eliminated). `draft` = rows already holding an sme_id.
    eliminated entries: {sme_id, name, rule} where rule in
    subject|sub_specialty|training_level|availability|calendar_busy|overlap:<session_id>."""
    start, end = session_span(session)
    survivors, eliminated = [], []
    for sme in smes:
        rule = None
        if not teaches_subject(sme, session["subject"]):
            rule = "subject"
        elif not carries_topic(sme, session.get("sub_specialty")):
            rule = "sub_specialty"
        elif int(sme["training_level"]) < int(session.get("required_training_level", 1)):
            rule = "training_level"
        elif not is_available(sme, start, end):
            rule = "availability"
        elif busy_at(sme, start, end):
            rule = "calendar_busy"
        else:
            for row in draft:
                if row.get("sme_id") == sme["id"] and row["session_id"] != session["id"] \
                        and row["session_id"] != exclude_session_id and overlaps(row, session):
                    rule = f"overlap:{row['session_id']}"
                    break
        if rule:
            eliminated.append({"sme_id": sme["id"], "name": sme["name"], "rule": rule})
        else:
            survivors.append(sme)
    return survivors, eliminated


def unfilled_reason(session: dict, eliminated: list[dict]) -> str:
    """Name the constraints that eliminated the same-subject candidates."""
    start, _ = session_span(session)
    same_subject = [e for e in eliminated if e["rule"] != "subject"]
    if not same_subject:
        return f"No eligible SME: no {session['subject']} SMEs in the pool."
    parts = []
    ss = [e for e in same_subject if e["rule"] == "sub_specialty"]
    tl = [e for e in same_subject if e["rule"] == "training_level"]
    av = [e for e in same_subject if e["rule"] == "availability"]
    cb = [e for e in same_subject if e["rule"] == "calendar_busy"]
    ov = [e for e in same_subject if e["rule"].startswith("overlap:")]
    if cb:
        parts.append(f"{', '.join(e['name'] for e in cb)} "
                     f"{'has' if len(cb) == 1 else 'have'} a calendar conflict at {fmt_ist(start)}")
    if av:
        parts.append(f"{len(av)} {session['subject']} SME(s) unavailable at {fmt_ist(start)} "
                     f"({', '.join(e['name'] for e in av)})")
    if tl:
        parts.append(f"{', '.join(e['name'] for e in tl)} below required training level "
                     f"{session.get('required_training_level', 1)}")
    if ov:
        parts.append("; ".join(f"{e['name']} already assigned to {e['rule'].split(':', 1)[1]} at this time" for e in ov))
    if ss:
        parts.append(f"{len(ss)} not carrying {session.get('sub_specialty')}")
    return "No eligible SME: " + "; ".join(parts) + "."


# ---------- Stage B ----------

def projected_load(sme_id: str, hist: dict, draft_counts: dict) -> int:
    return past_load(hist.get(sme_id, [])) + draft_counts.get(sme_id, 0)


def subject_pool(smes: list[dict], subject: str) -> list[dict]:
    return [s for s in smes if teaches_subject(s, subject)]


def stage_b_score(session: dict, survivors: list[dict], smes: list[dict], hist: dict,
                  draft_counts: dict, adjust: dict | None = None) -> list[dict]:
    """score = 0.5*fairness + 0.3*continuity + 0.2*performance (+ override adjustment). Sorted desc."""
    adjust = adjust or {}
    pool = subject_pool(smes, session["subject"])
    loads = {s["id"]: projected_load(s["id"], hist, draft_counts) for s in pool}
    if not loads:
        return []      # no SME in the pool teaches this subject at all: no candidates, not a crash
    lo, hi = min(loads.values()), max(loads.values())
    topic = topic_of(session)
    out = []
    for sme in survivors:
        weeks = hist.get(sme["id"], [])
        fairness = 1 - (loads[sme["id"]] - lo) / (hi - lo + EPS)
        continuity = 1.0 if session["batch_id"] in taught_batches(weeks) else 0.0
        performance = topic_rating(weeks, topic) / 5
        adj = adjust.get((sme["id"], session["batch_id"]), 0.0)
        score = 0.5 * fairness + 0.3 * continuity + 0.2 * performance + adj
        out.append({
            "sme_id": sme["id"], "name": sme["name"], "score": round(score, 4),
            "components": {"fairness": round(fairness, 4), "continuity": continuity,
                           "performance": round(performance, 4), "adjustment": adj},
        })
    out.sort(key=lambda c: (-c["score"], c["sme_id"]))
    return out


def is_clear_winner(scored: list[dict]) -> bool:
    return len(scored) == 1 or (scored[0]["score"] - scored[1]["score"]) >= MARGIN - 1e-9


# ---------- Stage D ----------

def stage_d_validate(rows: list[dict], smes: list[dict], hist: dict) -> list[dict]:
    """Re-check every assignment against hard rules (reject -> UNFILLED) and the fairness band
    (keep, emit FAIRNESS_VIOLATION). Mutates and returns rows."""
    by_id = {s["id"]: s for s in smes}
    accepted: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda r: (r["start_utc"], r["session_id"])):
        if not row.get("sme_id"):
            continue
        sme = by_id.get(row["sme_id"])
        rule = None
        if sme is None or not teaches_subject(sme, row["subject"]):
            rule = "subject"
        elif not carries_topic(sme, row.get("sub_specialty")):
            rule = "sub_specialty"
        elif int(sme["training_level"]) < int(row.get("required_training_level", 1)):
            rule = "training_level"
        else:
            start, end = session_span(row)
            if not is_available(sme, start, end):
                rule = "availability"
            elif busy_at(sme, start, end):
                rule = "calendar_busy"
            else:
                for other in accepted.get(sme["id"], []):
                    if overlaps(other, row):
                        rule = f"overlap:{other['session_id']}"
                        row["flags"].append(make_flag(
                            "HARD_CONFLICT", row["session_id"],
                            f"{sme['name']} is already assigned to {other['session_id']} at this time.", sme["id"]))
                        break
        if rule:
            name = sme["name"] if sme else row["sme_id"]
            row["flags"].append(make_flag(
                "UNFILLED", row["session_id"],
                f"No eligible SME: validation rejected {name} ({rule_label(rule)})."))
            row["rejected_sme_id"] = row["sme_id"]
            row["sme_id"] = row["sme_name"] = row["score"] = row["stage"] = None
            continue
        accepted.setdefault(sme["id"], []).append(row)

    # fairness band, per subject pool, on the validated draft
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("sme_id"):
            counts[row["sme_id"]] = counts.get(row["sme_id"], 0) + 1
    for subject in sorted({subj for s in smes for subj in sme_subjects(s)}):
        pool = subject_pool(smes, subject)
        loads = {s["id"]: projected_load(s["id"], hist, counts) for s in pool}
        m = mean(loads.values())
        for row in rows:
            sid = row.get("sme_id")
            if row["subject"] != subject or sid not in loads:
                continue  # flag a row once, against its own session's pool
            if abs(loads[sid] - m) > FAIRNESS_BAND:
                row["flags"].append(make_flag(
                    "FAIRNESS_VIOLATION", row["session_id"],
                    f"{by_id[sid]['name']} at {loads[sid]} sessions over 4 weeks vs. pool mean {m:.1f}.", sid))
    return rows


def fairness_band_breach(sme_id: str, subject: str, smes: list[dict], hist: dict, counts: dict) -> bool:
    """Would assigning one more session to sme_id put them outside mean ± 2 for their pool?"""
    pool = subject_pool(smes, subject)
    loads = {s["id"]: projected_load(s["id"], hist, counts) for s in pool}
    loads[sme_id] = loads.get(sme_id, 0) + 1
    return abs(loads[sme_id] - mean(loads.values())) > FAIRNESS_BAND


# ---------- Stage E ----------

def stage_e_adjustments(overrides: list[dict]) -> dict[tuple[str, str], float]:
    """Override feedback: -0.2 for the overridden (SME, batch) pairing, +0.1 for the pairing ops chose.
    ponytail: pairing key is batch_id (every session has one); topic-level pairing not needed for the demo."""
    adjust: dict[tuple[str, str], float] = {}
    for o in overrides or []:
        if o.get("from_sme_id"):
            k = (o["from_sme_id"], o["batch_id"])
            adjust[k] = round(adjust.get(k, 0.0) + OVERRIDE_PENALTY, 4)
        if o.get("to_sme_id"):
            k = (o["to_sme_id"], o["batch_id"])
            adjust[k] = round(adjust.get(k, 0.0) + OVERRIDE_BONUS, 4)
    return adjust

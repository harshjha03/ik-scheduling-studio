"""The copilot's toolbox: pure, JSON-safe functions over one draft + fixtures.

Nothing here schedules. Every tool composes the existing Stage A/B/D functions from `stages.py`;
`simulate_plan` works on a deep copy and never touches the caller's rows.

A `ctx` is the state one API call carries: {"week", "draft", "smes", "history", "unavailable"?}.
`unavailable` = {"sme_id", "days": ["Wed", ...] | None} for a drop-out — rows still held by that
teacher on those days count as uncovered, and that teacher is never offered as a candidate.
"""
from __future__ import annotations

import copy
from collections import Counter
from datetime import timedelta, timezone

from . import stages as S

STAGE_D_CODES = {"UNFILLED", "HARD_CONFLICT", "FAIRNESS_VIOLATION"}
ACTION_KINDS = ("move", "reschedule", "upgrade")
SEVERITY = {"doubt": 0, "class": 1, "mock": 2}   # lowest first: disturb doubt sessions before classes before mocks
DAY_ALIASES = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}


class ToolError(ValueError):
    """Bad tool args — the agent loop feeds the message back to the model and retries once."""


# ---------- ctx helpers ----------

def make_ctx(week: str, draft: list[dict], smes: list[dict], history: list[dict] | None = None,
             unavailable: dict | None = None) -> dict:
    if unavailable and unavailable.get("days"):
        unavailable = {**unavailable, "days": [norm_day(d) for d in unavailable["days"]]}
    return {"week": week, "draft": draft, "smes": smes, "history": history or [],
            "hist": S.build_hist(history or [], smes), "unavailable": unavailable}


def norm_day(d: str) -> str:
    key = str(d).strip()[:3].lower()
    if key not in DAY_ALIASES:
        raise ToolError(f"unknown day `{d}` — use Mon..Sun")
    return DAY_ALIASES[key]


def _day(row: dict) -> str:
    return S.WEEKDAYS[S.parse_utc(row["start_utc"]).astimezone(S.IST).weekday()]


def _rows(ctx: dict) -> dict[str, dict]:
    return {r["session_id"]: r for r in ctx["draft"]}


def _row(ctx: dict, session_id: str) -> dict:
    row = _rows(ctx).get(session_id)
    if row is None:
        raise ToolError(f"unknown session_id `{session_id}`")
    return row


def _sme(ctx: dict, sme_id: str) -> dict:
    sme = next((s for s in ctx["smes"] if s["id"] == sme_id), None)
    if sme is None:
        raise ToolError(f"unknown sme_id `{sme_id}`")
    return sme


def _check_week(ctx: dict, week: str | None) -> None:
    if week and week != ctx["week"]:
        raise ToolError(f"only week `{ctx['week']}` is loaded")


def blocked(ctx: dict, sme_id: str | None, row: dict) -> bool:
    """Is this teacher the reported drop-out for this row's day?"""
    u = ctx.get("unavailable")
    return bool(u and sme_id == u["sme_id"] and (not u.get("days") or _day(row) in u["days"]))


def _brief(row: dict) -> dict:
    return {"session_id": row["session_id"], "batch_id": row["batch_id"], "subject": row["subject"],
            "sub_specialty": row.get("sub_specialty"), "type": row["type"], "day": _day(row),
            "time_ist": S.fmt_ist(S.parse_utc(row["start_utc"])), "sme_id": row.get("sme_id"),
            "sme_name": row.get("sme_name"), "required_training_level": row.get("required_training_level", 1),
            "flags": [f["code"] for f in row.get("flags", [])]}


UNBLOCK = {
    "training_level": "needs a training-level upgrade to {level}",
    "sub_specialty": "does not carry {topic}",
    "availability": "free that day, but {time} is outside their working hours",
}


def _blocked_detail(ctx: dict, e: dict, sess: dict) -> str:
    """What single rule blocks this teacher, and what would remove it. A dead end is only useful
    to a coordinator if it names the nearest miss."""
    rule = e["rule"]
    if rule.startswith("overlap:"):
        other = _rows(ctx).get(rule.split(":", 1)[1])
        what = f"{other['batch_id']} {other.get('sub_specialty') or other['type']}" if other else rule.split(":", 1)[1]
        return f"already teaching {what} at that hour — free them and they qualify"
    return UNBLOCK.get(rule, rule).format(
        level=sess.get("required_training_level", 1), topic=sess.get("sub_specialty") or sess["subject"],
        time=S.fmt_ist(S.parse_utc(sess["start_utc"])))


def _final_candidates(ctx: dict, rows: list[dict], session_id: str, exclude: set[str] = frozenset()) -> tuple[list[dict], list[dict]]:
    """The final-candidates pass from run_pipeline, over an arbitrary set of rows: Stage A against
    the assigned rows (this session excluded), Stage B with this row's own load taken out."""
    sess = next(r for r in rows if r["session_id"] == session_id)
    sess = {**sess, "id": session_id}          # stage_a reads a session's `id`; rows carry `session_id`
    assigned = [r for r in rows if r.get("sme_id")]
    counts = Counter(r["sme_id"] for r in assigned)
    if sess.get("sme_id"):
        counts[sess["sme_id"]] -= 1
    survivors, eliminated = S.stage_a_hard_filter(sess, ctx["smes"], assigned, exclude_session_id=session_id)
    survivors = [s for s in survivors if s["id"] not in exclude and not blocked(ctx, s["id"], sess)]
    scored = S.stage_b_score(sess, survivors, ctx["smes"], ctx["hist"], counts) if survivors else []
    for c in scored:
        c["breaches_fairness"] = S.fairness_band_breach(c["sme_id"], sess["subject"], ctx["smes"], ctx["hist"], counts)
    same_subject = [{**e, "detail": _blocked_detail(ctx, e, sess)} for e in eliminated if e["rule"] != "subject"]
    return scored, same_subject


# ---------- tools ----------

def roster(ctx: dict) -> list[dict]:
    """Every teacher with the handful of facts the agent needs to pick one by name, not by guess."""
    return [{"sme_id": s["id"], "name": s["name"], "subjects": S.sme_subjects(s), "topics": S.sme_topics(s),
             "training_level": s["training_level"],
             "sessions_this_week": sum(1 for r in ctx["draft"] if r.get("sme_id") == s["id"])}
            for s in ctx["smes"]]


def get_draft_summary(ctx: dict, week: str | None = None) -> dict:
    _check_week(ctx, week)
    rows = ctx["draft"]
    flags = [f for r in rows for f in r.get("flags", [])]
    load = Counter(r["sme_id"] for r in rows if r.get("sme_id"))
    names = {s["id"]: s["name"] for s in ctx["smes"]}
    return {
        "week": ctx["week"], "total_sessions": len(rows),
        "by_status": {"assigned": sum(1 for r in rows if r.get("sme_id")), "unfilled": sum(1 for r in rows if not r.get("sme_id")),
                      "auto": sum(1 for r in rows if r.get("stage") == "auto"), "llm": sum(1 for r in rows if r.get("stage") == "llm"),
                      "override": sum(1 for r in rows if r.get("stage") == "override")},
        "flags_by_severity": dict(Counter(f["severity"] for f in flags)),
        "flags_by_code": dict(Counter(f["code"] for f in flags)),
        "unfilled": [{"session_id": r["session_id"], "batch_id": r["batch_id"], "day": _day(r),
                      "time_ist": S.fmt_ist(S.parse_utc(r["start_utc"])),
                      "required_training_level": r.get("required_training_level", 1),
                      "reason": next((f["reason"] for f in r.get("flags", []) if f["code"] == "UNFILLED"), None),
                      # the blockers as data, not prose: the model was reading names out of the sentence
                      "blocked": _final_candidates(ctx, rows, r["session_id"])[1]}
                     for r in rows if not r.get("sme_id")],
        "load_by_sme": [{"sme_id": sid, "name": names.get(sid, sid), "sessions": n} for sid, n in load.most_common()],
        "unavailable": ctx.get("unavailable"),
    }


def get_row(ctx: dict, session_id: str, week: str | None = None) -> dict:
    _check_week(ctx, week)
    row = _row(ctx, session_id)
    scored, blocked = _final_candidates(ctx, ctx["draft"], session_id)
    return {**_brief(row), "score": row.get("score"), "components": row.get("components"), "stage": row.get("stage"),
            "flags": [{"code": f["code"], "severity": f["severity"], "reason": f["reason"]} for f in row.get("flags", [])],
            "top_candidates": [{"sme_id": c["sme_id"], "name": c["name"], "score": c["score"]} for c in scored[:5]],
            "blocked": blocked}


def get_affected_rows(ctx: dict, sme_id: str, days: list[str] | None = None, week: str | None = None) -> dict:
    _check_week(ctx, week)
    sme = _sme(ctx, sme_id)
    want = {norm_day(d) for d in days} if days else None
    rows = [r for r in ctx["draft"] if r.get("sme_id") == sme_id and (want is None or _day(r) in want)]
    rows.sort(key=lambda r: (SEVERITY.get(r["type"], 9), r["start_utc"]))
    return {"sme_id": sme_id, "name": sme["name"], "days": sorted(want) if want else None,
            "rows": [_brief(r) for r in rows], "count": len(rows)}


def get_candidates(ctx: dict, session_id: str, week: str | None = None) -> dict:
    _check_week(ctx, week)
    row = _row(ctx, session_id)
    scored, eliminated = _final_candidates(ctx, ctx["draft"], session_id)
    return {"session_id": session_id, "current_sme": row.get("sme_id"),
            "candidates": [c for c in scored if c["sme_id"] != row.get("sme_id")],
            "eliminated": eliminated}


def get_sme(ctx: dict, sme_id: str, week: str | None = None) -> dict:
    sme = _sme(ctx, sme_id)
    mine = [r for r in ctx["draft"] if r.get("sme_id") == sme_id]
    return {"sme_id": sme["id"], "name": sme["name"], "subjects": S.sme_subjects(sme), "topics": S.sme_topics(sme),
            "training_level": sme["training_level"], "timezone": sme.get("timezone"),
            "availability": [{"weekday": w["weekday"], "start_utc": w["start_utc"], "end_utc": w["end_utc"]} for w in sme.get("weekly_availability", [])],
            "preference_notes": str(sme.get("preference_notes") or "")[:500], "preferred_per_week": sme.get("preferred"),
            "past_load_3w": S.past_load(ctx["hist"].get(sme_id, [])),
            "this_week": [_brief(r) for r in sorted(mine, key=lambda r: r["start_utc"])], "this_week_count": len(mine)}


def max_level(smes: list[dict], rows: list[dict] | None = None) -> int:
    """The highest level that means anything here: what someone already holds, or what a class asks for.
    Reading only the roster would make a level nobody holds yet unreachable."""
    levels = [int(s["training_level"]) for s in smes]
    levels += [int(r.get("required_training_level", 1)) for r in rows or []]
    return max(levels)


def _hour(value) -> int:
    try:
        return int(str(value).split(":")[0])
    except (TypeError, ValueError):
        raise ToolError(f"`to_hour_ist` must look like '13:00', got {value!r}")


def _kind_of(a: dict) -> str:
    if a.get("kind") in ACTION_KINDS:
        return a["kind"]
    if a.get("to_day") or a.get("to_hour_ist"):
        return "reschedule"
    if a.get("to_level"):
        return "upgrade"
    return "move"


def _slot_utc(ctx: dict, row: dict, day: str, hour_ist: str) -> str:
    """The UTC start for (day, HH:MM IST) in this row's own week."""
    ist = S.parse_utc(row["start_utc"]).astimezone(S.IST)
    d = S.WEEKDAYS.index(norm_day(day))
    h = _hour(hour_ist)
    when = ist.replace(hour=h, minute=0, second=0, microsecond=0) + timedelta(days=d - ist.weekday())
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _norm_action(ctx: dict, a: dict, i: int) -> dict:
    """One plan entry, validated into a shape apply can execute. Raises ToolError on anything else."""
    if not isinstance(a, dict):
        raise ToolError(f"plan entry #{i + 1} must be an object")
    kind = _kind_of(a)
    reason = str(a.get("reason") or "")
    if kind == "move":
        if not a.get("session_id") or not a.get("to_sme"):
            raise ToolError(f"plan entry #{i + 1} must be {{session_id, from_sme, to_sme, reason}}")
        row, sme = _row(ctx, a["session_id"]), _sme(ctx, a["to_sme"])
        return {"kind": "move", "session_id": row["session_id"], "from_sme": row.get("sme_id"),
                "to_sme": sme["id"], "to_sme_name": sme["name"], "reason": reason,
                **({"flag": a["flag"]} if a.get("flag") else {})}
    if kind == "reschedule":
        if not a.get("session_id") or not a.get("to_day") or not a.get("to_hour_ist"):
            raise ToolError(f"plan entry #{i + 1} must be {{kind: 'reschedule', session_id, to_day, to_hour_ist, reason}}")
        row = _row(ctx, a["session_id"])
        ist = S.parse_utc(row["start_utc"]).astimezone(S.IST)
        return {"kind": "reschedule", "session_id": row["session_id"], "to_day": norm_day(a["to_day"]),
                "to_hour_ist": f"{_hour(a['to_hour_ist']):02d}:00",
                "from_day": S.WEEKDAYS[ist.weekday()], "from_hour_ist": f"{ist.hour:02d}:00",
                "start_utc": _slot_utc(ctx, row, a["to_day"], a["to_hour_ist"]), "reason": reason}
    if kind == "upgrade":
        if not a.get("sme_id") or not a.get("to_level"):
            raise ToolError(f"plan entry #{i + 1} must be {{kind: 'upgrade', sme_id, to_level, reason}}")
        sme = _sme(ctx, a["sme_id"])
        try:
            level = int(a["to_level"])
        except (TypeError, ValueError):
            raise ToolError(f"`to_level` must be a whole number, got {a['to_level']!r}")
        return {"kind": "upgrade", "sme_id": sme["id"], "sme_name": sme["name"],
                "from_level": int(sme["training_level"]), "to_level": level, "reason": reason}
    raise ToolError(f"unknown plan entry kind `{a.get('kind')}`; use one of {', '.join(ACTION_KINDS)}")


def _trial(ctx: dict, actions: list[dict]) -> tuple[list[dict], list[dict]]:
    """The draft and roster as they would be with every action applied. Copies only — never the real state."""
    rows = copy.deepcopy(ctx["draft"])
    smes = copy.deepcopy(ctx["smes"])
    by_id = {r["session_id"]: r for r in rows}
    by_sme = {s["id"]: s for s in smes}
    for r in rows:
        r["flags"] = [f for f in r["flags"] if f["code"] not in STAGE_D_CODES]
    for a in actions:                       # upgrades first: they decide who is eligible for the rest
        if a["kind"] == "upgrade":
            by_sme[a["sme_id"]]["training_level"] = a["to_level"]
    for a in actions:
        if a["kind"] == "reschedule":
            by_id[a["session_id"]]["start_utc"] = a["start_utc"]
    for a in actions:
        if a["kind"] == "move":
            by_id[a["session_id"]].update(sme_id=a["to_sme"], sme_name=a["to_sme_name"], stage="override")
    return rows, smes


def simulate_plan(ctx: dict, moves: list[dict], week: str | None = None) -> dict:
    """Apply a plan to a copy of the draft, re-run Stage A + B + D, and give a verdict per entry:
    `ok` | `fairness_warning` | `breaks:<rule>`. The real draft and roster are never touched.

    A plan entry is a staffing `move`, a `reschedule` (same class, another hour) or an `upgrade`
    (a teacher's training level). All three change who is eligible, so all three are simulated here
    rather than trusted — an upgrade is applied before the moves that depend on it.
    """
    _check_week(ctx, week)
    if not isinstance(moves, list):
        raise ToolError("`moves` must be a list")
    actions = [_norm_action(ctx, m, i) for i, m in enumerate(moves)]
    for kind, key in (("move", "session_id"), ("reschedule", "session_id"), ("upgrade", "sme_id")):
        keys = [a[key] for a in actions if a["kind"] == kind]
        if len(set(keys)) != len(keys):
            raise ToolError(f"two {kind} entries target the same {key}")
    rows, trial_smes = _trial(ctx, actions)
    by_id = {r["session_id"]: r for r in rows}
    before = Counter(f["code"] for r in ctx["draft"] for f in r.get("flags", []))
    trial_ctx = {**ctx, "draft": rows, "smes": trial_smes,
                 "hist": S.build_hist(ctx["history"], trial_smes) if ctx["history"] else ctx["hist"]}
    days, hours = _grid(ctx)

    verdicts = []
    for a in actions:
        verdict, detail, score = "ok", None, None
        if a["kind"] == "upgrade":
            top = max_level(ctx["smes"], ctx["draft"])
            was = {r["session_id"] for r in ctx["draft"]
                   if any(c["sme_id"] == a["sme_id"] for c in _final_candidates(ctx, ctx["draft"], r["session_id"])[0])}
            # Only a class that actually needs somebody counts: unstaffed, or held by the reported drop-out.
            # Judged on the draft as it stands, not the trial — a move in the same plan may already have
            # filled the very class the upgrade exists to unblock.
            needs_cover = {r["session_id"] for r in ctx["draft"]
                           if not r.get("sme_id") or blocked(ctx, r.get("sme_id"), r)}
            newly = [r["session_id"] for r in rows
                     if int(r.get("required_training_level", 1)) > a["from_level"]
                     and r["session_id"] not in was
                     and any(c["sme_id"] == a["sme_id"] for c in _final_candidates(trial_ctx, rows, r["session_id"])[0])]
            unblocks = [sid for sid in newly if sid in needs_cover]
            if a["to_level"] <= a["from_level"]:
                verdict, detail = "breaks:not_an_upgrade", f"{a['sme_name']} is already at level {a['from_level']}."
            elif a["to_level"] > top:
                verdict, detail = "breaks:level_out_of_range", f"Level {a['to_level']} does not exist (the roster tops out at {top})."
            elif not unblocks:
                verdict = "breaks:changes_nothing"
                detail = (f"Raising {a['sme_name']} to level {a['to_level']} would qualify them for "
                          f"{', '.join(newly)}, which already have a teacher — it does not help any class that "
                          f"needs one." if newly else
                          f"Raising {a['sme_name']} to level {a['to_level']} would not make them eligible for any "
                          f"class they cannot already take — a training level is not a knob to turn for its own sake.")
            else:
                detail = f"Unblocks {', '.join(unblocks)}."
            verdicts.append({**a, "verdict": verdict, "detail": detail, "score": None, "unblocks": unblocks})
            continue

        if a["kind"] == "reschedule":
            row = by_id[a["session_id"]]
            d, h = S.WEEKDAYS.index(a["to_day"]), int(a["to_hour_ist"][:2])
            clash = next((r for r in rows if r["batch_id"] == row["batch_id"] and r["session_id"] != row["session_id"]
                          and S.parse_utc(r["start_utc"]) == S.parse_utc(row["start_utc"])), None)
            eligible, _ = _final_candidates(trial_ctx, rows, a["session_id"])
            if d not in days or h not in hours:
                verdict, detail = "breaks:outside_the_week", f"{a['to_day']} {a['to_hour_ist']} is outside this week's teaching hours."
            elif clash:
                verdict, detail = "breaks:batch_clash", f"{row['batch_id']} already has a session then ({clash['session_id']}) — learners cannot attend both."
            elif not eligible:
                verdict, detail = "breaks:no_eligible_teacher", f"Nobody can teach it at {a['to_day']} {a['to_hour_ist']} either."
            else:
                detail = (f"{eligible[0]['name']} can take it at {a['to_day']} {a['to_hour_ist']}"
                          + (f" (+{len(eligible) - 1} other)" if len(eligible) > 1 else "") + ".")
            verdicts.append({**a, "verdict": verdict, "detail": detail, "score": None,
                             "eligible_after": [{"sme_id": c["sme_id"], "name": c["name"]} for c in eligible[:3]]})
            continue

        r = by_id[a["session_id"]]
        if blocked(ctx, a["to_sme"], r):
            verdict, detail = "breaks:unavailable", f"{a['to_sme_name']} is the teacher reported unavailable."
        else:
            scored, eliminated = _final_candidates(trial_ctx, rows, a["session_id"])
            hit = next((c for c in scored if c["sme_id"] == a["to_sme"]), None)
            if hit is None:
                rule = next((e["rule"] for e in eliminated if e["sme_id"] == a["to_sme"]), "subject")
                verdict, detail = f"breaks:{rule}", f"{a['to_sme_name']} fails {S.rule_label(rule)}."
            else:
                score = hit["score"]
                if hit["breaches_fairness"]:
                    verdict, detail = "fairness_warning", f"{a['to_sme_name']} would sit outside the fairness band (mean ± {S.FAIRNESS_BAND})."
        verdicts.append({**a, "verdict": verdict, "detail": detail, "score": score})

    # Stage D is the guarantee: whatever Stage A said, a rejected or conflicting row is a break.
    S.stage_d_validate(rows, trial_smes, trial_ctx["hist"])
    for v in verdicts:
        if v["kind"] == "upgrade":
            continue
        r = by_id[v["session_id"]]
        hard = next((f for f in r["flags"] if f["code"] in ("UNFILLED", "HARD_CONFLICT")), None)
        if v["kind"] == "reschedule" and hard and hard["code"] == "UNFILLED" and not v["verdict"].startswith("breaks"):
            continue   # a rescheduled class is meant to be re-staffed by the next run; unfilled is expected
        if hard and not v["verdict"].startswith("breaks"):
            v["verdict"], v["detail"] = f"breaks:{hard['code'].lower()}", hard["reason"]
        elif v["verdict"] == "ok" and any(f["code"] == "FAIRNESS_VIOLATION" for f in r["flags"]):
            v["verdict"] = "fairness_warning"
            v["detail"] = next(f["reason"] for f in r["flags"] if f["code"] == "FAIRNESS_VIOLATION")
    # a move can also knock out a row it did not name (the new teacher was already booked there)
    moved = {v.get("session_id") for v in verdicts}
    collateral = [{"session_id": r["session_id"], "code": f["code"], "reason": f["reason"]}
                  for r in rows if r["session_id"] not in moved
                  for f in r["flags"] if f["code"] in ("UNFILLED", "HARD_CONFLICT")
                  and f["code"] not in {x["code"] for x in _rows(ctx)[r["session_id"]].get("flags", [])}]
    for c in collateral:
        culprit = next((v for v in verdicts if v["kind"] == "move" and (
            v["to_sme"] == by_id[c["session_id"]].get("sme_id")
            or v["to_sme"] == by_id[c["session_id"]].get("rejected_sme_id"))), None)
        if culprit and not culprit["verdict"].startswith("breaks"):
            culprit["verdict"], culprit["detail"] = f"breaks:overlap:{c['session_id']}", c["reason"]
    for r in rows:
        r["flags"] = S.sort_flags(r["flags"])
    after = Counter(f["code"] for r in rows for f in r["flags"])
    u = ctx.get("unavailable")
    uncovered = [_brief(r) for r in rows if u and blocked(ctx, r.get("sme_id"), r)]
    return {"verdicts": verdicts, "all_ok": all(not v["verdict"].startswith("breaks") for v in verdicts),
            "flag_diff": {"before": dict(before), "after": dict(after)},
            "unfilled_after": [r["session_id"] for r in rows if not r.get("sme_id")],
            "collateral": collateral, "still_uncovered": uncovered}


def reflag_unfilled(rows: list[dict], smes: list[dict]) -> list[dict]:
    """Give every still-unstaffed row its UNFILLED flag back, with the Stage-A reason.

    Stage D only flags rows whose assignment it *rejected*; a class that never had a candidate is
    skipped, so re-validating a draft in place would quietly drop the flag that blocks publishing.
    This is the same Stage A + unfilled_reason pass run_pipeline does. Mutates and returns rows.
    """
    assigned = [r for r in rows if r.get("sme_id")]
    for row in rows:
        if row.get("sme_id") or any(f["code"] == "UNFILLED" for f in row["flags"]):
            continue
        sess = {**row, "id": row["session_id"]}
        _, eliminated = S.stage_a_hard_filter(sess, smes, assigned, exclude_session_id=row["session_id"])
        row["flags"].append(S.make_flag("UNFILLED", row["session_id"], S.unfilled_reason(sess, eliminated)))
    return rows


def _grid(ctx: dict) -> tuple[list[int], list[int]]:
    """The week's own day/hour grid, read off the draft — no config to keep in sync."""
    parts = [S.parse_utc(r["start_utc"]).astimezone(S.IST) for r in ctx["draft"]]
    days = sorted({p.weekday() for p in parts})
    hours = sorted({p.hour for p in parts})
    return days, list(range(min(hours), max(hours) + 1))


def find_slots(ctx: dict, session_id: str, limit: int = 6, week: str | None = None) -> dict:
    """Other slots this week where somebody *could* teach this class.

    The answer to "nobody can take it" is usually "not at that hour" — this is the move that unblocks
    a class no swap can fix. Stage A decides eligibility, exactly as it does for the real slot; a slot
    the batch already has a session in is skipped so learners are never double-booked.
    """
    _check_week(ctx, week)
    row = _row(ctx, session_id)
    ist = S.parse_utc(row["start_utc"]).astimezone(S.IST)
    assigned = [r for r in ctx["draft"] if r.get("sme_id") and r["session_id"] != session_id]
    busy_batch = {(S.parse_utc(r["start_utc"]).astimezone(S.IST).weekday(), S.parse_utc(r["start_utc"]).astimezone(S.IST).hour)
                  for r in ctx["draft"] if r["batch_id"] == row["batch_id"] and r["session_id"] != session_id}
    days, hours = _grid(ctx)
    out = []
    for d in days:
        for h in hours:
            if (d, h) in busy_batch or (d == ist.weekday() and h == ist.hour):
                continue
            when = ist.replace(hour=h, minute=0) + timedelta(days=d - ist.weekday())
            trial = {**row, "id": session_id, "start_utc": when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}
            free, _ = S.stage_a_hard_filter(trial, ctx["smes"], assigned, exclude_session_id=session_id)
            free = [f for f in free if not blocked(ctx, f["id"], trial)]
            if free:
                out.append({"day": S.WEEKDAYS[d], "hour_ist": f"{h:02d}:00",
                            "eligible": [{"sme_id": f["id"], "name": f["name"]} for f in free[:4]],
                            "eligible_count": len(free)})
    # least disruption first: same day as the class, then nearest to its hour, then the widest choice
    out.sort(key=lambda s: (S.WEEKDAYS.index(s["day"]) != ist.weekday(), abs(int(s["hour_ist"][:2]) - ist.hour),
                            -s["eligible_count"], S.WEEKDAYS.index(s["day"])))
    return {"session_id": session_id, "current": {"day": S.WEEKDAYS[ist.weekday()], "hour_ist": f"{ist.hour:02d}:00"},
            "slots": out[:limit], "searched": len(days) * len(hours),
            "note": ("Offer the best of these as a {kind: 'reschedule'} plan entry — one slot, not a list."
                     if out else "No slot this week has an eligible teacher.")}


def get_issues(ctx: dict, codes: list[str] | None = None, limit: int = 8, week: str | None = None) -> dict:
    """Every flagged class with its fix material in ONE call: blockers, candidates, and — only when
    nothing is eligible — the swaps and slots that would work.

    Asked to "solve all 7 issues", the copilot used its whole budget calling get_candidates row by row
    and never reached an answer. Severity order, so a truncated list is still the right list.
    """
    _check_week(ctx, week)
    want = {c.upper() for c in codes} if codes else None
    # A class held by a teacher who has been reported unavailable carries no flag yet — it is still the
    # most urgent issue on the week, so it belongs here alongside the flagged rows.
    rows = [r for r in ctx["draft"]
            if (not r.get("sme_id") or r.get("flags") or blocked(ctx, r.get("sme_id"), r))
            and (want is None or {f["code"] for f in r["flags"]} & want
                 or (blocked(ctx, r.get("sme_id"), r) and "UNCOVERED" in want))]
    def urgency(r: dict) -> int:
        """No teacher at all beats a teacher who just dropped out, which beats any flag."""
        if not r.get("sme_id"):
            return 0
        if blocked(ctx, r.get("sme_id"), r):
            return 1
        return 2 + min((f["priority"] for f in r["flags"]), default=9)

    rows.sort(key=lambda r: (urgency(r), r["start_utc"]))
    issues = []
    for row in rows[:max(1, int(limit))]:
        sid = row["session_id"]
        scored, blocked_list = _final_candidates(ctx, ctx["draft"], sid)
        cands = [c for c in scored if c["sme_id"] != row.get("sme_id")]
        issue = {
            "session_id": sid, "batch_id": row["batch_id"], "type": row["type"], "day": _day(row),
            "time_ist": S.fmt_ist(S.parse_utc(row["start_utc"])), "sub_specialty": row.get("sub_specialty"),
            "required_training_level": row.get("required_training_level", 1),
            "sme_id": row.get("sme_id"), "sme_name": row.get("sme_name"),
            **({"needs_cover": f"{row['sme_name']} was reported unavailable"} if blocked(ctx, row.get("sme_id"), row) else {}),
            "flags": [{"code": f["code"], "reason": f["reason"]} for f in row["flags"]],
            "candidates": [{"sme_id": c["sme_id"], "name": c["name"], "score": c["score"],
                            "breaches_fairness": c["breaches_fairness"]} for c in cands[:3]],
        }
        if not cands:      # only then is the expensive search worth running
            issue["blocked"] = blocked_list
            issue["freeable"] = [{"sme_id": f["sme_id"], "name": f["name"],
                                  "frees_session": f["frees_session"]["session_id"],
                                  "has_replacement": f["has_replacement"]}
                                 for f in find_freeable(ctx, sid)["freeable"][:3]]
            issue["slots"] = find_slots(ctx, sid, limit=2)["slots"]
        issues.append(issue)
    return {"issues": issues, "total_flagged": len(rows), "shown": len(issues),
            "note": ("Everything you need to plan is here — simulate one plan covering the lot rather than "
                     "re-querying each class." if issues else "Nothing is flagged.")}


def with_unavailable(ctx: dict, sme_id: str, days: list[str] | None) -> dict:
    """A new ctx in which this teacher is the reported drop-out. Never mutates the one passed in."""
    _sme(ctx, sme_id)
    return {**ctx, "unavailable": {"sme_id": sme_id, "days": [norm_day(d) for d in days] if days else None}}


def report_unavailable(ctx: dict, sme_id: str, days: list[str] | None = None, week: str | None = None) -> dict:
    """Mark a teacher unavailable for the rest of this run and return what they were holding.

    The agent loop swaps in the ctx this returns, so from here on the teacher is offered to nobody and
    `simulate_plan` rejects any move back to them. This is how a chat turn like "Priya is out Wednesday"
    becomes the same state the SME-profile entry point sets up.
    """
    _check_week(ctx, week)
    new = with_unavailable(ctx, sme_id, days)
    out = get_affected_rows(new, sme_id, days)
    return {**out, "unavailable": new["unavailable"], "_ctx": new,
            "note": f"{out['name']} is now treated as unavailable; {out['count']} session(s) need cover."}


def find_freeable(ctx: dict, session_id: str, week: str | None = None) -> dict:
    """Teachers who would be eligible for this session if one of their lower-or-equal-severity rows
    (doubt sessions first) were reassigned — and whether that row has its own eligible candidates."""
    _check_week(ctx, week)
    target = _row(ctx, session_id)
    rows = ctx["draft"]
    _, eliminated = _final_candidates(ctx, rows, session_id)
    out = []
    for e in eliminated:
        if not e["rule"].startswith("overlap:") or blocked(ctx, e["sme_id"], target):
            continue
        other = _rows(ctx).get(e["rule"].split(":", 1)[1])
        if other is None or SEVERITY.get(other["type"], 9) > SEVERITY.get(target["type"], 9):
            continue
        # vacate `other`, hand `target` to this teacher, then ask who could take `other`
        trial = copy.deepcopy(rows)
        t_by = {r["session_id"]: r for r in trial}
        t_by[other["session_id"]].update(sme_id=None, sme_name=None)
        elig, _ = _final_candidates(ctx, trial, session_id)
        if not any(c["sme_id"] == e["sme_id"] for c in elig):
            continue      # freeing that row is not enough — another rule still blocks them
        t_by[session_id].update(sme_id=e["sme_id"], sme_name=e["name"])
        repl, _ = _final_candidates(ctx, trial, other["session_id"], exclude={e["sme_id"]})
        out.append({"sme_id": e["sme_id"], "name": e["name"],
                    "frees_session": _brief(other), "lateral": other["type"] == target["type"],
                    "replacement_candidates": [{"sme_id": c["sme_id"], "name": c["name"], "score": c["score"],
                                                "breaches_fairness": c["breaches_fairness"]} for c in repl[:5]],
                    "has_replacement": bool(repl)})
    out.sort(key=lambda f: (SEVERITY.get(f["frees_session"]["type"], 9), not f["has_replacement"], f["sme_id"]))
    return {"session_id": session_id, "freeable": out}


# name -> (fn, required args, optional args). The agent loop validates against this before calling.
REGISTRY = {
    "get_draft_summary": (get_draft_summary, (), ("week",)),
    "get_row": (get_row, ("session_id",), ("week",)),
    "get_affected_rows": (get_affected_rows, ("sme_id",), ("days", "week")),
    "get_candidates": (get_candidates, ("session_id",), ("week",)),
    "get_sme": (get_sme, ("sme_id",), ("week",)),
    "list_teachers": (lambda ctx, week=None: {"teachers": roster(ctx)}, (), ("week",)),
    "simulate_plan": (simulate_plan, ("moves",), ("week",)),
    "find_freeable": (find_freeable, ("session_id",), ("week",)),
    "report_unavailable": (report_unavailable, ("sme_id",), ("days", "week")),
    "find_slots": (find_slots, ("session_id",), ("limit", "week")),
    "get_issues": (get_issues, (), ("codes", "limit", "week")),
}


# One plan, three kinds of entry — so the model reaches for `plan`, `actions`, or even separate
# `reschedules`/`upgrades` lists. All of them mean the same thing; refusing on the spelling of a key
# burned two turns and a fallback in a live run.
PLAN_KEYS = ("plan", "moves", "actions", "changes", "entries", "reschedules", "upgrades")


def _plan_args(args: dict) -> dict:
    entries: list = []
    for key in PLAN_KEYS:
        val = args.get(key)
        if isinstance(val, list):
            entries.extend(val)
        elif isinstance(val, dict):
            entries.append(val)
    if not any(k in args for k in PLAN_KEYS):
        raise ToolError("simulate_plan needs `plan`: a list of {kind: move|reschedule|upgrade, ...} entries")
    extra = [k for k in args if k not in PLAN_KEYS + ("week",)]
    if extra:
        raise ToolError(f"simulate_plan: unexpected {extra}; pass the entries in `plan`")
    return {"moves": entries, **({"week": args["week"]} if "week" in args else {})}


def call_tool(ctx: dict, name: str, args: dict) -> dict:
    if name not in REGISTRY:
        raise ToolError(f"unknown tool `{name}`; available: {', '.join(REGISTRY)}")
    fn, required, optional = REGISTRY[name]
    if not isinstance(args, dict):
        raise ToolError("`args` must be an object")
    if name == "simulate_plan":
        return fn(ctx, **_plan_args(args))
    missing = [k for k in required if k not in args]
    extra = [k for k in args if k not in required + optional]
    if missing or extra:
        raise ToolError(f"{name}: " + "; ".join(filter(None, [
            f"missing {missing}" if missing else "", f"unexpected {extra}" if extra else ""])))
    return fn(ctx, **args)

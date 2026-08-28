"""Pipeline entrypoints: run_pipeline (Stages A→E scoring) and apply_approvals (Stage E decisions)."""
from __future__ import annotations

from collections import Counter

from . import stages as S
from .llm import active_model, explain, fallback_cfg, llm_provider, stage_c_llm_adjudicate


def _base_row(session: dict) -> dict:
    row = {k: session.get(k) for k in ("batch_id", "subject", "sub_specialty", "type", "start_utc",
                                        "duration_min", "mode", "required_training_level")}
    row.update({"session_id": session["id"], "sme_id": None, "sme_name": None, "score": None,
                "components": None, "stage": None, "flags": [], "candidates": [], "eliminated": [],
                "adjusted_from_override": False, "override_effect": None, "score_now": None})
    return row


def _candidate_payload(cand: dict, sme: dict, hist: dict) -> dict:
    weeks = hist.get(sme["id"], [])
    return {**cand, "preference_notes": sme.get("preference_notes", ""),
            "training_level": sme["training_level"],
            "per_topic_rating": (weeks[-1].get("per_topic_rating") if weeks else {}) or {},
            "recent_batches": sorted(S.taught_batches(weeks))}


def run_pipeline(sessions: list[dict], smes: list[dict], history: list[dict] | None,
                 overrides: list[dict] | None, llm_call=None, llm_enabled: bool = True) -> dict:
    """llm_enabled=False skips Stage C entirely: the queue takes the Stage-B top score as a normal
    auto-assignment (no LLM_FALLBACK flag) — for a settled past week, which is not an LLM outage."""
    hist = S.build_hist(history or [], smes)
    adjust = S.stage_e_adjustments(overrides or [])
    # Every SME an override touched. Moving a class off one teacher and onto another changes both of
    # their projected loads, and load is normalised per subject pool — so an SME who carries two
    # subjects (Rahul Desai is PM + DSA) shifts the fairness term for rows in a pool the override
    # never named. That coupling is the model being right, and it used to read as random churn.
    touched = {sid for o in (overrides or []) for sid in (o.get("from_sme_id"), o.get("to_sme_id")) if sid}
    by_id = {s["id"]: s for s in smes}
    ordered = sorted(sessions, key=lambda s: (s["start_utc"], s["id"]))
    rows: dict[str, dict] = {}
    draft: list[dict] = []           # rows currently holding an sme_id (auto or tentative)
    counts: Counter = Counter()      # sme_id -> sessions in this draft
    queue: list[tuple[dict, list[dict]]] = []

    # Stage A + B, chronological greedy, no backtracking. On realistic utilisation this is
    # matching-optimal — test_greedy_is_matching_optimal asserts the seed week's maximum matching
    # fills no more sessions than this loop does (39 of 41; the other two have zero and one eligible
    # SME respectively, and that one is double-booked at the same hour). An optimiser would add cost
    # and explanation burden for zero rows, and "why did it move Priya?" is the harder question.
    for sess in ordered:
        row = _base_row(sess)
        rows[sess["id"]] = row
        survivors, eliminated = S.stage_a_hard_filter(sess, smes, draft)
        row["eliminated"] = eliminated
        if not survivors:
            row["flags"].append(S.make_flag("UNFILLED", sess["id"], S.unfilled_reason(sess, eliminated)))
            continue
        scored = S.stage_b_score(sess, survivors, smes, hist, counts, adjust)
        row["adjusted_from_override"] = any(c["components"]["adjustment"] for c in scored)
        top = scored[0]
        row.update(sme_id=top["sme_id"], sme_name=top["name"], score=top["score"], components=top["components"])
        if S.is_clear_winner(scored):
            row["stage"] = "auto"
        else:
            row["stage"] = "pending"      # tentatively holds Stage-B top so overlap accounting stays correct
            queue.append((row, scored))
        counts[top["sme_id"]] += 1
        draft.append(row)

    # Stage C — exception queue only; candidates must still be overlap-free against the full tentative draft
    items, cand_maps = [], {}
    for row, scored in queue:
        sess = next(s for s in ordered if s["id"] == row["session_id"])
        elig = []
        for c in scored:
            clash = any(r["sme_id"] == c["sme_id"] and r is not row and S.overlaps(r, row) for r in draft)
            if not clash:
                elig.append(c)
        cand_maps[row["session_id"]] = {c["sme_id"]: c for c in elig}
        items.append({"session_id": row["session_id"],
                      "session": {k: sess.get(k) for k in ("batch_id", "subject", "sub_specialty", "type",
                                                            "required_training_level")} | {
                          "time_ist": S.fmt_ist(S.parse_utc(sess["start_utc"]))},
                      "candidates": [_candidate_payload(c, by_id[c["sme_id"]], hist) for c in elig]})
    pre_flags = [{"session_id": f["session_id"], "code": f["code"], "template_reason": f["reason"]}
                 for r in rows.values() for f in r["flags"]]
    if llm_enabled:
        llm = stage_c_llm_adjudicate(items, pre_flags, llm_call=llm_call)
    else:  # settled week: the Stage-B top score stands, and that is not a fallback
        llm = {"decisions": {}, "reasons": {}, "fallback_ids": [], "error": None, "error_kind": None,
               "failover": None, "skipped": True}
    for row, scored in queue:
        d = llm["decisions"].get(row["session_id"])
        # Two concurrent queued sessions may both be switched to the same free SME: re-check overlap
        # against the draft as it stands now; a clash is an ineligible pick -> deterministic fallback.
        if d and any(r["sme_id"] == d["sme_id"] and r is not row and S.overlaps(r, row) for r in draft):
            d = None
        if d:
            chosen = cand_maps[row["session_id"]][d["sme_id"]]
            if chosen["sme_id"] != row["sme_id"]:
                counts[row["sme_id"]] -= 1
                counts[chosen["sme_id"]] += 1
            row.update(sme_id=chosen["sme_id"], sme_name=chosen["name"], score=chosen["score"],
                       components=chosen["components"], stage="llm")
            row["flags"].append(S.make_flag("TIE_ESCALATED", row["session_id"], d["reason"], chosen["sme_id"]))
        else:
            row["stage"] = "auto"
            if llm_enabled:
                row["flags"].append(S.make_flag("LLM_FALLBACK", row["session_id"], S.LLM_FALLBACK_REASON, row["sme_id"]))
    for r in rows.values():
        for f in r["flags"]:
            new = llm["reasons"].get((f["session_id"], f["code"]))
            if new:
                f["reason"] = new

    # Stage D — re-validate everything
    S.stage_d_validate(list(rows.values()), smes, hist)

    # Final candidate lists (Stage-A-eligible vs the final draft) for the override dropdown
    final_draft = [r for r in rows.values() if r["sme_id"]]
    final_counts = Counter(r["sme_id"] for r in final_draft)
    for sess in ordered:
        row = rows[sess["id"]]
        survivors, eliminated = S.stage_a_hard_filter(sess, smes, final_draft, exclude_session_id=sess["id"])
        row["eliminated"] = eliminated
        own = Counter(final_counts)
        if row["sme_id"]:
            own[row["sme_id"]] -= 1
        scored = S.stage_b_score(sess, survivors, smes, hist, own, adjust)
        for c in scored:
            c["breaches_fairness"] = S.fairness_band_breach(c["sme_id"], sess["subject"], smes, hist, own)
        row["candidates"] = scored
        # The row's `score` is the number the decision was made on, in chronological order against a
        # partly-built draft. `candidates` is a different snapshot: the finished draft, with this row's
        # own assignment taken back out, which is what a reassignment would actually score. Both are
        # true and they differ, so the assigned SME's entry in that list is carried explicitly rather
        # than leaving the UI to show two numbers on unstated scales.
        row["score_now"] = next((c["score"] for c in scored if c["sme_id"] == row["sme_id"]), None)
        # A fairness flag on a row that had no within-band alternative is not a choice the pipeline
        # made badly — it is the honest floor, and the flag should say so.
        if row["sme_id"] and not any(not c["breaches_fairness"] for c in scored):
            for f in row["flags"]:
                if f["code"] == "FAIRNESS_VIOLATION" and f["sme_id"] == row["sme_id"]:
                    f["reason"] += (" Only qualified SME available for this class"
                                    if len(scored) == 1 else " No candidate for this class is inside the band")
                    f["forced"] = True
        # Direct: this row's own pairing carries a Stage E adjustment. Ripple: one of its candidates
        # had its load moved by an override elsewhere, which re-normalised this pool.
        direct = sorted({by_id[c["sme_id"]]["name"] for c in scored if c["components"]["adjustment"]})
        # Ripple reaches the whole subject pool, not just this row's candidates: Stage B normalises
        # fairness against the pool's lightest and heaviest load, so moving any pool member's load
        # re-scores every row in that pool — including rows the moved SME cannot teach.
        pool = {m["id"] for m in S.subject_pool(smes, sess["subject"])}
        ripple = sorted({by_id[sid]["name"] for sid in touched & pool} - set(direct))
        row["adjusted_from_override"] = bool(direct or ripple)
        row["override_effect"] = ({"kind": "direct" if direct else "ripple", "smes": direct or ripple}
                                  if direct or ripple else None)
        row["flags"] = S.sort_flags(row["flags"])

    draft_rows = [rows[s["id"]] for s in ordered]
    flags = S.sort_flags([f for r in draft_rows for f in r["flags"]])
    # Three views of the same band, because one number was being misread. `spread` is the 4-week
    # projected load spread (the metric of record); `inherited` is how much of it the week arrived
    # with and cannot touch; `assigned` is what this week's assignment actually contributed.
    spread, inherited, assigned_spread = {}, {}, {}
    for subject in sorted({subj for s in smes for subj in S.sme_subjects(s)}):
        pool = S.subject_pool(smes, subject)
        loads = [S.projected_load(s["id"], hist, final_counts) for s in pool]
        past = [S.past_load(hist.get(s["id"], [])) for s in pool]
        mine = [final_counts.get(s["id"], 0) for s in pool]
        spread[subject] = max(loads) - min(loads)
        inherited[subject] = max(past) - min(past)
        assigned_spread[subject] = max(mine) - min(mine)
    stats = {
        "total_sessions": len(draft_rows),
        "assigned": sum(1 for r in draft_rows if r["sme_id"]),
        "auto_assigned": sum(1 for r in draft_rows if r["stage"] == "auto"),
        "llm_resolved": sum(1 for r in draft_rows if r["stage"] == "llm"),
        "unfilled": sum(1 for r in draft_rows if not r["sme_id"]),
        "flags_by_severity": dict(Counter(f["severity"] for f in flags)),
        "flags_by_code": dict(Counter(f["code"] for f in flags)),
        "fairness_spread_per_subject": spread,
        "fairness_inherited_per_subject": inherited,
        "fairness_assigned_per_subject": assigned_spread,
        "llm": _llm_stats(items, llm),
    }
    return {"draft": draft_rows, "flags": flags, "stats": stats}


def _llm_stats(items: list[dict], llm: dict) -> dict:
    if llm.get("skipped"):
        return {"queued": len(items), "resolved": 0, "resolved_by_fallback_provider": 0, "fallback": 0,
                "provider": None, "model": None, "fallback_provider_model": None,
                "error_kind": None, "error": None, "failover": None, "message": None, "skipped": True}
    fo = llm.get("failover")
    fcfg = fallback_cfg()
    failover = None
    if fo:
        failover = {**fo, "model": fcfg["model"] if fcfg else None}
    # the banner's cause is the primary provider's problem even when the fallback rescued every row
    kind = llm["error_kind"] or (fo["kind"] if fo else None)
    return {
        "queued": len(items),
        "resolved": len(llm["decisions"]),
        "resolved_by_fallback_provider": sum(1 for d in llm["decisions"].values() if d.get("via") == "fallback"),
        "fallback": len(llm["fallback_ids"]),
        "provider": llm_provider(), "model": active_model(),
        "fallback_provider_model": fcfg["model"] if fcfg else None,
        "error_kind": kind, "error": llm["error"] or (fo and fo.get("error")),
        "failover": failover,
        "message": explain(kind, active_model(), len(llm["fallback_ids"]), failover),
    }


def _export_row(row: dict, status: str) -> dict:
    dt = S.parse_utc(row["start_utc"]).astimezone(S.IST)
    y, w, _ = dt.isocalendar()
    return {"week": f"{y}-W{w:02d}", "date": dt.strftime("%Y-%m-%d"), "time_ist": dt.strftime("%H:%M"),
            "batch": row["batch_id"], "subject": row["subject"], "sub_specialty": row.get("sub_specialty") or "",
            "session_type": row["type"], "sme_name": row.get("sme_name") or "", "status": status,
            "flags": "; ".join(f["code"] for f in row["flags"])}


def apply_approvals(draft: list[dict], decisions: list[dict]) -> dict:
    """Stage E decisions. Overrides outside Stage-A eligibility get RULE_OVERRIDE_RISK — never silently accepted."""
    rows = {r["session_id"]: {**r, "flags": list(r["flags"])} for r in draft}
    status = {sid: ("pending" if r["sme_id"] else "unfilled") for sid, r in rows.items()}
    log = []
    for d in decisions or []:
        row = rows.get(d.get("session_id"))
        if not row:
            continue
        if d.get("action") == "approve":
            status[row["session_id"]] = "approved"
        elif d.get("action") == "override" and d.get("override_sme_id"):
            target = d["override_sme_id"]
            cands = {c["sme_id"]: c for c in row.get("candidates", [])}
            prev = row["sme_id"]
            risk = None
            if target in cands:
                name, score = cands[target]["name"], cands[target]["score"]
            else:
                elim = next((e for e in row.get("eliminated", []) if e["sme_id"] == target), None)
                name = (elim or {}).get("name") or d.get("override_sme_name") or target
                rule = (elim or {}).get("rule", "subject")
                score = None
                risk = f"Override assigns {name} outside {S.rule_label(rule)}."
                row["flags"].append(S.make_flag("RULE_OVERRIDE_RISK", row["session_id"], risk, target))
            row.update(sme_id=target, sme_name=name, score=score, stage="override")
            row["flags"] = S.sort_flags(row["flags"])
            status[row["session_id"]] = "overridden"
            log.append({"session_id": row["session_id"], "batch_id": row["batch_id"], "from_sme_id": prev,
                        "to_sme_id": target, "to_sme_name": name, "rule_risk": risk})
    final = [{**r, "status": status[sid]} for sid, r in rows.items()]
    final.sort(key=lambda r: (r["start_utc"], r["session_id"]))
    return {"final_schedule": final, "override_log": log,
            "export_rows": [_export_row(r, r["status"]) for r in final]}

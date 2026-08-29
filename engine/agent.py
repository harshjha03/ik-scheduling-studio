"""Recovery & Review Copilot — an LLM tool-calling loop over `tools.py`.

The model decides what to look at and what to propose; it cannot propose anything invalid:
every returned plan is simulated here and any move that breaks a hard rule is stripped, and every
`to_sme` must have surfaced in a get_candidates / find_freeable result during this run.
Budgets are enforced here, never trusted to the model. Never raises; a failure is a labelled status.
"""
from __future__ import annotations

import json
import os
import re
import time

from . import tools as T
from .llm import LLMError, active_model, agent_llm_call, cause, classify

MAX_TOOL_CALLS = int(os.environ.get("AGENT_MAX_TOOL_CALLS", "8"))
MAX_LLM_TURNS = int(os.environ.get("AGENT_MAX_LLM_TURNS", "6"))
MAX_SWAP_DEPTH = 2
MAX_OTHER_ACTIONS = 4      # reschedules + upgrades in one plan; a plan must stay reviewable at a glance
WALL_CLOCK_S = float(os.environ.get("AGENT_WALL_CLOCK", "60"))
RESULT_CHARS = 12000       # per tool result fed back. A whole-week triage is ~7KB; truncating that mid-JSON
                           # hands the model a broken object, which is worse than a longer prompt.
RETRYABLE_KINDS = {"empty_response"}   # a 200 with no text is a bad answer, not an outage
CHAT_HISTORY = 8           # prior turns replayed into the prompt
TURN_CHARS = 700           # per replayed turn
SESSION_ID_RE = re.compile(r"\bW\d{2}-[A-Z]+-\d+-[0-9a-z]+\b")

SYSTEM = """You are the scheduling copilot for IK Scheduling Studio. You help an ops coordinator
recover from teacher drop-outs and understand the weekly draft.

You interact ONLY by calling tools. Respond with strict JSON, no prose outside it:
either {"thought": "...", "action": {"tool": "<name>", "args": {...}}}
or     {"thought": "...", "final": {"answer": "...", "plan": [...] or null}}.

Rules you must follow:
1. You may only propose moves whose to_sme appeared in a get_candidates or find_freeable
   result for that session during THIS run. Never invent a teacher.
2. Before returning any plan, you MUST call simulate_plan on it and include only moves
   that simulate as "ok" or "fairness_warning". If a move breaks a hard rule, drop or
   replace it and say why in the answer.
3. Prefer the smallest plan: fewest moves, lowest-severity sessions disturbed first
   (doubt sessions before classes, classes before mocks).
4. A fairness_warning is acceptable but must be named in your answer.
5. If no valid plan exists, say so plainly and explain the blocking rule — a clear "no
   with a reason" is a successful outcome.
6. Write answers for a non-technical coordinator: short sentences, names not ids where
   possible, one clear recommendation.
7. Only discuss this week's schedule, teachers, and sessions. For anything else reply
   with final: answer explaining you only handle scheduling, plan: null.
8. Cancelling a class is the last resort, never the first answer. Exhaust reassigning, freeing
   somebody by a swap, rescheduling, merging two batches for the hour, and a training-level upgrade
   before you propose one — and say in the answer which of those you ruled out.
9. Budget: you have at most 8 tool calls. Orient with get_draft_summary or
   get_affected_rows first; do not re-call a tool with identical args."""

# The section-6 prompt above is fixed; everything below is appended to it, never edited into it.
# Rule 5 ("a clear no with a reason is a successful outcome") is true but incomplete: a coordinator
# reading "nobody can take it" still has to do the thinking. These rules make the no actionable.
OPTIONS_ADDENDUM = """
Answering when no valid plan exists:
O1. Never stop at "nobody is eligible". Every get_candidates result lists the blocked teachers with a
    `detail` saying what would change it — read it and name the nearest miss by name, e.g. "Ananya Iyer
    is the only one who teaches this topic at this level; 15:00 Saturday is just outside her hours".
O2. Then call find_slots before you conclude a class cannot run. If another hour this week has an
    eligible teacher, that is the recommendation: name the slot and who could take it.
O3. There is a fixed ladder, cheapest first. Never offer a rung before you have ruled out the one above it:
      1. reassign the class to another eligible teacher (move)
      2. free somebody by reassigning one of THEIR lower-severity rows (find_freeable, two moves)
      3. move the class to another hour this week (reschedule)
      4. fold the class into another batch running the same topic (merge)
      5. raise a training level, but only when simulate says it actually unblocks a class (upgrade)
      6. cancel the class (cancel) — the last resort, and only when 1-5 are all impossible
O4. Five kinds of change you can put in `plan`, and the coordinator applies the whole plan with one click:
      {"kind":"move", "session_id":..., "from_sme":..., "to_sme":..., "reason":...}
      {"kind":"reschedule", "session_id":..., "to_day":"Sat", "to_hour_ist":"13:00", "reason":...}
      {"kind":"upgrade", "sme_id":..., "to_level":3, "reason":...}
      {"kind":"merge", "session_id":..., "into_session_id":..., "reason":...}
      {"kind":"cancel", "session_id":..., "reason":...}
    A reschedule needs a slot find_slots actually returned, and it names ONE slot — the best one. List
    the alternatives in `answer` if they help, but the plan carries a single hour the coordinator can
    apply. An upgrade is only for a teacher a tool result showed as blocked by training level, and
    `to_level` is that class's required level — never higher. Order does not matter; upgrades are
    applied before the moves that need them.
O4e. A merge needs a host that find_merge_candidates actually returned, and both classes must run the
    same topic at the same hour — if the host is at another hour, put a reschedule for THIS class into
    the same plan and simulate the pair together. Learners from both batches sit the one class, so say
    in the answer how many that is.
O4f. A cancel is the bottom of the ladder and it is what learners feel. Propose one only after
    get_candidates or get_issues has shown that class with zero eligible teachers AND
    find_merge_candidates and find_slots came back empty. Its `reason` is required and is read by the
    learners — write it as a sentence for them, not for the engine. If simulate returns
    "cover_warning", somebody could still take the class: drop the cancel and offer them instead.
O4d. If a class can be fixed, put the fix IN the plan. Describing a reschedule in prose while leaving
    `plan` null gives the coordinator nothing to click — that is the failure this replaced.
O4b. Simulate before you offer, in prose or in a plan. An upgrade that leaves the teacher blocked by
    something else changes nothing, and simulate_plan says so — offering it anyway wastes the
    coordinator's time. If simulate returns breaks:changes_nothing, do not recommend it in words either.
O4c. `plan` holds only those three shapes. Anything outside them — publishing, e-mailing, editing a
    profile, an override that breaks a hard rule — is a sentence in `answer`, never a plan entry.
O5. Two or three concrete options beat a paragraph of apology. Do not say "unfortunately" twice.
O6. Lead with the state, not the regret: "Neither class can be staffed as scheduled." then the options."""

# Chat is a third mode, so its extra rules are appended too.
CHAT_ADDENDUM = """
You are in a running chat with the coordinator. Extra rules for chat:
C1. Match the shape of the answer to the question. One thing to say -> two or three short sentences.
    More than one class, teacher or option -> structure it, using exactly this plain-text layout:
      a label line per subject, e.g. "DSA-01 · Sat 15:00 · Dynamic Programming"
      then one option per line starting with "- ", shortest-effort option first, e.g.
      "- Move to Sat 13:00 — Ananya Iyer can take it (recommended)"
      "- Or upgrade Rohan Mehta to level 3"
    A blank line between subjects. No markdown, no ** or #, no numbered lists, no tables.
    At most three options per subject, and one closing line for anything only they can authorise.
    Names, days and times, never ids — unless the coordinator used an id first.
C2. When they name a teacher or a batch in words ("Priya", "the Friday DSA class"), resolve it with
    list_teachers or get_draft_summary first. Never guess an id.
C3. When they tell you someone is unavailable, sick, on leave or dropping out, call report_unavailable
    FIRST (with days if they named any), then find cover for what it returns.
C4. You can apply five things, all through a plan the coordinator approves: reassign a class (move),
    move a class to another hour (reschedule), raise a teacher's training level (upgrade), fold two
    batches into one class for a single hour (merge), and — only when nothing else works — cancel a
    class (cancel). Work down that ladder in that order and say which rungs you ruled out. You cannot
    publish the week, e-mail or message anyone, export a CSV, create a batch or a class, edit contact
    details, or force an assignment that breaks a hard rule. If asked for one of those, say plainly that
    it is not yours to do. NEVER name a button, tab or screen unless the coordinator named it first —
    you cannot see the app, and inventing "the Upgrade button" sends them looking for something that
    does not exist. Describe the change instead, and leave finding it to them.
C5. If a turn is small talk or a follow-up question, just answer it — a reply with no plan is fine.
C6. Read the conversation so far before acting; do not redo work you already did in an earlier turn."""

TOOL_DOC = """Tools (args are JSON objects; `week` is optional and always this week):
- get_draft_summary {week}                      counts by status, flags, unfilled list, load per teacher
- get_issues {codes?: ["FAIRNESS_VIOLATION"], limit?}   EVERY flagged class with its blockers, candidates and
    (when nothing is eligible) its swaps and slots, in one call. Use this whenever the coordinator says
    "all the issues" or names more than one class — it is what makes a sweep fit in the budget.
- get_row {session_id}                          one session: assignment, score, flags with reasons
- get_affected_rows {sme_id, days?: ["Wed"]}    sessions this teacher holds (optionally on given days)
- get_candidates {session_id}                   eligible teachers with score and breaches_fairness, plus who is blocked and why
- get_sme {sme_id}                              profile, availability, load, this week's sessions
- find_freeable {session_id}                    teachers who become eligible if one of their lower-severity rows is moved, with replacements for that row
- find_slots {session_id, limit?}               other hours this week where somebody could teach this class (use it before concluding a class cannot run)
- find_merge_candidates {session_id, limit?}    other batches running the same topic that this class could be folded into for one hour
- simulate_plan also accepts {kind: "reschedule", session_id, to_day, to_hour_ist}, {kind: "upgrade", sme_id, to_level},
    {kind: "merge", session_id, into_session_id} and {kind: "cancel", session_id, reason}
- list_teachers {}                              every teacher: id, name, subjects, topics, level, load — use this to turn a name into an id
- report_unavailable {sme_id, days?: ["Wed"]}   mark a teacher unavailable for the rest of this run and get the sessions needing cover
- simulate_plan {plan: [entry, ...]}            verdict per entry: ok | fairness_warning | breaks:<rule>.
    `plan` holds every kind together — moves, reschedules and upgrades in one list, never split by kind.
A move is {"session_id": "...", "from_sme": "...", "to_sme": "...", "reason": "..."}."""


def _goal(mode: str, ctx: dict, sme_id: str | None, days: list[str] | None, question: str | None) -> str:
    if mode == "chat":
        return f"Week {ctx['week']}. The coordinator says: {question}"
    if mode == "recovery":
        name = next((s["name"] for s in ctx["smes"] if s["id"] == sme_id), sme_id)
        when = f"on {', '.join(days)}" if days else "for the whole week"
        return (f"Week {ctx['week']}. {name} ({sme_id}) is unavailable {when}. Re-staff every session they hold "
                f"{when} with the smallest valid plan. Start with get_affected_rows.")
    return f"Week {ctx['week']}. Coordinator's question: {question}"


def _digest(result) -> str:
    """One line per tool result for the transcript — enough to follow the reasoning, not the payload."""
    if isinstance(result, dict):
        if "verdicts" in result:
            def one(v):
                if v["kind"] == "upgrade":
                    return f"{v['sme_name']} to level {v['to_level']}: {v['verdict']}"
                if v["kind"] == "reschedule":
                    return f"{v['session_id']} → {v['to_day']} {v['to_hour_ist']}: {v['verdict']}"
                return f"{v['session_id']} → {v.get('to_sme_name') or v.get('to_sme')}: {v['verdict']}"
            return "; ".join(one(v) for v in result["verdicts"]) or "nothing to simulate"
        if "candidates" in result:
            c = result["candidates"]
            return f"{len(c)} eligible: " + ", ".join(f"{x['name']} ({x['score']:.2f}{'*' if x.get('breaches_fairness') else ''})" for x in c[:5]) \
                if c else f"nobody eligible; {len(result.get('eliminated', []))} blocked"
        if "slots" in result:
            sl = result["slots"]
            return (f"could also run: " + ", ".join(f"{x['day']} {x['hour_ist']} ({', '.join(p['name'] for p in x['eligible'][:2])})"
                                                    for x in sl[:3])) if sl else "no other hour this week works"
        if "issues" in result:
            return (f"{result['shown']} of {result['total_flagged']} flagged: " + "; ".join(
                f"{i['session_id']} ({','.join(f['code'] for f in i['flags']) or 'unfilled'}, "
                f"{len(i['candidates'])} candidate(s)"
                + (f", {len(i.get('slots') or [])} slot(s)" if not i["candidates"] else "") + ")"
                for i in result["issues"][:4])) + ("; …" if result["shown"] > 4 else "")
        if "freeable" in result:
            f = result["freeable"]
            return ", ".join(f"{x['name']} if {x['frees_session']['session_id']} ({x['frees_session']['type']}) moves"
                             f"{'' if x['has_replacement'] else ' — no replacement'}" for x in f[:4]) or "nobody can be freed"
        if "rows" in result and "count" in result:
            return f"{result['count']} session(s): " + ", ".join(f"{r['session_id']} ({r['type']}, {r['day']})" for r in result["rows"])
        if "by_status" in result:
            return f"{result['by_status']['assigned']} assigned, {result['by_status']['unfilled']} unfilled; flags {result['flags_by_code']}"
        if "this_week_count" in result:
            return f"{result['name']}: {result['this_week_count']} session(s) this week, level {result['training_level']}"
        if "flags" in result and "session_id" in result:
            return f"{result['session_id']}: {result.get('sme_name') or 'unfilled'}; " + "; ".join(f"{f['code']}: {f['reason']}" for f in result["flags"])[:200]
    return json.dumps(result)[:200]


ANSWER_KEYS = ("answer", "response", "message", "text", "reply", "content")


def _parse(raw) -> tuple[str, dict | str]:
    """('action', {tool,args}) | ('final', {answer,plan}) | ('error', message).

    Lenient on the shape of a *finished* answer and strict on everything that acts. A model replying
    {"answer": "..."} to a conversational turn has said what it means; rejecting that as a protocol
    error, twice, and showing a fallback banner was the wrong trade.
    """
    if isinstance(raw, str) and raw.strip():
        return "final", {"answer": raw.strip(), "plan": None}
    if not isinstance(raw, dict):
        return "error", "response must be a JSON object"
    if isinstance(raw.get("action"), dict):
        a = raw["action"]
        if not isinstance(a.get("tool"), str):
            return "error", "action.tool must be a string"
        return "action", {"tool": a["tool"], "args": a.get("args") or {}}
    if isinstance(raw.get("action"), str):          # {"action": "get_row", "args": {...}}
        return "action", {"tool": raw["action"], "args": raw.get("args") or {}}
    if isinstance(raw.get("tool"), str):            # the action fields, unwrapped
        return "action", {"tool": raw["tool"], "args": raw.get("args") or {}}
    fin = raw.get("final")
    if isinstance(fin, dict) or isinstance(fin, str) or any(isinstance(raw.get(k), str) for k in ANSWER_KEYS):
        f = fin if isinstance(fin, dict) else ({"answer": fin} if isinstance(fin, str) else raw)
        plan = f.get("plan", raw.get("plan"))
        if plan is not None and not isinstance(plan, list):
            return "error", "plan must be a list of entries or null"
        answer = next((str(f[k]) for k in ANSWER_KEYS if isinstance(f.get(k), str) and f[k].strip()), "")
        if not answer and plan is None:
            return "error", 'reply with {"final": {"answer": "...", "plan": [...] or null}}'
        return "final", {"answer": answer, "plan": plan}
    return "error", 'response must contain either "action" or "final"'


LADDER_RUNGS = ("candidates", "merge", "slots")
RUNG_LABEL = {"candidates": "no eligible teacher", "merge": "no batch to merge into",
              "slots": "no other hour that works"}


def _register(ctx: dict, body: dict, result: dict, seen: dict, slots_seen: dict, upgrades_seen: dict,
              hosts_seen: dict, dead_ends: dict) -> None:
    """Record what this tool actually showed the model, so provenance can be checked later.

    Every tool that names a session and its options counts — the model legitimately read candidate names
    off get_draft_summary and get_issues, and rejecting those picks for "no provenance" was a false alarm.
    """
    rows = T._rows(ctx)
    # each block of the payload that is about one session: the result itself, plus any list inside it
    blocks = [{"session_id": body.get("args", {}).get("session_id"), **result}]
    for key in ("issues", "unfilled", "rows"):
        blocks += [b for b in (result.get(key) or []) if isinstance(b, dict)]
    for b in blocks:
        sid = b.get("session_id")
        if not sid or sid not in rows:
            continue
        seen.setdefault(sid, set()).update(
            c["sme_id"] for c in (b.get("candidates") or []) if isinstance(c, dict) and c.get("sme_id"))
        slots_seen.setdefault(sid, set()).update(
            (x["day"], x["hour_ist"]) for x in (b.get("slots") or []) if isinstance(x, dict) and x.get("day"))
        need = int(rows[sid].get("required_training_level", 1))
        for e in (b.get("eliminated") or []) + (b.get("blocked") or []):
            if isinstance(e, dict) and e.get("rule") == "training_level":
                upgrades_seen[e["sme_id"]] = max(upgrades_seen.get(e["sme_id"], 0), need)
    # a freeable entry vouches for the teacher it frees *and* for that row's replacements
    for f in (result.get("freeable") or []):
        target = body.get("args", {}).get("session_id")
        if target:
            seen.setdefault(target, set()).add(f["sme_id"])
        freed = f.get("frees_session")
        freed_id = freed.get("session_id") if isinstance(freed, dict) else freed
        if freed_id:
            seen.setdefault(freed_id, set()).update(
                c["sme_id"] for c in (f.get("replacement_candidates") or []))
    for issue in (result.get("issues") or []):
        for f in (issue.get("freeable") or []):
            seen.setdefault(issue["session_id"], set()).add(f["sme_id"])

    # Which rungs of the ladder this run has actually watched come back empty. A cancel is only
    # allowed once all three have — the model may not skip to the bottom because it is quicker.
    target = body.get("args", {}).get("session_id")
    for b in blocks:
        sid = b.get("session_id")
        if not sid or sid not in rows:
            continue
        if "candidates" in b and not b["candidates"]:
            dead_ends.setdefault(sid, set()).add("candidates")
        if "merge_options" in b and not b["merge_options"]:
            dead_ends.setdefault(sid, set()).add("merge")
        if "slots" in b and not b["slots"]:
            dead_ends.setdefault(sid, set()).add("slots")
        hosts_seen.setdefault(sid, set()).update(
            h["session_id"] for h in (b.get("merge_options") or []) if isinstance(h, dict) and h.get("session_id"))
    if target and target in rows and "hosts" in result:
        hosts_seen.setdefault(target, set()).update(
            h["session_id"] for h in (result["hosts"] or []) if isinstance(h, dict) and h.get("session_id"))
        if not result["hosts"]:
            dead_ends.setdefault(target, set()).add("merge")
    if target and target in rows and "slots" in result and not result["slots"]:
        dead_ends.setdefault(target, set()).add("slots")


def _fallback_plan(ctx: dict, affected: list[dict]) -> list[dict]:
    """The floor: top eligible candidate per affected row, no swap chains. Same idea as LLM_FALLBACK."""
    plan = []
    for r in affected:
        cands = T.get_candidates(ctx, r["session_id"])["candidates"]
        if cands:
            top = cands[0]
            plan.append({"session_id": r["session_id"], "from_sme": r["sme_id"], "to_sme": top["sme_id"],
                         "reason": f"Highest-scoring eligible teacher ({top['score']:.2f}) — deterministic fallback.",
                         "flag": "AGENT_FALLBACK"})
    return plan


def _review_fallback_answer(ctx: dict, question: str | None, why: str) -> str:
    """The floor for a question: the engine's own numbers, prefixed by the true reason we are here."""
    s = T.get_draft_summary(ctx)
    lines = [f"{why}", f"Draft as the engine sees it · {s['by_status']['assigned']} of {s['total_sessions']} staffed "
             f"· {s['by_status']['unfilled']} unfilled", ""]
    ids = SESSION_ID_RE.findall(question or "")
    rows = [T.get_row(ctx, i) for i in ids if i in T._rows(ctx)] or [T.get_row(ctx, u["session_id"]) for u in s["unfilled"]]
    for r in rows:
        lines.append(f"{r['batch_id']} · {r['time_ist']} · {r.get('sub_specialty') or r['type']}")
        lines.append(f"- {r['sme_name'] or 'No teacher assigned'}")
        for f in r["flags"]:
            lines.append(f"- {f['reason']}")
        lines.append("")
    return "\n".join(lines).strip()


def _provenance_error(a: dict, prov: dict, names: dict) -> str | None:
    """Why this entry is not something the copilot was actually shown. None when it checks out."""
    if a["kind"] == "move":
        if a["to_sme"] not in prov["seen"].get(a["session_id"], set()):
            return (f"{names.get(a['to_sme'], a['to_sme'])} never appeared as a candidate for {a['session_id']} "
                    f"during this run (provenance rule)")
    elif a["kind"] == "reschedule":
        slot = (a.get("to_day"), a.get("to_hour_ist"))
        if slot not in prov["slots"].get(a["session_id"], set()):
            return (f"{slot[0]} {slot[1]} was never returned by find_slots for {a['session_id']} — the copilot "
                    f"may only offer a slot it checked")
    elif a["kind"] == "merge":
        if a["into_session_id"] not in prov["hosts"].get(a["session_id"], set()):
            return (f"{a['into_session_id']} was never returned by find_merge_candidates for "
                    f"{a['session_id']} — the copilot may only merge into a host it checked")
    elif a["kind"] == "cancel":
        # The guard that matters most. A copilot that cancels a class it could have covered is worse
        # than one that gives up, so this is enforced here and not left to the prompt.
        proved = prov["dead_ends"].get(a["session_id"], set())
        missing = [r for r in LADDER_RUNGS if r not in proved]
        if missing:
            return (f"{a['session_id']} was not shown to be beyond rescue — the copilot never confirmed "
                    f"{' or '.join(RUNG_LABEL[r] for r in missing)}, and cancelling is the last resort")
    elif a["kind"] == "upgrade":
        allowed = prov["upgrades"].get(a.get("sme_id"))
        if not allowed:
            return (f"{names.get(a.get('sme_id'), a.get('sme_id'))} was never shown as blocked by training level, "
                    f"so a level change is not the copilot's to propose")
        if int(a.get("to_level") or 0) > allowed:
            return (f"level {a.get('to_level')} is higher than the {allowed} the class requires — the copilot "
                    f"may only raise a level as far as a class needs")
    return None


def _enforce(ctx: dict, plan: list[dict] | None, prov: dict, affected: list[dict],
             notes: list[str], stray: list[str] | None = None) -> tuple[list[dict] | None, dict | None]:
    """Provenance → size cap → simulate → strip breaks (re-simulating until stable). Returns (plan, simulation)."""
    if not plan:
        return None, None
    names = {s["id"]: s["name"] for s in ctx["smes"]}
    kept, moves, bad_shape = [], [], []
    for m in plan:
        try:
            norm = T._norm_action(ctx, m, 0) if isinstance(m, dict) else None
        except T.ToolError:
            norm = None
        if norm is None:
            # The model sometimes writes its prose options into `plan`. They are not applyable, but they
            # are what the coordinator asked for — keep the text, drop it from the plan, say so once.
            text = m if isinstance(m, str) else (m.get("reason") or m.get("option") or m.get("text") or "") if isinstance(m, dict) else ""
            if isinstance(text, str) and len(text.strip()) > 3 and stray is not None:
                stray.append(text.strip())
            bad_shape.append(m)
            continue
        why = _provenance_error(norm, prov, names)
        if why:
            notes.append(f"Dropped one step — {why}.")
            continue
        moves.append(m)
    if bad_shape:
        notes.append(f"{len(bad_shape)} suggestion(s) were not staffing moves the copilot can apply — "
                     f"they are listed above as options for you to action.")
    # Swap depth doubles as a size cap — each affected row may carry at most one freeing move on top of
    # its own, plus a bounded number of reschedules/upgrades so a plan stays reviewable in one glance.
    staffing = [m for m in moves if T._kind_of(m) == "move"]
    other = [m for m in moves if T._kind_of(m) != "move"]
    cap = MAX_SWAP_DEPTH * max(1, len(affected) or len({m["session_id"] for m in staffing}) or 1)
    if len(staffing) > cap or len(other) > MAX_OTHER_ACTIONS:
        notes.append(f"Plan trimmed to {cap} staffing move(s) and {MAX_OTHER_ACTIONS} other change(s).")
    moves = staffing[:cap] + other[:MAX_OTHER_ACTIONS]
    sim = None
    for _ in range(3):        # dropping a move can invalidate a move that relied on it — settle
        if not moves:
            break
        try:
            sim = T.simulate_plan(ctx, moves)
        except T.ToolError as e:
            notes.append(f"Plan rejected: {e}.")
            return None, None
        bad = [v for v in sim["verdicts"] if v["verdict"].startswith("breaks")]
        if not bad:
            kept = list(sim["verdicts"])          # normalised, with verdict and detail attached
            break
        for v in bad:
            who = v.get("to_sme_name") or v.get("sme_name") or v.get("session_id")
            notes.append(f"Dropped {v['kind']} ({who}): {v['detail'] or v['verdict']}")
        dropped_rows = {v.get("session_id") for v in bad if v["kind"] != "upgrade"}
        dropped_smes = {v.get("sme_id") for v in bad if v["kind"] == "upgrade"}
        moves = [m for m in moves
                 if not (T._kind_of(m) != "upgrade" and m.get("session_id") in dropped_rows)
                 and not (T._kind_of(m) == "upgrade" and m.get("sme_id") in dropped_smes)]
    return (kept or None), sim


def run_agent(ctx: dict, mode: str, sme_id: str | None = None, days: list[str] | None = None,
              question: str | None = None, turns: list[dict] | None = None, llm_call=None,
              clock=time.monotonic) -> dict:
    """Returns {status: ok|budget_exhausted|fallback, answer, plan, transcript, simulation, meta}.
    `llm_call(system, messages) -> dict` is injectable so tests script the model without a network."""
    llm_call = llm_call or agent_llm_call
    t0 = clock()
    transcript: list[dict] = []
    seen: dict[str, set[str]] = {}          # session_id -> to_sme ids the model has actually been shown
    slots_seen: dict[str, set[tuple[str, str]]] = {}   # session_id -> (day, hour) find_slots returned
    upgrades_seen: dict[str, int] = {}      # sme_id -> the level a class actually requires of them
    hosts_seen: dict[str, set[str]] = {}    # session_id -> hosts find_merge_candidates offered for it
    dead_ends: dict[str, set[str]] = {}     # session_id -> ladder rungs this run watched come back empty
    called: set[str] = set()
    affected = T.get_affected_rows(ctx, sme_id, days)["rows"] if mode == "recovery" and sme_id else []
    goal = _goal(mode, ctx, sme_id, days, question)
    if turns and mode == "chat":      # QA-09: review and recovery are single shots whatever the caller sends
        goal = ("Conversation so far:\n"
                + "\n".join(f"{'Coordinator' if t.get('role') == 'user' else 'You'}: {str(t.get('content') or '')[:TURN_CHARS]}"
                             for t in turns[-CHAT_HISTORY:])
                + "\n\n" + goal)
    log: list[str] = []      # the run so far, replayed as one user message every turn
    system = SYSTEM + OPTIONS_ADDENDUM + (CHAT_ADDENDUM if mode == "chat" else "") + "\n\n" + TOOL_DOC
    status, final, error, plain, tool_calls, turns, bad_streak = "ok", None, None, None, 0, 0, 0

    while final is None:
        if turns >= MAX_LLM_TURNS or tool_calls >= MAX_TOOL_CALLS or clock() - t0 > WALL_CLOCK_S:
            status = "budget_exhausted"
            break
        turns += 1
        # One rolling user message, never a replayed assistant turn: thinking models hand back an
        # opaque signature with their answer and return empty content when it is not echoed exactly.
        prompt = goal + ("\n\nWhat you have done so far:\n" + "\n".join(log) if log else "")
        # The turn budget is smaller than the tool budget, so a run that keeps exploring can never
        # answer. Spend the last turn on the answer: say so, and the model stops mid-search.
        if turns == MAX_LLM_TURNS or tool_calls == MAX_TOOL_CALLS - 1:
            prompt += ("\n\nThis is your LAST step for THIS message — no further tool calls are possible now. "
                       'Reply with "final" using what you already know. If the job is only part done, say what '
                       "you fixed, what is left, and that the coordinator can just ask you to carry on — the "
                       "budget is per message, so NEVER tell them to start a new session or a new chat.")
        try:
            raw = llm_call(system, [{"role": "user", "content": prompt}])
        except Exception as exc:          # provider down / quota / bad JSON — classified, then the floor
            kind_ = classify(exc)
            if kind_ in RETRYABLE_KINDS and bad_streak < 1:
                bad_streak += 1
                transcript.append({"thought": "", "tool": None, "args": None,
                                   "result_digest": f"provider returned nothing ({kind_}) — retrying once", "error": True})
                log.append("- Your last reply was empty or unparseable. Answer with one short JSON object and no other text.")
                continue
            status, error, plain = "fallback", f"{kind_}: {exc}", cause(kind_, active_model())
            break
        kind, body = _parse(raw)
        if kind == "action":
            key = json.dumps({"t": body["tool"], "a": body["args"]}, sort_keys=True)
            if key in called:
                kind, body = "error", f"{body['tool']} was already called with identical args — use the earlier result"
            else:
                try:
                    result = T.call_tool(ctx, body["tool"], body["args"])
                except T.ToolError as e:
                    kind, body = "error", str(e)
        if kind == "error":
            bad_streak += 1
            transcript.append({"thought": raw.get("thought", "") if isinstance(raw, dict) else "", "tool": None,
                               "args": None, "result_digest": f"invalid response: {body}", "error": True})
            if bad_streak >= 2:
                status, error = "fallback", f"model produced two invalid responses in a row: {body}"
                plain = "The model kept replying in a form the scheduler could not use."
                break
            log.append(f"- Your last reply was rejected — error: {body}. Reply with valid JSON: an action or a final.")
            continue
        bad_streak = 0
        if kind == "final":
            final = body
            break
        tool_calls += 1
        called.add(key)
        if isinstance(result, dict) and "_ctx" in result:
            ctx = result.pop("_ctx")            # report_unavailable: the drop-out now applies to every later call
            affected = result["rows"]
        _register(ctx, body, result, seen, slots_seen, upgrades_seen, hosts_seen, dead_ends)
        transcript.append({"thought": raw.get("thought", ""), "tool": body["tool"], "args": body["args"],
                           "result_digest": _digest(result)})
        log.append(f"- {body['tool']}({json.dumps(body['args'])}) returned: "
                   + json.dumps(result, default=str)[:RESULT_CHARS])

    notes: list[str] = []
    stray: list[str] = []
    prov = {"seen": seen, "slots": slots_seen, "upgrades": upgrades_seen,
            "hosts": hosts_seen, "dead_ends": dead_ends}
    if final is not None:
        answer = final["answer"].strip()
        plan, sim = _enforce(ctx, final["plan"], prov, affected, notes, stray)
        if stray:      # options the model filed under `plan`; they belong in the text, one per line
            answer = (answer + "\n" if answer else "") + "\n".join(
                t if t.startswith(("-", "•")) else f"- {t}" for t in stray)
        if final["plan"] and not plan:
            notes.append("Every proposed move was dropped, so there is no plan to apply.")
    else:
        # the floor: the formula's answer, never "no answer"
        if mode == "recovery" or (mode == "chat" and affected):
            fb = _fallback_plan(ctx, affected)
            for m in fb:
                seen.setdefault(m["session_id"], set()).add(m["to_sme"])
            plan, sim = _enforce(ctx, fb, prov, affected, notes)
            covered = {m["session_id"] for m in plan or []}
            missing = [r["session_id"] for r in affected if r["session_id"] not in covered]
            answer = (("The copilot ran out of budget" if status == "budget_exhausted" else "The copilot could not finish")
                      + f" ({plain or error or f'{tool_calls} tool calls, {turns} turns'}) "
                      + (f"Fallback plan: the highest-scoring eligible teacher for {len(plan)} of {len(affected)} affected session(s)."
                         if plan else f"No eligible direct replacement was found for the {len(affected)} affected session(s).")
                      + (f" Still uncovered: {', '.join(missing)} — nobody is eligible without a swap." if missing else ""))
        else:
            plan, sim = None, None
            why = (f"The copilot used its whole budget ({tool_calls} tool calls, {turns} turns) without "
                   f"reaching an answer." if status == "budget_exhausted"
                   else f"The copilot could not finish — {plain or error}")
            answer = _review_fallback_answer(ctx, question, why)
            if transcript:
                answer += "\n\nWhat it did establish\n" + "\n".join(
                    f"- {st['result_digest']}" for st in transcript[-3:] if not st.get("error"))
    if notes:
        answer = (answer + "\n\n" if answer else "") + "\n".join(notes)
    return {"status": status, "answer": answer, "plan": plan, "transcript": transcript, "simulation": sim,
            "meta": {"tool_calls": tool_calls, "llm_turns": turns, "elapsed_s": round(clock() - t0, 2),
                     "model": active_model(), "error": error, "error_plain": plain,
                     "affected": [r["session_id"] for r in affected]}}


__all__ = ["run_agent", "SYSTEM", "CHAT_ADDENDUM", "OPTIONS_ADDENDUM", "MAX_OTHER_ACTIONS", "MAX_TOOL_CALLS", "MAX_LLM_TURNS", "MAX_SWAP_DEPTH", "WALL_CLOCK_S", "LLMError"]

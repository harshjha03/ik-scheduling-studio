"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import sessionsCurrentJson from "@/data/sessions_current.json";
import sessionsNextJson from "@/data/sessions_next.json";
import smesJson from "@/data/smes.json";
import smesCurrentJson from "@/data/smes_current.json";
import historyJson from "@/data/history.json";
import batchesJson from "@/data/batches.json";
import coursesJson from "@/data/courses.json";
import weeksJson from "@/data/weeks.json";
import metaJson from "@/data/meta.json";
import type {
  Batch, Category, Course, Decision, DraftRow, HistoryRecord, Meta, ModuleKey, OverrideEvent, Role, RunResult,
  Session, SheetState, SME, WeekKey, WeekMeta,
} from "@/lib/types";
import { runMatching, submitApprovals } from "@/lib/api";
import { csvExporter } from "@/lib/export";
import {
  FLAG_LABEL, SEV_CHIP, applyAvailabilityBlocks, initials, istParts, nextBatchId, newBatchSessions, sheetCandidates,
  weekAsHistory, workItems,
} from "@/lib/view";
import Sidebar, { MODULES, PERSONA, ROLE_MODULES } from "./components/Sidebar";
import LlmBanner from "./components/LlmBanner";
import KpiCards from "./components/KpiCards";
import Dashboard from "./components/Dashboard";
import SmeManagement from "./components/SmeManagement";
import BatchManagement from "./components/BatchManagement";
import MyWeek, { type PendingChange } from "./components/MyWeek";
import Sheet, { PersonRow, SectionLabel } from "./components/Sheet";
import Toast from "./components/Toast";

const SESSIONS: Record<WeekKey, Session[]> = {
  current: sessionsCurrentJson as unknown as Session[],
  next: sessionsNextJson as unknown as Session[],
};
const SMES: Record<WeekKey, SME[]> = {
  current: smesCurrentJson as unknown as SME[],
  next: smesJson as unknown as SME[],
};
const HISTORY = historyJson as unknown as HistoryRecord[];
const BATCHES0 = batchesJson as unknown as Batch[];
const COURSES = coursesJson as unknown as Record<string, Course>;
const WEEKS = weeksJson as unknown as Record<WeekKey, WeekMeta>;
const META = metaJson as unknown as Meta;

const flagKey = (r: DraftRow) => r.flags.map((f) => f.code).sort().join("|");

export default function Page() {
  const [role, setRole] = useState<Role>("coordinator");
  const [mod, setMod] = useState<ModuleKey>("dashboard");
  const [week, setWeek] = useState<WeekKey>("next");
  const [tab, setTab] = useState<"schedule" | "overrides">("schedule");
  const [runs, setRuns] = useState<Partial<Record<WeekKey, RunResult>>>({});
  const [changed, setChanged] = useState<Set<string>>(new Set());
  const [approved, setApproved] = useState<Set<string>>(new Set());
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [overrides, setOverrides] = useState<OverrideEvent[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [leave, setLeave] = useState<Record<string, string>>({});
  const [droppedOut, setDroppedOut] = useState<string[]>([]);
  const [availOff, setAvailOff] = useState<Record<string, boolean>>({});   // SME view: blocks switched off
  const [pending, setPending] = useState<PendingChange[]>([]);
  const [batches, setBatches] = useState<Batch[]>(BATCHES0);
  const [extraSessions, setExtraSessions] = useState<Session[]>([]);
  const [selSme, setSelSme] = useState("T01");
  const [selBatch, setSelBatch] = useState("DSA-01");
  const [batchFilter, setBatchFilter] = useState("all");
  const [statusOff, setStatusOff] = useState<Record<string, boolean>>({});
  const [sheet, setSheet] = useState<SheetState>(null);
  const [armed, setArmed] = useState<string | null>(null);   // risky override awaiting confirmation
  const [toast, setToast] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [newBatch, setNewBatch] = useState({ course: "DSA", level: "beginner", per_week: 4, learners: 30 });
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const nextRef = useRef<DraftRow[] | null>(null);

  const say = useCallback((msg: string) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast(msg);
    toastTimer.current = setTimeout(() => setToast(null), 2800);
  }, []);

  const smesFor = useCallback((w: WeekKey): SME[] => SMES[w].map((s) => (
    droppedOut.includes(s.id) ? { ...s, weekly_availability: [] } : s
  )), [droppedOut]);

  const sessionsFor = useCallback((w: WeekKey): Session[] => (
    w === "next" ? [...SESSIONS.next, ...extraSessions] : SESSIONS.current
  ), [extraSessions]);

  /** Draft the next week on top of the settled current week. */
  const runNext = useCallback(async (opts: {
    overrides?: OverrideEvent[]; dropped?: string[]; sessions?: Session[];
    availOff?: Record<string, boolean>; quiet?: boolean;
  } = {}) => {
    const cur = runs.current;
    if (!cur) return;
    setLoading(true);
    try {
      const dropped = opts.dropped ?? droppedOut;
      const blocks = opts.availOff ?? availOff;
      const smes = SMES.next.map((s) => {
        if (dropped.includes(s.id)) return { ...s, weekly_availability: [] };
        return s.id === META.me ? applyAvailabilityBlocks(s, blocks) : s;   // the SME's own blocks
      });
      const history = weekAsHistory(cur.draft, SMES.next, WEEKS.current.iso, HISTORY);
      const sessions = opts.sessions ?? sessionsFor("next");
      const res = await runMatching(sessions, smes, history, opts.overrides ?? overrides, { llm: true });
      const prev = nextRef.current;
      const changedIds = new Set<string>();
      if (prev) {
        const before = new Map(prev.map((r) => [r.session_id, r]));
        res.draft.forEach((r) => {
          const p = before.get(r.session_id);
          if (!p || p.sme_id !== r.sme_id || flagKey(p) !== flagKey(r)) changedIds.add(r.session_id);
        });
      }
      nextRef.current = res.draft;
      setChanged(changedIds);
      setRuns((s) => ({ ...s, next: res }));
      setApproved((a) => new Set([...a].filter((id) => !res.draft.some((r) => r.session_id === id))));
      setDecisions({});
      setOverrides((log) => log.map((o) => (o.week === "next" ? {
        ...o,
        changed_rows: res.draft
          .filter((r) => changedIds.has(r.session_id) && r.batch_id === o.batch_id)
          .map((r) => r.session_id),
        // a pick that breaks a hard rule can never survive Stage A — say so instead of going quiet
        reverted: !res.draft.some((r) => r.session_id === o.session_id && r.sme_id === o.to_sme_id),
      } : o)));
      if (!opts.quiet) say(changedIds.size ? `Draft re-run — ${changedIds.size} row(s) changed.` : "Draft re-run — nothing changed.");
      return res;
    } catch (e) {
      say(String(e).slice(0, 160));
    } finally {
      setLoading(false);
    }
  }, [runs.current, droppedOut, availOff, overrides, sessionsFor, say]);

  // initial load: settle the current week (no LLM), then draft the next on top of it
  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const cur = await runMatching(SESSIONS.current, SMES.current, HISTORY, [], { llm: false });
        if (!alive) return;
        setRuns((s) => ({ ...s, current: cur }));
        setApproved(new Set(cur.draft.filter((r) => r.sme_id).map((r) => r.session_id)));
        const history = weekAsHistory(cur.draft, SMES.next, WEEKS.current.iso, HISTORY);
        const nxt = await runMatching(SESSIONS.next, SMES.next, history, [], { llm: true });
        if (!alive) return;
        nextRef.current = nxt.draft;
        setRuns((s) => ({ ...s, next: nxt }));
      } catch (e) {
        say(String(e).slice(0, 160));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [say]);

  const run = runs[week];
  const rows = useMemo(() => run?.draft ?? [], [run]);
  const smes = useMemo(() => smesFor(week), [smesFor, week]);
  const smeName = useCallback((id: string | null) => SMES.next.find((s) => s.id === id)?.name ?? id ?? "—", []);
  const me = SMES.next.find((s) => s.id === META.me)!;
  const [preferred, setPreferred] = useState(me.preferred);
  // what the SME view shows must be what the draft was run against
  const meNow = useMemo(() => applyAvailabilityBlocks(me, availOff), [me, availOff]);

  const weekDates = useMemo(() => {
    const first = SESSIONS[week][0]?.start_utc ?? new Date().toISOString();
    const p0 = istParts(first);
    const base = new Date(new Date(first).getTime() - p0.day * 864e5);
    return META.days.map((d, i) => ({ day: d, date: istParts(new Date(base.getTime() + i * 864e5).toISOString()).date }));
  }, [week]);

  const filtered = useMemo(
    () => (batchFilter === "all" ? rows : rows.filter((r) => r.batch_id === batchFilter)),
    [rows, batchFilter],
  );
  const work = useMemo(
    () => workItems(rows, smes, dismissed, week === "next" ? leave : {}),
    [rows, smes, dismissed, leave, week],
  );

  // ---------- actions ----------

  const openClass = (sessionId: string, stage: "info" | "pick" = "info") => {
    setArmed(null);
    setSheet({ kind: "class", sessionId, week, stage });
  };

  const assign = (row: DraftRow, smeId: string, name: string, blocked: boolean) => {
    setArmed(null);
    if (WEEKS[week].locked) {
      setPending((p) => [...p.filter((x) => x.session_id !== row.session_id),
        { session_id: row.session_id, sme_id: smeId, name, from_sme_id: row.sme_id }]);
      setOverrides((log) => [{
        kind: "change requested", session_id: row.session_id, batch_id: row.batch_id, week,
        from_sme_id: row.sme_id, to_sme_id: smeId, to_sme_name: name, at: new Date().toISOString(),
        note: "Waiting for the SME to approve before learners see it.", changed_rows: [],
      }, ...log]);
      setSheet(null);
      say(`Change request sent to ${name}.`);
      return;
    }
    setDecisions((d) => ({ ...d, [row.session_id]: { session_id: row.session_id, action: "override", override_sme_id: smeId } }));
    setOverrides((log) => [{
      kind: row.sme_id ? "teacher change" : "assigned", session_id: row.session_id, batch_id: row.batch_id, week,
      from_sme_id: row.sme_id, to_sme_id: smeId, to_sme_name: name, at: new Date().toISOString(),
      note: blocked
        ? "Breaks a hard rule — kept visible as OVERRIDE RISK on the row."
        : "−0.2 on the old pairing, +0.1 on yours in the next run.",
    }, ...log]);
    setRuns((s) => {
      const cur = s[week];
      if (!cur) return s;
      return {
        ...s,
        [week]: {
          ...cur,
          draft: cur.draft.map((x) => (x.session_id !== row.session_id ? x : {
            ...x,
            sme_id: smeId, sme_name: name, stage: "override" as const,
            score: x.candidates.find((c) => c.sme_id === smeId)?.score ?? x.score,
            flags: blocked
              ? [...x.flags.filter((f) => f.code !== "UNFILLED"),
                { code: "RULE_OVERRIDE_RISK" as const, priority: 3, severity: "high" as const,
                  session_id: x.session_id, sme_id: smeId,
                  reason: `Override assigns ${name} against a hard rule — kept visible on purpose.` }]
              : x.flags.filter((f) => f.code !== "UNFILLED"),
          })),
        },
      };
    });
    setSheet(null);
    say(`${name} assigned to ${row.batch_id}.`);
  };

  const approveOne = (row: DraftRow) => {
    setApproved((a) => new Set(a).add(row.session_id));
    setDecisions((d) => ({ ...d, [row.session_id]: { session_id: row.session_id, action: "approve" } }));
    setSheet(null);
    say(`${row.batch_id} · ${row.sub_specialty ?? META.type_label[row.type]} approved.`);
  };

  const approveWeek = () => {
    const unf = rows.filter((r) => !r.sme_id).length;
    if (unf) { say(`${unf} class(es) still have no teacher — clear them from Work items first.`); return; }
    setApproved((a) => new Set([...a, ...rows.map((r) => r.session_id)]));
    setDecisions((d) => ({ ...d, ...Object.fromEntries(rows.map((r) => [r.session_id, { session_id: r.session_id, action: "approve" as const }])) }));
    say("Week approved — ready to publish to calendars.");
  };

  const exportCsv = async () => {
    if (!run) return;
    setLoading(true);
    try {
      // the settled week is approved on load without an explicit decision — export it as approved,
      // not as "pending"
      const decs = rows
        .map((r) => decisions[r.session_id]
          ?? (approved.has(r.session_id) ? { session_id: r.session_id, action: "approve" as const } : null))
        .filter(Boolean) as Decision[];
      const out = await submitApprovals(run.draft, decs);
      await csvExporter.export(out.export_rows, `ik-schedule-${WEEKS[week].iso}`);
      const risky = out.final_schedule.filter((r) => r.flags.some((f) => f.code === "RULE_OVERRIDE_RISK"));
      say(risky.length ? `CSV exported — ${risky.length} override(s) flagged OVERRIDE RISK.` : "CSV exported.");
    } catch (e) {
      say(String(e).slice(0, 160));
    } finally {
      setLoading(false);
    }
  };

  const dropOut = async (smeId: string) => {
    const next = [...new Set([...droppedOut, smeId])];
    setDroppedOut(next);
    setWeek("next");
    const res = await runNext({ dropped: next, quiet: true });
    const unf = res ? res.draft.filter((r) => !r.sme_id).length : 0;
    say(`${smeName(smeId)} marked unavailable — draft re-run, ${unf} class(es) now unfilled.`);
  };

  const createBatch = async () => {
    const id = nextBatchId(batches, newBatch.course);
    const batch: Batch = {
      id, course: newBatch.course, level: newBatch.level as Batch["level"], learners: newBatch.learners,
      per_week: newBatch.per_week, weeks_done: 0, weeks_total: 12, started: "7 Sep 2026",
    };
    const fresh = newBatchSessions(batch, COURSES[newBatch.course], sessionsFor("next"), META.levels, META.hours);
    setBatches((b) => [...b, batch]);
    setExtraSessions((s) => [...s, ...fresh]);
    setSelBatch(id);
    setSheet(null);
    setWeek("next");
    await runNext({ sessions: [...sessionsFor("next"), ...fresh], quiet: true });
    say(`${id} created — ${newBatch.per_week} classes drafted for next week.`);
  };

  /** SME view: switching a block off shrinks their availability and re-drafts next week for real. */
  const toggleAvail = async (key: string) => {
    const nextOff = { ...availOff, [key]: !availOff[key] };
    setAvailOff(nextOff);
    const res = await runNext({ availOff: nextOff, quiet: true });
    const mine = res ? res.draft.filter((r) => r.sme_id === META.me).length : 0;
    say(`${nextOff[key] ? "Block marked off" : "Block re-opened"} — next week re-drafted, you now have ${mine} class(es).`);
  };

  const resolvePending = (sessionId: string, accept: boolean) => {
    const p = pending.find((x) => x.session_id === sessionId);
    setPending((list) => list.filter((x) => x.session_id !== sessionId));
    if (!p) return;
    if (accept) {
      setRuns((s) => {
        const cur = s.current;
        if (!cur) return s;
        return {
          ...s,
          current: {
            ...cur,
            draft: cur.draft.map((x) => (x.session_id === sessionId
              ? { ...x, sme_id: p.sme_id, sme_name: p.name, stage: "override" as const } : x)),
          },
        };
      });
      say("Change accepted — learners will see the new instructor.");
    } else {
      say("Change declined — ops has been notified.");
    }
  };

  // ---------- sheets ----------

  const sheetRow = sheet && (sheet.kind === "class" || sheet.kind === "ghost")
    ? (runs[sheet.week]?.draft ?? []).find((r) => r.session_id === sheet.sessionId) ?? null
    : null;

  const renderSheet = () => {
    if (!sheet) return null;

    if (sheet.kind === "work") {
      return (
        <Sheet
          width={600} eyebrow={`${WEEKS[week].label} · ${WEEKS[week].range}`}
          title={`${work.length} work item${work.length === 1 ? "" : "s"}`}
          subtitle="Everything that needs a decision before the week can be published."
          footer={[{ label: "Close", onClick: () => setSheet(null) }]}
          onClose={() => setSheet(null)}
        >
          <div className="flex flex-col gap-[9px]">
            {work.map((w) => (
              <div
                key={w.key}
                className="rounded-[16px] p-[13px_15px]"
                style={{
                  border: `1px solid ${w.severity === "critical" ? "var(--red-line)" : "var(--amber-line)"}`,
                  background: w.severity === "critical" ? "#fdf8f7" : "#fdfaf2",
                }}
              >
                <div className="flex items-center gap-[9px]">
                  <span className={`chip ${SEV_CHIP[w.severity]}`}>{w.code === "LEAVE" ? "LEAVE" : FLAG_LABEL[w.code]}</span>
                  <span className="text-[12.5px] font-semibold">{w.title}</span>
                </div>
                <div className="mt-[6px] text-[12px] leading-[1.55]" style={{ color: "var(--ink-3)" }}>{w.detail}</div>
                <div className="mt-[10px] flex flex-wrap gap-[7px]">
                  {w.session_id && (
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={() => openClass(w.session_id!, w.code === "UNFILLED" ? "pick" : "info")}
                    >
                      {w.code === "UNFILLED" ? "Pick a teacher" : "Open class"}
                    </button>
                  )}
                  {w.code === "FAIRNESS_VIOLATION" && (
                    <button
                      className="btn btn-sm"
                      onClick={() => { setDismissed((d) => new Set(d).add(w.key)); say("Workload flag accepted for this week."); }}
                    >
                      Accept for this week
                    </button>
                  )}
                  {w.code === "LEAVE" && w.sme_id && (
                    <>
                      <button className="btn btn-sm" onClick={() => { setMod("smes"); setSelSme(w.sme_id!); setSheet(null); }}>
                        Open SME profile
                      </button>
                      <button className="btn btn-sm" onClick={() => { setSheet(null); void dropOut(w.sme_id!); }}>
                        Re-run draft without them
                      </button>
                    </>
                  )}
                </div>
              </div>
            ))}
            {!work.length && (
              <div className="p-[28px_6px] text-center text-[13px] leading-[1.6]" style={{ color: "var(--muted)" }}>
                Everything is handled — no unfilled classes, conflicts or fairness flags left this week.
              </div>
            )}
          </div>
        </Sheet>
      );
    }

    if (sheet.kind === "newBatch") {
      const id = nextBatchId(batches, newBatch.course);
      return (
        <Sheet
          eyebrow="Batch management" title="Create a new batch"
          subtitle="The id is generated from the course — the scheduler drafts it into next week straight away."
          footerNote={`New id will be ${id} · 12-week course starting 7 Sep 2026`}
          footer={[
            { label: "Cancel", onClick: () => setSheet(null) },
            { label: "Create batch", kind: "go", onClick: () => void createBatch() },
          ]}
          onClose={() => setSheet(null)}
        >
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="label-caps mb-[6px] block">Course</span>
              <select className="field w-full" value={newBatch.course} onChange={(e) => setNewBatch({ ...newBatch, course: e.target.value })}>
                {Object.values(COURSES).map((c) => <option key={c.id} value={c.id}>{c.id} — {c.name}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="label-caps mb-[6px] block">Level</span>
              <select className="field w-full" value={newBatch.level} onChange={(e) => setNewBatch({ ...newBatch, level: e.target.value })}>
                {META.levels.map((l) => <option key={l} value={l}>{l}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="label-caps mb-[6px] block">Classes per week</span>
              <input type="number" min={1} max={6} className="field w-full" value={newBatch.per_week}
                onChange={(e) => setNewBatch({ ...newBatch, per_week: Number(e.target.value) })} />
            </label>
            <label className="block">
              <span className="label-caps mb-[6px] block">Learners enrolled</span>
              <input type="number" min={1} className="field w-full" value={newBatch.learners}
                onChange={(e) => setNewBatch({ ...newBatch, learners: Number(e.target.value) })} />
            </label>
          </div>
        </Sheet>
      );
    }

    if (!sheetRow) return null;
    const r = sheetRow;
    const p = istParts(r.start_utc);
    const course = COURSES[r.subject];
    const locked = WEEKS[sheet.week].locked;
    const batch = batches.find((b) => b.id === r.batch_id);
    const isApproved = approved.has(r.session_id);
    const pend = pending.find((x) => x.session_id === r.session_id);

    if (sheet.kind === "ghost") {
      const sm = SMES.next.find((s) => s.id === sheet.smeId)!;
      return (
        <Sheet
          eyebrow={`${r.batch_id} · ${course?.name}`} title={r.sub_specialty ?? META.type_label[r.type]}
          subtitle={`${META.days[p.day]} ${p.label} IST · 60 min · ${META.type_label[r.type]}`}
          banner={{ text: `${sm.name} is free at this hour and carries this topic. Assigning fills the gap immediately.`, tone: "green" }}
          facts={[
            { label: "Batch", value: `${r.batch_id} · ${batch?.level ?? ""}` },
            { label: "Slot", value: `${META.days[p.day]} ${p.label} IST` },
            { label: "Teacher level", value: `${sm.level} · ★ ${sm.rating.toFixed(1)}` },
            { label: "Their week", value: `${rows.filter((x) => x.sme_id === sm.id).length} of ${sm.preferred} preferred` },
          ]}
          footer={[
            { label: "Cancel", onClick: () => setSheet(null) },
            { label: `Assign ${sm.name.split(" ")[0]}`, kind: "go", onClick: () => assign(r, sm.id, sm.name, false) },
          ]}
          onClose={() => setSheet(null)}
        />
      );
    }

    const cands = sheetCandidates(r, SMES.next);
    const showList = sheet.stage === "pick" || !r.sme_id;
    const sme = SMES.next.find((s) => s.id === r.sme_id);
    const mineCount = rows.filter((x) => x.sme_id === r.sme_id).length;

    return (
      <Sheet
        eyebrow={`${r.batch_id} · ${course?.name}`}
        title={r.sub_specialty ?? META.type_label[r.type]}
        subtitle={`${META.days[p.day]} ${p.label} IST · 60 min · ${META.type_label[r.type]} · ${batch?.level ?? ""} batch`}
        facts={[
          { label: "Batch", value: `${r.batch_id} · ${batch?.learners ?? "—"} learners` },
          {
            label: "Decided by",
            value: r.stage === "llm" ? `LLM tie-break${run?.stats.llm.model ? ` (${run.stats.llm.model})` : ""}`
              : r.stage === "override" ? "Ops override" : r.stage === "auto" ? "Automatic score" : "—",
          },
          { label: "Match score", value: r.score !== null ? r.score.toFixed(2) : "—" },
          {
            label: "Status",
            value: !r.sme_id ? "Unfilled" : pend ? "Change pending SME approval"
              : isApproved ? "Approved" : locked ? "Live" : "Draft — not yet approved",
          },
        ]}
        footerNote={locked
          ? "This week is live: a teacher change is sent to the SME for approval before learners see it."
          : "Next week is a system-generated draft — your changes apply immediately and feed the next run."}
        footer={[
          { label: "Close", onClick: () => setSheet(null) },
          ...(r.sme_id && !isApproved && !locked ? [{ label: "Approve this class", kind: "go" as const, onClick: () => approveOne(r) }] : []),
        ]}
        onClose={() => setSheet(null)}
      >
        {r.adjusted_from_override && (
          <div className="rounded-[14px] p-[12px_14px] text-[12.5px]" style={{ background: "var(--brand-tint)", color: "var(--brand-deep)" }}>
            Adjusted from your override — the score for this pairing was nudged on the last re-run.
          </div>
        )}
        {sme && (
          <div>
            <SectionLabel>Teacher assigned</SectionLabel>
            <PersonRow
              id={sme.id} name={sme.name}
              meta={`${sme.level} · ★ ${sme.rating.toFixed(1)} · ${mineCount} of ${sme.preferred} classes this week${leave[sme.id] ? ` · ${leave[sme.id]}` : ""}`}
              tone={sheet.stage === "pick" ? "active" : "plain"}
              right={<span className="whitespace-nowrap text-[11.5px] font-semibold" style={{ color: "var(--brand-deep)" }}>
                {sheet.stage === "pick" ? "Choosing…" : "Change teacher →"}
              </span>}
              onClick={() => setSheet({ ...sheet, stage: sheet.stage === "pick" ? "info" : "pick" })}
            />
          </div>
        )}
        {!!r.flags.length && (
          <div>
            <SectionLabel>Why it is flagged</SectionLabel>
            {r.flags.map((f, i) => (
              <div key={i} className="mb-2 flex items-start gap-[9px]">
                <span className={`chip ${SEV_CHIP[f.severity]}`}>{FLAG_LABEL[f.code]}</span>
                <span className="text-[12.5px] leading-[1.55]" style={{ color: "var(--ink-2)" }}>{f.reason}</span>
              </div>
            ))}
          </div>
        )}
        {showList && (
          <div>
            <SectionLabel>{r.sme_id ? "Choose a different teacher" : "Teachers who could take this class"}</SectionLabel>
            <div className="flex flex-col gap-[7px]">
              {cands.map((c) => {
                const level = SMES.next.find((s) => s.id === c.sme_id)?.level ?? "";
                const risky = c.blocked || !!c.warn;          // rule breach or fairness breach
                const isArmed = armed === c.sme_id;
                return (
                  <PersonRow
                    key={c.sme_id} id={c.sme_id} name={c.name}
                    tone={isArmed ? "active" : "plain"}
                    meta={isArmed
                      ? (c.blocked
                        ? `Breaks a hard rule (${c.warn}). It stays flagged as OVERRIDE RISK${locked
                          ? " and goes to the SME for approval" : ", and the next re-run cannot keep it"}. Click again to confirm.`
                        : `${c.warn}. Click again to confirm.`)
                      : c.score !== null ? `match ${c.score.toFixed(2)} · ${level}` : level}
                    right={
                      <span className="flex items-center gap-2">
                        {c.warn && (
                          <span
                            className="whitespace-nowrap rounded-[8px] px-2 py-[3px] text-[10px] font-semibold"
                            style={c.blocked
                              ? { background: "var(--red-tint)", color: "var(--red-ink)" }
                              : { background: "var(--amber-tint)", color: "var(--amber-ink)" }}
                          >
                            {c.warn}
                          </span>
                        )}
                        <span
                          className="whitespace-nowrap text-[11.5px] font-semibold"
                          style={{ color: isArmed ? "var(--red-ink)" : "var(--brand-deep)" }}
                        >
                          {isArmed ? "Confirm →" : locked ? "Request →" : "Assign →"}
                        </span>
                      </span>
                    }
                    // spec: a breach is allowed, but only with an explicit confirmation
                    onClick={() => (risky && !isArmed ? setArmed(c.sme_id) : assign(r, c.sme_id, c.name, c.blocked))}
                  />
                );
              })}
              {!cands.length && (
                <div className="text-[12.5px]" style={{ color: "var(--muted)" }}>
                  Nobody else is eligible for this slot — every other SME is blocked by a hard rule.
                </div>
              )}
            </div>
          </div>
        )}
      </Sheet>
    );
  };

  // ---------- render ----------

  const showModule = () => {
    if (!run) {
      return (
        <div className="card p-8 text-center text-[13px]" style={{ color: "var(--muted)" }}>
          {loading ? "Running the matching pipeline…" : "No draft yet."}
        </div>
      );
    }
    if (mod === "dashboard") {
      return (
        <>
          <KpiCards
            rows={rows} smes={smes} batches={batches} approved={approved}
            leaveCount={Object.keys(leave).length} spread={run.stats.fairness_spread_per_subject}
            llm={run.stats.llm} autoAssigned={run.stats.auto_assigned} llmResolved={run.stats.llm_resolved}
          />
          <Dashboard
            rows={filtered} allRows={rows} batches={batches} courses={COURSES} meta={META} weeks={WEEKS}
            week={week} weekDates={weekDates} tab={tab} approved={approved} changed={changed}
            batchFilter={batchFilter} statusOff={statusOff} workCount={work.length} overrides={overrides}
            loading={loading} smeName={smeName}
            onTab={setTab} onWeek={setWeek} onBatchFilter={setBatchFilter}
            onStatusToggle={(k: Category) => setStatusOff((s) => ({ ...s, [k]: !s[k] }))}
            onStatusAll={(allOn) => setStatusOff(allOn ? { red: true, amber: true, approved: true, staffed: true } : {})}
            onOpenWork={() => setSheet({ kind: "work" })}
            onApproveWeek={approveWeek}
            onRerun={() => void runNext()}
            onOpen={openClass}
            onOpenOverride={(o) => { setWeek(o.week); setTab("schedule"); setSheet({ kind: "class", sessionId: o.session_id, week: o.week, stage: "info" }); }}
          />
        </>
      );
    }
    if (mod === "smes") {
      return (
        <SmeManagement
          smes={smes} rows={rows} courses={COURSES} meta={META} weeks={WEEKS} week={week} weekDates={weekDates}
          approved={approved} selected={selSme} leave={leave}
          onSelect={setSelSme} onWeek={setWeek} onOpen={openClass}
          onGhost={(sessionId) => setSheet({ kind: "ghost", sessionId, week, smeId: selSme })}
          onToggleLeave={(id) => setLeave((l) => {
            const n = { ...l };
            if (n[id]) delete n[id]; else n[id] = "On leave next week";
            return n;
          })}
          onDropOut={(id) => void dropOut(id)}
        />
      );
    }
    if (mod === "batches") {
      return (
        <BatchManagement
          batches={batches} rows={rows} courses={COURSES} meta={META} weeks={WEEKS} week={week}
          weekDates={weekDates} approved={approved} selected={selBatch}
          onSelect={setSelBatch} onWeek={setWeek} onOpen={openClass} onNewBatch={() => setSheet({ kind: "newBatch" })}
        />
      );
    }
    return (
      <MyWeek
        role={role === "student" ? "student" : "sme"} me={meNow} myBatch={batches.find((b) => b.id === META.my_batch)}
        rows={rows} smes={SMES.next} courses={COURSES} meta={META} weeks={WEEKS} week={week} weekDates={weekDates}
        approved={approved} availOff={availOff} preferred={preferred}
        onAvail={(k) => void toggleAvail(k)}
        onPreferred={setPreferred} onWeek={setWeek} onOpen={openClass}
        leave={leave[META.me] ?? null}
        onToggleLeave={() => setLeave((l) => {
          const n = { ...l };
          if (n[META.me]) { delete n[META.me]; say("Leave request withdrawn."); }
          else { n[META.me] = "Leave requested for next week (7–12 Sep)"; say("Leave requested for next week — ops notified."); }
          return n;
        })}
        pending={pending} onResolve={resolvePending}
      />
    );
  };

  return (
    <div className="flex min-h-screen items-stretch" style={{ background: "var(--page)" }}>
      <Sidebar
        role={role} mod={mod}
        badges={{ dashboard: work.length || undefined, myweek: pending.length || undefined }}
        onRole={(r) => { setRole(r); setMod(ROLE_MODULES[r][0]); setSheet(null); }}
        onMod={(m) => { setMod(m); setSheet(null); }}
      />
      <main className="min-w-0 flex-1" style={{ marginLeft: 74 }}>
        <div
          className="sticky top-0 flex flex-wrap items-end gap-[14px] p-[18px_26px_14px]"
          style={{
            zIndex: 20, background: "rgba(245,247,250,0.8)", backdropFilter: "blur(22px) saturate(180%)",
            WebkitBackdropFilter: "blur(22px) saturate(180%)", borderBottom: "0.5px solid rgba(16,26,51,0.06)",
          }}
        >
          <div>
            <h1 className="m-0 text-[22px] font-bold" style={{ letterSpacing: "-0.02em" }}>{MODULES[mod].label}</h1>
            <div className="mt-[3px] text-[12.5px]" style={{ color: "var(--muted)" }}>{MODULES[mod].sub}</div>
          </div>
          <div className="ml-auto flex items-center gap-[9px]">
            <span className="text-[11.5px]" style={{ color: "var(--muted)" }}>
              {PERSONA[role].name} · {Object.keys(decisions).length} decision(s) pending export
            </span>
            {role === "coordinator" && (
              <button className="btn" onClick={() => void exportCsv()} disabled={loading || !run}>Export CSV</button>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-4 p-[0_26px_44px]">
          {run && <LlmBanner llm={run.stats.llm} />}
          {showModule()}
        </div>
      </main>
      {renderSheet()}
      <Toast text={toast} />
    </div>
  );
}

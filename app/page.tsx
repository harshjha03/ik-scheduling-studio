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
  AgentMove, AgentMoveAction, AgentRequest, AgentResult, ChatTurn, DataProvenance,
  Batch, Category, Course, Decision, DraftRow, Fix, HistoryRecord, LeafId, Meta, ModuleKey, NewClass, OverrideEvent,
  Cancellation, Profile, ResolvedEntry, Role, RunResult, SendState, Session, SheetState, SME,
  WeekKey, WeekMeta, WorkItem,
} from "@/lib/types";
import {
  agentApply, agentRun, getData, getIntegrations, getOverrides, loadSchedule, publishLeaf, pullSheet,
  pushSheet, putData, resetData, runMatching, saveSchedule, submitApprovals, syncAvailability,
  type IntegrationsInfo, type OverrideStats,
} from "@/lib/api";
import { isCancel, isMerge, isMove, isReschedule, isUpgrade } from "@/lib/types";
import { csvExporter } from "@/lib/export";
import {
  classTemplate, downloadCsv, emptyImport, historyTemplate, isWorkbook, parseClassImport,
  parseHistoryImport, parseSmeImport, smeTemplate, toHistoryRecord, toSme,
  WORKBOOK_HINT, type ImportedClass, type ImportedHistory, type ImportedSme, type ImportResult,
} from "@/lib/import";
import {
  FLAG_LABEL, SEV_CHIP, applyAvailabilityBlocks, autoFix, isAvailable, isLive, istParts, liveRows, mergeCandidates,
  nextBatchId, newBatchSessions, publishLeaves, sendSummary, sheetCandidates, weekAsHistory, workItems, workload,
  type SmeFilter,
} from "@/lib/view";
import Sidebar, { MODULES, PERSONA, ROLE_MODULES } from "./components/Sidebar";
import KpiCards from "./components/KpiCards";
import Dashboard from "./components/Dashboard";
import SmeManagement from "./components/SmeManagement";
import BatchManagement from "./components/BatchManagement";
import MyWeek from "./components/MyWeek";
import Sheet, { PersonRow, SectionLabel } from "./components/Sheet";
import WorkSheet from "./components/WorkSheet";
import PublishSheet from "./components/PublishSheet";
import ImportSheet from "./components/ImportSheet";
import AgentSheet from "./components/AgentSheet";
import CopilotChat from "./components/CopilotChat";
import Toast from "./components/Toast";

// JSON imports infer over-narrow literal unions; the engine owns the schema.
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
/** The three ingestable datasets, in the order the header names them. */
const DATASET_LABELS: [string, string][] = [["sessions", "Sessions"], ["smes", "Roster"], ["history", "History"]];

function ago(iso: string): string {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  return hrs < 24 ? `${hrs} h ago` : `${Math.round(hrs / 24)} d ago`;
}
const ALL_LEAVES: Record<string, boolean> = Object.fromEntries(
  ["cal", "email", "sms"].flatMap((c) => ["sme", "stu"].map((a) => [`${c}:${a}`, true])),
);

/** QA-02: a failed write used to be indistinguishable from success. */
const SAVE_FAILED = "Saved here, but the server copy failed — your changes may not survive a reload.";
const SAVE_CONFLICT = "Someone else saved this week since you loaded it — reload to see their version before saving again.";

export default function Page() {
  // Bundled seed data is the initial state, so first paint is identical with the API unreachable.
  // A /api/data fetch below replaces whatever has been stored since; there is no loading state.
  const [sessionData, setSessionData] = useState<Record<WeekKey, Session[]>>(SESSIONS);
  const [smeData, setSmeData] = useState<Record<WeekKey, SME[]>>(SMES);
  const [role, setRole] = useState<Role>("coordinator");
  const [mod, setMod] = useState<ModuleKey>("dashboard");
  const [week, setWeek] = useState<WeekKey>("next");
  const [tab, setTab] = useState<"schedule" | "overrides">("schedule");
  const [runs, setRuns] = useState<Partial<Record<WeekKey, RunResult>>>({});
  const [changed, setChanged] = useState<Set<string>>(new Set());
  const [approved, setApproved] = useState<Set<string>>(new Set());
  const [published, setPublished] = useState<Partial<Record<WeekKey, boolean>>>({});
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [overrides, setOverrides] = useState<OverrideEvent[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [resolvedLog, setResolvedLog] = useState<ResolvedEntry[]>([]);
  const [leave, setLeave] = useState<Record<string, string>>({});
  const [availOff, setAvailOff] = useState<Record<string, boolean>>({});
  // a live-week teacher change waits on the SME; the class sheet reports it as pending
  const [pending, setPending] = useState<{ session_id: string; sme_id: string; name: string; from_sme_id: string | null }[]>([]);
  const [batches, setBatches] = useState<Batch[]>(BATCHES0);
  const [extraSessions, setExtraSessions] = useState<Session[]>([]);
  const [extraSmes, setExtraSmes] = useState<SME[]>([]);
  const [smeEdits, setSmeEdits] = useState<Record<string, Partial<SME>>>({});
  const [imp, setImp] = useState<ImportResult<ImportedClass>>(emptyImport);
  const [smeImp, setSmeImp] = useState<ImportResult<ImportedSme>>(emptyImport);
  const [prof, setProf] = useState<Profile | null>(null);
  // Students are a persona here, not a roster — one e-mail in state; move onto a learner record when one exists
  const [studentEmail, setStudentEmail] = useState("aarav.shah@example.com");
  const [emailDraft, setEmailDraft] = useState("");
  const [selSme, setSelSme] = useState("T01");
  const [selBatch, setSelBatch] = useState("DSA-01");
  const [batchFilter, setBatchFilter] = useState("all");
  const [statusOff, setStatusOff] = useState<Record<string, boolean>>({});
  const [smeQuery, setSmeQuery] = useState("");
  const [smeFilter, setSmeFilter] = useState<SmeFilter>("all");
  const [sheet, setSheet] = useState<SheetState>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [newBatch, setNewBatch] = useState({ course: "DSA", level: "beginner", per_week: 4, learners: 30 });
  const [newClass, setNewClass] = useState<NewClass>({ topic: "", type: "class", day: 0, hour: 10, smeId: "" });
  const [pubSel, setPubSel] = useState<Record<string, boolean>>(ALL_LEAVES);
  const [pubStatus, setPubStatus] = useState<Record<string, SendState>>({});
  const [vh, setVh] = useState(900);
  // Recovery & Review Copilot — request, last result, in-flight flag (all owned here, rendered by AgentSheet)
  const [agentReq, setAgentReq] = useState<AgentRequest>({ mode: "review" });
  const [agentRes, setAgentRes] = useState<AgentResult | null>(null);
  const [agentBusy, setAgentBusy] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
  const [chatDraft, setChatDraft] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  // session_id -> new start_utc, from a copilot reschedule the coordinator applied
  const [sessionEdits, setSessionEdits] = useState<Record<string, string>>({});
  // Classes ops (or the copilot) has dropped. Both are edit layers over the fixtures, exactly like
  // `sessionEdits`, so they survive every re-draft instead of being wiped by the next run.
  const [sessionCancels, setSessionCancels] = useState<Record<string, Cancellation>>({});
  const [sessionMerges, setSessionMerges] = useState<Record<string, string>>({});
  const [cancelReason, setCancelReason] = useState("");
  // A published week that has since been amended: the calendars people hold are stale for the rows
  // in `changed`, and only those need re-sending.
  const [amended, setAmended] = useState<Partial<Record<WeekKey, boolean>>>({});
  // what is actually wired up right now, so every control can label itself live or simulated
  const [integrations, setIntegrations] = useState<IntegrationsInfo | null>(null);
  const [sheetBusy, setSheetBusy] = useState(false);
  // where each dataset came from, so the live source is visible instead of being a README claim
  const [provenance, setProvenance] = useState<DataProvenance>({});
  const [overrideStats, setOverrideStats] = useState<OverrideStats | null>(null);
  // assignment history drives Stage B's fairness and performance terms; an import replaces it
  const [historyRecords, setHistoryRecords] = useState<HistoryRecord[]>(HISTORY);
  const [histImp, setHistImp] = useState<ImportResult<ImportedHistory>>(emptyImport);
  // what the last pull reported, promoted into `provenance` only once the import is confirmed
  const [pulled, setPulled] = useState<DataProvenance>({});
  // busy blocks read off each teacher's calendar, and how that sync went
  const [busyBlocks, setBusyBlocks] = useState<Record<string, number>>({});
  const [syncBusy, setSyncBusy] = useState(false);
  const [syncDetail, setSyncDetail] = useState<string | null>(null);
  const [externalBusy, setExternalBusy] = useState<Record<string, { start_utc: string; end_utc: string }[]>>({});
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const nextRef = useRef<DraftRow[] | null>(null);
  const pubToken = useRef(0);
  const pubTimers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const say = useCallback((msg: string) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast(msg);
    toastTimer.current = setTimeout(() => setToast(null), 2800);
  }, []);
  // Every durable write goes through here: a failure is said out loud and stays on screen until a
  // later write succeeds, so a success toast that lands afterwards cannot hide it.
  const [saveFailed, setSaveFailed] = useState<string | null>(null);
  const persist = useCallback((p: Promise<unknown>) => {
    p.then(() => setSaveFailed(null), (e) => {
      const msg = /→ 409/.test(String(e)) ? SAVE_CONFLICT : SAVE_FAILED;
      setSaveFailed(msg); say(msg);
    });
  }, [say]);
  // The server's updated_at for each week we have seen; sent back with every save so a stale tab is
  // refused (409) instead of silently overwriting what another coordinator saved.
  const savedAt = useRef<Record<string, string>>({});
  const save = useCallback((week: string, draft: DraftRow[], extra: Record<string, unknown> = {}) =>
    saveSchedule(week, draft, { ...extra, expected_updated_at: savedAt.current[week] })
      .then((r) => { savedAt.current[week] = r.updated_at; return r; }), []);

  useEffect(() => {
    // never claim a live source: the labels come from the server, and absence is shown as simulated
    getIntegrations().then(setIntegrations).catch(() => setIntegrations(null));
    getOverrides().then(setOverrideStats).catch(() => setOverrideStats(null));
    // Hydrate, do not block: state already holds the bundled seed data, so a failure here leaves the
    // app exactly as it boots today rather than showing a spinner or a blank screen.
    getData().then(({ datasets }) => {
      const prov: DataProvenance = {};
      const mark = (key: string, name: string) => {
        const d = datasets[name];
        if (d && d.source !== "seed" && d.updated_at) prov[key] = { source: d.source, at: d.updated_at };
      };
      const next = datasets.sessions_next?.payload as Session[] | undefined;
      const cur = datasets.sessions_current?.payload as Session[] | undefined;
      if (next?.length || cur?.length) {
        setSessionData((s) => ({ next: next?.length ? next : s.next, current: cur?.length ? cur : s.current }));
      }
      const roster = datasets.smes?.payload as SME[] | undefined;
      const rosterCur = datasets.smes_current?.payload as SME[] | undefined;
      if (roster?.length || rosterCur?.length) {
        setSmeData((s) => ({ next: roster?.length ? roster : s.next, current: rosterCur?.length ? rosterCur : s.current }));
      }
      const hist = datasets.history?.payload as HistoryRecord[] | undefined;
      if (hist?.length) setHistoryRecords(hist);
      const bts = datasets.batches?.payload as Batch[] | undefined;
      if (bts?.length) setBatches(bts);
      mark("sessions", "sessions_next");
      mark("smes", "smes");
      mark("history", "history");
      setProvenance((p) => ({ ...prov, ...p }));
    }).catch(() => {});
  }, []);

  useEffect(() => {
    const onResize = () => setVh(window.innerHeight);
    onResize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      pubTimers.current.forEach(clearTimeout);
      pubToken.current += 1;
    };
  }, []);

  /** The seed roster plus whatever ops added or edited this session — imports and profile edits. */
  // Teachers ops has taken out of next week. Applied as an empty availability, so Stage A rules them
  // out with the ordinary `availability` reason and the week re-drafts without a copilot.
  const [unavailable, setUnavailable] = useState<Record<string, boolean>>({});
  const rosterFor = useCallback((w: WeekKey, off: Record<string, boolean> = unavailable): SME[] => (
    [...smeData[w], ...extraSmes].map((s) => {
      const merged = smeEdits[s.id] ? { ...s, ...smeEdits[s.id] } : s;
      // a synced calendar block is a hard rule in Stage A, so it travels with the roster
      const withBusy = externalBusy[s.id]?.length ? { ...merged, external_busy: externalBusy[s.id] } : merged;
      return w === "next" && off[s.id] ? { ...withBusy, weekly_availability: [] } : withBusy;
    })
  ), [extraSmes, smeEdits, externalBusy, unavailable]);

  const smesFor = useCallback((w: WeekKey): SME[] => rosterFor(w), [rosterFor]);

  /** Seeded sessions plus anything ops added, with copilot reschedules folded in. A reschedule only
   *  changes when a class runs, so it is an edit layer over the fixtures — not a new session. */
  const sessionsFor = useCallback((
    w: WeekKey,
    edits: Record<string, string> = sessionEdits,
    cancels: Record<string, Cancellation> = sessionCancels,
    merges: Record<string, string> = sessionMerges,
  ): Session[] => {
    const list = w === "next" ? [...sessionData.next, ...extraSessions] : sessionData.current;
    return list.map((x) => ({
      ...x,
      ...(edits[x.id] ? { start_utc: edits[x.id] } : {}),
      ...(cancels[x.id] ? { cancelled: cancels[x.id] } : {}),
      ...(merges[x.id] ? { merged_into: merges[x.id] } : {}),
    }));
  }, [sessionData, extraSessions, sessionEdits, sessionCancels, sessionMerges]);

  /** Re-run one week's pipeline.
   *
   *  `next` is drafted on top of the settled current week, as it always was. `current` is re-run in
   *  place: it is published and approved, so it is never re-drafted wholesale — but a last-minute
   *  drop-out has to be able to move classes inside it, and running the same Stages A-D over the
   *  amended sessions is a stronger check than patching rows by hand. Deterministic (no LLM) there,
   *  because a settled week is not a place to introduce a new tie-break.
   */
  const runWeek = useCallback(async (target: WeekKey, opts: {
    overrides?: OverrideEvent[]; sessions?: Session[]; smes?: SME[];
    availOff?: Record<string, boolean>; quiet?: boolean;
  } = {}) => {
    const cur = runs.current;
    if (target === "next" && !cur) return;
    setLoading(true);
    try {
      const blocks = opts.availOff ?? availOff;
      const roster = opts.smes ?? rosterFor(target);
      const smes = roster.map((s) => (s.id === META.me ? applyAvailabilityBlocks(s, blocks) : s));   // the SME's own blocks
      const history = target === "next" && cur
        ? weekAsHistory(cur.draft, roster, WEEKS.current.iso, historyRecords)
        : historyRecords;
      const sessions = opts.sessions ?? sessionsFor(target);
      const res = await runMatching(sessions, smes, history, opts.overrides ?? overrides, { llm: target === "next" });
      const prev = target === "next" ? nextRef.current : runs.current?.draft ?? null;
      const changedIds = new Set<string>();
      if (prev) {
        const before = new Map(prev.map((r) => [r.session_id, r]));
        res.draft.forEach((r) => {
          const p = before.get(r.session_id);
          if (!p || p.sme_id !== r.sme_id || flagKey(p) !== flagKey(r)) changedIds.add(r.session_id);
        });
      }
      if (target === "next") nextRef.current = res.draft;
      setChanged(changedIds);
      setRuns((s) => ({ ...s, [target]: res }));
      const iso = WEEKS[target].iso;
      // durable copy, so a refresh does not lose the coordinator's work
      persist(save(iso, res.draft, {
        stats: res.stats, flags: res.flags, published: target === "next" ? false : !!published[target],
        provenance, history: historyRecords,
      }));
      // A re-draft invalidates the sign-off it was given. On the live week only the rows that
      // actually moved lose theirs — the rest of the week is still the week ops approved.
      setApproved((a) => new Set([...a].filter((id) => (target === "next"
        ? !res.draft.some((r) => r.session_id === id)
        : !changedIds.has(id) && res.draft.some((r) => r.session_id === id && isLive(r))))));
      if (target === "next") {
        setPublished((p) => ({ ...p, next: false }));   // a re-draft invalidates what people were sent
        setDecisions({});
        setResolvedLog([]);
      } else if (published.current && changedIds.size) {
        // the live week stays published — only the rows that moved need re-sending
        setAmended((a) => ({ ...a, current: true }));
      }
      setOverrides((log) => log.map((o) => (o.week === target ? {
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
  }, [runs.current, availOff, overrides, sessionsFor, rosterFor, historyRecords, published, provenance, say, persist, save]);

  const runNext = useCallback((opts: Parameters<typeof runWeek>[1] = {}) => runWeek("next", opts), [runWeek]);

  // initial load: settle the current week (no LLM), then draft the next on top of it
  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const cur = await runMatching(sessionData.current, smeData.current, HISTORY, [], { llm: false });
        if (!alive) return;
        setRuns((s) => ({ ...s, current: cur }));
        setApproved(new Set(cur.draft.filter((r) => r.sme_id).map((r) => r.session_id)));
        setPublished({ current: true });
        // a saved draft wins over a fresh run — it holds the decisions ops already made
        const saved = await loadSchedule(WEEKS.next.iso).catch(() => null);
        if (saved?.updated_at) savedAt.current[WEEKS.next.iso] = saved.updated_at;
        if (saved?.draft?.length && saved.stats) {
          if (!alive) return;
          if (saved.provenance) setProvenance(saved.provenance);
          if (saved.history?.length) setHistoryRecords(saved.history);
          nextRef.current = saved.draft;
          setRuns((s) => ({ ...s, next: { draft: saved.draft, flags: saved.flags ?? [], stats: saved.stats! } }));
          if (saved.published) setPublished((p) => ({ ...p, next: true }));
          say(`Restored the draft you saved at ${new Date(saved.updated_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}.`);
          return;
        }
        const history = weekAsHistory(cur.draft, smeData.next, WEEKS.current.iso, HISTORY);
        const nxt = await runMatching(sessionData.next, smeData.next, history, [], { llm: true });
        if (!alive) return;
        nextRef.current = nxt.draft;
        setRuns((s) => ({ ...s, next: nxt }));
        persist(save(WEEKS.next.iso, nxt.draft, { stats: nxt.stats, flags: nxt.flags, published: false }));
      } catch (e) {
        say(String(e).slice(0, 160));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [say, persist]);

  const run = runs[week];
  const rows = useMemo(() => run?.draft ?? [], [run]);
  // The widest spread, and how much of it arrived with the week. Twelve of DSA's fourteen is seeded
  // history no assignment this week can reach, and a bare number against a ±2 band hid that.
  const fairnessNote = useMemo(() => {
    const st = run?.stats;
    const widest = Object.entries(st?.fairness_spread_per_subject ?? {}).sort((a, b) => b[1] - a[1])[0];
    if (!widest) return null;
    const inherited = st?.fairness_inherited_per_subject?.[widest[0]];
    return `${widest[0]} spread ${widest[1]}${inherited == null ? "" : `, ${inherited} of it inherited`}`;
  }, [run]);
  const smes = useMemo(() => smesFor(week), [smesFor, week]);
  const smeName = useCallback((id: string | null) => rosterFor("next").find((s) => s.id === id)?.name ?? id ?? "—", [rosterFor]);
  const me = rosterFor("next").find((s) => s.id === META.me)!;
  const [preferred, setPreferred] = useState(me.preferred);
  const meNow = useMemo(() => applyAvailabilityBlocks(me, availOff), [me, availOff]);
  const isPublished = !!published[week];

  const weekDates = useMemo(() => {
    const first = sessionData[week][0]?.start_utc ?? new Date().toISOString();
    const p0 = istParts(first);
    const base = new Date(new Date(first).getTime() - p0.day * 864e5);
    return META.days.map((d, i) => ({ day: d, date: istParts(new Date(base.getTime() + i * 864e5).toISOString()).date }));
  }, [week]);

  const filtered = useMemo(
    () => (batchFilter === "all" ? rows : rows.filter((r) => r.batch_id === batchFilter)),
    [rows, batchFilter],
  );
  // The rolling window the engine actually scores on, so the SME table and the FAIRNESS_VIOLATION
  // reasons on the calendar are quoting one set of numbers rather than two.
  const workloadRows = useMemo(() => workload(smesFor(week), historyRecords, rows), [smesFor, week, historyRecords, rows]);
  // Leave applies to whichever week is on screen: ops can now mark a teacher out of the live week,
  // and a drop-out that raises no work item is a drop-out nobody acts on.
  const work = useMemo(
    () => workItems(rows, smes, dismissed, leave, WEEKS[week].locked ? "this week" : "next week"),
    [rows, smes, dismissed, leave, week],
  );
  /** one proposal per item, computed once so the list and its buttons never disagree */
  const fixes = useMemo(
    () => new Map(work.map((w) => [w.key, autoFix(w, rows, smes)])),
    [work, rows, smes],
  );
  /** This week's override rate against the week before it — the number that should fall over time. */
  const overrideRate = useMemo(() => {
    const by = overrideStats?.by_week;
    if (!by) return null;
    const weeks = Object.keys(by).sort();
    const iso = WEEKS[week].iso;
    const here = by[iso];
    const prevIso = weeks.filter((w) => w < iso).pop();
    const live = here ?? { overridden: 0, assigned: rows.filter((r) => r.sme_id).length, rate: 0 };
    return {
      rate: live.assigned ? (live.overridden / live.assigned) : null,
      prev: prevIso && by[prevIso].assigned ? by[prevIso].overridden / by[prevIso].assigned : null,
      overridden: live.overridden, assigned: live.assigned,
    };
  }, [overrideStats, week, rows]);

  const unfilledCount = rows.filter((r) => !r.sme_id).length;
  const conflictCount = rows.filter((r) => r.flags.some((f) => f.code === "HARD_CONFLICT")).length;

  // ---------- row edits ----------

  const patchRow = useCallback((w: WeekKey, sessionId: string, patch: (r: DraftRow) => DraftRow) => {
    setRuns((s) => {
      const cur = s[w];
      if (!cur) return s;
      return { ...s, [w]: { ...cur, draft: cur.draft.map((x) => (x.session_id === sessionId ? patch(x) : x)) } };
    });
  }, []);

  const openClass = (sessionId: string, stage: "info" | "pick" = "info") => {
    setSheet({ kind: "class", sessionId, week, stage });
  };

  const assign = (row: DraftRow, smeId: string, name: string, blocked: boolean, quiet = false) => {
    // On the live week a routine teacher swap still goes to the SME for approval. Covering a
    // drop-out does not: there is nobody left on the class to approve it, and the whole point of a
    // last-minute change is that it lands before the class does.
    const recovery = !row.sme_id || unavailable[row.sme_id];
    if (WEEKS[week].locked && !recovery) {
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
    // A pick the rules rejected is a decision, not a click: say which rule, ask, and if it puts the
    // teacher in two rooms at once keep that as a critical HARD_CONFLICT so the week cannot publish.
    // ponytail: same start = same hour — every session is 60 minutes on the hour
    const clash = blocked ? rows.find((r) => r.session_id !== row.session_id && r.sme_id === smeId
      && new Date(r.start_utc).getTime() === new Date(row.start_utc).getTime()) : undefined;
    if (blocked && !quiet && !window.confirm(
      `${name} is blocked for this class by a hard rule${clash ? ` — they already teach ${clash.batch_id} at this hour` : ""}.\n\n`
      + `Assign anyway? The row stays flagged${clash ? ", and the week cannot be approved until the double-booking is resolved" : ""}.`)) return;
    setDecisions((d) => ({ ...d, [row.session_id]: { session_id: row.session_id, action: "override", override_sme_id: smeId } }));
    setOverrides((log) => [{
      kind: row.sme_id ? "teacher change" : "assigned", session_id: row.session_id, batch_id: row.batch_id, week,
      from_sme_id: row.sme_id, to_sme_id: smeId, to_sme_name: name, at: new Date().toISOString(),
      note: blocked
        ? "Breaks a hard rule — kept visible as OVERRIDE RISK on the row."
        : "Applied now; the next draft scores this pairing +0.1 and the old one −0.2.",
    }, ...log]);
    patchRow(week, row.session_id, (x) => ({
      ...x,
      sme_id: smeId, sme_name: name, stage: "override" as const,
      score: x.candidates.find((c) => c.sme_id === smeId)?.score ?? x.score,
      flags: blocked
        ? [...x.flags.filter((f) => f.code !== "UNFILLED"),
          { code: "RULE_OVERRIDE_RISK" as const, priority: 3, severity: "high" as const,
            session_id: x.session_id, sme_id: smeId,
            reason: `Override assigns ${name} against a hard rule — kept visible on purpose.` },
          ...(clash ? [{ code: "HARD_CONFLICT" as const, priority: 2, severity: "critical" as const,
            session_id: x.session_id, sme_id: smeId,
            reason: `${name} also teaches ${clash.batch_id} at this hour — double-booked.` }] : [])]
        : x.flags.filter((f) => f.code !== "UNFILLED" && f.code !== "HARD_CONFLICT" && f.code !== "FAIRNESS_VIOLATION"),
    }));
    setApproved((a) => { const n = new Set(a); n.delete(row.session_id); return n; });
    setChanged((c) => new Set(c).add(row.session_id));
    setSheet(null);
    // Editing a published week makes the calendars people already have stale. On a draft that
    // un-publishes the whole week; on the live week only this class moved, so the week stays
    // published and is marked amended — re-publishing then sends just the rows that changed.
    const wasPublished = !!published[week];
    if (wasPublished && !WEEKS[week].locked) setPublished((p) => ({ ...p, [week]: false }));
    else if (wasPublished) setAmended((a) => ({ ...a, [week]: true }));
    if (!quiet) {
      say(wasPublished
        ? (WEEKS[week].locked
          ? `${name} assigned — re-publish the change so calendars stay in sync.`
          : `${name} assigned — the week needs re-publishing so calendars stay in sync.`)
        : `${name} assigned to ${row.batch_id}.`);
    }
  };

  const approveOne = (row: DraftRow) => {
    setApproved((a) => new Set(a).add(row.session_id));
    setDecisions((d) => ({ ...d, [row.session_id]: { session_id: row.session_id, action: "approve" } }));
    setSheet(null);
    say(`${row.batch_id} · ${row.sub_specialty ?? META.type_label[row.type]} approved.`);
  };

  // ---------- ops assist ----------

  const applyFix = (item: WorkItem, fix: Fix, quiet = false) => {
    const snap = rows.find((r) => r.session_id === item.session_id) ?? null;
    const a = fix.action;
    if (a.kind === "assign" && snap) assign(snap, a.smeId, a.smeName, false, true);
    else if (a.kind === "accept") patchRow(week, a.sessionId, (r) => ({ ...r, flags: r.flags.filter((f) => f.code !== a.code) }));
    else if (a.kind === "dismiss") setDismissed((d) => new Set(d).add(a.key));
    const entry: ResolvedEntry = {
      key: item.key,
      text: `${item.title.split(" · ")[0]} · ${fix.label}`,
      undo: a.kind === "dismiss" ? { kind: "dismiss", key: a.key } : { kind: "row", week, row: snap! },
    };
    setResolvedLog((l) => [entry, ...l]);
    setSheet({ kind: "work", auto: true });
    if (!quiet) say(fix.label);
  };

  const applyAllFixes = () => {
    const list = work.map((w) => ({ w, fix: fixes.get(w.key) })).filter((p): p is { w: WorkItem; fix: Fix } => !!p.fix);
    if (!list.length) { say("Nothing I can resolve here — these need a judgement call."); return; }
    list.forEach(({ w, fix }) => applyFix(w, fix, true));
    say(`${list.length} fix${list.length === 1 ? "" : "es"} applied — each one can be undone.`);
  };

  const undoResolve = (i: number) => {
    const entry = resolvedLog[i];
    if (!entry) return;
    if (entry.undo.kind === "dismiss") {
      setDismissed((d) => { const n = new Set(d); n.delete(entry.undo.kind === "dismiss" ? entry.undo.key : ""); return n; });
    } else if (entry.undo.row) {
      const { week: w, row } = entry.undo;
      patchRow(w, row.session_id, () => row);
    }
    setResolvedLog((l) => l.filter((_, x) => x !== i));
    say("Reverted.");
  };

  /** Ops takes a teacher out of the week on screen (or brings them back). Deterministic: Stage A
   *  rules them out and the week re-runs — the copilot is for finding cover, not for recording the
   *  fact. This works on the live week too: a teacher dropping out tomorrow is the whole point, and
   *  waiting for next week's draft to notice is not an answer. */
  const toggleUnavailable = async (smeId: string) => {
    const who = rosterFor(week).find((x) => x.id === smeId);
    if (!who) return;
    const off = !unavailable[smeId];
    const next = { ...unavailable, [smeId]: off };
    const had = (runs[week]?.draft ?? []).filter((r) => r.sme_id === smeId).length;
    setUnavailable(next);
    setLeave((l) => { const n = { ...l }; if (off) n[smeId] = "Marked unavailable by ops"; else delete n[smeId]; return n; });
    const res = await runWeek(week, { smes: rosterFor(week, next), quiet: true });
    const stranded = (res?.draft ?? []).filter((r) => isLive(r) && !r.sme_id).length;
    const when = WEEKS[week].locked ? "this week" : "next week";
    say(off
      ? `${who.name} marked unavailable ${when} — ${had} class(es) re-run; ${stranded
        ? `${stranded} still need cover — merge, reschedule or cancel them from the class.`
        : "everything found cover."}`
      : `${who.name} is available again — ${when} re-run.`);
  };

  // ---------- last-minute drop-outs: merge or cancel a single class ----------

  /** Fold this class into another batch's class for the hour. Both cohorts sit the surviving class,
   *  which is re-staffed for the higher of the two required levels by the ordinary pipeline. */
  const mergeClass = async (row: DraftRow, host: DraftRow) => {
    const merges = { ...sessionMerges, [row.session_id]: host.session_id };
    setSessionMerges(merges);
    setOverrides((log) => [{
      kind: "teacher change", session_id: row.session_id, batch_id: row.batch_id, week,
      from_sme_id: row.sme_id, to_sme_id: "", to_sme_name: host.batch_id, at: new Date().toISOString(),
      actor: "ops", note: `${row.batch_id} merged into ${host.batch_id} for this class — one room, both cohorts.`,
    }, ...log]);
    setSheet(null);
    await runWeek(week, { sessions: sessionsFor(week, sessionEdits, sessionCancels, merges), quiet: true });
    say(`${row.batch_id} merged into ${host.batch_id} for ${row.sub_specialty ?? META.type_label[row.type]}.`);
  };

  /** The bottom of the ladder. A reason is required — it is what the learners are told. */
  const cancelClass = async (row: DraftRow, reason: string) => {
    const cancels = { ...sessionCancels, [row.session_id]: { reason, by: "Ops", at: new Date().toISOString() } };
    setSessionCancels(cancels);
    setOverrides((log) => [{
      kind: "teacher change", session_id: row.session_id, batch_id: row.batch_id, week,
      from_sme_id: row.sme_id, to_sme_id: "", to_sme_name: "cancelled", at: new Date().toISOString(),
      actor: "ops", note: `Class cancelled — ${reason}`,
    }, ...log]);
    setSheet(null);
    setCancelReason("");
    await runWeek(week, { sessions: sessionsFor(week, sessionEdits, cancels, sessionMerges), quiet: true });
    say(`${row.batch_id} · ${row.sub_specialty ?? META.type_label[row.type]} cancelled — ${
      published[week] ? "re-publish to tell the learners." : "learners are told when the week is published."}`);
  };

  /** Put a dropped class back — undoes either a cancel or a merge. */
  const restoreClass = async (row: DraftRow) => {
    const cancels = { ...sessionCancels };
    const merges = { ...sessionMerges };
    delete cancels[row.session_id];
    delete merges[row.session_id];
    setSessionCancels(cancels);
    setSessionMerges(merges);
    setSheet(null);
    await runWeek(week, { sessions: sessionsFor(week, sessionEdits, cancels, merges), quiet: true });
    say(`${row.batch_id} · ${row.sub_specialty ?? META.type_label[row.type]} is back on the schedule.`);
  };

  // ---------- publish ----------

  const approveWeek = () => {
    if (unfilledCount) { say(`${unfilledCount} class(es) still have no teacher — clear them from Work items first.`); return; }
    if (conflictCount) { say(`${conflictCount} class(es) have a double-booked teacher — resolve them from Work items first.`); return; }
    setPubStatus({});
    setPubSel(ALL_LEAVES);
    setSheet({ kind: "publish" });
  };

  /** What this publish actually sends: the whole week normally, or just the rows that moved when the
   *  live week is being amended. Cancelled and merged classes travel with it — a class that is not
   *  running is the one thing people most need told. */
  const pubRows = useMemo(
    () => (amended[week] && changed.size ? rows.filter((r) => changed.has(r.session_id)) : rows),
    [amended, week, changed, rows],
  );
  const leaves = useMemo(() => publishLeaves(pubRows, batches), [pubRows, batches]);

  /** Publish for real, one leaf at a time: the API sends where it has credentials and reports
   *  `simulated` where it does not — either way the outcome is recorded server-side. */
  const publishWeek = async () => {
    const list = leaves.filter((l) => pubSel[l.id]);
    if (!list.length) { say("Pick at least one channel and audience."); return; }
    const token = pubToken.current + 1;
    pubToken.current = token;
    setPubStatus(Object.fromEntries(list.map((l) => [l.id, "sending" as SendState])));
    // Concurrent, but each leaf still reports the moment it lands — that per-leaf feedback is the
    // reason the loop was serial, and it is worth keeping. Wall clock becomes the slowest leaf
    // instead of the sum of all six.
    const outcomes = await Promise.all(list.map(async (l): Promise<boolean | null> => {
      try {
        const res = await publishLeaf({
          week: WEEKS[week].iso, week_label: WEEKS[week].label, channel: l.channel.key, audience: l.audience.key,
          rows: pubRows, smes: rosterFor(week), batches,
        });
        if (pubToken.current !== token) return false;   // cancelled mid-flight, per callback
        setPubStatus((s) => ({ ...s, [l.id]: res.status as SendState }));   // sent | simulated | skipped | error
        return res.live;
      } catch {
        // the request itself failed (network, 5xx) — not a simulated leaf, and not a published week
        if (pubToken.current === token) setPubStatus((s) => ({ ...s, [l.id]: "error" }));
        return null;
      }
    }));
    if (pubToken.current !== token) return;
    const failed = outcomes.filter((o) => o === null).length;
    if (failed) {
      say(`Publish incomplete — ${failed} of ${list.length} send(s) failed. The week is still a draft; check the connection and send again.`);
      return;
    }
    // reduced after the fact rather than written from several callbacks
    const anyLive = outcomes.some(Boolean);
    const amending = !!amended[week];
    setApproved((a) => new Set([...a, ...pubRows.map((r) => r.session_id)]));
    setDecisions((d) => ({ ...d, ...Object.fromEntries(pubRows.map((r) => [r.session_id, { session_id: r.session_id, action: "approve" as const }])) }));
    setPublished((p) => ({ ...p, [week]: true }));
    if (amending) { setAmended((a) => ({ ...a, [week]: false })); setChanged(new Set()); }
    persist(save(WEEKS[week].iso, rows, { published: true, stats: run?.stats, flags: run?.flags }));
    const what = amending ? `${pubRows.length} change${pubRows.length === 1 ? "" : "s"} sent` : "Week published";
    say(anyLive
      ? `${what} — ${sendSummary(list)}.`
      : `${what} — ${sendSummary(list)} simulated (no channel credentials yet).`);
  };

  /** Re-send only what moved. The live week stays published; a drop-out touched a handful of classes
   *  and re-announcing the other forty is how people learn to ignore the announcements. */
  const republishChanges = () => {
    if (!changed.size) { say("Nothing has changed since the week was published."); return; }
    setPubStatus({});
    setPubSel(ALL_LEAVES);
    setSheet({ kind: "publish" });
  };

  const cancelPublish = () => {
    pubToken.current += 1;
    pubTimers.current.forEach(clearTimeout);
    pubTimers.current = [];
    setPubStatus({});
    setSheet(null);
    say("Send cancelled — the week is still a draft.");
  };

  // ---------- batches & classes ----------

  const exportCsv = async () => {
    if (!run) return;
    setLoading(true);
    try {
      // the settled week is approved on load without an explicit decision — export it as approved
      const decs = rows
        .map((r) => decisions[r.session_id]
          ?? (approved.has(r.session_id) ? { session_id: r.session_id, action: "approve" as const } : null))
        .filter(Boolean) as Decision[];
      const out = await submitApprovals(run.draft, decs, WEEKS[week].iso, "human");
      getOverrides().then(setOverrideStats).catch(() => {});
      await csvExporter.export(out.export_rows, `ik-schedule-${WEEKS[week].iso}`);
      const risky = out.final_schedule.filter((r) => r.flags.some((f) => f.code === "RULE_OVERRIDE_RISK"));
      say(risky.length ? `CSV exported — ${risky.length} override(s) flagged OVERRIDE RISK.` : "CSV exported.");
    } catch (e) {
      say(String(e).slice(0, 160));
    } finally {
      setLoading(false);
    }
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

  const openNewClass = () => {
    const bt = batches.find((b) => b.id === selBatch) ?? batches[0];
    setNewClass({ topic: COURSES[bt.course].topics[0], type: "class", day: 0, hour: 10, smeId: "" });
    setSheet({ kind: "newClass" });
  };

  /** Teachers who carry the topic, work that hour and are not already booked in it. */
  const freeFor = useCallback((topic: string, day: number, hour: number, level: number): SME[] => {
    const ref = sessionData.next[0]?.start_utc;
    if (!ref) return [];
    const nextRows = runs.next?.draft ?? [];
    return smesFor("next").filter((s) => s.topics.includes(topic))
      .filter((s) => s.training_level >= level)
      .filter((s) => isAvailable(s, ref, day, hour))
      .filter((s) => !nextRows.some((r) => r.sme_id === s.id && istParts(r.start_utc).day === day && istParts(r.start_utc).hour === hour));
  }, [runs.next, smesFor]);

  const addClass = async () => {
    const bt = batches.find((b) => b.id === selBatch) ?? batches[0];
    const ref = sessionData.next[0].start_utc;
    const p0 = istParts(ref);
    const at = new Date(new Date(ref).getTime() + (newClass.day - p0.day) * 864e5 + (newClass.hour - p0.hour) * 36e5);
    const level = META.levels.indexOf(bt.level) + 1;
    const session: Session = {
      id: `${WEEKS.next.iso.split("-")[1]}-${bt.id}-x${extraSessions.filter((s) => s.batch_id === bt.id).length + 1}`,
      batch_id: bt.id, subject: bt.course,
      sub_specialty: newClass.type === "class" ? newClass.topic : null,
      type: newClass.type, start_utc: at.toISOString().replace(/\.\d+Z$/, "Z"),
      duration_min: 60, mode: "online", required_training_level: level,
    };
    setExtraSessions((s) => [...s, session]);
    setSheet(null);
    setWeek("next");
    if (published.next) setPublished((p) => ({ ...p, next: false }));
    // a chosen teacher rides in as a named override so the engine honours it or says why it cannot
    const named: OverrideEvent[] = newClass.smeId ? [{
      kind: "assigned", session_id: session.id, batch_id: bt.id, week: "next",
      from_sme_id: null, to_sme_id: newClass.smeId, to_sme_name: smeName(newClass.smeId),
      at: new Date().toISOString(), note: "Added by ops outside the weekly run.",
    }] : [];
    setOverrides((log) => [...(named.length ? named : [{
      kind: "assigned" as const, session_id: session.id, batch_id: bt.id, week: "next" as const,
      from_sme_id: null, to_sme_id: "", to_sme_name: "—",
      at: new Date().toISOString(), note: "Added by ops — needs a teacher before the week can publish.",
    }]), ...log]);
    await runNext({ sessions: [...sessionsFor("next"), session], overrides: [...named, ...overrides], quiet: true });
    say(newClass.smeId
      ? `${newClass.topic} added to ${bt.id} — ${smeName(newClass.smeId)} assigned.${published.next ? " Re-publish to update calendars." : ""}`
      : `${newClass.topic} added to ${bt.id} — the draft staffs it from the eligible pool.`);
  };

  // ---------- the Excel round-trip ----------

  const readCsv = (file: File, onText: (text: string) => void, onWorkbook: () => void) => {
    if (isWorkbook(file.name)) { onWorkbook(); return; }
    const fr = new FileReader();
    fr.onload = () => onText(String(fr.result ?? ""));
    fr.readAsText(file);
  };

  /** What the week already holds, so an upload cannot double-book a batch or a teacher. */
  const takenSlots = () => rows.map((r) => {
    const p = istParts(r.start_utc);
    return { smeId: r.sme_id, day: p.day, hour: p.hour, batch: r.batch_id };
  });

  /** The one place CSV text becomes checked rows. A file upload and a Google Sheet tab both land
   *  here, so there is a single column contract and a single place row errors are worded. */
  const parseClasses = useCallback((name: string, text: string) => setImp(parseClassImport(name, text, {
    courses: COURSES, levels: META.levels, types: META.type_label, days: META.days,
    hours: META.hours, taken: takenSlots(), smes: smesFor("next"),
    isAvailable: (s, d, h) => isAvailable(s, sessionData.next[0].start_utc, d, h),
  })), [rows, smesFor]);

  const parseSmes = useCallback((name: string, text: string) => setSmeImp(parseSmeImport(name, text, {
    courses: COURSES, levels: META.levels, days: META.days, smes: rosterFor("next"),
  })), [rosterFor]);

  const onImportFile = (file: File) => readCsv(
    file,
    (text) => parseClasses(file.name, text),
    () => setImp({ name: file.name, rows: [], errors: [WORKBOOK_HINT], parsed: true }),
  );

  const onSmeImportFile = (file: File) => readCsv(
    file,
    (text) => parseSmes(file.name, text),
    () => setSmeImp({ name: file.name, rows: [], errors: [WORKBOOK_HINT], parsed: true }),
  );

  /** Pull a dataset as CSV text from whichever source is configured, then hand it to the same
   *  parser the file picker uses. Nothing is applied until the coordinator confirms the check. */
  const pullFromSheet = async (dataset: "sessions" | "smes" | "history") => {
    setSheetBusy(true);
    try {
      const res = await pullSheet(dataset);
      const label = `${res.source ?? "Google Sheet"} · ${res.tab ?? dataset}`;
      setPulled((p) => ({ ...p, [dataset]: { source: res.source ?? "Google Sheet", at: res.synced_at ?? new Date().toISOString(), rows: res.count } }));
      if (!res.csv) {
        const issue = { line: "Source", msg: res.detail };
        if (dataset === "smes") setSmeImp({ name: label, rows: [], errors: [issue], parsed: true });
        else if (dataset === "history") setHistImp({ name: label, rows: [], errors: [issue], parsed: true });
        else setImp({ name: label, rows: [], errors: [issue], parsed: true });
        return;
      }
      if (dataset === "smes") parseSmes(label, res.csv);
      else if (dataset === "history") setHistImp(parseHistoryImport(label, res.csv, { smes: rosterFor("next") }));
      else parseClasses(label, res.csv);
      say(res.detail);
    } catch (e) {
      say(String(e).slice(0, 160));
    } finally {
      setSheetBusy(false);
    }
  };

  /** Back to the bundled seed week — demo safety, and the honest way to undo a bad import. */
  const resetToSeed = async () => {
    if (!window.confirm("Discard imported sessions, roster and history, and go back to the bundled seed week?")) return;
    try {
      await resetData();
      setSessionData(SESSIONS);
      setSmeData(SMES);
      setHistoryRecords(HISTORY);
      setBatches(BATCHES0);
      setExtraSessions([]);
      setExtraSmes([]);
      setSessionEdits({});
      setSmeEdits({});
      setProvenance({});
      setPulled({});
      await runNext({ sessions: SESSIONS.next, smes: SMES.next, quiet: true });
      say("Back to the bundled seed week.");
    } catch (e) {
      say(String(e).slice(0, 160));
    }
  };

  /** An imported history replaces what Stage B scores fairness and performance from. */
  const runHistoryImport = async () => {
    const recs = histImp.rows.map(toHistoryRecord) as HistoryRecord[];
    setHistoryRecords(recs);
    const srcH = pulled.history?.source ?? "CSV upload";
    setProvenance((p) => ({ ...p, history: pulled.history ?? { source: srcH, at: new Date().toISOString(), rows: recs.length } }));
    persist(putData("history", recs, srcH));
    setHistImp(emptyImport());
    setSheet(null);
    await runNext({ quiet: true });
    say(`${recs.length} history row(s) applied — fairness and performance re-scored.`);
  };

  /** The approved week into the Sheet's draft tab — the same rows the CSV export writes. */
  const pushToSheet = async () => {
    if (!run) return;
    setSheetBusy(true);
    try {
      const decs = rows
        .map((r) => decisions[r.session_id]
          ?? (approved.has(r.session_id) ? { session_id: r.session_id, action: "approve" as const } : null))
        .filter(Boolean) as Decision[];
      const out = await submitApprovals(run.draft, decs, WEEKS[week].iso, "human");
      const res = await pushSheet(WEEKS[week].iso, WEEKS[week].label, out.export_rows);
      say(res.live ? res.detail : `${res.detail} Set SHEET_ID and Google credentials to write for real.`);
    } catch (e) {
      say(String(e).slice(0, 160));
    } finally {
      setSheetBusy(false);
    }
  };

  /** Imported classes become real sessions and go through the same pipeline as everything else —
   *  a named teacher rides in as an override so the engine honours it or says why it cannot. */
  const runImport = async () => {
    const ref = sessionData.next[0].start_utc;
    const p0 = istParts(ref);
    const known = new Set(batches.map((b) => b.id));
    const fresh: Batch[] = [];
    const sessions: Session[] = [];
    const named: OverrideEvent[] = [];
    const at = new Date().toISOString();

    // QA-03: ids used to restart at -i1 on every import, so importing a file twice produced two
    // sessions with one id. Continue from the highest suffix already on the week instead.
    const importSeq = sessionsFor("next").reduce((m, s) => Math.max(m, Number(/-i(\d+)$/.exec(s.id)?.[1] ?? 0)), 0);
    imp.rows.forEach((r, i) => {
      if (!known.has(r.batch)) {
        known.add(r.batch);
        fresh.push({ id: r.batch, course: r.course, level: r.level, learners: r.learners,
          per_week: 4, weeks_done: 0, weeks_total: 12, started: "7 Sep 2026" });
      }
      const when = new Date(new Date(ref).getTime() + (r.day - p0.day) * 864e5 + (r.hour - p0.hour) * 36e5);
      const id = `${WEEKS.next.iso.split("-")[1]}-${r.batch}-i${importSeq + i + 1}`;
      sessions.push({
        id, batch_id: r.batch, subject: r.course,
        sub_specialty: r.type === "class" ? r.topic : null, type: r.type,
        start_utc: when.toISOString().replace(/\.\d+Z$/, "Z"), duration_min: 60, mode: "online",
        required_training_level: META.levels.indexOf(r.level) + 1,
      });
      if (r.smeId) {
        named.push({ kind: "assigned", session_id: id, batch_id: r.batch, week: "next",
          from_sme_id: null, to_sme_id: r.smeId, to_sme_name: r.smeName ?? smeName(r.smeId), at,
          note: `Named in ${imp.name}.` });
      }
    });

    const unfilled = imp.rows.length - named.length;
    const log: OverrideEvent = {
      kind: "assigned", session_id: sessions[0]?.id ?? "", batch_id: imp.rows[0]?.batch ?? "—", week: "next",
      from_sme_id: null, to_sme_id: "", to_sme_name: "ops", at,
      note: `${imp.rows.length} classes imported from ${imp.name}${fresh.length ? ` · ${fresh.length} new batch${fresh.length === 1 ? "" : "es"} created` : ""}${unfilled ? ` · ${unfilled} still awaiting a teacher` : ""}.`,
    };

    if (fresh.length) setBatches((b) => [...b, ...fresh]);
    setExtraSessions((s) => [...s, ...sessions]);
    setOverrides((l) => [log, ...named, ...l]);
    setSheet(null);
    setImp(emptyImport());
    setMod("dashboard");
    setTab("schedule");
    setWeek("next");
    const srcS = pulled.sessions?.source ?? "CSV upload";
    setProvenance((p) => ({ ...p, sessions: pulled.sessions ?? { source: srcS, at, rows: imp.rows.length } }));
    persist(putData("sessions_next", [...sessionsFor("next"), ...sessions], srcS));
    await runNext({ sessions: [...sessionsFor("next"), ...sessions], overrides: [...named, ...overrides], quiet: true });
    say(`${imp.rows.length} classes imported${fresh.length ? ` · ${fresh.length} new batch${fresh.length === 1 ? "" : "es"}` : ""}.`);
  };

  /** Imported teachers join the pool and are assignable on the very next draft. */
  const runSmeImport = async () => {
    const added = smeImp.rows.map((r) => toSme(r, META.days, META.levels));
    setExtraSmes((s) => [...s, ...added]);
    const src = pulled.smes?.source ?? "CSV upload";
    setProvenance((p) => ({ ...p, smes: pulled.smes ?? { source: src, at: new Date().toISOString(), rows: added.length } }));
    persist(putData("smes", [...smeData.next, ...added], src));
    setSheet(null);
    setSmeImp(emptyImport());
    setSmeFilter("all");
    setSmeQuery("");
    setMod("smes");
    if (added[0]) setSelSme(added[0].id);
    say(`${added.length} SME${added.length === 1 ? "" : "s"} added to the pool — they are assignable right away.`);
  };

  const openProfile = (id: string) => {
    const s = rosterFor("next").find((x) => x.id === id);
    if (!s) return;
    setProf({ id: s.id, name: s.name, email: s.email ?? "", phone: s.phone ?? "", city: s.city, level: s.level, preferred: String(s.preferred) });
    setSheet({ kind: "profile" });
  };

  const saveProfile = () => {
    if (!prof) return;
    const pref = Math.max(1, Math.min(8, parseInt(prof.preferred, 10) || 4));
    setSmeEdits((e) => ({
      ...e,
      [prof.id]: { ...e[prof.id], name: prof.name.trim(), email: prof.email.trim(), phone: prof.phone.trim(),
        city: prof.city.trim(), level: prof.level, preferred: pref, training_level: META.levels.indexOf(prof.level) + 1 },
    }));
    if (prof.id === META.me) setPreferred(pref);
    setSheet(null);
    setProf(null);
    say(`${prof.name.trim()}’s profile updated.`);
  };

  const requestCover = (row: DraftRow) => {
    setOverrides((log) => [{
      kind: "change requested", session_id: row.session_id, batch_id: row.batch_id, week,
      from_sme_id: row.sme_id, to_sme_id: "", to_sme_name: "ops", at: new Date().toISOString(),
      note: "Raised by the SME from their own week — ops to find cover.", changed_rows: [],
    }, ...log]);
    setSheet(null);
    say("Sent to ops — they will find cover and confirm.");
  };

  const toggleAvail = async (key: string) => {
    const nextOff = { ...availOff, [key]: !availOff[key] };
    setAvailOff(nextOff);
    const res = await runNext({ availOff: nextOff, quiet: true });
    const mine = res ? res.draft.filter((r) => r.sme_id === META.me).length : 0;
    say(`${nextOff[key] ? "Block marked off" : "Block re-opened"} — next week re-drafted, you now have ${mine} class(es).`);
  };

  /** Read the week's calendars, then re-draft against what is already booked. */
  const runAvailabilitySync = async () => {
    const first = sessionData.next[0]?.start_utc;
    if (!first) return;
    const start = new Date(new Date(first).getTime() - istParts(first).day * 864e5);
    const end = new Date(start.getTime() + 7 * 864e5);
    setSyncBusy(true);
    try {
      const res = await syncAvailability(rosterFor("next"), start.toISOString(), end.toISOString());
      setBusyBlocks(res.per_sme ?? {});
      setSyncDetail(res.detail);
      const map: Record<string, { start_utc: string; end_utc: string }[]> = {};
      res.smes.forEach((s) => { if (s.external_busy?.length) map[s.id] = s.external_busy; });
      setExternalBusy(map);
      const roster = rosterFor("next").map((s) => (map[s.id] ? { ...s, external_busy: map[s.id] } : s));
      await runNext({ smes: roster, quiet: true });
      say(res.live
        ? `${res.detail} Draft re-run against the calendars.`
        : `${res.detail} Set Google credentials to read calendars for real.`);
    } catch (e) {
      say(String(e).slice(0, 160));
    } finally {
      setSyncBusy(false);
    }
  };

  // ---------- copilot ----------

  const openAgent = (req: AgentRequest) => {
    setAgentReq(req);
    setAgentRes(null);
    setSheet({ kind: "agent" });
  };

  /** The history the copilot scores fairness against: for next week, the settled current week counts
   *  as its most recent past week; for the live week it is the record as imported. */
  const agentHistory = useCallback((w: WeekKey) => (
    w === "next" && runs.current
      ? weekAsHistory(runs.current.draft, rosterFor(w), WEEKS.current.iso, historyRecords)
      : historyRecords
  ), [runs.current, rosterFor, historyRecords]);

  const runAgent = async () => {
    const target = runs[week];
    if (!target) return;
    setAgentBusy(true);
    try {
      const roster = rosterFor(week);
      setAgentRes(await agentRun(WEEKS[week].iso, agentReq, target.draft, roster, agentHistory(week), undefined, batches));
    } catch (e) {
      say(String(e).slice(0, 160));
    } finally {
      setAgentBusy(false);
    }
  };

  /** Every move goes through the engine's override path server-side (actor `agent`), Stage D re-validates,
   *  and the result replaces the draft — exactly what a manual override does, logged as the Copilot. */
  const applyPlan = async (plan: AgentMoveAction[], draft?: DraftRow[]): Promise<boolean> => {
    const target = runs[week];
    if (!target || !plan.length) return false;
    const base = draft ?? target.draft;
    const iso = WEEKS[week].iso;
    try {
      const roster = rosterFor(week);
      const out = await agentApply(iso, plan, base, roster, agentHistory(week));
      const at = new Date().toISOString();
      if (week === "next") nextRef.current = out.draft;
      setRuns((s) => ({ ...s, [week]: { draft: out.draft, flags: out.flags, stats: { ...target.stats, ...out.stats } as RunResult["stats"] } }));
      setChanged(new Set(out.applied));
      setOverrides((log) => [...out.override_log.map((e): OverrideEvent => ({
        kind: e.from_sme_id ? "teacher change" : "assigned", session_id: e.session_id, batch_id: e.batch_id, week,
        from_sme_id: e.from_sme_id, to_sme_id: e.to_sme_id, to_sme_name: e.to_sme_name, at, actor: "Copilot",
        note: `${e.reason ?? "Copilot plan"} — applied via the override path and re-validated by Stage D.`,
      })), ...log]);
      setApproved((a) => new Set([...a].filter((id) => !out.applied.includes(id))));
      setDecisions((d) => ({ ...d, ...Object.fromEntries(out.override_log.map((e) => [e.session_id,
        { session_id: e.session_id, action: "override" as const, override_sme_id: e.to_sme_id }])) }));
      // a draft un-publishes wholesale; the live week keeps its publish and is marked amended
      if (published[week]) {
        if (WEEKS[week].locked) setAmended((a) => ({ ...a, [week]: true }));
        else setPublished((p) => ({ ...p, [week]: false }));
      }
      persist(save(iso, out.draft, { stats: out.stats, flags: out.flags,
        published: WEEKS[week].locked ? !!published[week] : false }));
      getOverrides().then(setOverrideStats).catch(() => {});
      return true;
    } catch (e) {
      say(String(e).slice(0, 160));
      return false;
    }
  };

  /** Everything except a staffing move changes the source data — when a class runs, whether it runs
   *  at all, whose cohort sits in it, a teacher's level — so those are applied here and the whole
   *  pipeline re-runs over them: Stage A–D from scratch, a stronger check than validating in place.
   *  Staffing moves then go through the override route against that fresh draft. */
  const applyActions = async (plan: AgentMove[]): Promise<boolean> => {
    const moves = plan.filter(isMove);
    const reschedules = plan.filter(isReschedule);
    const upgrades = plan.filter(isUpgrade);
    const merges = plan.filter(isMerge);
    const cancels = plan.filter(isCancel);
    const at = new Date().toISOString();
    let draftRows = runs[week]?.draft ?? [];

    if (reschedules.length || upgrades.length || merges.length || cancels.length) {
      const edits = { ...sessionEdits, ...Object.fromEntries(reschedules.map((r) => [r.session_id, r.start_utc])) };
      const drops = { ...sessionCancels, ...Object.fromEntries(cancels.map((c) => [c.session_id,
        { reason: c.reason || "Cancelled by the copilot", by: "Copilot", at }])) };
      const folds = { ...sessionMerges, ...Object.fromEntries(merges.map((m) => [m.session_id, m.into_session_id])) };
      const nextEdits: Record<string, Partial<SME>> = { ...smeEdits };
      upgrades.forEach((u) => {
        nextEdits[u.sme_id] = { ...nextEdits[u.sme_id], training_level: u.to_level, level: META.levels[u.to_level - 1] };
      });
      const roster = [...smeData[week], ...extraSmes].map((x) => (nextEdits[x.id] ? { ...x, ...nextEdits[x.id] } : x));
      setSessionEdits(edits);
      setSessionCancels(drops);
      setSessionMerges(folds);
      setSmeEdits(nextEdits);
      setOverrides((log) => [
        ...reschedules.map((r): OverrideEvent => ({
          kind: "teacher change", session_id: r.session_id,
          batch_id: draftRows.find((x) => x.session_id === r.session_id)?.batch_id ?? "—", week: "next",
          from_sme_id: null, to_sme_id: "", to_sme_name: `${r.to_day} ${r.to_hour_ist}`, at, actor: "Copilot",
          note: `Class moved from ${r.from_day} ${r.from_hour_ist} to ${r.to_day} ${r.to_hour_ist} — ${r.reason || "copilot plan"}.`,
        })),
        ...upgrades.map((u): OverrideEvent => ({
          kind: "assigned", session_id: u.unblocks?.[0] ?? "", batch_id: "roster", week,
          from_sme_id: null, to_sme_id: u.sme_id, to_sme_name: u.sme_name, at, actor: "Copilot",
          note: `Training level raised ${u.from_level} → ${u.to_level} — ${u.reason || "copilot plan"}.`,
        })),
        ...merges.map((m): OverrideEvent => ({
          kind: "teacher change", session_id: m.session_id, batch_id: m.batch_id, week,
          from_sme_id: m.from_sme, to_sme_id: "", to_sme_name: m.host_batch_id, at, actor: "Copilot",
          note: `${m.batch_id} merged into ${m.host_batch_id} for this class — ${m.reason || "copilot plan"}.`,
        })),
        ...cancels.map((c): OverrideEvent => ({
          kind: "teacher change", session_id: c.session_id, batch_id: c.batch_id, week,
          from_sme_id: c.from_sme, to_sme_id: "", to_sme_name: "cancelled", at, actor: "Copilot",
          note: `Class cancelled — ${c.reason || "copilot plan"}.`,
        })),
        ...log,
      ]);
      const res = await runWeek(week, { sessions: sessionsFor(week, edits, drops, folds), smes: roster, quiet: true });
      if (!res) return false;
      draftRows = res.draft;
      // runNext diffs teacher and flags; a class that only changed hour would otherwise show no dot
      if (reschedules.length) {
        setChanged((c) => new Set([...c, ...reschedules.map((r) => r.session_id)]));
      }
    }

    if (moves.length) {
      // the fresh draft may already have staffed a rescheduled class; only send moves that still apply
      const live = moves.filter((m) => draftRows.some((r) => r.session_id === m.session_id && r.sme_id !== m.to_sme));
      if (live.length && !(await applyPlan(live, draftRows))) return false;
    }
    if (published[week] && !WEEKS[week].locked) setPublished((p) => ({ ...p, [week]: false }));
    else if (published[week]) setAmended((a) => ({ ...a, [week]: true }));
    const n = plan.length;
    say(`Copilot plan applied — ${n} change${n === 1 ? "" : "s"}.`);
    return true;
  };

  const applyAgentPlan = async () => {
    if (!agentRes?.plan?.length) return;
    setAgentBusy(true);
    const ok = await applyActions(agentRes.plan);
    setAgentBusy(false);
    if (ok) {
      setSheet(null);
      setAgentRes(null);
      setMod("dashboard");
    }
  };

  /** The floating chat: one turn in, one turn out, the whole conversation replayed server-side. */
  const sendChat = async () => {
    const text = chatDraft.trim();
    const nxt = runs.next;
    if (!text || !nxt || !runs.current) return;
    const history0 = chatTurns;
    setChatTurns((t) => [...t, { role: "user", content: text }]);
    setChatDraft("");
    setChatBusy(true);
    try {
      const roster = rosterFor("next");
      const history = weekAsHistory(runs.current.draft, roster, WEEKS.current.iso, historyRecords);
      const res = await agentRun(WEEKS.next.iso, { mode: "chat", question: text }, nxt.draft, roster, history, history0);
      setChatTurns((t) => [...t, { role: "assistant", content: res.answer, res }]);
    } catch (e) {
      setChatTurns((t) => [...t, { role: "assistant", content: `That did not go through — ${String(e).slice(0, 140)}` }]);
    } finally {
      setChatBusy(false);
    }
  };

  const applyChatPlan = async (index: number) => {
    const turn = chatTurns[index];
    if (!turn?.res?.plan?.length || turn.applied) return;
    setChatBusy(true);
    const ok = await applyActions(turn.res.plan);
    setChatBusy(false);
    if (ok) {
      setChatTurns((t) => t.map((x, i) => (i === index ? { ...x, applied: true } : x)));
      setMod("dashboard");
      setTab("schedule");
    }
  };

  // ---------- sheets ----------

  const sheetRow = sheet && (sheet.kind === "class" || sheet.kind === "ghost")
    ? (runs[sheet.week]?.draft ?? []).find((r) => r.session_id === sheet.sessionId) ?? null
    : null;

  const renderSheet = () => {
    if (!sheet) return null;

    if (sheet.kind === "work") {
      const blockers = work.filter((w) => w.blocking).length;
      return (
        <Sheet
          width={620} eyebrow={`${WEEKS[week].label} · ${WEEKS[week].range}`}
          title={work.length ? `${work.length} decision${work.length === 1 ? "" : "s"} before you can publish` : "Week is clear"}
          subtitle={work.length
            ? `${blockers} blocking · ${work.length - blockers} advisory${resolvedLog.length ? ` · ${resolvedLog.length} resolved` : ""}`
            : "Nothing is blocking this week."}
          footerNote={work.length
            ? (blockers ? "The week cannot publish while anything is blocking." : "Nothing blocking — advisory items can wait.")
            : "Approving the week is the next step."}
          footer={[{ label: work.length ? "Close" : "Save & close", kind: work.length ? undefined : "go", onClick: () => setSheet(null) }]}
          onClose={() => setSheet(null)}
        >
          <WorkSheet
            items={work} fixes={fixes} auto={!!sheet.auto} resolved={resolvedLog}
            onReview={() => setSheet({ kind: "work", auto: true })}
            onDiscard={() => setSheet({ kind: "work" })}
            onApplyFix={(w, f) => applyFix(w, f)}
            onApplyAll={applyAllFixes}
            onUndo={undoResolve}
            onOpenClass={(w) => { setMod("dashboard"); setTab("schedule"); openClass(w.session_id!, w.code === "UNFILLED" ? "pick" : "info"); }}
            onOpenSme={(id) => { setMod("smes"); setSelSme(id); setSheet(null); }}
          />
        </Sheet>
      );
    }

    if (sheet.kind === "import" || sheet.kind === "smeImport") {
      const sme = sheet.kind === "smeImport";
      // History is SME data, so it shares this sheet rather than earning a screen of its own.
      const hist = sme && histImp.parsed;
      const im: ImportResult<unknown> = hist ? histImp : sme ? smeImp : imp;
      const ok = im.rows.length, bad = im.errors.length;
      const reset = () => { setHistImp(emptyImport()); return sme ? setSmeImp(emptyImport()) : setImp(emptyImport()); };
      const noTeacher = sme
        ? smeImp.rows.filter((r) => !r.topics.length).length
        : imp.rows.filter((r) => !r.smeId).length;
      const steps = sme
        ? [
          { n: "1", title: "Download the SME template",
            sub: "Eleven columns — name, email, phone, city, courses, topics, level, weekly preference and working hours — with two example rows.",
            action: "Download", onAction: () => { downloadCsv(smeTemplate(COURSES, META.levels), "ik-sme-template"); say("SME template downloaded — fill it in Excel and upload it back."); } },
          { n: "2", title: "One row per teacher",
            sub: "Email is required and must be unique — it is how invites and the weekly schedule reach them. Leave sme_id blank and we issue the next one." },
          { n: "3", title: "Save as CSV and upload",
            sub: "We check every row against the course topics and level rules before anything is created." },
          { n: "4", title: "Assignment history (optional)",
            sub: "Five columns — sme_id, week, sessions_taught, batches, per_topic_rating. This is what fairness and performance are scored from.",
            action: sheetBusy ? "Pulling…" : "Pull history",
            onAction: () => void pullFromSheet("history") },
        ]
        : [
          { n: "1", title: "Download the template",
            sub: "Nine columns with two example rows and the allowed values listed at the bottom. Opens straight in Excel.",
            action: "Download", onAction: () => { downloadCsv(classTemplate(batches, COURSES, META.days, META.hours, rosterFor("next")), "ik-schedule-template"); say("Template downloaded — fill it in Excel and upload it back."); } },
          { n: "2", title: "Fill one row per class",
            sub: "A new batch_id creates the batch; an existing one adds to it. Leave sme_name blank and the scheduler fills it." },
          { n: "3", title: "Upload it back",
            sub: "Every row is checked against courses, topics, working hours and double bookings before anything is created." },
        ];
      return (
        <Sheet
          width={im.parsed ? 620 : 780} eyebrow={sme ? "SME management" : "Batch management"}
          title={im.parsed ? (hist ? "Check the history" : "Check the upload") : sme ? "Import SMEs" : "Import from Excel"}
          subtitle={im.parsed
            ? `${im.name} · ${ok} ${hist ? `history row${ok === 1 ? "" : "s"}` : sme ? `SME${ok === 1 ? "" : "s"}` : `row${ok === 1 ? "" : "s"}`} ready${bad ? `, ${bad} to fix` : ""}`
            : sme
              ? "Onboard a batch of teachers at once. Download the template so the columns match, fill it in Excel, upload it back."
              : "Bring your batch → class → SME tracker in. Download the template so the columns match, fill it in Excel, upload it back."}
          footerNote={im.parsed
            ? (ok
              ? hist
                ? "Applying replaces the history the draft scores fairness and performance from, then re-runs it."
                : sme
                ? "New SMEs join the pool straight away and become assignable for unfilled classes."
                : `Imports into ${WEEKS.next.label.toLowerCase()} · anything without a teacher lands in Work items`
              : "Fix the rows above in Excel and upload again")
            : "Nothing is created until you review the check."}
          footer={im.parsed
            ? [
              { label: "Upload another", onClick: reset },
              ...(ok ? [{
                label: hist ? `Apply ${ok} history row${ok === 1 ? "" : "s"}`
                  : sme ? `Add ${ok} SME${ok === 1 ? "" : "s"}` : `Import ${ok} class${ok === 1 ? "" : "es"}`,
                kind: "go" as const,
                onClick: () => void (hist ? runHistoryImport() : sme ? runSmeImport() : runImport()),
              }] : []),
            ]
            : [{ label: "Cancel", onClick: () => setSheet(null) }]}
          onClose={() => { setSheet(null); reset(); }}
        >
          <ImportSheet
            steps={im.parsed ? null : steps}
            stepSize={sme ? 22 : 24}
            warnInk={sme ? "#8a5218" : "#8a6512"}
            dropTitle="Choose your populated file"
            dropSub="CSV saved from Excel · we check it before importing"
            onFile={sme ? onSmeImportFile : onImportFile}
            historyAction={sme ? { label: "Download history template", onClick: () => { downloadCsv(historyTemplate(rosterFor("next")), "ik-history-template"); say("History template downloaded."); } } : undefined}
            onPullSheet={() => void pullFromSheet(sme ? "smes" : "sessions")}
            sheetLive={integrations?.sheets.live}
            sheetBusy={sheetBusy}
            tallies={im.parsed ? [
              { value: ok, label: hist ? "ready to apply" : sme ? "ready to add" : "ready to import", tone: ok ? "good" : "warn" },
              { value: bad, label: "need fixing", tone: bad ? "bad" : "good" },
              ...(hist ? [] : [{ value: noTeacher, label: sme ? "no topics yet" : "no teacher yet", tone: "warn" as const }]),
            ] : null}
            issues={im.errors}
            preview={hist
              ? histImp.rows.map((r, i) => ({ key: `${r.smeId}-${r.week}-${i}`, tag: r.smeId, main: smeName(r.smeId),
                when: `${r.week} · ${r.sessionsTaught} taught`,
                who: r.batches.join(", ") || "no batches", whoTone: "plain" as const }))
              : sme
              ? smeImp.rows.map((r) => ({ key: r.id, tag: r.id, main: r.name, when: r.email, who: `${r.level} · ${r.preferred}/wk`, whoTone: "plain" as const }))
              : imp.rows.map((r, i) => ({
                key: `${r.batch}-${i}`, tag: r.batch, main: `${r.topic} · ${META.type_label[r.type]}`,
                when: `${META.days[r.day]} ${String(r.hour).padStart(2, "0")}:00`,
                who: r.smeId ? smeName(r.smeId) : "to be filled", whoTone: r.smeId ? "good" as const : "warn" as const,
              }))}
          />
        </Sheet>
      );
    }

    if (sheet.kind === "profile") {
      if (!prof) return null;
      const self = role !== "coordinator";
      const sm = rosterFor("next").find((x) => x.id === prof.id);
      const emailOk = /^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(prof.email.trim());
      const nameOk = prof.name.trim().length > 1;
      const set = (k: keyof Profile) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
        setProf((p) => (p ? { ...p, [k]: e.target.value } : p));
      // the artboard's own field metrics — `.label-caps` is 10.5px, this form's labels are 11px
      const LABEL: React.CSSProperties = {
        display: "block", fontSize: 11, fontWeight: 600, textTransform: "uppercase",
        letterSpacing: "0.06em", color: "var(--muted-3)", marginBottom: 6,
      };
      const HINT: React.CSSProperties = { display: "block", fontSize: 11, color: "var(--muted-2)", marginTop: 5, lineHeight: 1.4 };
      const READONLY: React.CSSProperties = {
        display: "block", borderRadius: 11, border: "1px solid #edf1f7", background: "#f7f9fc",
        padding: "9px 10px", fontSize: 12.5, color: "var(--muted)",
      };
      const field = (label: string, hint: string, node: React.ReactNode, wide = false) => (
        <label className={wide ? "col-span-2 block" : "block"} key={label}>
          <span style={LABEL}>{label}</span>
          {node}
          {hint && <span style={HINT}>{hint}</span>}
        </label>
      );
      return (
        <Sheet
          width={560} eyebrow={self ? "My profile" : "SME management"}
          title={self ? "Edit my profile basics" : `Edit ${sm ? sm.name : "SME"}`}
          subtitle={self
            ? "Contact details and your weekly preference. Skills, level and ratings are maintained by ops."
            : `${prof.id} · ${sm ? sm.topics.length : 0} topic(s) · rating ${sm && sm.rating ? sm.rating.toFixed(1) : "not rated yet"}. Topics and availability are edited from the calendar below.`}
          footerNote={emailOk && nameOk
            ? "Changes apply to future invites — already published classes keep their sent details."
            : "Fix the highlighted fields to save."}
          footer={[
            { label: "Cancel", onClick: () => { setSheet(null); setProf(null); } },
            ...(emailOk && nameOk ? [{ label: "Save changes", kind: "primary" as const, onClick: saveProfile }] : []),
          ]}
          onClose={() => { setSheet(null); setProf(null); }}
        >
          <div className="grid grid-cols-2 gap-[12px]">
            {field("SME ID", "System-issued, cannot be changed", <span style={READONLY}>{prof.id}</span>)}
            {field("Full name", nameOk ? "" : "A name is required",
              <input className="field w-full" value={prof.name} placeholder="e.g. Rahul Desai" onChange={set("name")} />)}
            {field("Email", emailOk ? "Class invites and the weekly schedule go here" : "That does not look like an email address",
              <input className="field w-full" value={prof.email} placeholder="name@interviewkickstart.com" onChange={set("email")} />, true)}
            {field("Phone", "Used for SMS reminders",
              <input className="field w-full" value={prof.phone} placeholder="+91 98230 60417" onChange={set("phone")} />)}
            {field("City", "Sets the time-zone note on invites",
              <input className="field w-full" value={prof.city} placeholder="e.g. Pune" onChange={set("city")} />)}
            {field("Level", self ? "Set by ops as you clear classes" : "Advanced unlocks advanced batches and mocks",
              self
                ? <span style={READONLY}>{prof.level}</span>
                : (
                  <select className="field w-full" value={prof.level} onChange={set("level")}>
                    {META.levels.map((l) => <option key={l} value={l}>{l}</option>)}
                  </select>
                ))}
            {field("Classes a week", "The scheduler treats this as a soft cap",
              <input className="field w-full" type="number" min={1} max={8} value={prof.preferred} onChange={set("preferred")} />)}
          </div>
        </Sheet>
      );
    }

    if (sheet.kind === "agent") {
      const recovery = agentReq.mode === "recovery";
      const who = rosterFor("next").find((x) => x.id === agentReq.smeId);
      const applicable = !!agentRes?.plan?.length;
      const close = () => { if (!agentBusy) { setSheet(null); setAgentRes(null); } };
      return (
        <Sheet
          width={640} eyebrow={`Copilot · ${WEEKS.next.label} · ${WEEKS.next.range}`}
          title={recovery ? `Cover for ${who?.name ?? "a teacher"}` : "Ask the copilot"}
          subtitle={recovery
            ? "Say who is out and when. The copilot searches replacements and swap chains, then simulates every move before proposing it."
            : "Ask about this week's draft — unfilled classes, workload, the least disruptive fix. Answers come from the engine, not from memory."}
          footerNote={agentRes
            ? applicable ? "Applying routes every move through the override path; Stage D re-validates the week." : "Nothing to apply."
            : "Nothing is changed until you apply a plan."}
          footer={[
            { label: "Dismiss", onClick: close, disabled: agentBusy },
            ...(agentRes ? [{ label: "Ask again", kind: "quiet" as const, onClick: () => setAgentRes(null), disabled: agentBusy }] : []),
            ...(applicable ? [{ label: agentBusy ? "Applying…" : `Apply plan · ${agentRes!.plan!.length}`, kind: "go" as const, onClick: () => void applyAgentPlan(), disabled: agentBusy }] : []),
          ]}
          onClose={close}
        >
          <AgentSheet
            req={agentReq} res={agentRes} busy={agentBusy} smes={rosterFor("next")} rows={runs.next?.draft ?? []} days={META.days}
            onReq={(patch) => setAgentReq((r) => ({ ...r, ...patch }))}
            onRun={() => void runAgent()}
          />
        </Sheet>
      );
    }

    if (sheet.kind === "studentEmail") {
      const ok = /^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(emailDraft.trim());
      const close = () => setSheet(null);
      return (
        <Sheet
          width={460} eyebrow="My profile" title="Edit my e-mail"
          subtitle="Schedule updates and calendar invites for your batch are sent here."
          footerNote={ok ? "Applies to future sends — already published classes keep their sent details." : "Enter a valid e-mail address to save."}
          footer={[
            { label: "Cancel", onClick: close },
            ...(ok ? [{ label: "Save", kind: "primary" as const, onClick: () => {
              setStudentEmail(emailDraft.trim()); close(); say("E-mail updated.");
            } }] : []),
          ]}
          onClose={close}
        >
          <label className="block">
            <span className="label-caps mb-[6px] block">E-mail</span>
            <input className="field w-full" type="email" autoFocus value={emailDraft} placeholder="name@example.com"
              onChange={(e) => setEmailDraft(e.target.value)} />
          </label>
        </Sheet>
      );
    }

    if (sheet.kind === "publish") {
      const chosen = leaves.filter((l) => pubSel[l.id]);
      const sending = leaves.some((l) => pubStatus[l.id] === "sending");
      const sent = leaves.filter((l) => pubStatus[l.id] === "sent");
      const settled = leaves.filter((l) => ["sent", "simulated", "skipped", "error"].includes(pubStatus[l.id]));
      const done = settled.length > 0 && !sending;
      const allOn = chosen.length === leaves.length;
      const reachSmes = new Set(pubRows.filter((r) => r.sme_id).map((r) => r.sme_id)).size;
      const reachLearners = batches.filter((b) => pubRows.some((r) => r.batch_id === b.id)).reduce((a, b) => a + b.learners, 0);
      const amending = !!amended[week];
      const off = pubRows.filter((r) => !isLive(r)).length;
      return (
        <Sheet
          width={580} eyebrow={`${WEEKS[week].label} · ${WEEKS[week].range}`}
          title={done ? (amending ? "Changes sent" : "Week published") : amending ? "Send this week's changes" : "Publish the week"}
          subtitle={done
            ? sendSummary(settled)
            : amending
              ? `Only the ${pubRows.length} class(es) that changed are re-sent${off ? `, including ${off} no longer running` : ""} — ${reachSmes} SMEs, ${reachLearners} students.`
              : `Pick who hears about it and how — ${reachSmes} SMEs, ${reachLearners} students.`}
          footerNote={done
            ? "SMEs and students can see the published week now."
            : sending ? "Delivering — do not close this window."
              : allOn ? "Everyone gets every channel." : `${chosen.length} of ${leaves.length} selected`}
          footer={done
            ? [{ label: "Done", kind: "go" as const, onClick: () => setSheet(null) }]
            : [
              { label: "Cancel", onClick: cancelPublish },
              ...(allOn || sending ? [] : [{
                label: "Select everything", kind: "quiet" as const,
                onClick: () => setPubSel(Object.fromEntries(leaves.map((l) => [l.id, true]))),
              }]),
              { label: sending ? "Sending…" : allOn ? "Send all" : `Send selected · ${chosen.length}`,
                kind: "go" as const, disabled: sending, onClick: publishWeek },
            ]}
          onClose={() => (sending ? undefined : setSheet(null))}
        >
          <PublishSheet
            leaves={leaves} selected={pubSel} status={pubStatus} locked={sending || done}
            onToggleLeaf={(id: LeafId) => setPubSel((s) => ({ ...s, [id]: !s[id] }))}
            onToggleChannel={(ids, on) => setPubSel((s) => ({ ...s, ...Object.fromEntries(ids.map((i) => [i, on])) }))}
          />
        </Sheet>
      );
    }

    if (sheet.kind === "newClass") {
      const bt = batches.find((b) => b.id === selBatch) ?? batches[0];
      const course = COURSES[bt.course];
      const level = META.levels.indexOf(bt.level) + 1;
      const pool = freeFor(newClass.topic || course.topics[0], newClass.day, newClass.hour, level);
      const clash = rows.filter((r) => r.batch_id === bt.id
        && istParts(r.start_utc).day === newClass.day && istParts(r.start_utc).hour === newClass.hour);
      const hours = Array.from({ length: META.hours[1] - META.hours[0] }, (_, i) => META.hours[0] + i);
      // a slot or topic change can invalidate the chosen teacher
      const set = <K extends keyof NewClass>(k: K, v: NewClass[K]) => setNewClass((n) => {
        const next = { ...n, [k]: v };
        if (k !== "smeId" && next.smeId
          && !freeFor(next.topic || course.topics[0], next.day, next.hour, level).some((s) => s.id === next.smeId)) {
          next.smeId = "";
        }
        return next;
      });
      return (
        <Sheet
          width={560} eyebrow={`${bt.id} · ${course.name}`} title="Add a class"
          subtitle={`Goes into ${WEEKS.next.label.toLowerCase()} (${WEEKS.next.range}). The teacher list only shows people who are actually free.`}
          banner={clash.length
            ? { text: `${bt.id} already has ${clash[0].sub_specialty ?? clash[0].type} at that hour — learners cannot attend both.`, tone: "red" }
            : pool.length ? null
              : { text: `Nobody who teaches ${newClass.topic} is free at ${META.days[newClass.day]} ${String(newClass.hour).padStart(2, "0")}:00. You can still add it and fill it from Work items.`, tone: "amber" }}
          footerNote={`${bt.id} runs ${bt.per_week} a week · ${rows.filter((r) => r.batch_id === bt.id).length} scheduled so far`}
          footer={[
            { label: "Cancel", onClick: () => setSheet(null) },
            ...(clash.length ? [] : [{
              label: newClass.smeId ? "Add class" : "Add unfilled class",
              kind: "go" as const, onClick: () => void addClass(),
            }]),
          ]}
          onClose={() => setSheet(null)}
        >
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="label-caps mb-[6px] block">Topic</span>
              <select className="field w-full" value={newClass.topic || course.topics[0]} onChange={(e) => set("topic", e.target.value)}>
                {course.topics.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="label-caps mb-[6px] block">Class type</span>
              <select className="field w-full" value={newClass.type} onChange={(e) => set("type", e.target.value as NewClass["type"])}>
                {Object.entries(META.type_label).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="label-caps mb-[6px] block">Day</span>
              <select className="field w-full" value={newClass.day} onChange={(e) => set("day", Number(e.target.value))}>
                {META.days.map((d, i) => <option key={d} value={i}>{d}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="label-caps mb-[6px] block">Time</span>
              <select className="field w-full" value={newClass.hour} onChange={(e) => set("hour", Number(e.target.value))}>
                {hours.map((h) => (
                  <option key={h} value={h}>{String(h).padStart(2, "0")}:00 – {String(h + 1).padStart(2, "0")}:00 IST</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="label-caps mb-[6px] block">Teacher — {pool.length} free</span>
              <select className="field w-full" value={newClass.smeId} onChange={(e) => set("smeId", e.target.value)}>
                <option value="">{pool.length ? "Leave unfilled for now" : "Nobody is free — leave unfilled"}</option>
                {pool.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} · {s.level} · ★ {s.rating.toFixed(1)}
                  </option>
                ))}
              </select>
            </label>
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
    const readOnly = role !== "coordinator";

    if (sheet.kind === "ghost") {
      const sm = rosterFor("next").find((s) => s.id === sheet.smeId)!;
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

    const roster = rosterFor(sheet.week);
    const dropped = !isLive(r);
    const stage = sheet.kind === "class" ? sheet.stage : "info";
    const cands = sheetCandidates(r, roster);
    const showList = !readOnly && !dropped && (stage === "pick" || !r.sme_id);
    const sme = roster.find((s) => s.id === r.sme_id);
    const mineCount = rows.filter((x) => x.sme_id === r.sme_id).length;
    // The recovery ladder: a class only offers a merge or a cancel once it actually needs rescuing —
    // no teacher, or the teacher ops just marked unavailable. Otherwise they are noise on every row.
    const needsRescue = !readOnly && !dropped && (!r.sme_id || (!!r.sme_id && !!unavailable[r.sme_id]));
    const hosts = needsRescue ? mergeCandidates(r, rows, batches) : [];

    // The cancel step is the class sheet with one question in it: a cancellation with no reason is a
    // notice to learners that says nothing, so the sheet will not let one through.
    if (sheet.kind === "cancelClass") {
      const still = cands.filter((c) => !c.blocked);
      return (
        <Sheet
          eyebrow={`${r.batch_id} · ${course?.name}`} title="Cancel this class"
          subtitle={`${META.days[p.day]} ${p.label} IST · ${batch?.learners ?? "—"} learners are told.`}
          banner={still.length
            ? { text: `${still[0].name} could still take this class. Cancelling is the last resort — assign them instead if you can.`, tone: "amber" }
            : { text: "Nobody else is eligible for this slot, and no other batch runs this topic at this hour.", tone: "red" }}
          footerNote={cancelReason.trim().length >= 4
            ? "The reason is sent to the learners with the cancellation."
            : "Say why — the learners are told this, not an error code."}
          footer={[
            { label: "Keep the class", onClick: () => { setCancelReason(""); setSheet({ kind: "class", sessionId: r.session_id, week: sheet.week, stage: "info" }); } },
            ...(cancelReason.trim().length >= 4
              ? [{ label: "Cancel the class", kind: "go" as const, onClick: () => void cancelClass(r, cancelReason.trim()) }]
              : []),
          ]}
          onClose={() => { setCancelReason(""); setSheet(null); }}
        >
          <label className="block">
            <span className="label-caps mb-[6px] block">Why is this class cancelled?</span>
            <textarea className="field w-full" rows={3} value={cancelReason} autoFocus
              placeholder="e.g. Ananya is unwell and nobody else carries Dynamic Programming at level 3."
              onChange={(e) => setCancelReason(e.target.value)} />
          </label>
        </Sheet>
      );
    }

    return (
      <Sheet
        eyebrow={`${r.batch_id} · ${course?.name}`}
        title={r.sub_specialty ?? META.type_label[r.type]}
        subtitle={`${META.days[p.day]} ${p.label} IST · 60 min · ${META.type_label[r.type]} · ${batch?.level ?? ""} batch`}
        // a learner sees what the class IS — never the scheduling internals
        facts={readOnly
          ? [
            { label: "Class type", value: META.type_label[r.type] },
            { label: "When", value: `${META.days[p.day]} ${p.label} IST` },
            { label: "Batch", value: `${r.batch_id} · ${course?.name}` },
            { label: "Topic", value: r.sub_specialty ?? META.type_label[r.type] },
          ]
          : [
            { label: "Batch", value: `${r.batch_id} · ${batch?.learners ?? "—"} learners` },
            {
              label: "Decided by",
              value: r.stage === "llm" ? `LLM tie-break${run?.stats.llm.model ? ` (${run.stats.llm.model})` : ""}`
                : r.stage === "override" ? "Ops override" : r.stage === "auto" ? "Automatic score" : "—",
            },
            {
              label: "Match score",
              value: r.score === null ? "—" : (
                <span title="The first number is what the assignment was decided on. The second re-scores the same teacher against the finished week, which is the scale the list below uses.">
                  {r.score.toFixed(2)}
                  {r.score_now !== null && Math.abs(r.score_now - r.score) >= 0.005 && (
                    <span className="font-normal" style={{ color: "var(--muted)" }}>
                      {" "}· {r.score_now.toFixed(2)} now
                    </span>
                  )}
                </span>
              ),
            },
            {
              label: "Status",
              value: r.cancelled ? "Cancelled" : r.merged_into ? `Merged into ${rows.find((x) => x.session_id === r.merged_into)?.batch_id ?? r.merged_into}`
                : !r.sme_id ? "Unfilled" : pend ? "Change pending SME approval"
                  : isApproved ? "Approved" : locked ? "Live" : "Draft — not yet approved",
            },
          ]}
        footerNote={readOnly
          ? (role === "sme"
            ? "Ops owns the schedule — ask them if you need this class covered."
            : r.sme_id ? "Your instructor for this session. Ops will let you know if anything changes." : "Your instructor is being assigned — you will be notified.")
          : locked
            ? "This week is live: a teacher change is sent to the SME for approval before learners see it."
            : "Next week is a system-generated draft — your changes apply immediately and feed the next run."}
        footer={[
          { label: "Close", onClick: () => setSheet(null) },
          ...(role === "sme" && r.sme_id === META.me ? [{ label: "Ask ops to reassign", kind: "go" as const, onClick: () => requestCover(r) }] : []),
          ...(!readOnly && dropped ? [{ label: "Put it back on the schedule", kind: "go" as const, onClick: () => void restoreClass(r) }] : []),
          ...(needsRescue ? [{ label: "Cancel this class", onClick: () => setSheet({ kind: "cancelClass", sessionId: r.session_id, week: sheet.week }) }] : []),
          ...(!readOnly && r.sme_id && !isApproved && !locked ? [{ label: "Approve this class", kind: "go" as const, onClick: () => approveOne(r) }] : []),
        ]}
        onClose={() => setSheet(null)}
      >
        {sme && (
          <div>
            <SectionLabel>Teacher assigned</SectionLabel>
            <PersonRow
              id={sme.id} name={sme.name}
              meta={readOnly
                ? `★ ${sme.rating.toFixed(1)} learner rating · ${sme.topics.slice(0, 2).join(", ")} · ${sme.city}`
                : `${sme.level} · ★ ${sme.rating.toFixed(1)} · ${mineCount} of ${sme.preferred} classes this week${leave[sme.id] ? ` · ${leave[sme.id]}` : ""}`}
              tone={stage === "pick" ? "active" : "plain"}
              right={readOnly ? undefined : (
                <span className="whitespace-nowrap text-[11.5px] font-semibold" style={{ color: "var(--brand-deep)" }}>
                  {stage === "pick" ? "Choosing…" : "Change teacher →"}
                </span>
              )}
              onClick={readOnly || dropped ? undefined : () => setSheet({ kind: "class", sessionId: r.session_id, week: sheet.week, stage: stage === "pick" ? "info" : "pick" })}
            />
          </div>
        )}
        {r.override_effect && !readOnly && (
          <div>
            <SectionLabel>Why the score moved</SectionLabel>
            <div className="rounded-[14px] p-[11px_13px] text-[12.5px] leading-[1.55]"
              style={{ background: "var(--brand-tint)", color: "var(--brand-deep)" }}>
              {r.override_effect.kind === "direct"
                ? `Your override on this class re-scored it — the pairing you replaced is −0.2 and the one you chose is +0.1 on the next draft.`
                : `Changed because your override moved ${r.override_effect.smes.join(" and ")}'s load. Fairness is normalised across the whole ${COURSES[r.subject]?.name ?? r.subject} pool, so every ${r.subject} class re-scores when a ${r.subject} teacher's week changes.`}
            </div>
          </div>
        )}
        {!!r.flags.length && !readOnly && (
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
        {needsRescue && !!hosts.length && (
          <div>
            <SectionLabel>Or merge it into another batch — one class, both cohorts</SectionLabel>
            <div className="flex flex-col gap-[7px]">
              {hosts.slice(0, 4).map((h) => (
                <PersonRow
                  key={h.row.session_id} id={h.row.batch_id} name={`${h.row.batch_id} · ${h.row.sub_specialty ?? META.type_label[h.row.type]}`}
                  meta={`${META.days[istParts(h.row.start_utc).day]} ${istParts(h.row.start_utc).label} IST · ${h.learners} learners together · ${
                    h.row.sme_name ?? "no teacher yet"}`}
                  right={
                    <span className="flex items-center gap-2">
                      {!h.sameHour && (
                        <span className="whitespace-nowrap rounded-[8px] px-2 py-[3px] text-[10px] font-semibold"
                          style={{ background: "var(--amber-tint)", color: "var(--amber-ink)" }}>
                          different hour
                        </span>
                      )}
                      {h.hostUnstaffed && (
                        <span className="whitespace-nowrap rounded-[8px] px-2 py-[3px] text-[10px] font-semibold"
                          style={{ background: "var(--red-tint)", color: "var(--red-ink)" }}>
                          no teacher either
                        </span>
                      )}
                      <span className="whitespace-nowrap text-[11.5px] font-semibold" style={{ color: "var(--brand-deep)" }}>
                        Merge →
                      </span>
                    </span>
                  }
                  onClick={() => void mergeClass(r, h.row)}
                />
              ))}
            </div>
            <div className="mt-2 text-[11.5px] leading-[1.5]" style={{ color: "var(--muted)" }}>
              {r.batch_id} keeps its own schedule everywhere else — this folds one hour only, and the
              surviving class is re-staffed for the higher of the two required levels.
            </div>
          </div>
        )}
        {showList && (
          <div>
            <SectionLabel>
              {r.sme_id ? "Choose a different teacher — score if reassigned" : "Teachers who could take this class"}
            </SectionLabel>
            <div className="flex flex-col gap-[7px]">
              {cands.map((c) => {
                const level = roster.find((s) => s.id === c.sme_id)?.level ?? "";
                return (
                  <PersonRow
                    key={c.sme_id} id={c.sme_id} name={c.name}
                    meta={c.score !== null ? `match ${c.score.toFixed(2)} · ${level}` : level}
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
                        <span className="whitespace-nowrap text-[11.5px] font-semibold" style={{ color: "var(--brand-deep)" }}>
                          {locked ? "Request →" : "Assign →"}
                        </span>
                      </span>
                    }
                    onClick={() => assign(r, c.sme_id, c.name, c.blocked)}
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

  const showKpis = vh >= 760;

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
          {showKpis && (
            <KpiCards
              rows={rows} smes={smes} batches={batches} approved={approved}
              leaveCount={Object.keys(leave).length} workCount={work.length}
              unfilled={unfilledCount} conflicts={conflictCount}
              advisory={work.length - work.filter((w) => w.blocking).length}
              fairnessNote={fairnessNote}
              onBatches={() => { setMod("batches"); setSheet(null); }}
              onSmes={() => { setMod("smes"); setSheet(null); }}
              onShowAll={() => { setTab("schedule"); setStatusOff({}); setBatchFilter("all"); }}
              onWork={() => setSheet({ kind: "work" })}
              overrideRate={overrideRate}
              onOverrides={() => { setTab("overrides"); setSheet(null); }}
            />
          )}
          <Dashboard
            rows={filtered} allRows={rows} batches={batches} courses={COURSES} meta={META} weeks={WEEKS}
            week={week} weekDates={weekDates} tab={tab} approved={approved} changed={changed}
            batchFilter={batchFilter} statusOff={statusOff} workCount={work.length} published={isPublished}
            amended={!!amended[week]} changedCount={changed.size} onRepublish={republishChanges}
            overrides={overrides} loading={loading} vh={vh} smeName={smeName}
            onTab={setTab} onBatchFilter={setBatchFilter}
            onStatusToggle={(k: Category) => setStatusOff((s) => ({ ...s, [k]: !s[k] }))}
            onOpenWork={() => setSheet({ kind: "work" })}
            onAskCopilot={() => openAgent({ mode: "review", question: "" })}
            onApproveWeek={approveWeek}
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
          approved={approved} selected={selSme} leave={leave} query={smeQuery} filter={smeFilter} vh={vh}
          workload={workloadRows}
          onQuery={setSmeQuery} onFilter={setSmeFilter} onSelect={setSelSme} onOpen={openClass}
          onGhost={(sessionId) => setSheet({ kind: "ghost", sessionId, week, smeId: selSme })}
          onEditSme={openProfile}
          onReportOut={(id) => openAgent({ mode: "recovery", smeId: id, days: [] })}
          unavailable={unavailable} onToggleUnavailable={(id) => void toggleUnavailable(id)}
          onSyncAvailability={() => void runAvailabilitySync()}
          syncBusy={syncBusy}
          busyBlocks={busyBlocks}
          syncDetail={syncDetail ?? integrations?.channels.freebusy?.detail}
          syncLive={integrations?.channels.freebusy?.live}
          onImportSmes={() => { setSmeImp(emptyImport()); setSheet({ kind: "smeImport" }); }}
        />
      );
    }
    if (mod === "batches") {
      return (
        <BatchManagement
          batches={batches} rows={rows} courses={COURSES} meta={META} weeks={WEEKS} week={week}
          weekDates={weekDates} approved={approved} selected={selBatch} vh={vh}
          onSelect={setSelBatch} onOpen={openClass} onNewBatch={() => setSheet({ kind: "newBatch" })} onNewClass={openNewClass}
          onImport={() => { setImp(emptyImport()); setSheet({ kind: "import" }); }}
        />
      );
    }
    return (
      <MyWeek
        role={role === "student" ? "student" : "sme"} me={meNow} myBatch={batches.find((b) => b.id === META.my_batch)}
        rows={rows} smes={rosterFor("next")} courses={COURSES} meta={META} weeks={WEEKS} week={week} weekDates={weekDates}
        approved={approved} availOff={availOff} preferred={preferred} vh={vh}
        onAvail={(k) => void toggleAvail(k)}
        onPreferred={setPreferred} onOpen={openClass}
        leave={leave[META.me] ?? null}
        onToggleLeave={() => setLeave((l) => {
          const n = { ...l };
          if (n[META.me]) { delete n[META.me]; say("Leave request withdrawn."); }
          else { n[META.me] = "Leave requested for next week (7–12 Sep)"; say("Leave requested for next week — ops notified."); }
          return n;
        })}
        onEditProfile={() => openProfile(META.me)}
        email={role === "student" ? studentEmail : (meNow.email ?? "")}
        onEditEmail={() => {
          if (role === "student") { setEmailDraft(studentEmail); setSheet({ kind: "studentEmail" }); }
          else openProfile(META.me);   // the SME sheet already carries the e-mail field
        }}
      />
    );
  };

  return (
    <div className="flex h-screen items-stretch overflow-hidden" style={{ background: "var(--page)" }}>
      <Sidebar
        role={role} mod={mod}
        onRole={(r) => { setRole(r); setMod(ROLE_MODULES[r][0]); setSheet(null); }}
        onMod={(m) => { setMod(m); setSheet(null); }}
      />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden" style={{ marginLeft: 74 }}>
        <div
          className="flex shrink-0 flex-wrap items-end gap-[14px] p-[18px_26px_14px]"
          style={{
            background: "rgba(245,247,250,0.8)", backdropFilter: "blur(22px) saturate(180%)",
            WebkitBackdropFilter: "blur(22px) saturate(180%)", borderBottom: "0.5px solid rgba(16,26,51,0.06)",
          }}
        >
          <div>
            <h1 className="m-0 text-[22px] font-bold" style={{ letterSpacing: "-0.02em" }}>{MODULES[mod].label}</h1>
            <div className="mt-[3px] text-[12.5px]" style={{ color: "var(--muted)" }}>{MODULES[mod].sub}</div>
            {role === "coordinator" && (
              <div className="mt-[3px] text-[11.5px]" style={{ color: "var(--muted-2)" }} title="Where each dataset in front of you came from">
                {DATASET_LABELS.map(([key, label]) => {
                  const o = provenance[key];
                  return `${label}: ${o ? `${o.source}, synced ${ago(o.at)}` : "seed data"}`;
                }).join(" · ")}
                {Object.keys(provenance).length > 0 && (
                  <button className="btn-quiet ml-[8px] text-[11px]" onClick={() => void resetToSeed()}>
                    Reset to seed data
                  </button>
                )}
              </div>
            )}
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-[14px]">
            <div className="flex items-center gap-[10px]">
              <div className="tabs tabs-solid">
                {(["current", "next"] as WeekKey[]).map((k) => (
                  <button key={k} onClick={() => setWeek(k)} className={`tab ${week === k ? "tab-on" : "tab-off"}`}>
                    {WEEKS[k].label}
                  </button>
                ))}
              </div>
              <span className="whitespace-nowrap text-[12px]" style={{ color: "var(--muted)" }}>{WEEKS[week].range}</span>
              {WEEKS[week].locked ? (
                <span className="rounded-[9px] px-[10px] py-[5px] text-[11.5px] font-semibold"
                  title={amended[week]
                    ? "This week is running, and it has changed since it was published — the classes that moved need re-sending."
                    : "This week is running: only a drop-out recovery changes it."}
                  style={amended[week]
                    ? { background: "var(--amber-tint)", color: "var(--amber-ink)" }
                    : { background: "var(--brand-tint)", color: "var(--brand-deep)" }}>
                  {amended[week] ? "Live week · amended" : "Live week"}
                </span>
              ) : isPublished ? (
                <span className="rounded-[9px] px-[10px] py-[5px] text-[11.5px] font-semibold" style={{ background: "var(--green-tint)", color: "var(--green-ink)" }}>
                  ✓ Approved
                </span>
              ) : null}
            </div>
            {role === "coordinator" && (
              <span className="flex items-center gap-[9px]">
                <button className="btn" onClick={() => void exportCsv()} disabled={loading || !run}>Export CSV</button>
                <button
                  className="btn" onClick={() => void pushToSheet()} disabled={loading || !run || sheetBusy}
                  title={integrations?.sheets.live
                    ? `Writes the week into the Google Sheet — ${integrations.sheets.detail}`
                    : `Simulated — ${integrations?.sheets.detail ?? "Google Sheets not configured"}`}
                >
                  {sheetBusy ? "Pushing…" : `Push to Sheet${integrations?.sheets.live ? "" : " (simulated)"}`}
                </button>
              </span>
            )}
          </div>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-[14px] overflow-auto p-[14px_26px_18px]">
          {showModule()}
        </div>
      </main>
      {renderSheet()}
      {role === "coordinator" && !!runs.next && (
        <CopilotChat
          open={chatOpen} turns={chatTurns} draft={chatDraft} busy={chatBusy}
          smes={rosterFor("next")} rows={runs.next.draft} days={META.days}
          onOpen={setChatOpen} onDraft={setChatDraft} onSend={() => void sendChat()}
          onApply={(i) => void applyChatPlan(i)}
          onReset={() => { setChatTurns([]); setChatDraft(""); }}
        />
      )}
      {saveFailed && (
        <div role="alert" className="fixed bottom-[70px] left-1/2 z-90 rounded-[12px] px-4 py-2 text-[12.5px]"
          style={{ transform: "translateX(-50%)", background: "var(--amber-tint)", color: "var(--amber-ink)", fontWeight: 550 }}>
          {saveFailed}
        </div>
      )}
      <Toast text={toast} />
    </div>
  );
}

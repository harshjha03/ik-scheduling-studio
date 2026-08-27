/** Pure view helpers shared by the dashboard components. No React, no fetching. */
import type {
  AvailabilityWindow, Batch, Candidate, Category, Course, DraftRow, Fix, Flag, FlagCode, HistoryRecord, LeafId,
  PublishLeaf, Session, SME, Severity, WorkItem,
} from "./types";

export const IST = "Asia/Kolkata";

const dayFmt = new Intl.DateTimeFormat("en-GB", { timeZone: IST, weekday: "short" });
const hourFmt = new Intl.DateTimeFormat("en-GB", { timeZone: IST, hour: "2-digit", hour12: false });
const dateFmt = new Intl.DateTimeFormat("en-GB", { timeZone: IST, day: "numeric", month: "short" });

export const DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** IST weekday index (Mon=0) and start hour for a UTC timestamp. */
export function istParts(utc: string): { day: number; hour: number; label: string; date: string } {
  const d = new Date(utc);
  const day = DAY_ORDER.indexOf(dayFmt.format(d));
  const hour = parseInt(hourFmt.format(d), 10);
  return { day, hour, label: `${String(hour).padStart(2, "0")}:00`, date: dateFmt.format(d) };
}

export const hhmm = (h: number) => `${String(h).padStart(2, "0")}:00`;

export function initials(name: string): string {
  return name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();
}

const AVATARS = ["#e8eff9", "#e9eff8", "#e6f2ec", "#fbf0e6", "#eef3fa"];
export function avatarBg(id: string): string {
  return AVATARS[(id.charCodeAt(id.length - 1) + id.length) % AVATARS.length];
}

// ---- flags ----

export const FLAG_LABEL: Record<FlagCode, string> = {
  UNFILLED: "UNFILLED",
  HARD_CONFLICT: "CONFLICT",
  RULE_OVERRIDE_RISK: "OVERRIDE RISK",
  FAIRNESS_VIOLATION: "FAIRNESS",
  TIE_ESCALATED: "TIE",
  LLM_FALLBACK: "FALLBACK",
};

export const SEV_CHIP: Record<Severity, string> = {
  critical: "chip-critical",
  high: "chip-high",
  medium: "chip-medium",
  info: "chip-info",
};

const SEV_RANK: Record<Severity, number> = { critical: 0, high: 1, medium: 2, info: 3 };

export function topFlag(row: DraftRow): Flag | null {
  if (!row.flags.length) return null;
  return row.flags.reduce((a, f) => (SEV_RANK[f.severity] < SEV_RANK[a.severity] ? f : a));
}

/** Visual bucket used by the calendar and the status filter. */
export function category(row: DraftRow, approved: boolean): Category {
  if (!row.sme_id || row.flags.some((f) => f.code === "HARD_CONFLICT")) return "red";
  if (row.flags.some((f) => f.severity === "high" || f.severity === "medium")) return "amber";
  if (approved) return "approved";
  return row.flags.length ? "amber" : "staffed";
}

export const CATEGORIES: { key: Category; label: string; hint: string; dot: string; swatch: string }[] = [
  { key: "red", label: "Unfilled / conflict", hint: "No eligible teacher, or a double booking", dot: "#c0392b",
    swatch: "background:#fbebe8;border:1px solid #f0c7c0;border-left:3px solid #c0392b" },
  { key: "amber", label: "Workload / tie-break", hint: "Outside the fairness band, or an LLM broke a near-tie", dot: "#d18b3c",
    swatch: "background:#fbf3e3;border:1px solid #eddba6;border-left:3px solid #d18b3c" },
  { key: "approved", label: "Approved", hint: "Signed off and ready to publish", dot: "#0f7a52",
    swatch: "background:#e6f2ec;border:1px solid #c2e2ce;border-left:3px solid #0f7a52" },
  { key: "staffed", label: "Staffed · no flags", hint: "Teacher assigned and inside every rule", dot: "#2f5fd0",
    swatch: "background:#fff;border:1px solid #e2e8f1;border-left:3px solid #2f5fd0" },
];

// ---- availability ----

const toMin = (s: string) => Number(s.slice(0, 2)) * 60 + Number(s.slice(3, 5));

/** Is the SME free for the IST day/hour in the given week? Mirrors engine.stages.is_available. */
export function isAvailable(sme: SME, weekStartUtcSession: string, day: number, hour: number): boolean {
  // Build the UTC instant for that IST slot in the same week as the reference session.
  const ref = new Date(weekStartUtcSession);
  const refParts = istParts(weekStartUtcSession);
  const shiftDays = day - refParts.day;
  const shiftHours = hour - refParts.hour;
  const at = new Date(ref.getTime() + shiftDays * 864e5 + shiftHours * 36e5);
  const end = new Date(at.getTime() + 36e5);
  const wd = DAY_ORDER[(at.getUTCDay() + 6) % 7];
  const s = at.getUTCHours() * 60 + at.getUTCMinutes();
  const e = s + 60;
  if (end.getUTCDate() !== at.getUTCDate() && e > 1440) return false;
  return sme.weekly_availability.some((w) => w.weekday === wd && toMin(w.start_utc) <= s && e <= toMin(w.end_utc));
}

/** IST hour blocks the SME view offers as availability toggles: label, hint, from, to. */
export const AVAIL_BLOCKS: [string, string, number, number][] = [
  ["Morning", "08–12", 8, 12],
  ["Afternoon", "12–16", 12, 16],
  ["Evening", "16–20", 16, 20],
];

const IST_OFFSET_MIN = 330;
const hhmmOf = (min: number) => `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;

/**
 * Cut the IST day/block ranges an SME switched off out of their UTC availability windows, so the
 * next draft really respects the change. The blocks live inside 08:00–20:00 IST = 02:30–14:30 UTC
 * on the same calendar day, so no window wraps midnight here. Keys are `${dayIndex}-${blockIndex}`.
 */
export function applyAvailabilityBlocks(sme: SME, off: Record<string, boolean>): SME {
  const cuts = Object.keys(off).filter((k) => off[k]).map((k) => {
    const [d, b] = k.split("-").map(Number);
    const [, , from, to] = AVAIL_BLOCKS[b];
    return { weekday: DAY_ORDER[d], start: from * 60 - IST_OFFSET_MIN, end: to * 60 - IST_OFFSET_MIN };
  });
  if (!cuts.length) return sme;
  let windows = sme.weekly_availability;
  for (const c of cuts) {
    windows = windows.flatMap((w): AvailabilityWindow[] => {
      if (w.weekday !== c.weekday) return [w];
      const s = toMin(w.start_utc);
      const e = toMin(w.end_utc);
      if (c.end <= s || c.start >= e) return [w];
      const kept: AvailabilityWindow[] = [];
      if (s < c.start) kept.push({ ...w, end_utc: hhmmOf(c.start) });
      if (c.end < e) kept.push({ ...w, start_utc: hhmmOf(c.end) });
      return kept;
    });
  }
  return { ...sme, weekly_availability: windows };
}

// ---- candidates for the class sheet ----

export interface SheetCandidate {
  sme_id: string;
  name: string;
  score: number | null;
  warn: string | null;
  blocked: boolean;
}

const RULE_WARN = (rule: string): string => {
  if (rule.startsWith("overlap:")) return `busy with ${rule.split(":")[1]}`;
  if (rule === "training_level") return "below batch level";
  if (rule === "availability") return "outside working hours";
  if (rule === "sub_specialty") return "does not carry this topic";
  return "different course";
};

/** Eligible SMEs first (with scores), then the rule-blocked ones, each with a reason. */
export function sheetCandidates(row: DraftRow, smes: SME[]): SheetCandidate[] {
  const eligible: SheetCandidate[] = row.candidates
    .filter((c) => c.sme_id !== row.sme_id)
    .map((c: Candidate) => ({
      sme_id: c.sme_id, name: c.name, score: c.score,
      warn: c.breaches_fairness ? "above fairness band" : null, blocked: false,
    }));
  const shown = new Set(eligible.map((c) => c.sme_id));
  const blocked: SheetCandidate[] = row.eliminated
    .filter((e) => e.rule !== "subject" && e.sme_id !== row.sme_id && !shown.has(e.sme_id))
    .filter((e) => {
      const s = smes.find((x) => x.id === e.sme_id);
      return !!s && s.subjects.includes(row.subject);
    })
    .map((e) => ({ sme_id: e.sme_id, name: e.name, score: null, warn: RULE_WARN(e.rule), blocked: true }));
  return [...eligible, ...blocked];
}

// ---- work items ----

export function workItems(rows: DraftRow[], smes: SME[], dismissed: Set<string>, leave: Record<string, string>): WorkItem[] {
  const out: WorkItem[] = [];
  const whenOf = (r: DraftRow) => { const p = istParts(r.start_utc); return `${DAY_ORDER[p.day]} ${p.label}`; };
  const add = (r: DraftRow, code: FlagCode, severity: Severity, title: string) => {
    const flag = r.flags.find((f) => f.code === code);
    if (!flag || dismissed.has(`${r.session_id}:${code}`)) return;
    out.push({ key: `${r.session_id}:${code}`, code, severity, title, detail: flag.reason,
      session_id: r.session_id, blocking: severity === "critical", when: whenOf(r) });
  };
  rows.forEach((r) => add(r, "UNFILLED", "critical", `${r.batch_id} · ${r.sub_specialty ?? r.type} has no teacher`));
  rows.forEach((r) => add(r, "HARD_CONFLICT", "critical", `${r.batch_id} · double booking`));
  rows.forEach((r) => add(r, "RULE_OVERRIDE_RISK", "high", `${r.batch_id} · override breaks a rule`));
  rows.forEach((r) => add(r, "FAIRNESS_VIOLATION", "medium", `${r.batch_id} · workload out of band`));
  Object.entries(leave).forEach(([smeId, text]) => {
    const s = smes.find((x) => x.id === smeId);
    if (!s || dismissed.has(`leave:${smeId}`)) return;
    const clash = rows.filter((r) => r.sme_id === smeId);
    out.push({
      key: `leave:${smeId}`, code: "LEAVE", severity: "medium", sme_id: smeId, blocking: false, when: "this week",
      title: `${s.name} is on leave next week`,
      detail: `${text}. ${clash.length
        ? `${clash.length} class(es) are still assigned to them — reassign or move.`
        : "No classes are assigned to them, nothing to do."}`,
      session_id: clash[0]?.session_id ?? null,
    });
  });
  return out;
}

// ---- ops assist: propose a fix, never apply one silently ----

/**
 * The best stand-in for a row, chosen only from `candidates` — the list the engine already
 * rule-checked — so an assist proposal can never break a hard rule. Prefers the lightest week,
 * then the better match, and skips anyone the fairness band would push out.
 */
export function bestCandidate(
  row: DraftRow, rows: DraftRow[], smes: SME[], opts: { exclude?: string | null; allowFairnessBreach?: boolean } = {},
): Candidate | null {
  const count = (id: string) => rows.filter((r) => r.sme_id === id).length;
  const byId = new Map(smes.map((s) => [s.id, s]));
  return row.candidates
    .filter((c) => c.sme_id !== row.sme_id && c.sme_id !== opts.exclude)
    .filter((c) => opts.allowFairnessBreach || !c.breaches_fairness)
    .filter((c) => { const s = byId.get(c.sme_id); return !!s && count(c.sme_id) < s.preferred; })
    .sort((a, b) => count(a.sme_id) - count(b.sme_id) || b.score - a.score)[0] ?? null;
}

export function autoFix(item: WorkItem, rows: DraftRow[], smes: SME[]): Fix | null {
  const row = rows.find((r) => r.session_id === item.session_id) ?? null;
  const byId = new Map(smes.map((s) => [s.id, s]));
  if (!row) {
    return item.code === "LEAVE"
      ? { label: "Dismiss — nothing to do", why: "No classes are assigned in that window, so there is nothing to move.",
          chips: [["No classes in that window", "good"]], action: { kind: "dismiss", key: item.key } }
      : null;
  }
  const pick = bestCandidate(row, rows, smes, {
    exclude: item.code === "HARD_CONFLICT" || item.code === "LEAVE" ? row.sme_id : null,
  });
  if (pick) {
    const s = byId.get(pick.sme_id)!;
    const mine = rows.filter((r) => r.sme_id === pick.sme_id).length;
    const p = istParts(row.start_utc);
    return {
      label: `Assign ${pick.name}`,
      who: { id: pick.sme_id, name: pick.name },
      why: `${pick.name} carries ${row.sub_specialty ?? row.type}, is free at that hour and stays inside the fairness band.`,
      chips: [
        [`Free ${DAY_ORDER[p.day]} ${p.label}`, "good"],
        [`${mine} of ${s.preferred} this week`, mine < s.preferred ? "good" : "warn"],
        [`★ ${s.rating.toFixed(1)} · ${s.level}`, "neutral"],
        [`Match ${pick.score.toFixed(2)}`, "neutral"],
      ],
      action: { kind: "assign", sessionId: row.session_id, smeId: pick.sme_id, smeName: pick.name },
    };
  }
  if (item.code === "FAIRNESS_VIOLATION") {
    return { label: "Accept for this week", why: "Nobody inside the fairness band carries this topic at that hour — the band evens out over the next run.",
      chips: [["No in-band teacher free", "warn"], ["Evens out next run", "neutral"]],
      action: { kind: "accept", sessionId: row.session_id, code: "FAIRNESS_VIOLATION" } };
  }
  if (item.code === "HARD_CONFLICT") {
    return { label: "Accept the clash this week", why: "Nobody else carries this topic at that hour. Accepting keeps the teacher on both and logs it for the next run.",
      chips: [["Nobody else free", "warn"], ["Logged as an override", "neutral"]],
      action: { kind: "accept", sessionId: row.session_id, code: "HARD_CONFLICT" } };
  }
  if (item.code === "LEAVE") {
    return { label: "Dismiss — handled elsewhere", why: "No in-band teacher is free for these classes; move them from the calendar or accept the clash.",
      chips: [["No in-band teacher free", "warn"]], action: { kind: "dismiss", key: item.key } };
  }
  return null;   // UNFILLED with no eligible teacher is a judgement call, by design
}

// ---- publish ----

export const PUB_CHANNELS: PublishLeaf["channel"][] = [
  { key: "cal", short: "Google Calendar", title: "Update Google Calendar", sub: "Creates or updates each class as an event, with topic, room link and teacher" },
  { key: "email", short: "e-mail", title: "Update via e-mail", sub: "One digest of the published week, sent per person" },
  { key: "sms", short: "SMS", title: "Update via SMS", sub: "Day-before reminder — topic, time and who is teaching" },
];

export function publishLeaves(rows: DraftRow[], batches: Batch[]): PublishLeaf[] {
  const live = rows.filter((r) => r.sme_id);
  const bs = batches.filter((b) => rows.some((r) => r.batch_id === b.id));
  const aud: PublishLeaf["audience"][] = [
    { key: "sme", label: "SMEs", count: `${new Set(live.map((r) => r.sme_id)).size} teaching this week` },
    { key: "stu", label: "Students", count: `${bs.reduce((a, b) => a + b.learners, 0)} across ${bs.length} batches` },
  ];
  return PUB_CHANNELS.flatMap((channel) => aud.map((audience) => ({ id: `${channel.key}:${audience.key}` as LeafId, channel, audience })));
}

export function sendSummary(leaves: PublishLeaf[]): string {
  const by = new Map<string, string[]>();
  leaves.forEach((l) => by.set(l.channel.short, [...(by.get(l.channel.short) ?? []), l.audience.label]));
  return [...by].map(([c, a]) => `${c} to ${a.join(" + ")}`).join(" · ");
}

// ---- SME management filters ----

export type SmeFilter = "all" | "fits" | "free" | "over" | "leave";

/** Open classes this SME could actually take: carries the topic, free, not already booked then. */
export function fitsFor(sme: SME, rows: DraftRow[]): DraftRow[] {
  const ref = rows[0]?.start_utc;
  if (!ref) return [];
  const busy = new Set(rows.filter((r) => r.sme_id === sme.id).map((r) => { const p = istParts(r.start_utc); return `${p.day}-${p.hour}`; }));
  return rows.filter((r) => !r.sme_id)
    .filter((r) => sme.subjects.includes(r.subject))
    .filter((r) => !r.sub_specialty || sme.topics.includes(r.sub_specialty))
    .filter((r) => sme.training_level >= r.required_training_level)
    .filter((r) => { const p = istParts(r.start_utc); return isAvailable(sme, ref, p.day, p.hour) && !busy.has(`${p.day}-${p.hour}`); });
}

export function smeMatches(sme: SME, rows: DraftRow[], query: string, filter: SmeFilter, leave: Record<string, string>): boolean {
  const q = query.trim().toLowerCase();
  if (q && !(sme.name.toLowerCase().includes(q) || sme.city.toLowerCase().includes(q)
    || sme.topics.join(" ").toLowerCase().includes(q) || sme.id.toLowerCase().includes(q))) return false;
  const assigned = rows.filter((r) => r.sme_id === sme.id).length;
  if (filter === "fits") return fitsFor(sme, rows).length > 0;
  if (filter === "free") return assigned < sme.preferred && !leave[sme.id];
  if (filter === "over") return assigned > sme.preferred;
  if (filter === "leave") return !!leave[sme.id];
  return true;
}

// ---- KPI numbers ----

export function kpis(rows: DraftRow[], smes: SME[], batches: Batch[], approved: Set<string>) {
  const assigned = rows.filter((r) => r.sme_id);
  const unfilled = rows.length - assigned.length;
  const conflicts = rows.filter((r) => r.flags.some((f) => f.code === "HARD_CONFLICT")).length;
  const workload = rows.filter((r) => r.flags.some((f) => f.code === "FAIRNESS_VIOLATION")).length;
  return {
    batches: batches.length,
    learners: batches.reduce((a, b) => a + b.learners, 0),
    courses: new Set(batches.map((b) => b.course)).size,
    activeTeachers: new Set(assigned.map((r) => r.sme_id)).size,
    totalTeachers: smes.length,
    classes: rows.length,
    byType: {
      class: rows.filter((r) => r.type === "class").length,
      doubt: rows.filter((r) => r.type === "doubt").length,
      mock: rows.filter((r) => r.type === "mock").length,
    },
    unfilled,
    conflicts,
    workload,
    attention: unfilled + conflicts + workload,
    approvedCount: rows.filter((r) => approved.has(r.session_id)).length,
  };
}

// ---- SME table ----

export function smeWeekStats(sme: SME, rows: DraftRow[]) {
  const assigned = rows.filter((r) => r.sme_id === sme.id).length;
  const load4w = sme.history.slice(-3).reduce((a, w) => a + w.sessions_taught, 0) + assigned;
  return { assigned, load4w, over: assigned > sme.preferred };
}

export function poolMean(smes: SME[], subject: string, rows: DraftRow[]): number {
  const pool = smes.filter((s) => s.subjects.includes(subject));
  if (!pool.length) return 0;
  const loads = pool.map((s) => smeWeekStats(s, rows).load4w);
  return loads.reduce((a, b) => a + b, 0) / loads.length;
}

// ---- new batch ----

export function nextBatchId(batches: Batch[], course: string): string {
  const n = batches.filter((b) => b.course === course).length + 1;
  return `${course}-${String(n).padStart(2, "0")}`;
}

/** Draft sessions for a newly created batch, spread over the week's free cells. */
export function newBatchSessions(
  batch: Batch, course: Course, existing: Session[], levels: string[], hours: [number, number],
): Session[] {
  const ref = existing[0]?.start_utc ?? new Date().toISOString();
  const refParts = istParts(ref);
  const level = levels.indexOf(batch.level) + 1;
  const busy = new Set(existing.map((s) => { const p = istParts(s.start_utc); return `${p.day}-${p.hour}`; }));
  const out: Session[] = [];
  for (let k = 0; k < batch.per_week; k++) {
    let day = 0, hour = hours[0];
    for (let i = 0; i < 72; i++) {
      const d = (k * 2 + Math.floor(i / 12)) % 6;
      const h = hours[0] + ((k * 3 + i) % (hours[1] - hours[0]));
      if (!busy.has(`${d}-${h}`) || i === 71) { day = d; hour = h; break; }
    }
    busy.add(`${day}-${hour}`);
    const at = new Date(new Date(ref).getTime() + (day - refParts.day) * 864e5 + (hour - refParts.hour) * 36e5);
    const typ = k === batch.per_week - 1 ? "mock" : k === batch.per_week - 2 ? "doubt" : "class";
    out.push({
      id: `${batch.id}-${k}`, batch_id: batch.id, subject: batch.course,
      sub_specialty: typ === "class" ? course.topics[k % course.topics.length] : null,
      type: typ as Session["type"], start_utc: at.toISOString().replace(/\.\d+Z$/, "Z"),
      duration_min: 60, mode: "online", required_training_level: level,
    });
  }
  return out;
}

/** Turn a settled week's draft into history records so the next week is scored on top of it. */
export function weekAsHistory(rows: DraftRow[], smes: SME[], week: string, existing: HistoryRecord[]): HistoryRecord[] {
  const byId = new Map(smes.map((s) => [s.id, s]));
  const counts = new Map<string, number>();
  const batches = new Map<string, Set<string>>();
  rows.forEach((r) => {
    if (!r.sme_id) return;
    counts.set(r.sme_id, (counts.get(r.sme_id) ?? 0) + 1);
    if (!batches.has(r.sme_id)) batches.set(r.sme_id, new Set());
    batches.get(r.sme_id)!.add(r.batch_id);
  });
  const added: HistoryRecord[] = smes.map((s) => ({
    sme_id: s.id,
    week,
    sessions_taught: counts.get(s.id) ?? 0,
    batches: [...(batches.get(s.id) ?? [])].sort(),
    per_topic_rating: byId.get(s.id)?.history.at(-1)?.per_topic_rating ?? {},
    post_session_rating: null,
  }));
  return [...existing, ...added];
}

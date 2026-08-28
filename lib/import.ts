/**
 * The Excel round-trip: template out, populated sheet back in.
 *
 * Everything here is pure — text in, rows and issues out — so the sheets can show exactly what
 * would be created before anything is. Nothing is imported until the coordinator has seen the check.
 * Ops keep their tracker in Excel; CSV is the honest lowest common denominator (`.xlsx` is a zip of
 * XML and is refused with an instruction rather than parsed).
 */
import type { AvailabilityWindow, Batch, Course, LevelName, SessionType, SME } from "./types";

export interface ImportIssue { line: string; msg: string }

export interface ImportedClass {
  batch: string; course: string; level: LevelName; learners: number;
  topic: string; type: SessionType; day: number; hour: number;
  smeId: string | null; smeName: string | null;
}

export interface ImportedSme {
  id: string; name: string; email: string; phone: string; city: string;
  courses: string[]; topics: string[]; level: LevelName; preferred: number;
  avail: [number, number, number][];      // [day, fromHourIst, toHourIst]
}

/** One past week for one SME — what Stage B's fairness and performance terms are computed from. */
export interface ImportedHistory {
  smeId: string; week: string; sessionsTaught: number;
  batches: string[]; ratings: Record<string, number>;
}

export interface ImportResult<T> { name: string; rows: T[]; errors: ImportIssue[]; parsed: boolean }

export const emptyImport = <T>(): ImportResult<T> => ({ name: "", rows: [], errors: [], parsed: false });

const EMAIL = /^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i;
const IST_OFFSET_MIN = 330;

// ---------------------------------------------------------------- csv

/** One line, honouring quoted cells and doubled quotes. */
export function splitCsv(line: string): string[] {
  const out: string[] = [];
  let cur = "", q = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (q && line[i + 1] === '"') { cur += '"'; i++; } else q = !q;
    } else if (ch === "," && !q) { out.push(cur); cur = ""; } else cur += ch;
  }
  out.push(cur);
  return out.map((x) => x.trim());
}

/** Data lines only: the templates carry `#` notes at the bottom that must not parse as rows. */
const dataLines = (text: string) =>
  text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));

export function downloadCsv(text: string, name: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: `${name}.csv` });
  a.click();
  URL.revokeObjectURL(url);
}

/** `.xlsx` is a zip, not text — say so instead of showing the user mojibake. */
export const isWorkbook = (filename: string) => /\.xlsx?$/i.test(filename);
export const WORKBOOK_HINT: ImportIssue = {
  line: "File",
  msg: "Excel workbooks cannot be read directly — in Excel choose File › Save As › CSV, then upload that.",
};

function header(text: string, need: string[], what: string): { head: string[]; error?: ImportIssue } {
  const lines = dataLines(text);
  if (!lines.length) return { head: [], error: { line: "File", msg: "The file is empty." } };
  const head = splitCsv(lines[0]).map((h) => h.toLowerCase());
  const missing = need.filter((h) => head.indexOf(h) < 0);
  if (missing.length) {
    return { head, error: { line: "Header", msg: `Missing column(s): ${missing.join(", ")}. Download the ${what} and use its header row.` } };
  }
  return { head };
}

// ---------------------------------------------------------------- templates

export function classTemplate(batches: Batch[], courses: Record<string, Course>, days: string[], hours: [number, number], smes: SME[]): string {
  const head = "batch_id,course,level,learners,topic,class_type,day,time,sme_name";
  const ex = batches.slice(0, 2).map((b, i) => {
    const c = courses[b.course];
    const topic = c.topics[i % c.topics.length];
    const who = smes.find((s) => s.topics.includes(topic))?.name ?? "";
    return [b.id, b.course, b.level, b.learners, topic, "class", days[i + 1], `${String(10 + i * 2).padStart(2, "0")}:00`, who].join(",");
  });
  const notes = [
    `# course: ${Object.keys(courses).join(" | ")}`,
    `# level: beginner | intermediate | advanced`,
    `# class_type: class | doubt | mock`,
    `# day: ${days.join(" | ")}`,
    `# time: on the hour, ${String(hours[0]).padStart(2, "0")}:00 to ${String(hours[1] - 1).padStart(2, "0")}:00 IST`,
    "# sme_name: leave blank to let the scheduler fill it",
    "# a new batch_id creates the batch; an existing one adds classes to it",
  ];
  return [head, ...ex, "", ...notes].join("\n");
}

export function smeTemplate(courses: Record<string, Course>, levels: string[]): string {
  const head = "sme_id,name,email,phone,city,courses,topics,level,preferred_per_week,work_days,work_hours";
  const ex = [
    ",Nikhil Raman,nikhil.raman@interviewkickstart.com,+91 98100 11223,Bengaluru,DSA,Arrays & Strings|Graphs & Trees,intermediate,4,Mon-Fri,09:00-18:00",
    ",Leena Fernandes,leena.fernandes@interviewkickstart.com,+91 98670 44556,Goa,ML|AI,ML Coding|RAG & Retrieval,beginner,3,Tue|Thu|Sat,14:00-20:00",
  ];
  const notes = [
    "# leave sme_id blank and the scheduler issues the next one",
    "# email is required and must be unique — invites and the weekly schedule go there",
    `# courses: ${Object.keys(courses).join(" | ")} (separate several with |)`,
    "# topics must belong to the listed courses, separated with |",
    `# level: ${levels.join(" | ")}`,
    "# preferred_per_week: 1–8 · work_days: Mon-Fri or Tue|Thu|Sat · work_hours: HH:MM-HH:MM between 08:00 and 20:00 IST",
  ];
  return [head, ...ex, "", ...notes].join("\n");
}

export function historyTemplate(smes: SME[]): string {
  const head = "sme_id,week,sessions_taught,batches,per_topic_rating";
  const ex = smes.slice(0, 2).map((s, i) => [
    s.id, `2026-W3${4 + i}`, 4 + i, (s.topics.length ? "DSA-01|DSA-02" : ""),
    s.topics.slice(0, 2).map((t, j) => `${t}:${(4.5 + j * 0.2).toFixed(1)}`).join("|"),
  ].join(","));
  const notes = [
    "# one row per SME per past week — this is what fairness and performance are scored from",
    "# week: ISO week, e.g. 2026-W34",
    "# sessions_taught: a whole number, 0 or more",
    "# batches: batch ids taught that week, separated with |",
    "# per_topic_rating: Topic:4.6|Topic:4.2 — the topic names must match the course topics",
  ];
  return [head, ...ex, "", ...notes].join("\n");
}

export interface HistoryCtx { smes: SME[] }

/** Deliberately the smallest of the three parsers: history is the least-used ingest path. Same
 *  contract as the others though — row-level errors, nothing accepted until the check is shown. */
export function parseHistoryImport(name: string, text: string, ctx: HistoryCtx): ImportResult<ImportedHistory> {
  const { head, error } = header(text, ["sme_id", "week", "sessions_taught"], "template");
  if (error) return { name, rows: [], errors: [error], parsed: true };

  const lines = dataLines(text).slice(1);
  const errors: ImportIssue[] = [];
  const rows: ImportedHistory[] = [];
  const col = (r: string[], c: string) => { const i = head.indexOf(c); return i < 0 ? "" : (r[i] ?? "").trim(); };
  const seen = new Set<string>();

  lines.forEach((line, i) => {
    const n = `Row ${i + 2}`;
    const r = splitCsv(line);
    if (!r.filter(Boolean).length) return;
    const smeId = col(r, "sme_id").toUpperCase();
    const week = col(r, "week");

    if (!smeId) return void errors.push({ line: n, msg: "sme_id is blank." });
    const sme = ctx.smes.find((s) => s.id === smeId);
    if (!sme) return void errors.push({ line: n, msg: `${smeId} is not on the roster — import the SMEs first.` });
    if (!/^\d{4}-W\d{2}$/.test(week)) return void errors.push({ line: n, msg: `Week "${week}" for ${sme.name} must look like 2026-W34.` });
    if (seen.has(`${smeId}|${week}`)) return void errors.push({ line: n, msg: `${sme.name} already has a row for ${week} in this file.` });

    const taught = Number(col(r, "sessions_taught"));
    if (!Number.isInteger(taught) || taught < 0) {
      return void errors.push({ line: n, msg: `${sme.name} ${week}: sessions_taught must be a whole number, 0 or more.` });
    }

    // Topic names are not checked against the course list: the engine also scores generic keys
    // ("doubt", and the lowercased subject) that no course declares as a topic.
    const ratings: Record<string, number> = {};
    let bad: string | null = null;
    col(r, "per_topic_rating").split("|").map((x) => x.trim()).filter(Boolean).forEach((pair) => {
      const cut = pair.lastIndexOf(":");
      const topic = cut < 0 ? "" : pair.slice(0, cut).trim();
      const score = Number(pair.slice(cut + 1));
      if (!topic || !Number.isFinite(score)) { bad = bad ?? `"${pair}" is not Topic:4.6.`; return; }
      if (score < 0 || score > 5) { bad = bad ?? `${topic} is rated ${score} — ratings run 0 to 5.`; return; }
      ratings[topic] = score;
    });
    if (bad) return void errors.push({ line: n, msg: `${sme.name} ${week}: ${bad}` });

    seen.add(`${smeId}|${week}`);
    rows.push({
      smeId, week, sessionsTaught: taught,
      batches: col(r, "batches").split("|").map((b) => b.trim().toUpperCase()).filter(Boolean),
      ratings,
    });
  });

  return { name, rows, errors, parsed: true };
}

// ---------------------------------------------------------------- class import

export interface ClassCtx {
  courses: Record<string, Course>;
  levels: string[];
  types: Record<string, string>;
  days: string[];
  hours: [number, number];
  /** what the week already holds, so an import cannot double-book a batch or a teacher */
  taken: { smeId: string | null; day: number; hour: number; batch: string }[];
  smes: SME[];
  isAvailable: (sme: SME, day: number, hour: number) => boolean;
}

export function parseClassImport(name: string, text: string, ctx: ClassCtx): ImportResult<ImportedClass> {
  const { head, error } = header(text, ["batch_id", "course", "level", "learners", "topic", "class_type", "day", "time", "sme_name"], "template again");
  if (error) return { name, rows: [], errors: [error], parsed: true };

  const lines = dataLines(text).slice(1);
  const errors: ImportIssue[] = [];
  const rows: ImportedClass[] = [];
  const at = (cells: string[], key: string) => cells[head.indexOf(key)] ?? "";

  lines.forEach((line, i) => {
    const n = `Row ${i + 2}`;
    const c = splitCsv(line);
    if (!c.filter(Boolean).length) return;
    const batch = at(c, "batch_id").toUpperCase();
    const courseKey = at(c, "course").toUpperCase();
    const course = ctx.courses[courseKey];
    const level = at(c, "level").toLowerCase();
    const topic = at(c, "topic");
    const type = at(c, "class_type").toLowerCase();
    const dayName = at(c, "day");
    const day = ctx.days.findIndex((d) => d.toLowerCase() === dayName.toLowerCase());
    const hour = parseInt(at(c, "time"), 10);
    const smeName = at(c, "sme_name");
    const learners = parseInt(at(c, "learners"), 10) || 30;
    const at0 = String(hour).padStart(2, "0");

    if (!batch) return void errors.push({ line: n, msg: "batch_id is empty." });
    if (!course) return void errors.push({ line: n, msg: `Unknown course "${at(c, "course")}" — use ${Object.keys(ctx.courses).join(", ")}.` });
    if (ctx.levels.indexOf(level) < 0) return void errors.push({ line: n, msg: `Unknown level "${at(c, "level")}" — use ${ctx.levels.join(", ")}.` });
    if (course.topics.indexOf(topic) < 0) return void errors.push({ line: n, msg: `"${topic}" is not a ${courseKey} topic. Allowed: ${course.topics.join(", ")}.` });
    if (!ctx.types[type]) return void errors.push({ line: n, msg: `Unknown class_type "${at(c, "class_type")}" — use ${Object.keys(ctx.types).join(", ")}.` });
    if (day < 0) return void errors.push({ line: n, msg: `Unknown day "${dayName}" — use ${ctx.days.join(", ")}.` });
    if (!(hour >= ctx.hours[0] && hour < ctx.hours[1])) {
      return void errors.push({ line: n, msg: `Time "${at(c, "time")}" is outside ${String(ctx.hours[0]).padStart(2, "0")}:00–${String(ctx.hours[1] - 1).padStart(2, "0")}:00.` });
    }
    if (rows.some((r) => r.batch === batch && r.day === day && r.hour === hour)
      || ctx.taken.some((t) => t.batch === batch && t.day === day && t.hour === hour)) {
      return void errors.push({ line: n, msg: `${batch} already has a class at ${dayName} ${at0}:00 — learners cannot attend both.` });
    }

    let smeId: string | null = null;
    if (smeName) {
      const sme = ctx.smes.find((x) => x.name.toLowerCase() === smeName.toLowerCase());
      if (!sme) return void errors.push({ line: n, msg: `No SME called "${smeName}".` });
      if (!sme.topics.includes(topic)) return void errors.push({ line: n, msg: `${sme.name} does not teach ${topic}.` });
      if (!ctx.isAvailable(sme, day, hour)) return void errors.push({ line: n, msg: `${sme.name} does not work ${dayName} ${at0}:00.` });
      if (ctx.taken.some((t) => t.smeId === sme.id && t.day === day && t.hour === hour)
        || rows.some((r) => r.smeId === sme.id && r.day === day && r.hour === hour)) {
        return void errors.push({ line: n, msg: `${sme.name} is already teaching at ${dayName} ${at0}:00.` });
      }
      if (level === "advanced" && sme.level === "beginner") {
        return void errors.push({ line: n, msg: `${sme.name} is beginner level and cannot take an advanced batch.` });
      }
      smeId = sme.id;
    }
    rows.push({ batch, course: courseKey, level: level as LevelName, learners, topic, type: type as SessionType, day, hour, smeId, smeName: smeId ? smeName : null });
  });
  return { name, rows, errors, parsed: true };
}

// ---------------------------------------------------------------- SME import

export interface SmeCtx { courses: Record<string, Course>; levels: string[]; days: string[]; smes: SME[] }

export function parseSmeImport(name: string, text: string, ctx: SmeCtx): ImportResult<ImportedSme> {
  const { head, error } = header(text, ["name", "email", "city", "courses", "topics", "level", "preferred_per_week", "work_days", "work_hours"], "template");
  if (error) return { name, rows: [], errors: [error], parsed: true };

  const lines = dataLines(text).slice(1);
  const errors: ImportIssue[] = [];
  const rows: ImportedSme[] = [];
  const col = (r: string[], c: string) => { const i = head.indexOf(c); return i < 0 ? "" : (r[i] ?? "").trim(); };
  const seenEmail = new Set<string>(), seenId = new Set<string>();

  lines.forEach((line, i) => {
    const n = `Row ${i + 2}`;
    const r = splitCsv(line);
    if (!r.filter(Boolean).length) return;
    const nm = col(r, "name");
    const email = col(r, "email").toLowerCase();
    const id = col(r, "sme_id").toUpperCase();

    if (!nm) return void errors.push({ line: n, msg: "Name is blank." });
    if (!email) return void errors.push({ line: n, msg: `${nm} has no email — invites cannot be sent without one.` });
    if (!EMAIL.test(email)) return void errors.push({ line: n, msg: `"${email}" is not a valid email address.` });
    if (ctx.smes.some((s) => (s.email ?? "").toLowerCase() === email) || seenEmail.has(email)) {
      return void errors.push({ line: n, msg: `${email} is already on the roster or repeated in this file.` });
    }
    if (id) {
      if (!/^T\d{2}$/.test(id)) return void errors.push({ line: n, msg: `sme_id "${id}" must look like T17 — or leave it blank.` });
      if (ctx.smes.some((s) => s.id === id) || seenId.has(id)) return void errors.push({ line: n, msg: `sme_id ${id} is already taken.` });
    }

    const courses = col(r, "courses").split("|").map((c) => c.trim().toUpperCase()).filter(Boolean);
    if (!courses.length) return void errors.push({ line: n, msg: `${nm} has no course — one of ${Object.keys(ctx.courses).join(", ")}.` });
    const badC = courses.filter((c) => !ctx.courses[c]);
    if (badC.length) return void errors.push({ line: n, msg: `Unknown course "${badC[0]}" for ${nm}.` });

    const allowed = courses.flatMap((c) => ctx.courses[c].topics);
    const topics = col(r, "topics").split("|").map((t) => t.trim()).filter(Boolean);
    const badT = topics.filter((t) => allowed.indexOf(t) < 0);
    if (badT.length) return void errors.push({ line: n, msg: `"${badT[0]}" is not a topic in ${courses.join(" + ")}.` });

    const level = col(r, "level").toLowerCase();
    if (ctx.levels.indexOf(level) < 0) return void errors.push({ line: n, msg: `Level "${level}" for ${nm} — use ${ctx.levels.join(", ")}.` });

    const preferred = parseInt(col(r, "preferred_per_week"), 10);
    if (!preferred || preferred < 1 || preferred > 8) return void errors.push({ line: n, msg: `${nm}: preferred_per_week must be a number from 1 to 8.` });

    const dayTxt = col(r, "work_days");
    let days: number[] = [];
    const span = dayTxt.match(/^([A-Za-z]{3})\s*-\s*([A-Za-z]{3})$/);
    if (span) {
      const a = ctx.days.findIndex((d) => d.toLowerCase() === span[1].toLowerCase());
      const b = ctx.days.findIndex((d) => d.toLowerCase() === span[2].toLowerCase());
      if (a < 0 || b < 0 || b < a) return void errors.push({ line: n, msg: `work_days "${dayTxt}" for ${nm} — use Mon-Fri or Tue|Thu|Sat.` });
      for (let k = a; k <= b; k++) days.push(k);
    } else {
      const parts = dayTxt.split("|").map((d) => d.trim()).filter(Boolean);
      days = parts.map((d) => ctx.days.findIndex((x) => x.toLowerCase() === d.toLowerCase()));
      if (!days.length || days.some((d) => d < 0)) return void errors.push({ line: n, msg: `work_days "${dayTxt}" for ${nm} — use ${ctx.days.join(", ")}.` });
    }

    const raw = col(r, "work_hours");
    const hm = raw.match(/^(\d{1,2}):?(\d{2})?\s*-\s*(\d{1,2}):?(\d{2})?$/);
    if (!hm) return void errors.push({ line: n, msg: `work_hours "${raw}" for ${nm} — use HH:MM-HH:MM.` });
    const from = parseInt(hm[1], 10), to = parseInt(hm[3], 10);
    if (from < 8 || to > 20 || to - from < 1) {
      return void errors.push({ line: n, msg: `${nm} works ${from}:00–${to}:00 — the teaching window is 08:00 to 20:00 IST.` });
    }

    seenEmail.add(email);
    if (id) seenId.add(id);
    rows.push({ id, name: nm, email, phone: col(r, "phone"), city: col(r, "city") || "—",
      courses, topics, level: level as LevelName, preferred, avail: days.map((d) => [d, from, to] as [number, number, number]) });
  });

  // ids are issued only once a row is known good, so the preview shows exactly what will exist
  let next = ctx.smes.reduce((m, s) => Math.max(m, parseInt(s.id.slice(1), 10) || 0), 0);
  rows.forEach((r) => { if (!r.id) { next += 1; r.id = `T${String(next).padStart(2, "0")}`; } });

  return { name, rows, errors, parsed: true };
}

// ---------------------------------------------------------------- roster record

const hhmmOf = (min: number) => `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;

/** An imported row becomes a real SME the engine can schedule: IST working hours land as UTC windows. */
/** An imported history row in the shape run_pipeline's `history` argument expects. */
export function toHistoryRecord(r: ImportedHistory) {
  return {
    sme_id: r.smeId, week: r.week, sessions_taught: r.sessionsTaught,
    batches: r.batches, per_topic_rating: r.ratings, post_session_rating: null,
  };
}

export function toSme(r: ImportedSme, days: string[], levels: string[]): SME {
  const weekly_availability: AvailabilityWindow[] = r.avail.map(([d, from, to]) => ({
    weekday: days[d],
    start_utc: hhmmOf(from * 60 - IST_OFFSET_MIN),
    end_utc: hhmmOf(to * 60 - IST_OFFSET_MIN),
    local: `${String(from).padStart(2, "0")}:00–${String(to).padStart(2, "0")}:00 Asia/Kolkata`,
  }));
  return {
    id: r.id, name: r.name, email: r.email, phone: r.phone,
    subject: r.courses[0], subjects: r.courses, sub_specialty: null, topics: r.topics,
    training_level: levels.indexOf(r.level) + 1, level: r.level,
    to_upgrade: r.level === "advanced" ? 0 : r.level === "intermediate" ? 8 : 12,
    timezone: "Asia/Kolkata", city: r.city, rating: 0, preferred: r.preferred, leave: null,
    weekly_availability, preference_notes: "Added from an imported roster — no preferences on record yet.",
    history: [],
  };
}

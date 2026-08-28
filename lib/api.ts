import type {
  AgentApplyResult, AgentMove, AgentRequest, AgentResult, ApprovalsResult, ChatTurn, Decision, DraftRow,
  DataProvenance, ExportRow, HistoryRecord, OverrideEvent, RunResult, Session, SME,
} from "./types";

async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${url} → ${res.status}: ${(await res.text()).slice(0, 300)}`);
  return res.json();
}

export function runMatching(
  sessions: Session[],
  smes: SME[],
  history: HistoryRecord[],
  overrides: OverrideEvent[],
  opts: { llm?: boolean } = {},
) {
  const payload = overrides.map((o) => ({
    session_id: o.session_id, batch_id: o.batch_id, from_sme_id: o.from_sme_id, to_sme_id: o.to_sme_id,
  }));
  return post<RunResult>("/api/run", { sessions, smes, history, overrides: payload, llm: opts.llm !== false });
}

/** `week` and `actor` are what let the server record each override — omit them and nothing is logged. */
export function submitApprovals(draft: DraftRow[], decisions: Decision[], week?: string, actor?: string) {
  return post<ApprovalsResult>("/api/approvals", { draft, decisions, week, actor });
}

export interface PublishResult { status: "sent" | "simulated" | "skipped" | "error"; detail: string; count: number; live: boolean }

/** One channel/audience leaf at a time, so the sheet can report each as it lands. */
export function publishLeaf(body: {
  week: string; week_label: string; channel: string; audience: string;
  rows: DraftRow[]; smes: SME[]; batches: unknown[];
}) {
  return post<PublishResult>("/api/publish", body);
}

export function saveSchedule(week: string, draft: DraftRow[], extra: Record<string, unknown> = {}) {
  return post<{ saved: string; rows: number }>("/api/schedule", { week, draft, ...extra });
}

export interface SavedSchedule {
  draft: DraftRow[];
  updated_at: string;
  stats?: RunResult["stats"];
  flags?: RunResult["flags"];
  published?: boolean;
  /** where each dataset came from, so provenance survives a refresh */
  provenance?: DataProvenance;
  /** an imported assignment history, which Stage B scores from */
  history?: HistoryRecord[];
}

export async function loadSchedule(week: string): Promise<SavedSchedule | null> {
  const res = await fetch(`/api/schedule?week=${encodeURIComponent(week)}`);
  if (!res.ok) throw new Error(`/api/schedule → ${res.status}`);
  return res.json();          // null when nothing has been saved for that week yet
}

/** The copilot reasons over the draft the page holds — nothing is applied by this call. */
export function agentRun(
  week: string, req: AgentRequest, draft: DraftRow[], smes: SME[], history: HistoryRecord[], turns?: ChatTurn[],
) {
  return post<AgentResult>("/api/agent/run", {
    week, mode: req.mode, sme_id: req.smeId, days: req.days?.length ? req.days : null, question: req.question,
    // only role + content travel: the server re-derives everything else from the draft it is sent
    turns: turns?.map((t) => ({ role: t.role, content: t.content })), draft, smes, history,
  });
}

/** Applies through the engine's override path with actor `agent`; Stage D re-validates server-side. */
export function agentApply(week: string, plan: AgentMove[], draft: DraftRow[], smes: SME[], history: HistoryRecord[]) {
  return post<AgentApplyResult>("/api/agent/apply", { week, plan, actor: "agent", auto: false, draft, smes, history });
}

export interface IntegrationsInfo {
  channels: Record<string, { live: boolean; detail: string; name: string }>;
  storage: { driver: string; location: string; durable: boolean };
  sheets: { live: boolean; detail: string; name: string; spreadsheet_id: string | null; tabs: Record<string, string> };
  llm: { live: boolean; provider: string | null; model: string | null };
}

export async function getIntegrations(): Promise<IntegrationsInfo> {
  const res = await fetch("/api/integrations");
  if (!res.ok) throw new Error(`/api/integrations → ${res.status}`);
  return res.json();
}

export interface SheetResult extends PublishResult { csv?: string; tab?: string }

export interface DatasetBundle {
  datasets: Record<string, { payload: unknown; source: string; updated_at: string | null }>;
}

/** What the app boots from: a stored dataset where one exists, the bundled seed JSON otherwise. */
export async function getData(): Promise<DatasetBundle> {
  const res = await fetch("/api/data");
  if (!res.ok) throw new Error(`/api/data → ${res.status}`);
  return res.json();
}

/** Persist a dataset the coordinator has just confirmed. */
export function putData(name: string, payload: unknown, source: string) {
  return post<{ saved: string; source: string; rows: number }>(`/api/data/${name}`, { payload, source });
}

/** Back to the bundled seed week. */
export function resetData() {
  return post<{ cleared: number }>("/api/data/reset", {});
}

export interface OverrideStats {
  entries: { week: string; session_id: string; batch_id: string; from_sme_id: string | null; to_sme_id: string; actor: string; rule_risk: string | null; at: string }[];
  by_week: Record<string, { overridden: number; assigned: number; rate: number | null }>;
}

export async function getOverrides(week?: string): Promise<OverrideStats> {
  const res = await fetch(`/api/overrides${week ? `?week=${encodeURIComponent(week)}` : ""}`);
  if (!res.ok) throw new Error(`/api/overrides → ${res.status}`);
  return res.json();
}

export interface AvailabilityResult extends PublishResult {
  smes: SME[];
  per_sme: Record<string, number>;
  synced_at: string;
}

/** Read every teacher's calendar for the week; Stage A then refuses to book over a busy block. */
export function syncAvailability(smes: SME[], weekStartUtc: string, weekEndUtc: string) {
  return post<AvailabilityResult>("/api/availability/sync", {
    smes, week_start_utc: weekStartUtc, week_end_utc: weekEndUtc,
  });
}

export interface PullResult extends SheetResult { dataset?: string; source?: string; synced_at?: string }

/** One dataset as CSV text, from whichever source is configured. The caller runs it through the
 *  same parser a file upload uses — there is no second column contract. */
export function pullSheet(dataset: "sessions" | "smes" | "history", spreadsheetId?: string) {
  return post<PullResult>("/api/sheets/pull", { dataset, spreadsheet_id: spreadsheetId ?? null });
}

/** The approved week into the draft tab, same columns as the CSV export. */
export function pushSheet(week: string, weekLabel: string, rows: ExportRow[], tab?: string) {
  return post<SheetResult>("/api/sheets/push", { week, week_label: weekLabel, rows, tab: tab ?? null });
}

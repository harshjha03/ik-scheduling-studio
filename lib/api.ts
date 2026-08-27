import type { ApprovalsResult, Decision, DraftRow, HistoryRecord, OverrideEvent, RunResult, Session, SME } from "./types";

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

export function submitApprovals(draft: DraftRow[], decisions: Decision[]) {
  return post<ApprovalsResult>("/api/approvals", { draft, decisions });
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
}

export async function loadSchedule(week: string): Promise<SavedSchedule | null> {
  const res = await fetch(`/api/schedule?week=${encodeURIComponent(week)}`);
  if (!res.ok) throw new Error(`/api/schedule → ${res.status}`);
  return res.json();          // null when nothing has been saved for that week yet
}

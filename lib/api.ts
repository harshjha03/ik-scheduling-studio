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

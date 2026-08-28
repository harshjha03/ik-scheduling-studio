"use client";
import { useState } from "react";
import type { AgentMove, AgentRequest, AgentResult, AgentStep, DraftRow, SME } from "@/lib/types";
import { isMove, isReschedule, isUpgrade } from "@/lib/types";
import { istParts } from "@/lib/view";

interface Props {
  req: AgentRequest;
  res: AgentResult | null;
  busy: boolean;
  smes: SME[];
  rows: DraftRow[];
  days: string[];
  onReq: (patch: Partial<AgentRequest>) => void;
  onRun: () => void;
}

const VERDICT_PILL = {
  ok: { background: "var(--green-tint)", color: "var(--green-ink)", label: "ok" },
  fairness_warning: { background: "var(--amber-tint)", color: "var(--amber-ink)", label: "fairness warning" },
} as const;

/** Honest status: a fallback or an exhausted budget is never dressed up as a copilot answer. */
export function StatusBanner({ res }: { res: AgentResult }) {
  if (res.status === "ok") return null;
  return (
    <div className="rounded-[14px] p-[12px_14px] text-[12.5px] leading-[1.55]" style={{ background: "var(--amber-tint)", color: "var(--amber-ink)" }}>
      {res.status === "fallback"
        ? <>Copilot fallback — the LLM could not complete this run{res.meta.error ? ` (${res.meta.error.split(":")[0]})` : ""}. What follows is the deterministic engine answer, not the copilot&apos;s reasoning.</>
        : <>Budget exhausted — the copilot stopped after {res.meta.tool_calls} tool calls / {res.meta.llm_turns} turns. Partial findings below.</>}
    </div>
  );
}

/** The copilot's evidence: every tool call with a one-line result digest. */
export function Working({ steps }: { steps: AgentStep[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button className="btn-quiet text-[12px]" onClick={() => setOpen((v) => !v)}>
        {open ? "Hide working" : `Show working — ${steps.length} step${steps.length === 1 ? "" : "s"}`}
      </button>
      {open && (
        <ol className="mt-2 flex flex-col gap-[7px]">
          {steps.map((st, i) => (
            <li key={i} className="rounded-[12px] p-[9px_12px] text-[12px] leading-[1.5]" style={{ background: "var(--page)" }}>
              <div style={{ color: "var(--ink-2)" }}>{st.thought || (st.error ? "Invalid response" : "")}</div>
              <div className="mt-[3px] font-mono text-[11px]" style={{ color: st.error ? "var(--red-ink)" : "var(--brand-deep)" }}>
                {st.tool ? `${st.tool}(${JSON.stringify(st.args)})` : "—"}
              </div>
              <div className="mt-[2px] text-[11.5px]" style={{ color: "var(--muted)" }}>{st.result_digest}</div>
            </li>
          ))}
          {!steps.length && <li className="text-[12px]" style={{ color: "var(--muted)" }}>No tool calls were made.</li>}
        </ol>
      )}
    </div>
  );
}

/** Renders the copilot's plain-text answer as the structure it was written in: label lines, option
 *  lines starting with "- ", blank lines between subjects. Deliberately not a markdown renderer —
 *  the prompt forbids markdown, so there is nothing to parse beyond these three cases. */
export function AnswerText({ text, muted }: { text: string; muted?: boolean }) {
  const ink = muted ? "var(--muted)" : "var(--ink-2)";
  const blocks: React.ReactNode[] = [];
  let bullets: string[] = [];
  const flush = () => {
    if (!bullets.length) return;
    blocks.push(
      <ul key={`u${blocks.length}`} className="m-0 flex list-none flex-col gap-[3px] p-0">
        {bullets.map((b, i) => (
          <li key={i} className="flex gap-[7px] text-[12.5px] leading-[1.5]" style={{ color: ink }}>
            <span aria-hidden style={{ color: "var(--brand-deep)" }}>·</span>
            <span className="min-w-0">{b}</span>
          </li>
        ))}
      </ul>,
    );
    bullets = [];
  };
  text.split(/\r?\n/).forEach((raw) => {
    const line = raw.trim();
    if (!line) { flush(); return; }
    if (/^[-•]\s+/.test(line)) { bullets.push(line.replace(/^[-•]\s+/, "")); return; }
    flush();
    // a label line: the subject a group of options belongs to ("DSA-01 · Sat 15:00 · Dynamic Programming")
    const label = line.includes(" · ") || line.endsWith(":");
    blocks.push(
      <p
        key={`p${blocks.length}`}
        className={label ? "text-[11.5px] font-semibold" : "text-[12.5px] leading-[1.55]"}
        style={label
          ? { color: "var(--ink)", letterSpacing: "0.01em", marginTop: blocks.length ? 4 : 0 }
          : { color: ink }}
      >
        {line.replace(/:$/, "")}
      </p>,
    );
  });
  flush();
  return <div className="flex flex-col gap-[5px]">{blocks}</div>;
}

/** One line per plan entry with its simulated verdict, then the copilot's answer.
 *  Three kinds read differently to a coordinator: who teaches it, when it runs, what someone is
 *  qualified for — so each is phrased in its own words rather than a generic "action". */
export function PlanCard({ plan, answer, rows, smes, days, bare = false }: {
  plan: AgentMove[] | null; answer: string; rows: DraftRow[]; smes: SME[]; days: string[]; bare?: boolean;
}) {
  const name = (id: string | null | undefined) => smes.find((s) => s.id === id)?.name ?? id ?? "—";
  const label = (sessionId: string) => {
    const row = rows.find((r) => r.session_id === sessionId);
    if (!row) return sessionId;
    const p = istParts(row.start_utc);
    return `${row.batch_id} · ${row.sub_specialty ?? row.type} (${days[p.day] ?? ""} ${p.label})`;
  };
  const heading = plan?.length
    ? `Plan — ${plan.length} change${plan.length === 1 ? "" : "s"}`
    : "No plan to apply";
  return (
    <div className={bare ? "" : "rounded-[16px] p-[13px_14px]"} style={bare ? undefined : { border: "1px solid var(--line)" }}>
      {(!bare || !!plan?.length) && <div className="label-caps mb-2">{heading}</div>}
      {plan?.map((a, i) => {
        const pill = VERDICT_PILL[a.verdict === "fairness_warning" ? "fairness_warning" : "ok"];
        return (
          <div key={i} className="flex items-start gap-[10px] py-[7px]" style={{ borderTop: "1px solid var(--line-2)" }}>
            <span
              className="mt-[1px] shrink-0 rounded-[7px] px-[6px] py-[2px] text-[9.5px] font-bold uppercase"
              style={{ background: "var(--brand-tint)", color: "var(--brand-deep)", letterSpacing: "0.04em" }}
            >
              {isReschedule(a) ? "move time" : isUpgrade(a) ? "level" : "teacher"}
            </span>
            <div className="min-w-0 flex-1 text-[12.5px] leading-[1.5]">
              {isReschedule(a) ? (
                <>
                  {label(a.session_id)} → <b>{a.to_day} {a.to_hour_ist}</b>
                  {a.eligible_after?.length ? <> · {a.eligible_after[0].name} can teach it</> : null}
                </>
              ) : isUpgrade(a) ? (
                <>
                  <b>{a.sme_name ?? name(a.sme_id)}</b> · training level {a.from_level} → <b>{a.to_level}</b>
                  {a.unblocks?.length ? <> · qualifies them for {a.unblocks.map(label).join(", ")}</> : null}
                </>
              ) : (
                <>
                  <b>{a.to_sme_name ?? name(a.to_sme)}</b> → {label(a.session_id)}
                  {a.from_sme ? <>, replacing {name(a.from_sme)}</> : null}
                </>
              )}
              {(a.reason || a.detail) && (
                <div className="text-[11.5px]" style={{ color: "var(--muted)" }}>
                  {[a.reason, a.detail].filter(Boolean).join(" · ")}
                </div>
              )}
            </div>
            <span className="shrink-0 rounded-[8px] px-2 py-[3px] text-[10px] font-semibold" style={{ background: pill.background, color: pill.color }}>
              {a.flag === "AGENT_FALLBACK" ? "fallback · " : ""}{pill.label}
            </span>
          </div>
        );
      })}
      {!!answer && <div className="mt-2"><AnswerText text={answer} /></div>}
    </div>
  );
}

/** The two targeted entry points (SME profile → recovery, dashboard → review). Free-form asks live in
 *  the floating chat instead; both render the same evidence and the same plan card. */
export default function AgentSheet({ req, res, busy, smes, rows, days, onReq, onRun }: Props) {
  const recovery = req.mode === "recovery";
  const canRun = recovery ? !!req.smeId : !!req.question?.trim();

  return (
    <div className="flex flex-col gap-[14px]">
      <div className="grid grid-cols-2 gap-[10px]">
        {recovery ? (
          <>
            <label className="block">
              <span className="label-caps mb-[6px] block">Teacher unavailable</span>
              <select className="field w-full" value={req.smeId ?? ""} disabled={busy} onChange={(e) => onReq({ smeId: e.target.value })}>
                {smes.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="label-caps mb-[6px] block">Which days</span>
              <select className="field w-full" value={req.days?.[0] ?? ""} disabled={busy} onChange={(e) => onReq({ days: e.target.value ? [e.target.value] : [] })}>
                <option value="">Whole week</option>
                {days.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </label>
          </>
        ) : (
          <label className="col-span-2 block">
            <span className="label-caps mb-[6px] block">Your question about this week</span>
            <input
              className="field w-full" autoFocus disabled={busy} value={req.question ?? ""}
              placeholder="e.g. why is W37-DSA-01-1 unfilled? who is overloaded?"
              onChange={(e) => onReq({ question: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter" && canRun && !busy) onRun(); }}
            />
          </label>
        )}
      </div>
      {!res && (
        <button className="btn btn-primary self-start" disabled={!canRun || busy} onClick={onRun}>
          {busy ? "Thinking…" : recovery ? "Find cover" : "Ask"}
        </button>
      )}

      {res && (
        <>
          <StatusBanner res={res} />
          <Working steps={res.transcript} />
          <PlanCard plan={res.plan} answer={res.answer} rows={rows} smes={smes} days={days} />
        </>
      )}
    </div>
  );
}

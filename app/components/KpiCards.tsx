import type { Batch, DraftRow, LlmStats, SME } from "@/lib/types";
import { kpis } from "@/lib/view";

interface Props {
  rows: DraftRow[];
  smes: SME[];
  batches: Batch[];
  approved: Set<string>;
  leaveCount: number;
  spread: Record<string, number>;
  llm: LlmStats;
  autoAssigned: number;
  llmResolved: number;
}

function Card({ label, value, unit, sub, alert = false, dot }: {
  label: string; value: React.ReactNode; unit?: string; sub: React.ReactNode; alert?: boolean; dot: string;
}) {
  return (
    <div
      className="kpi"
      style={alert ? { background: "var(--red-tint)", borderColor: "var(--red-line)" } : undefined}
    >
      <div className="flex items-center gap-2">
        <span className="size-[9px] rounded-full" style={{ background: dot }} />
        <span
          className="text-[11.5px] font-semibold"
          style={{ letterSpacing: "0.02em", color: alert ? "var(--red-ink)" : "var(--ink-3)" }}
        >
          {label}
        </span>
      </div>
      <div className="mt-3 flex items-end gap-[9px]">
        <span
          className="text-[34px] font-bold leading-none"
          style={{ letterSpacing: "-0.03em", color: alert ? "#8a2318" : "var(--ink)" }}
        >
          {value}
        </span>
        {unit && <span className="pb-[3px] text-[12px]" style={{ color: "var(--muted)" }}>{unit}</span>}
      </div>
      <div className="mt-2 text-[11.5px] leading-[1.45]" style={{ color: "var(--muted)" }}>{sub}</div>
    </div>
  );
}

export default function KpiCards({ rows, smes, batches, approved, leaveCount, spread, llm, autoAssigned, llmResolved }: Props) {
  const k = kpis(rows, smes, batches, approved);
  const worst = Object.entries(spread).sort((a, b) => b[1] - a[1])[0];
  return (
    <div className="grid gap-[14px]" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(196px,1fr))" }}>
      <Card
        label="Batches running" value={k.batches} unit="batches" dot="#2f5fd0"
        sub={`${k.courses} courses · ${k.learners} learners`}
      />
      <Card
        label="Teachers active" value={k.activeTeachers} unit={`of ${k.totalTeachers}`} dot="#4a7fd0"
        sub={`${leaveCount} on leave soon · ${k.workload} above the fairness band`}
      />
      <Card
        label="Classes this week" value={k.classes} unit="sessions" dot="#0f7a52"
        sub={`${k.byType.class} classes · ${k.byType.doubt} doubt · ${k.byType.mock} mocks`}
      />
      <Card
        label="Conflicts to clear" value={k.attention} unit={k.attention === 1 ? "item" : "items"} dot="#c0392b"
        alert={k.attention > 0}
        sub={`${k.unfilled} unfilled · ${k.conflicts} double-booked · ${k.workload} workload`}
      />
      <Card
        label="Draft quality" value={`${autoAssigned}/${rows.length}`} unit="auto" dot="#5568c4"
        sub={
          <>
            {llmResolved} resolved by {llm.skipped ? "score" : `LLM${llm.model ? ` (${llm.model})` : ""}`}
            {llm.fallback ? ` · ${llm.fallback} deterministic` : ""}
            <br />
            fairness spread {worst ? `${worst[0]} ${worst[1]}` : "—"}
            {Object.keys(spread).length > 1 ? ` (worst of ${Object.keys(spread).length} pools)` : ""}
          </>
        }
      />
    </div>
  );
}

import type { Batch, DraftRow, SME } from "@/lib/types";
import { kpis } from "@/lib/view";

interface Props {
  rows: DraftRow[];
  smes: SME[];
  batches: Batch[];
  approved: Set<string>;
  leaveCount: number;
  workCount: number;
  unfilled: number;
  conflicts: number;
  advisory: number;
  onBatches: () => void;
  onSmes: () => void;
  onShowAll: () => void;
  onWork: () => void;
}

function Card({ label, value, unit, sub, dot, tone = "plain", onClick, tip }: {
  label: string; value: React.ReactNode; unit?: string; sub: React.ReactNode; dot: string;
  tone?: "plain" | "alert" | "good"; onClick: () => void; tip: string;
}) {
  const bg = tone === "alert" ? "var(--red-tint)" : tone === "good" ? "var(--green-tint)" : "#fff";
  const border = tone === "alert" ? "var(--red-line)" : tone === "good" ? "var(--green-line)" : "var(--line)";
  const labelColor = tone === "alert" ? "var(--red-ink)" : tone === "good" ? "var(--green-ink)" : "var(--ink-3)";
  const valueColor = tone === "alert" ? "#8a2318" : tone === "good" ? "var(--green-ink)" : "var(--ink)";
  return (
    <button
      onClick={onClick}
      title={tip}
      className="kpi block w-full text-left transition hover:-translate-y-[2px] hover:shadow-[0_10px_26px_rgba(16,26,51,0.09)]"
      style={{ background: bg, borderColor: border, cursor: "pointer" }}
    >
      <span className="flex items-center gap-2">
        <span className="size-[9px] rounded-full" style={{ background: dot }} />
        <span className="text-[11.5px] font-semibold" style={{ letterSpacing: "0.02em", color: labelColor }}>{label}</span>
      </span>
      <span className="mt-3 flex items-end gap-[9px]">
        <span className="text-[34px] font-bold leading-none" style={{ letterSpacing: "-0.03em", color: valueColor }}>{value}</span>
        {unit && <span className="pb-[3px] text-[12px]" style={{ color: "var(--muted)" }}>{unit}</span>}
      </span>
      <span className="mt-2 block text-[11.5px] leading-[1.45]" style={{ color: "var(--muted)" }}>{sub}</span>
    </button>
  );
}

export default function KpiCards({
  rows, smes, batches, approved, leaveCount, workCount, unfilled, conflicts, advisory,
  onBatches, onSmes, onShowAll, onWork,
}: Props) {
  const k = kpis(rows, smes, batches, approved);
  return (
    <div className="grid shrink-0 gap-[14px]" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(178px,1fr))" }}>
      <Card
        label="Batches running" value={k.batches} unit="batches" dot="#2f5fd0"
        sub={`${k.courses} courses · ${k.learners} learners`}
        tip="Open batch management" onClick={onBatches}
      />
      <Card
        label="Teachers active" value={k.activeTeachers} unit={`of ${k.totalTeachers}`} dot="#4a7fd0"
        sub={`${leaveCount} on leave soon · ${k.workload} above the fairness band`}
        tip="Open SME management" onClick={onSmes}
      />
      <Card
        label="Classes this week" value={k.classes} unit="sessions" dot="#0f7a52"
        sub={`${k.byType.class} classes · ${k.byType.doubt} doubt · ${k.byType.mock} mocks`}
        tip="Show every status on the calendar" onClick={onShowAll}
      />
      <Card
        label="Needs a decision" value={workCount} unit={workCount === 1 ? "item" : "items"}
        dot={workCount ? "#c0392b" : "#0f7a52"} tone={workCount ? "alert" : "good"}
        sub={workCount
          ? `${unfilled} unfilled · ${conflicts} double-booked · ${advisory} workload / leave`
          : "Nothing blocking this week"}
        tip="Open work items" onClick={onWork}
      />
    </div>
  );
}

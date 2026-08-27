"use client";
import type { Batch, Category, Course, DraftRow, Meta, OverrideEvent, WeekKey, WeekMeta } from "@/lib/types";
import { CATEGORIES, category } from "@/lib/view";
import WeekCalendar from "./WeekCalendar";
import OverridesList from "./OverridesList";
import { BatchMenu, StatusMenu } from "./FilterMenus";

interface Props {
  rows: DraftRow[];
  allRows: DraftRow[];
  batches: Batch[];
  courses: Record<string, Course>;
  meta: Meta;
  weeks: Record<WeekKey, WeekMeta>;
  week: WeekKey;
  weekDates: { day: string; date: string }[];
  tab: "schedule" | "overrides";
  approved: Set<string>;
  changed: Set<string>;
  batchFilter: string;
  statusOff: Record<string, boolean>;
  workCount: number;
  overrides: OverrideEvent[];
  loading: boolean;
  smeName: (id: string | null) => string;
  onTab: (t: "schedule" | "overrides") => void;
  onWeek: (w: WeekKey) => void;
  onBatchFilter: (v: string) => void;
  onStatusToggle: (k: Category) => void;
  onStatusAll: (allOn: boolean) => void;
  onOpenWork: () => void;
  onApproveWeek: () => void;
  onRerun: () => void;
  onOpen: (sessionId: string) => void;
  onOpenOverride: (o: OverrideEvent) => void;
}

export default function Dashboard({
  rows, allRows, batches, courses, meta, weeks, week, weekDates, tab, approved, changed, batchFilter, statusOff,
  workCount, overrides, loading, smeName, onTab, onWeek, onBatchFilter, onStatusToggle, onStatusAll,
  onOpenWork, onApproveWeek, onRerun, onOpen, onOpenOverride,
}: Props) {
  const locked = weeks[week].locked;
  const unfilled = allRows.filter((r) => !r.sme_id).length;
  const allApproved = allRows.length > 0 && allRows.every((r) => approved.has(r.session_id));
  const counts = CATEGORIES.reduce((acc, c) => {
    acc[c.key] = rows.filter((r) => category(r, approved.has(r.session_id)) === c.key).length;
    return acc;
  }, {} as Record<Category, number>);
  const shown = rows.filter((r) => !statusOff[category(r, approved.has(r.session_id))]);

  return (
    <section className="card">
      <div className="flex flex-wrap items-center gap-3 p-[16px_20px_14px]" style={{ borderBottom: "1px solid var(--line-2)" }}>
        <div className="tabs">
          <button onClick={() => onTab("schedule")} className={`tab ${tab === "schedule" ? "tab-on" : "tab-off"}`}>Schedule</button>
          <button onClick={() => onTab("overrides")} className={`tab ${tab === "overrides" ? "tab-on" : "tab-off"}`}>
            Overrides {overrides.length}
          </button>
        </div>
        <div className="tabs">
          {(["current", "next"] as WeekKey[]).map((k) => (
            <button key={k} onClick={() => onWeek(k)} className={`tab ${week === k ? "tab-on" : "tab-off"}`}>
              {weeks[k].label}
            </button>
          ))}
        </div>
        <span className="text-[12px]" style={{ color: "var(--muted)" }}>{weeks[week].range}</span>
        {locked ? (
          <span
            className="rounded-[9px] px-[10px] py-[5px] text-[11.5px] font-semibold"
            style={{ background: "var(--brand-tint)", color: "var(--brand-deep)" }}
          >
            Live week
          </span>
        ) : allApproved ? (
          <span
            className="rounded-[9px] px-[10px] py-[5px] text-[11.5px] font-semibold"
            style={{ background: "var(--green-tint)", color: "var(--green-ink)" }}
          >
            ✓ Approved
          </span>
        ) : null}

        <div className="ml-auto flex flex-wrap items-center gap-[9px]">
          {!locked && (
            <>
              <button className="btn" onClick={onRerun} disabled={loading} title="Re-run the matching pipeline with your overrides applied">
                {loading ? "Running…" : "Re-run draft"}
              </button>
              <span
                className="rounded-[9px] px-[10px] py-[5px] text-[11.5px]"
                style={changed.size
                  ? { background: "var(--amber-tint)", color: "var(--amber-ink)", fontWeight: 650 }
                  : { color: "var(--muted)" }}
              >
                {changed.size} rows changed
              </span>
            </>
          )}
          <button className="btn btn-primary flex items-center gap-2" onClick={onOpenWork} title="Unfilled classes, conflicts and workload flags for this week">
            <span>Work items</span>
            <span className="rounded-[7px] px-[7px] text-[11px] font-bold text-white" style={{ background: "var(--red)" }}>{workCount}</span>
          </button>
          {!locked && !allApproved && (
            <button
              className="btn btn-go"
              onClick={onApproveWeek}
              title={unfilled
                ? `System-generated draft — ${unfilled} class(es) still have no teacher; clear them from Work items first.`
                : `Approving publishes all ${allRows.length} classes to learner and SME calendars.`}
            >
              Approve all {allRows.length} classes
            </button>
          )}
        </div>
      </div>

      {tab === "schedule" && (
        <div className="relative flex flex-wrap items-center gap-[9px] p-[12px_20px]" style={{ borderBottom: "0.5px solid rgba(16,26,51,0.06)" }}>
          <BatchMenu batches={batches} courses={courses} rows={allRows} value={batchFilter} onChange={onBatchFilter} />
          <StatusMenu counts={counts} off={statusOff} onToggle={onStatusToggle} onAll={onStatusAll} />
        </div>
      )}

      {tab === "schedule" ? (
        <div style={{ opacity: loading ? 0.55 : 1, transition: "opacity .2s ease" }}>
          <WeekCalendar
            rows={shown} courses={courses} meta={meta} weekDates={weekDates}
            approved={approved} changed={changed} onOpen={onOpen}
          />
        </div>
      ) : (
        <OverridesList log={overrides} smeName={smeName} onOpen={onOpenOverride} />
      )}
    </section>
  );
}

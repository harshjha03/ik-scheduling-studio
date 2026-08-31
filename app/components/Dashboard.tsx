"use client";
import type { Batch, Category, Course, DraftRow, Meta, OverrideEvent, WeekKey, WeekMeta } from "@/lib/types";
import { CATEGORIES, category, isLive } from "@/lib/view";
import WeekCalendar from "./WeekCalendar";
import OverridesList from "./OverridesList";
import { BatchMenu } from "./FilterMenus";

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
  published: boolean;
  /** the live week has been changed since it was published — only `changedCount` rows are stale */
  amended?: boolean;
  changedCount?: number;
  onRepublish?: () => void;
  overrides: OverrideEvent[];
  loading: boolean;
  vh: number;
  smeName: (id: string | null) => string;
  onTab: (t: "schedule" | "overrides") => void;
  onBatchFilter: (v: string) => void;
  onStatusToggle: (k: Category) => void;
  onOpenWork: () => void;
  onAskCopilot: () => void;
  onApproveWeek: () => void;
  onOpen: (sessionId: string) => void;
  onOpenOverride: (o: OverrideEvent) => void;
}

export default function Dashboard({
  rows, allRows, batches, courses, meta, weeks, week, weekDates, tab, approved, changed, batchFilter, statusOff,
  workCount, published, amended, changedCount = 0, onRepublish, overrides, loading, vh, smeName, onTab, onBatchFilter, onStatusToggle,
  onOpenWork, onAskCopilot, onApproveWeek, onOpen, onOpenOverride,
}: Props) {
  const locked = weeks[week].locked;
  // A cancelled or merged class has no sme_id by definition — nobody teaches a class that is not
  // running. Counting it as unstaffed blocked Approve with no way to clear it, because the only way
  // to clear an unfilled row is to staff it. `category()` has always routed these to "dropped";
  // these two counters were the ones reading the raw field instead. (Matches engine/run.py, which
  // counts `stats.unfilled` over live rows only.)
  const open = allRows.filter(isLive);
  const unfilled = open.filter((r) => !r.sme_id).length;
  const conflicts = open.filter((r) => r.flags.some((f) => f.code === "HARD_CONFLICT")).length;
  const counts = CATEGORIES.reduce((acc, c) => {
    acc[c.key] = rows.filter((r) => category(r, approved.has(r.session_id)) === c.key).length;
    return acc;
  }, {} as Record<Category, number>);
  const shown = rows.filter((r) => !statusOff[category(r, approved.has(r.session_id))]);
  const tight = vh < 680;

  return (
    <section className="card flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-3 p-[16px_20px_14px]" style={{ borderBottom: "1px solid var(--line-2)" }}>
        <div className="tabs">
          <button onClick={() => onTab("schedule")} className={`tab ${tab === "schedule" ? "tab-on" : "tab-off"}`}>Schedule</button>
          <button onClick={() => onTab("overrides")} className={`tab ${tab === "overrides" ? "tab-on" : "tab-off"}`}>
            {overrides.length ? `Overrides ${overrides.length}` : "Overrides"}
          </button>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-[11px]">
          {!locked && unfilled > 0 && (
            <span className="max-w-[26ch] text-right text-[11.5px] font-semibold leading-[1.4]" style={{ color: "var(--red-ink)" }}>
              {unfilled} class{unfilled === 1 ? "" : "es"} still without a teacher
            </span>
          )}
          {/* No manual re-run control: the design has none, and every action that invalidates the
              draft (override, added class, drop-out, availability change) re-runs it on its own. */}
          {!locked && (
            <button className="btn" onClick={onAskCopilot} title="Ask about this week's draft — answers come live from the engine">
              Ask the copilot
            </button>
          )}
          <button className="btn btn-soft flex items-center gap-2" onClick={onOpenWork} title="Unfilled classes, conflicts and workload flags for this week">
            <span>Work items</span>
            <span
              className="rounded-[7px] px-[7px] text-[11px] font-bold tabular-nums"
              style={workCount
                ? { background: "var(--red-tint)", color: "var(--red-ink)" }
                : { background: "#eef1f6", color: "var(--muted-3)" }}
            >
              {workCount}
            </span>
          </button>
          {locked && amended && (
            <button
              className="btn btn-go"
              onClick={onRepublish}
              title={`Re-sends only the ${changedCount} class(es) that changed — the rest of the week is untouched.`}
            >
              Re-publish {changedCount} change{changedCount === 1 ? "" : "s"}
            </button>
          )}
          {!locked && !published && (
            <button
              className="btn btn-go"
              onClick={onApproveWeek}
              disabled={unfilled > 0 || conflicts > 0}
              title={unfilled
                ? `${unfilled} class(es) still have no teacher — clear them from Work items first.`
                : conflicts
                  ? `${conflicts} class(es) have a double-booked teacher — resolve them from Work items first.`
                  : `Publishes all ${allRows.length} classes to learner and SME calendars.`}
            >
              Approve week · {allRows.length}
            </button>
          )}
        </div>
      </div>

      {tab === "schedule" && (
        <div className="relative flex shrink-0 flex-wrap items-center gap-[9px] p-[12px_20px]" style={{ borderBottom: "0.5px solid rgba(16,26,51,0.06)" }}>
          <BatchMenu batches={batches} courses={courses} rows={allRows} value={batchFilter} onChange={onBatchFilter} />
        </div>
      )}

      {tab === "schedule" && (
        <div
          className="flex shrink-0 flex-wrap items-center gap-[7px] px-5"
          style={tight ? { paddingTop: 7, paddingBottom: 0 } : { paddingTop: 11, paddingBottom: 3 }}
        >
          {!tight && (
            <span className="label-caps mr-[3px]" style={{ letterSpacing: "0.06em" }}>Card colour</span>
          )}
          {CATEGORIES.map((c) => {
            const on = !statusOff[c.key];
            return (
              <button
                key={c.key}
                onClick={() => onStatusToggle(c.key)}
                title={`${c.label} — ${c.hint} · click to ${on ? "hide" : "show"}`}
                className="flex items-center gap-[7px] rounded-[9px] px-[9px] py-1 text-[11.5px] font-semibold"
                style={{
                  border: on ? "1px solid #dde4ee" : "1px dashed #c8d2e0",
                  background: on ? "#fff" : "#f6f8fb",
                  color: on ? "var(--ink-3)" : "#5a6880",
                  textDecoration: on ? "none" : "line-through",
                  cursor: "pointer",
                }}
              >
                <span
                  className="size-[11px] shrink-0 rounded-[4px]"
                  style={on ? { background: c.dot } : { background: "transparent", border: `1px dashed ${c.dot}` }}
                />
                {!tight && <span>{c.label}</span>}
                <span className="tabular-nums" style={{ color: "var(--muted-3)" }}>{counts[c.key]}</span>
              </button>
            );
          })}
        </div>
      )}

      {tab === "schedule" ? (
        <div className="flex min-h-0 flex-1 flex-col" style={{ opacity: loading ? 0.55 : 1, transition: "opacity .2s ease" }}>
          <WeekCalendar
            rows={shown} courses={courses} meta={meta} weekDates={weekDates}
            approved={approved} changed={changed} onOpen={onOpen} vh={vh}
          />
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <OverridesList log={overrides} smeName={smeName} onOpen={onOpenOverride} />
        </div>
      )}
    </section>
  );
}

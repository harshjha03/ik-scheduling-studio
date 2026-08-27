"use client";
import type { Batch, Course, DraftRow, Meta, WeekKey, WeekMeta } from "@/lib/types";
import { initials } from "@/lib/view";
import WeekCalendar from "./WeekCalendar";

interface Props {
  batches: Batch[];
  rows: DraftRow[];
  courses: Record<string, Course>;
  meta: Meta;
  weeks: Record<WeekKey, WeekMeta>;
  week: WeekKey;
  weekDates: { day: string; date: string }[];
  approved: Set<string>;
  selected: string;
  vh: number;
  onSelect: (id: string) => void;
  onOpen: (sessionId: string) => void;
  onNewBatch: () => void;
  onNewClass: () => void;
}

const LEVEL_CHIP: Record<string, { bg: string; fg: string }> = {
  beginner: { bg: "var(--green-tint)", fg: "var(--green-ink)" },
  intermediate: { bg: "var(--brand-tint)", fg: "var(--brand-deep)" },
  advanced: { bg: "#e8eff9", fg: "var(--brand-deep)" },
};

export default function BatchManagement({
  batches, rows, courses, meta, weeks, week, weekDates, approved, selected, vh,
  onSelect, onOpen, onNewBatch, onNewClass,
}: Props) {
  const bt = batches.find((b) => b.id === selected) ?? batches[0];
  const btRows = rows.filter((r) => r.batch_id === bt.id);
  const course = courses[bt.course];
  const topics = [...new Map(btRows.filter((r) => r.sub_specialty).map((r) => [r.sub_specialty!, r])).values()].slice(0, 4);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[14px]">
      <div className="flex shrink-0 flex-wrap items-center gap-[10px]">
        <span className="text-[12px]" style={{ color: "var(--muted)" }}>{batches.length} batches · {weeks[week].range}</span>
        <button className="btn btn-brand ml-auto" onClick={onNewBatch}>+ Create new batch</button>
      </div>

      <div className="flex shrink-0 gap-3 overflow-x-auto overflow-y-hidden p-[2px_2px_8px]">
        {batches.map((b) => {
          const bRows = rows.filter((r) => r.batch_id === b.id);
          const unf = bRows.filter((r) => !r.sme_id).length;
          const on = b.id === bt.id;
          const c = courses[b.course];
          const chip = LEVEL_CHIP[b.level];
          return (
            <button
              key={b.id}
              onClick={() => onSelect(b.id)}
              className="block w-[248px] shrink-0 rounded-[18px] p-[16px_17px] text-left transition hover:-translate-y-[2px]"
              style={{
                border: `1px solid ${on ? c?.accent : "var(--line)"}`, background: on ? c?.tint : "#fff",
                cursor: "pointer", boxShadow: "0 1px 2px rgba(16,26,51,0.03)",
              }}
            >
              <span className="flex items-center gap-2">
                <span className="text-[15px] font-bold" style={{ letterSpacing: "-0.01em" }}>{b.id}</span>
                <span className="rounded-[8px] px-2 py-[3px] text-[10px] font-bold capitalize" style={{ background: chip.bg, color: chip.fg }}>
                  {b.level}
                </span>
                {!!unf && (
                  <span className="ml-auto rounded-[8px] px-[7px] py-[2px] text-[10px] font-bold" style={{ background: "var(--red-tint)", color: "var(--red-ink)" }}>
                    {unf} unfilled
                  </span>
                )}
              </span>
              <span className="mt-1 block text-[11.5px]" style={{ color: "var(--muted)" }}>{c?.name}</span>
              <span className="mt-3 flex gap-[14px] text-[11.5px]" style={{ color: "var(--ink-3)" }}>
                <span>{b.learners} learners</span>
                <span>{b.per_week} / week</span>
              </span>
              <span className="relative mt-[11px] block h-[7px] overflow-hidden rounded-[4px]" style={{ background: "#f0edf9" }}>
                <span
                  className="absolute inset-y-0 left-0 rounded-[4px]"
                  style={{ width: `${((b.weeks_done / b.weeks_total) * 100).toFixed(0)}%`, background: c?.accent }}
                />
              </span>
              <span className="mt-[7px] block text-[11px]" style={{ color: "var(--muted)" }}>
                week {b.weeks_done} of {b.weeks_total} · {b.weeks_total - b.weeks_done} weeks left
              </span>
            </button>
          );
        })}
      </div>

      <section className="card flex min-h-0 flex-1 flex-col">
        <div className="flex flex-wrap items-start gap-4 p-[18px_20px_14px]" style={{ borderBottom: "1px solid var(--line-2)" }}>
          <div>
            <div className="flex items-center gap-[9px]">
              <span className="text-[19px] font-bold" style={{ letterSpacing: "-0.02em" }}>{bt.id}</span>
              <span
                className="rounded-[8px] px-2 py-[3px] text-[10px] font-bold capitalize"
                style={{ background: LEVEL_CHIP[bt.level].bg, color: LEVEL_CHIP[bt.level].fg }}
              >
                {bt.level}
              </span>
            </div>
            <div className="mt-[3px] text-[12.5px]" style={{ color: "var(--muted)" }}>
              {course?.name} · {bt.learners} learners · {bt.per_week} classes a week · started {bt.started}
            </div>
            <button
              className="btn btn-soft mt-[11px] inline-flex items-center gap-[7px]"
              onClick={onNewClass}
              title="Add a class to this batch and pick a teacher who is free"
            >
              <span className="text-[14px] leading-none">+</span><span>Add a class</span>
            </button>
          </div>
          <div className="ml-auto grid min-w-[340px] flex-1 gap-3" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(132px,1fr))" }}>
            <div className="rounded-[14px] p-[11px_13px]" style={{ background: "#f1f5fa" }}>
              <div className="label-caps">Course left</div>
              <div className="mt-[5px] text-[15px] font-bold">{bt.weeks_total - bt.weeks_done} weeks</div>
              <div className="mt-1 text-[11px]" style={{ color: "var(--ink-3)" }}>week {bt.weeks_done} of {bt.weeks_total} done</div>
            </div>
            <div className="rounded-[14px] p-[11px_13px]" style={{ background: "var(--brand-tint)" }}>
              <div className="label-caps" style={{ color: "#5b7fae" }}>Classes needed</div>
              <div className="mt-[5px] text-[15px] font-bold" style={{ color: "var(--brand-deep)" }}>{bt.per_week} / week</div>
              <div className="mt-1 text-[11px]" style={{ color: "#5b7fae" }}>
                {btRows.length} scheduled{btRows.length < bt.per_week ? ` — short by ${bt.per_week - btRows.length}` : ""}
              </div>
            </div>
            <div className="rounded-[14px] p-[11px_13px]" style={{ background: "var(--green-tint)" }}>
              <div className="label-caps" style={{ color: "#3c7a62" }}>Learners</div>
              <div className="mt-[5px] text-[15px] font-bold" style={{ color: "var(--green-ink)" }}>{bt.learners}</div>
              <div className="mt-1 text-[11px]" style={{ color: "#3c7a62" }}>started {bt.started}</div>
            </div>
          </div>
        </div>

        <div className="p-[14px_20px]" style={{ borderBottom: "1px solid var(--line-2)" }}>
          <div className="label-caps mb-[10px]">Running topics &amp; assigned SMEs</div>
          <div className="grid gap-[10px]" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(230px,1fr))" }}>
            {topics.map((r) => (
              <div key={r.sub_specialty} className="rounded-[14px] p-[12px_13px]" style={{ background: course?.tint }}>
                <div className="text-[12.5px] font-semibold">{r.sub_specialty}</div>
                <div className="mt-[7px] flex items-center gap-[7px]">
                  <span className="flex size-[22px] items-center justify-center rounded-full bg-white text-[9.5px] font-bold" style={{ color: "var(--ink-2)" }}>
                    {r.sme_name ? initials(r.sme_name) : "—"}
                  </span>
                  <span className="text-[11.5px]" style={{ color: "var(--ink-2)", fontWeight: 550 }}>{r.sme_name ?? "Unfilled"}</span>
                  <span className="ml-auto text-[10.5px]" style={{ color: "var(--muted)" }}>
                    {btRows.filter((x) => x.sub_specialty === r.sub_specialty).length} class(es)
                  </span>
                </div>
              </div>
            ))}
            {!topics.length && <div className="text-[12px]" style={{ color: "var(--muted)" }}>No topic classes scheduled this week.</div>}
          </div>
        </div>

        <WeekCalendar rows={btRows} courses={courses} meta={meta} weekDates={weekDates} approved={approved} onOpen={onOpen} vh={vh} />
      </section>
    </div>
  );
}

"use client";
import { useMemo } from "react";
import type { Course, DraftRow, Meta, PastWeek, Session, SME } from "@/lib/types";
import { avatarBg, initials, isLive, istParts, liveRows } from "@/lib/view";
import WeekCalendar from "./WeekCalendar";

interface Props {
  weeks: PastWeek[];
  selected: string;
  sessions: Record<string, Session[]>;
  smes: SME[];
  courses: Record<string, Course>;
  meta: Meta;
  vh: number;
  onSelect: (iso: string) => void;
}

/** A settled week's sessions in the shape the calendar reads. No pipeline runs here: these rows
 *  already carry the teacher who actually took the class, and re-drafting the past would invent a
 *  history nobody lived. */
function asRows(sessions: Session[], smes: SME[]): DraftRow[] {
  const byId = new Map(smes.map((s) => [s.id, s]));
  return sessions.map((x) => ({
    session_id: x.id, batch_id: x.batch_id, subject: x.subject, sub_specialty: x.sub_specialty,
    type: x.type, start_utc: x.start_utc, duration_min: x.duration_min, mode: x.mode,
    required_training_level: x.required_training_level,
    cancelled: x.cancelled ?? null, merged_into: x.merged_into ?? null, merged_batches: x.merged_batches ?? null,
    sme_id: x.sme_id ?? null, sme_name: x.sme_name ?? byId.get(x.sme_id ?? "")?.name ?? null,
    score: null, score_now: null, components: null, stage: null,
    flags: [], candidates: [], eliminated: [], adjusted_from_override: false, override_effect: null,
  }));
}

const Stat = ({ n, label, tone }: { n: number; label: string; tone?: "red" | "muted" }) => (
  <div className="rounded-[14px] p-[11px_13px]" style={{ border: "1px solid var(--line)" }}>
    <div className="text-[21px] font-bold leading-none"
      style={{ color: tone === "red" ? "var(--red-ink)" : tone === "muted" ? "var(--muted)" : "var(--ink)" }}>
      {n}
    </div>
    <div className="mt-[5px] text-[11px]" style={{ color: "var(--muted)" }}>{label}</div>
  </div>
);

export default function PastWeeks({ weeks, selected, sessions, smes, courses, meta, vh, onSelect }: Props) {
  const week = weeks.find((w) => w.iso === selected) ?? weeks[weeks.length - 1];
  const rows = useMemo(() => asRows(sessions[week?.iso] ?? [], smes), [sessions, week, smes]);

  const taught = liveRows(rows).filter((r) => r.sme_id);
  const cancelled = rows.filter((r) => r.cancelled);
  const merged = rows.filter((r) => r.merged_into);

  // one line per teacher: what they actually carried that week
  const perSme = useMemo(() => {
    const acc = new Map<string, { sme: SME; count: number; batches: Set<string> }>();
    taught.forEach((r) => {
      const sme = smes.find((s) => s.id === r.sme_id);
      if (!sme) return;
      const e = acc.get(sme.id) ?? { sme, count: 0, batches: new Set<string>() };
      e.count += 1;
      e.batches.add(r.batch_id);
      acc.set(sme.id, e);
    });
    return [...acc.values()].sort((a, b) => b.count - a.count || a.sme.name.localeCompare(b.sme.name));
  }, [taught, smes]);

  const weekDates = useMemo(() => {
    const first = [...rows].sort((a, b) => a.start_utc.localeCompare(b.start_utc))[0]?.start_utc;
    if (!first) return meta.days.map((d) => ({ day: d, date: "" }));
    const p0 = istParts(first);
    const base = new Date(new Date(first).getTime() - p0.day * 864e5);
    return meta.days.map((d, i) => ({ day: d, date: istParts(new Date(base.getTime() + i * 864e5).toISOString()).date }));
  }, [rows, meta.days]);

  if (!week) return null;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[14px]">
      <section className="card p-[12px_20px_14px]">
        <div className="flex flex-wrap items-center gap-[9px]">
          <span className="whitespace-nowrap text-[13px] font-bold">Past weeks</span>
          <span className="text-[11.5px]" style={{ color: "var(--muted-2)" }}>
            What actually ran. Fairness is scored on the last three of these plus the current draft.
          </span>
          <div className="ml-auto flex flex-wrap gap-[6px]">
            {weeks.map((w) => (
              <button
                key={w.iso} className="btn btn-sm" onClick={() => onSelect(w.iso)}
                title={w.range}
                style={w.iso === week.iso
                  ? { background: "var(--brand-tint)", color: "var(--brand-deep)", fontWeight: 650, borderColor: "var(--brand-line, var(--line))" }
                  : undefined}
              >
                {w.label}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-[12px] grid gap-[9px]" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(118px,1fr))" }}>
          <Stat n={taught.length} label={`classes run · ${week.range}`} />
          <Stat n={cancelled.length} label="cancelled" tone={cancelled.length ? "red" : undefined} />
          <Stat n={merged.length} label="merged into another batch" tone="muted" />
          <Stat n={perSme.length} label="teachers who taught" />
          <Stat n={new Set(taught.map((r) => r.batch_id)).size} label="batches running" />
        </div>
        {!!cancelled.length && (
          <div className="mt-[11px] flex flex-col gap-[5px]">
            {cancelled.map((r) => (
              <div key={r.session_id} className="rounded-[12px] p-[9px_12px] text-[12px] leading-[1.5]"
                style={{ background: "var(--red-tint)", color: "var(--red-ink)" }}>
                <b>{r.batch_id} · {r.sub_specialty ?? meta.type_label[r.type]}</b>
                {" — "}{r.cancelled?.reason ?? "cancelled"}
              </div>
            ))}
          </div>
        )}
      </section>

      <div className="flex min-h-0 flex-1 gap-[14px]">
        <section className="card flex min-h-0 flex-[2] flex-col overflow-hidden">
          <WeekCalendar
            rows={rows} courses={courses} meta={meta} weekDates={weekDates}
            approved={new Set()} onOpen={() => {}} vh={vh}
          />
        </section>
        <section className="card flex min-h-0 w-[292px] shrink-0 flex-col">
          <div className="p-[12px_16px_10px]" style={{ borderBottom: "0.5px solid rgba(16,26,51,0.06)" }}>
            <div className="text-[13px] font-bold">Who taught what</div>
            <div className="mt-[2px] text-[11px]" style={{ color: "var(--muted)" }}>
              {taught.length} classes across {perSme.length} teachers
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-[8px_10px]">
            {perSme.map(({ sme, count, batches }) => (
              <div key={sme.id} className="flex items-center gap-[9px] p-[7px_6px]">
                <span className="grid size-[27px] shrink-0 place-items-center rounded-full text-[10.5px] font-bold"
                  style={{ background: avatarBg(sme.id), color: "var(--brand-deep)" }}>
                  {initials(sme.name)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block overflow-hidden text-ellipsis whitespace-nowrap text-[12.5px] font-semibold">{sme.name}</span>
                  <span className="block overflow-hidden text-ellipsis whitespace-nowrap text-[11px]" style={{ color: "var(--muted)" }}>
                    {[...batches].sort().join(" · ")}
                  </span>
                </span>
                <span className="shrink-0 text-[12.5px] font-bold">{count}</span>
              </div>
            ))}
            {!perSme.length && (
              <div className="p-3 text-[12px]" style={{ color: "var(--muted)" }}>Nothing ran that week.</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

export { asRows, isLive };

"use client";
import type { Course, DraftRow, Meta, SME, WeekKey, WeekMeta } from "@/lib/types";
import { avatarBg, initials, istParts, isAvailable, poolMean, smeWeekStats } from "@/lib/view";
import WeekCalendar, { type GhostRow } from "./WeekCalendar";

interface Props {
  smes: SME[];
  rows: DraftRow[];
  courses: Record<string, Course>;
  meta: Meta;
  weeks: Record<WeekKey, WeekMeta>;
  week: WeekKey;
  weekDates: { day: string; date: string }[];
  approved: Set<string>;
  selected: string;
  leave: Record<string, string>;
  onSelect: (id: string) => void;
  onWeek: (w: WeekKey) => void;
  onOpen: (sessionId: string) => void;
  onGhost: (sessionId: string) => void;
  onToggleLeave: (smeId: string) => void;
  onDropOut: (smeId: string) => void;
}

const LEVEL_PCT: Record<string, number> = { beginner: 30, intermediate: 62, advanced: 100 };
const LEVEL_CHIP: Record<string, { bg: string; fg: string }> = {
  beginner: { bg: "var(--green-tint)", fg: "var(--green-ink)" },
  intermediate: { bg: "var(--brand-tint)", fg: "var(--brand-deep)" },
  advanced: { bg: "#e8eff9", fg: "var(--brand-deep)" },
};

export default function SmeManagement({
  smes, rows, courses, meta, weeks, week, weekDates, approved, selected, leave,
  onSelect, onWeek, onOpen, onGhost, onToggleLeave, onDropOut,
}: Props) {
  const sel = smes.find((s) => s.id === selected) ?? smes[0];
  const selRows = rows.filter((r) => r.sme_id === sel.id);
  const [h0, h1] = meta.hours;
  const ref = rows[0]?.start_utc;

  const freeCells = new Set<string>();
  if (ref) {
    for (let d = 0; d < 6; d++) {
      for (let h = h0; h < h1; h++) {
        if (isAvailable(sel, ref, d, h)) freeCells.add(`${d}-${h}`);
      }
    }
  }
  const busy = new Set(selRows.map((r) => { const p = istParts(r.start_utc); return `${p.day}-${p.hour}`; }));
  const ghosts: GhostRow[] = rows
    .filter((r) => !r.sme_id)
    .filter((r) => sel.subjects.includes(r.subject))
    .filter((r) => !r.sub_specialty || sel.topics.includes(r.sub_specialty))
    .filter((r) => sel.training_level >= r.required_training_level)
    .filter((r) => { const p = istParts(r.start_utc); return freeCells.has(`${p.day}-${p.hour}`) && !busy.has(`${p.day}-${p.hour}`); })
    .map((r) => ({ session_id: r.session_id, batch_id: r.batch_id, subject: r.subject, sub_specialty: r.sub_specialty, type: r.type, start_utc: r.start_utc }));

  return (
    <div className="flex flex-col gap-[14px]">
      <section className="card">
        <div className="flex items-center gap-[10px] p-[15px_20px_13px]" style={{ borderBottom: "0.5px solid rgba(16,26,51,0.06)" }}>
          <span className="text-[13px] font-bold">
            SME glossary <span style={{ color: "var(--muted-2)", fontWeight: 500 }}>({smes.length})</span>
          </span>
          <span className="ml-auto text-[11.5px]" style={{ color: "var(--muted)" }}>
            Click a row to load their availability &amp; history below
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1040px] border-collapse">
            <thead>
              <tr className="label-caps text-left">
                <th className="p-[9px_20px] font-semibold">SME</th>
                <th className="p-[9px_10px] font-semibold">Skills / topics</th>
                <th className="w-[170px] p-[9px_10px] font-semibold">Level</th>
                <th className="w-[92px] p-[9px_10px] font-semibold">Rating</th>
                <th className="w-[170px] p-[9px_10px] font-semibold">This week</th>
                <th className="w-[196px] p-[9px_20px] font-semibold">Leave</th>
              </tr>
            </thead>
            <tbody>
              {smes.map((s) => {
                const st = smeWeekStats(s, rows);
                const on = s.id === sel.id;
                const mean = poolMean(smes, s.subjects[0], rows);
                const td = { padding: "11px 10px", verticalAlign: "top" as const, borderTop: "0.5px solid rgba(16,26,51,0.06)" };
                const chip = LEVEL_CHIP[s.level];
                return (
                  <tr
                    key={s.id}
                    onClick={() => onSelect(s.id)}
                    className="cursor-pointer hover:bg-[rgba(47,95,208,0.05)]"
                    style={{ background: on ? "rgba(47,95,208,0.06)" : "transparent", boxShadow: on ? "inset 3px 0 0 var(--brand)" : "none" }}
                  >
                    <td style={{ ...td, paddingLeft: 20 }}>
                      <div className="flex items-center gap-[9px]">
                        <span
                          className="flex size-[30px] shrink-0 items-center justify-center rounded-full text-[10.5px] font-bold"
                          style={{ background: avatarBg(s.id), color: "var(--ink-2)" }}
                        >
                          {initials(s.name)}
                        </span>
                        <span className="min-w-0">
                          <span className="block whitespace-nowrap text-[12.5px] font-semibold">{s.name}</span>
                          <span className="mt-px block whitespace-nowrap text-[10.5px]" style={{ color: "var(--muted-2)" }}>
                            {s.id} · {s.subjects.join(" + ")} · {s.city}
                          </span>
                        </span>
                      </div>
                    </td>
                    <td style={td}>
                      <div className="flex flex-wrap gap-1">
                        {s.topics.map((t) => (
                          <span key={t} className="rounded-[8px] px-2 py-[3px] text-[10.5px]" style={{ background: "#f1f5fa", color: "#42506b", fontWeight: 550 }}>
                            {t}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td style={td}>
                      <span
                        className="rounded-[8px] px-2 py-[3px] text-[10px] font-bold capitalize"
                        style={{ background: chip.bg, color: chip.fg }}
                      >
                        {s.level}
                      </span>
                      <div className="relative mt-[7px] h-[5px] overflow-hidden rounded-[3px]" style={{ background: "var(--line-2)" }}>
                        <span className="absolute inset-y-0 left-0 rounded-[3px]" style={{ width: `${LEVEL_PCT[s.level]}%`, background: "var(--brand)" }} />
                      </div>
                      <div className="mt-[5px] text-[10.5px] leading-[1.35]" style={{ color: "var(--muted)" }}>
                        {s.level === "advanced" ? "top level · mocks & advanced batches" : `${s.to_upgrade} classes to next level`}
                      </div>
                    </td>
                    <td style={td}>
                      <div className="text-[14px] font-bold" style={{ color: "var(--green-ink)" }}>★ {s.rating.toFixed(1)}</div>
                      <div className="mt-[3px] text-[10.5px]" style={{ color: "var(--muted)" }}>monthly form</div>
                    </td>
                    <td style={td}>
                      <div className="text-[12.5px] font-semibold">{st.assigned} of {s.preferred} preferred</div>
                      <div className="relative mt-[7px] h-[5px] overflow-hidden rounded-[3px]" style={{ background: "var(--line-2)" }}>
                        <span
                          className="absolute inset-y-0 left-0 rounded-[3px]"
                          style={{ width: `${Math.min(100, (st.assigned / s.preferred) * 100).toFixed(0)}%`, background: st.over ? "var(--red)" : "var(--green)" }}
                        />
                      </div>
                      <div className="mt-[5px] text-[10.5px] leading-[1.35]" style={{ color: "var(--muted)" }}>
                        {st.over ? `${st.assigned - s.preferred} above preference · ` : ""}4-wk load {st.load4w} · pool mean {mean.toFixed(1)}
                      </div>
                    </td>
                    <td style={{ ...td, paddingRight: 20 }} onClick={(e) => e.stopPropagation()}>
                      <span
                        className="inline-block rounded-[9px] px-[9px] py-[5px] text-[10.5px] font-semibold leading-[1.35]"
                        style={leave[s.id]
                          ? { background: "var(--sand-tint)", color: "var(--sand-ink)" }
                          : { background: "var(--green-tint)", color: "var(--green-ink)" }}
                      >
                        {leave[s.id] ?? "Available"}
                      </span>
                      <div className="mt-2 flex flex-wrap gap-1">
                        <button className="btn btn-sm" onClick={() => onToggleLeave(s.id)}>
                          {leave[s.id] ? "Clear leave" : "Mark on leave"}
                        </button>
                        {leave[s.id] && (
                          <button className="btn btn-sm" onClick={() => onDropOut(s.id)} title="Re-run the draft with this SME unavailable all week">
                            Re-run without them
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <div className="flex flex-wrap items-center gap-3 p-[15px_20px_13px]" style={{ borderBottom: "1px solid var(--line-2)" }}>
          <div>
            <div className="text-[13px] font-bold">Availability &amp; assignment history</div>
            <div className="mt-[2px] text-[11.5px]" style={{ color: "var(--muted)" }}>
              {sel.name} · {selRows.length} assigned this week · green blocks are free working hours
              {ghosts.length ? ` · ${ghosts.length} unfilled class(es) fit in them` : ""}
            </div>
          </div>
          <div className="tabs ml-auto">
            {(["current", "next"] as WeekKey[]).map((k) => (
              <button key={k} onClick={() => onWeek(k)} className={`tab ${week === k ? "tab-on" : "tab-off"}`}>
                {weeks[k].label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap gap-[14px] p-[10px_20px_8px] text-[11.5px]" style={{ color: "var(--ink-3)" }}>
          <span>
            <span className="mr-[6px] inline-block size-[10px] rounded-[3px]" style={{ background: "#e6f2ec", border: "1px solid var(--green-line)" }} />
            free working hour
          </span>
          <span>
            <span className="mr-[6px] inline-block size-[10px] rounded-[3px]" style={{ background: "#fff", border: "1px solid var(--field)", borderLeft: "3px solid #4a7fd0" }} />
            assigned class
          </span>
          <span>
            <span className="mr-[6px] inline-block size-[10px] rounded-[3px]" style={{ background: "var(--sand-tint)", border: "1px dashed var(--amber)" }} />
            unfilled class that fits here — click to assign
          </span>
        </div>
        <WeekCalendar
          rows={selRows} courses={courses} meta={meta} weekDates={weekDates} approved={approved}
          onOpen={onOpen} freeCells={freeCells} ghosts={ghosts} onGhost={onGhost} showSme={false}
        />
      </section>
    </div>
  );
}

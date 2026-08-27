"use client";
import type { Course, DraftRow, Meta, SessionType } from "@/lib/types";
import { avatarBg, hhmm, istParts, topFlag, FLAG_LABEL, SEV_CHIP } from "@/lib/view";

const ROW_H = 68;

export interface GhostRow {
  session_id: string;
  batch_id: string;
  subject: string;
  sub_specialty: string | null;
  type: SessionType;
  start_utc: string;
}

interface Props {
  rows: DraftRow[];
  courses: Record<string, Course>;
  meta: Meta;
  weekDates: { day: string; date: string }[];
  approved: Set<string>;
  changed?: Set<string>;
  onOpen: (sessionId: string) => void;
  /** free working hours to shade (SME view): set of `${day}-${hour}` */
  freeCells?: Set<string>;
  /** unfilled sessions that would fit this SME — rendered as dashed "take this class" cards */
  ghosts?: GhostRow[];
  onGhost?: (sessionId: string) => void;
  showSme?: boolean;
}

export default function WeekCalendar({
  rows, courses, meta, weekDates, approved, changed, onOpen, freeCells, ghosts = [], onGhost, showSme = true,
}: Props) {
  const [h0, h1] = meta.hours;
  const height = (h1 - h0) * ROW_H;

  const byDay: Record<number, { row?: DraftRow; ghost?: GhostRow; hour: number }[]> = {};
  rows.forEach((r) => {
    const p = istParts(r.start_utc);
    (byDay[p.day] = byDay[p.day] || []).push({ row: r, hour: p.hour });
  });
  ghosts.forEach((g) => {
    const p = istParts(g.start_utc);
    (byDay[p.day] = byDay[p.day] || []).push({ ghost: g, hour: p.hour });
  });

  return (
    <div className="overflow-x-auto px-5 pb-[22px] pt-[14px]">
      <div className="grid min-w-[920px]" style={{ gridTemplateColumns: "58px repeat(6,minmax(0,1fr))" }}>
        <div />
        {weekDates.map((h) => (
          <div key={h.day} className="px-[6px] pb-[10px] text-center">
            <div className="text-[12.5px] font-bold">{h.day}</div>
            <div className="mt-px text-[11px]" style={{ color: "var(--muted-2)" }}>{h.date}</div>
          </div>
        ))}

        <div className="relative" style={{ height }}>
          {Array.from({ length: h1 - h0 + 1 }, (_, i) => h0 + i).map((h) => (
            <div
              key={h}
              className="absolute right-[10px] text-[10.5px]"
              style={{ top: (h - h0) * ROW_H, color: "var(--muted-2)", transform: "translateY(-6px)" }}
            >
              {hhmm(h)}
            </div>
          ))}
        </div>

        {meta.days.map((_, di) => {
          const items = (byDay[di] ?? []).slice().sort((a, b) => a.hour - b.hour);
          const groups: Record<number, typeof items> = {};
          items.forEach((it) => { (groups[it.hour] = groups[it.hour] || []).push(it); });
          return (
            <div key={di} className="relative" style={{ height, borderLeft: "1px solid var(--line-2)" }}>
              {Array.from({ length: h1 - h0 + 1 }, (_, i) => h0 + i).map((h) => (
                <div
                  key={h}
                  className="absolute inset-x-0"
                  style={{ top: (h - h0) * ROW_H, height: 1, background: h % 2 ? "#faf9fd" : "var(--line-2)" }}
                />
              ))}
              {freeCells &&
                Array.from({ length: h1 - h0 }, (_, i) => h0 + i)
                  .filter((h) => freeCells.has(`${di}-${h}`) && !items.some((it) => it.hour === h))
                  .map((h) => (
                    <div
                      key={`f${h}`}
                      className="absolute rounded-[11px]"
                      style={{
                        left: 3, right: 3, top: (h - h0) * ROW_H + 3, height: ROW_H - 6,
                        background: "#eaf5ef", border: "1px solid var(--green-line)",
                      }}
                    />
                  ))}
              {Object.values(groups).flatMap((arr) =>
                arr.map((it, idx) => {
                  const total = arr.length;
                  const width = total > 1 ? `calc(${(100 / total).toFixed(2)}% - 5px)` : "calc(100% - 8px)";
                  const left = total > 1 ? `calc(${((idx * 100) / total).toFixed(2)}% + 4px)` : "4px";
                  const top = (it.hour - h0) * ROW_H + 3;
                  const course = courses[(it.row ?? it.ghost)!.subject];

                  if (it.ghost) {
                    const g = it.ghost;
                    return (
                      <button
                        key={`${g.session_id}-g`}
                        onClick={() => onGhost?.(g.session_id)}
                        title={`${g.batch_id} · ${g.sub_specialty ?? meta.type_label[g.type]} · needs a teacher`}
                        className="absolute overflow-hidden rounded-[12px] px-2 py-[5px] text-left"
                        style={{
                          left, width, top, height: ROW_H - 6, background: "var(--sand-tint)",
                          border: "1px dashed var(--amber)", borderLeft: "3px solid var(--amber)", cursor: "pointer",
                        }}
                      >
                        <span className="flex items-center gap-[5px]">
                          <span className="text-[10px] font-bold" style={{ color: course?.deep }}>{g.batch_id}</span>
                          <span className="chip chip-medium">open</span>
                        </span>
                        <span className="mt-[2px] block overflow-hidden text-ellipsis whitespace-nowrap text-[11.5px] font-semibold leading-[1.3]">
                          Needs a teacher · {g.sub_specialty ?? meta.type_label[g.type]}
                        </span>
                        <span className="block text-[10.5px] font-semibold" style={{ color: "var(--red-ink)" }}>
                          Click to take this class
                        </span>
                      </button>
                    );
                  }

                  const r = it.row!;
                  const flag = topFlag(r);
                  const unfilled = !r.sme_id;
                  const isApproved = approved.has(r.session_id);
                  const medium = flag && (flag.severity === "medium" || flag.severity === "high");
                  const bg = unfilled ? "var(--red-tint)" : isApproved ? "var(--green-tint)" : medium ? "#fdf9ef" : "#fff";
                  const border = unfilled
                    ? "1px solid var(--red-line)"
                    : isApproved ? "1px solid var(--green-line)" : medium ? "1px solid var(--amber-line)" : "1px solid #ece9f5";
                  const accent = unfilled ? "var(--red)" : isApproved ? "var(--green)" : course?.accent ?? "var(--brand)";
                  return (
                    <button
                      key={r.session_id}
                      onClick={() => onOpen(r.session_id)}
                      title={`${r.batch_id} · ${r.sub_specialty ?? meta.type_label[r.type]} · ${hhmm(it.hour)} IST · ${r.sme_name ?? "unfilled"}`}
                      className="absolute overflow-hidden rounded-[12px] px-2 py-[5px] text-left"
                      style={{
                        left, width, top, height: ROW_H - 6, background: bg, border,
                        borderLeft: `3px solid ${accent}`, cursor: "pointer",
                        boxShadow: unfilled ? "0 2px 10px rgba(192,57,43,0.16)" : "0 1px 2px rgba(16,26,51,0.05)",
                      }}
                    >
                      <span className="flex min-w-0 flex-nowrap items-center gap-[5px] overflow-hidden">
                        <span className="text-[10px] font-bold" style={{ color: course?.deep, letterSpacing: "0.02em" }}>
                          {r.batch_id}
                        </span>
                        {flag && total === 1 && (
                          <span className={`chip ${SEV_CHIP[flag.severity]}`}>{FLAG_LABEL[flag.code]}</span>
                        )}
                        {changed?.has(r.session_id) && (
                          <span title="changed since last run" className="size-[7px] shrink-0 rounded-full" style={{ background: "#d18b3c" }} />
                        )}
                        {isApproved && <span className="ml-auto text-[10px] font-bold" style={{ color: "var(--green-ink)" }}>✓</span>}
                      </span>
                      <span className="mt-[2px] block overflow-hidden text-ellipsis whitespace-nowrap text-[11.5px] font-semibold leading-[1.3]">
                        {r.sub_specialty ?? meta.type_label[r.type]}
                      </span>
                      {showSme && (
                        <span
                          className="mt-px block overflow-hidden text-ellipsis whitespace-nowrap text-[10.5px]"
                          style={unfilled ? { color: "var(--red-ink)", fontWeight: 650 } : { color: "var(--ink-3)" }}
                        >
                          {r.sme_name ?? "Unfilled — needs a teacher"}
                        </span>
                      )}
                    </button>
                  );
                }),
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export { ROW_H, avatarBg };

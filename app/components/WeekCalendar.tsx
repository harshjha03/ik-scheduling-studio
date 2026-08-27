"use client";
import type { CSSProperties } from "react";
import type { Course, DraftRow, Meta, SessionType } from "@/lib/types";
import { accentBorder, hhmm, istParts, topFlag, FLAG_LABEL, SEV_CHIP } from "@/lib/view";

const ROW_H = 84;      // tallest an hour row is allowed to get
const MIN_ROW = 40;    // shortest, on a very short viewport

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
  /** unfilled classes that would fit this SME — dashed "take this class" cards */
  ghosts?: GhostRow[];
  onGhost?: (sessionId: string) => void;
  showSme?: boolean;
  /** viewport height, so the week always fits one screen */
  vh?: number;
}

type Item = { hour: number; day: number; row?: DraftRow; ghost?: GhostRow };

/**
 * Compressed hour axis: only hours that actually hold something get a row, so a whole week fits
 * one screen. A jump in the axis is marked with a dashed rule and a `⋯` on the label.
 */
export default function WeekCalendar({
  rows, courses, meta, weekDates, approved, changed, onOpen, freeCells, ghosts = [], onGhost, showSme = true, vh = 900,
}: Props) {
  const [H0, H1] = meta.hours;
  const items: Item[] = [
    ...rows.map((r) => { const p = istParts(r.start_utc); return { hour: p.hour, day: p.day, row: r }; }),
    ...ghosts.map((g) => { const p = istParts(g.start_utc); return { hour: p.hour, day: p.day, ghost: g }; }),
  ];

  // which hours the axis shows
  const used = new Set<number>(items.map((i) => i.hour));
  if (freeCells) freeCells.forEach((k) => used.add(Number(k.split("-")[1])));
  let lo = H0;
  let hi = H1;
  if (items.length) {
    const hs = items.map((i) => i.hour);
    lo = Math.max(H0, Math.min(...hs));
    hi = Math.min(H1, Math.max(...hs) + 1);
    if (hi - lo < 4) hi = Math.min(H1, lo + 4);
  }
  const list = [...used].sort((a, b) => a - b).filter((h) => h >= lo && h <= hi);
  if (list.length < 4) {
    for (let h = lo; h < lo + 4 && h <= H1; h++) if (!list.includes(h)) list.push(h);
    list.sort((a, b) => a - b);
  }
  const n = Math.max(1, list.length);
  const at = new Map(list.map((h, i) => [h, i]));
  const pct = 100 / n;
  const minRow = vh >= 640 ? 58 : MIN_ROW;
  const colStyle: CSSProperties = { height: "100%", minHeight: n * minRow, maxHeight: n * ROW_H };
  const gapAt = (i: number) => i > 0 && list[i] - list[i - 1] > 1;

  const byDay = new Map<number, Item[]>();
  items.forEach((it) => byDay.set(it.day, [...(byDay.get(it.day) ?? []), it]));

  return (
    <div className="flex-1 overflow-auto px-5 pb-[14px]" data-cal-pane="1">
      <div
        className="grid min-h-full min-w-[920px]"
        style={{ gridTemplateColumns: "58px repeat(6,minmax(0,1fr))", gridTemplateRows: "auto minmax(0,1fr)" }}
      >
        <div className="sticky left-0 top-0 z-[4] bg-white" />
        {weekDates.map((h) => (
          <div key={h.day} className="sticky top-0 z-[3] bg-white px-[6px] pb-[10px] pt-[6px] text-center">
            <div className="text-[12.5px] font-bold">{h.day}</div>
            <div className="mt-px text-[11px]" style={{ color: "var(--muted-2)" }}>{h.date}</div>
          </div>
        ))}

        <div className="sticky left-0 z-[2] bg-white" style={colStyle}>
          {list.map((h, i) => (
            <div
              key={h}
              className="absolute right-[10px] whitespace-nowrap text-[10.5px]"
              style={{
                top: `${(i * pct).toFixed(4)}%`, transform: "translateY(-1px)",
                color: gapAt(i) ? "var(--muted)" : "var(--muted-2)", fontWeight: gapAt(i) ? 650 : 400,
              }}
            >
              {gapAt(i) ? "⋯ " : ""}{hhmm(h)}
            </div>
          ))}
        </div>

        {meta.days.map((_, di) => {
          const mine = (byDay.get(di) ?? []).slice().sort((a, b) => a.hour - b.hour);
          const groups = new Map<number, Item[]>();
          mine.forEach((it) => groups.set(it.hour, [...(groups.get(it.hour) ?? []), it]));
          return (
            <div key={di} className="relative" style={{ ...colStyle, borderLeft: "1px solid var(--line-2)" }}>
              {list.map((h, i) => (
                <div
                  key={h}
                  className="absolute inset-x-0"
                  style={{
                    top: `${(i * pct).toFixed(4)}%`, height: 0,
                    borderTop: gapAt(i) ? "1px dashed #c3cede" : "1px solid var(--line-2)",
                  }}
                />
              ))}

              {freeCells && list.filter((h) => freeCells.has(`${di}-${h}`) && !mine.some((it) => it.hour === h)).map((h) => (
                <div
                  key={`f${h}`}
                  className="absolute rounded-[11px]"
                  style={{
                    left: 3, right: 3, top: `calc(${((at.get(h) ?? 0) * pct).toFixed(4)}% + 3px)`,
                    height: `calc(${pct.toFixed(4)}% - 6px)`, background: "#eaf5ef", border: "1px solid var(--green-line)",
                  }}
                />
              ))}

              {[...groups.values()].flatMap((arr) => arr.map((it, idx) => {
                const total = arr.length;
                const width = total > 1 ? `calc(${(100 / total).toFixed(2)}% - 5px)` : "calc(100% - 8px)";
                const left = total > 1 ? `calc(${((idx * 100) / total).toFixed(2)}% + 4px)` : "4px";
                const top = `calc(${((at.get(it.hour) ?? 0) * pct).toFixed(4)}% + 3px)`;
                const height = `calc(${pct.toFixed(4)}% - 6px)`;
                const subject = (it.row ?? it.ghost)!.subject;
                const course = courses[subject];
                const base: CSSProperties = {
                  position: "absolute", left, width, top, height, padding: "2px 7px", borderRadius: 10,
                  boxShadow: "0 1px 2px rgba(16,26,51,0.05)",
                };

                if (it.ghost) {
                  const g = it.ghost;
                  return (
                    <button
                      key={`${g.session_id}-g`}
                      className="cal-card"
                      onClick={() => onGhost?.(g.session_id)}
                      title={`${g.batch_id} · ${g.sub_specialty ?? meta.type_label[g.type]} · needs a teacher`}
                      style={{
                        ...base, background: "var(--sand-tint)",
                        ...accentBorder("1px dashed var(--amber)", "3px solid var(--amber)"),
                      }}
                    >
                      <span className="flex w-full flex-nowrap items-center gap-[5px] overflow-hidden whitespace-nowrap">
                        <span className="overflow-hidden text-ellipsis text-[10px] font-bold" style={{ color: course?.deep }}>{g.batch_id}</span>
                        <span className="chip chip-medium cal-chip">open</span>
                      </span>
                      <span className="block overflow-hidden text-ellipsis whitespace-nowrap text-[10.5px] font-semibold leading-[1.15]">
                        Needs a teacher · {g.sub_specialty ?? meta.type_label[g.type]}
                      </span>
                      <span className="cal-sme block overflow-hidden text-ellipsis whitespace-nowrap text-[10.5px] font-semibold leading-[1.3]" style={{ color: "var(--red-ink)" }}>
                        Click to take this class
                      </span>
                    </button>
                  );
                }

                const r = it.row!;
                const flag = topFlag(r);
                const unfilled = !r.sme_id;
                const isApproved = approved.has(r.session_id);
                const medium = !!flag && (flag.severity === "medium" || flag.severity === "high");
                const okApproved = isApproved && !unfilled && !flag;
                return (
                  <button
                    key={r.session_id}
                    className="cal-card"
                    onClick={() => onOpen(r.session_id)}
                    title={`${r.batch_id} · ${r.sub_specialty ?? meta.type_label[r.type]} · ${hhmm(it.hour)} IST · ${r.sme_name ?? "unfilled"}`}
                    style={{
                      ...base,
                      background: unfilled ? "var(--red-tint)" : medium ? "#fdf9ef" : okApproved ? "#f4faf6" : "#fff",
                      ...accentBorder(
                        unfilled ? "1px solid var(--red-line)" : medium ? "1px solid var(--amber-line)"
                          : okApproved ? "1px solid var(--green-line)" : "1px solid #ece9f5",
                        `3px solid ${unfilled ? "var(--red)" : okApproved ? "var(--green)" : course?.accent ?? "var(--brand)"}`,
                      ),
                      boxShadow: unfilled ? "0 2px 10px rgba(192,57,43,0.16)" : base.boxShadow,
                    }}
                  >
                    {okApproved && (
                      <span
                        className="pointer-events-none absolute right-[4px] top-[2px] z-[1] text-[10px] font-bold leading-none"
                        style={{ color: "var(--green-ink)" }}
                      >
                        ✓
                      </span>
                    )}
                    <span className="flex w-full min-w-0 flex-nowrap items-center gap-[5px] overflow-hidden whitespace-nowrap">
                      <span className="min-w-0 overflow-hidden text-ellipsis text-[10px] font-bold" style={{ color: course?.deep, letterSpacing: "0.02em" }}>
                        {r.batch_id}
                      </span>
                      {flag && total === 1 && (
                        <span className={`chip cal-chip ${SEV_CHIP[flag.severity]}`}>{FLAG_LABEL[flag.code]}</span>
                      )}
                      {changed?.has(r.session_id) && (
                        <span title="changed since last run" className="size-[7px] shrink-0 rounded-full" style={{ background: "var(--amber)" }} />
                      )}
                    </span>
                    <span className="block overflow-hidden text-ellipsis whitespace-nowrap text-[10.5px] font-semibold leading-[1.15]">
                      {r.sub_specialty ?? meta.type_label[r.type]}
                    </span>
                    {showSme && (
                      <span
                        className="cal-sme block overflow-hidden text-ellipsis whitespace-nowrap text-[10.5px] leading-[1.3]"
                        style={unfilled ? { color: "var(--red-ink)", fontWeight: 650 } : { color: "var(--ink-3)" }}
                      >
                        {r.sme_name ?? "Unfilled — needs a teacher"}
                      </span>
                    )}
                  </button>
                );
              }))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export { ROW_H };

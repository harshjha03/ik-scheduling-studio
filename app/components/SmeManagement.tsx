"use client";
import { useState } from "react";
import type { Course, DraftRow, Meta, SME, WeekKey, WeekMeta } from "@/lib/types";
import type { SmeFilter, WorkloadRow } from "@/lib/view";
import { FAIRNESS_BAND, accentBorder, avatarBg, fitsFor, initials, istParts, isAvailable, smeMatches, smeWeekStats } from "@/lib/view";
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
  query: string;
  filter: SmeFilter;
  vh: number;
  onQuery: (v: string) => void;
  onFilter: (f: SmeFilter) => void;
  onSelect: (id: string) => void;
  onOpen: (sessionId: string) => void;
  onGhost: (sessionId: string) => void;
  onEditSme: (smeId: string) => void;
  onReportOut: (smeId: string) => void;
  /** teachers ops has taken out of next week; toggling re-drafts the week deterministically */
  unavailable: Record<string, boolean>;
  onToggleUnavailable: (smeId: string) => void;
  onImportSmes: () => void;
  /** read each teacher's calendar for the week and re-draft against what is already booked */
  onSyncAvailability: () => void;
  syncBusy?: boolean;
  /** every teacher's four-week rolling load, exactly as Stage B scores it */
  workload: WorkloadRow[];
  /** busy blocks found per SME on the last sync, and whether that sync was live */
  busyBlocks?: Record<string, number>;
  syncDetail?: string;
  syncLive?: boolean;
}

const LEVEL_CHIP: Record<string, { bg: string; fg: string }> = {
  beginner: { bg: "var(--green-tint)", fg: "var(--green-ink)" },
  intermediate: { bg: "var(--brand-tint)", fg: "var(--brand-deep)" },
  advanced: { bg: "var(--brand-tint)", fg: "var(--brand-deep)" },
};

export default function SmeManagement({
  smes, rows, courses, meta, weeks, week, weekDates, approved, selected, leave, query, filter, vh,
  onQuery, onFilter, onSelect, onOpen, onGhost, onEditSme, onReportOut, unavailable, onToggleUnavailable, onImportSmes,
  onSyncAvailability, syncBusy, workload, busyBlocks, syncDetail, syncLive,
}: Props) {
  const [section, setSection] = useState<"list" | "workload">("list");
  const [showDetail, setShowDetail] = useState(false);
  const shown = smes.filter((s) => smeMatches(s, rows, query, filter, leave));
  const sel = smes.find((s) => s.id === selected) ?? shown[0] ?? smes[0];
  const selRows = rows.filter((r) => r.sme_id === sel.id);
  const [h0, h1] = meta.hours;
  const ref = rows[0]?.start_utc;

  const freeCells = new Set<string>();
  if (ref) {
    for (let d = 0; d < 6; d++) for (let h = h0; h < h1; h++) if (isAvailable(sel, ref, d, h)) freeCells.add(`${d}-${h}`);
  }
  const busy = new Set(selRows.map((r) => { const p = istParts(r.start_utc); return `${p.day}-${p.hour}`; }));
  const ghosts: GhostRow[] = fitsFor(sel, rows)
    .filter((r) => { const p = istParts(r.start_utc); return !busy.has(`${p.day}-${p.hour}`); })
    .map((r) => ({ session_id: r.session_id, batch_id: r.batch_id, subject: r.subject, sub_specialty: r.sub_specialty, type: r.type, start_utc: r.start_utc }));

  const FILTERS: [SmeFilter, string, string][] = [
    ["all", "All", "Everyone in the pool"],
    ["fits", `Can fill an open class · ${smes.filter((s) => fitsFor(s, rows).length > 0).length}`, "Free at the hour of an unfilled class and carries the topic"],
    ["free", "Has headroom", "Below their stated weekly preference"],
    ["over", `Over preference · ${smes.filter((s) => rows.filter((r) => r.sme_id === s.id).length > s.preferred).length}`, "Assigned more classes than they asked for"],
    ["leave", "On leave", "Has leave booked"],
  ];

  const openSme = (id: string) => {
    onSelect(id);
    setShowDetail(true);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[14px]">
      <div className="tabs w-fit" aria-label="SME subsections">
        <button
          className={`tab ${section === "list" ? "tab-on" : "tab-off"}`}
          aria-pressed={section === "list"}
          onClick={() => { setSection("list"); setShowDetail(false); }}
        >
          SME List
        </button>
        <button
          className={`tab ${section === "workload" ? "tab-on" : "tab-off"}`}
          aria-pressed={section === "workload"}
          onClick={() => { setSection("workload"); setShowDetail(false); }}
        >
          Workload over past weeks
        </button>
      </div>

      {section === "list" && (
      <section className="card flex min-h-[268px] flex-1 flex-col">
        <div className="flex shrink-0 flex-wrap items-center gap-[9px] p-[12px_20px_11px]" style={{ borderBottom: "0.5px solid rgba(16,26,51,0.06)" }}>
          <span className="whitespace-nowrap text-[13px] font-bold">
            SMEs <span style={{ color: "var(--muted-2)", fontWeight: 500 }}>{shown.length} of {smes.length}</span>
          </span>
          <input
            className="field w-[186px] py-[7px] text-[12px]"
            placeholder="Search name, topic or city…"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            aria-label="Search SMEs"
          />
          <div className="flex flex-wrap gap-[6px]">
            {FILTERS.map(([k, label, tip]) => (
              <button
                key={k}
                onClick={() => onFilter(k)}
                title={tip}
                className="whitespace-nowrap rounded-[9px] px-[10px] py-[5px] text-[11.5px]"
                style={{
                  border: `1px solid ${filter === k ? "var(--brand-ring)" : "var(--line)"}`,
                  background: filter === k ? "var(--brand-tint)" : "#fff",
                  color: filter === k ? "var(--brand-deep)" : "var(--ink-3)",
                  fontWeight: filter === k ? 700 : 600, cursor: "pointer",
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <button
            className="ml-auto inline-flex cursor-pointer items-center gap-2 whitespace-nowrap rounded-[11px] bg-white p-[7px_13px] text-[12px] font-semibold"
            style={{ border: "1px solid #dfe7f2", color: "var(--ink)", boxShadow: "0 1px 2px rgba(54,67,87,0.05)" }}
            onClick={onSyncAvailability}
            disabled={syncBusy}
            title={syncLive
              ? `Reads each teacher's Google Calendar for this week — ${syncDetail ?? ""}`
              : `Simulated — ${syncDetail ?? "Google Calendar not configured"}`}
          >
            <span>{syncBusy ? "Syncing…" : `Sync availability${syncLive ? "" : " (simulated)"}`}</span>
          </button>
          <button
            className="inline-flex cursor-pointer items-center gap-2 whitespace-nowrap rounded-[11px] bg-white p-[7px_13px_7px_8px] text-[12px] font-semibold"
            style={{ border: "1px solid #dfe7f2", color: "var(--ink)", boxShadow: "0 1px 2px rgba(54,67,87,0.05)" }}
            onClick={onImportSmes}
            title="Download the SME template, fill it in Excel, upload it back"
          >
            <span className="flex size-[22px] shrink-0 items-center justify-center rounded-[7px]" style={{ background: "var(--brand-tint)", color: "var(--brand-deep)" }}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 16V4M7.5 8.5L12 4l4.5 4.5M4 17.5V20h16v-2.5" />
              </svg>
            </span>
            <span>Import SMEs</span>
          </button>
        </div>
        <div className="min-h-[152px] flex-1 overflow-auto">
          <table className="w-full min-w-[1040px] border-collapse">
            <thead>
              <tr className="label-caps text-left">
                <th className="p-[9px_20px] font-semibold">SME</th>
                <th className="p-[9px_10px] font-semibold">Skills / topics</th>
                <th className="w-[150px] p-[9px_10px] font-semibold">Level</th>
                <th className="w-[80px] p-[9px_10px] font-semibold">Rating</th>
                <th className="w-[158px] p-[9px_10px] font-semibold">This week</th>
                <th className="w-[150px] p-[9px_10px] font-semibold">Leave</th>
                <th className="w-[74px] p-[9px_20px] font-semibold">Profile</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((s) => {
                const st = smeWeekStats(s, rows);
                const fits = fitsFor(s, rows).length;
                const nextLevel = meta.levels[meta.levels.indexOf(s.level) + 1];
                const td = { padding: "11px 10px", verticalAlign: "top" as const, borderTop: "0.5px solid rgba(16,26,51,0.06)" };
                const chip = LEVEL_CHIP[s.level];
                return (
                  <tr
                    key={s.id}
                    onClick={() => openSme(s.id)}
                    className="cursor-pointer hover:bg-[rgba(79,149,216,0.05)]"
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
                          <span className="mt-px block whitespace-nowrap text-[10.5px]" style={{ color: "var(--muted-3)" }}>
                            {s.id} · {s.subjects.join(" + ")} · {s.city}
                          </span>
                          <span className="mt-px block overflow-hidden text-ellipsis whitespace-nowrap text-[10.5px]" style={{ color: "#8892a4" }}>
                            {s.email ?? "—"}
                          </span>
                        </span>
                      </div>
                    </td>
                    <td style={td}>
                      <div className="flex flex-wrap gap-1">
                        {s.topics.map((t) => (
                          <span key={t} className="rounded-[8px] px-2 py-[3px] text-[10.5px]" style={{ background: "#f1f5fa", color: "#42506b", fontWeight: 550 }}>{t}</span>
                        ))}
                      </div>
                    </td>
                    <td style={td}>
                      <span className="rounded-[8px] px-2 py-[3px] text-[10px] font-bold capitalize" style={{ background: chip.bg, color: chip.fg }}>{s.level}</span>
                      <div className="mt-[6px] text-[10.5px] leading-[1.35]" style={{ color: "var(--muted)" }}>
                        {s.level === "advanced" ? "top level · mocks & advanced batches" : `${s.to_upgrade} classes to ${nextLevel}`}
                      </div>
                    </td>
                    <td style={td}>
                      <div className="text-[14px] font-bold" style={{ color: "var(--green-ink)" }}>★ {s.rating.toFixed(1)}</div>
                    </td>
                    <td style={td}>
                      <div className="text-[12.5px] font-semibold">{st.assigned} / {s.preferred} classes</div>
                      <div className="relative mt-[7px] h-[5px] overflow-hidden rounded-[3px]" style={{ background: "var(--line-2)" }}>
                        <span
                          className="absolute inset-y-0 left-0 rounded-[3px]"
                          style={{ width: `${Math.min(100, (st.assigned / s.preferred) * 100).toFixed(0)}%`, background: st.over ? "var(--red)" : "var(--green)" }}
                        />
                      </div>
                      <div
                        className="mt-[5px] text-[10.5px] leading-[1.35]"
                        style={fits ? { color: "var(--brand-deep)", fontWeight: 650 } : { color: "var(--muted)" }}
                      >
                        {fits ? `Can fill ${fits} open class${fits === 1 ? "" : "es"} →`
                          : st.over ? `${st.assigned - s.preferred} over their preference`
                            : `${s.preferred - st.assigned} slot${s.preferred - st.assigned === 1 ? "" : "s"} of headroom`}
                      </div>
                    </td>
                    <td style={td}>
                      <span
                        className="inline-block rounded-[9px] px-[9px] py-[5px] text-[10.5px] font-semibold leading-[1.35]"
                        style={leave[s.id]
                          ? { background: "var(--sand-tint)", color: "var(--sand-ink)" }
                          : { background: "var(--green-tint)", color: "var(--green-ink)" }}
                      >
                        {leave[s.id] ?? "Available"}
                      </span>
                      {!!busyBlocks?.[s.id] && (
                        <span
                          className="mt-[4px] block rounded-[8px] px-[7px] py-[3px] text-[10px] font-semibold"
                          style={{ background: "var(--sand-tint)", color: "var(--sand-ink)" }}
                          title="Blocks already on their calendar this week — Stage A will not book over them"
                        >
                          {busyBlocks[s.id]} calendar block{busyBlocks[s.id] === 1 ? "" : "s"}
                        </span>
                      )}
                    </td>
                    <td style={{ ...td, paddingRight: 20, textAlign: "right" }} onClick={(e) => e.stopPropagation()}>
                      <span className="inline-flex gap-[6px]">
                        <button className="btn btn-sm" onClick={() => onToggleUnavailable(s.id)}
                          title={unavailable[s.id]
                            ? "Put this teacher back into next week's draft"
                            : "Take this teacher out of next week — the draft re-runs without them and anything uncovered lands in Work items"}>
                          {unavailable[s.id] ? "Mark available" : "Mark unavailable"}
                        </button>
                        <button className="btn btn-sm" onClick={() => onReportOut(s.id)} title="Ask the copilot to find cover for this teacher">
                          Find cover…
                        </button>
                        <button className="btn btn-sm" onClick={() => onEditSme(s.id)} title="Edit profile basics">
                          Edit
                        </button>
                      </span>
                    </td>
                  </tr>
                );
              })}
              {!shown.length && (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-[12.5px]" style={{ color: "var(--muted)" }}>
                    Nobody matches that search or filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
      )}

      {section === "workload" && (
        <WorkloadCard rows={workload} query={query} filter={filter} smeRows={rows} leave={leave}
          onSelect={openSme} />
      )}

      {showDetail && (
        <div
          className="fixed inset-0 flex items-center justify-center p-6"
          onClick={() => setShowDetail(false)}
          style={{
            background: "rgba(16,26,51,0.28)", backdropFilter: "blur(16px) saturate(130%)",
            WebkitBackdropFilter: "blur(16px) saturate(130%)", zIndex: 60, animation: "overlayIn .22s ease-out",
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-label={`Availability and assignment history for ${sel.name}`}
            onClick={(e) => e.stopPropagation()}
            className="flex min-h-0 w-full flex-col overflow-hidden rounded-[22px] bg-white"
            style={{
              width: "min(760px, calc(100vw - 64px), calc(100vh - 120px))", aspectRatio: "1 / 1",
              boxShadow: "0 32px 80px rgba(16,26,51,0.28), 0 0 0 0.5px rgba(16,26,51,0.08)",
              animation: "sheetIn .32s cubic-bezier(.32,.72,0,1)",
            }}
          >
            <div className="flex shrink-0 flex-wrap items-center gap-3 p-[16px_20px_13px]" style={{ borderBottom: "1px solid var(--line-2)" }}>
              <div>
                <div className="text-[15px] font-bold">Availability &amp; assignment history</div>
                <div className="mt-[2px] text-[11.5px]" style={{ color: "var(--muted)" }}>
                  {sel.name} · {selRows.length} assigned in {weeks[week].label.toLowerCase()} · green blocks are free working hours
                  {ghosts.length ? ` · ${ghosts.length} unfilled class(es) fit in them` : ""}
                </div>
              </div>
              <button className="btn btn-sm ml-auto" onClick={() => setShowDetail(false)}>
                Close calendar
              </button>
            </div>
            <div className="flex shrink-0 flex-wrap gap-[14px] p-[10px_20px_8px] text-[11.5px]" style={{ color: "var(--ink-3)" }}>
              <span>
                <span className="mr-[6px] inline-block size-[10px] rounded-[3px]" style={{ background: "#e6f2ec", border: "1px solid var(--green-line)" }} />
                free working hour
              </span>
              <span>
                <span
                  className="mr-[6px] inline-block size-[10px] rounded-[3px]"
                  style={{ background: "#fff", ...accentBorder("1px solid var(--field)", "3px solid #4a7fd0") }}
                />
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
              vh={Math.min(vh, 620)} fit
            />
          </section>
        </div>
      )}
    </div>
  );
}


/**
 * The rolling window fairness is actually scored on: three history weeks plus this draft, against
 * the mean of that teacher's subject pool.
 *
 * A FAIRNESS_VIOLATION on a class says "26 sessions over 4 weeks, 9.9 above the DSA pool mean" and
 * there was nowhere in the app to see where that came from — so the band was a number a coordinator
 * had to take on trust. These are the same figures, per teacher, with the band drawn on.
 */
function WorkloadCard({ rows, query, filter, smeRows, leave, onSelect }: {
  rows: WorkloadRow[]; query: string; filter: SmeFilter; smeRows: DraftRow[]; leave: Record<string, string>;
  onSelect: (id: string) => void;
}) {
  const shown = rows.filter((w) => smeMatches(w.sme, smeRows, query, filter, leave));
  const peak = Math.max(1, ...rows.map((w) => Math.max(...w.byWeek.map((b) => b.sessions))));
  const out = shown.filter((w) => !w.inBand);
  return (
    <section className="card flex min-h-0 flex-1 flex-col">
      <div className="flex w-full items-center gap-[9px] p-[12px_20px_11px] text-left" style={{ borderBottom: "1px solid var(--line-2)" }}>
        <span className="text-[13px] font-bold">Workload over past weeks</span>
        <span className="text-[11.5px]" style={{ color: "var(--muted-2)" }}>
          4-week load and fairness against the subject-pool mean ± {FAIRNESS_BAND}
        </span>
        <span className="ml-auto flex items-center gap-[9px]">
          {!!out.length && (
            <span className="chip chip-medium">{out.length} outside the band</span>
          )}
        </span>
      </div>
        <div className="min-h-0 flex-1 overflow-auto p-[6px_14px_12px]">
          <div className="sticky top-0 z-[2] grid items-center gap-x-[8px] bg-white px-[8px] py-[8px] label-caps" style={{ gridTemplateColumns: "168px minmax(200px,1fr) 44px 190px" }}>
            <span className="text-center">SME</span>
            <span className="border-l border-[var(--line-2)] text-center">Weekly classes</span>
            <span className="border-l border-[var(--line-2)] text-center">Total</span>
            <span className="border-l border-[var(--line-2)] text-center">Fairness</span>
          </div>
          <div role="list">
              {shown.map((w) => {
                const tone = w.inBand ? null : w.delta > 0 ? "over" : "under";
                return (
                  <div role="listitem" key={w.sme.id} onClick={() => onSelect(w.sme.id)}
                    className="grid cursor-pointer items-center gap-x-[8px] rounded-[8px] hover:bg-[rgba(79,149,216,0.05)]"
                    style={{ gridTemplateColumns: "168px minmax(200px,1fr) 44px 190px" }}>
                    <span className="p-[5px_8px] text-[12.5px] font-semibold">
                      <span className="block overflow-hidden text-ellipsis whitespace-nowrap">{w.sme.name}</span>
                      <span className="block text-[10.5px] font-normal" style={{ color: "var(--muted)" }}>{w.subject} pool</span>
                    </span>
                    <span className="min-w-0 border-l border-[var(--line-2)] p-[5px_8px]">
                      <span className="flex items-end gap-[7px]">
                        {w.byWeek.map((b, i) => (
                          <span key={i} className="flex min-w-0 flex-1 flex-col items-center gap-[3px]" title={`${b.draft ? "This week" : b.week}: ${b.sessions} classes`}>
                            <span className="relative flex h-[30px] w-full items-center justify-center overflow-hidden rounded-[5px] text-[11px] font-bold"
                              style={{ background: "#eef4fb", border: "1px solid var(--brand-line)", color: "var(--ink-2)" }}>
                              <span className="absolute inset-y-0 left-0 rounded-[4px]" style={{
                                width: `${Math.max(0, Math.min(100, (b.sessions / peak) * 100))}%`,
                                background: b.draft ? "#8dbce1" : "#b8d5ef",
                              }} />
                              <span className="relative z-[1]" style={{ color: "var(--ink-2)" }}>{b.sessions}</span>
                            </span>
                            <span className="whitespace-nowrap text-[9.5px] font-semibold" style={{ color: b.draft ? "var(--brand-deep)" : "var(--muted)" }}>
                              {b.draft ? "This" : b.week.replace(/^\d{4}-/, "")}
                            </span>
                          </span>
                        ))}
                      </span>
                    </span>
                    <span className="border-l border-[var(--line-2)] p-[5px_8px] text-right text-[12.5px] font-bold">{w.total}</span>
                    <span className="border-l border-[var(--line-2)] p-[5px_8px] text-[11px]" style={{ color: "var(--muted)" }}>
                      pool mean {w.poolMean.toFixed(1)}
                      {" · "}
                      <span style={{
                        color: tone === "over" ? "var(--red-ink)" : tone === "under" ? "var(--amber-ink)" : "var(--green-ink)",
                        fontWeight: 650,
                      }}>
                        {tone === null ? "inside the band"
                          : `${Math.abs(w.delta).toFixed(1)} ${w.delta > 0 ? "above" : "below"}`}
                      </span>
                    </span>
                  </div>
                );
              })}
              {!shown.length && (
                <div className="p-4 text-center text-[12.5px]" style={{ color: "var(--muted)" }}>
                  Nobody matches that search or filter.
                </div>
              )}
          </div>
        </div>
    </section>
  );
}

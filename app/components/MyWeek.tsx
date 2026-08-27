"use client";
import type { Batch, Course, DraftRow, Meta, SME, WeekKey, WeekMeta } from "@/lib/types";
import { AVAIL_BLOCKS, avatarBg, initials, isAvailable, istParts } from "@/lib/view";
import WeekCalendar from "./WeekCalendar";

export interface PendingChange { session_id: string; sme_id: string; name: string; from_sme_id: string | null }

interface Props {
  role: "sme" | "student";
  me: SME;
  myBatch: Batch | undefined;
  rows: DraftRow[];
  smes: SME[];
  courses: Record<string, Course>;
  meta: Meta;
  weeks: Record<WeekKey, WeekMeta>;
  week: WeekKey;
  weekDates: { day: string; date: string }[];
  approved: Set<string>;
  availOff: Record<string, boolean>;
  preferred: number;
  onAvail: (key: string) => void;
  onPreferred: (n: number) => void;
  onWeek: (w: WeekKey) => void;
  onOpen: (sessionId: string) => void;
  leave: string | null;
  onToggleLeave: () => void;
  pending: PendingChange[];
  onResolve: (sessionId: string, accept: boolean) => void;
}

function Stat({ label, value, sub, size = 30 }: { label: string; value: React.ReactNode; sub: string; size?: number }) {
  return (
    <div className="kpi">
      <div className="text-[11.5px] font-semibold" style={{ color: "var(--ink-3)" }}>{label}</div>
      <div className="mt-[10px] font-bold leading-[1.15]" style={{ fontSize: size, letterSpacing: "-0.02em" }}>{value}</div>
      <div className="mt-[7px] text-[11.5px] leading-[1.45]" style={{ color: "var(--muted)" }}>{sub}</div>
    </div>
  );
}

export default function MyWeek({
  role, me, myBatch, rows, smes, courses, meta, weeks, week, weekDates, approved, availOff, preferred,
  onAvail, onPreferred, onWeek, onOpen, leave, onToggleLeave, pending, onResolve,
}: Props) {
  const isStudent = role === "student";
  const mine = isStudent ? rows.filter((r) => r.batch_id === myBatch?.id) : rows.filter((r) => r.sme_id === me.id);
  const ref = rows[0]?.start_utc;
  const [h0, h1] = meta.hours;

  const freeCells = new Set<string>();
  if (!isStudent && ref) {
    for (let d = 0; d < 6; d++) {
      for (let h = h0; h < h1; h++) if (isAvailable(me, ref, d, h)) freeCells.add(`${d}-${h}`);
    }
  }

  const next = mine.slice().sort((a, b) => a.start_utc.localeCompare(b.start_utc))[0];
  const byInstructor = new Map<string, DraftRow[]>();
  mine.filter((r) => r.sme_id).forEach((r) => {
    if (!byInstructor.has(r.sme_id!)) byInstructor.set(r.sme_id!, []);
    byInstructor.get(r.sme_id!)!.push(r);
  });

  return (
    <div className="flex flex-col gap-[14px]">
      <div className="grid gap-[14px]" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(196px,1fr))" }}>
        {isStudent ? (
          <>
            <Stat label="Classes this week" value={mine.length} sub={`${mine.filter((r) => r.type === "mock").length} mock interview(s) included`} />
            <Stat
              label="Next class" size={15}
              value={next ? next.sub_specialty ?? meta.type_label[next.type] : "—"}
              sub={next ? `${meta.days[istParts(next.start_utc).day]} ${istParts(next.start_utc).label} · ${next.sme_name ?? "teacher TBD"}` : ""}
            />
            <Stat
              label="Course progress" value={myBatch ? `${myBatch.weeks_done}/${myBatch.weeks_total}` : "—"}
              sub={myBatch ? `${myBatch.weeks_total - myBatch.weeks_done} weeks left · ${myBatch.level} track` : ""}
            />
            <Stat
              label="Teachers confirmed" value={`${mine.filter((r) => r.sme_id).length}/${mine.length}`}
              sub={mine.some((r) => !r.sme_id) ? "Ops is filling one slot" : "All classes staffed"}
            />
          </>
        ) : (
          <>
            <Stat label="My classes" value={mine.length} sub={`Preferred ${preferred} a week`} />
            <Stat label="My level" value={me.level} size={19} sub={me.level === "advanced" ? "Eligible for advanced batches and mocks" : `${me.to_upgrade} classes to the next level`} />
            <Stat label="Monthly rating" value={me.rating.toFixed(1)} sub="From learner feedback forms" />
            <Stat label="Free working hours" value={Math.max(0, freeCells.size - mine.length)} sub="Ops can still book these" />
          </>
        )}
      </div>

      {!isStudent && !!pending.length && (
        <section className="card">
          <div className="p-[15px_20px_13px] text-[13px] font-bold" style={{ borderBottom: "1px solid var(--line-2)" }}>
            Change requests from ops ({pending.length})
          </div>
          {pending.map((p) => {
            const row = rows.find((r) => r.session_id === p.session_id);
            return (
              <div key={p.session_id} className="flex flex-wrap items-center gap-3 p-[12px_20px]" style={{ borderTop: "1px solid #f3f6fb" }}>
                <div className="min-w-0">
                  <div className="text-[12.5px] font-semibold">
                    {row?.batch_id} · {row?.sub_specialty ?? (row ? meta.type_label[row.type] : "")}
                  </div>
                  <div className="mt-px text-[11.5px]" style={{ color: "var(--muted)" }}>
                    {row ? `${meta.days[istParts(row.start_utc).day]} ${istParts(row.start_utc).label} IST` : ""} · ops asked you to take this class
                  </div>
                </div>
                <div className="ml-auto flex gap-2">
                  <button className="btn btn-sm" onClick={() => onResolve(p.session_id, false)}>Decline</button>
                  <button className="btn btn-go btn-sm" onClick={() => onResolve(p.session_id, true)}>Accept</button>
                </div>
              </div>
            );
          })}
        </section>
      )}

      <section className="card">
        <div className="flex flex-wrap items-center gap-3 p-[15px_20px_13px]" style={{ borderBottom: "1px solid var(--line-2)" }}>
          <div className="text-[13px] font-bold">
            {isStudent ? `${myBatch?.id} · ${weeks[week].label}` : `My classes · ${weeks[week].label}`}
          </div>
          <div className="tabs ml-auto">
            {(["current", "next"] as WeekKey[]).map((k) => (
              <button key={k} onClick={() => onWeek(k)} className={`tab ${week === k ? "tab-on" : "tab-off"}`}>
                {weeks[k].label}
              </button>
            ))}
          </div>
        </div>
        <WeekCalendar
          rows={mine} courses={courses} meta={meta} weekDates={weekDates} approved={approved}
          onOpen={onOpen} freeCells={isStudent ? undefined : freeCells}
        />
      </section>

      {isStudent ? (
        <section className="card">
          <div className="p-[15px_20px_13px] text-[13px] font-bold" style={{ borderBottom: "1px solid var(--line-2)" }}>
            My instructors this week
          </div>
          {[...byInstructor.entries()].map(([id, rs]) => {
            const s = smes.find((x) => x.id === id);
            if (!s) return null;
            return (
              <div key={id} className="flex items-center gap-[11px] p-[12px_20px]" style={{ borderTop: "1px solid #f3f6fb" }}>
                <span
                  className="flex size-8 items-center justify-center rounded-full text-[11px] font-bold"
                  style={{ background: avatarBg(id), color: "var(--ink-2)" }}
                >
                  {initials(s.name)}
                </span>
                <span className="min-w-0">
                  <span className="block text-[12.5px] font-semibold">{s.name}</span>
                  <span className="mt-px block text-[11.5px]" style={{ color: "var(--muted)" }}>
                    {[...new Set(rs.map((r) => r.sub_specialty ?? meta.type_label[r.type]))].join(" · ")}
                  </span>
                </span>
                <span className="ml-auto flex items-center gap-3">
                  <span className="text-[11.5px] font-semibold" style={{ color: "var(--green-ink)" }}>★ {s.rating.toFixed(1)}</span>
                  <span className="text-[11.5px]" style={{ color: "var(--ink-3)" }}>{rs.length} class(es)</span>
                </span>
              </div>
            );
          })}
          {!byInstructor.size && <div className="p-5 text-[12.5px]" style={{ color: "var(--muted)" }}>No instructors assigned yet.</div>}
        </section>
      ) : (
        <div className="grid gap-[14px]" style={{ gridTemplateColumns: "minmax(0,1.3fr) minmax(0,1fr)" }}>
          <section className="card p-[18px_20px]">
            <div className="text-[13px] font-bold">My availability</div>
            <div className="mb-[14px] mt-[3px] text-[11.5px]" style={{ color: "var(--muted)" }}>
              Tap a block to switch it. Ops sees the change instantly; next week&apos;s draft respects it.
            </div>
            <div className="grid gap-[6px]" style={{ gridTemplateColumns: "98px repeat(6,minmax(0,1fr))" }}>
              <div />
              {meta.days.map((d) => (
                <div key={d} className="text-center text-[11.5px] font-semibold" style={{ color: "var(--ink-2)" }}>{d}</div>
              ))}
              {AVAIL_BLOCKS.map(([label, hrs], bi) => (
                <div key={label} className="contents">
                  <div className="flex items-center text-[11.5px]" style={{ color: "var(--ink-3)" }}>{label} {hrs}</div>
                  {meta.days.map((_, di) => {
                    const key = `${di}-${bi}`;
                    const off = !!availOff[key];
                    return (
                      <button
                        key={key}
                        onClick={() => onAvail(key)}
                        className="rounded-[12px] py-[11px] text-[11.5px]"
                        style={off
                          ? { border: "1px solid var(--line)", background: "var(--page)", color: "var(--muted-2)", cursor: "pointer" }
                          : { border: "1px solid var(--green-line)", background: "#eaf5ef", color: "var(--green-ink)", fontWeight: 650, cursor: "pointer" }}
                      >
                        {off ? "Off" : "Free"}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          </section>

          <section className="card p-[18px_20px]">
            <div className="text-[13px] font-bold">Leave &amp; preferences</div>
            <div className="mb-[14px] mt-[3px] text-[11.5px]" style={{ color: "var(--muted)" }}>
              What ops sees when drafting next week.
            </div>
            <div
              className="rounded-[14px] p-[14px_15px]"
              style={leave ? { background: "var(--sand-tint)" } : { background: "#eaf5ef" }}
            >
              <div className="text-[12.5px] font-semibold" style={{ color: leave ? "var(--sand-ink)" : "var(--green-ink)" }}>
                {leave ?? "No leave planned — available next week"}
              </div>
              <button className="btn btn-sm mt-[9px]" onClick={onToggleLeave}>
                {leave ? "Withdraw leave request" : "Request leave next week"}
              </button>
            </div>
            <div className="mt-4 flex items-center gap-3">
              <span className="text-[12px]" style={{ color: "var(--ink-3)" }}>Preferred classes / week</span>
              <div className="ml-auto flex items-center gap-2">
                <button className="btn size-[28px] p-0 text-[14px]" onClick={() => onPreferred(Math.max(1, preferred - 1))}>−</button>
                <span className="w-5 text-center text-[15px] font-bold">{preferred}</span>
                <button className="btn size-[28px] p-0 text-[14px]" onClick={() => onPreferred(Math.min(8, preferred + 1))}>+</button>
              </div>
            </div>
            <div className="mt-2 text-[11.5px] leading-[1.5]" style={{ color: "var(--muted)" }}>
              Ops drafts around this number. You are currently at {mine.length} for {weeks[week].label.toLowerCase()}.
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

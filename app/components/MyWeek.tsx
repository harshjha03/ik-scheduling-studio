"use client";
import type { Batch, Course, DraftRow, Meta, SME, WeekKey, WeekMeta } from "@/lib/types";
import { AVAIL_BLOCKS, avatarBg, initials, isAvailable, istParts } from "@/lib/view";
import WeekCalendar from "./WeekCalendar";

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
  vh: number;
  onAvail: (key: string) => void;
  onPreferred: (n: number) => void;
  onOpen: (sessionId: string) => void;
  leave: string | null;
  onToggleLeave: () => void;
  onEditProfile: () => void;
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
  role, me, myBatch, rows, smes, courses, meta, weeks, week, weekDates, approved, availOff, preferred, vh,
  onAvail, onPreferred, onOpen, leave, onToggleLeave, onEditProfile,
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

  const up = next ? istParts(next.start_utc) : null;
  const nextRating = next?.sme_id ? smes.find((x) => x.id === next.sme_id)?.rating ?? null : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[14px]">
      {next && up && (
        <div className="card flex shrink-0 items-center gap-[14px] rounded-[18px] p-[13px_18px]">
          <span className="flex size-[46px] shrink-0 flex-col items-center justify-center rounded-[14px]" style={{ background: "var(--brand-tint)" }}>
            <span className="text-[9.5px] font-bold" style={{ color: "var(--brand-deep)", letterSpacing: "0.04em" }}>{meta.days[up.day]}</span>
            <span className="text-[13px] font-bold leading-[1.1]" style={{ color: "var(--brand-deep)" }}>{up.label}</span>
          </span>
          <span className="min-w-0">
            <span className="label-caps block">Up next</span>
            <span className="mt-[2px] block overflow-hidden text-ellipsis whitespace-nowrap text-[14.5px] font-bold" style={{ letterSpacing: "-0.01em" }}>
              {next.sub_specialty ?? meta.type_label[next.type]}
            </span>
            <span className="mt-[2px] block text-[11.5px]" style={{ color: "var(--muted)" }}>
              {isStudent
                ? [meta.type_label[next.type], next.sme_name ?? "teacher to be confirmed",
                   nextRating ? `★ ${nextRating.toFixed(1)}` : null].filter(Boolean).join(" · ")
                : `${next.batch_id} · ${meta.type_label[next.type]} · ${courses[next.subject]?.name ?? next.subject}`}
            </span>
          </span>
          <button className="btn btn-brand ml-auto shrink-0" onClick={() => onOpen(next.session_id)}>
            {isStudent ? "Open class" : "Class details"}
          </button>
        </div>
      )}
      <div className="grid shrink-0 gap-[14px]" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(196px,1fr))" }}>
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
            <Stat label="My level" value={me.level} size={19} sub={me.level === "advanced" ? "Eligible for advanced batches and mocks" : `${me.to_upgrade} classes to advanced`} />
            <Stat label="Monthly rating" value={me.rating.toFixed(1)} sub="From learner feedback forms" />
            <Stat label="Free working hours" value={Math.max(0, freeCells.size - mine.length)} sub="Ops can still book these" />
          </>
        )}
      </div>

      {/* the artboard sets the side panels beside the calendar in a fixed 322px column, not below it */}
      <div className="grid min-h-0 flex-1 gap-4" style={{ gridTemplateColumns: "minmax(0,1fr) 322px" }}>
        <section className="card flex min-h-0 flex-col">
          <div className="flex shrink-0 flex-wrap items-center gap-3 p-[15px_20px_13px]" style={{ borderBottom: "1px solid var(--line-2)" }}>
            <div className="text-[13px] font-bold">
              {isStudent ? `${myBatch?.id} · ${weeks[week].label}` : `My classes · ${weeks[week].label}`}
            </div>
            {!isStudent && (
              <button className="btn btn-sm ml-auto" onClick={onEditProfile} title="Edit your contact details and weekly preference">
                Edit my profile
              </button>
            )}
          </div>
          {/* no free-slot shading here: the artboard keeps this calendar clean and shows
              availability in the side card instead (ops still sees free slots in SME management) */}
          <WeekCalendar
            rows={mine} courses={courses} meta={meta} weekDates={weekDates} approved={approved}
            onOpen={onOpen} vh={vh}
          />
        </section>

        <div className="flex min-h-0 flex-col gap-4 overflow-auto">
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
        <>
          <section className="card p-[18px_20px]">
            <div className="text-[13px] font-bold">My availability</div>
            <div className="mb-[14px] mt-[3px] text-[11.5px]" style={{ color: "var(--muted)" }}>
              Tap a block to switch it. Ops sees the change instantly; next week&apos;s draft respects it.
            </div>
            <div className="grid gap-[5px]" style={{ gridTemplateColumns: "64px repeat(6,minmax(0,1fr))" }}>
              <div />
              {meta.days.map((d) => (
                <div key={d} className="text-center text-[11.5px] font-semibold" style={{ color: "var(--ink-2)" }}>{d}</div>
              ))}
              {AVAIL_BLOCKS.map(([label, hrs], bi) => (
                <div key={label} className="contents">
                  <div className="flex items-center text-[10.5px] leading-[1.25]" style={{ color: "var(--ink-3)" }}>{label} {hrs}</div>
                  {meta.days.map((_, di) => {
                    const key = `${di}-${bi}`;
                    const off = !!availOff[key];
                    return (
                      <button
                        key={key}
                        onClick={() => onAvail(key)}
                        className="overflow-hidden text-ellipsis whitespace-nowrap rounded-[10px] p-[10px_2px] text-[10.5px]"
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
        </>
      )}
        </div>
      </div>
    </div>
  );
}

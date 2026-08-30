"use client";
import { useMemo } from "react";
import type { Course, DraftRow, HistoryRecord, Meta, PastWeek, Session, SME } from "@/lib/types";
import { avatarBg, initials, isLive, liveRows } from "@/lib/view";

interface Props {
  weeks: PastWeek[];
  selected: string;
  sessions: Record<string, Session[]>;
  smes: SME[];
  history: HistoryRecord[];
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

export default function PastWeeks({ weeks, selected, sessions, smes, history, courses, meta, vh, onSelect }: Props) {
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

      <section className="card flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="flex shrink-0 items-center gap-3 p-[13px_20px_11px]" style={{ borderBottom: "0.5px solid rgba(16,26,51,0.06)" }}>
          <div>
            <div className="text-[13px] font-bold">SME performance</div>
            <div className="mt-[2px] text-[11px]" style={{ color: "var(--muted)" }}>
              Ratings and completed teaching load for {week.range}
            </div>
          </div>
          <span className="ml-auto text-[11.5px]" style={{ color: "var(--muted)" }}>{perSme.length} teachers</span>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full min-w-[860px] border-collapse">
            <thead>
              <tr className="label-caps text-left">
                <th className="p-[10px_20px] font-semibold">SME</th>
                <th className="p-[10px] font-semibold">Subject pool</th>
                <th className="w-[130px] p-[10px] font-semibold">Classes taken</th>
                <th className="w-[220px] p-[10px] font-semibold">Batches covered</th>
                <th className="w-[190px] p-[10px] font-semibold">Session rating</th>
                <th className="w-[170px] p-[10px_20px] font-semibold">Cancellations / surplus</th>
              </tr>
            </thead>
            <tbody>
              {perSme.map(({ sme, count, batches }) => {
                const historical = history.find((h) => h.sme_id === sme.id && h.week === week.iso)
                  ?? sme.history.find((h) => h.week === week.iso);
                const values = Object.values(historical?.per_topic_rating ?? {});
                const sessionRating = historical?.post_session_rating
                  ?? (values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : sme.rating);
                const cancellations = rows.filter((r) => r.sme_id === sme.id && r.cancelled).length;
                const surplus = Math.max(0, count - sme.preferred);
                const mergedHosted = rows.filter((r) => r.merged_into)
                  .filter((r) => rows.find((host) => host.session_id === r.merged_into)?.sme_id === sme.id).length;
                return (
                  <tr key={sme.id} className="hover:bg-[rgba(79,149,216,0.04)]" style={{ borderTop: "0.5px solid rgba(16,26,51,0.06)" }}>
                    <td className="p-[12px_20px]">
                      <div className="flex items-center gap-[9px]">
                        <span className="grid size-[30px] shrink-0 place-items-center rounded-full text-[10.5px] font-bold"
                          style={{ background: avatarBg(sme.id), color: "var(--brand-deep)" }}>
                          {initials(sme.name)}
                        </span>
                        <span>
                          <span className="block text-[12.5px] font-semibold">{sme.name}</span>
                          <span className="block text-[10.5px]" style={{ color: "var(--muted)" }}>{sme.id} · lifetime ★ {sme.rating.toFixed(1)}</span>
                        </span>
                      </div>
                    </td>
                    <td className="p-[12px_10px] text-[12px]">{sme.subject} pool</td>
                    <td className="p-[12px_10px]"><span className="text-[14px] font-bold">{count}</span><span className="ml-1 text-[11px]" style={{ color: "var(--muted)" }}>classes</span></td>
                    <td className="p-[12px_10px] text-[11.5px]" style={{ color: "var(--ink-3)" }}>{[...batches].sort().join(" · ")}</td>
                    <td className="p-[12px_10px]">
                      <div className="flex items-center gap-2">
                        <span className="text-[13px] font-bold" style={{ color: "var(--green-ink)" }}>★ {sessionRating.toFixed(1)}</span>
                        <span className="h-[5px] w-[82px] overflow-hidden rounded-full" style={{ background: "var(--line-2)" }}>
                          <span className="block h-full rounded-full" style={{ width: `${(sessionRating / 5) * 100}%`, background: "var(--green)" }} />
                        </span>
                      </div>
                    </td>
                    <td className="p-[12px_20px]">
                      <span className="flex flex-wrap gap-[5px]">
                        {cancellations > 0 && (
                          <span className="rounded-[8px] px-2 py-[4px] text-[10.5px] font-bold" style={{ background: "var(--red-tint)", color: "var(--red-ink)" }}>
                            {cancellations} cancelled
                          </span>
                        )}
                        {surplus > 0 && (
                          <span className="rounded-[8px] px-2 py-[4px] text-[10.5px] font-bold" style={{ background: "var(--green-tint)", color: "var(--green-ink)" }}>
                            +{surplus} surplus
                          </span>
                        )}
                        {mergedHosted > 0 && (
                          <span className="rounded-[8px] px-2 py-[4px] text-[10.5px] font-bold" style={{ background: "var(--brand-tint)", color: "var(--brand-deep)" }}>
                            {mergedHosted} merged
                          </span>
                        )}
                        {!cancellations && !surplus && !mergedHosted && <span className="text-[11.5px]" style={{ color: "var(--muted)" }}>—</span>}
                      </span>
                    </td>
                  </tr>
                );
              })}
              {!perSme.length && (
                <tr><td colSpan={6} className="p-8 text-center text-[12.5px]" style={{ color: "var(--muted)" }}>Nothing ran that week.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

export { asRows, isLive };

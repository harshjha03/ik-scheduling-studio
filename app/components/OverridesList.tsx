import type { OverrideEvent } from "@/lib/types";

const KIND_STYLE: Record<string, { bg: string; fg: string }> = {
  "teacher change": { bg: "var(--brand-tint)", fg: "var(--brand-deep)" },
  assigned: { bg: "var(--green-tint)", fg: "var(--green-ink)" },
  "change requested": { bg: "var(--sand-tint)", fg: "var(--sand-ink)" },
};

export default function OverridesList({ log, smeName, onOpen }: {
  log: OverrideEvent[];
  smeName: (id: string | null) => string;
  onOpen: (o: OverrideEvent) => void;
}) {
  if (!log.length) {
    return (
      <div className="p-[34px_8px] text-center text-[13px] leading-[1.6]" style={{ color: "var(--muted)" }}>
        No overrides yet this session.
        <br />
        Open a class from the calendar and change its teacher — every change lands here with what it displaced.
      </div>
    );
  }
  return (
    <div className="px-5 pb-5 pt-2">
      {log.map((o, i) => {
        const tone = KIND_STYLE[o.kind] ?? KIND_STYLE["teacher change"];
        return (
          <div key={`${o.session_id}-${i}`} className="flex items-start gap-[14px] py-[14px]" style={{ borderBottom: "1px solid var(--line-2)" }}>
            <span
              className="shrink-0 rounded-[9px] px-[9px] py-1 text-[10px] font-bold"
              style={{ background: tone.bg, color: tone.fg, letterSpacing: "0.03em" }}
            >
              {o.kind}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-semibold">
                {o.batch_id} · {o.session_id} — {o.from_sme_id ? `${smeName(o.from_sme_id)} → ` : "assigned "}
                <b>{o.to_sme_name}</b>
              </div>
              <div className="mt-[3px] text-[12px] leading-[1.5]" style={{ color: "var(--ink-3)" }}>{o.note}</div>
              <div
                className="mt-[3px] text-[11.5px]"
                style={{ color: o.reverted ? "var(--red-ink)" : "var(--brand-deep)", fontWeight: o.reverted ? 650 : 400 }}
              >
                {o.changed_rows === undefined
                  ? "pending re-run — −0.2 on the old pairing, +0.1 on yours"
                  : o.reverted
                    ? `re-run could not keep ${o.to_sme_name} — the pick breaks a hard rule, so the class went back to the pipeline`
                    : o.changed_rows.length
                      ? `re-run changed: ${o.changed_rows.join(", ")}`
                      : "re-run: no other row changed because of this override"}
              </div>
            </div>
            <span className="whitespace-nowrap text-[11px]" style={{ color: "var(--muted-2)" }}>
              {new Date(o.at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}
            </span>
            <button className="btn btn-sm" onClick={() => onOpen(o)}>Open class</button>
          </div>
        );
      })}
    </div>
  );
}

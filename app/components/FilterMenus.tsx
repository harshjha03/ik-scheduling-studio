"use client";
import { useEffect, useRef, useState } from "react";
import type { Batch, Course, DraftRow } from "@/lib/types";


function useDismiss(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const down = (e: PointerEvent) => {
      if (ref.current && e.target instanceof Node && !ref.current.contains(e.target)) onClose();
    };
    document.addEventListener("keydown", key);
    document.addEventListener("pointerdown", down, true);
    return () => {
      document.removeEventListener("keydown", key);
      document.removeEventListener("pointerdown", down, true);
    };
  }, [onClose]);
  return ref;
}

export function BatchMenu({ batches, courses, rows, value, onChange }: {
  batches: Batch[]; courses: Record<string, Course>; rows: DraftRow[];
  value: string; onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useDismiss(() => setOpen(false));
  const opts = [
    { id: "all", label: "All batches", course: null as string | null, meta: `${rows.length} classes` },
    ...batches.map((b) => ({
      id: b.id, label: b.id, course: b.course,
      meta: `${b.course} · ${rows.filter((r) => r.batch_id === b.id).length} classes`,
    })),
  ];
  const needle = q.trim().toLowerCase();
  const shown = opts.filter((o) =>
    !needle || o.label.toLowerCase().includes(needle) ||
    (o.course && courses[o.course]?.name.toLowerCase().includes(needle)));
  const label = value === "all" ? "All batches" : value;
  const note = value === "all"
    ? `${batches.length} batches · ${rows.length} classes this week`
    : `${courses[batches.find((b) => b.id === value)?.course ?? ""]?.name ?? ""} · ${rows.filter((r) => r.batch_id === value).length} classes this week`;

  return (
    <div ref={ref} className="relative flex items-center gap-[9px]">
      <button
        onClick={() => { setOpen(!open); setQ(""); }}
        className="flex items-center gap-[9px] rounded-[11px] bg-white px-[13px] py-2 text-[12.5px] font-semibold"
        style={{ border: `1px solid ${open ? "var(--brand-ring)" : "var(--field)"}`, color: "var(--ink)", cursor: "pointer" }}
      >
        <span>{label}</span>
        <span className="text-[9px]" style={{ color: "var(--muted-3)" }}>▾</span>
      </button>
      <span className="text-[11.5px]" style={{ color: "var(--muted)" }}>{note}</span>
      {open && (
        <div className="glass popover absolute left-0 top-[46px] w-[264px] overflow-hidden">
          <div className="p-[10px_12px]" style={{ borderBottom: "1px solid var(--line-2)" }}>
            <input
              autoFocus className="field w-full" placeholder="Search batch or course…"
              value={q} onChange={(e) => setQ(e.target.value)}
            />
          </div>
          <div className="max-h-[260px] overflow-y-auto p-[6px]">
            {shown.map((o) => (
              <button
                key={o.id}
                onClick={() => { onChange(o.id); setOpen(false); }}
                className="block w-full rounded-[10px] px-[10px] py-2 text-left hover:bg-[rgba(47,95,208,0.07)]"
                style={{ border: "none", background: value === o.id ? "#f1f5fa" : "transparent", cursor: "pointer", color: "var(--ink)" }}
              >
                <span className="flex w-full items-center gap-2">
                  <span
                    className="size-2 shrink-0 rounded-full"
                    style={{ background: o.course ? courses[o.course]?.accent : "var(--brand)" }}
                  />
                  <span className="text-[12.5px] font-semibold">{o.label}</span>
                  <span className="ml-auto text-[11px]" style={{ color: "var(--muted)" }}>{o.meta}</span>
                </span>
              </button>
            ))}
            {!shown.length && (
              <div className="px-[10px] py-4 text-center text-[12px]" style={{ color: "var(--muted)" }}>
                No batch matches that.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

"use client";
import type { ImportIssue } from "@/lib/import";

export interface ImportStep { n: string; title: string; sub: string; action?: string; onAction?: () => void }
export interface ImportTally { value: number; label: string; tone: "good" | "bad" | "warn" }
export interface ImportPreviewRow { key: string; tag: string; main: string; when: string; who: string; whoTone: "good" | "warn" | "plain" }

interface Props {
  /** the steps + dropzone only show until a file has been checked */
  steps: ImportStep[] | null;
  /** the artboard sizes the step numerals differently per importer: 24/11.5 classes, 22/11 SMEs */
  stepSize: 22 | 24;
  /** the "still incomplete" tally ink: #8a6512 for classes, #8a5218 for SMEs */
  warnInk: string;
  dropTitle: string;
  dropSub: string;
  onFile: (file: File) => void;
  /** Pull the same columns straight from a Google Sheet tab. Absent when the importer has no tab. */
  onPullSheet?: () => void;
  /** live | simulated, from /api/integrations — never claim a source we cannot read */
  sheetLive?: boolean;
  sheetBusy?: boolean;
  /** an extra template download offered alongside the main one (the history contract) */
  historyAction?: { label: string; onClick: () => void };
  tallies?: ImportTally[] | null;
  issues?: ImportIssue[] | null;
  preview?: ImportPreviewRow[] | null;
}

// Values are the artboard's own, not the app's shared classes — `.label-caps` and `.btn-sm` are a
// half-pixel off the design here, and this sheet is the one place that difference is visible.
const TALLY = {
  good: { border: "#c2e2ce", background: "#f6fbf8", color: "var(--green-ink)" },
  bad: { border: "#f0c7c0", background: "#fdf8f7", color: "var(--red-ink)" },
  warn: { border: "#eddba6", background: "#fdfaf2", color: "" },     // ink set per importer
};
const WHO = {
  good: { background: "#e6f2ec", color: "#14684a", borderRadius: 8 },
  warn: { background: "#fbf3e3", color: "#8a6512", borderRadius: 8 },
  plain: { background: "#eef1f6", color: "#42506b", borderRadius: 7 },
};
const SECTION_LABEL: React.CSSProperties = {
  fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em",
  color: "var(--muted)", marginBottom: 8,
};

function Section({ label, gap, maxHeight, children }: {
  label: string; gap: number; maxHeight: number; children: React.ReactNode;
}) {
  return (
    <div>
      <div style={SECTION_LABEL}>{label}</div>
      <div className="flex flex-col overflow-y-auto" style={{ gap, maxHeight }}>{children}</div>
    </div>
  );
}

/** Shared by the class and SME importers — same shape, different rows. */
export default function ImportSheet({
  steps, stepSize, warnInk, dropTitle, dropSub, onFile, onPullSheet, sheetLive, sheetBusy,
  historyAction, tallies, issues, preview,
}: Props) {
  return (
    <div className="flex flex-col gap-[13px]">
      {steps && (
        <>
          <div className="flex flex-col gap-[9px]">
            {steps.map((s) => (
              <div key={s.n} className="flex items-start gap-[11px]">
                <span
                  className="flex shrink-0 items-center justify-center rounded-full font-bold"
                  style={{ width: stepSize, height: stepSize, fontSize: stepSize === 24 ? 11.5 : 11,
                    background: "var(--brand-tint)", color: "var(--brand-deep)" }}
                >
                  {s.n}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[12.5px] font-semibold leading-[1.35]">{s.title}</span>
                  <span className="mt-[2px] block text-[11.5px] leading-[1.5]" style={{ color: "var(--muted)" }}>{s.sub}</span>
                </span>
                {s.action && (
                  <button
                    className="shrink-0 cursor-pointer border-none font-bold text-white"
                    style={{ borderRadius: 10, background: "var(--brand)", padding: "7px 13px", fontSize: 11.5 }}
                    onClick={s.onAction}
                  >
                    {s.action}
                  </button>
                )}
              </div>
            ))}
          </div>
          <label
            className="relative block cursor-pointer rounded-[16px] p-[20px_16px] text-center"
            style={{ border: "1.5px dashed var(--brand-ring)", background: "#F4F8FD" }}
          >
            <span className="text-[22px] leading-none">📄</span>
            <span className="mt-[7px] block text-[12.5px] font-bold">{dropTitle}</span>
            <span className="mt-[3px] block text-[11.5px]" style={{ color: "var(--muted)" }}>{dropSub}</span>
            <input
              type="file"
              accept=".csv,text/csv,.xlsx,.xls"
              className="absolute inset-0 size-full cursor-pointer opacity-0"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }}
            />
          </label>
          {onPullSheet && (
            /* Same path as the file: the tab is fetched as CSV text and goes through the very same
               checks and preview. Rendered as one more step so it reads like the rest of the sheet. */
            <div className="flex items-start gap-[11px]">
              <span
                className="flex shrink-0 items-center justify-center rounded-full font-bold"
                style={{ width: stepSize, height: stepSize, fontSize: stepSize === 24 ? 11.5 : 11,
                  background: "var(--brand-tint)", color: "var(--brand-deep)" }}
              >
                or
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[12.5px] font-semibold leading-[1.35]">Pull it from your Google Sheet instead</span>
                <span className="mt-[2px] block text-[11.5px] leading-[1.5]" style={{ color: "var(--muted)" }}>
                  {sheetLive
                    ? "Same columns, same checks — the tab is read live and nothing is created until you review it."
                    : "Not connected — set SHEET_ID and Google credentials to read a tab directly. Until then, upload the file."}
                </span>
              </span>
              <button
                className="shrink-0 cursor-pointer border-none font-bold text-white disabled:opacity-60"
                style={{ borderRadius: 10, background: "var(--brand)", padding: "7px 13px", fontSize: 11.5 }}
                disabled={sheetBusy || !sheetLive}
                onClick={onPullSheet}
              >
                {sheetBusy ? "Pulling…" : "Pull from Sheet"}
              </button>
            </div>
          )}
          {historyAction && (
            <button className="btn-quiet self-start text-[11.5px]" onClick={historyAction.onClick}>
              {historyAction.label}
            </button>
          )}
        </>
      )}

      {tallies && (
        <div className="flex flex-wrap gap-[9px]">
          {tallies.map((t) => (
            <div key={t.label} className="min-w-[104px] rounded-[14px] p-[11px_14px]"
              style={{ ...TALLY[t.tone], color: TALLY[t.tone].color || warnInk, border: `1px solid ${TALLY[t.tone].border}` }}>
              <span className="block text-[20px] font-bold leading-none tracking-[-0.02em]">{t.value}</span>
              <span className="mt-[4px] block text-[11px] font-semibold">{t.label}</span>
            </div>
          ))}
        </div>
      )}

      {issues && issues.length > 0 && (
        <Section label="Rows we could not use" gap={6} maxHeight={190}>
          {issues.map((e, i) => (
            <div key={i} className="flex items-start gap-[9px] rounded-[11px] p-[8px_11px]"
              style={{ border: "1px solid #f0c7c0", background: "#fdf8f7" }}>
              <span className="shrink-0 rounded-[6px] px-[7px] py-[2px] text-[10px] font-bold"
                style={{ background: "#fbebe8", color: "var(--red-ink)" }}>{e.line}</span>
              <span className="min-w-0 flex-1 text-[11.5px] leading-[1.45]" style={{ color: "#8a2318" }}>{e.msg}</span>
            </div>
          ))}
        </Section>
      )}

      {preview && preview.length > 0 && (
        <Section label="Ready to import" gap={5} maxHeight={210}>
          {preview.map((p) => (
            <div key={p.key} className="flex items-center gap-[9px] rounded-[11px] bg-white p-[8px_11px]"
              style={{ border: "1px solid var(--line)" }}>
              <span className="shrink-0 rounded-[6px] px-[7px] py-[2px] text-[10px] font-bold"
                style={{ background: "var(--brand-tint)", color: "var(--brand-deep)" }}>{p.tag}</span>
              <span className="min-w-0 flex-1 truncate text-[11.5px] font-semibold">{p.main}</span>
              <span className="shrink-0 text-[11px]" style={{ color: "var(--muted)" }}>{p.when}</span>
              <span className="shrink-0 px-[8px] py-[3px] text-[10.5px]" style={{ ...WHO[p.whoTone], fontWeight: 650 }}>{p.who}</span>
            </div>
          ))}
        </Section>
      )}
    </div>
  );
}

"use client";
import type { Fix, ResolvedEntry, WorkItem } from "@/lib/types";
import { FLAG_LABEL, SEV_CHIP, initials, avatarBg } from "@/lib/view";

interface Props {
  items: WorkItem[];
  /** proposal per item key — computed once by the page so the list and the actions agree */
  fixes: Map<string, Fix | null>;
  /** true once ops asked for a plan; suggestions are hidden until then */
  auto: boolean;
  resolved: ResolvedEntry[];
  onReview: () => void;
  onDiscard: () => void;
  onApplyFix: (item: WorkItem, fix: Fix) => void;
  onApplyAll: () => void;
  onUndo: (index: number) => void;
  onOpenClass: (item: WorkItem) => void;
  onOpenSme: (smeId: string) => void;
}

const CHIP_TONE = {
  good: { background: "var(--green-tint)", color: "var(--green-ink)" },
  warn: { background: "var(--amber-tint)", color: "var(--amber-ink)" },
  neutral: { background: "#fff", color: "#42506b", border: "1px solid #dfe7f2" },
} as const;

export default function WorkSheet({
  items, fixes, auto, resolved, onReview, onDiscard, onApplyFix, onApplyAll, onUndo, onOpenClass, onOpenSme,
}: Props) {
  const fixable = items.filter((w) => fixes.get(w.key)).length;
  const done = resolved.length;
  const blocking = items.filter((w) => w.blocking);
  const advisory = items.filter((w) => !w.blocking);

  if (!items.length) {
    return (
      <>
        {!!resolved.length && <Resolved resolved={resolved} onUndo={onUndo} />}
        <div className="px-2 pb-[22px] pt-[26px] text-center">
          <div
            className="mx-auto mb-3 flex size-[46px] items-center justify-center rounded-full text-[20px] font-bold"
            style={{ background: "var(--green-tint)", color: "var(--green-ink)" }}
          >
            ✓
          </div>
          <div className="text-[15px] font-bold" style={{ letterSpacing: "-0.01em" }}>Nothing left to decide</div>
          <div className="mx-auto mt-[5px] max-w-[38ch] text-[12.5px] leading-[1.55]" style={{ color: "var(--muted)" }}>
            Every unfilled class, conflict and workload flag for this week is handled. Approve the week when you are ready.
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      {/* the assist never acts on its own — it drafts, ops approves */}
      <div
        className="flex items-start gap-3 rounded-[16px] p-[13px_15px]"
        style={auto
          ? { border: "1px solid var(--brand-line)", background: "var(--brand-tint)" }
          : { border: "1px solid #dfe7f2", background: "var(--brand-soft)" }}
      >
        <span
          className="flex size-8 shrink-0 items-center justify-center rounded-[10px] text-[15px]"
          style={{ background: auto ? "#fff" : "var(--brand-tint)", border: "1px solid var(--brand-line)" }}
        >
          ⚡
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[12.5px] font-bold leading-[1.35]" style={{ color: auto ? "var(--brand-deep)" : "var(--ink)" }}>
            {auto
              ? fixable ? `Plan ready — ${fixable} of ${items.length} can be fixed now` : "No fix I can propose"
              : "Let ops assist draft the fixes"}
          </span>
          <span className="mt-[3px] block text-[11.5px] leading-[1.5]" style={{ color: "var(--muted)" }}>
            {auto
              ? fixable
                ? "Each suggestion names the teacher and why they fit. Approve them one by one, or approve the lot."
                : "The rest need a judgement call — open the class and decide."
              : fixable
                ? `I can propose a teacher or an accepted exception for ${fixable} of these ${items.length}. You approve each one — nothing changes until you do.`
                : "These all need a judgement call, but I can show you what I would do."}
          </span>
          {auto && (
            <span className="relative mt-[10px] block h-1 overflow-hidden rounded-[3px]" style={{ background: "#dfe7f2" }}>
              <span
                className="absolute inset-y-0 left-0 rounded-[3px]"
                style={{ width: `${Math.round((done / Math.max(1, done + items.length)) * 100)}%`, background: "var(--brand-bright)" }}
              />
            </span>
          )}
        </span>
        <span className="flex shrink-0 flex-col gap-[6px]">
          {auto ? (
            <>
              {!!fixable && (
                <button className="btn btn-brand btn-sm" onClick={onApplyAll} title="Applies every suggestion above">
                  Approve all {fixable}
                </button>
              )}
              <button className="btn-quiet" onClick={onDiscard} title="Clears the suggestions">Discard plan</button>
            </>
          ) : (
            <button className="btn btn-brand btn-sm" onClick={onReview} title="Drafts a fix for every item it can">
              Review suggestions
            </button>
          )}
        </span>
      </div>

      {!!resolved.length && <Resolved resolved={resolved} onUndo={onUndo} />}

      <div className="flex flex-col gap-[15px]">
        {[
          { label: "Blocking publish", list: blocking, tone: "#c0392b", note: "must be cleared" },
          { label: "Advisory", list: advisory, tone: "#d18b3c", note: "can ship as-is" },
        ].filter((g) => g.list.length).map((g) => (
          <div key={g.label}>
            <div className="mb-[9px] flex items-center gap-2">
              <span className="size-2 shrink-0 rounded-full" style={{ background: g.tone }} />
              <span className="text-[11px] font-bold uppercase" style={{ letterSpacing: "0.07em", color: "var(--muted)" }}>{g.label}</span>
              <span
                className="rounded-[7px] px-[7px] text-[10.5px] font-bold tabular-nums"
                style={g.tone === "#c0392b"
                  ? { background: "var(--red-tint)", color: "var(--red-ink)" }
                  : { background: "var(--amber-tint)", color: "var(--amber-ink)" }}
              >
                {g.list.length}
              </span>
              <span className="ml-auto text-[11px]" style={{ color: "var(--muted-2)" }}>{g.note}</span>
            </div>
            <div className="flex flex-col gap-[9px]">
              {g.list.map((w) => {
                const fix = auto ? fixes.get(w.key) ?? null : null;
                return (
                  <div
                    key={w.key}
                    className="rounded-[14px] bg-white p-[13px_15px]"
                    style={{
                      border: `1px solid ${w.blocking ? "var(--red-line)" : "var(--amber-line)"}`,
                      borderLeft: `3px solid ${w.blocking ? "var(--red)" : "var(--amber)"}`,
                    }}
                  >
                    <div className="flex items-start gap-[10px]">
                      <span className={`chip ${SEV_CHIP[w.severity]}`}>{w.code === "LEAVE" ? "LEAVE" : FLAG_LABEL[w.code]}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-[12.5px] font-semibold leading-[1.35]">{w.title}</span>
                        <span className="mt-[3px] block text-[11.5px] leading-[1.5]" style={{ color: "var(--muted)" }}>{w.detail}</span>
                      </span>
                      <span className="shrink-0 whitespace-nowrap text-[11px] font-semibold" style={{ color: "var(--muted-2)" }}>{w.when}</span>
                    </div>

                    {fix && (
                      <div
                        className="mt-[11px] flex items-start gap-[11px] rounded-[12px] p-[11px_12px]"
                        style={{ border: "1px solid var(--brand-line)", background: "var(--brand-soft)" }}
                      >
                        <span
                          className="flex size-[34px] shrink-0 items-center justify-center font-bold"
                          style={{
                            borderRadius: fix.who ? "50%" : 11, fontSize: fix.who ? 11 : 14, color: "var(--brand-deep)",
                            background: fix.who ? avatarBg(fix.who.id) : "var(--brand-tint)",
                          }}
                        >
                          {fix.who ? initials(fix.who.name) : "⚡"}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="label-caps block" style={{ letterSpacing: "0.07em" }}>Suggested fix</span>
                          <span className="mt-[2px] block text-[12.5px] font-bold">{fix.label}</span>
                          <span className="mt-[6px] flex flex-wrap gap-[5px]">
                            {fix.chips.map(([text, tone]) => (
                              <span key={text} className="whitespace-nowrap rounded-[7px] px-2 py-[3px] text-[10.5px] font-semibold" style={CHIP_TONE[tone]}>
                                {text}
                              </span>
                            ))}
                          </span>
                        </span>
                      </div>
                    )}

                    <div className="mt-[11px] flex items-center gap-3">
                      {fix && (
                        <button className="btn btn-brand btn-sm" title={fix.why} onClick={() => onApplyFix(w, fix)}>Approve fix</button>
                      )}
                      {w.session_id && (
                        <button
                          className={fix ? "btn-quiet" : "btn btn-brand btn-sm"}
                          title="Opens the class so you can decide by hand"
                          onClick={() => onOpenClass(w)}
                        >
                          {fix ? "Choose someone else" : w.code === "UNFILLED" ? "Pick a teacher" : "Open the class"}
                        </button>
                      )}
                      {w.code === "LEAVE" && w.sme_id && (
                        <button className="btn-quiet" title="See their week and availability" onClick={() => onOpenSme(w.sme_id!)}>
                          Open SME profile
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function Resolved({ resolved, onUndo }: { resolved: ResolvedEntry[]; onUndo: (i: number) => void }) {
  return (
    <div className="flex flex-col gap-[6px]">
      {resolved.map((r, i) => (
        <div
          key={r.key + i}
          className="flex items-center gap-[10px] rounded-[12px] p-[9px_12px]"
          style={{ border: "1px solid var(--green-line)", background: "#f6fbf8" }}
        >
          <span className="shrink-0 text-[12px] font-bold" style={{ color: "var(--green-ink)" }}>✓</span>
          <span className="min-w-0 flex-1 text-[12px] leading-[1.45]" style={{ color: "var(--green-ink)" }}>{r.text}</span>
          <button className="btn-quiet shrink-0" onClick={() => onUndo(i)}>Undo</button>
        </div>
      ))}
    </div>
  );
}

"use client";
import { useEffect, useRef } from "react";
import type { ChatTurn, DraftRow, SME } from "@/lib/types";
import { AnswerText, PlanCard, StatusBanner, Working } from "./AgentSheet";

interface Props {
  open: boolean;
  turns: ChatTurn[];
  draft: string;
  busy: boolean;
  smes: SME[];
  rows: DraftRow[];
  days: string[];
  onOpen: (open: boolean) => void;
  onDraft: (text: string) => void;
  onSend: () => void;
  onApply: (index: number) => void;
  onReset: () => void;
}

const SUGGESTIONS = [
  "Who is overloaded this week?",
  "Why is a class unfilled?",
  "Rahul is out on Tuesday — find cover",
];

/** Floating copilot: one button, one conversation. Every reply carries its own evidence and, when the
 *  copilot proposes a change, its own plan card — so an old plan can never be applied by a new answer. */
export default function CopilotChat({
  open, turns, draft, busy, smes, rows, days, onOpen, onDraft, onSend, onApply, onReset,
}: Props) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ block: "end" });
  }, [open, turns, busy]);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onOpen(false); };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [open, onOpen]);

  if (!open) {
    return (
      <button
        onClick={() => onOpen(true)}
        aria-label="Open the scheduling copilot"
        title="Ask the copilot — questions about this week, or tell it who dropped out"
        className="fixed flex items-center gap-[9px] rounded-full p-[13px_18px] text-[13px] font-semibold"
        style={{
          right: 26, bottom: 24, zIndex: 55, border: "none", cursor: "pointer", color: "#fff",
          background: "linear-gradient(135deg, var(--brand) 0%, var(--brand-deep) 100%)",
          boxShadow: "0 12px 30px rgba(16,26,51,0.28), 0 0 0 0.5px rgba(16,26,51,0.08)",
        }}
      >
        <span aria-hidden style={{ fontSize: 15, lineHeight: 1 }}>✦</span>
        Copilot
        {!!turns.length && (
          <span className="rounded-[7px] px-[6px] text-[10.5px] font-bold tabular-nums" style={{ background: "rgba(255,255,255,0.22)" }}>
            {turns.filter((t) => t.role === "assistant").length}
          </span>
        )}
      </button>
    );
  }

  return (
    <section
      aria-label="Copilot chat"
      className="fixed flex flex-col rounded-[20px] bg-white"
      style={{
        right: 22, bottom: 20, width: 404, maxWidth: "calc(100vw - 44px)", height: 560, maxHeight: "calc(100vh - 60px)",
        zIndex: 55, boxShadow: "0 26px 64px rgba(16,26,51,0.26), 0 0 0 0.5px rgba(16,26,51,0.08)",
        animation: "sheetIn .32s cubic-bezier(.32,.72,0,1)",
      }}
    >
      <header className="flex items-center gap-[10px] p-[14px_16px]" style={{ borderBottom: "1px solid var(--line-2)" }}>
        <span
          className="flex size-[26px] shrink-0 items-center justify-center rounded-full text-[12px]"
          style={{ background: "var(--brand-tint)", color: "var(--brand-deep)" }}
          aria-hidden
        >
          ✦
        </span>
        <div className="min-w-0">
          <div className="text-[13.5px] font-bold leading-[1.25]">Scheduling copilot</div>
          <div className="text-[11px]" style={{ color: "var(--muted)" }}>Next week&apos;s draft · answers come from the engine</div>
        </div>
        <span className="ml-auto flex items-center gap-[6px]">
          {!!turns.length && (
            <button className="btn-quiet text-[11.5px]" onClick={onReset} disabled={busy} title="Start a new conversation">
              Clear
            </button>
          )}
          <button
            onClick={() => onOpen(false)}
            aria-label="Close the copilot"
            className="flex size-[26px] items-center justify-center rounded-[9px] text-[15px]"
            style={{ border: "none", background: "#f1f5fa", color: "var(--muted)", cursor: "pointer" }}
          >
            ×
          </button>
        </span>
      </header>

      <div className="sheet-scroll flex min-h-0 flex-1 flex-col gap-[11px] p-[14px_16px]">
        {!turns.length && (
          <div className="flex flex-col gap-[9px]">
            <p className="text-[12.5px] leading-[1.55]" style={{ color: "var(--ink-2)" }}>
              Ask about this week, or tell me who dropped out and I will find cover. I propose the change —
              you apply it, and the engine re-checks every rule.
            </p>
            {SUGGESTIONS.map((q) => (
              <button
                key={q} className="rounded-[13px] p-[9px_12px] text-left text-[12px]"
                style={{ border: "1px solid var(--line)", background: "#fff", cursor: "pointer", color: "var(--ink-2)" }}
                onClick={() => { onDraft(q); inputRef.current?.focus(); }}
              >
                {q}
              </button>
            ))}
          </div>
        )}

        {turns.map((t, i) => (
          t.role === "user" ? (
            <div key={i} data-turn="user" className="max-w-[86%] self-end rounded-[14px] p-[9px_12px] text-[12.5px] leading-[1.5]"
              style={{ background: "var(--brand-deep)", color: "#fff" }}>
              {t.content}
            </div>
          ) : (
            <div key={i} data-turn="assistant" className="flex max-w-[94%] flex-col gap-[9px] self-start">
              {t.res && <StatusBanner res={t.res} />}
              <div className="rounded-[14px] p-[10px_12px]" style={{ background: "var(--page)" }}>
                <AnswerText text={t.content} />
              </div>
              {t.res && !!t.res.transcript.length && <Working steps={t.res.transcript} />}
              {!!t.res?.plan?.length && (
                <div className="rounded-[14px] p-[11px_12px]" style={{ border: "1px solid var(--line)" }}>
                  <PlanCard plan={t.res.plan} answer="" rows={rows} smes={smes} days={days} bare />
                  {t.applied ? (
                    <div className="mt-[8px] text-[11.5px] font-semibold" style={{ color: "var(--green-ink)" }}>
                      ✓ Applied — logged in Overrides as Copilot, re-validated by Stage D.
                    </div>
                  ) : (
                    <button className="btn btn-go btn-sm mt-[9px]" disabled={busy} onClick={() => onApply(i)}>
                      Apply plan · {t.res.plan.length}
                    </button>
                  )}
                </div>
              )}
            </div>
          )
        ))}

        {busy && (
          <div className="self-start rounded-[14px] p-[9px_12px] text-[12.5px]" style={{ background: "var(--page)", color: "var(--muted)" }}>
            Working — reading the draft…
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="flex items-center gap-[8px] p-[12px_14px]" style={{ borderTop: "1px solid var(--line-2)" }}>
        <input
          ref={inputRef} className="field min-w-0 flex-1" value={draft} disabled={busy}
          placeholder="Ask, or say who is out…"
          onChange={(e) => onDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && draft.trim() && !busy) { e.preventDefault(); onSend(); } }}
        />
        <button className="btn btn-primary btn-sm" disabled={busy || !draft.trim()} onClick={onSend}>
          {busy ? "…" : "Send"}
        </button>
      </div>
    </section>
  );
}

"use client";
import { useEffect } from "react";
import { avatarBg, initials } from "@/lib/view";

export interface SheetFact { label: string; value: React.ReactNode }
export interface SheetAction { label: string; kind?: "primary" | "go" | "ghost"; onClick: () => void; disabled?: boolean }

interface Props {
  width?: number;
  eyebrow?: string;
  title: string;
  subtitle?: string;
  banner?: { text: string; tone: "green" | "amber" | "red" | "blue" } | null;
  facts?: SheetFact[];
  children?: React.ReactNode;
  footerNote?: string;
  footer?: SheetAction[];
  onClose: () => void;
}

const TONES = {
  green: { bg: "var(--green-tint)", fg: "var(--green-ink)" },
  amber: { bg: "var(--amber-tint)", fg: "var(--amber-ink)" },
  red: { bg: "var(--red-tint)", fg: "var(--red-ink)" },
  blue: { bg: "var(--brand-tint)", fg: "var(--brand-deep)" },
};

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="label-caps mb-2">{children}</div>;
}

export function PersonRow({ id, name, meta, right, onClick, tone }: {
  id: string; name: string; meta: React.ReactNode; right?: React.ReactNode; onClick?: () => void; tone?: "active" | "plain";
}) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      onClick={onClick}
      className="block w-full rounded-[16px] p-[11px_13px] text-left"
      style={{
        border: `1px solid ${tone === "active" ? "var(--brand-ring)" : "var(--line)"}`,
        background: tone === "active" ? "#eff4fd" : "#fff",
        cursor: onClick ? "pointer" : "default",
      }}
    >
      <span className="flex w-full items-center gap-[10px]">
        <span
          className="flex size-[30px] shrink-0 items-center justify-center rounded-full text-[10px] font-bold"
          style={{ background: avatarBg(id), color: "var(--ink-2)" }}
        >
          {initials(name)}
        </span>
        <span className="min-w-0 flex-1 text-left">
          <span className="block text-[12.5px] font-semibold">{name}</span>
          <span className="mt-px block text-[11px]" style={{ color: "var(--muted)" }}>{meta}</span>
        </span>
        {right}
      </span>
    </Tag>
  );
}

export default function Sheet({
  width = 520, eyebrow, title, subtitle, banner, facts, children, footerNote, footer, onClose,
}: Props) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose]);

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 flex items-center justify-center p-6"
      style={{
        background: "rgba(16,26,51,0.26)", backdropFilter: "blur(18px) saturate(140%)",
        WebkitBackdropFilter: "blur(18px) saturate(140%)", zIndex: 60, animation: "overlayIn .22s ease-out",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        className="flex flex-col rounded-[22px] bg-white"
        style={{
          width, maxWidth: "100%", maxHeight: "86vh",
          boxShadow: "0 32px 80px rgba(16,26,51,0.24), 0 0 0 0.5px rgba(16,26,51,0.06)",
          animation: "sheetIn .4s cubic-bezier(.32,.72,0,1)",
        }}
      >
        <div className="flex items-start gap-3 p-[20px_22px_16px]" style={{ borderBottom: "1px solid var(--line-2)" }}>
          <div className="min-w-0">
            {eyebrow && <div className="label-caps mb-[5px]" style={{ letterSpacing: "0.07em" }}>{eyebrow}</div>}
            <div className="text-[17px] font-bold leading-[1.3]" style={{ letterSpacing: "-0.01em" }}>{title}</div>
            {subtitle && <div className="mt-1 text-[12.5px] leading-[1.5]" style={{ color: "var(--muted)" }}>{subtitle}</div>}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="ml-auto flex size-[30px] shrink-0 items-center justify-center rounded-[10px] text-[16px]"
            style={{ border: "none", background: "#f1f5fa", color: "var(--muted)", cursor: "pointer" }}
          >
            ×
          </button>
        </div>

        <div className="sheet-scroll flex flex-col gap-[14px] p-[18px_22px_20px]">
          {banner && (
            <div
              className="rounded-[14px] p-[12px_14px] text-[12.5px] leading-[1.55]"
              style={{ background: TONES[banner.tone].bg, color: TONES[banner.tone].fg }}
            >
              {banner.text}
            </div>
          )}
          {facts && !!facts.length && (
            <div className="grid grid-cols-2 gap-[10px]">
              {facts.map((f) => (
                <div key={f.label} className="rounded-[14px] p-[11px_13px]" style={{ background: "var(--page)" }}>
                  <div className="label-caps">{f.label}</div>
                  <div className="mt-[5px] text-[13px] font-semibold leading-[1.35]">{f.value}</div>
                </div>
              ))}
            </div>
          )}
          {children}
        </div>

        {footer && !!footer.length && (
          <div className="flex items-center gap-[9px] p-[14px_22px_18px]" style={{ borderTop: "1px solid var(--line-2)" }}>
            {footerNote && (
              <span className="max-w-[52ch] text-[11.5px] leading-[1.45]" style={{ color: "var(--muted)" }}>{footerNote}</span>
            )}
            <span className="ml-auto flex gap-2">
              {footer.map((a) => (
                <button
                  key={a.label}
                  onClick={a.onClick}
                  disabled={a.disabled}
                  className={`btn ${a.kind === "primary" ? "btn-primary" : a.kind === "go" ? "btn-go" : ""}`}
                >
                  {a.label}
                </button>
              ))}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

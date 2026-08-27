"use client";
import type { LeafId, PublishLeaf, SendState } from "@/lib/types";

interface Props {
  leaves: PublishLeaf[];
  selected: Record<string, boolean>;
  status: Record<string, SendState>;
  /** while a send is in flight (or finished) the picker is frozen */
  locked: boolean;
  onToggleLeaf: (id: LeafId) => void;
  onToggleChannel: (ids: LeafId[], turnOn: boolean) => void;
}

const PILL: Record<SendState | "ready", { background: string; color: string }> = {
  sent: { background: "var(--green-tint)", color: "var(--green-ink)" },
  sending: { background: "var(--amber-tint)", color: "var(--amber-ink)" },
  ready: { background: "var(--brand-tint)", color: "var(--brand-deep)" },
  idle: { background: "#f1f5fa", color: "var(--muted-3)" },
};
const LABEL: Record<SendState | "ready", string> = { sent: "Sent ✓", sending: "Sending…", ready: "Ready", idle: "Skipped" };

function tickStyle(state: "all" | "some" | "none", leaf: boolean, locked: boolean) {
  const size = leaf ? 18 : 20;
  return {
    width: size, height: size, borderRadius: leaf ? 5 : 6, fontSize: leaf ? 11 : 12,
    display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
    fontWeight: 700, color: "#fff", cursor: locked ? "default" : "pointer", opacity: locked ? 0.6 : 1,
    background: state === "all" ? "var(--brand)" : state === "some" ? "var(--brand-ring)" : "#fff",
    border: `1px solid ${state === "none" ? "var(--brand-line)" : "var(--brand)"}`,
  } as const;
}

export default function PublishSheet({ leaves, selected, status, locked, onToggleLeaf, onToggleChannel }: Props) {
  const channels = [...new Map(leaves.map((l) => [l.channel.key, l.channel])).values()];
  return (
    <div className="flex flex-col gap-[10px]">
      {channels.map((ch) => {
        const mine = leaves.filter((l) => l.channel.key === ch.key);
        const onCount = mine.filter((l) => selected[l.id]).length;
        const state = onCount === mine.length ? "all" : onCount ? "some" : "none";
        const anySending = mine.some((l) => status[l.id] === "sending");
        const allSent = mine.every((l) => status[l.id] === "sent");
        const someSent = mine.some((l) => status[l.id] === "sent");
        const chState: SendState | "ready" = allSent || someSent ? "sent" : anySending ? "sending" : onCount ? "ready" : "idle";
        return (
          <div
            key={ch.key}
            className="rounded-[16px] p-[13px_15px]"
            style={{
              border: `1px solid ${allSent ? "var(--green-line)" : onCount ? "var(--brand-line)" : "var(--line)"}`,
              background: allSent ? "#f6fbf8" : onCount ? "var(--brand-soft)" : "#fff",
            }}
          >
            <div className="flex items-start gap-[11px]">
              <button
                style={tickStyle(state, false, locked)}
                onClick={() => !locked && onToggleChannel(mine.map((l) => l.id), state !== "all")}
                title={state === "all" ? `Turn off ${ch.title}` : `Send ${ch.title} to everyone`}
                aria-label={ch.title}
              >
                {state === "all" ? "✓" : state === "some" ? "–" : ""}
              </button>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-bold leading-[1.3]">{ch.title}</span>
                <span className="mt-[2px] block text-[11.5px] leading-[1.45]" style={{ color: "var(--muted)" }}>{ch.sub}</span>
              </span>
              <span className="shrink-0 rounded-[9px] px-[9px] py-[3px] text-[10.5px] font-bold" style={{ ...PILL[chState], letterSpacing: "0.02em" }}>
                {LABEL[chState]}
              </span>
            </div>
            <div className="mt-[10px] flex flex-col gap-[5px] pl-[31px]">
              {mine.map((l) => {
                const raw = status[l.id] ?? "idle";
                const leafState: SendState | "ready" = raw === "idle" ? (selected[l.id] ? "ready" : "idle") : raw;
                return (
                  <div
                    key={l.id}
                    className="flex items-center gap-[9px] rounded-[11px] p-[7px_10px]"
                    style={{
                      background: raw === "sent" ? "#f2f9f5" : selected[l.id] ? "#fff" : "#fafbfd",
                      border: `1px solid ${raw === "sent" ? "#d5ebdf" : selected[l.id] ? "var(--line)" : "#eef1f6"}`,
                    }}
                  >
                    <button
                      style={tickStyle(selected[l.id] ? "all" : "none", true, locked)}
                      onClick={() => !locked && onToggleLeaf(l.id)}
                      title={`${selected[l.id] ? "Skip" : "Include"} ${l.audience.label.toLowerCase()} for ${ch.title.toLowerCase()}`}
                      aria-label={`${ch.title} — ${l.audience.label}`}
                    >
                      {selected[l.id] ? "✓" : ""}
                    </button>
                    <span className="min-w-0 flex-1 text-[12px] font-semibold">{l.audience.label}</span>
                    <span className="shrink-0 tabular-nums text-[11px]" style={{ color: "var(--muted)" }}>{l.audience.count}</span>
                    <span className="shrink-0 rounded-[9px] px-[9px] py-[3px] text-[10.5px] font-bold" style={PILL[leafState]}>{LABEL[leafState]}</span>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

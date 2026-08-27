import type { LlmStats } from "@/lib/types";

const TITLES: Record<string, string> = {
  daily_quota_exhausted: "LLM daily limit reached",
  rate_limited: "LLM rate limit hit",
  provider_unavailable: "LLM provider temporarily unavailable",
  timeout: "LLM timed out",
  provider_error: "LLM provider error",
  not_configured: "No LLM configured",
};

export default function LlmBanner({ llm }: { llm: LlmStats }) {
  if (!llm.error_kind) return null;
  const rescued = llm.fallback === 0 && (llm.failover?.resolved ?? 0) > 0;
  const info = llm.error_kind === "not_configured";
  const tone = rescued || info || llm.error_kind !== "daily_quota_exhausted"
    ? (info
      ? { bg: "var(--brand-tint)", line: "#cfe0f6", ink: "var(--brand-deep)" }
      : { bg: "var(--amber-tint)", line: "var(--amber-line)", ink: "var(--amber-ink)" })
    : { bg: "var(--red-tint)", line: "var(--red-line)", ink: "var(--red-ink)" };

  return (
    <div
      role="alert"
      className="rounded-[16px] px-4 py-3 text-[12.5px] leading-relaxed"
      style={{ background: tone.bg, border: `1px solid ${tone.line}`, color: tone.ink, animation: "floatin .3s ease-out" }}
    >
      <b>{TITLES[llm.error_kind] ?? "LLM issue"}{rescued ? " — handled by fallback provider" : ""}</b>
      {" — "}
      {llm.message}
      {llm.error && !info && (
        <details className="mt-1 text-[11.5px] opacity-80">
          <summary className="cursor-pointer">provider response</summary>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-[11px]">{llm.error}</pre>
        </details>
      )}
    </div>
  );
}

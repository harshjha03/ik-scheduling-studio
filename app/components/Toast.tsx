export default function Toast({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <div
      role="status"
      className="fixed bottom-[22px] left-1/2 z-90 rounded-[16px] px-[19px] py-3 text-[12.5px] text-white"
      style={{
        transform: "translateX(-50%)", background: "var(--ink)", fontWeight: 550,
        boxShadow: "0 16px 40px rgba(16,26,51,0.32)", animation: "toastIn .42s cubic-bezier(.32,.72,0,1)", zIndex: 90,
      }}
    >
      {text}
    </div>
  );
}

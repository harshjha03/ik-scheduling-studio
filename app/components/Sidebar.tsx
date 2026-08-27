"use client";
import { useRef, useState } from "react";
import type { ModuleKey, Role } from "@/lib/types";
import { initials } from "@/lib/view";

export const MODULES: Record<ModuleKey, { label: string; sub: string }> = {
  dashboard: { label: "Dashboard", sub: "The week at a glance — batch calendars, conflicts and work items" },
  smes: { label: "SME management", sub: "Skills, level, availability, history and ratings in one place" },
  batches: { label: "Batch management", sub: "Course progress, running topics and weekly calendars per batch" },
  myweek: { label: "My teaching week", sub: "Your classes, availability and preferences" },
  mysched: { label: "My schedule", sub: "Your batch's classes and instructors" },
};

export const ROLE_MODULES: Record<Role, ModuleKey[]> = {
  coordinator: ["dashboard", "smes", "batches"],
  sme: ["myweek"],
  student: ["mysched"],
};

/** Single-path line icons, drawn at 18px with the current colour. */
export const ICONS: Record<ModuleKey, string> = {
  dashboard: "M4 4.8h6.4v5.4H4zM13.6 4.8H20v3.4h-6.4zM13.6 11.2H20v8h-6.4zM4 13.2h6.4v6H4z",
  smes: "M9.2 11.4a3.3 3.3 0 100-6.6 3.3 3.3 0 000 6.6zM3.2 19.6c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5M16.6 11.3a2.7 2.7 0 000-5.4M17.2 14.5c2.4.5 3.9 2.3 3.9 5.1",
  batches: "M12 3.2l8.2 4.3-8.2 4.3-8.2-4.3zM3.8 12.2l8.2 4.3 8.2-4.3M3.8 16.6l8.2 4.3 8.2-4.3",
  myweek: "M8 3v3.2M16 3v3.2M3.4 9.6h17.2M4.4 6.2h15.2v14.4H4.4zM8 13h3v3H8z",
  mysched: "M8 3v3.2M16 3v3.2M3.4 9.6h17.2M4.4 6.2h15.2v14.4H4.4zM8 13h3v3H8z",
};

export const PERSONA: Record<Role, { name: string; sub: string }> = {
  coordinator: { name: "Shruti Rao", sub: "Ops coordinator" },
  sme: { name: "Rahul Desai", sub: "SME · T14 · PM + DSA" },
  student: { name: "Aarav Shah", sub: "Student · DSA-01" },
};

interface Props {
  role: Role;
  mod: ModuleKey;
  pinned: boolean;
  onPinned: (v: boolean) => void;
  onRole: (r: Role) => void;
  onMod: (m: ModuleKey) => void;
}

export default function Sidebar({ role, mod, pinned, onPinned, onRole, onMod }: Props) {
  const [hover, setHover] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const open = pinned || hover;
  const persona = PERSONA[role];

  const enter = () => {
    if (timer.current) clearTimeout(timer.current);
    if (!pinned) timer.current = setTimeout(() => setHover(true), 80);
  };
  const leave = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setHover(false), 150);
  };

  return (
    <aside
      onMouseEnter={enter}
      onMouseLeave={leave}
      className="flex shrink-0 flex-col"
      style={{
        width: open ? 236 : 74,
        background: "rgba(255,255,255,0.78)",
        backdropFilter: "blur(24px) saturate(180%)",
        WebkitBackdropFilter: "blur(24px) saturate(180%)",
        borderRight: "0.5px solid rgba(16,26,51,0.08)",
        position: pinned ? "sticky" : "fixed",
        left: 0, top: 0, height: "100vh", zIndex: 36,
        transition: "width .34s cubic-bezier(.32,.72,0,1), box-shadow .3s ease",
        boxShadow: !pinned && hover ? "0 20px 54px rgba(16,26,51,0.16)" : undefined,
      }}
    >
      <div className={open
        ? "flex items-center gap-[11px] px-[14px] pt-5 pb-4"
        : "flex flex-col items-center gap-2 px-[14px] pt-5 pb-4"}
      >
        <span
          className="flex size-[34px] shrink-0 items-center justify-center rounded-[11px] text-[13px] font-bold text-white"
          style={{ background: "#141b34", letterSpacing: "-0.02em" }}
        >
          IK
        </span>
        {open && (
          <span className="min-w-0">
            <span className="block whitespace-nowrap text-[13.5px] font-bold" style={{ letterSpacing: "-0.01em" }}>
              Interview Kickstart
            </span>
            <span className="mt-px block whitespace-nowrap text-[11.5px]" style={{ color: "var(--muted)" }}>
              Scheduling studio
            </span>
          </span>
        )}
        <button
          onClick={() => { onPinned(!pinned); setHover(false); }}
          title={pinned ? "Unpin — menu will peek on hover" : "Pin menu open"}
          aria-label={pinned ? "Unpin menu" : "Open menu"}
          className="flex size-[26px] shrink-0 items-center justify-center rounded-[9px] bg-white text-[12px]"
          // always visible: hovering is not an option on touch, and the role switcher lives behind it
          style={{
            border: "0.5px solid rgba(16,26,51,0.14)", color: "var(--muted)",
            marginLeft: open ? "auto" : undefined,
          }}
        >
          {pinned ? "«" : "»"}
        </button>
      </div>

      <nav className="flex flex-col gap-[3px] px-[10px]">
        {ROLE_MODULES[role].map((m) => {
          const on = mod === m;
          const label = MODULES[m].label;
          return (
            <button
              key={m}
              onClick={() => onMod(m)}
              title={label}
              className="flex w-full items-center gap-[6px] rounded-[12px] px-3 py-[10px] text-[13px]"
              style={{
                background: on ? "var(--brand-tint)" : "none",
                color: on ? "var(--brand-deep)" : "var(--ink-3)",
                fontWeight: on ? 650 : 550,
                justifyContent: open ? "flex-start" : "center",
                border: "none", cursor: "pointer",
              }}
            >
              <svg
                width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className="shrink-0"
              >
                <path d={ICONS[m]} />
              </svg>
              {open && <span className="overflow-hidden text-ellipsis whitespace-nowrap">{label}</span>}
            </button>
          );
        })}
      </nav>

      <div
        className="mt-auto"
        style={{
          margin: open ? "auto 12px 16px" : "auto 10px 16px",
          padding: open ? 14 : "10px 8px",
          borderRadius: open ? 16 : 14,
          background: "#f1f5fa",
        }}
      >
        {open && (
          <div>
            <div className="label-caps mb-2">Viewing as</div>
            <select className="field w-full" value={role} onChange={(e) => onRole(e.target.value as Role)} aria-label="Role">
              <option value="coordinator">Ops coordinator</option>
              <option value="sme">SME</option>
              <option value="student">Student</option>
            </select>
          </div>
        )}
        <div
          className={open ? "mt-3 flex items-center gap-[9px]" : "flex justify-center"}
        >
          <span
            title={persona.name}
            className="flex size-8 shrink-0 items-center justify-center rounded-full text-[11px] font-bold"
            style={{ background: "#e9eef8", color: "#2b3752" }}
          >
            {initials(persona.name)}
          </span>
          {open && (
            <span className="min-w-0">
              <span className="block whitespace-nowrap text-[12.5px] font-semibold">{persona.name}</span>
              <span className="block overflow-hidden text-ellipsis whitespace-nowrap text-[11px]" style={{ color: "var(--muted)" }}>
                {persona.sub}
              </span>
            </span>
          )}
        </div>
      </div>
    </aside>
  );
}

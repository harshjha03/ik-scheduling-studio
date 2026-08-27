# Design context — SME-to-Session Scheduling Agent (ops dashboard)

This brief describes a working prototype whose UI needs a proper design pass. Everything below is real:
the data shapes, copy strings, counts and edge cases come from the running system. Design the single
dashboard page; do not invent features beyond those listed in "Scope".

---

## 1. Product in one paragraph

A weekly scheduler for a live-classes coaching operation (JEE-style: Maths, Chemistry, Physics).
Every week ~216 sessions across 24 student batches must each get an instructor ("SME"). A pipeline
drafts the whole week automatically: hard rules first (subject, sub-specialty, training level,
availability, no double-booking), then a fairness/continuity/performance score, then an LLM breaks
genuine ties, then everything is re-validated. The dashboard exists so an **ops coordinator** can
review the draft in one sitting: see what needs attention, understand *why* in plain language,
approve or override rows, re-run, and export the approved week to Google Sheets.

**The product is attention direction.** Most rows are fine and should get out of the way. The few
that need a human (unfilled slots, conflicts, fairness problems, machine-decided ties) must be
impossible to miss and quick to act on.

## 2. Users and context of use

- **Primary: ops coordinator** (1–3 people). Not technical. Uses a laptop, Chrome, often with a
  spreadsheet open next to it. Wants to finish the weekly review in <30 minutes. Currently does this
  by hand in Sheets (~100 ops-hours/week at real scale).
- **Secondary: ops lead** glancing at the stats bar to judge "is this week healthy?".
- Timezone: everything displays in **IST** (Asia/Kolkata). Two instructors live abroad (London,
  Dubai); their availability is converted correctly but the UI never shows other timezones.
- Desktop-first (≥1280px). Should degrade gracefully to a tablet; mobile is not a target.

## 3. Core loop (the job the page supports)

1. Page loads → pipeline runs (2–10 s) → draft appears, **filtered to "Needs attention"**.
2. Coordinator works top-down through flagged rows, most severe first:
   - reads the flag reason (one plain sentence),
   - either **Approve** (accept the machine's pick) or **Override** (pick another *eligible* SME),
   - for unfilled rows: sees exactly which constraint eliminated everyone, may override anyway with
     an explicit warning.
3. **Re-run matching** → the system re-drafts, honouring overrides as score nudges (−0.2 on the
   pairing they rejected, +0.1 on the one they chose). A diff badge says "n rows changed", changed
   rows are highlighted, and rows influenced by an override are labelled "adjusted from your override".
4. Optional: **Simulate drop-out** — mark one SME unavailable for the week, auto re-run, see which
   sessions go unfilled or move.
5. **Export CSV** → downloads a Sheets-shaped file of the approved week.

Nothing is persisted server-side. All state (draft, decisions, override log) lives in the page for
the session; refreshing the page starts over. Design should make that feel intentional (a "review
session") rather than fragile — e.g. a visible count of pending decisions.

## 4. Scope (what to design) and non-goals

**Design:** one page with these regions — header, LLM/status banner, stats bar, controls row,
schedule table (with filters, expandable rows, per-row actions), override log panel, empty/loading/
error states.

**Do not design:** login/roles, multi-week navigation, calendar views, instructor-facing screens,
notifications, settings pages, mobile layouts, live Google Sheets sync (CSV export only), post-session
rating capture.

Styling constraints: Tailwind CSS utility classes, light theme only, system font stack is fine,
minimal-clean. No component library is in use.

## 5. Vocabulary (use these exact terms in the UI)

| Term | Meaning |
|---|---|
| **Session** | One 60-min class or doubt session for one batch. Id like `S037`. |
| **Batch** | A student cohort, `B01`…`B24`. |
| **SME** | Subject-matter expert = instructor. 18 of them. |
| **Subject / sub-specialty** | Maths (no split); Chemistry → Organic, Physical+Inorganic; Physics → EM+Modern, Mechanics. Doubt sessions have no sub-specialty. |
| **Training level** | 1 or 2. Some sessions require level 2. |
| **Draft** | The machine's proposed schedule for the week. |
| **Flag** | A problem or note attached to a row (see §7). |
| **Decided by** | `auto` (deterministic score), `LLM` (tie broken by the model), `override` (ops changed it). |
| **Needs attention** | Any row with ≥1 flag (default view). |
| **Exception queue** | Rows where the score margin was too small (< 0.15) and the LLM decided. |

## 6. Data shapes the UI receives

### 6.1 Draft row (one per session; 216 per run)

```json
{
  "session_id": "S073",
  "batch_id": "B09",
  "subject": "Physics",
  "sub_specialty": "em_modern",
  "type": "class",
  "start_utc": "2026-08-31T02:30:00Z",
  "duration_min": 60,
  "mode": "online",
  "required_training_level": 1,
  "sme_id": "PE2",
  "sme_name": "Rahul Desai",
  "score": 0.669,
  "components": { "fairness": 1.0, "continuity": 0.0, "performance": 0.84, "adjustment": 0.0 },
  "stage": "llm",
  "flags": [
    { "code": "TIE_ESCALATED", "priority": 5, "severity": "info", "session_id": "S073", "sme_id": "PE2",
      "reason": "Rahul Desai is the only available candidate and meets all criteria." }
  ],
  "candidates": [
    { "sme_id": "PE2", "name": "Rahul Desai", "score": 0.669, "breaches_fairness": false },
    { "sme_id": "PE3", "name": "Ishita Ghosh", "score": 0.664, "breaches_fairness": false }
  ],
  "eliminated": [
    { "sme_id": "PE1", "name": "Sneha Reddy", "rule": "overlap:S019" },
    { "sme_id": "M1",  "name": "Ananya Iyer", "rule": "subject" }
  ],
  "adjusted_from_override": false
}
```

Notes for design:
- `sme_id` is `null` and `stage` is `null` for **unfilled** rows.
- `candidates` = the only SMEs the override dropdown may offer (already rule-checked), with score;
  `breaches_fairness: true` means choosing them should show an inline warning + confirm.
- `eliminated` explains why every other SME is *not* offered: rule is one of
  `subject`, `sub_specialty`, `training_level`, `availability`, `overlap:<session_id>`.
  Hide `subject` eliminations (noise); show the rest in the expanded row.
- `components` are 0–1 values; `adjustment` is −0.2/+0.1 when an override nudged this pairing.
- Time display: IST, e.g. **Mon 08:00** (from `start_utc`). Week is Mon 31 Aug – Sat 5 Sep 2026;
  teaching hours 08:00–20:00 IST.

### 6.2 Stats (top of page)

```json
{
  "total_sessions": 216, "assigned": 214, "auto_assigned": 134, "llm_resolved": 80, "unfilled": 2,
  "flags_by_severity": { "critical": 2, "medium": 9, "info": 80 },
  "flags_by_code": { "UNFILLED": 2, "FAIRNESS_VIOLATION": 9, "TIE_ESCALATED": 80 },
  "fairness_spread_per_subject": { "Chemistry": 5, "Maths": 5, "Physics": 2 },
  "llm": {
    "queued": 80, "resolved": 80, "resolved_by_fallback_provider": 0, "fallback": 0,
    "provider": "openai", "model": "gemini-3.5-flash-lite", "fallback_provider_model": "gemini-3.5-flash",
    "error_kind": null, "error": null, "failover": null, "message": null
  }
}
```

`fairness_spread_per_subject` = max−min rolling 4-week load inside each subject pool (lower is fairer;
≤2 is the target band). Typical healthy week: ~130 auto, ~80 LLM, 0–3 unfilled.

### 6.3 LLM status (banner)

`llm.error_kind` ∈ `daily_quota_exhausted | rate_limited | provider_unavailable | timeout |
provider_error | not_configured | null`. When non-null, `llm.message` is a ready-to-show sentence, e.g.

> LLM daily request limit reached for gemini-3.5-flash-lite. 80 queued row(s) were adjudicated by the
> fallback provider (gemini-3.5-flash) instead.

> LLM daily request limit reached for gemini-3.6-flash. 80 queued row(s) were resolved by the
> deterministic score instead (LLM_FALLBACK). Switch LLM_MODEL to a model with unused quota, or wait
> for the daily reset.

> No LLM key configured. 80 queued row(s) were resolved by the deterministic score instead
> (LLM_FALLBACK). Set ANTHROPIC_API_KEY, or LLM_API_KEY + LLM_BASE_URL.

Tone rules: **red** when rows actually fell back to the deterministic score (`llm.fallback > 0`),
**amber** when the fallback provider rescued everything ("handled by fallback provider"), **blue/info**
for "no LLM configured". The raw provider response is available for a collapsible "details".

### 6.4 Override log entry (client-side)

```json
{ "session_id": "S189", "batch_id": "B21", "from_sme_id": "CP3", "to_sme_id": "CP1",
  "to_sme_name": "Aditya Verma", "at": "2026-08-27T09:12:44Z",
  "changed_rows": ["S189", "S190"] }
```
`changed_rows` is `undefined` until the next re-run ("pending re-run"), then a list (possibly empty).

### 6.5 CSV export columns (fixed order)

`week, date, time_ist, batch, subject, sub_specialty, session_type, sme_name, status, flags`
(`status` ∈ approved | overridden | pending | unfilled).

## 7. Flag taxonomy — the visual priority system

Sort rows and colour chips by this order. A row can carry several flags; its position is set by its
most severe one.

| # | Code | Severity | What it means for ops | Colour intent |
|---|---|---|---|---|
| 1 | `UNFILLED` | critical | No eligible SME; a class has no teacher. Must act. | red |
| 2 | `HARD_CONFLICT` | critical | Double-booking caught at validation (rare). | red |
| 3 | `RULE_OVERRIDE_RISK` | high | An ops override breaks a rule (subject/training/availability). Allowed, but stays visible. | orange |
| 4 | `FAIRNESS_VIOLATION` | medium | SME's 4-week load is outside pool mean ± 2. Survivable this week. | amber |
| 5 | `TIE_ESCALATED` | info | The LLM chose between near-equal candidates; reason is its one sentence. | blue |
| 6 | `LLM_FALLBACK` | info | LLM unavailable; deterministic top score used. Fixed text: "LLM unavailable — resolved by deterministic score." | blue/grey |

Real reason strings (show on hover *and* in the expanded row — hover-only is not enough):

- UNFILLED (E1): *"No eligible SME: 3 Chemistry SME(s) unavailable at Wed 14:00 IST (Priya Menon, Sameer Khan, Divya Pillai); 3 outside sub-specialty organic."*
- UNFILLED (E2): *"No eligible SME: 2 Physics SME(s) unavailable at Thu 14:00 IST (Nikhil Agarwal, Farhan Sheikh); Pooja Singh below required training level 2; 3 outside sub-specialty mechanics."*
- FAIRNESS_VIOLATION: *"Ananya Iyer at 55 sessions over 4 weeks vs. pool mean 51.0."*
- TIE_ESCALATED: *"Karan Bose is selected as he has the highest score and strong continuity with batch B21."*
- HARD_CONFLICT: *"Sneha Reddy is already assigned to S019 at this time."*
- RULE_OVERRIDE_RISK: *"Override assigns Ananya Iyer outside subject expertise."*

## 8. Page anatomy (current structure — improve it, keep the parts)

```
┌ Header: "SME scheduler — week of 31 Aug 2026 (IST)"          "3 decisions pending export" ┐
├ [LLM status banner — only when llm.error_kind != null]                                    ┤
├ Stats bar: Sessions 216 · Auto 134 · LLM 80 · Unfilled 2 · Flags 2 crit/0 high/9 med/80 info ┤
│            Fairness spread: Chemistry 5 · Maths 5 · Physics 2 · Stage C: 80/80 by LLM (model) │
├ Controls: [Re-run matching] (n rows changed since last run)  Simulate drop-out: [SME ▾]    │
│           [Unavailable this week: CO1 · restore]                          [Export CSV (Sheets)] │
├───────────────────────────────────────────────┬────────────────────────────────────────────┤
│ Schedule table                                │ Override log (n)                           │
│  ☑ Needs attention only  Batch▾ Subject▾ Flag▾ SME▾   "91 of 216 rows"                    │
│  Session | Time (IST) | Batch | Assigned SME | Score | Flags | Decided by | Review          │
│  S037 Chemistry Organic·class | Wed 14:00 | B05 | — unfilled | — | [UNFILLED] | — | Override…│
│  S172 Maths·class | Sat 20:00 | B20 | Ananya Iyer | 0.46 | [FAIRNESS_VIOLATION] | auto | Approve Override…│
│  S073 Physics EM+Modern·class | Mon 08:00 | B09 | Rahul Desai | 0.67 | [TIE_ESCALATED] | LLM | Approve Override…│
│  ▸ click row → expanded: flag reasons, score components, eligible list, eliminated list      │
└───────────────────────────────────────────────┴────────────────────────────────────────────┘
```

Per-row **Review** cell today: an `Approve` button (only when a SME is assigned), an `Override…`
select listing `candidates` as "Name (0.67)" with a ⚠ suffix when `breaches_fairness`; choosing a ⚠
candidate shows an inline amber notice *"Puts Neha Kulkarni outside the fairness band (mean ± 2).
Confirm / Cancel"*. After a decision the cell shows a green pill `approved` / `overridden`.

Row states to design: default · flagged (by severity) · unfilled (no SME, red "— unfilled") ·
changed since last run (yellow tint + "changed since last run") · adjusted from your override
(violet note) · decided (approved/overridden pill) · expanded.

## 9. Design problems worth solving (observed in the prototype)

1. **Info-flag noise.** ~80 of 216 rows are `TIE_ESCALATED` (LLM-decided). They are "needs
   attention" by definition but rarely need action. The critical 2 + medium 9 must dominate visually;
   consider grouping/collapsing info rows or a severity segmented control instead of a flat list.
2. **Reason discoverability.** Reasons are the product's main value and currently live in a hover
   title. Make the primary reason readable in the row without expanding.
3. **Override affordance.** A native `<select>` in a table cell is cramped; the fairness warning
   appears inline and pushes layout. Explore a popover/side sheet with candidates, their scores and
   components, and the warning.
4. **Diff visibility after re-run.** Yellow row tint + badge count works but is easy to lose in a long
   table; consider a "Changed (n)" filter chip and per-override links from the log to the rows.
5. **Unfilled rows** have no primary action other than override — make "who could take this if we
   relaxed X" legible (the eliminated list already says which rule blocked whom).
6. **Stats bar** is seven equal cards; unfilled and critical flags deserve emphasis, fairness spread
   deserves a mini-visual (three small bars against a ±2 target).
7. **Loading** (2–10 s on first run and on every re-run): the table should not blank out; show the
   previous draft dimmed with a progress hint ("Re-running matching… Stage C: 2 LLM calls").

## 10. Realistic content for mockups

Instructors (id → name, subject/sub-specialty, note):
`M1 Ananya Iyer` (Maths, overloaded: 18 sessions/wk history) · `M2 Rohan Mehta` · `M3 Kavya Nair` ·
`M4 Arjun Sharma` · `M5 Neha Kulkarni` · `M6 Vikram Rao` · `CO1 Priya Menon` (Organic) ·
`CO2 Sameer Khan` (Organic) · `CO3 Divya Pillai` (Organic) · `CP1 Aditya Verma` (Phys+Inorg) ·
`CP2 Meera Joshi` (Phys+Inorg, London) · `CP3 Karan Bose` (Phys+Inorg) · `PE1 Sneha Reddy`
(EM+Modern) · `PE2 Rahul Desai` (EM+Modern) · `PE3 Ishita Ghosh` (EM+Modern) · `PM1 Nikhil Agarwal`
(Mechanics) · `PM2 Pooja Singh` (Mechanics, level 1) · `PM3 Farhan Sheikh` (Mechanics, Dubai).

Seeded scenarios that a mockup should be able to show:
- **S037** B05 Chemistry Organic, Wed 14:00 — UNFILLED (all three organic SMEs unavailable).
- **S100** B12 Physics Mechanics, Thu 14:00, requires level 2 — UNFILLED (only level-1 SME available).
- **S019 / S073** B03 & B09 Physics EM+Modern, both Mon 08:00 — S019 auto → Sneha Reddy; S073 went to
  the LLM because Sneha was already booked (shown in its eliminated list as "overlap with S019").
- **S172** B20 Maths, Sat 20:00 — Ananya Iyer forced (only one available) → FAIRNESS_VIOLATION.
- ~80 LLM-decided rows with one-sentence reasons; 9 fairness flags; 134 clean auto rows.
- Drop-out of `CO1 Priya Menon` → 26 rows change, 1 new UNFILLED.

Counts for the stats bar mock: 216 sessions · 134 auto · 80 LLM · 2 unfilled · flags 2 critical /
0 high / 9 medium / 80 info · fairness spread Chemistry 5, Maths 5, Physics 2.

## 11. Copy that already exists (reuse or improve)

- Page title: *SME scheduler — week of 31 Aug 2026 (IST)*
- Buttons: *Re-run matching* · *Export CSV (Sheets)* · *Approve* · *Override…* · *Confirm* · *Cancel* · *restore*
- Badges: *n rows changed since last run* · *changed since last run* · *adjusted from your override* ·
  *approved* · *overridden* · *— unfilled* · *n decisions pending export*
- Filters: *Needs attention only* · *Batch: all* · *Subject: all* · *Flag: all* · *SME: all* · *91 of 216 rows*
- Drop-out: *Simulate drop-out: pick an SME…* · *Unavailable this week: CO1*
- Override log: *pending re-run (−0.2 on the old pairing, +0.1 on yours)* · *re-run changed: S189, S190* ·
  *re-run: no row changed because of this override* · *No overrides yet. Re-run after overriding to see which rows change.*
- Expanded row labels: *Score components — fairness 0.50 · continuity 1 · performance 0.84 · override adjustment +0.1* ·
  *Eligible (Stage A): …* · *Eliminated: Sneha Reddy (overlap with S019), …*
- Banner titles: *LLM daily limit reached* · *LLM rate limit hit* · *LLM provider temporarily unavailable* ·
  *LLM timed out* · *LLM provider error* · *No LLM configured* · suffix *— handled by fallback provider*

## 12. Accessibility & behaviour notes

- Severity must not rely on colour alone (chip text = code; keep it).
- Row expand is click-anywhere on the row; action controls stop propagation. Keyboard: rows and
  controls must be tabbable; expanded content should be reachable.
- Table scrolls horizontally inside its container on narrow widths; the page never scrolls sideways.
- Times are fixed IST; no timezone switcher.
- Destructive-ish actions: confirming a fairness-breaching override, and drop-out (which triggers a
  re-run and discards pending decisions) — both deserve a clear confirm.

## 13. Success criteria for the redesign

- A coordinator can identify the 2 critical rows and the 9 medium rows within 5 seconds of load.
- Any flag's reason is readable without a hover.
- Override takes ≤3 interactions and never offers an ineligible SME.
- After a re-run, it is obvious which rows changed and which override caused it.
- The LLM status (working / rescued by fallback / degraded to deterministic) is understood at a glance.

# IK Scheduling studio — SME-to-session scheduling agent (prototype)

Weekly scheduler for a live-classes operation (Interview Kickstart: DSA / ML / AI / PM courses).
10 batches × 3–5 sessions = 41 sessions per week over two weeks, 16 SMEs, 4 weeks of history.
A hybrid pipeline (deterministic rules + LLM tie-break) drafts the week, flags what needs a human,
and lets ops approve/override with overrides feeding back into the next run.

```
api/index.py          FastAPI app (Vercel Python function) — /api/run, /api/draft, /api/approvals
engine/stages.py      Stage A hard filter · Stage B score/auto-assign · Stage D validate · Stage E adjustments
engine/llm.py         Stage C LLM adjudication (Anthropic or any OpenAI-compatible provider) + failover
engine/run.py         run_pipeline() / apply_approvals()
engine/dotenv.py      tiny .env loader (local dev only)
data/*.json           seed dataset: sessions_current, sessions_next, smes, smes_current, history,
                      batches, courses, weeks, meta
scripts/generate_data.py  deterministic generator; asserts edge cases E1–E6 against the engine
scripts/llm_check.py  live provider check (ping + full run)
tests/test_engine.py  pytest: Stage A/B/C/D units + full seed-data runs (41 tests)
app/, lib/            Next.js (App Router) + Tailwind dashboard, implemented from the
                      Claude Design artboard "IK Scheduler v3"
```

## The dashboard

One page, three personas via the sidebar switcher (no auth — the persona is picked from the data):

- **Ops coordinator** — *Dashboard* (clickable KPI cards, week calendar, work items, class sheet,
  overrides tab), *SME management* (searchable table with capacity filters — "can fill an open
  class", "has headroom", "over preference", "on leave" — plus a per-SME availability calendar with
  free-hour blocks and "unfilled class that fits here" ghosts), *Batch management* (batch strip,
  course progress, running topics, per-batch calendar, **add a class**, create a new batch).
- **SME** — *My teaching week*: up-next card, my classes, availability blocks that really re-draft
  the week, leave request, preferred load, and change requests from ops on the live week.
- **Student** — *My schedule*: up-next card, my batch's calendar and instructors, read-only.

The week calendar uses a **compressed hour axis** — only hours that hold something get a row (a jump
is marked `⋯` with a dashed rule) — so a whole week fits one screen. The colour legend under the
tabs *is* the status filter: click a swatch to hide or show that group.

Three things the coordinator can do beyond editing rows:

- **Ops assist** (Work items → *Review suggestions*) drafts a concrete fix per item — a named
  teacher with why-chips (free at that hour, n of their preferred, rating, match score) — chosen
  only from candidates the engine already rule-checked. Nothing is applied until ops approves it,
  one at a time or all at once, and every applied fix is logged with an **Undo**. Items are split
  into *blocking publish* and *advisory*.
- **Publish** (*Approve week*) opens the channel sheet: Google Calendar, e-mail and SMS × SMEs and
  students, with tri-state ticks, a staged send you can cancel mid-flight, and a summary of exactly
  what went where. Publishing is blocked while any class has no teacher.
- **Editing a published week un-publishes it** — the calendars people already hold are now stale,
  so the badge clears and the toast says to re-publish.

Two weeks are shown: **This week** (settled and approved — drafted deterministically with Stage C
skipped, since a past week is not an LLM outage) and **Next week** (the live draft; the only week
that spends LLM quota). The next week is scored on top of the current week's realised load.

## Run locally

Prereqs: Node ≥ 20, Python 3.12+ (`.python-version` pins 3.12 for Vercel), optional Vercel CLI.

```bash
# Python
python3 -m venv .venv && source .venv/bin/activate      # or: uv venv --python 3.12 .venv
pip install -r requirements-dev.txt                    # or: uv pip install -r requirements-dev.txt
pytest -q

# Node
npm install
```

Two terminals (Next proxies `/api/*` to uvicorn in dev — see `next.config.ts`):

```bash
npm run dev:api      # uvicorn api.index:app --reload --port 8000
npm run dev          # http://localhost:3000
```

Or, with a linked Vercel project, `vercel dev` runs both the Next app and the Python function
(activate the venv first so the Python runtime finds the dependencies).

### Environment variables

| Var | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Option A. Stage C uses the Anthropic Messages API with strict JSON-schema output. |
| `LLM_API_KEY` + `LLM_BASE_URL` | Option B. Any OpenAI-compatible `/chat/completions` endpoint — Groq (free tier, the default base URL), xAI Grok, Gemini, OpenRouter, Ollama. Used when `ANTHROPIC_API_KEY` is unset (or `LLM_PROVIDER=openai`). Model names change often — check the provider's model list. **Gemini free tier is 20 requests/day/model**: keep `LLM_CHUNK=40` (2 requests per run) and switch `LLM_MODEL` for a fresh bucket when a run reports a `PerDay` quota error. Groq's free tier limits per minute instead, which suits unlimited local iteration. |
| `LLM_MODEL` | Model for Stage C. Defaults: `claude-opus-5` (Anthropic) / `openai/gpt-oss-20b` (Groq). |
| `LLM_FALLBACK_MODEL` (+ optional `LLM_FALLBACK_API_KEY`, `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_CHUNK`, `LLM_FALLBACK_EXTRA_BODY`) | Option C — failover. A chunk whose primary call fails on `daily_quota_exhausted` / `rate_limited` / `timeout` is retried on the fallback before the deterministic fallback. Set only `LLM_FALLBACK_MODEL` to use a second model on the primary key (e.g. `gemini-3.7-flash` — a separate 20/day bucket that rescues the whole queue); set `LLM_FALLBACK_API_KEY` for a different provider (default Groq `openai/gpt-oss-20b`, 12-item sub-chunks). `stats.llm.failover` reports what it rescued; the dashboard banner turns amber ("handled by fallback provider") instead of red. Best-effort: Groq's free tier caps tokens per minute (~8k), so a full 80-row queue (~22k tokens) is rescued only partially per run; the rest fall back deterministically and the message says so. |
| `LLM_EXTRA_BODY` | JSON merged into the OpenAI-compatible request body, e.g. `{"reasoning_effort":"low"}` for Gemini (measured: 12-item chunk 15s → 5.5s, same decisions). |
| `LLM_CHUNK` / `LLM_PARALLEL` / `LLM_TIMEOUT` | Tuning (defaults 20 items per call, 4 concurrent calls, 45s). Free tiers rate-limit requests per minute, so prefer fewer, larger calls (Gemini: `LLM_CHUNK=40` → 2 calls for the ~80-item queue). One 429 retry honours `Retry-After`; anything still failing falls back per chunk with the provider's message in `stats.llm.error`. |
| *(nothing)* | Stage C is skipped; every exception-queue row is resolved by the Stage B top score with an `LLM_FALLBACK` flag. The whole demo works without any key. |

Stage C failures are classified, never silent: `stats.llm.error_kind` is one of
`daily_quota_exhausted | rate_limited | provider_unavailable | timeout | provider_error | not_configured`, `stats.llm.message`
is the plain-language cause ("LLM daily request limit reached for gemini-3.5-flash-lite. 80 queued row(s)
were resolved by the deterministic score instead…"), and the dashboard shows it as a banner above the
stats. Affected rows still carry the spec's `LLM_FALLBACK` flag.

Put them in a local `.env` (git-ignored): the API (`api/index.py`) and `npm run llm:check` load it via
`engine/dotenv.py` (plain Python — JSON values keep their quotes; don't `source` the file in a shell). `GET /api/health` reports which provider/model is active. Any provider error, timeout, invalid JSON or
ineligible pick takes the same fallback path — the deterministic guardrails don't depend on the provider.

See `.env.example` for the base URLs. Export the vars in the shell that runs uvicorn locally; on
Vercel, set them in Project → Settings → Environment Variables.

## Deploy (Vercel)

```bash
vercel login
vercel           # preview
vercel --prod
```

`vercel.json` pins `framework: nextjs`, rewrites `/api/(.*)` → `/api/index` so the single FastAPI
app in `api/index.py` handles every `/api/*` route, and sets `maxDuration: 120` for the LLM call.
Python deps come from `requirements.txt`. Verify: `curl -X POST https://<app>/api/run -d @payload.json`.

**What you have to provision**, in the order it stops mattering if you skip it:

| # | Connection | Env vars | Without it |
|---|---|---|---|
| 1 | **Postgres** (Neon / Supabase / RDS) | `DATABASE_URL` | *Required on Vercel.* Saved weeks and calendar event ids vanish on every cold start; re-publishing duplicates events. |
| 2 | **Google Cloud project** with the Calendar API enabled, plus one identity to publish as — a service account (share the calendar with it, "Make changes to events") or an OAuth Desktop client consented by a person | `GOOGLE_CALENDAR_ID` + either `GOOGLE_SERVICE_ACCOUNT_JSON` or `GOOGLE_OAUTH_JSON` (raw or base64) | Calendar publish reports `simulated`; nothing is written. |
| 2b | Whichever identity you skipped — see the table under *Publishing* | `GOOGLE_OAUTH_JSON`, or `GOOGLE_IMPERSONATE` for Workspace delegation | Events are written but SMEs are not invited; they have to subscribe to the calendar. |
| 3 | **Resend** account with a verified sending domain | `RESEND_API_KEY`, `MAIL_FROM` | E-mail digests report `simulated`. |
| 4 | **Twilio** account + a number you own | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` | SMS reports `simulated`. |
| 5 | **Staging guard** while the roster is still seed data | `PUBLISH_REDIRECT_TO`, `PUBLISH_REDIRECT_SMS_TO` | Live sends go to `.example` addresses and placeholder numbers — bounces against your provider's reputation. |
| 6 | **LLM key** (Anthropic or any OpenAI-compatible) | see the table above | Stage C is skipped; queued rows fall back to the Stage-B score with an `LLM_FALLBACK` flag. |

Nothing here is a hard dependency of the app: every missing piece degrades to `simulated` and says
so in the UI. `GET /api/integrations` is the single source of truth for what is actually wired.

## State: what lives where (read this)

The **pipeline** is stateless — the frontend loads the seed JSON, holds the working state (draft,
decisions, override log, simulated drop-outs) and passes what the API needs on every call.
`GET /api/draft` only returns the last run from a *warm* instance's memory and otherwise 404s;
it exists to satisfy the trigger/fetch/approve API shape, not as storage.

Three things outlive a request and go to a database (`engine/store.py`): the **saved week**
(so a refresh doesn't lose the coordinator's work), the **publish log**, and the **calendar event
ids we own** (so re-publishing updates events instead of duplicating them).

| `DATABASE_URL` | Driver | Location | Durable |
|---|---|---|---|
| unset | SQLite | `IK_DB_PATH`, default `.data/ik.db` | no — local dev only |
| set | Postgres (Neon/Supabase/RDS) | that server | yes |

**On Vercel you must set `DATABASE_URL`.** Function filesystems are ephemeral: with SQLite, every
cold start silently loses the saved week and the event ids, and the next publish duplicates every
calendar event. `GET /api/integrations` reports `storage.durable` so the running app tells you which
one it is. Plain SQL, two placeholder styles, no ORM and no migration step — the schema is three
`CREATE TABLE IF NOT EXISTS` statements.

## API

- `POST /api/run` — `{sessions, smes, history, overrides[]}` → `{draft, flags, stats}`.
  `draft` rows carry `candidates` (Stage-A-eligible SMEs with scores, `breaches_fairness`),
  `eliminated` (per-SME rule), `stage` (`auto|llm|null`), sorted `flags`, `adjusted_from_override`.
  `overrides[]` items: `{session_id, batch_id, from_sme_id, to_sme_id}`.
- `GET /api/draft` — cached last run or 404 (see above).
- `POST /api/approvals` — `{draft, decisions:[{session_id, action: approve|override, override_sme_id?}]}`
  → `{final_schedule, override_log, export_rows}`. An override outside the row's Stage-A
  candidates is accepted **with** a `RULE_OVERRIDE_RISK` flag naming the rule it breaks — never silently.
- `GET /api/health` — LLM provider/model actually in use.
- `GET /api/integrations` — `{channels, storage, llm}`: what is wired up right now. The publish
  sheet labels each channel **live** or **simulated** from this, so a mock never looks like a send.
- `POST /api/publish` — `{week, week_label, channel: cal|email|sms, audience: sme|stu, rows, smes, batches}`
  → `{status: sent|simulated|skipped|error, detail, count, live}`. Sends where that channel has
  credentials, reports `simulated` where it doesn't, records the outcome either way.
- `GET /api/publish/log?week=` — what was sent, when, and whether it was live.
- `GET|POST /api/schedule` — save/load the week (`{week, draft, ...}`); `null` when nothing is saved.

## Publishing: how a week reaches people

`engine/channels.py`, one function per channel, each live only when its credentials are present.

- **Google Calendar** — Calendar v3, one event per class. The event id is stored per
  `(session_id, calendar_id)`, so a re-publish **PUT**s the event it already owns rather than
  creating a duplicate; if the event was deleted in Google (404/410) it is re-created. Students
  publish to their batch's `calendar_id` when the batch names one, otherwise to the shared
  `GOOGLE_CALENDAR_ID`.

  **Whether teachers get invited depends on which identity publishes**, and that is a Google policy
  boundary, not a setting in this code:

  | Identity | Env | Attendees |
  |---|---|---|
  | A person | `GOOGLE_OAUTH_JSON` | **Yes** — the class lands on the teacher's own calendar with nothing to click. Works on a plain gmail.com account. Events show that human as organiser. |
  | A service account | `GOOGLE_SERVICE_ACCOUNT_JSON` | **No** — `403 forbiddenForServiceAccounts`. The teacher is named in the description; people subscribe to the calendar. |
  | A delegated service account | `+ GOOGLE_IMPERSONATE` | Yes, but needs Google Workspace domain-wide delegation, and only covers addresses in that domain. |

  `GOOGLE_OAUTH_JSON` wins when both are set, and `/api/integrations` reports which one is live
  ("user account, invites teachers" vs "service account, writes events only"). Addresses on reserved
  domains (`.example`, `.invalid`, `.test`) are never invited — the seed roster is placeholders, and
  inviting it would fire off a bounce per class.
- **E-mail** — Resend, one HTML digest per recipient (SMEs individually, batches via
  `contact_email`).
- **SMS** — Twilio, one short message per number. Batches have no group number, so student SMS
  honestly reports "no recipients".

One bad row never sinks the batch: failures are counted per row and reported in `detail`.

> **Before the first live send**, set `PUBLISH_REDIRECT_TO` / `PUBLISH_REDIRECT_SMS_TO`. The seed
> roster is `.example` addresses and placeholder numbers; the redirect sends one message to an inbox
> you own instead of bouncing dozens off a real provider.

> **Running the flow suite once anything is live.** It runs the whole product: it publishes every
> channel/audience leaf for a week *and* saves the result. Against a configured API that means real
> calendar events; against the production database it overwrites the saved week and then reads it
> back on the next run, so the assertions start depending on what the last run left behind. Start the
> API isolated from both:
>
> ```bash
> DATABASE_URL= IK_DB_PATH=/tmp/ik-flow-test.db PUBLISH_DISABLED=1 npm run dev:api
> ```
>
> `PUBLISH_DISABLED` forces every channel to simulate whatever the credentials say. The suite checks
> `/api/integrations` before launching Chrome and exits 2 if any channel is live or storage is
> durable, so this isn't something you can forget. `pytest` is unaffected — it never touches the
> network or a real database.

## Pipeline

| Stage | Where | What |
|---|---|---|
| A Hard filter | `stage_a_hard_filter` | course → topic → level → availability (UTC window) → overlap with the draft. Zero survivors ⇒ `UNFILLED` naming the constraints that eliminated the same-course SMEs. An SME may carry several courses (`subjects`) and topics (`topics`); a doubt/mock session accepts any SME of the course. |
| B Score | `stage_b_score`, `is_clear_winner` | `0.5·fairness + 0.3·continuity + 0.2·performance`; fairness normalised over the subject pool's projected 4-week load (past 3 weeks + draft). Chronological; margin ≥ 0.15 ⇒ auto, else exception queue (Stage-B top tentatively reserved so overlap accounting stays correct). |
| C LLM | `stage_c_llm_adjudicate` | One batched call (chunks of `LLM_CHUNK`) with strict JSON output; candidates = Stage-A survivors still overlap-free. Invalid JSON / ineligible id ⇒ one retry ⇒ failover provider ⇒ `LLM_FALLBACK`. Also rewrites flag reasons known at that point (UNFILLED). `run_pipeline(..., llm_enabled=False)` (API: `{"llm": false}`) skips the stage entirely and takes the Stage-B top score with **no** fallback flag — used for the settled week. |
| D Validate | `stage_d_validate` | Re-checks every assignment (auto + LLM) against all hard rules ⇒ reject to `UNFILLED` (`HARD_CONFLICT` for overlaps). Rolling load outside pool mean ± 2 ⇒ keep + `FAIRNESS_VIOLATION`. |
| E Human | UI + `apply_approvals`, `stage_e_adjustments` | Approve/override per row; on re-run −0.2 on the overridden (SME, batch) pairing, +0.1 on the chosen one; affected rows labelled "adjusted from your override". A pick that breaches a rule or the fairness band is allowed, but only after an explicit **confirm** step that spells out the consequence, and it keeps its flag. |

Guardrail: the LLM only picks among Stage-A survivors, its pick is overlap-checked when applied,
and Stage D re-validates everything — it can never create or keep a hard-rule violation. The same
guardrail applies to people: an ops override that breaks a hard rule cannot survive the next re-run
(Stage A eliminates it), so the override log says so explicitly — *"re-run could not keep {SME} — the
pick breaks a hard rule, so the class went back to the pipeline"* — rather than going quiet.

Flags sort/colour by priority: UNFILLED (critical) › HARD_CONFLICT (critical) › RULE_OVERRIDE_RISK
(high) › FAIRNESS_VIOLATION (medium) › TIE_ESCALATED (info) › LLM_FALLBACK (info).

## Seeded edge cases (all in **next week**; session ids are stable)

| | Where to look in the UI |
|---|---|
| E1 unfilled (concurrent ML System Design classes — Priya Menon takes one, Divya Pillai is outside her San Francisco window) | Dashboard → status filter "Unfilled / conflict", or Work items → `ML-02-0` |
| E2 level gap (advanced Dynamic Programming on Saturday afternoon; the only free carrier is a beginner) | Work items → `DSA-01-1` — the reason names the training-level rule and Vikram Rao |
| E3 conflict pressure (two intermediate Arrays & Strings classes at Tue 11:00) | Open `DSA-04-0` → "Choose a different teacher" shows the other SME as **busy with DSA-02-0** |
| E4 ties | Cards badged `TIE` (key set) / `FALLBACK` (no key) — 10–14 rows |
| E5 fairness skew (Ananya Iyer at 8 classes/week in history vs a pool base of 4) | `DSA-01-0` Mon 19:00 — she is the only qualified SME, so it is assigned **and** flagged FAIRNESS; elsewhere the draft routes around her |
| E6 timezones | Divya Pillai (America/Los_Angeles 18:00–23:00 → IST 06:30–11:30), Meera Joshi (Europe/London), Farhan Sheikh (Asia/Dubai) — authored local, stored UTC, shown IST |

Regenerate with `python scripts/generate_data.py` (seeded; it runs the engine and asserts all of the above,
plus that the current week is fully staffed).

## Testing

Three layers, all runnable locally:

```bash
pytest -q                # 43 engine tests: stages A–E, LLM failure paths, seed-data invariants
npm run test:flows       # 95 browser checks: the three personas + the v3 features
npm run test:flows -- sme
npm run test:flows -- features    # ops assist, publish, un-publish on edit, add a class
```

`scripts/flow_test.js` drives Chrome over the DevTools Protocol with no dependencies (node's global
`WebSocket` + an installed Chrome; set `CHROME=` for a non-default path). It walks every persona:
the coordinator's draft → filter → class sheet → confirm-and-assign → override log → re-run → work
items → approve → export (the CSV's bytes are checked, not just the toast) → SME management →
drop-out → batch creation; the SME's availability toggle, leave request and change-request
approval; the student's read-only schedule; and the v3 features end to end — assist proposes a fix,
ops approves it, undo reverts it; publishing is refused while a class is unfilled, then sends on
every channel; editing after that un-publishes the week. The engine tests guard the rules, these
guard what each persona can actually see and do.

## Notes and deliberate simplifications

- **Dataset size deviates from the original brief** (24 batches / 216 sessions / 18 SMEs) because the UI was
  re-themed to the Interview Kickstart world of the design: 10 batches / 41 sessions per week / 16 SMEs, over
  two weeks. The engine is unchanged and linear in sessions — scale is a data change, not a design change.
- Batch level (beginner/intermediate/advanced) maps to the engine's `required_training_level` 1/2/3, and an
  SME's level to `training_level`; a batch only runs topics somebody at its level actually carries.
- Override pairing key is `(SME, batch_id)` — every session has a batch, so the "or-topic" variant is not needed.
- Stage C rewrites reason strings for the flags that exist before Stage D (UNFILLED). FAIRNESS_VIOLATION /
  HARD_CONFLICT reasons use the templates from the flag table (already plain language) to keep one LLM call per run.
- Approvals, overrides, leave, drop-outs and created batches live in the page for the session — there is no
  database (see the statelessness note above). Refreshing starts a fresh review.
- The live ("This week") teacher change is a request: it appears in the SME persona as a change request and is
  applied only when accepted — the design's live-week rule.
- Google Sheets: `lib/export.ts` declares `ScheduleExporter`; only the CSV implementation exists.
  `post_session_rating` is present in the schema and stays `null`.
- Out of scope per spec: live Calendar/Sheets sync, rating capture UI, webhook drop-outs, auth, persistence.

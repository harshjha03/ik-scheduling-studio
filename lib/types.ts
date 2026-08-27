export type WeekKey = "current" | "next";
export type Role = "coordinator" | "sme" | "student";
export type ModuleKey = "dashboard" | "smes" | "batches" | "myweek" | "mysched";
export type SessionType = "class" | "doubt" | "mock";
export type LevelName = "beginner" | "intermediate" | "advanced";

export interface Course {
  id: string;
  name: string;
  accent: string;
  tint: string;
  deep: string;
  topics: string[];
}

export interface Batch {
  id: string;
  course: string;
  level: LevelName;
  learners: number;
  per_week: number;
  weeks_done: number;
  weeks_total: number;
  started: string;
  /** where the cohort is reachable when the week is published */
  contact_email?: string | null;
  contact_phone?: string | null;
  /** cohort calendar to publish into; null falls back to the shared GOOGLE_CALENDAR_ID */
  calendar_id?: string | null;
}

export interface WeekMeta {
  key: WeekKey;
  label: string;
  range: string;
  locked: boolean;
  iso: string;
}

export interface Meta {
  days: string[];
  hours: [number, number];
  levels: LevelName[];
  type_label: Record<SessionType, string>;
  me: string;
  my_batch: string;
}

export interface Session {
  id: string;
  batch_id: string;
  subject: string;
  sub_specialty: string | null;
  type: SessionType;
  start_utc: string;
  duration_min: number;
  mode: string;
  required_training_level: number;
}

export interface AvailabilityWindow {
  weekday: string;
  start_utc: string;
  end_utc: string;
  local?: string;
}

export interface HistoryWeek {
  week: string;
  sessions_taught: number;
  batches: string[];
  per_topic_rating: Record<string, number>;
  post_session_rating: number | null;
}

export interface SME {
  id: string;
  name: string;
  /** publish targets — absent means that channel reports no recipients for this teacher */
  email?: string | null;
  phone?: string | null;
  subject: string;
  subjects: string[];
  sub_specialty: string | null;
  topics: string[];
  training_level: number;
  level: LevelName;
  to_upgrade: number;
  timezone: string;
  city: string;
  rating: number;
  preferred: number;
  leave: string | null;
  weekly_availability: AvailabilityWindow[];
  preference_notes: string;
  history: HistoryWeek[];
}

export type HistoryRecord = HistoryWeek & { sme_id: string };

export type FlagCode =
  | "UNFILLED"
  | "HARD_CONFLICT"
  | "RULE_OVERRIDE_RISK"
  | "FAIRNESS_VIOLATION"
  | "TIE_ESCALATED"
  | "LLM_FALLBACK";

export type Severity = "critical" | "high" | "medium" | "info";

export interface Flag {
  code: FlagCode;
  priority: number;
  severity: Severity;
  session_id: string;
  sme_id: string | null;
  reason: string;
}

export interface Components {
  fairness: number;
  continuity: number;
  performance: number;
  adjustment: number;
}

export interface Candidate {
  sme_id: string;
  name: string;
  score: number;
  components: Components;
  breaches_fairness?: boolean;
}

export interface Eliminated {
  sme_id: string;
  name: string;
  rule: string;
}

export interface DraftRow {
  session_id: string;
  batch_id: string;
  subject: string;
  sub_specialty: string | null;
  type: SessionType;
  start_utc: string;
  duration_min: number;
  mode: string;
  required_training_level: number;
  sme_id: string | null;
  sme_name: string | null;
  score: number | null;
  components: Components | null;
  stage: "auto" | "llm" | "override" | null;
  flags: Flag[];
  candidates: Candidate[];
  eliminated: Eliminated[];
  adjusted_from_override: boolean;
  status?: string;
}

export interface LlmStats {
  queued: number;
  resolved: number;
  resolved_by_fallback_provider: number;
  fallback: number;
  provider: string | null;
  model: string | null;
  fallback_provider_model: string | null;
  error_kind:
    | "daily_quota_exhausted"
    | "rate_limited"
    | "provider_unavailable"
    | "timeout"
    | "provider_error"
    | "not_configured"
    | null;
  error: string | null;
  failover: { kind: string; resolved: number; error_kind: string | null; error: string | null; model: string | null } | null;
  message: string | null;
  skipped?: boolean;
}

export interface RunResult {
  draft: DraftRow[];
  flags: Flag[];
  stats: {
    total_sessions: number;
    assigned: number;
    auto_assigned: number;
    llm_resolved: number;
    unfilled: number;
    flags_by_severity: Partial<Record<Severity, number>>;
    flags_by_code: Partial<Record<FlagCode, number>>;
    fairness_spread_per_subject: Record<string, number>;
    llm: LlmStats;
  };
}

export interface Decision {
  session_id: string;
  action: "approve" | "override";
  override_sme_id?: string;
}

export type OverrideKind = "teacher change" | "assigned" | "change requested";

export interface OverrideEvent {
  kind: OverrideKind;
  session_id: string;
  batch_id: string;
  week: WeekKey;
  from_sme_id: string | null;
  to_sme_id: string;
  to_sme_name: string;
  at: string;
  note: string;
  changed_rows?: string[];
  /** set after a re-run: the pipeline could not keep this pick (it breaks a hard rule) */
  reverted?: boolean;
}

export interface ExportRow {
  week: string;
  date: string;
  time_ist: string;
  batch: string;
  subject: string;
  sub_specialty: string;
  session_type: string;
  sme_name: string;
  status: string;
  flags: string;
}

export interface ApprovalsResult {
  final_schedule: DraftRow[];
  override_log: unknown[];
  export_rows: ExportRow[];
}

// ---- UI ----

export type Category = "red" | "amber" | "approved" | "staffed";

export interface WorkItem {
  key: string;
  code: FlagCode | "LEAVE";
  severity: Severity;
  title: string;
  detail: string;
  session_id: string | null;
  sme_id?: string;
  /** critical items block publishing; the rest are advisory */
  blocking: boolean;
  when: string;
}

// ---- publish ----

export type Audience = "sme" | "stu";
export type ChannelKey = "cal" | "email" | "sms";
export type LeafId = `${ChannelKey}:${Audience}`;
export type SendState = "idle" | "ready" | "sending" | "sent" | "simulated" | "skipped" | "error";

export interface PublishLeaf {
  id: LeafId;
  channel: { key: ChannelKey; short: string; title: string; sub: string };
  audience: { key: Audience; label: string; count: string };
}

// ---- ops assist ----

/** A concrete, reviewable proposal for one work item. Nothing is applied until ops approves it. */
export interface Fix {
  label: string;
  why: string;
  chips: [string, "good" | "warn" | "neutral"][];
  who?: { id: string; name: string };
  /** what the fix does — a decision the page applies, never a silent rule break */
  action:
    | { kind: "assign"; sessionId: string; smeId: string; smeName: string }
    | { kind: "accept"; sessionId: string; code: FlagCode }
    | { kind: "dismiss"; key: string };
}

export interface ResolvedEntry {
  key: string;
  text: string;
  /** enough to put the row back exactly as it was */
  undo: { kind: "row"; week: WeekKey; row: DraftRow } | { kind: "dismiss"; key: string };
}

export interface NewClass {
  topic: string;
  type: SessionType;
  day: number;
  hour: number;
  smeId: string;
}

export type SheetState =
  | { kind: "class"; sessionId: string; week: WeekKey; stage: "info" | "pick" }
  | { kind: "ghost"; sessionId: string; week: WeekKey; smeId: string }
  | { kind: "work"; auto?: boolean }
  | { kind: "publish" }
  | { kind: "newClass" }
  | { kind: "newBatch" }
  | { kind: "import" }
  | { kind: "smeImport" }
  | { kind: "profile" }
  | null;

/** The editable half of an SME record — contact details and the weekly preference. Skills, level
 *  and ratings stay with ops, so an SME editing their own profile sees those as read-only. */
export interface Profile {
  id: string;
  name: string;
  email: string;
  phone: string;
  city: string;
  level: LevelName;
  preferred: string;
}

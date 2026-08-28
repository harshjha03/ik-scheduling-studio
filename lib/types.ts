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
  /** blocks already on their calendar this week, from an availability sync — a Stage A hard rule */
  external_busy?: { start_utc: string; end_utc: string }[];
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
  /** why this row's scores moved: its own pairing was overridden (direct), or an override elsewhere
   *  re-normalised its subject pool's load (ripple). `smes` names who moved. */
  override_effect: { kind: "direct" | "ripple"; smes: string[] } | null;
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
  /** who made the change — ops (default) or the Copilot agent */
  actor?: "ops" | "Copilot";
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
  | { kind: "studentEmail" }
  | { kind: "agent" }
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

/** Where each dataset in front of the coordinator actually came from, and when. */
export interface DatasetOrigin { source: string; at: string; rows?: number }
export type DataProvenance = Record<string, DatasetOrigin>;

// ---- Recovery & Review Copilot ----

export type AgentMode = "recovery" | "review" | "chat";

export interface AgentRequest {
  mode: AgentMode;
  /** recovery: the teacher reported unavailable, optionally only on these days (Mon..Sat) */
  smeId?: string;
  days?: string[];
  /** review: free-text question about the draft */
  question?: string;
}

export type AgentActionKind = "move" | "reschedule" | "upgrade";

interface AgentActionBase {
  kind?: AgentActionKind;
  reason: string;
  verdict?: "ok" | "fairness_warning" | string;
  detail?: string | null;
  score?: number | null;
  /** AGENT_FALLBACK when the deterministic floor produced this entry */
  flag?: string;
}

/** Reassign one class. */
export interface AgentMoveAction extends AgentActionBase {
  kind?: "move";
  session_id: string;
  from_sme: string | null;
  to_sme: string;
  to_sme_name?: string;
}

/** Same class, another hour this week — applied by editing the session, then re-running the pipeline. */
export interface AgentRescheduleAction extends AgentActionBase {
  kind: "reschedule";
  session_id: string;
  from_day: string;
  from_hour_ist: string;
  to_day: string;
  to_hour_ist: string;
  /** the new start the engine computed, so the client never re-derives it */
  start_utc: string;
  eligible_after?: { sme_id: string; name: string }[];
}

/** Raise a teacher's training level, bounded by what a class actually requires. */
export interface AgentUpgradeAction extends AgentActionBase {
  kind: "upgrade";
  sme_id: string;
  sme_name: string;
  from_level: number;
  to_level: number;
  unblocks?: string[];
}

export type AgentMove = AgentMoveAction | AgentRescheduleAction | AgentUpgradeAction;

export const isReschedule = (a: AgentMove): a is AgentRescheduleAction => a.kind === "reschedule";
export const isUpgrade = (a: AgentMove): a is AgentUpgradeAction => a.kind === "upgrade";
export const isMove = (a: AgentMove): a is AgentMoveAction => !a.kind || a.kind === "move";

export interface AgentStep {
  thought: string;
  tool: string | null;
  args: Record<string, unknown> | null;
  result_digest: string;
  error?: boolean;
}

export type AgentPlan = AgentMove[] | null;

export interface AgentResult {
  status: "ok" | "budget_exhausted" | "fallback";
  answer: string;
  plan: AgentPlan;
  transcript: AgentStep[];
  simulation: { verdicts: AgentMove[]; all_ok: boolean; flag_diff: { before: Record<string, number>; after: Record<string, number> } } | null;
  meta: { tool_calls: number; llm_turns: number; elapsed_s: number; model: string | null; error: string | null; affected: string[] };
}

/** One line of the floating copilot conversation. Assistant turns keep the whole result so their own
 *  evidence and plan render with them — a later answer can never apply an earlier plan. */
export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  res?: AgentResult;
  applied?: boolean;
}

export interface AgentApplyResult {
  draft: DraftRow[];
  flags: Flag[];
  stats: Partial<RunResult["stats"]>;
  override_log: { session_id: string; batch_id: string; from_sme_id: string | null; to_sme_id: string; to_sme_name: string; rule_risk: string | null; actor: string; reason: string | null }[];
  applied: string[];
  diff: number;
}

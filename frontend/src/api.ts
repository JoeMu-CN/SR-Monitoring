import type {DataSource, MonitoringDimension, RiskItem, RiskLevel, Supplier} from './types';

/* ── 有效期策略类型（任务 8-10 后端已实现，任务 11 前端对齐） ───────── */

export type ValidityMode = 'fixed_days' | 'until_superseded' | 'until_revoked' | 'event_end_plus_grace' | 'indefinite';

export type ValidityState = 'pending_classification' | 'active' | 'expired' | 'superseded' | 'revoked' | 'conflicted' | 'legacy';

export type LifecycleAction = 'assert' | 'confirm' | 'revoke' | 'supersede';

export type ValidityProfile = string;

export interface SourceValidityPolicy {
  readonly profile?: ValidityProfile | null;
  readonly mode: ValidityMode;
  readonly fixed_days?: number | null;
  readonly grace_days?: number | null;
  readonly critical_grace_days?: number | null;
  readonly review_days?: number | null;
  readonly review_required?: boolean;
}

export type ValidityAnchorSource = 'published_at' | 'collected_at' | 'official_valid_until' | 'event_end' | 'legacy';

export interface ValidityReasonRead {
  readonly code: string;
  readonly anchor_source: ValidityAnchorSource;
  readonly details: Record<string, unknown>;
}

export const VALIDITY_MODE_LABELS: Record<ValidityMode, string> = {
  fixed_days: '固定天数',
  until_superseded: '替代时失效',
  until_revoked: '撤销时失效',
  event_end_plus_grace: '事件结束+宽限',
  indefinite: '长期有效',
};

export const VALIDITY_STATE_LABELS: Record<ValidityState, string> = {
  pending_classification: '待分类',
  active: '有效',
  expired: '已过期',
  superseded: '已替代',
  revoked: '已撤销',
  conflicted: '冲突',
  legacy: '旧版兼容',
};

export type ResearchTaskType = 'manual' | 'daily' | 'weekly';
export type ResearchTaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export type ResearchTaskEventStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped' | 'info';
export type ResearchReportStatus = 'draft' | 'submitted' | 'rejected';
export type ResearchReviewStatus = 'pending' | 'approved' | 'rejected';
export type ResearchWorkerRuntimeStatus = 'online' | 'stale' | 'stopped';
export type ResearchWorkerOverallStatus = 'online' | 'stale' | 'offline';

export interface ResearchTaskRead {
  id: number;
  owner_user_id: number;
  task_type: ResearchTaskType;
  topic: string;
  supplier_scope: number[];
  source_urls: string[];
  budget_snapshot: Record<string, unknown>;
  search_queries_used: number;
  search_results_used: number;
  input_tokens_used: number;
  output_tokens_used: number;
  cost_amount: string;
  current_step: string | null;
  status: ResearchTaskStatus;
  execution_requested_at: string | null;
  cancel_requested_at: string | null;
  worker_id: string | null;
  lease_until: string | null;
  attempts: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface ResearchTaskEventRead {
  id: number;
  task_id: number;
  event_type: string;
  node_key: string;
  parent_node_key: string | null;
  status: ResearchTaskEventStatus;
  label: string;
  detail: Record<string, unknown>;
  occurred_at: string;
}

export interface ResearchWorkerRead {
  worker_id: string;
  mode: string;
  orchestrator: 'legacy' | 'langgraph';
  status: ResearchWorkerRuntimeStatus;
  started_at: string;
  last_seen_at: string;
  stopped_at: string | null;
}

export interface ResearchWorkerStatusRead {
  checked_at: string;
  stale_after_seconds: number;
  status: ResearchWorkerOverallStatus;
  workers: ResearchWorkerRead[];
}

export interface ResearchSourceRead {
  id: number;
  task_id: number;
  url: string;
  title: string | null;
  source_type: string;
  credibility_tier: string;
  http_status: number | null;
  content_excerpt: string | null;
  retrieved_at: string;
}

export interface ResearchClaimDraft {
  claim_id: string;
  claim_type: 'fact' | 'inference' | 'forecast';
  text: string;
  citation_ids: string[];
  confidence: number | null;
}

export interface ResearchCitationDraft {
  citation_id: string;
  url: string;
  quote: string;
  verified: boolean;
}

export interface ResearchReportDraft {
  title: string;
  disclaimer: string;
  facts: ResearchClaimDraft[];
  inferences: ResearchClaimDraft[];
  forecasts: ResearchClaimDraft[];
  citations: ResearchCitationDraft[];
}

export interface ResearchReportRead {
  id: number;
  task_id: number;
  title: string;
  draft: ResearchReportDraft;
  status: ResearchReportStatus;
  review_status: ResearchReviewStatus;
  model_version: string | null;
  created_at: string;
  updated_at: string;
}

export interface RiskAlertRead {
  id: number;
  level: RiskLevel;
  score: number;
  score_detail: Record<string, unknown>;
  status: string;
  supplier_id: number;
  supplier_name: string;
  event_id: number;
  event_type: string;
  event_subtype: string | null;
  event_summary: string;
  event_start_at: string | null;
  event_end_at: string | null;
  confidence: number;
  match_type: string;
  match_reasons: string[];
  match_evidence: Array<Record<string, unknown>>;
  source_title: string;
  source_url: string | null;
  published_at: string | null;
  updated_at: string;
  expires_at: string | null;
  expiry_kind: string;
  validity_state: ValidityState;
  valid_until: string | null;
  review_due_at: string | null;
  validity_policy_version: string | null;
  validity_reason: ValidityReasonRead;
}

export interface EventSignalEvidence {
  readonly signal_id: number;
  readonly title: string;
  readonly content: string;
  readonly url: string | null;
  readonly published_at: string | null;
}

export interface EventEntityEvidence {
  readonly name: string;
  readonly normalized_name: string | null;
  readonly registry_no: string | null;
}

export interface EventLocationEvidence {
  readonly name: string;
  readonly country_code: string | null;
  readonly region: string | null;
  readonly city: string | null;
  readonly district: string | null;
  readonly latitude: number | null;
  readonly longitude: number | null;
  readonly radius_km: number | null;
}

export interface EventDetailRead {
  readonly id: number;
  readonly dedup_key: string;
  readonly event_type: string;
  readonly event_subtype: string | null;
  readonly severity: string;
  readonly summary: string;
  readonly start_at: string | null;
  readonly end_at: string | null;
  readonly confidence: number;
  readonly created_at: string;
  readonly signals: readonly EventSignalEvidence[];
  readonly entities: readonly EventEntityEvidence[];
  readonly locations: readonly EventLocationEvidence[];
  readonly validity_state: ValidityState;
  readonly valid_until: string | null;
  readonly review_due_at: string | null;
  readonly validity_policy_version: string | null;
  readonly validity_reason: ValidityReasonRead;
}

interface RiskAlertListResponse { items: RiskAlertRead[]; total: number }

/* ── 总览汇总契约（任务 4 后端 GET /dashboard/summary?days=7|30|90） ───────── */

export type DashboardWindowDays = 7 | 30 | 90;

export interface DashboardLevelCount {
  readonly level: RiskLevel;
  readonly count: number;
}

export interface DashboardEventTypeCount {
  readonly event_type: string;
  readonly count: number;
}

/** 来源采集新鲜度：每来源最近一次采集运行的完成时间与状态。 */
export interface DashboardSourceHealth {
  readonly id: number;
  readonly code: string;
  readonly name: string;
  readonly enabled: boolean;
  readonly last_run_at: string | null;
  readonly last_run_status: string | null;
}

/** 来源分布：同一提醒关联到某来源去重计数，多来源可重复归属；各来源之和可大于当前提醒总数，非互斥占比。 */
export interface DashboardSourceDistributionItem {
  readonly source_id: number;
  readonly code: string;
  readonly name: string;
  readonly count: number;
}

export interface DashboardSummary {
  /** 当前有效快照下的 P1–P4 计数，恒含四档、顺序固定，不随 days 变化。 */
  readonly level_counts: readonly DashboardLevelCount[];
  /** 当前有效提醒总数（同一只读快照），不随 days 变化。 */
  readonly total_current: number;
  /** 过去 24 小时内创建且当前仍有效的提醒数（兼容字段，非自然日）。 */
  readonly today_new: number;
  readonly type_distribution: readonly DashboardEventTypeCount[];
  /** 最近提醒：updated_at DESC, id DESC，最多 10 条。 */
  readonly recent_alerts: readonly RiskAlertRead[];
  readonly sources: readonly DashboardSourceHealth[];
  /** 数据截至时间（聚合快照点，UTC ISO）。 */
  readonly as_of: string;
  readonly window_start: string;
  /** 服务端回显的窗口天数（7|30|90）。 */
  readonly window_days: number;
  /** 窗口 [window_start, as_of) 内创建的全部提醒行，含已失效。 */
  readonly period_new_count: number;
  readonly supplier_total: number;
  readonly active_supplier_total: number;
  readonly source_distribution: readonly DashboardSourceDistributionItem[];
  readonly retention_window_days: number;
  /** days 超出已配置提醒保留期时为 true：更早的期间新增可能已被清理，历史不完整。 */
  readonly history_may_be_partial: boolean;
}

export interface SupplierRead {
  id: number;
  supplier_code: string;
  legal_name: string;
  country_code: string;
  registry_no: string | null;
  registration_address: string | null;
  industry: string | null;
  raw_materials: string[];
  enabled: boolean;
  updated_at: string;
  aliases: Array<{id: number; alias: string; language: string | null}>;
  sites: Array<{
    id: number;
    site_name: string;
    country_code: string;
    region: string | null;
    city: string | null;
    district: string | null;
    address: string;
    latitude: number | null;
    longitude: number | null;
  }>;
  products: Array<{id: number; name: string; keywords: string[]}>;
}

export interface SupplierListItem extends SupplierRead {
  current_risk_level: RiskLevel | null;
  current_risk_score: number | null;
}

export interface SupplierListResponse {
  items: SupplierListItem[];
  total: number;
  limit: number;
  offset: number;
}

export type SupplierStatusFilter = 'all' | 'normal' | 'high_risk' | 'paused';

export const SUPPLIER_PAGE_SIZE = 20;

// 监控状态在后端只有 enabled 与 has_current_alert 两个维度，这里固定四种前端语义的映射。
const supplierStatusFilterParams: Record<SupplierStatusFilter, ReadonlyArray<readonly [string, string]>> = {
  all: [],
  normal: [['enabled', 'true'], ['has_current_alert', 'false']],
  high_risk: [['enabled', 'true'], ['has_current_alert', 'true']],
  paused: [['enabled', 'false']],
};

export interface DataSourceRead {
  id: number;
  code: string;
  name: string;
  source_type: string;
  credibility: number;
  schedule: string | null;
  endpoint_url?: string | null;
  auth_type?: 'none' | 'api_key' | 'bearer' | 'basic' | 'oauth2' | 'custom';
  login_config?: Record<string, unknown>;
  credential_ref?: string | null;
  api_key_configured?: boolean;
  api_key_hint?: string | null;
  description?: string | null;
  adapter_config?: Record<string, unknown>;
  adapter_status?: 'builtin' | 'unconfigured' | 'draft' | 'published' | 'invalid';
  adapter_version?: number;
  adapter_published_at?: string | null;
  access_status?: 'ready' | 'throttled' | 'busy' | 'cooldown';
  access_cooldown_until?: string | null;
  access_last_http_status?: number | null;
  access_last_error_kind?: string | null;
  enabled: boolean;
  created_at?: string;
  updated_at?: string;
  total_signal_count?: number;
  valid_signal_count?: number;
  signal_validity_days?: number | null;
  validity_policy?: SourceValidityPolicy | null;
  validity_policy_version?: string | null;
  applies_to?: 'new_signals_only';
}

export interface SourceSignalRead {
  readonly id: number;
  readonly external_id: string | null;
  readonly title: string;
  readonly content: string;
  readonly url: string | null;
  readonly published_at: string | null;
  readonly collected_at: string;
  readonly validity_profile: ValidityProfile | null;
  readonly validity_state: ValidityState;
  readonly valid_from: string | null;
  readonly valid_until: string | null;
  readonly review_due_at: string | null;
  readonly validity_mode: ValidityMode | null;
  readonly validity_key: string | null;
  readonly lifecycle_action: LifecycleAction;
  readonly validity_policy_version: string | null;
  readonly validity_reason: ValidityReasonRead;
}

export interface SourceSignalListResponse {
  readonly source: {
    readonly id: number;
    readonly code: string;
    readonly name: string;
    readonly signal_validity_days: number | null;
    readonly validity_policy: SourceValidityPolicy | null;
  };
  readonly items: readonly SourceSignalRead[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
}

export interface DataSourceAuditLogRead {
  id: number;
  source_id: number | null;
  action: string;
  actor_role: string;
  actor_id: string | null;
  changes: Record<string, unknown>;
  created_at: string;
}

export interface DataSourceWritePayload {
  code: string;
  name: string;
  source_type: string;
  credibility: number;
  schedule: string | null;
  endpoint_url: string | null;
  auth_type: DataSourceRead['auth_type'];
  login_config: Record<string, unknown>;
  credential_ref: string | null;
  api_key?: string | null;
  description: string | null;
  adapter_config?: Record<string, unknown> | null;
  enabled: boolean;
  signal_validity_days?: number | null;
  validity_policy?: SourceValidityPolicy | null;
}

export interface AdapterPreviewResponse {
  fetched_count: number;
  items: Array<{
    external_id: string | null;
    title: string;
    content: string;
    url: string | null;
    published_at: string | null;
  }>;
}

export interface CollectionRunRead {
  id: number;
  source_id: number;
  started_at: string;
  finished_at: string | null;
  status: string;
  fetched_count: number;
  created_count: number;
  duplicate_count: number;
  error: string | null;
}

export interface RunAllSourcesItem {
  source_id: number;
  code: string;
  status: 'succeeded' | 'failed' | 'skipped' | 'error';
  created_count: number;
  reason?: string | null;
}

export interface RunAllSourcesResult {
  total: number;
  succeeded: number;
  failed: number;
  skipped: number;
  items: RunAllSourcesItem[];
}

/** 天眼查专用同步批量核查（POST /api/v1/sources/{id}/run-tyc-batch）结果契约。 */
export interface TycBatchRunResult {
  readonly source_id: number;
  /** 本轮纳入核查的启用供应商总数。 */
  readonly targeted_count: number;
  /** 实际发起过天眼查调用的供应商数；额度耗尽时小于 targeted_count。 */
  readonly attempted_count: number;
  readonly created_count: number;
  readonly duplicate_count: number;
  /** 天眼查返回无命中（empty）的次数。 */
  readonly empty_count: number;
  readonly failed_count: number;
  /** 调用额度耗尽导致本轮提前停止。 */
  readonly quota_exhausted: boolean;
}

export interface DimensionSourceRead {
  code: string;
  name: string;
  declared_status: 'connected' | 'planned' | 'external_tool';
  linked: boolean;
  enabled: boolean | null;
  adapter_status: string | null;
  last_collected_at: string | null;
  valid_signal_count: number | null;
}

export interface DimensionInputSourceRead {
  code: string;
  name: string;
  signal_count: number;
  latest_at: string | null;
}

export interface DimensionInputsRead {
  declared_total: number;
  declared_linked: number;
  declared_enabled: number;
  observed: DimensionInputSourceRead[];
  has_input: boolean;
}

export interface ForcedRuleRead {
  name: string;
  description: string;
  event_types: string[];
  event_subtypes: string[];
  match_types: string[];
  forced_level: string;
  reason: string;
}

/**
 * 全局评分与强制规则草稿（对应后端 GlobalScoringPatch；PUT 为合并语义）。
 * 只包含全局层可写字段：不含 match_columns/event_types（那是维度层）。
 */
export interface GlobalScoringPatchPayload {
  severity_scores?: Record<string, number>;
  association_scores?: Record<string, number>;
  credibility_weight?: number;
  timeliness_with_date?: number;
  timeliness_without_date?: number;
  product_relevance_score?: number;
  p1_min?: number;
  p2_min?: number;
  p3_min?: number;
  strong_match_types?: string[];
  alert_expiry_days?: number;
  forced_rules?: ForcedRuleRead[];
}

/**
 * 维度配置草稿（对应后端 DimensionConfigPatch；维度 PUT 与 /test 草稿预览共用）。
 */
export interface DimensionConfigPatchPayload {
  match_columns?: string[];
  event_types?: string[];
  severity_scores?: Record<string, number>;
  association_scores?: Record<string, number>;
  credibility_weight?: number;
  timeliness_with_date?: number;
  timeliness_without_date?: number;
  product_relevance_score?: number;
  p1_min?: number;
  p2_min?: number;
  p3_min?: number;
  strong_match_types?: string[];
  alert_expiry_days?: number;
  forced_rules?: ForcedRuleRead[];
}

/**
 * 全局评分与强制规则配置读模型（GET/PUT/DELETE /rule-engine/global-config）。
 * 只描述全局层：effective = 代码默认 + 全局行；defaults = 代码默认（forced_rules
 * 含可全局化的维度追加）。维度增量造成的遮蔽通过 shadowed_by 披露。
 */
export interface GlobalScoringConfigRead {
  source: 'configured' | 'default';
  enabled: boolean;
  effective: Record<string, unknown>;
  defaults: Record<string, unknown>;
  shadowed_by: Record<string, string[]>;
  forced_rules_shadowed_by: string[];
  dropped_dimension_rules: Array<Record<string, unknown>>;
}

export interface DimensionTraceEventRead {
  event_type: string;
  event_subtype: string | null;
  severity: string;
  summary: string;
  confidence: number;
  published_at: string | null;
  source_name: string | null;
}

/** 轨迹路由：该提醒评分时的历史归属维度；match_columns 取该维度当前合并配置。 */
export interface DimensionTraceRoutingRead {
  key: string;
  label: string;
  match_columns: string[];
}

export interface DimensionTraceMatchRead {
  match_type: string;
  match_reasons: string[];
  match_evidence: Array<Record<string, unknown>>;
}

export interface DimensionTraceScoreRead {
  total: number;
  level: string;
  detail: Record<string, unknown>;
  level_cap: string | null;
  forced_rule: Record<string, unknown> | null;
}

/** 样例选择器条目：该维度最近一条 current 提醒的摘要。 */
export interface DimensionTraceSampleRead {
  id: number;
  supplier_id: number;
  supplier_name: string;
  level: string;
  event_summary: string;
  updated_at: string;
}

/**
 * 维度运行轨迹（GET /rule-engine/dimensions/{key}/trace）。
 * available=false 表示该维度当前没有 current 提醒（HTTP 仍为 200）。
 */
export interface DimensionTraceRead {
  available: boolean;
  event: DimensionTraceEventRead | null;
  routing: DimensionTraceRoutingRead | null;
  match: DimensionTraceMatchRead | null;
  score: DimensionTraceScoreRead | null;
  samples: DimensionTraceSampleRead[];
}

export interface DimensionRead {
  key: string;
  label: string;
  description: string;
  content_items: string[];
  data_sources: DimensionSourceRead[];
  event_types: string[];
  match_columns: string[];
  enabled: boolean;
  has_override: boolean;
  active_alerts: number;
  scoring: {
    rule_version?: string;
    severity_scores?: Record<string, number>;
    association_scores?: Record<string, number>;
    credibility_weight?: number;
    p1_min?: number;
    p2_min?: number;
    p3_min?: number;
    alert_expiry_days?: number;
    forced_rules?: ForcedRuleRead[];
    [key: string]: unknown;
  };
}

export interface RuleEngineOptions {
  match_columns: string[];
  event_types: Array<{value: string; label: string}>;
  event_subtypes: Array<{value: string; label: string}>;
}

export interface SandboxRequest {
  event_type: string;
  event_subtype?: string | null;
  severity: 'critical' | 'high' | 'medium' | 'low';
  organizations: Array<{name: string; aliases: string[]; registry_no: string | null}>;
  locations: Array<{
    name: string;
    country_code?: string | null;
    region?: string | null;
    city?: string | null;
    district?: string | null;
  }>;
  affected_products: string[];
  affected_industries: string[];
  summary: string;
  credibility: number;
  has_published_at: boolean;
  // —— 未保存草稿预览（与后端 SandboxRequest 对齐；不传时行为与旧请求一致） ——
  /** 草稿作用的维度 key；提供 draft_config 时必填 */
  dimension_key?: string | null;
  /** 该维度的草稿覆盖（与保存路径同构的合并与边界校验） */
  draft_config?: DimensionConfigPatchPayload | null;
  /** 全局评分/强制规则的草稿覆盖（与全局 PUT 同构的合并语义） */
  global_config?: GlobalScoringPatchPayload | null;
}

export interface SandboxCandidate {
  supplier_id: number;
  supplier_name: string;
  match_type: string;
  association_score: number;
  reasons: string[];
  score: number;
  level: 'P1' | 'P2' | 'P3' | 'P4';
  score_detail: Record<string, unknown>;
}

export interface SandboxResult {
  dimension: {key: string; label: string; match_columns: string[]} | null;
  message?: string;
  candidates: SandboxCandidate[];
}

export interface SignalFilterConfig {
  high_impact: string[];
  priority_countries: string[];
  list_sources: string[];
  source: 'default' | 'configured';
  updated_at?: string | null;
}

export interface SignalFilterConfigUpdate {
  high_impact?: string[];
  priority_countries?: string[];
  list_sources?: string[];
}

export interface ToolCallRead {
  name: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
}

export interface SourceOnboardingDraftRead {
  id: number;
  agent_session_id: number | null;
  source_id: number | null;
  actor_id: string | null;
  current_step: string;
  answers: Record<string, string>;
  created_at: string;
  updated_at: string;
}

export interface SourceOnboardingDraftBoxItem {
  kind: 'in_progress' | 'adapter_draft' | 'pending_enable';
  title: string;
  detail: string;
  draft_id: number | null;
  session_id: number | null;
  source_id: number | null;
  source_code: string | null;
  current_step: string | null;
  updated_at: string;
}

export interface ChatResponse {
  session_id: number;
  answer: string;
  tool_calls: ToolCallRead[];
  onboarding_draft?: SourceOnboardingDraftRead | null;
}

export interface AgentStatusRead {
  llm_configured: boolean;
  model: string;
  tyc_enabled: boolean;
  max_steps: number;
}

export interface SystemHealth { status: string; database: string }

// —— 只读监控健康聚合（GET /api/v1/system/monitoring-health，沿用 source_status_view 权限）。
// 契约与 backend/app/scheduler/health_schemas.py 一一对应：只含稳定枚举、计数与时间戳。
export type MonitoringOverallStatus = 'ok' | 'degraded' | 'unknown' | 'inactive';
export type MonitoringSourceState = 'ok' | 'failed' | 'overdue' | 'never_run' | 'disabled' | 'on_demand' | 'invalid_schedule';

export interface MonitoringSchedulerHealth {
  status: 'unknown' | 'ok' | 'stale';
  last_heartbeat_at: string | null;
  age_seconds: number | null;
  interval_seconds: number;
  stale_after_seconds: number;
}

export interface MonitoringProcessingRun {
  status: 'idle' | 'running' | 'succeeded' | 'failed';
  started_at: string | null;
  finished_at: string | null;
  processed: number;
  filtered: number;
  failed: number;
}

export interface MonitoringProcessingHealth {
  total: number;
  classification_failed: number;
  backlog_over_1h: number;
  oldest_pending_age_seconds: number | null;
  last_run: MonitoringProcessingRun;
}

export interface MonitoringSourceHealth {
  source_id: number;
  code: string;
  name: string;
  state: MonitoringSourceState;
  reason_code: string;
  last_success_at: string | null;
  last_attempt_at: string | null;
  next_expected_at: string | null;
}

export interface MonitoringHealthRead {
  as_of: string;
  overall: MonitoringOverallStatus;
  scheduler: MonitoringSchedulerHealth;
  processing: MonitoringProcessingHealth;
  sources: MonitoringSourceHealth[];
}

export interface AIReviewSummary {
  needs_review: number;
  filtered: number;
  analyzed_without_alert: number;
}

export interface AIReviewItem {
  id: number;
  signal_id: number;
  title: string;
  content: string;
  url: string | null;
  provider: string;
  model: string;
  status: string;
  started_at: string;
  review_reason: string | null;
}

export type UserRole = 'viewer' | 'risk_analyst' | 'risk_admin' | 'platform_admin';

export type UserStatus = 'pending' | 'active' | 'disabled';

export interface AuthUser {
  id: number;
  username: string;
  email: string | null;
  display_name: string | null;
  role: UserRole;
  status: UserStatus;
  last_login_at: string | null;
  created_at: string;
}

export interface AuthMeResponse {
  user: AuthUser;
  permissions: string[];
}

export interface UserCreatePayload {
  readonly username: string;
  readonly password: string;
  readonly display_name?: string;
  readonly email?: string;
  readonly role?: UserRole;
}

export interface UserUpdatePayload {
  readonly role?: UserRole;
  readonly status?: UserStatus;
  readonly display_name?: string;
  readonly email?: string;
}

export interface PasswordResetPayload {
  readonly new_password: string;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly detail?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

function formatDetail(raw: unknown, status: number): string {
  if (typeof raw === 'string') return raw;
  if (
    typeof raw === 'object' && raw !== null && !Array.isArray(raw)
    && 'message' in raw && typeof raw.message === 'string'
  ) {
    return raw.message;
  }
  if (Array.isArray(raw)) {
    const msgs = raw
      .map((item) => {
        if (typeof item === 'object' && item !== null && 'msg' in item) {
          return typeof item.msg === 'string' ? item.msg : null;
        }
        return null;
      })
      .filter((m): m is string => m !== null);
    if (msgs.length > 0) return msgs.join('；');
  }
  return `HTTP ${status}`;
}

export interface SupplierCreatePayload {
  supplier_code: string;
  legal_name: string;
  country_code: string;
  registry_no: string | null;
  registration_address: string | null;
  industry: string | null;
  raw_materials: string[];
  enabled: boolean;
  aliases: Array<{alias: string; language: string | null}>;
  sites: Array<{
    site_name: string;
    country_code: string;
    region: string | null;
    city: string | null;
    district: string | null;
    address: string;
    latitude: number | null;
    longitude: number | null;
  }>;
  products: Array<{name: string; keywords: string[]}>;
}

export type SupplierUpdatePayload = Omit<SupplierCreatePayload, 'supplier_code' | 'aliases' | 'sites' | 'products'> & {
  expected_updated_at?: string;
  aliases: Array<{id?: number; alias: string; language: string | null}>;
  sites: Array<{id?: number; site_name: string; country_code: string; region: string | null; city: string | null; district: string | null; address: string; latitude: number | null; longitude: number | null}>;
  products: Array<{id?: number; name: string; keywords: string[]}>;
};

export interface SupplierDeletionImpactRead {
  can_delete: boolean;
  match_count: number;
  alert_count: number;
  sites_count: number;
  products_count: number;
  aliases_count: number;
  blocked_reason: string | null;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const method = (options.method ?? 'GET').toUpperCase();
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && typeof document !== 'undefined') {
    const csrfCookie = document.cookie.split('; ').find((item) => item.split('=', 1)[0].endsWith('_csrf'));
    if (csrfCookie) headers.set('X-CSRF-Token', csrfCookie.split('=').slice(1).join('='));
  }
  const response = await fetch(path, {...options, headers, credentials: 'include'});
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as {detail?: unknown} | null;
    throw new ApiError(response.status, formatDetail(payload?.detail, response.status), payload?.detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  auth: {
    me: () => request<AuthMeResponse>('/api/v1/auth/me'),
    login: (username: string, password: string) => request<AuthUser>('/api/v1/auth/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username, password}),
    }),
    logout: () => request<{detail: string}>('/api/v1/auth/logout', {method: 'POST'}),
    listUsers: () => request<readonly AuthUser[]>('/api/v1/auth/users'),
    createUser: (payload: UserCreatePayload) => request<AuthUser>('/api/v1/auth/users', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    }),
    updateUser: (id: number, payload: UserUpdatePayload) => request<AuthUser>(`/api/v1/auth/users/${id}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    }),
    resetPassword: (id: number, payload: PasswordResetPayload) => request<{detail: string}>(`/api/v1/auth/users/${id}/password-reset`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    }),
  },
  alerts: () => request<RiskAlertListResponse>('/api/v1/risk-alerts?status=current&limit=100'),
  alert: (id: number) => request<RiskAlertRead>(`/api/v1/risk-alerts/${id}`),
  dashboardSummary: (days: DashboardWindowDays) => request<DashboardSummary>(`/api/v1/dashboard/summary?days=${days}`),
  event: (id: number) => request<EventDetailRead>(`/api/v1/events/${id}`),
  suppliers: () => request<SupplierListResponse>('/api/v1/suppliers?limit=100'),
  supplierPage: (query: string, status: SupplierStatusFilter, offset: number) => {
    const params = new URLSearchParams({limit: String(SUPPLIER_PAGE_SIZE), offset: String(offset)});
    if (query) params.set('q', query);
    for (const [key, value] of supplierStatusFilterParams[status]) params.set(key, value);
    return request<SupplierListResponse>(`/api/v1/suppliers?${params.toString()}`);
  },
  sources: () => request<DataSourceRead[]>('/api/v1/sources'),
  sourcesAdmin: () => request<DataSourceRead[]>('/api/v1/sources/admin'),
  sourceSignals: (sourceId: number, scope: 'valid' | 'all', offset: number) => request<SourceSignalListResponse>(
    `/api/v1/sources/${sourceId}/signals?scope=${scope}&offset=${offset}`,
  ),
  createSource: (payload: DataSourceWritePayload) => request<DataSourceRead>('/api/v1/sources', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  }),
  updateSource: (id: number, payload: Partial<DataSourceWritePayload>) => request<DataSourceRead>(`/api/v1/sources/${id}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  }),
  deleteSource: (id: number) => request<{deleted: boolean; id: number; code: string}>(`/api/v1/sources/${id}`, {method: 'DELETE'}),
  sourceAuditLogs: (sourceId?: number) => request<{items: DataSourceAuditLogRead[]; total: number}>(
    `/api/v1/sources/audit-logs?limit=100${sourceId ? `&source_id=${sourceId}` : ''}`,
  ),
  previewSource: (payload: {
    source_code: string;
    adapter_config: Record<string, unknown>;
    auth_type: 'none' | 'api_key' | 'bearer';
    credential_ref: string | null;
    login_config: Record<string, unknown>;
  }) => request<AdapterPreviewResponse>('/api/v1/sources/preview', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  }),
  publishSource: (id: number) => request<DataSourceRead>(`/api/v1/sources/${id}/publish`, {method: 'POST'}),
  collectionRuns: () => request<{items: CollectionRunRead[]; total: number}>('/api/v1/collection-runs?limit=100'),
  dimensions: () => request<DimensionRead[]>('/api/v1/rule-engine/dimensions'),
  dimensionInputs: (key: string, days = 30) => request<DimensionInputsRead>(`/api/v1/rule-engine/dimensions/${key}/inputs?days=${days}`),
  ruleEngineOptions: () => request<RuleEngineOptions>('/api/v1/rule-engine/match-columns'),
  testRuleEngine: (payload: SandboxRequest) => request<SandboxResult>('/api/v1/rule-engine/test', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  }),
  // 维度运行轨迹（观察态真实数据）；alert_id 为空时取该维度最近一条 current 提醒
  dimensionTrace: (key: string, alertId?: number) => request<DimensionTraceRead>(
    `/api/v1/rule-engine/dimensions/${key}/trace${alertId ? `?alert_id=${alertId}` : ''}`,
  ),
  globalConfig: {
    get: () => request<GlobalScoringConfigRead>('/api/v1/rule-engine/global-config'),
    // PUT 为合并语义：只更新传入字段；移除当前生效强制规则时须显式确认
    update: (payload: GlobalScoringPatchPayload, confirmDisableForcedRules = false) => request<GlobalScoringConfigRead>(
      `/api/v1/rule-engine/global-config${confirmDisableForcedRules ? '?confirm_disable_forced_rules=true' : ''}`,
      {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)},
    ),
    // DELETE 删除全局行、回退代码默认；会移除当前生效强制规则时须显式确认
    reset: (confirmDisableForcedRules = false) => request<GlobalScoringConfigRead>(
      `/api/v1/rule-engine/global-config${confirmDisableForcedRules ? '?confirm_disable_forced_rules=true' : ''}`,
      {method: 'DELETE'},
    ),
  },
  filterConfig: {
    get: () => request<SignalFilterConfig>('/api/v1/signals/filter-config'),
    update: (payload: SignalFilterConfigUpdate) => request<SignalFilterConfig>('/api/v1/signals/filter-config', {
      method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    }),
    reset: () => request<void>('/api/v1/signals/filter-config', {method: 'DELETE'}),
  },
  health: () => request<SystemHealth>('/api/v1/system/health'),
  monitoringHealth: (signal?: AbortSignal) => request<MonitoringHealthRead>(
    '/api/v1/system/monitoring-health', signal ? {signal} : {},
  ),
  agentStatus: () => request<AgentStatusRead>('/api/v1/agent/status'),
  aiReviewSummary: () => request<AIReviewSummary>('/api/v1/ai-review-summary'),
  aiReviewItems: () => request<AIReviewItem[]>('/api/v1/ai-review-items?limit=5'),
  research: {
    tasks: () => request<{items: ResearchTaskRead[]}>('/api/v1/research/tasks'),
    workerStatus: () => request<ResearchWorkerStatusRead>('/api/v1/research/worker/status'),
    task: (taskId: number) => request<ResearchTaskRead>(`/api/v1/research/tasks/${taskId}`),
    events: (taskId: number, afterId = 0, limit = 200) => request<{items: ResearchTaskEventRead[]; next_after_id: number}>(`/api/v1/research/tasks/${taskId}/events?after_id=${afterId}&limit=${limit}`),
    sources: (taskId: number) => request<{items: ResearchSourceRead[]}>(`/api/v1/research/tasks/${taskId}/sources`),
    startTask: (taskId: number) => request<ResearchTaskRead>(`/api/v1/research/tasks/${taskId}/start`, {method: 'POST'}),
    cancelTask: (taskId: number) => request<ResearchTaskRead>(`/api/v1/research/tasks/${taskId}/cancel`, {method: 'POST'}),
    deleteTask: (taskId: number) => request<void>(`/api/v1/research/tasks/${taskId}`, {method: 'DELETE'}),
    createTask: (topic: string, supplierScope: number[] = []) => request<ResearchTaskRead>('/api/v1/research/tasks', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({task_type: 'manual', topic, supplier_scope: supplierScope}),
    }),
    reports: (taskId: number) => request<{items: ResearchReportRead[]}>(`/api/v1/research/tasks/${taskId}/reports`),
  },
  chat: (question: string, sessionId: number | null) => request<ChatResponse>('/api/v1/chat', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({question, session_id: sessionId}),
  }),
  sourceAgentChat: (question: string, sessionId: number | null, draftId: number | null) => request<ChatResponse>('/api/v1/source-agent/chat', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({question, session_id: sessionId, draft_id: draftId}),
  }),
  sourceOnboardingDrafts: () => request<{items: SourceOnboardingDraftBoxItem[]}>('/api/v1/source-agent/drafts'),
  deleteSourceOnboardingDraft: (draftId: number) => request<void>(`/api/v1/source-agent/drafts/${draftId}`, {method: 'DELETE'}),
  createSupplier: (payload: SupplierCreatePayload) => request<SupplierRead>('/api/v1/suppliers', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  }),
  toggleSupplier: (id: number, enabled: boolean) => request<SupplierRead>(`/api/v1/suppliers/${id}/enabled`, {
    method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled}),
  }),
  getSupplier: (id: number, signal?: AbortSignal) => request<SupplierRead>(`/api/v1/suppliers/${id}`, signal ? {signal} : {}),
  supplierDeletionImpact: (id: number) => request<SupplierDeletionImpactRead>(`/api/v1/suppliers/${id}/deletion-impact`),
  updateSupplier: (id: number, payload: SupplierUpdatePayload) => request<SupplierRead>(`/api/v1/suppliers/${id}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  }),
  deleteSupplier: (id: number) => request<void>(`/api/v1/suppliers/${id}`, {method: 'DELETE'}),
  runSource: (id: number) => request<CollectionRunRead>(`/api/v1/sources/${id}/run`, {method: 'POST'}),
  // 天眼查专用：同步执行一次批量主体核查并返回本轮汇总（非通用拉取，不走 /run）。
  runTycBatch: (id: number) => request<TycBatchRunResult>(`/api/v1/sources/${id}/run-tyc-batch`, {method: 'POST'}),
  runAllSources: () => request<RunAllSourcesResult>('/api/v1/sources/run-all', {method: 'POST'}),
  toggleDimension: (key: string, enabled: boolean) => request<DimensionRead>(`/api/v1/rule-engine/dimensions/${key}/toggle`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled}),
  }),
  updateDimension: (key: string, config: Record<string, unknown>) => request<DimensionRead>(`/api/v1/rule-engine/dimensions/${key}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({config}),
  }),
};

const levelNames: Record<RiskLevel, string> = {P1: '重大风险', P2: '高风险', P3: '中风险', P4: '低风险'};
const eventLabels: Record<string, string> = {
  weather: '天气', geological: '地质灾害', logistics: '物流', trade_policy: '贸易政策',
  geopolitical: '地缘政治', corporate: '企业经营', judicial: '司法', compliance: '合规', other: '其他',
};
const scoreLabels: Record<string, [string, number]> = {
  severity: ['事件严重程度', 35], association: ['关联强度', 30],
  source_credibility: ['来源可信度', 20], timeliness: ['时效性', 10], product_relevance: ['产品相关性', 5],
};
const dimensionIcons: Record<string, string> = {
  natural: 'flood', geopolitical: 'public', economic: 'monitoring', policy: 'policy', industry: 'factory', corporate: 'domain',
};

function formatDateTime(value: string | null): string {
  if (!value) return '时间未披露';
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value));
}

function evidenceText(evidence: Array<Record<string, unknown>>): string {
  const first = evidence[0];
  if (!first) return '结构化匹配证据已留存';
  return Object.entries(first).slice(0, 3).map(([key, value]) => `${key}: ${String(value)}`).join('；');
}

export function mapRiskAlert(alert: RiskAlertRead): RiskItem {
  const scoreBreakdown = Object.entries(scoreLabels).map(([key, [category, maxScore]]) => {
    const score = Number(alert.score_detail[key] ?? 0);
    return {category, score, maxScore, weightPercent: maxScore, contribution: score};
  });
  return {
    id: String(alert.id), companyName: alert.supplier_name, vendorId: String(alert.supplier_id),
    level: alert.level, levelName: levelNames[alert.level], riskType: eventLabels[alert.event_type] ?? alert.event_type,
    summary: alert.event_summary, aiConfidence: Math.round(alert.confidence * 1000) / 10,
    updatedTime: formatDateTime(alert.updated_at), matchType: alert.match_type,
    source: alert.source_title || '来源未披露',
    tags: [eventLabels[alert.event_type] ?? alert.event_type, ...alert.match_reasons.slice(0, 2)],
    status: alert.status === 'current' ? 'valid' : 'invalid', overallScore: alert.score,
    validityState: alert.validity_state,
    validUntil: alert.valid_until,
    reviewDueAt: alert.review_due_at,
    validityReason: alert.validity_reason,
    validityPolicyVersion: alert.validity_policy_version,
    eventCategory: alert.event_subtype ?? alert.event_type, impactScope: evidenceText(alert.match_evidence),
    evidenceChain: {
      sourceName: alert.source_title || '来源未披露', sourceType: '风险信号', eventSummary: alert.event_summary,
      matchStatus: alert.match_reasons.join('；') || alert.match_type,
      ruleTriggered: String(alert.score_detail.rule_version ?? '当前评分规则'), calculatedScore: alert.score,
    },
    timeline: [
      {time: formatDateTime(alert.published_at), stage: 'source', title: '取得风险信号', description: alert.source_title || '来源未披露'},
      {time: formatDateTime(alert.event_start_at), stage: 'event', title: '形成风险事件', description: alert.event_summary},
      {time: formatDateTime(alert.updated_at), stage: 'match', title: '完成供应商关联与评分', description: alert.match_reasons.join('；') || alert.match_type},
    ],
    scoreBreakdown,
    originalSignals: [{title: alert.source_title || alert.event_summary, source: alert.source_title || '来源未披露', time: formatDateTime(alert.published_at)}],
    matchReasons: {entityMatch: alert.match_reasons[0] ?? alert.match_type, locationMatch: evidenceText(alert.match_evidence), keywords: alert.match_reasons.slice(1)},
  };
}

export function mapSupplier(supplier: SupplierRead, riskLevel?: RiskLevel, riskScore?: number): Supplier {
  const site = supplier.sites[0];
  return {
    id: String(supplier.id), code: supplier.supplier_code, legalName: supplier.legal_name,
    registrationNo: supplier.registry_no ?? '未登记',
    registrationAddress: supplier.registration_address ?? '未登记',
    productionLocation: supplier.sites.map((item) => [item.city, item.district, item.site_name].filter(Boolean).join(' ')).join('、') || '未登记',
    // 编辑表单需要首地点的真实字段值才能无损往返（保存时按表单值回传，未编辑即原值）
    productionRegion: site?.region ?? undefined,
    productionCity: site?.city ?? undefined,
    productionDistrict: site?.district ?? undefined,
    productionAddress: site?.address ?? undefined,
    countryRegion: supplier.country_code, tier: '重点供应商',
    category: supplier.industry ?? supplier.products[0]?.name ?? '未分类',
    suppliedProduct: supplier.products.map((item) => item.name).join('、') || '未登记',
    monitoringStatus: supplier.enabled ? (riskLevel === 'P1' || riskLevel === 'P2' ? 'high_risk' : 'normal') : 'paused',
    riskLevel, riskScore, lastUpdated: site ? `${site.country_code} · ${site.city ?? site.site_name}` : '当前数据',
  };
}

/** 列表项已带服务端判定的当前有效风险，监控状态直接采用该口径，不再由前端按 P1/P2 推断。 */
export function mapSupplierListItem(item: SupplierListItem): Supplier {
  return {
    ...mapSupplier(item, item.current_risk_level ?? undefined, item.current_risk_score ?? undefined),
    monitoringStatus: !item.enabled ? 'paused' : item.current_risk_level !== null ? 'high_risk' : 'normal',
  };
}

export function mapDataSource(source: DataSourceRead, runs: CollectionRunRead[]): DataSource {
  const lastRun = runs.filter((run) => run.source_id === source.id).sort((a, b) => b.id - a.id)[0];
  const isExternalTool = source.source_type === 'external_tool';
  const accessStatus = source.access_status ?? 'ready';
  // 状态判定：disabled (停用) > running (正在跑) > error (失败) >
  //           warning (冷却/限流/尚未运行) > normal (正常)；外部工具/冷却单独处理
  const status: DataSource['status'] = !source.enabled
    ? 'disabled'
    : accessStatus !== 'ready'
    ? 'warning'
    : isExternalTool
    ? source.api_key_configured ? 'normal' : 'warning'
    : !lastRun
    ? 'warning'
    : lastRun.status === 'running'
    ? 'running'
    : lastRun.status === 'failed'
    ? 'error'
    : lastRun.status === 'succeeded'
    ? 'normal'
    : 'warning';
  return {
    id: String(source.id), name: source.name, type: source.source_type, status,
    latency: accessStatus === 'cooldown'
      ? `访问冷却至 ${source.access_cooldown_until ? formatDateTime(source.access_cooldown_until) : '稍后'}`
      : accessStatus === 'busy' ? '同域名请求执行中'
      : accessStatus === 'throttled' ? '域名请求间隔保护中'
      : !source.enabled ? '已停用' : isExternalTool
      ? source.api_key_configured ? '核查可用' : '运行密钥未配置'
      : !lastRun ? '尚未运行' : lastRun.status === 'succeeded' ? '运行正常' : lastRun.status === 'failed' ? '运行失败' : lastRun.status === 'running' ? '运行中' : '运行中',
    lastSyncTime: isExternalTool ? '调用' : lastRun ? formatDateTime(lastRun.finished_at ?? lastRun.started_at) : '尚未运行',
    itemCount: lastRun?.created_count ?? 0,
    totalSignalCount: source.total_signal_count ?? 0,
    validSignalCount: source.valid_signal_count ?? source.total_signal_count ?? 0,
    signalValidityDays: source.signal_validity_days ?? null,
    validityMode: source.validity_policy?.mode ?? null,
    validityPolicy: source.validity_policy ?? null,
    validityPolicyVersion: source.validity_policy_version ?? null,
    code: source.code, credibility: source.credibility, schedule: source.schedule,
    endpointUrl: source.endpoint_url ?? null, authType: source.auth_type ?? 'none',
    loginConfig: source.login_config ?? {}, credentialRef: source.credential_ref ?? null,
    apiKeyConfigured: source.api_key_configured ?? false, apiKeyHint: source.api_key_hint ?? null,
    description: source.description ?? null,
    adapterConfig: source.adapter_config ?? {}, adapterStatus: source.adapter_status ?? 'unconfigured',
    adapterVersion: source.adapter_version ?? 0, adapterPublishedAt: source.adapter_published_at ?? null,
    accessStatus, accessCooldownUntil: source.access_cooldown_until ?? null,
    accessLastHttpStatus: source.access_last_http_status ?? null,
    accessLastErrorKind: source.access_last_error_kind ?? null,
    enabled: source.enabled,
  };
}

export function mapDimension(dimension: DimensionRead): MonitoringDimension {
  return {
    id: dimension.key, name: dimension.label, icon: dimensionIcons[dimension.key] ?? 'shield', enabled: dimension.enabled,
    ruleId: String(dimension.scoring.rule_version ?? dimension.key),
    severityScores: {...(dimension.scoring.severity_scores ?? {})},
    associationScores: {...(dimension.scoring.association_scores ?? {})},
    thresholds: {p1: Number(dimension.scoring.p1_min ?? 85), p2: Number(dimension.scoring.p2_min ?? 65), p3: Number(dimension.scoring.p3_min ?? 40)},
    matchColumns: [...(dimension.match_columns ?? [])],
    eventTypes: [...(dimension.event_types ?? [])],
    forcedRules: [...(dimension.scoring.forced_rules ?? [])],
    contentItems: dimension.content_items,
    dataSources: dimension.data_sources.map((source) => ({
      code: source.code,
      name: source.name,
      status: source.declared_status,
      linked: source.linked,
      enabled: source.enabled,
      adapterStatus: source.adapter_status,
      lastCollectedAt: source.last_collected_at,
      validSignalCount: source.valid_signal_count,
    })),
    source: dimension,
  };
}

function diffScores(original: Record<string, number>, updated: Record<string, number>): Record<string, number> {
  const diff: Record<string, number> = {};
  for (const key of Object.keys(updated)) {
    if (updated[key] !== original[key]) diff[key] = updated[key];
  }
  return diff;
}

function arraysEqual(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

export function updateDimensionConfig(original: MonitoringDimension, updated: MonitoringDimension): Record<string, unknown> {
  const patch: Record<string, unknown> = {};

  const severityDiff = diffScores(original.severityScores, updated.severityScores);
  if (Object.keys(severityDiff).length > 0) patch.severity_scores = severityDiff;

  const associationDiff = diffScores(original.associationScores, updated.associationScores);
  if (Object.keys(associationDiff).length > 0) patch.association_scores = associationDiff;

  if (updated.thresholds.p1 !== original.thresholds.p1) patch.p1_min = updated.thresholds.p1;
  if (updated.thresholds.p2 !== original.thresholds.p2) patch.p2_min = updated.thresholds.p2;
  if (updated.thresholds.p3 !== original.thresholds.p3) patch.p3_min = updated.thresholds.p3;

  if (!arraysEqual(updated.matchColumns, original.matchColumns)) patch.match_columns = [...updated.matchColumns];
  if (!arraysEqual(updated.eventTypes, original.eventTypes)) patch.event_types = [...updated.eventTypes];

  return patch;
}

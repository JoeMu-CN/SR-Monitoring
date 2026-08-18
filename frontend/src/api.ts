import type {DataSource, MonitoringDimension, RiskItem, RiskLevel, Supplier} from './types';

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
}

interface RiskAlertListResponse { items: RiskAlertRead[]; total: number }

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

interface SupplierListResponse { items: SupplierRead[]; total: number }

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

export interface DimensionRead {
  key: string;
  label: string;
  description: string;
  content_items: string[];
  data_sources: Array<{
    code: string;
    name: string;
    status: 'connected' | 'planned' | 'external_tool';
  }>;
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

export interface AuthUser {
  id: number;
  username: string;
  email: string | null;
  display_name: string | null;
  role: 'viewer' | 'risk_analyst' | 'risk_admin' | 'platform_admin';
  status: 'pending' | 'active' | 'disabled';
  last_login_at: string | null;
  created_at: string;
}

export interface AuthMeResponse {
  user: AuthUser;
  permissions: string[];
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
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
    throw new ApiError(response.status, payload?.detail ? String(payload.detail) : `HTTP ${response.status}`);
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
  },
  alerts: () => request<RiskAlertListResponse>('/api/v1/risk-alerts?status=current&limit=100'),
  suppliers: () => request<SupplierListResponse>('/api/v1/suppliers?limit=100'),
  sources: () => request<DataSourceRead[]>('/api/v1/sources'),
  sourcesAdmin: () => request<DataSourceRead[]>('/api/v1/sources/admin'),
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
  ruleEngineOptions: () => request<RuleEngineOptions>('/api/v1/rule-engine/match-columns'),
  testRuleEngine: (payload: SandboxRequest) => request<SandboxResult>('/api/v1/rule-engine/test', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  }),
  health: () => request<SystemHealth>('/api/v1/system/health'),
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
  runSource: (id: number) => request<CollectionRunRead>(`/api/v1/sources/${id}/run`, {method: 'POST'}),
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
    countryRegion: supplier.country_code, tier: '重点供应商',
    category: supplier.industry ?? supplier.products[0]?.name ?? '未分类',
    suppliedProduct: supplier.products.map((item) => item.name).join('、') || '未登记',
    monitoringStatus: supplier.enabled ? (riskLevel === 'P1' || riskLevel === 'P2' ? 'high_risk' : 'normal') : 'paused',
    riskLevel, riskScore, lastUpdated: site ? `${site.country_code} · ${site.city ?? site.site_name}` : '当前数据',
  };
}

export function mapDataSource(source: DataSourceRead, runs: CollectionRunRead[]): DataSource {
  const lastRun = runs.filter((run) => run.source_id === source.id).sort((a, b) => b.id - a.id)[0];
  const isExternalTool = source.source_type === 'external_tool';
  const accessStatus = source.access_status ?? 'ready';
  const status = accessStatus !== 'ready'
    ? 'warning'
    : isExternalTool
    ? source.enabled && source.api_key_configured ? 'normal' : 'warning'
    : !source.enabled || !lastRun ? 'warning'
    : lastRun.status === 'failed' ? 'error' : lastRun.status === 'succeeded' ? 'normal' : 'warning';
  return {
    id: String(source.id), name: source.name, type: source.source_type, status,
    latency: accessStatus === 'cooldown'
      ? `访问冷却至 ${source.access_cooldown_until ? formatDateTime(source.access_cooldown_until) : '稍后'}`
      : accessStatus === 'busy' ? '同域名请求执行中'
      : accessStatus === 'throttled' ? '域名请求间隔保护中'
      : !source.enabled ? '已停用' : isExternalTool
      ? source.api_key_configured ? '按需核查可用' : '运行密钥未配置'
      : !lastRun ? '尚未运行' : lastRun.status === 'succeeded' ? '运行正常' : lastRun.status === 'failed' ? '运行失败' : '运行中',
    lastSyncTime: isExternalTool ? '按需调用' : lastRun ? formatDateTime(lastRun.finished_at ?? lastRun.started_at) : '尚未运行',
    itemCount: lastRun?.created_count ?? 0,
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
  const severityMax = Math.max(0, ...Object.values(dimension.scoring.severity_scores ?? {}));
  const associationMax = Math.max(0, ...Object.values(dimension.scoring.association_scores ?? {}));
  return {
    id: dimension.key, name: dimension.label, icon: dimensionIcons[dimension.key] ?? 'shield', enabled: dimension.enabled,
    ruleId: String(dimension.scoring.rule_version ?? dimension.key),
    severityWeight: Math.round((severityMax / 35) * 100) / 100,
    relevanceWeight: Math.round((associationMax / 30) * 100) / 100,
    thresholds: {p1: Number(dimension.scoring.p1_min ?? 85), p2: Number(dimension.scoring.p2_min ?? 65), p3: Number(dimension.scoring.p3_min ?? 40)},
    ttlHours: Number(dimension.scoring.alert_expiry_days ?? 14) * 24,
    contentItems: dimension.content_items,
    dataSources: dimension.data_sources,
    source: dimension,
  };
}

export function updateDimensionConfig(original: MonitoringDimension, updated: MonitoringDimension): Record<string, unknown> {
  const source = original.source;
  if (!source) return {};
  const scale = (values: Record<string, number> | undefined, ratio: number, maximum: number) => Object.fromEntries(
    Object.entries(values ?? {}).map(([key, value]) => [key, Math.max(0, Math.min(maximum, Math.round(value * ratio)))]),
  );
  const severityRatio = original.severityWeight > 0 ? updated.severityWeight / original.severityWeight : 1;
  const relevanceRatio = original.relevanceWeight > 0 ? updated.relevanceWeight / original.relevanceWeight : 1;
  return {
    severity_scores: scale(source.scoring.severity_scores, severityRatio, 35),
    association_scores: scale(source.scoring.association_scores, relevanceRatio, 30),
    p1_min: updated.thresholds.p1, p2_min: updated.thresholds.p2, p3_min: updated.thresholds.p3,
    alert_expiry_days: Math.max(1, Math.round(updated.ttlHours / 24)),
  };
}

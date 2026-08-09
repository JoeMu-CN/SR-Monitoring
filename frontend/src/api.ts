import type {DataSource, MonitoringDimension, RiskItem, RiskLevel, Supplier} from './types';

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

export interface ChatResponse {
  session_id: number;
  answer: string;
  tool_calls: ToolCallRead[];
}

export interface AgentStatusRead {
  llm_configured: boolean;
  model: string;
  tyc_enabled: boolean;
  max_steps: number;
}

export interface SystemHealth { status: string; database: string }

export interface SupplierCreatePayload {
  supplier_code: string;
  legal_name: string;
  country_code: string;
  registry_no: string | null;
  industry: string | null;
  raw_materials: string[];
  enabled: boolean;
  aliases: Array<{alias: string; language: string | null}>;
  sites: Array<{
    site_name: string;
    country_code: string;
    region: string | null;
    city: string | null;
    address: string;
    latitude: number | null;
    longitude: number | null;
  }>;
  products: Array<{name: string; keywords: string[]}>;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (currentRole) headers.set('X-User-Role', currentRole);
  headers.set('X-User-Id', 'console-user');
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as {detail?: unknown} | null;
    throw new Error(payload?.detail ? String(payload.detail) : `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

let currentRole: 'viewer' | 'admin' = 'viewer';

export function setApiRole(role: 'viewer' | 'admin'): void {
  currentRole = role;
}

export const api = {
  alerts: () => request<RiskAlertListResponse>('/api/v1/risk-alerts?status=current&limit=100'),
  suppliers: () => request<SupplierListResponse>('/api/v1/suppliers?limit=100'),
  sources: () => request<DataSourceRead[]>('/api/v1/sources'),
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
  chat: (question: string, sessionId: number | null) => request<ChatResponse>('/api/v1/chat', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({question, session_id: sessionId}),
  }),
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
    productionLocation: supplier.sites.map((item) => item.city || item.site_name).join('、') || '未登记',
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
  const status = isExternalTool
    ? source.enabled && source.api_key_configured ? 'normal' : 'warning'
    : !source.enabled || !lastRun ? 'warning'
    : lastRun.status === 'failed' ? 'error' : lastRun.status === 'succeeded' ? 'normal' : 'warning';
  return {
    id: String(source.id), name: source.name, type: source.source_type, status,
    latency: !source.enabled ? '已停用' : isExternalTool
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

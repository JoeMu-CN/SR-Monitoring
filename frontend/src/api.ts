// API 封装：统一请求与类型定义

export interface RiskAlert {
  id: number
  level: 'P1' | 'P2' | 'P3' | 'P4'
  score: number
  score_detail: Record<string, unknown>
  status: string
  supplier_id: number
  supplier_name: string
  event_id: number
  event_type: string
  event_subtype: string | null
  event_summary: string
  event_start_at: string | null
  event_end_at: string | null
  confidence: number
  match_type: string
  match_reasons: string[]
  match_evidence: Array<Record<string, unknown>>
  source_title: string
  source_url: string | null
  published_at: string | null
  updated_at: string
}

export interface AlertListResponse {
  items: RiskAlert[]
  total: number
  limit: number
  offset: number
}

export interface LevelCount {
  level: string
  count: number
}

export interface EventTypeCount {
  event_type: string
  count: number
}

export interface SourceHealth {
  id: number
  code: string
  name: string
  enabled: boolean
  last_run_at: string | null
  last_run_status: string | null
}

export interface DashboardSummary {
  level_counts: LevelCount[]
  total_current: number
  today_new: number
  type_distribution: EventTypeCount[]
  recent_alerts: RiskAlert[]
  sources: SourceHealth[]
}

export interface SupplierSite {
  id?: number
  site_name: string
  country_code: string
  region: string | null
  city: string | null
  address: string
  latitude: number | null
  longitude: number | null
}

export interface SupplierProduct {
  id?: number
  name: string
  keywords: string[]
}

export interface Supplier {
  id: number
  supplier_code: string
  legal_name: string
  country_code: string
  registry_no: string | null
  enabled: boolean
  aliases: Array<{ id?: number; alias: string; language: string | null }>
  sites: SupplierSite[]
  products: SupplierProduct[]
}

export interface SupplierListResponse {
  items: Supplier[]
  total: number
  limit: number
  offset: number
}

export interface CollectionRun {
  id: number
  source_id: number
  started_at: string
  finished_at: string | null
  status: string
  fetched_count: number
  created_count: number
  duplicate_count: number
  error: string | null
}

export interface DataSourceItem {
  id: number
  code: string
  name: string
  source_type: string
  credibility: number
  schedule: string | null
  enabled: boolean
}

export interface EventDetail {
  id: number
  dedup_key: string
  event_type: string
  event_subtype: string | null
  severity: string
  summary: string
  start_at: string | null
  end_at: string | null
  confidence: number
  created_at: string
  signals: Array<{
    signal_id: number
    title: string
    content: string
    url: string | null
    published_at: string | null
  }>
  entities: Array<Record<string, unknown>>
  locations: Array<Record<string, unknown>>
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, options)
  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    const message =
      detail && typeof detail === 'object' && 'detail' in detail
        ? String((detail as { detail: unknown }).detail)
        : `HTTP ${response.status}`
    throw new Error(message)
  }
  return (await response.json()) as T
}

export interface ForcedRuleInfo {
  name: string
  description: string
  event_types: string[]
  event_subtypes: string[]
  match_types: string[]
  forced_level: string
  reason: string
}

export interface DimensionScoring {
  rule_version: string
  severity_scores: Record<string, number>
  association_scores: Record<string, number>
  credibility_weight: number
  timeliness_with_date: number
  timeliness_without_date: number
  product_relevance_score: number
  p1_min: number
  p2_min: number
  p3_min: number
  strong_match_types: string[]
  alert_expiry_days: number
  forced_rules: ForcedRuleInfo[]
}

export interface RuleEngineDimension {
  key: string
  label: string
  description: string
  event_types: string[]
  match_columns: string[]
  enabled: boolean
  has_override: boolean
  active_alerts: number
  scoring: DimensionScoring
}

export interface MatchColumnOption {
  value: string
  label: string
}

export interface SandboxCandidate {
  supplier_id: number
  supplier_name: string
  match_type: string
  association_score: number
  reasons: string[]
  score: number
  level: string
  score_detail: Record<string, unknown>
}

export interface SandboxResult {
  dimension: { key: string; label: string; match_columns: string[] } | null
  message?: string
  candidates: SandboxCandidate[]
}

export const api = {
  dashboard: () => request<DashboardSummary>('/api/v1/dashboard/summary'),
  alerts: (params: { status?: string; level?: string; limit?: number; offset?: number } = {}) => {
    const query = new URLSearchParams()
    query.set('status', params.status ?? 'current')
    if (params.level) query.set('level', params.level)
    query.set('limit', String(params.limit ?? 50))
    query.set('offset', String(params.offset ?? 0))
    return request<AlertListResponse>(`/api/v1/risk-alerts?${query.toString()}`)
  },
  alertDetail: (id: number) => request<RiskAlert>(`/api/v1/risk-alerts/${id}`),
  eventDetail: (id: number) => request<EventDetail>(`/api/v1/events/${id}`),
  suppliers: (params: { keyword?: string; limit?: number; offset?: number } = {}) => {
    const query = new URLSearchParams()
    if (params.keyword) query.set('q', params.keyword)
    query.set('limit', String(params.limit ?? 50))
    query.set('offset', String(params.offset ?? 0))
    return request<SupplierListResponse>(`/api/v1/suppliers?${query.toString()}`)
  },
  supplierDetail: (id: number) => request<Supplier>(`/api/v1/suppliers/${id}`),
  toggleSupplier: (id: number, enabled: boolean) =>
    request<Supplier>(`/api/v1/suppliers/${id}/enabled`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    }),
  sources: () => request<DataSourceItem[]>('/api/v1/sources'),
  collectionRuns: (sourceId?: number, limit = 10) => {
    const query = new URLSearchParams()
    if (sourceId) query.set('source_id', String(sourceId))
    query.set('limit', String(limit))
    return request<{ items: CollectionRun[]; total: number }>(
      `/api/v1/collection-runs?${query.toString()}`,
    )
  },
  runSource: (sourceId: number) =>
    request<CollectionRun>(`/api/v1/sources/${sourceId}/run`, { method: 'POST' }),
  ruleEngine: {
    dimensions: () => request<RuleEngineDimension[]>('/api/v1/rule-engine/dimensions'),
    matchColumns: () =>
      request<{
        match_columns: string[]
        event_types: MatchColumnOption[]
        event_subtypes: MatchColumnOption[]
      }>(
        '/api/v1/rule-engine/match-columns',
      ),
    update: (key: string, payload: { enabled?: boolean; config?: Record<string, unknown> }) =>
      request<RuleEngineDimension>(`/api/v1/rule-engine/dimensions/${key}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }),
    toggle: (key: string, enabled: boolean) =>
      request<RuleEngineDimension>(`/api/v1/rule-engine/dimensions/${key}/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      }),
    test: (payload: Record<string, unknown>) =>
      request<SandboxResult>('/api/v1/rule-engine/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }),
  },
  health: () => request<{ status: string; database: string }>('/api/v1/system/health'),
}

export const eventLabels: Record<string, string> = {
  weather: '天气',
  geological: '地质灾害',
  logistics: '物流',
  trade_policy: '贸易政策',
  geopolitical: '地缘政治',
  corporate: '企业经营',
  judicial: '司法',
  compliance: '合规',
  other: '其他',
}

export const eventSubtypeLabels: Record<string, string> = {
  weather_alert: '气象预警',
  geological_hazard: '地质灾害',
  armed_conflict: '武装冲突',
  sanctions: '制裁',
  export_control: '出口管制',
  political_instability: '政治不稳定',
  public_security: '公共安全',
  trade_tariff: '关税与一般贸易摩擦',
  regulatory_change: '监管政策变化',
  raw_material_shortage: '原材料短缺',
  transport_disruption: '运输中断',
  corporate_distress: '企业经营异常',
  judicial_case: '司法案件',
  compliance_violation: '合规违规',
  other: '其他',
}

export function formatDateTime(value: string | null): string {
  if (!value) return '时间未披露'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

export function formatTime(value: string | null): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

import React from 'react';
import {cleanup, render, screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest';
import {api, ApiError} from './api';
import type {
  AgentStatusRead,
  AuthUser,
  CollectionRunRead,
  DataSourceRead,
  DimensionRead,
  DimensionInputsRead,
  RuleEngineOptions,
  RiskAlertRead,
  SupplierListItem,
  SystemHealth,
} from './api';
import {App} from './App';

// ---- mock api 模块：保留真实 map* 与 ApiError，仅把网络方法替换为可控 mock ----
vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      auth: {
        ...actual.api.auth,
        me: vi.fn(),
        login: vi.fn(),
        logout: vi.fn(),
        listUsers: vi.fn(),
        createUser: vi.fn(),
        updateUser: vi.fn(),
        resetPassword: vi.fn(),
      },
      alerts: vi.fn(),
      suppliers: vi.fn(),
      sources: vi.fn(),
      sourcesAdmin: vi.fn(),
      collectionRuns: vi.fn(),
      dimensions: vi.fn(),
      health: vi.fn(),
      agentStatus: vi.fn(),
      dimensionInputs: vi.fn(),
      ruleEngineOptions: vi.fn(),
    },
  };
});

const viewerUser: AuthUser = {
  id: 1,
  username: 'e2e-viewer',
  email: null,
  display_name: '只读账号',
  role: 'viewer',
  status: 'active',
  last_login_at: null,
  created_at: '2026-01-01T00:00:00Z',
};

const VIEWER_PERMISSIONS = ['risk_view', 'supplier_view', 'source_status_view', 'rule_summary_view'];

const platformAdminUser: AuthUser = {
  id: 2,
  username: 'e2e-platform-admin',
  email: null,
  display_name: '平台管理员',
  role: 'platform_admin',
  status: 'active',
  last_login_at: null,
  created_at: '2026-01-01T00:00:00Z',
};

const ADMIN_PERMISSIONS = [
  'risk_view', 'supplier_view', 'source_status_view', 'rule_summary_view', 'risk_query_use',
  'user_manage', 'source_manage', 'supplier_manage', 'rule_manage',
];

const agentStatusOk: AgentStatusRead = {llm_configured: true, model: 'qwen-plus', tyc_enabled: false, max_steps: 5};

const healthOk: SystemHealth = {status: 'ok', database: 'ok'};

const denied403 = new ApiError(403, '权限不足');

const alertBackend: RiskAlertRead = {
  id: 1,
  level: 'P1',
  score: 86,
  score_detail: {severity: 30, association: 25, source_credibility: 18, timeliness: 8, product_relevance: 5, rule_version: 'rule-v1'},
  status: 'current',
  supplier_id: 1,
  supplier_name: '示例精密电子有限公司',
  event_id: 10,
  event_type: 'compliance',
  event_subtype: 'sanction',
  event_summary: '因违反出口管制被列入制裁实体清单',
  event_start_at: '2026-08-01T00:00:00Z',
  event_end_at: null,
  confidence: 0.97,
  match_type: 'registry_no',
  match_reasons: ['注册号精确匹配'],
  match_evidence: [],
  source_title: 'OFAC SDN',
  source_url: null,
  published_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-02T00:00:00Z',
  expires_at: null,
  expiry_kind: 'event_end_plus_grace',
  validity_state: 'active',
  valid_until: null,
  review_due_at: null,
  validity_policy_version: 'rule-v1',
  validity_reason: {code: 'active', anchor_source: 'published_at', details: {}},
};

const supplierBackend: SupplierListItem = {
  id: 1,
  supplier_code: 'SUP-0001',
  legal_name: '示例精密电子有限公司',
  country_code: 'CN',
  registry_no: '91330100MA1234567X',
  registration_address: '浙江省杭州市滨江区江陵路100号',
  industry: '电子元件制造',
  raw_materials: ['硅片'],
  enabled: true,
  aliases: [{id: 1, alias: '示例电子', language: 'zh'}],
  sites: [{
    id: 1,
    site_name: '杭州工厂',
    country_code: 'CN',
    region: '浙江省',
    city: '杭州市',
    district: '滨江区',
    address: '江陵路100号',
    latitude: null,
    longitude: null,
  }],
  products: [{id: 1, name: '微电子元件', keywords: []}],
  current_risk_level: 'P1',
  current_risk_score: 86,
};

const sourceBackend: DataSourceRead = {
  id: 1,
  code: 'nmc-weather',
  name: '中央气象台预警',
  source_type: 'pull',
  credibility: 90,
  schedule: '*/5 * * * *',
  endpoint_url: 'https://example.org/nmc/weather.json',
  auth_type: 'none',
  login_config: {},
  credential_ref: null,
  api_key_configured: false,
  api_key_hint: null,
  description: null,
  adapter_config: {},
  adapter_status: 'builtin',
  adapter_version: 1,
  adapter_published_at: null,
  access_status: 'ready',
  access_cooldown_until: null,
  access_last_http_status: 200,
  access_last_error_kind: null,
  enabled: true,
  total_signal_count: 12,
  valid_signal_count: 12,
  signal_validity_days: 30,
  validity_policy: {mode: 'fixed_days', fixed_days: 30},
  validity_policy_version: 'v1',
};

const dimensionBackend = (
  key: string,
  label: string,
  contentItems: string[],
  eventTypes: string[],
): DimensionRead => ({
  key,
  label,
  description: `${label}监控说明`,
  content_items: contentItems,
  data_sources: [],
  event_types: eventTypes,
  match_columns: ['entity', 'location', 'product'],
  enabled: true,
  has_override: false,
  active_alerts: 0,
  scoring: {
    rule_version: 'v1',
    severity_scores: {critical: 35, high: 28, medium: 20, low: 10},
    association_scores: {registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12},
    p1_min: 85,
    p2_min: 65,
    p3_min: 40,
    forced_rules: [],
  },
});

const DIMENSION_LABELS = ['自然环境', '经济贸易', '地缘政治与安全', '监管政策', '行业景气', '企业经营'];

const sixDimensions: DimensionRead[] = [
  dimensionBackend('natural', '自然环境', ['地震', '台风'], ['weather', 'geological']),
  dimensionBackend('economic', '经济贸易', ['贸易政策', '关税'], ['trade_policy']),
  dimensionBackend('geopolitical', '地缘政治与安全', ['制裁', '出口管制'], ['geopolitical']),
  dimensionBackend('policy', '监管政策', ['环保处罚', '资质吊销'], ['compliance']),
  dimensionBackend('industry', '行业景气', ['产能下降', '停产'], ['corporate']),
  dimensionBackend('corporate', '企业经营', ['司法诉讼', '失信被执行'], ['judicial', 'other']),
];

const inputsOk: DimensionInputsRead = {
  declared_total: 1,
  declared_linked: 1,
  declared_enabled: 1,
  observed: [{code: 'nmc-weather', name: '中央气象台预警', signal_count: 12, latest_at: '2026-09-01T00:00:00Z'}],
  has_input: true,
};

const optionsOk: RuleEngineOptions = {
  match_columns: ['entity', 'location', 'product'],
  event_types: [
    {value: 'weather', label: '天气'},
    {value: 'geological', label: '地质灾害'},
    {value: 'trade_policy', label: '贸易政策'},
    {value: 'geopolitical', label: '地缘政治'},
    {value: 'compliance', label: '合规'},
    {value: 'corporate', label: '企业经营'},
    {value: 'judicial', label: '司法'},
    {value: 'other', label: '其他'},
  ],
  event_subtypes: [],
};

const noCollectionRuns: {items: CollectionRunRead[]; total: number} = {items: [], total: 0};

const defaultMocks = (overrides: {permissions?: string[]; user?: AuthUser; agentStatus?: () => Promise<AgentStatusRead>} = {}) => {
  const user = overrides.user ?? viewerUser;
  const permissions = overrides.permissions ?? VIEWER_PERMISSIONS;
  vi.mocked(api.auth.me).mockResolvedValue({user, permissions});
  vi.mocked(api.auth.login).mockResolvedValue(user);
  vi.mocked(api.auth.logout).mockResolvedValue({detail: 'ok'});
  vi.mocked(api.alerts).mockResolvedValue({items: [alertBackend], total: 1});
  vi.mocked(api.suppliers).mockResolvedValue({items: [supplierBackend], total: 1, limit: 20, offset: 0});
  vi.mocked(api.sources).mockResolvedValue([sourceBackend]);
  vi.mocked(api.sourcesAdmin).mockResolvedValue([sourceBackend]);
  vi.mocked(api.collectionRuns).mockResolvedValue(noCollectionRuns);
  vi.mocked(api.dimensions).mockResolvedValue(sixDimensions);
  vi.mocked(api.health).mockResolvedValue(healthOk);
  vi.mocked(api.agentStatus).mockImplementation(overrides.agentStatus ?? (async () => agentStatusOk));
  vi.mocked(api.dimensionInputs).mockResolvedValue(inputsOk);
  vi.mocked(api.ruleEngineOptions).mockResolvedValue(optionsOk);
};

const renderApp = (path: string) => render(
  <MemoryRouter initialEntries={[path]}>
    <App />
  </MemoryRouter>,
);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// jsdom 未实现 Element#scrollIntoView；风险查询助手挂载时会调用它，统一垫上 no-op。
beforeAll(() => {
  Element.prototype.scrollIntoView ??= vi.fn();
});

describe('App loadData：Agent 状态接口失败不阻塞只读角色主数据', () => {
  it('agentStatus 返回 403 时 /rules 六个维度仍全部渲染、写控件禁用、无全局「权限不足」横幅', async () => {
    defaultMocks({agentStatus: async () => { throw denied403; }});
    renderApp('/rules');

    // 维度主数据加载成功：六个维度名称逐一出现
    for (const label of DIMENSION_LABELS) {
      expect((await screen.findAllByText(label)).length).toBeGreaterThan(0);
    }
    // viewer 写控件禁用
    expect(screen.getByRole('button', {name: '保存配置'})).toBeDisabled();
    // 全局错误横幅未被 agentStatus 的 403 触发
    expect(screen.queryByText('权限不足')).not.toBeInTheDocument();
  });

  it('agentStatus 返回 403 时风险提醒与供应商主数据仍正常渲染（/overview）', async () => {
    defaultMocks({agentStatus: async () => { throw denied403; }});
    renderApp('/overview');

    expect(await screen.findByText('全网供应链风险概览')).toBeInTheDocument();
    // 风险提醒（来自 alerts 主数据）正常渲染
    expect((await screen.findAllByText('示例精密电子有限公司')).length).toBeGreaterThan(0);
    // 供应商（来自 suppliers 主数据）计数正常渲染：总监控企业 1 家、当前风险提醒 1 条
    const supplierStat = screen.getByText((_content, element) => (
      element?.tagName === 'SPAN' && element.textContent === '总监控企业：1 家'
    ));
    const riskStat = screen.getByText((_content, element) => (
      element?.tagName === 'SPAN' && element.textContent === '当前风险提醒：1 条'
    ));
    expect(supplierStat).toBeInTheDocument();
    expect(riskStat).toBeInTheDocument();
    expect(screen.queryByText('权限不足')).not.toBeInTheDocument();
  });

  it('agentStatus 返回 403 时数据源列表仍正常渲染（/sources）', async () => {
    defaultMocks({agentStatus: async () => { throw denied403; }});
    renderApp('/sources');

    expect(await screen.findByText('中央气象台预警')).toBeInTheDocument();
    expect(screen.queryByText('权限不足')).not.toBeInTheDocument();
  });
});

describe('App loadData：真正的核心主数据失败仍报错', () => {
  it('维度主数据接口 500 时出现全局错误横幅、渲染停止、且不再发起 agentStatus', async () => {
    defaultMocks();
    vi.mocked(api.dimensions).mockRejectedValue(new ApiError(500, '规则维度查询失败'));
    renderApp('/overview');

    // 全局错误横幅显示核心失败消息
    expect(await screen.findByText('规则维度查询失败')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '重新加载'})).toBeInTheDocument();
    // 核心数据失败属于整页失败：不进入后续 agentStatus 容错请求
    expect(api.agentStatus).not.toHaveBeenCalled();
  });
});

describe('App 中依赖 agentStatus 的视图', () => {
  it('agentStatus 不可用（保持 null）时风险查询助手页正常渲染、不崩溃', async () => {
    defaultMocks({
      user: platformAdminUser,
      permissions: ADMIN_PERMISSIONS,
      agentStatus: async () => { throw new ApiError(500, 'Agent 状态服务不可用'); },
    });
    renderApp('/assistant');

    // 风险查询助手顶栏渲染成功（不因 agentStatus 为 null 崩溃）
    expect(await screen.findByRole('heading', {name: '风险查询助手'})).toBeInTheDocument();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    // 无全局横幅（agentStatus 500 不触发全局 error）
    expect(screen.queryByText('Agent 状态服务不可用')).not.toBeInTheDocument();
  });
});

import React from 'react';
import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
  SupplierRead,
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
      supplierPage: vi.fn(),
      sources: vi.fn(),
      sourcesAdmin: vi.fn(),
      collectionRuns: vi.fn(),
      dimensions: vi.fn(),
      health: vi.fn(),
      agentStatus: vi.fn(),
      dimensionInputs: vi.fn(),
      ruleEngineOptions: vi.fn(),
      getSupplier: vi.fn(),
      updateSupplier: vi.fn(),
      createSupplier: vi.fn(),
      toggleSupplier: vi.fn(),
      deleteSupplier: vi.fn(),
      supplierDeletionImpact: vi.fn(),
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
  updated_at: '2026-09-01T00:00:00Z',
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

// —— 编辑弹窗行为测试夹具 ——
// 详情中首条地点国家(DE)与供应商国家(CN)不同，用于锁定「不把供应商国家当生产地点国家」。
const editDetailA: SupplierRead = {
  id: 1,
  supplier_code: 'SUP-0001',
  legal_name: '示例精密电子有限公司',
  country_code: 'CN',
  registry_no: '91330100MA1234567X',
  registration_address: '浙江省杭州市滨江区江陵路100号',
  industry: '电子元件制造',
  raw_materials: ['硅片'],
  enabled: true,
  updated_at: '2026-09-01T10:00:00Z',
  aliases: [{id: 10, alias: '示例电子', language: 'zh'}],
  sites: [{
    id: 20,
    site_name: '杭州工厂',
    country_code: 'DE',
    region: '巴伐利亚州',
    city: '慕尼黑',
    district: null,
    address: '慕尼黑工业区1号',
    latitude: 48.1,
    longitude: 11.5,
  }],
  products: [{id: 30, name: '功率器件', keywords: ['MOSFET', 'IGBT']}],
};

// 「他人修改后」的最新详情：updated_at 令牌与法人名称、地点城市均不同，可观察重载同步。
const editDetailAFresh: SupplierRead = {
  ...editDetailA,
  legal_name: '服务器最新名称有限公司',
  updated_at: '2026-09-02T00:00:00Z',
  sites: [{...editDetailA.sites[0], city: '苏州市'}],
};

const editDetailB: SupplierRead = {
  ...editDetailA,
  id: 2,
  supplier_code: 'SUP-0002',
  legal_name: '乙测试精密有限公司',
  registry_no: '91330100MA7654321X',
  updated_at: '2026-09-01T11:00:00Z',
  aliases: [{id: 12, alias: '乙测试', language: 'zh'}],
  sites: [{...editDetailA.sites[0], id: 21, site_name: '苏州工厂', country_code: 'CN', region: '江苏省', city: '苏州市', district: '工业园区', address: '金鸡湖大道2号'}],
  products: [{id: 31, name: '连接器', keywords: []}],
};

const supplierListItemB: SupplierListItem = {
  ...supplierBackend,
  id: 2,
  supplier_code: 'SUP-0002',
  legal_name: '乙测试精密有限公司',
  aliases: [{id: 12, alias: '乙测试', language: 'zh'}],
  sites: [{...supplierBackend.sites[0], id: 21, site_name: '苏州工厂', city: '苏州市'}],
  products: [{...supplierBackend.products[0], id: 31, name: '连接器'}],
  current_risk_level: null,
  current_risk_score: null,
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
  vi.mocked(api.supplierPage).mockResolvedValue({items: [supplierBackend], total: 1, limit: 20, offset: 0});
  vi.mocked(api.sources).mockResolvedValue([sourceBackend]);
  vi.mocked(api.sourcesAdmin).mockResolvedValue([sourceBackend]);
  vi.mocked(api.collectionRuns).mockResolvedValue(noCollectionRuns);
  vi.mocked(api.dimensions).mockResolvedValue(sixDimensions);
  vi.mocked(api.health).mockResolvedValue(healthOk);
  vi.mocked(api.agentStatus).mockImplementation(overrides.agentStatus ?? (async () => agentStatusOk));
  vi.mocked(api.dimensionInputs).mockResolvedValue(inputsOk);
  vi.mocked(api.ruleEngineOptions).mockResolvedValue(optionsOk);
  vi.mocked(api.supplierDeletionImpact).mockResolvedValue({
    can_delete: true,
    match_count: 0,
    alert_count: 0,
    sites_count: 1,
    products_count: 1,
    aliases_count: 1,
    blocked_reason: null,
  });
};

const renderApp = (path: string) => render(
  <MemoryRouter initialEntries={[path]}>
    <App />
  </MemoryRouter>,
);

afterEach(() => {
  cleanup();
  // reset 而非 clear：mockResolvedValueOnce 等一次性实现若跨测试残留，
  // 会把上一个用例的队列错配到下一个用例（表现为偶发超时/错值）。
  vi.resetAllMocks();
});

// jsdom 未实现 Element#scrollIntoView；风险查询助手挂载时会调用它，统一垫上 no-op。
beforeAll(() => {
  Element.prototype.scrollIntoView ??= vi.fn();
});

describe('App loadData：Agent 状态接口失败不阻塞只读角色主数据', () => {
  it('agentStatus 返回 403 时 /rules 六个维度仍全部渲染、写控件禁用、无全局「权限不足」横幅', async () => {
    defaultMocks({agentStatus: async () => { throw denied403; }});
    renderApp('/rules');

    // 等待 route-content 出现：loading=false + auth 就绪后 AppRoutes 才挂载
    const routeContent = await screen.findByTestId('route-content', {}, {timeout: 5000});
    // 维度主数据加载成功：六个维度名称均已渲染
    for (const label of DIMENSION_LABELS) {
      expect(within(routeContent).getAllByText(label).length).toBeGreaterThan(0);
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

// ---- 供应商编辑弹窗：409 并发冲突语义与地点字段无损（任务2 语义缺口回归）----

const adminSetup = () => {
  defaultMocks({user: platformAdminUser, permissions: ADMIN_PERMISSIONS});
  vi.mocked(api.supplierPage).mockResolvedValue({items: [supplierBackend, supplierListItemB], total: 2, limit: 20, offset: 0});
};

const openEditModal = async (label: string, code: string) => {
  const user = userEvent.setup();
  renderApp('/suppliers');
  // 先等供应商页挂载完成，再找行内编辑按钮（jsdom 负载下默认 1s 可能不够）
  await screen.findByText('供应商管理');
  await user.click(await screen.findByRole('button', {name: `编辑供应商：${label}`}, {timeout: 5000}));
  await screen.findByText(`编辑供应商 · ${code}`);
  return user;
};

describe('App 供应商编辑：409 冲突后不得自动替换并发令牌', () => {
  it('409 后不自动调用 getSupplier 刷新令牌，再次保存仍携带同一旧 expected_updated_at', async () => {
    adminSetup();
    vi.mocked(api.getSupplier)
      .mockResolvedValueOnce(editDetailA)
      .mockResolvedValueOnce(editDetailAFresh);
    vi.mocked(api.updateSupplier).mockRejectedValue(new ApiError(409, '供应商已被其他人修改，请刷新后重试'));
    const user = await openEditModal('示例精密电子有限公司', 'SUP-0001');

    // 用户修改法人名称
    const legalInput = await screen.findByDisplayValue('示例精密电子有限公司', {}, {timeout: 5000});
    await user.clear(legalInput);
    await user.type(legalInput, '用户改名有限公司');
    await user.click(screen.getByRole('button', {name: '保存修改'}));
    await screen.findByText(/已被其他用户修改/);
    // 用户输入保留
    expect(await screen.findByDisplayValue('用户改名有限公司', {}, {timeout: 5000})).toBeInTheDocument();
    // 关键：409 后不得自动重拉详情刷新 expected_updated_at（getSupplier 仅在打开编辑时调用一次）
    expect(api.getSupplier).toHaveBeenCalledTimes(1);

    // 再次保存：payload 仍基于旧详情构造，携带同一旧令牌（直接重试会被再次 409，而非静默覆盖）
    await user.click(screen.getByRole('button', {name: '保存修改'}));
    expect(api.updateSupplier).toHaveBeenCalledTimes(2);
    const [, payload1] = vi.mocked(api.updateSupplier).mock.calls[0];
    const [, payload2] = vi.mocked(api.updateSupplier).mock.calls[1];
    expect(payload1.expected_updated_at).toBe('2026-09-01T10:00:00Z');
    expect(payload2.expected_updated_at).toBe('2026-09-01T10:00:00Z');
  });

  it('409 提示要求关闭后重新打开；明确重载时表单与完整详情同步为最新数据', async () => {
    adminSetup();
    vi.mocked(api.getSupplier)
      .mockResolvedValueOnce(editDetailA)
      .mockResolvedValueOnce(editDetailAFresh);
    vi.mocked(api.updateSupplier).mockRejectedValueOnce(new ApiError(409, '供应商已被其他人修改，请刷新后重试'))
      .mockResolvedValue(editDetailAFresh);
    const user = await openEditModal('示例精密电子有限公司', 'SUP-0001');

    // 先修改法人名称，再触发 409
    const legalInput = await screen.findByDisplayValue('示例精密电子有限公司', {}, {timeout: 5000});
    await user.clear(legalInput);
    await user.type(legalInput, '用户改名有限公司');
    await user.click(screen.getByRole('button', {name: '保存修改'}));
    // 冲突提示要求明确重载（关闭后重新打开），而非声称已自动重载
    expect(await screen.findByText(/已被其他用户修改/)).toBeInTheDocument();
    expect(screen.getByText(/请关闭后重新打开/)).toBeInTheDocument();
    // 用户输入保留
    expect(await screen.findByDisplayValue('用户改名有限公司', {}, {timeout: 5000})).toBeInTheDocument();

    // 明确重载：关闭弹窗后重新打开同一供应商
    await user.click(screen.getByRole('button', {name: '取消'}));
    await user.click(await screen.findByRole('button', {name: '编辑供应商：示例精密电子有限公司'}, {timeout: 5000}));
    await screen.findByText('编辑供应商 · SUP-0001');
    expect(api.getSupplier).toHaveBeenCalledTimes(2);

    // 表单与完整详情同步：法人名称与地点字段均为服务器最新值
    expect(screen.getByDisplayValue('服务器最新名称有限公司')).toBeInTheDocument();
    expect(screen.getByDisplayValue('苏州市')).toBeInTheDocument();

    // 完整详情同步：不再修改直接保存 → 携带重载后的新令牌
    await user.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(vi.mocked(api.updateSupplier)).toHaveBeenCalledTimes(2));
    const [, reloadedPayload] = vi.mocked(api.updateSupplier).mock.calls[1];
    expect(reloadedPayload.expected_updated_at).toBe('2026-09-02T00:00:00Z');
  });
});

describe('App 供应商编辑：地点字段无损', () => {
  it('编辑保存不把供应商国家写入生产地点国家，未编辑地点字段原值保留', async () => {
    adminSetup();
    vi.mocked(api.getSupplier).mockResolvedValue(editDetailA);
    vi.mocked(api.updateSupplier).mockResolvedValue(editDetailAFresh);
    const user = await openEditModal('示例精密电子有限公司', 'SUP-0001');

    // 仅修改法人名称，不触碰任何地点字段
    const legalInput = await screen.findByDisplayValue('示例精密电子有限公司', {}, {timeout: 5000});
    await user.clear(legalInput);
    await user.type(legalInput, '仅改名称有限公司');
    await user.click(screen.getByRole('button', {name: '保存修改'}));

    const [savedId, payload] = vi.mocked(api.updateSupplier).mock.calls[0];
    expect(savedId).toBe(1);
    // 供应商国家随表单更新，但不得下写到生产地点
    expect(payload.country_code).toBe('CN');
    expect(payload.sites[0].country_code).toBe('DE');
    // 未编辑的地点字段按详情原值回传，不被表单空值清空或拼接近似值污染
    expect(payload.sites[0].site_name).toBe('杭州工厂');
    expect(payload.sites[0].region).toBe('巴伐利亚州');
    expect(payload.sites[0].city).toBe('慕尼黑');
    expect(payload.sites[0].district).toBeNull();
    expect(payload.sites[0].address).toBe('慕尼黑工业区1号');
    expect(payload.sites[0].latitude).toBe(48.1);
    expect(payload.sites[0].longitude).toBe(11.5);
    // 首产品名变更保留关键词
    expect(payload.products[0].name).toBe('功率器件');
    expect(payload.products[0].keywords).toEqual(['MOSFET', 'IGBT']);
  });

  it('无地点无产品供应商仅改名称时保持空集合', async () => {
    adminSetup();
    const emptyDetail: SupplierRead = {...editDetailA, sites: [], products: []};
    vi.mocked(api.getSupplier).mockResolvedValue(emptyDetail);
    vi.mocked(api.updateSupplier).mockResolvedValue(emptyDetail);
    const user = await openEditModal('示例精密电子有限公司', 'SUP-0001');

    const legalInput = await screen.findByDisplayValue('示例精密电子有限公司', {}, {timeout: 5000});
    await user.clear(legalInput);
    await user.type(legalInput, '仅改名称有限公司');
    await user.click(screen.getByRole('button', {name: '保存修改'}));

    const [, payload] = vi.mocked(api.updateSupplier).mock.calls[0];
    expect(payload.sites).toEqual([]);
    expect(payload.products).toEqual([]);
  });

  it('快速连续编辑两个供应商时，先发出的旧详情响应不会覆盖新选择', async () => {
    adminSetup();
    let resolveA!: (detail: SupplierRead) => void;
    vi.mocked(api.getSupplier).mockImplementation((id: number) => {
      if (id === 1) {
        return new Promise<SupplierRead>((resolve) => { resolveA = resolve; });
      }
      return Promise.resolve(editDetailB);
    });
    const user = userEvent.setup();
    renderApp('/suppliers');
    await screen.findByText('供应商管理');

    // 先点供应商 A（详情请求挂起），再点供应商 B
    await user.click(await screen.findByRole('button', {name: '编辑供应商：示例精密电子有限公司'}, {timeout: 5000}));
    await user.click(await screen.findByRole('button', {name: '编辑供应商：乙测试精密有限公司'}, {timeout: 5000}));
    await screen.findByText('编辑供应商 · SUP-0002');
    expect(screen.getByDisplayValue('乙测试精密有限公司')).toBeInTheDocument();

    // A 的旧详情此时才返回：不得覆盖当前选择的 B
    resolveA(editDetailA);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByText('编辑供应商 · SUP-0002')).toBeInTheDocument();
    expect(screen.getByDisplayValue('乙测试精密有限公司')).toBeInTheDocument();
  });
});

describe('App 供应商编辑：删除影响展示', () => {
  const blockedImpact = {
    can_delete: false,
    match_count: 3,
    alert_count: 2,
    sites_count: 2,
    products_count: 1,
    aliases_count: 1,
    blocked_reason: 'supplier_has_risk_history',
  };

  it('管理员打开编辑时拉取删除影响并在弹窗内渲染阻止提示', async () => {
    adminSetup();
    vi.mocked(api.getSupplier).mockResolvedValue(editDetailA);
    vi.mocked(api.supplierDeletionImpact).mockResolvedValue(blockedImpact);
    const user = userEvent.setup();
    renderApp('/suppliers');
    await screen.findByText('供应商管理');
    await user.click(await screen.findByRole('button', {name: '编辑供应商：示例精密电子有限公司'}, {timeout: 5000}));
    await screen.findByText('编辑供应商 · SUP-0001');

    expect(api.supplierDeletionImpact).toHaveBeenCalledTimes(1);
    const alertBox = await screen.findByRole('alert', {}, {timeout: 5000});
    expect(alertBox).toHaveTextContent('风险关联 3 条');
    expect(alertBox).toHaveTextContent('暂停监控可保留全部历史');
  });

  it('删除影响请求失败时弹窗仍可编辑并显示错误，不触发全局横幅', async () => {
    adminSetup();
    vi.mocked(api.getSupplier).mockResolvedValue(editDetailA);
    vi.mocked(api.supplierDeletionImpact).mockRejectedValue(new ApiError(403, '权限不足'));
    const user = userEvent.setup();
    renderApp('/suppliers');
    await screen.findByText('供应商管理');
    await user.click(await screen.findByRole('button', {name: '编辑供应商：示例精密电子有限公司'}, {timeout: 5000}));
    await screen.findByText('编辑供应商 · SUP-0001');

    const alertBox = await screen.findByRole('alert', {}, {timeout: 5000});
    expect(alertBox).toHaveTextContent('权限不足');
    expect(alertBox).toHaveTextContent('服务端仍会执行安全检查');
    // 编辑功能不受影响：法人名称可修改
    const legalInput = await screen.findByDisplayValue('示例精密电子有限公司', {}, {timeout: 5000});
    await user.clear(legalInput);
    await user.type(legalInput, '仍可编辑有限公司');
    expect(screen.getByDisplayValue('仍可编辑有限公司')).toBeInTheDocument();
    // 无全局错误横幅（横幅内含「重新加载」按钮；此处不应出现）
    expect(screen.queryByRole('button', {name: '重新加载'})).not.toBeInTheDocument();
  });
});

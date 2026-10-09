import React from 'react';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {MemoryRouter} from 'react-router-dom';
import {
  api,
  DimensionInputsRead,
  DimensionTraceRead,
  ForcedRuleRead,
  GlobalScoringConfigRead,
  RuleEngineOptions,
} from '../api';
import type {MonitoringDimension} from '../types';
import {RuleEngineView} from './RuleEngineView';

/**
 * 壳层测试约定（todo 4，独立评审缺陷修复轮）：
 * 本文件只做「模式感知的壳层/挂载断言」—— 默认观察态、admin 可切换、viewer 不可切换、
 * 各子组件挂载点与壳层关键文案；不绑定具体控件行为（patch 只含改动项、空柱阻止、
 * 422 冲突详情、保存次数、勾选交互等）。那些行为断言归属 todos 7/8 新建的
 * RuleEngineScoringEditor.test.tsx / RuleEngineForcedRuleEditor.test.tsx（本任务不创建），
 * 以保证 todos 7/8/9 替换子组件实现后本文件仍然成立。
 */

const dimension = (overrides: Partial<MonitoringDimension> = {}): MonitoringDimension => ({
  id: 'natural',
  name: '自然环境',
  icon: 'landscape',
  enabled: true,
  ruleId: 'natural-v1',
  severityScores: {critical: 35, high: 28, medium: 20, low: 10},
  associationScores: {registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12},
  thresholds: {p1: 85, p2: 65, p3: 40},
  matchColumns: ['entity', 'location', 'product'],
  eventTypes: ['weather', 'geological'],
  forcedRules: [],
  contentItems: ['地震', '台风'],
  dataSources: [
    {code: 'nmc-weather', name: '中央气象台', status: 'connected', linked: true, enabled: true, adapterStatus: 'builtin', lastCollectedAt: '2026-09-01T08:00:00Z', validSignalCount: 12},
    {code: 'usgs-earthquake', name: 'USGS 地震', status: 'planned', linked: false, enabled: null, adapterStatus: null, lastCollectedAt: null, validSignalCount: null},
  ],
  ...overrides,
});

const geopoliticalDimension = (): MonitoringDimension => ({
  id: 'geopolitical',
  name: '地缘政治与安全',
  icon: 'public',
  enabled: true,
  ruleId: 'geopolitical-v1',
  severityScores: {critical: 35, high: 28, medium: 20, low: 10},
  associationScores: {registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12, country: 8, industry: 12},
  thresholds: {p1: 85, p2: 65, p3: 40},
  matchColumns: ['entity', 'location', 'product'],
  eventTypes: ['geopolitical'],
  forcedRules: [
    {
      name: 'sanctions_entity_hit',
      description: '供应商主体直接命中制裁或合规事件',
      event_types: ['compliance', 'judicial'],
      event_subtypes: [],
      match_types: ['registry_no', 'legal_name', 'alias'],
      forced_level: 'P1',
      reason: '供应商主体直接命中制裁/合规事件，强制提升为 P1',
    },
  ],
  contentItems: ['制裁', '出口管制'],
  dataSources: [{code: 'ofac-sdn', name: 'OFAC SDN', status: 'connected', linked: true, enabled: true, adapterStatus: 'builtin', lastCollectedAt: '2026-09-01T08:00:00Z', validSignalCount: 5}],
});

const ruleEngineOptionsMock = (): RuleEngineOptions => ({
  match_columns: ['entity', 'location', 'product', 'country', 'industry'],
  event_types: [
    {value: 'weather', label: '天气'},
    {value: 'geological', label: '地质灾害'},
    {value: 'logistics', label: '物流'},
    {value: 'trade_policy', label: '贸易政策'},
    {value: 'geopolitical', label: '地缘政治'},
    {value: 'corporate', label: '企业经营'},
    {value: 'judicial', label: '司法'},
    {value: 'compliance', label: '合规'},
    {value: 'other', label: '其他'},
  ],
  event_subtypes: [],
});

const inputsOk = (): DimensionInputsRead => ({
  declared_total: 1, declared_linked: 1, declared_enabled: 1,
  observed: [], has_input: true,
});

const emptyTrace = (): DimensionTraceRead => ({
  available: false, event: null, routing: null, match: null, score: null, samples: [],
});

const globalConfigWith = (forcedRules: ForcedRuleRead[] = [], source: GlobalScoringConfigRead['source'] = forcedRules.length > 0 ? 'configured' : 'default'): GlobalScoringConfigRead => ({
  source,
  enabled: source === 'configured',
  effective: {
    severity_scores: {critical: 35, high: 28, medium: 20, low: 10},
    association_scores: {registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12, country: 8, industry: 12},
    p1_min: 85, p2_min: 65, p3_min: 40,
    forced_rules: forcedRules,
  },
  defaults: {forced_rules: forcedRules},
  shadowed_by: {},
  forced_rules_shadowed_by: [],
  dropped_dimension_rules: [],
});

const renderWithRouter = (ui: React.ReactElement) => render(<MemoryRouter>{ui}</MemoryRouter>);

// 配置态用例：admin 渲染后先切到配置态（viewer 不能切换，不能用 viewer 渲染配置态用例）
const renderAdminConfig = (
  dims: MonitoringDimension[],
  onUpdateDimension: (updated: MonitoringDimension) => Promise<void> = vi.fn().mockResolvedValue(undefined),
) => {
  const result = renderWithRouter(
    <RuleEngineView
      dimensions={dims}
      onToggleDimension={vi.fn()}
      onUpdateDimension={onUpdateDimension}
      role="admin"
    />,
  );
  fireEvent.click(screen.getByTestId('rule-engine-mode-toggle'));
  return {...result, onUpdateDimension};
};

beforeEach(() => {
  // 壳组件 hook 的四个信息源 + 信号过滤配置全部固定：任何用例都不走真实网络
  vi.spyOn(api, 'dimensionInputs').mockResolvedValue(inputsOk());
  vi.spyOn(api, 'ruleEngineOptions').mockResolvedValue(ruleEngineOptionsMock());
  vi.spyOn(api, 'dimensionTrace').mockResolvedValue(emptyTrace());
  vi.spyOn(api.globalConfig, 'get').mockResolvedValue(globalConfigWith());
  vi.spyOn(api.globalConfig, 'update').mockResolvedValue(globalConfigWith());
  vi.spyOn(api.globalConfig, 'reset').mockResolvedValue(globalConfigWith());
  vi.spyOn(api.filterConfig, 'get').mockResolvedValue({
    high_impact: [], priority_countries: [], list_sources: ['ofac-sdn'], source: 'default',
  });
});

afterEach(() => {
  cleanup();
  // Tab 切换会经 history.replaceState 把 '#global' 写进 window.location.hash；jsdom 的
  // location 跨用例共享，必须清理，避免后续用例首帧落在「全局规则」Tab。
  window.history.replaceState(null, '', '/');
});

describe('规则引擎引用信息源真实状态', () => {
  it('enabled 信源渲染真实状态且链接 href 正确', async () => {
    vi.spyOn(api, 'dimensionInputs').mockResolvedValue({
      declared_total: 2, declared_linked: 1, declared_enabled: 1,
      observed: [{code: 'nmc-weather', name: '中央气象台', signal_count: 12, latest_at: '2026-09-01T08:00:00Z'}],
      has_input: true,
    });
    renderWithRouter(
      <RuleEngineView
        dimensions={[dimension()]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    const sourceRow = screen.getByText('中央气象台').closest('div') as HTMLElement;
    expect(within(sourceRow).getByText('已启用')).toBeInTheDocument();
    expect(within(sourceRow).getByText(/12 条/)).toBeInTheDocument();
    const link = within(sourceRow).getByRole('link');
    expect(link).toHaveAttribute('href', '/sources/nmc-weather/signals?scope=valid&page=1');
  });

  it('disabled 信源不出现「已接入」字样', () => {
    const dim = dimension({
      dataSources: [
        {code: 'nmc-weather', name: '中央气象台', status: 'connected', linked: true, enabled: false, adapterStatus: 'builtin', lastCollectedAt: '2026-09-01T08:00:00Z', validSignalCount: 12},
      ],
    });
    vi.spyOn(api, 'dimensionInputs').mockResolvedValue({
      declared_total: 1, declared_linked: 1, declared_enabled: 0,
      observed: [], has_input: false,
    });
    renderWithRouter(
      <RuleEngineView
        dimensions={[dim]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    const sourceRow = screen.getByText('中央气象台').closest('div') as HTMLElement;
    expect(within(sourceRow).getByText('已停用')).toBeInTheDocument();
    expect(within(sourceRow).queryByText('已接入')).not.toBeInTheDocument();
  });

  it('壳层挂载：默认观察态渲染选中维度与模式开关（未接入信源折叠行为由 todo 9 单独覆盖）', () => {
    const dim = dimension({
      dataSources: [
        {code: 'usgs-earthquake', name: 'USGS 地震', status: 'planned', linked: false, enabled: null, adapterStatus: null, lastCollectedAt: null, validSignalCount: null},
      ],
    });
    renderWithRouter(
      <RuleEngineView
        dimensions={[dim]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    expect(screen.getByText('规则引擎与权重配置')).toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-mode-toggle')).toHaveTextContent('进入配置模式');
    expect(screen.getAllByText('自然环境').length).toBeGreaterThan(0);
    expect(screen.queryByTestId('rule-engine-config')).not.toBeInTheDocument();
  });

  it('has_input=false 时维度卡出现文本提示「当前无输入」', async () => {
    vi.spyOn(api, 'dimensionInputs').mockResolvedValue({
      declared_total: 1, declared_linked: 1, declared_enabled: 0,
      observed: [], has_input: false,
    });
    renderWithRouter(
      <RuleEngineView
        dimensions={[dimension()]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    expect((await screen.findAllByText('当前无输入')).length).toBeGreaterThan(0);
  });

  it('inputs 请求失败时观察态骨架仍渲染、错误以可访问方式提示', async () => {
    vi.spyOn(api, 'dimensionInputs').mockRejectedValue(new Error('网络错误'));
    renderWithRouter(
      <RuleEngineView
        dimensions={[dimension()]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    // 观察态骨架仍完整渲染（保留原意图：接口失败不得让页面整体消失）
    expect(screen.getByText('规则引擎与权重配置')).toBeInTheDocument();
    expect(screen.getAllByText('自然环境').length).toBeGreaterThan(0);
    expect(screen.getByTestId('rule-engine-observation')).toBeInTheDocument();
    // 错误以 role=alert 可访问地提示
    const alerts = await screen.findAllByRole('alert');
    expect(alerts.some((node) => /输入健康度加载失败/.test(node.textContent ?? ''))).toBe(true);
  });

  it('QA happy：壳层默认观察态，已接入信源保持可见', async () => {
    const dim = dimension({
      dataSources: [
        {code: 'nmc-weather', name: '中央气象台', status: 'connected', linked: true, enabled: true, adapterStatus: 'builtin', lastCollectedAt: '2026-09-01T08:00:00Z', validSignalCount: 12},
        {code: 'usgs-earthquake', name: 'USGS 地震', status: 'planned', linked: true, enabled: false, adapterStatus: 'builtin', lastCollectedAt: '2026-08-20T08:00:00Z', validSignalCount: 3},
        {code: 'cenc-earthquake', name: '中国地震台网', status: 'planned', linked: false, enabled: null, adapterStatus: null, lastCollectedAt: null, validSignalCount: null},
        {code: 'nhc-cdc', name: '国家卫健委', status: 'planned', linked: false, enabled: null, adapterStatus: null, lastCollectedAt: null, validSignalCount: null},
      ],
    });
    vi.spyOn(api, 'dimensionInputs').mockResolvedValue({
      declared_total: 4, declared_linked: 2, declared_enabled: 1,
      observed: [{code: 'nmc-weather', name: '中央气象台', signal_count: 12, latest_at: '2026-09-01T08:00:00Z'}],
      has_input: true,
    });
    renderWithRouter(
      <RuleEngineView
        dimensions={[dim]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    expect(screen.getByText('规则引擎与权重配置')).toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-observation')).toBeInTheDocument();
    // 已接入信源在观察态保持可见（未接入项的折叠汇总由 todo 9 的信源组件测试覆盖）
    expect((await screen.findAllByText('中央气象台')).length).toBeGreaterThan(0);
    expect(screen.getByText('引用信息源')).toBeInTheDocument();
  });
});

describe('规则引擎观察/配置双态：模式感知壳层挂载', () => {
  it('默认观察态：只读子组件挂载点出现，无配置面板与写控件', () => {
    renderWithRouter(
      <RuleEngineView
        dimensions={[geopoliticalDimension()]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="admin"
      />,
    );

    expect(screen.getByTestId('rule-engine-observation')).toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-config')).not.toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-mode-toggle')).toHaveAttribute('aria-pressed', 'false');

    // 观察态冻结挂载点：流水线 / 矩阵表 / 说明模块
    expect(screen.getByTestId('rule-engine-pipeline')).toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-rule-matrix')).toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-explainers')).toBeInTheDocument();

    // 观察态无写控件（配置面板未挂载）
    expect(screen.queryByRole('button', {name: '保存配置'})).not.toBeInTheDocument();
  });

  it('admin 切换到配置态：配置子组件挂载点与壳层关键文案出现', async () => {
    renderAdminConfig([geopoliticalDimension()]);

    expect(screen.getByTestId('rule-engine-config')).toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-observation')).not.toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-mode-toggle')).toHaveTextContent('返回观察模式');

    // 冻结的配置态子组件挂载点：评分编辑器 / 强制规则 / 匹配柱 / 事件类型
    expect(screen.getByTestId('rule-engine-scoring-editor')).toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-forced-rules')).toBeInTheDocument();
    // 「匹配柱/事件类型」标题限定在配置面板内查找：常驻但隐藏的全局面板里，
    // Explainers 说明卡有同名标题副本（jsdom 的文本查询不过滤 hidden，全局 getByText 会歧义）
    const configPanel = screen.getByTestId('rule-engine-config-panel');
    expect(within(configPanel).getByText('匹配柱')).toBeInTheDocument();
    expect(within(configPanel).getByText('事件类型')).toBeInTheDocument();

    // 壳层关键文案：配置面板标题、维度规则 ID、提醒失效只读说明。
    // 维度规则 ID 限定在配置面板内查找：维度头卡（rule-engine-dimension-header，两种模式常驻）
    // 也渲染同一枚 `ID: {ruleId}` 文本，全局 getByText 会歧义；断言意图（配置卡头部展示规则 ID）不变。
    // align-T 迁移：卡头标题按 demo 改为「维度级配置 · {name}」，限定在配置面板内断言。
    expect(within(configPanel).getByText('维度级配置 · 地缘政治与安全')).toBeInTheDocument();
    expect(within(configPanel).getByText('ID: geopolitical-v1')).toBeInTheDocument();
    expect(screen.getByText('提醒失效')).toBeInTheDocument();
    const link = screen.getByRole('link', {name: '信息源'});
    expect(link).toHaveAttribute('href', '/sources');

    // 配置态沙箱入口出现（写控件细节由各子组件测试与 shell.test 覆盖）。
    // #1 迁移：左栏维度列表及其中的沙箱开关已整体移除，全页只剩配置卡底这一个入口，
    // 断言仍在 rule-engine-config-panel 内，避免与其它文案混排。
    expect(within(configPanel).getByRole('button', {name: /沙箱测试/})).toBeInTheDocument();
    // 信号过滤区块已迁入全局面板（常驻、只切 hidden）：切到「全局规则」Tab 后断言；
    // align-T 迁移：embedded 模式去掉自身标题，改为断言全局层分节卡 summary 标题 + body 归属。
    fireEvent.click(screen.getByTestId('rule-engine-tab-global'));
    const filterSection = (await screen.findByTestId('signal-filter-field-keywords')).closest('section') as HTMLElement;
    const filterCard = filterSection.closest('details') as HTMLElement;
    expect(filterCard).not.toBeNull();
    expect(filterCard.querySelector('summary') as HTMLElement).toHaveTextContent('信号过滤规则');
    expect(filterCard).toContainElement(filterSection);
  });

  it('viewer 不能切换：按钮禁用、点击无效、始终停留在观察态', () => {
    renderWithRouter(
      <RuleEngineView
        dimensions={[geopoliticalDimension()]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    const toggle = screen.getByTestId('rule-engine-mode-toggle');
    expect(toggle).toBeDisabled();
    fireEvent.click(toggle);

    expect(screen.getByTestId('rule-engine-observation')).toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-config')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '保存配置'})).not.toBeInTheDocument();
    expect(screen.getByText('当前权限：只读')).toBeInTheDocument();
  });

  it('配置态强制规则挂载点出现，数据取自 GET /global-config 全局层', async () => {
    vi.spyOn(api.globalConfig, 'get').mockResolvedValue(globalConfigWith([
      {
        name: 'sanctions_entity_hit',
        description: '供应商主体直接命中制裁或合规事件',
        event_types: ['compliance', 'judicial'],
        event_subtypes: [],
        match_types: ['registry_no', 'legal_name', 'alias'],
        forced_level: 'P1',
        reason: '供应商主体直接命中制裁/合规事件，强制提升为 P1',
      },
    ]));
    renderAdminConfig([geopoliticalDimension()]);

    expect(await screen.findByTestId('rule-engine-forced-rules')).toBeInTheDocument();
    expect(api.globalConfig.get).toHaveBeenCalled();
    expect(screen.getByTestId('rule-engine-config')).toBeInTheDocument();
  });

  it('全局配置加载失败时页面不崩溃：配置骨架仍挂载且错误以可访问方式提示', async () => {
    vi.spyOn(api.globalConfig, 'get').mockRejectedValue(new Error('全局服务不可用'));
    renderAdminConfig([geopoliticalDimension()]);

    expect(screen.getByTestId('rule-engine-config')).toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-forced-rules')).toBeInTheDocument();
    const alerts = await screen.findAllByRole('alert');
    expect(alerts.length).toBeGreaterThan(0);
  });
});

describe('监控维度选择器：键盘与可访问语义（缺陷 D 回归）', () => {
  const renderTwoDimensions = () =>
    renderWithRouter(
      <RuleEngineView
        dimensions={[dimension(), geopoliticalDimension()]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

  it('维度选择项使用原生 button 语义，并暴露 aria-pressed 选中态与分组名', () => {
    renderTwoDimensions();

    const natural = screen.getByTestId('rule-engine-dimension-natural');
    const geopolitical = screen.getByTestId('rule-engine-dimension-geopolitical');

    expect(natural.tagName).toBe('BUTTON');
    expect(natural).toHaveAttribute('type', 'button');
    expect(natural).toHaveAttribute('aria-pressed', 'true');
    expect(geopolitical.tagName).toBe('BUTTON');
    expect(geopolitical).toHaveAttribute('type', 'button');
    expect(geopolitical).toHaveAttribute('aria-pressed', 'false');

    // 可编程聚焦（键盘可达的前提）；整组以「监控维度」名称暴露
    natural.focus();
    expect(natural).toHaveFocus();
    expect(screen.getByRole('group', {name: '监控维度'})).toContainElement(natural);
  });

  it('聚焦后按 Enter 即可切换选中维度（无需鼠标）', async () => {
    const user = userEvent.setup();
    renderTwoDimensions();

    const geopolitical = screen.getByTestId('rule-engine-dimension-geopolitical');
    geopolitical.focus();
    expect(geopolitical).toHaveFocus();

    await user.keyboard('{Enter}');

    await waitFor(() => expect(geopolitical).toHaveAttribute('aria-pressed', 'true'));
    expect(screen.getByTestId('rule-engine-dimension-natural')).toHaveAttribute('aria-pressed', 'false');
  });

  it('聚焦后按空格同样切换选中维度', async () => {
    const user = userEvent.setup();
    renderTwoDimensions();

    const geopolitical = screen.getByTestId('rule-engine-dimension-geopolitical');
    geopolitical.focus();

    await user.keyboard(' ');

    await waitFor(() => expect(geopolitical).toHaveAttribute('aria-pressed', 'true'));
    expect(screen.getByTestId('rule-engine-dimension-natural')).toHaveAttribute('aria-pressed', 'false');
  });
});

describe('规则引擎事件有效期控件移除', () => {
  it('TTL 输入框不存在', () => {
    renderAdminConfig([dimension()]);

    expect(screen.queryByText('事件有效期 (TTL)')).not.toBeInTheDocument();
    expect(screen.queryByText('小时')).not.toBeInTheDocument();
  });

  it('只读说明存在且链接指向 /sources', () => {
    renderAdminConfig([dimension()]);

    expect(screen.getByText('提醒失效')).toBeInTheDocument();
    expect(screen.getByText(/提醒失效由信号有效期策略决定/)).toBeInTheDocument();
    const link = screen.getByRole('link', {name: '信息源'});
    expect(link).toHaveAttribute('href', '/sources');
  });
});

describe('方案 B 布局与位置契约（#1/#16/#17/#18/#19/#33）', () => {
  /** 带两条真实样例的轨迹：验证选择器接驳唯一 selectedSampleId（无独立第二份状态）。 */
  const sampleTrace = (): DimensionTraceRead => ({
    available: true,
    event: {event_type: 'weather', event_subtype: null, severity: 'high', summary: '样例事件', confidence: 0.9, published_at: null, source_name: null},
    routing: {key: 'natural', label: '自然环境', match_columns: ['entity']},
    match: null,
    score: null,
    samples: [
      {id: 11, supplier_id: 1, supplier_name: '沿海科技', level: 'P2', event_summary: '沿岸强台风预警', updated_at: '2026-09-13T08:00:00Z'},
      {id: 12, supplier_id: 2, supplier_name: '北岭实业', level: 'P1', event_summary: '出口管制清单更新', updated_at: '2026-09-12T08:00:00Z'},
    ],
  });

  const renderViewer = (dims: MonitoringDimension[]) =>
    renderWithRouter(
      <RuleEngineView dimensions={dims} onToggleDimension={vi.fn()} onUpdateDimension={vi.fn()} role="viewer" />,
    );

  const evidenceOrder = () =>
    within(screen.getByTestId('rule-engine-scope-evidence'))
      .getAllByTestId(/^rule-engine-evidence-/)
      .map((node) => node.getAttribute('data-testid'));

  it('维度 chips 位于维度 tabpanel 内且先于维度头卡；切到全局 Tab 后随面板 hidden（#1）', () => {
    renderViewer([dimension(), geopoliticalDimension()]);

    const panel = screen.getByTestId('rule-engine-tabpanel-dimension');
    const chips = screen.getByTestId('rule-engine-dimension-chips');
    const naturalChip = screen.getByTestId('rule-engine-dimension-natural');
    const header = screen.getByTestId('rule-engine-dimension-header');

    expect(panel).toContainElement(chips);
    expect(chips).toContainElement(naturalChip);
    expect(screen.getByRole('group', {name: '监控维度'})).toBe(chips);
    expect(naturalChip.tagName).toBe('BUTTON');
    expect(naturalChip).toHaveAttribute('aria-pressed', 'true');
    expect(naturalChip.className).toContain('focus-visible:ring-2');
    // chips 在头卡之前（FOLLOWING 位证明头卡位于按钮之后）
    expect(naturalChip.compareDocumentPosition(header) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);

    fireEvent.click(screen.getByTestId('rule-engine-tab-global'));
    expect(panel).toHaveAttribute('hidden');
    expect(panel).toContainElement(naturalChip);
  });

  it('样例选择器位于证据卡内而非矩阵卡或外层壳（#17）', () => {
    renderViewer([dimension()]);

    const selector = screen.getByTestId('rule-engine-sample-selector');
    expect(screen.getByTestId('rule-engine-tabpanel-dimension')).toContainElement(selector);
    expect(selector.closest('[data-testid="rule-engine-scope-evidence"]')).not.toBeNull();
    expect(selector.closest('[data-testid="rule-engine-rule-matrix"]')).toBeNull();
    expect(within(screen.getByTestId('rule-engine-rule-matrix')).queryByTestId('rule-engine-sample-selector')).toBeNull();
  });

  it('观察态与配置态证据块顺序一致：信源 → 样例 → 运行轨迹（#19），且 1280px 宽屏网格为两列（#16）', () => {
    const expected = ['rule-engine-evidence-sources', 'rule-engine-evidence-sample', 'rule-engine-evidence-timeline'];

    renderViewer([geopoliticalDimension()]);
    expect(evidenceOrder()).toEqual(expected);
    // #16：证据网格在 xl 起两列、窄屏单列兜底；运行轨迹跨全宽
    const grid = screen.getByTestId('rule-engine-evidence-sources').parentElement as HTMLElement;
    expect(grid.className).toContain('grid-cols-1');
    expect(grid.className).toContain('xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]');
    expect(screen.getByTestId('rule-engine-evidence-timeline').className).toContain('xl:col-span-2');

    cleanup();
    renderAdminConfig([geopoliticalDimension()]);
    expect(screen.getByTestId('rule-engine-config')).toBeInTheDocument();
    expect(evidenceOrder()).toEqual(expected);
    const configGrid = screen.getByTestId('rule-engine-evidence-sources').parentElement as HTMLElement;
    expect(configGrid.className).toContain('xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]');
    expect(screen.getByTestId('rule-engine-evidence-timeline').className).toContain('xl:col-span-2');
  });

  it('运行轨迹以嵌入块形态位于证据卡内，不再是第二张独立整卡（#18）', async () => {
    renderViewer([geopoliticalDimension()]);

    const pipeline = screen.getByTestId('rule-engine-pipeline');
    expect(screen.getByTestId('rule-engine-evidence-timeline')).toContainElement(pipeline);
    expect(pipeline).toHaveAttribute('data-embedded', 'true');
    expect(pipeline.className).not.toContain('rounded-2xl');
    // 等待轨迹请求落地：嵌入块头（标题 + 「8 阶段」计数）出现；加载中分支同标题、无计数
    expect(await screen.findByText('8 阶段')).toBeInTheDocument();
    expect(screen.getByText('运行轨迹')).toBeInTheDocument();
  });

  it('配置态全页恰有一个沙箱测试入口，位于配置卡底并接线到 sandbox 面板', () => {
    const {container} = renderAdminConfig([geopoliticalDimension()]);

    const triggers = screen.getAllByRole('button', {name: /沙箱测试/});
    expect(triggers).toHaveLength(1);
    const trigger = triggers[0];
    expect(within(screen.getByTestId('rule-engine-config-panel')).getByRole('button', {name: /沙箱测试/})).toBe(trigger);
    expect(trigger).toHaveAttribute('aria-controls', 'rule-engine-sandbox');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(container.querySelector('#rule-engine-sandbox')).not.toBeNull();
  });

  it('样例选择器接驳唯一 selectedSampleId：点击样例后按该样例重取轨迹并驱动证据卡流水线（#17）', async () => {
    vi.spyOn(api, 'dimensionTrace').mockResolvedValue(sampleTrace());
    renderViewer([dimension()]);

    const chip = await screen.findByTestId('rule-matrix-sample-12');
    expect(chip).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(chip);
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(api.dimensionTrace).toHaveBeenLastCalledWith('natural', 12));
    // 同一状态源驱动证据卡内流水线：被选样例编号出现在嵌入块头
    expect(await screen.findByText('真实样本 #12')).toBeInTheDocument();
  });

  it('配置态：头卡启停开关仍接驳 onToggleDimension（左栏移除后行为不变）', () => {
    const onToggleDimension = vi.fn().mockResolvedValue(undefined);
    renderWithRouter(
      <RuleEngineView
        dimensions={[geopoliticalDimension()]}
        onToggleDimension={onToggleDimension}
        onUpdateDimension={vi.fn()}
        role="admin"
      />,
    );
    fireEvent.click(screen.getByTestId('rule-engine-mode-toggle'));

    fireEvent.click(screen.getByLabelText('地缘政治与安全 启用状态'));
    expect(onToggleDimension).toHaveBeenCalledWith('geopolitical');
  });

  it('信号过滤与样例选择 chips 使用 12px 圆角（#33）', async () => {
    vi.spyOn(api.filterConfig, 'get').mockResolvedValue({
      high_impact: ['cbam'], priority_countries: [], list_sources: ['ofac-sdn'], source: 'default',
    });
    renderAdminConfig([geopoliticalDimension()]);
    fireEvent.click(screen.getByTestId('rule-engine-tab-global'));

    const selectedContainer = await screen.findByTestId('signal-filter-keywords-selected');
    const selectedChip = within(selectedContainer).getByText('cbam');
    expect(selectedChip.className).toContain('rounded-xl');
    expect(selectedChip.className).not.toContain('rounded-md');

    const candidateChip = await screen.findByLabelText('加入 制裁');
    expect(candidateChip.className).toContain('rounded-xl');
    expect(candidateChip.className).not.toContain('rounded-md');

    const listSourceChip = screen.getByText('ofac-sdn');
    expect(listSourceChip.className).toContain('rounded-xl');
    expect(listSourceChip.className).not.toContain('rounded-md');

    // 样例选择 chips（证据卡内）同为 12px
    const sampleChip = screen.getByTestId('rule-matrix-sample-builtin');
    expect(sampleChip.className).toContain('rounded-xl');
    expect(sampleChip.className).not.toContain('rounded-md');
  });
});

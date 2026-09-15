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
  // 壳组件 hook 的四个数据源 + 信号过滤配置全部固定：任何用例都不走真实网络
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

afterEach(cleanup);

describe('规则引擎引用数据源真实状态', () => {
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
    expect(screen.getByText('引用数据源')).toBeInTheDocument();
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
    expect(screen.getByText('匹配柱')).toBeInTheDocument();
    expect(screen.getByText('事件类型')).toBeInTheDocument();

    // 壳层关键文案：配置面板标题、维度规则 ID、提醒失效只读说明
    expect(screen.getByText('地缘政治与安全 规则配置')).toBeInTheDocument();
    expect(screen.getByText('ID: geopolitical-v1')).toBeInTheDocument();
    expect(screen.getByText('提醒失效')).toBeInTheDocument();
    const link = screen.getByRole('link', {name: '数据源'});
    expect(link).toHaveAttribute('href', '/sources');

    // 配置态沙箱入口与信号过滤区块出现（写控件细节由各子组件测试与 shell.test 覆盖）
    expect(screen.getByRole('button', {name: /沙箱测试/})).toBeInTheDocument();
    expect(await screen.findByText('信号过滤规则')).toBeInTheDocument();
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
    const link = screen.getByRole('link', {name: '数据源'});
    expect(link).toHaveAttribute('href', '/sources');
  });
});

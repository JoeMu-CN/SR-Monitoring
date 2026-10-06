import React from 'react';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {MemoryRouter} from 'react-router-dom';
import {api, DimensionTraceRead, GlobalScoringConfigRead, RuleEngineOptions} from '../api';
import type {MonitoringDimension} from '../types';
import {RuleEngineView} from './RuleEngineView';

const naturalDimension = (): MonitoringDimension => ({
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
  dataSources: [],
});

const geopoliticalDimension = (): MonitoringDimension => ({
  ...naturalDimension(),
  id: 'geopolitical',
  name: '地缘政治与安全',
  icon: 'public',
  ruleId: 'geopolitical-v1',
  eventTypes: ['geopolitical'],
  associationScores: {registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12, country: 8, industry: 12},
  contentItems: ['制裁', '出口管制'],
});

const optionsMock = (): RuleEngineOptions => ({
  match_columns: ['entity', 'location', 'product', 'country', 'industry'],
  event_types: [
    {value: 'weather', label: '天气'},
    {value: 'geopolitical', label: '地缘政治'},
    {value: 'trade_policy', label: '贸易政策'},
  ],
  event_subtypes: [],
});

const traceFor = (key: string): DimensionTraceRead => ({
  available: true,
  event: {event_type: key === 'natural' ? 'weather' : 'geopolitical', event_subtype: null, severity: 'high', summary: `${key} 样例`, confidence: 0.9, published_at: null, source_name: null},
  routing: {key, label: key === 'natural' ? '自然环境' : '地缘政治与安全', match_columns: ['entity']},
  match: {match_type: 'legal_name', match_reasons: [], match_evidence: []},
  score: {total: 70, level: 'P2', detail: {}, level_cap: null, forced_rule: null},
  samples: [],
});

const globalConfigOk = (): GlobalScoringConfigRead => ({
  source: 'default',
  enabled: false,
  effective: {forced_rules: []},
  defaults: {forced_rules: []},
  shadowed_by: {},
  forced_rules_shadowed_by: [],
  dropped_dimension_rules: [],
});

const renderWithRouter = (ui: React.ReactElement) => render(<MemoryRouter>{ui}</MemoryRouter>);

beforeEach(() => {
  vi.spyOn(api, 'dimensionInputs').mockResolvedValue({declared_total: 0, declared_linked: 0, declared_enabled: 0, observed: [], has_input: false});
  vi.spyOn(api, 'ruleEngineOptions').mockResolvedValue(optionsMock());
  vi.spyOn(api, 'dimensionTrace').mockResolvedValue({available: false, event: null, routing: null, match: null, score: null, samples: []});
  vi.spyOn(api.globalConfig, 'get').mockResolvedValue(globalConfigOk());
  vi.spyOn(api.globalConfig, 'update').mockResolvedValue(globalConfigOk());
  vi.spyOn(api.globalConfig, 'reset').mockResolvedValue(globalConfigOk());
  vi.spyOn(api.filterConfig, 'get').mockResolvedValue({high_impact: ['cbam'], priority_countries: ['JP'], list_sources: ['ofac-sdn'], source: 'default'});
});

afterEach(() => {
  cleanup();
  // Tab 切换会经 history.replaceState 把 '#global' 写进 window.location.hash；jsdom 的
  // location 跨用例共享，必须清理，避免后续用例首帧落在「全局规则」Tab。
  window.history.replaceState(null, '', '/');
});

describe('规则引擎观察/配置双态壳层', () => {
  it('默认进入观察态：不渲染保存/输入类写控件', () => {
    renderWithRouter(
      <RuleEngineView dimensions={[geopoliticalDimension()]} onToggleDimension={vi.fn()} onUpdateDimension={vi.fn()} role="admin" />,
    );

    // 默认观察态
    expect(screen.getByTestId('rule-engine-observation')).toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-config')).not.toBeInTheDocument();
    const toggle = screen.getByTestId('rule-engine-mode-toggle');
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    expect(toggle).toHaveTextContent('进入配置模式');

    // 无任何写控件：无输入框、无勾选框、无保存按钮
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '保存配置'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '保存规则'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '重置默认'})).not.toBeInTheDocument();
  });

  it('admin 可切换到配置态：配置子组件挂载、写控件出现', async () => {
    renderWithRouter(
      <RuleEngineView dimensions={[geopoliticalDimension()]} onToggleDimension={vi.fn()} onUpdateDimension={vi.fn()} role="admin" />,
    );

    fireEvent.click(screen.getByTestId('rule-engine-mode-toggle'));

    expect(screen.getByTestId('rule-engine-config')).toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-observation')).not.toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-mode-toggle')).toHaveTextContent('返回观察模式');
    // 冻结的配置态子组件挂载点全部存在
    expect(screen.getByTestId('rule-engine-scoring-editor')).toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-forced-rules')).toBeInTheDocument();
    // 「匹配柱/事件类型」标题限定在配置面板内查找：常驻但隐藏的全局面板里，
    // Explainers 说明卡有同名标题副本（jsdom 的文本查询不过滤 hidden，全局 getByText 会歧义）
    const configPanel = screen.getByTestId('rule-engine-config-panel');
    expect(within(configPanel).getByText('匹配柱')).toBeInTheDocument();
    expect(within(configPanel).getByText('事件类型')).toBeInTheDocument();
    // 信号过滤区块已迁入全局面板（常驻、只切 hidden）：切到「全局规则」Tab 后再断言，
    // 配置态下其编辑输入框因此才在可见 DOM 中（role 查询忽略 hidden 面板）
    fireEvent.click(screen.getByTestId('rule-engine-tab-global'));
    const globalPanel = screen.getByTestId('rule-engine-tabpanel-global');
    const filterSection = (await screen.findByTestId('signal-filter-field-keywords')).closest('section') as HTMLElement;
    // align-T 迁移：SignalFilter embedded 后不再自带标题「信号过滤规则」，标题改由全局层
    // 分节卡 summary 承担。断言改为「分节卡存在 + summary 标题正确 + 分节 body 内含过滤字段
    // （section 归属该卡）」，语义等价于原「该区块内可读到其标题」。
    const filterCard = filterSection.closest('details') as HTMLElement;
    expect(filterCard).not.toBeNull();
    expect(filterCard.querySelector('summary') as HTMLElement).toHaveTextContent('信号过滤规则');
    expect(filterCard).toContainElement(filterSection);
    // 写控件可用（编辑输入框在全局面板内渲染）
    expect((await within(globalPanel).findAllByRole('textbox')).length).toBeGreaterThan(0);
  });

  it('viewer 的切换按钮禁用，点击后仍停留在观察态', () => {
    renderWithRouter(
      <RuleEngineView dimensions={[geopoliticalDimension()]} onToggleDimension={vi.fn()} onUpdateDimension={vi.fn()} role="viewer" />,
    );

    const toggle = screen.getByTestId('rule-engine-mode-toggle');
    expect(toggle).toBeDisabled();
    fireEvent.click(toggle);

    expect(screen.getByTestId('rule-engine-observation')).toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-config')).not.toBeInTheDocument();
    // viewer 观察态同样没有写控件
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('admin 切到配置态后被降级为 viewer：重渲染立即回到观察态，不残留配置写控件（权限/状态漂移回归）', () => {
    const dim = geopoliticalDimension();
    const {rerender} = renderWithRouter(
      <RuleEngineView dimensions={[dim]} onToggleDimension={vi.fn()} onUpdateDimension={vi.fn()} role="admin" />,
    );

    fireEvent.click(screen.getByTestId('rule-engine-mode-toggle'));
    expect(screen.getByTestId('rule-engine-config')).toBeInTheDocument();

    // 会话中途降级：role 变为 viewer，但 mode 状态仍停留在 config（陈旧状态）
    rerender(
      <MemoryRouter>
        <RuleEngineView dimensions={[dim]} onToggleDimension={vi.fn()} onUpdateDimension={vi.fn()} role="viewer" />
      </MemoryRouter>,
    );

    // 有效模式必须由当前角色派生：viewer 只能观察，配置写控件全部消失
    expect(screen.getByTestId('rule-engine-observation')).toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-config')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '保存配置'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: /沙箱测试/})).not.toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-mode-toggle')).toBeDisabled();
  });

  it('观察态下 SignalFilterSection 渲染只读说明：不存在 textbox', async () => {
    renderWithRouter(
      <RuleEngineView dimensions={[geopoliticalDimension()]} onToggleDimension={vi.fn()} onUpdateDimension={vi.fn()} role="admin" />,
    );

    // 等信号过滤配置加载完成后仍是只读形态
    expect(await screen.findByText('高影响关键词')).toBeInTheDocument();
    expect(screen.getByText('只读查看')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('删除 cbam')).not.toBeInTheDocument();
  });

  it('轨迹接口失败时观察态仍渲染骨架并以 role=alert 提示（failure QA）', async () => {
    vi.spyOn(api, 'dimensionTrace').mockRejectedValue(new Error('轨迹服务不可用'));
    renderWithRouter(
      <RuleEngineView dimensions={[geopoliticalDimension()]} onToggleDimension={vi.fn()} onUpdateDimension={vi.fn()} role="viewer" />,
    );

    // 骨架仍完整：页面标题与观察态容器都在
    expect(screen.getByText('规则引擎与权重配置')).toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-observation')).toBeInTheDocument();
    // 错误以 role=alert 可访问地提示，且不渲染写控件
    const alerts = await screen.findAllByRole('alert');
    expect(alerts.some((node) => /运行轨迹加载失败/.test(node.textContent ?? ''))).toBe(true);
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('切换维度后轨迹按新维度 key 重取，不残留旧维度数据（stale_state）', async () => {
    vi.spyOn(api, 'dimensionTrace').mockImplementation(async (key: string) => traceFor(key));
    renderWithRouter(
      <RuleEngineView dimensions={[naturalDimension(), geopoliticalDimension()]} onToggleDimension={vi.fn()} onUpdateDimension={vi.fn()} role="viewer" />,
    );

    await waitFor(() => expect(api.dimensionTrace).toHaveBeenCalledWith('natural', undefined));
    expect(await screen.findByText(/→ 自然环境/)).toBeInTheDocument();

    // 切换到第二个维度：重取轨迹，界面只显示新维度的路由
    fireEvent.click(screen.getByTestId('rule-engine-dimension-geopolitical'));

    await waitFor(() => expect(api.dimensionTrace).toHaveBeenCalledWith('geopolitical', undefined));
    expect(await screen.findByText(/→ 地缘政治与安全/)).toBeInTheDocument();
    expect(screen.queryByText(/→ 自然环境/)).not.toBeInTheDocument();
  });
});

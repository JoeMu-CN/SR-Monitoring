import React from 'react';
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {MemoryRouter} from 'react-router-dom';
import {
  api,
  DimensionInputsRead,
  DimensionTraceRead,
  GlobalScoringConfigRead,
  RuleEngineOptions,
} from '../api';
import type {MonitoringDimension} from '../types';
import {RuleEngineDimensionSources} from './RuleEngineDimensionSources';
import {RuleEngineView} from './RuleEngineView';

/**
 * todo 9 信息架构精简测试。
 *
 * 迁移自旧 `RuleEngineView.test.tsx:106-128`（linked=false 渲染「未创建」）与
 * `:165-204`（四种信源呈现互不相同、链接只在 linked 项出现）两个用例，改写为：
 * - 未接入信源默认不出现，且不再渲染「另有 N 个声明信源未接入」汇总行与「去信息源接入」链接；
 * - 零接入维度（declared_linked===0）显著标注「无已接入信源，当前不会产生提醒」；
 * - 零接入判定以 `api.dimensionInputs` 的声明计数为**权威口径**；`dimension.dataSources`
 *   快照仅在同名请求未返回（加载中/失败）时兜底，绝不覆盖接口值（快照与接口来自独立请求）；
 * - 输入健康度区块保留，接口失败仍渲染信源列表并给出可访问错误；
 * - 「具体监控内容」独立卡片移除，contentItems 改挂左栏维度项悬浮/详情（不丢信息）。
 */

type DimensionSourceFixture = MonitoringDimension['dataSources'][number];

const nmcSource: DimensionSourceFixture = {
  code: 'nmc-weather', name: '中央气象台', status: 'connected', linked: true, enabled: true,
  adapterStatus: 'builtin', lastCollectedAt: '2026-09-01T08:00:00Z', validSignalCount: 12,
};
const usgsSource: DimensionSourceFixture = {
  code: 'usgs-earthquake', name: 'USGS 地震', status: 'planned', linked: true, enabled: false,
  adapterStatus: 'builtin', lastCollectedAt: '2026-08-20T08:00:00Z', validSignalCount: 3,
};
const cencSource: DimensionSourceFixture = {
  code: 'cenc-earthquake', name: '中国地震台网', status: 'planned', linked: false, enabled: null,
  adapterStatus: null, lastCollectedAt: null, validSignalCount: null,
};
const nhcSource: DimensionSourceFixture = {
  code: 'nhc-cdc', name: '国家卫健委', status: 'planned', linked: false, enabled: null,
  adapterStatus: null, lastCollectedAt: null, validSignalCount: null,
};

const makeDimension = (overrides: Partial<MonitoringDimension> = {}): MonitoringDimension => ({
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
  ...overrides,
});

const inputs = (overrides: Partial<DimensionInputsRead> = {}): DimensionInputsRead => ({
  declared_total: 0,
  declared_linked: 0,
  declared_enabled: 0,
  observed: [],
  has_input: false,
  ...overrides,
});

const renderSources = (dimension: MonitoringDimension, inputsProp: DimensionInputsRead | null, inputsError = '') =>
  render(
    <MemoryRouter>
      <RuleEngineDimensionSources dimension={dimension} inputs={inputsProp} inputsError={inputsError} />
    </MemoryRouter>,
  );

const optionsMock = (): RuleEngineOptions => ({
  match_columns: ['entity', 'location', 'product', 'country', 'industry'],
  event_types: [
    {value: 'weather', label: '天气'},
    {value: 'geological', label: '地质灾害'},
  ],
  event_subtypes: [],
});

const emptyTrace = (): DimensionTraceRead => ({
  available: false, event: null, routing: null, match: null, score: null, samples: [],
});

const globalConfigDefault = (): GlobalScoringConfigRead => ({
  source: 'default',
  enabled: false,
  effective: {forced_rules: []},
  defaults: {forced_rules: []},
  shadowed_by: {},
  forced_rules_shadowed_by: [],
  dropped_dimension_rules: [],
});

beforeEach(() => {
  // 壳层用例需要固定规则引擎四路数据 + 信号过滤配置；信源组件用例不发起请求，mock 不影响其确定性
  vi.spyOn(api, 'dimensionInputs').mockResolvedValue(inputs());
  vi.spyOn(api, 'ruleEngineOptions').mockResolvedValue(optionsMock());
  vi.spyOn(api, 'dimensionTrace').mockResolvedValue(emptyTrace());
  vi.spyOn(api.globalConfig, 'get').mockResolvedValue(globalConfigDefault());
  vi.spyOn(api.filterConfig, 'get').mockResolvedValue({
    high_impact: [], priority_countries: [], list_sources: [], source: 'default',
  });
});

afterEach(cleanup);

describe('引用信息源：未接入信源完全隐藏且不再提供汇总入口（迁移旧 106-128 / 165-204 用例）', () => {
  it('未接入信源不出现，也没有「声明信源未接入」汇总行与「去信息源接入」链接（迁移自旧「未创建渲染」用例）', () => {
    const dim = makeDimension({dataSources: [cencSource]});
    renderSources(dim, inputs({declared_total: 1, declared_linked: 0}));

    // 旧行为是单行渲染「未创建」徽标；现在未接入项与接入入口都完全不存在
    expect(screen.queryByText('中国地震台网')).not.toBeInTheDocument();
    expect(screen.queryByText('未创建')).not.toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-unlinked-sources-summary')).not.toBeInTheDocument();
    expect(screen.queryByText(/声明信源未接入/)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', {name: '去信息源接入'})).not.toBeInTheDocument();

    // 零接入维度仍显著标注
    expect(screen.getByText('无已接入信源，当前不会产生提醒')).toBeInTheDocument();
  });

  it('四种声明信源：已接入两项按真实状态与深链渲染，未接入两项不出现在卡片中（迁移自旧「四种信源呈现」用例）', () => {
    const dim = makeDimension({dataSources: [nmcSource, usgsSource, cencSource, nhcSource]});
    renderSources(dim, inputs({declared_total: 4, declared_linked: 2, declared_enabled: 1}));

    // 已接入项：状态互不相同、保留原有信号列表链接
    const nmcRow = screen.getByText('中央气象台').closest('div') as HTMLElement;
    const usgsRow = screen.getByText('USGS 地震').closest('div') as HTMLElement;
    expect(within(nmcRow).getByText('已启用')).toBeInTheDocument();
    expect(within(nmcRow).getByText(/12 条/)).toBeInTheDocument();
    expect(within(usgsRow).getByText('已停用')).toBeInTheDocument();
    expect(within(usgsRow).getByText(/3 条/)).toBeInTheDocument();
    expect(within(nmcRow).getByRole('link')).toHaveAttribute('href', '/sources/nmc-weather/signals?scope=valid&page=1');
    expect(within(usgsRow).getByRole('link')).toHaveAttribute('href', '/sources/usgs-earthquake/signals?scope=valid&page=1');

    // 未接入项：完全隐藏（不是旧「未创建」行，也没有折叠汇总或接入链接）
    expect(screen.queryByText('中国地震台网')).not.toBeInTheDocument();
    expect(screen.queryByText('国家卫健委')).not.toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-unlinked-sources-summary')).not.toBeInTheDocument();
    expect(screen.queryByText('另有 2 个声明信源未接入')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', {name: '去信息源接入'})).not.toBeInTheDocument();

    // 有已接入信源的维度不出现零接入标注
    expect(screen.queryByText('无已接入信源，当前不会产生提醒')).not.toBeInTheDocument();
  });

  it('全部声明信源均已接入时只渲染已接入行，不出现任何未接入汇总', () => {
    renderSources(makeDimension({dataSources: [nmcSource]}), inputs({declared_total: 1, declared_linked: 1, declared_enabled: 1}));

    expect(screen.getByText('中央气象台')).toBeInTheDocument();
    expect(screen.queryByText(/声明信源未接入/)).not.toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-unlinked-sources-summary')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', {name: '去信息源接入'})).not.toBeInTheDocument();
  });

  it('declared_linked=0 的维度显著标注「无已接入信源，当前不会产生提醒」，且不渲染任何接入入口', () => {
    const dim = makeDimension({dataSources: [cencSource, nhcSource]});
    renderSources(dim, inputs({declared_total: 2, declared_linked: 0, observed: [], has_input: false}));

    const annotation = screen.getByTestId('rule-engine-no-linked-sources');
    expect(annotation).toHaveTextContent('无已接入信源，当前不会产生提醒');
    expect(screen.queryByText('另有 2 个声明信源未接入')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', {name: '去信息源接入'})).not.toBeInTheDocument();
    expect(screen.queryByRole('link', {name: /有效信号/})).not.toBeInTheDocument();
  });

  it('inputs 加载中（null）：有已接入项时不误标零接入，信源列表照常渲染', () => {
    const {container} = renderSources(makeDimension({dataSources: [nmcSource]}), null);

    expect(screen.getByText('中央气象台')).toBeInTheDocument();
    expect(screen.queryByText('无已接入信源，当前不会产生提醒')).not.toBeInTheDocument();
    expect(screen.getByText('输入健康度加载中…')).toBeInTheDocument();
    expect(container.querySelector('.animate-spin')).toBeNull();
  });
});

describe('信源行排版：空间不足时元数据组整体换行，不挤压中文名称产生单字孤行（视觉 QA 回归）', () => {
  it('行容器允许换行、名称采用 text-pretty 防孤字、元数据组整体不收缩，状态/时间/条数仍完整', () => {
    const longNameSource: DimensionSourceFixture = {
      code: 'usgs-earthquake', name: '美国地质调查局 USGS 地震', status: 'planned', linked: true, enabled: false,
      adapterStatus: 'builtin', lastCollectedAt: '2026-08-20T08:00:00Z', validSignalCount: 3,
    };
    renderSources(makeDimension({dataSources: [longNameSource]}), inputs({declared_total: 1, declared_linked: 1}));

    const nameLink = screen.getByRole('link', {name: /美国地质调查局 USGS 地震 有效信号 3 条/});
    const row = nameLink.closest('div') as HTMLElement;
    const meta = nameLink.nextElementSibling as HTMLElement;

    // 名称 + 元数据超过一行宽度时整行换行，元数据组整体落到下一行，而不是把中文名称压出单字孤行
    expect(row).toHaveClass('flex-wrap');
    expect(row.children).toHaveLength(2);
    // 名称自身的 CJK 防孤字策略：名称不得不换行时避免过短的末行
    expect(nameLink).toHaveClass('text-pretty');
    expect(nameLink).toHaveTextContent('美国地质调查局 USGS 地震');
    // 元数据组整体不收缩，状态、时间与条数保持完整可读
    expect(meta).toHaveClass('shrink-0');
    expect(within(meta).getByText('已停用')).toBeInTheDocument();
    expect(within(meta).getByTitle('最近采集时间')).toBeInTheDocument();
    expect(within(meta).getByText(/3 条/)).toBeInTheDocument();
  });
});

describe('权威计数：零接入标注取 api.dimensionInputs，快照仅作接口缺失时的兜底', () => {
  it('快照仍有 2 个已接入项但接口 declared_linked=0：零接入标注按接口触发（不因快照漏报）', () => {
    // 维度快照（props）与接口计数来自各自独立的请求；接口可用时接口是唯一权威口径。
    renderSources(
      makeDimension({dataSources: [nmcSource, usgsSource]}),
      inputs({declared_total: 2, declared_linked: 0, declared_enabled: 0}),
    );

    expect(screen.getByTestId('rule-engine-no-linked-sources')).toHaveTextContent('无已接入信源，当前不会产生提醒');
  });

  it('快照未接入项数为 0 但接口声明 2 个未接入：不出任何未接入汇总，只渲染已接入行', () => {
    renderSources(
      makeDimension({dataSources: [nmcSource, usgsSource]}),
      inputs({declared_total: 2, declared_linked: 0, declared_enabled: 0}),
    );

    // 未接入汇总已从卡片移除：无论接口声明多少未接入项，都不会出现汇总行或接入链接
    expect(screen.queryByTestId('rule-engine-unlinked-sources-summary')).not.toBeInTheDocument();
    expect(screen.queryByText('另有 2 个声明信源未接入')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', {name: '去信息源接入'})).not.toBeInTheDocument();
    // 已接入行不受影响
    expect(screen.getByText('中央气象台')).toBeInTheDocument();
    expect(screen.getByText('USGS 地震')).toBeInTheDocument();
  });

  it('快照 0 已接入 / 2 未接入但接口声明 3 个未接入：不出未接入汇总，零接入标注按接口已接入数判定', () => {
    // 接口 declared_linked=2（>0）→ 快照虽无已接入项也不应误标零接入；
    // 未接入汇总已移除，因此两种口径的计数都不再渲染任何行。
    renderSources(
      makeDimension({dataSources: [cencSource, nhcSource]}),
      inputs({declared_total: 5, declared_linked: 2, declared_enabled: 1}),
    );

    expect(screen.queryByText('另有 3 个声明信源未接入')).not.toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-unlinked-sources-summary')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', {name: '去信息源接入'})).not.toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-no-linked-sources')).not.toBeInTheDocument();
  });
});

describe('输入健康度区块保留（两态共用的只读组件）', () => {
  it('has_input=true 渲染近 30 天观测摘要与观测项', () => {
    renderSources(
      makeDimension({dataSources: [nmcSource, cencSource]}),
      inputs({
        declared_total: 2, declared_linked: 1, declared_enabled: 1,
        observed: [{code: 'nmc-weather', name: '中央气象台', signal_count: 12, latest_at: '2026-09-01T08:00:00Z'}],
        has_input: true,
      }),
    );

    expect(screen.getByText('近 30 天输入：1 个信源有信号')).toBeInTheDocument();
    const health = screen.getByTestId('rule-engine-input-health');
    expect(within(health).getByText('中央气象台')).toBeInTheDocument();
    expect(within(health).getByText(/12 条/)).toBeInTheDocument();
  });

  it('has_input=false 渲染「当前无输入」', () => {
    renderSources(makeDimension({dataSources: [nmcSource]}), inputs({declared_total: 1, declared_linked: 1}));

    expect(screen.getByText('当前无输入')).toBeInTheDocument();
  });

  it('dimensionInputs 失败时仍渲染已接入信源列表，并以 role=alert 给出可访问错误（malformed_input）', () => {
    renderSources(makeDimension({dataSources: [nmcSource, cencSource]}), null, 'HTTP 503：输入健康度服务不可用');

    // 已接入信源在失败态下照常渲染，不被错误吞掉；失败态也不再出现未接入汇总（兜底计数不再使用）
    expect(screen.getByText('中央气象台')).toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-unlinked-sources-summary')).not.toBeInTheDocument();
    expect(screen.queryByText(/声明信源未接入/)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', {name: '去信息源接入'})).not.toBeInTheDocument();
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('输入健康度加载失败');
    expect(alert).toHaveTextContent('HTTP 503');
  });

  it('只读：无任何写控件（button/textbox/checkbox），唯一链接是已接入信源的信号深链', () => {
    renderSources(makeDimension({dataSources: [nmcSource, cencSource]}), inputs({declared_total: 2, declared_linked: 1, declared_enabled: 1}));

    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    // 唯一链接是已接入信源的信号列表深链（接入入口已移除），为只读导航
    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute('href', '/sources/nmc-weather/signals?scope=valid&page=1');
  });
});

describe('信息精简（壳层）：「具体监控内容」卡片移除，contentItems 改挂维度 chip 悬浮提示', () => {
  it('观察态与配置态都不再出现「具体监控内容」标题；完整 contentItems 以 chip title 保留', () => {
    const dim = makeDimension({contentItems: ['地震', '台风', '海啸', '火山']});
    render(
      <MemoryRouter>
        <RuleEngineView
          dimensions={[dim]}
          onToggleDimension={vi.fn()}
          onUpdateDimension={vi.fn()}
          role="admin"
        />
      </MemoryRouter>,
    );

    // 观察态：原独立卡片标题不再出现
    expect(screen.queryByText('具体监控内容')).not.toBeInTheDocument();
    // #1 迁移：维度选择器改为 Tab 内 chips，完整清单（含原卡片才会显示的第 4 项）
    // 以 chip 的悬浮 title 保留，值逐字不变
    expect(screen.getByTestId('rule-engine-dimension-natural')).toHaveAttribute('title', '地震 · 台风 · 海啸 · 火山');
    // 信源区块为两态共用只读信息，照常渲染
    expect(screen.getByText('引用信息源')).toBeInTheDocument();

    // 配置态（原卡片所在位置）：同样不再出现，chip 悬浮提示仍可访问
    fireEvent.click(screen.getByTestId('rule-engine-mode-toggle'));
    expect(screen.getByTestId('rule-engine-config')).toBeInTheDocument();
    expect(screen.queryByText('具体监控内容')).not.toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-dimension-natural')).toHaveAttribute('title', '地震 · 台风 · 海啸 · 火山');
    expect(screen.getAllByText('引用信息源').length).toBeGreaterThan(0);
  });
});

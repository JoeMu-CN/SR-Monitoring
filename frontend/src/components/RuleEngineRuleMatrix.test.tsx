import React, {useState} from 'react';
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {DimensionTraceSampleRead, ForcedRuleRead, RuleEngineOptions} from '../api';
import type {MonitoringDimension} from '../types';
import {RuleEngineRuleMatrix} from './RuleEngineRuleMatrix';
import type {RuleEngineRuleMatrixProps} from './RuleEngineRuleMatrix';

afterEach(cleanup);

// —— 夹具：9 个事件类型（后端 _EVENT_TYPE_LABELS 的完整集合）——
const EVENT_TYPES: RuleEngineOptions['event_types'] = [
  {value: 'weather', label: '天气'},
  {value: 'geological', label: '地质灾害'},
  {value: 'logistics', label: '物流'},
  {value: 'trade_policy', label: '贸易政策'},
  {value: 'geopolitical', label: '地缘政治'},
  {value: 'corporate', label: '企业经营'},
  {value: 'judicial', label: '司法'},
  {value: 'compliance', label: '合规'},
  {value: 'other', label: '其他'},
];

const options = (): RuleEngineOptions => ({
  match_columns: ['entity', 'location', 'product', 'country', 'industry'],
  event_types: EVENT_TYPES.map((item) => ({...item})),
  event_subtypes: [],
});

const sanctionsRule: ForcedRuleRead = {
  name: 'sanctions_geopolitical_entity_hit',
  description: '地缘政治维度主体命中制裁',
  event_types: ['geopolitical'],
  event_subtypes: [],
  match_types: ['registry_no', 'legal_name', 'alias'],
  forced_level: 'P1',
  reason: '供应商主体直接命中制裁事件，强制提升为 P1',
};

const baseDimension = (overrides: Partial<MonitoringDimension>): MonitoringDimension => ({
  id: 'natural',
  name: '自然环境',
  icon: 'landscape',
  enabled: true,
  ruleId: 'natural-v1',
  severityScores: {critical: 35, high: 28, medium: 20, low: 10},
  associationScores: {registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12},
  thresholds: {p1: 85, p2: 65, p3: 40},
  matchColumns: ['entity', 'location', 'product'],
  eventTypes: ['weather', 'geological', 'logistics'],
  forcedRules: [],
  contentItems: [],
  dataSources: [
    {code: 'nmc-weather', name: '中央气象台', status: 'connected', linked: true, enabled: true, adapterStatus: 'builtin', lastCollectedAt: null, validSignalCount: 12},
    {code: 'usgs', name: 'USGS', status: 'planned', linked: false, enabled: null, adapterStatus: null, lastCollectedAt: null, validSignalCount: null},
  ],
  ...overrides,
});

/** 两个启用维度：自然环境（weather/geological/logistics）与地缘政治（geopolitical/trade_policy）。 */
const naturalDimension = () => baseDimension({});
const geopoliticalDimension = () =>
  baseDimension({
    id: 'geopolitical',
    name: '地缘政治与安全',
    ruleId: 'geopolitical-v1',
    eventTypes: ['geopolitical', 'trade_policy'],
    matchColumns: ['entity', 'location', 'product', 'country', 'industry'],
    forcedRules: [sanctionsRule],
    dataSources: [
      {code: 'ofac-sdn', name: 'OFAC SDN', status: 'connected', linked: true, enabled: true, adapterStatus: 'builtin', lastCollectedAt: null, validSignalCount: 3},
    ],
  });

/** 第三个维度：已停用，声明了 corporate/judicial/compliance/other，但不得接管任何行。 */
const disabledPolicyDimension = () =>
  baseDimension({
    id: 'policy',
    name: '政策法规',
    ruleId: 'policy-v1',
    enabled: false,
    eventTypes: ['corporate', 'judicial', 'compliance', 'other'],
    matchColumns: ['entity', 'country', 'industry'],
    dataSources: [],
  });

const samples: DimensionTraceSampleRead[] = [
  {id: 11, supplier_id: 1, supplier_name: '沿海科技', level: 'P2', event_summary: '沿岸强台风预警', updated_at: '2026-09-13T08:00:00Z'},
  {id: 12, supplier_id: 2, supplier_name: '北岭实业', level: 'P1', event_summary: '出口管制清单更新', updated_at: '2026-09-12T08:00:00Z'},
];

const defaultProps = (overrides: Partial<RuleEngineRuleMatrixProps> = {}): RuleEngineRuleMatrixProps => ({
  dimensions: [naturalDimension(), geopoliticalDimension(),
  ],
  options: options(),
  optionsError: '',
  samples: [],
  selectedSampleId: null,
  onSelectSample: vi.fn(),
  activeEventType: null,
  ...overrides,
});

const renderMatrix = (overrides: Partial<RuleEngineRuleMatrixProps> = {}) => {
  const props = defaultProps(overrides);
  return render(<RuleEngineRuleMatrix {...props} />);
};

const row = (eventType: string) => screen.getByTestId(`rule-matrix-row-${eventType}`);

describe('规则矩阵表：行=事件类型，列=接管维度与规则口径', () => {
  it('给定 9 个事件类型与两个启用维度：渲染 9 行，各自显示接管维度或「当前无启用维度接管」', () => {
    renderMatrix();

    expect(screen.getAllByTestId(/^rule-matrix-row-/)).toHaveLength(9);

    // 有启用维度接管的事件类型显示维度名
    expect(within(row('weather')).getByText('自然环境')).toBeInTheDocument();
    expect(within(row('geological')).getByText('自然环境')).toBeInTheDocument();
    expect(within(row('logistics')).getByText('自然环境')).toBeInTheDocument();
    expect(within(row('geopolitical')).getByText('地缘政治与安全')).toBeInTheDocument();
    expect(within(row('trade_policy')).getByText('地缘政治与安全')).toBeInTheDocument();

    // 无维度接管的事件类型明确标注
    for (const eventType of ['corporate', 'judicial', 'compliance', 'other']) {
      expect(within(row(eventType)).getByText('当前无启用维度接管')).toBeInTheDocument();
    }

    expect(screen.getByTestId('rule-matrix-summary')).toHaveTextContent(
      /共 9 个事件类型：5 个由启用维度接管，\s*4 个当前无接管/,
    );
  });

  it('已停用维度不接管事件类型：显示无接管且各规则列以「—」占位', () => {
    renderMatrix({
      dimensions: [naturalDimension(), geopoliticalDimension(), disabledPolicyDimension()],
    });

    // policy 维度虽然声明了 compliance，但 enabled=false，不得接管
    expect(within(row('compliance')).getByText('当前无启用维度接管')).toBeInTheDocument();
    expect(within(row('compliance')).queryByText('政策法规')).not.toBeInTheDocument();
    // 匹配柱/严重程度/关联类型/分级阈值/信源可用性 五列均以「—」占位（无接管则来源未知）
    expect(within(row('compliance')).getAllByText('—')).toHaveLength(5);
    expect(within(row('compliance')).getByText('无')).toBeInTheDocument();
  });

  it('信源可用性列反映接管维度口径：未声明信源 / 声明但均未接入', () => {
    renderMatrix({dimensions: [baseDimension({dataSources: []})]});
    expect(within(row('weather')).getByText('未声明信源')).toBeInTheDocument();
    cleanup();

    renderMatrix({
      dimensions: [
        baseDimension({
          dataSources: [
            {code: 'usgs', name: 'USGS', status: 'planned', linked: false, enabled: null, adapterStatus: null, lastCollectedAt: null, validSignalCount: null},
          ],
        }),
      ],
    });
    expect(within(row('weather')).getByText('无已接入信源')).toBeInTheDocument();
    expect(within(row('weather')).getByText('声明 1 个，均未接入')).toBeInTheDocument();
  });

  it('列覆盖匹配柱/严重程度分值/关联类型分值/分级阈值/强制规则/信源可用性', () => {
    renderMatrix();

    for (const header of ['事件类型', '接管维度', '启用匹配柱', '严重程度分值', '关联类型分值', '分级阈值', '相关强制规则', '信源可用性']) {
      expect(screen.getByRole('columnheader', {name: header})).toBeInTheDocument();
    }

    const weatherRow = row('weather');
    expect(within(weatherRow).getByText('主体、地点、产品')).toBeInTheDocument();
    // 严重程度四项与关联类型八项的真实分值来自维度配置
    const severityCell = within(weatherRow).getByTestId('rule-matrix-severity-scores');
    for (const pair of ['严重 35', '高 28', '中 20', '低 10']) {
      expect(severityCell).toHaveTextContent(pair);
    }
    const associationCell = within(weatherRow).getByTestId('rule-matrix-association-scores');
    for (const pair of ['注册号 30', '法人全称 25', '别名 25', '地点距离 20', '地点文本 20', '产品 12']) {
      expect(associationCell).toHaveTextContent(pair);
    }
    expect(within(weatherRow).getByText('≥85')).toBeInTheDocument();
    expect(within(weatherRow).getByText('≥65')).toBeInTheDocument();
    expect(within(weatherRow).getByText('≥40')).toBeInTheDocument();
    expect(within(weatherRow).getByText('无')).toBeInTheDocument();
    expect(within(weatherRow).getByText('已接入 1/2 个信源')).toBeInTheDocument();
    expect(within(weatherRow).getByText('有效信号 12')).toBeInTheDocument();

    const geopoliticalRow = row('geopolitical');
    const forcedRuleItem = within(geopoliticalRow).getByTestId('rule-matrix-forced-sanctions_geopolitical_entity_hit');
    expect(forcedRuleItem).toHaveTextContent('sanctions_geopolitical_entity_hit');
    expect(forcedRuleItem).toHaveTextContent('P1');
    expect(within(geopoliticalRow).getByText('已接入 1/1 个信源')).toBeInTheDocument();
  });

  it('行来自接口选项（数据驱动，不是静态伪数据）：选项变少时行数随之变化', () => {
    renderMatrix({
      options: {
        match_columns: ['entity'],
        event_types: [
          {value: 'weather', label: '天气'},
          {value: 'trade_policy', label: '贸易政策'},
        ],
        event_subtypes: [],
      },
    });

    expect(screen.getAllByTestId(/^rule-matrix-row-/)).toHaveLength(2);
    expect(screen.getByTestId('rule-matrix-row-weather')).toBeInTheDocument();
    expect(screen.getByTestId('rule-matrix-row-trade_policy')).toBeInTheDocument();
    expect(screen.queryByTestId('rule-matrix-row-geopolitical')).not.toBeInTheDocument();
  });
});

describe('样例事件选择器：真实样例驱动矩阵高亮', () => {
  /** 模拟壳组件：选择样例会重新加载轨迹，再把新的事件类型通过 activeEventType 回传。 */
  const Harness = ({
    onSelect,
    traceBySample,
  }: {
    onSelect: (sampleId: number | null) => void;
    traceBySample: Record<number, string>;
  }) => {
    const [selectedSampleId, setSelectedSampleId] = useState<number | null>(null);
    const activeEventType = selectedSampleId === null ? 'weather' : traceBySample[selectedSampleId] ?? null;
    return (
      <RuleEngineRuleMatrix
        dimensions={[naturalDimension(), geopoliticalDimension()]}
        options={options()}
        optionsError=""
        samples={samples}
        selectedSampleId={selectedSampleId}
        onSelectSample={(sampleId) => {
          onSelect(sampleId);
          setSelectedSampleId(sampleId);
        }}
        activeEventType={activeEventType}
      />
    );
  };

  it('切换样例后高亮的事件类型随之变化（行 data-active 与选中态同步）', () => {
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} traceBySample={{11: 'weather', 12: 'geopolitical'}} />);

    // 真实样例存在时不出现内置样例兜底
    expect(screen.queryByTestId('rule-matrix-sample-builtin')).not.toBeInTheDocument();
    // 默认「最近一条」样例：天气行高亮
    expect(screen.getByTestId('rule-matrix-sample-latest')).toHaveAttribute('aria-pressed', 'true');
    expect(row('weather')).toHaveAttribute('data-active', 'true');
    expect(within(row('weather')).getByText('当前样例')).toBeInTheDocument();
    expect(row('geopolitical')).toHaveAttribute('data-active', 'false');

    // 切换到第二条真实样例：高亮移动到地缘政治行
    fireEvent.click(screen.getByTestId('rule-matrix-sample-12'));
    expect(onSelect).toHaveBeenCalledWith(12);
    expect(screen.getByTestId('rule-matrix-sample-12')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('rule-matrix-sample-latest')).toHaveAttribute('aria-pressed', 'false');
    expect(row('geopolitical')).toHaveAttribute('data-active', 'true');
    expect(within(row('geopolitical')).getByText('当前样例')).toBeInTheDocument();
    expect(row('weather')).toHaveAttribute('data-active', 'false');
    expect(screen.getByText(/已选样例：北岭实业 · P1 · 出口管制清单更新/)).toBeInTheDocument();

    // 切回第一条：高亮回到天气行
    fireEvent.click(screen.getByTestId('rule-matrix-sample-11'));
    expect(onSelect).toHaveBeenLastCalledWith(11);
    expect(row('weather')).toHaveAttribute('data-active', 'true');
    expect(row('geopolitical')).toHaveAttribute('data-active', 'false');

    // 切回「最近一条」：回传 null
    fireEvent.click(screen.getByTestId('rule-matrix-sample-latest'));
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });

  it('无真实样例时使用内置样例事件（沙箱字段结构）兜底并显式标注非真实数据', () => {
    const onSelect = vi.fn();
    renderMatrix({samples: [], activeEventType: null, onSelectSample: onSelect});

    const builtinChip = screen.getByTestId('rule-matrix-sample-builtin');
    expect(builtinChip).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByTestId('rule-matrix-sample-latest')).not.toBeInTheDocument();
    // 内置样例的默认事件类型是选项中的第一个（天气），并显式说明非真实数据
    expect(screen.getByText(/非真实数据/)).toBeInTheDocument();
    expect(screen.getByText(/事件类型「天气」/)).toBeInTheDocument();
    expect(row('weather')).toHaveAttribute('data-active', 'true');
    expect(within(row('weather')).getByText('内置样例')).toBeInTheDocument();
    expect(row('geopolitical')).toHaveAttribute('data-active', 'false');

    fireEvent.click(builtinChip);
    expect(onSelect).toHaveBeenCalledWith(null);
  });
});

describe('窄屏滚动与失败降级', () => {
  it('表格容器在窄屏可横向滚动且只有一层滚动容器（不嵌套）', () => {
    renderMatrix();

    const scroll = screen.getByTestId('rule-engine-rule-matrix-scroll');
    expect(scroll.className).toContain('overflow-x-auto');
    // 滚动容器内部不得再有滚动容器
    expect(scroll.querySelectorAll('.overflow-x-auto')).toHaveLength(0);
    // 整个矩阵区块只有这一个横向滚动容器
    expect(screen.getByTestId('rule-engine-rule-matrix').querySelectorAll('.overflow-x-auto')).toHaveLength(1);
    // 表格有最小宽度以触发横向滚动
    expect(screen.getByRole('table').className).toContain('min-w-[1080px]');
  });

  it('选项接口失败时降级为可访问错误提示（role=alert），不渲染表格也不崩溃', () => {
    renderMatrix({
      options: {match_columns: [], event_types: [], event_subtypes: []},
      optionsError: 'HTTP 503：规则选项服务不可用',
      samples,
    });

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('规则矩阵表加载失败');
    expect(alert).toHaveTextContent('HTTP 503');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-rule-matrix')).toBeInTheDocument();
    // 真实样例选择器仍可用（降级不丢功能）
    expect(screen.getByTestId('rule-matrix-sample-11')).toBeInTheDocument();
  });
});

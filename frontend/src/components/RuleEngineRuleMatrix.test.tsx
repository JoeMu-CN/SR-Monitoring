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
  // 关联分值夹具覆盖 6 组中的 4 组；alias/site_text 刻意低于组内主键，用于证明「组内取最高计入」
  associationScores: {registry_no: 30, legal_name: 25, alias: 18, site_distance: 20, site_text: 16, product: 12},
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

/**
 * 数据行 testid 精确匹配：排除同前缀的 `rule-matrix-row-toggle`（展开按钮）与
 * `rule-matrix-row-detail`（行内详情容器，折叠态仍在 DOM），避免前缀碰撞。
 */
const DATA_ROW_TESTID = /^rule-matrix-row-(?!toggle$|detail$).+$/;
const dataRows = () => screen.getAllByTestId(DATA_ROW_TESTID);

/** 行内详情行以 id（`rule-matrix-row-detail-${value}`）标识而非 testid；按事件类型精确定位。 */
const rowDetail = (eventType: string): HTMLElement => {
  const element = document.getElementById(`rule-matrix-row-detail-${eventType}`);
  if (!(element instanceof HTMLElement)) throw new Error(`未找到行内详情：rule-matrix-row-detail-${eventType}`);
  return element;
};

describe('规则矩阵表：行=事件类型，列=接管维度与规则口径', () => {
  it('给定 9 个事件类型与两个启用维度：默认只渲染 5 个接管行，4 个无接管折叠进汇总行', () => {
    renderMatrix();

    // 数据行 testid 精确匹配（排除 toggle/detail 同前缀）；无接管类型默认折叠，不渲染数据行
    expect(dataRows()).toHaveLength(5);
    for (const eventType of ['corporate', 'judicial', 'compliance', 'other']) {
      expect(screen.queryByTestId(`rule-matrix-row-${eventType}`)).not.toBeInTheDocument();
    }
    const unownedSummary = screen.getByTestId('rule-matrix-unowned-summary');
    expect(unownedSummary).toHaveAttribute('aria-expanded', 'false');
    expect(unownedSummary).toHaveTextContent(
      '另有 4 个事件类型当前无启用维度接管：企业经营、司法、合规、其他',
    );

    // 有启用维度接管的事件类型显示维度名
    expect(within(row('weather')).getByText('自然环境')).toBeInTheDocument();
    expect(within(row('geological')).getByText('自然环境')).toBeInTheDocument();
    expect(within(row('logistics')).getByText('自然环境')).toBeInTheDocument();
    expect(within(row('geopolitical')).getByText('地缘政治与安全')).toBeInTheDocument();
    expect(within(row('trade_policy')).getByText('地缘政治与安全')).toBeInTheDocument();

    // 展开汇总行后，无接管事件类型渲染数据行并明确标注
    fireEvent.click(within(unownedSummary).getByRole('button'));
    expect(screen.getByTestId('rule-matrix-unowned-summary')).toHaveAttribute('aria-expanded', 'true');
    for (const eventType of ['corporate', 'judicial', 'compliance', 'other']) {
      expect(within(row(eventType)).getByText('当前无启用维度接管')).toBeInTheDocument();
    }
    expect(dataRows()).toHaveLength(9);

    expect(screen.getByTestId('rule-matrix-summary')).toHaveTextContent(
      /共 9 个事件类型：5 个由启用维度接管，\s*4 个当前无接管/,
    );
  });

  it('已停用维度不接管事件类型：展开汇总行后显示无接管且各规则列以「—」占位', () => {
    renderMatrix({
      dimensions: [naturalDimension(), geopoliticalDimension(), disabledPolicyDimension()],
    });

    // policy 维度虽然声明了 compliance，但 enabled=false，不得接管；无接管行默认折叠在汇总行内
    const unownedSummary = screen.getByTestId('rule-matrix-unowned-summary');
    expect(screen.queryByTestId('rule-matrix-row-compliance')).not.toBeInTheDocument();
    fireEvent.click(within(unownedSummary).getByRole('button'));

    expect(within(row('compliance')).getByText('当前无启用维度接管')).toBeInTheDocument();
    expect(within(row('compliance')).queryByText('政策法规')).not.toBeInTheDocument();
    // 数据行内：分值摘要/信源可用性 两列以「—」占位（无接管则来源未知）
    expect(within(row('compliance')).getAllByText('—')).toHaveLength(2);
    // 行内详情（默认折叠）内：匹配柱/严重程度分值/关联类型分值/分级阈值 均以「—」占位，强制规则为「无」
    const complianceDetail = rowDetail('compliance');
    expect(within(complianceDetail).getAllByText('—')).toHaveLength(4);
    expect(within(complianceDetail).getByText('无')).toBeInTheDocument();
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
    // align-T 迁移：表层文案由「声明 1 个，均未接入」改为「0/1 已接入」+ 灰点状态。
    // 「0/1 已接入」等价承载「声明 1 个、均未接入」的事实（接入数 0 / 声明数 1），
    // 状态语义由上一行的「无已接入信源」承担；数据口径（summarizeSources）未变。
    expect(within(row('weather')).getByText('0/1 已接入')).toBeInTheDocument();
  });

  it('列覆盖分值摘要/信源可用性，其余口径在可展开的行内详情中', () => {
    renderMatrix();

    // 表头为 5 列新结构
    for (const header of ['事件类型', '接管维度', '分值摘要', '信源可用性', '展开']) {
      expect(screen.getByRole('columnheader', {name: header})).toBeInTheDocument();
    }
    // 已移出表头的旧列不得再充当列头
    for (const removed of ['启用匹配柱', '严重程度分值', '关联类型分值', '分级阈值', '相关强制规则']) {
      expect(screen.queryByRole('columnheader', {name: removed})).not.toBeInTheDocument();
    }

    const weatherRow = row('weather');
    // 分值摘要列：严重程度四项与关联类型最高分的真实分值来自维度配置
    const scoreSummary = within(weatherRow).getByTestId('rule-matrix-score-summary');
    expect(scoreSummary).toHaveTextContent('严重 35·28·20·10');
    expect(scoreSummary).toHaveTextContent('关联最高 30');
    // 信源可用性列仍在表层单元格：新形态为「N/M 已接入」（mono 主文案）+ 圆点状态。
    // align-T 迁移：具体有效信号数（12）已不在表层与行内详情中渲染，等价信息由
    // 「有信号」状态点承载（validSignalCount>0 → 品牌色圆点），故以状态语义断言替代，
    // 并核对圆点确为「信号存在」的品牌色形态而非灰点。
    expect(within(weatherRow).getByText('1/2 已接入')).toBeInTheDocument();
    const weatherSignalState = within(weatherRow).getByText('有信号');
    expect(weatherSignalState.querySelector('span[aria-hidden="true"]')).toHaveClass('bg-[#007aff]');

    // 行内详情默认折叠（hidden）不可见；点击该行「展开」后才可见
    expect(rowDetail('weather')).not.toBeVisible();
    fireEvent.click(within(weatherRow).getByTestId('rule-matrix-row-toggle'));
    expect(rowDetail('weather')).toBeVisible();

    // 展开后从详情内断言被移除列的完整口径
    const weatherDetail = within(rowDetail('weather'));
    expect(weatherDetail.getByText('主体、地点、产品')).toBeInTheDocument();
    const severityCell = weatherDetail.getByTestId('rule-matrix-severity-scores');
    for (const pair of ['严重 35', '高 28', '中 20', '低 10']) {
      expect(severityCell).toHaveTextContent(pair);
    }
    // 关联分值按原型收敛为 6 项分组（组内取最高计入）：法人全称/别名取 25、地点取 20，
    // 未配置的行业/国家以「—」占位
    expect(weatherDetail.getByText('关联分值（6 项，取最高计入）')).toBeInTheDocument();
    const associationCell = weatherDetail.getByTestId('rule-matrix-association-scores');
    for (const pair of ['注册号 30', '法人全称 / 别名 25', '地点 20', '产品 12', '行业 —', '国家 —']) {
      expect(associationCell).toHaveTextContent(pair);
    }
    // 6 项目录恰好 6 个子项；被合并的旧独立标签不再作为单项出现
    expect(associationCell.querySelectorAll(':scope > span')).toHaveLength(6);
    expect(associationCell).not.toHaveTextContent('地点距离');
    expect(associationCell).not.toHaveTextContent('地点文本');
    expect(weatherDetail.getByText('≥85')).toBeInTheDocument();
    expect(weatherDetail.getByText('≥65')).toBeInTheDocument();
    expect(weatherDetail.getByText('≥40')).toBeInTheDocument();
    expect(weatherDetail.getByText('无')).toBeInTheDocument();

    // 地缘政治行的行内详情承载相关强制规则
    const geopoliticalRow = row('geopolitical');
    fireEvent.click(within(geopoliticalRow).getByTestId('rule-matrix-row-toggle'));
    expect(rowDetail('geopolitical')).toBeVisible();
    const forcedRuleItem = within(rowDetail('geopolitical')).getByTestId('rule-matrix-forced-sanctions_geopolitical_entity_hit');
    expect(forcedRuleItem).toHaveTextContent('sanctions_geopolitical_entity_hit');
    expect(forcedRuleItem).toHaveTextContent('P1');
    expect(within(geopoliticalRow).getByText('1/1 已接入')).toBeInTheDocument();
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

    // 数据行 testid 精确匹配（排除 toggle/detail 同前缀）；两个选项都由启用维度接管
    expect(dataRows()).toHaveLength(2);
    expect(screen.getByTestId('rule-matrix-row-weather')).toBeInTheDocument();
    expect(screen.getByTestId('rule-matrix-row-trade_policy')).toBeInTheDocument();
    expect(screen.queryByTestId('rule-matrix-row-geopolitical')).not.toBeInTheDocument();
    // 两个选项均有接管维度，不渲染无接管汇总行
    expect(screen.queryByTestId('rule-matrix-unowned-summary')).not.toBeInTheDocument();
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
    // 表格自适应铺满、不设强制最小宽度（min-w-* 已移除），横向滚动只由唯一容器兜底
    const table = screen.getByRole('table');
    expect(table.className).toContain('w-full');
    expect(table.className).not.toContain('min-w-');
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

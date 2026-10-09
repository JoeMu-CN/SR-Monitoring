import React from 'react';
import {cleanup, render, screen, within} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {DimensionInputsRead, DimensionTraceRead} from '../api';
import type {MonitoringDimension} from '../types';
import {RuleEnginePipeline} from './RuleEnginePipeline';

/**
 * todo 5 验收用例（观察态规则流水线）：
 * - 七阶段标签、命中匹配柱/封顶/强制规则高亮、无数据引导、窄屏结构、深色类名、减少动效；
 * - 对抗类：malformed_input（缺字段/空轨迹不崩溃并降级）、misleading_success_output
 *   （数值断言直接读 DOM 的 data-value，不依赖组件自述）；
 * - 修复轮次（独立评审 3 缺陷）改用后端真实 `MatchType`（registry_no/site_text 等，
 *   见 `backend/app/risks/workbench_schemas.py:16-26`）验证 `MatchType→MatchColumn` 映射；
 *   缺字段时必须渲染 `—`、隐藏总分标记、阈值缺失显示"阈值未提供"，只有显式 0 才显示 0。
 *
 * 动效断言通过 mock `useReducedMotion` 控制：motion 的 hook 在 jsdom 中依环境返回，
 * 直接 mock 才能确定性覆盖"true→不做位移动画"与"false→允许入场动画"两个方向。
 */
const motionControl = vi.hoisted(() => ({reduced: false}));

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  return {
    ...actual,
    useReducedMotion: () => motionControl.reduced,
  };
});

const dimension = (overrides: Partial<MonitoringDimension> = {}): MonitoringDimension => ({
  id: 'geopolitical',
  name: '地缘政治与安全',
  icon: 'public',
  enabled: true,
  ruleId: 'geopolitical-v1',
  severityScores: {critical: 35, high: 28, medium: 20, low: 10},
  associationScores: {registry_no: 30, legal_name: 25, alias: 25, country: 8},
  thresholds: {p1: 85, p2: 65, p3: 40},
  matchColumns: ['entity', 'location', 'product', 'country', 'industry'],
  eventTypes: ['geopolitical'],
  forcedRules: [],
  contentItems: ['制裁', '出口管制'],
  dataSources: [
    {
      code: 'ofac-sdn',
      name: 'OFAC SDN',
      status: 'connected',
      linked: true,
      enabled: true,
      adapterStatus: 'builtin',
      lastCollectedAt: '2026-09-13T10:00:00Z',
      validSignalCount: 5,
    },
    {
      code: 'planned-list',
      name: '规划中的清单',
      status: 'planned',
      linked: false,
      enabled: null,
      adapterStatus: null,
      lastCollectedAt: null,
      validSignalCount: null,
    },
  ],
  ...overrides,
});

const inputs = (overrides: Partial<DimensionInputsRead> = {}): DimensionInputsRead => ({
  declared_total: 2,
  declared_linked: 1,
  declared_enabled: 1,
  observed: [{code: 'ofac-sdn', name: 'OFAC SDN', signal_count: 12, latest_at: '2026-09-13T10:00:00Z'}],
  has_input: true,
  ...overrides,
});

/**
 * 与后端真实语义一致的轨迹 fixture：
 * compute_score 合计 93（severity 35 + association 30 + credibility 18 + timeliness 5 + product 5）
 * → 命中 weak_association_max_p2 封顶（P1→P2）→ 命中 sanctions_entity_hit 强制规则（P2→P1、满分 100）。
 */
const trace = (overrides: Partial<DimensionTraceRead> = {}): DimensionTraceRead => ({
  available: true,
  event: {
    event_type: 'geopolitical',
    event_subtype: 'sanctions',
    severity: 'high',
    summary: '某国新增对某行业的制裁清单',
    confidence: 0.92,
    published_at: '2026-09-13T10:00:00Z',
    source_name: 'OFAC SDN',
  },
  routing: {
    key: 'geopolitical',
    label: '地缘政治与安全',
    match_columns: ['entity', 'location', 'product', 'country', 'industry'],
  },
  match: {
    // 真实后端取值：`MatchType` 枚举（registry_no/site_text/...），不是匹配柱名（entity/location/...）。
    match_type: 'registry_no+country',
    match_reasons: ['注册编号精确命中供应商', '事件国家与生产地点所在国家一致'],
    match_evidence: [
      {object_type: 'supplier', supplier_id: 1, registry_no: '91310000MA1K1234XX'},
      {object_type: 'country', country_code: 'US'},
    ],
  },
  score: {
    total: 100,
    level: 'P1',
    detail: {
      severity: 35,
      association: 30,
      source_credibility: 18,
      timeliness: 5,
      product_relevance: 5,
      level_cap: 'weak_association_max_p2',
      forced_rule: {
        name: 'sanctions_entity_hit',
        description: '供应商主体直接命中制裁或合规事件',
        reason: '注册编号命中制裁清单，强制提升为 P1',
        original_level: 'P2',
        original_score: 93,
      },
    },
    level_cap: 'weak_association_max_p2',
    forced_rule: {
      name: 'sanctions_entity_hit',
      description: '供应商主体直接命中制裁或合规事件',
      reason: '注册编号命中制裁清单，强制提升为 P1',
      original_level: 'P2',
      original_score: 93,
    },
  },
  samples: [],
  ...overrides,
});

const emptyTrace = (): DimensionTraceRead => ({
  available: false,
  event: null,
  routing: null,
  match: null,
  score: null,
  samples: [],
});

const STAGE_LABELS = [
  '① 信号输入',
  '② 事件路由',
  '③ 匹配柱',
  '④ 得分构成',
  '⑤ 总分层',
  '⑥ 封顶与强制规则',
  '⑦ 输出等级',
];

const renderPipeline = (
  options: {
    trace?: DimensionTraceRead | null;
    traceError?: string;
    inputs?: DimensionInputsRead | null;
    inputsError?: string;
    dimension?: MonitoringDimension;
    selectedSampleId?: number | null;
    embedded?: boolean;
  } = {},
) =>
  render(
    <RuleEnginePipeline
      dimension={options.dimension ?? dimension()}
      trace={options.trace === undefined ? trace() : options.trace}
      traceError={options.traceError ?? ''}
      inputs={options.inputs === undefined ? inputs() : options.inputs}
      inputsError={options.inputsError ?? ''}
      selectedSampleId={options.selectedSampleId ?? null}
      embedded={options.embedded}
    />,
  );

afterEach(() => {
  cleanup();
  motionControl.reduced = false;
});

describe('规则引擎观察态流水线：七阶段渲染', () => {
  it('真实轨迹下七个阶段标签与阶段数值全部渲染', () => {
    renderPipeline();

    for (const label of STAGE_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // ② 事件路由：事件类型（中文标签）→ 接管维度
    expect(screen.getByText('地缘政治 → 地缘政治与安全')).toBeInTheDocument();
    expect(screen.getByText(/事件类型代码：geopolitical \/ 制裁/)).toBeInTheDocument();
    // ④ 得分构成：五项分值直接读 DOM（不依赖组件自述）
    expect(screen.getByTestId('rule-engine-pipeline-composition-severity')).toHaveAttribute('data-value', '35');
    expect(screen.getByTestId('rule-engine-pipeline-composition-association')).toHaveAttribute('data-value', '30');
    expect(screen.getByTestId('rule-engine-pipeline-composition-source_credibility')).toHaveAttribute('data-value', '18');
    expect(screen.getByTestId('rule-engine-pipeline-composition-timeliness')).toHaveAttribute('data-value', '5');
    expect(screen.getByTestId('rule-engine-pipeline-composition-product_relevance')).toHaveAttribute('data-value', '5');
    expect(screen.getByText(/五项合计 93 分；/)).toBeInTheDocument();
    // ⑤ 总分层：总分 100 与 P1/P2/P3 刻度
    const gauge = screen.getByTestId('rule-engine-pipeline-gauge');
    expect(within(gauge).getByText('100 分')).toBeInTheDocument();
    expect(screen.getByText('等级刻度：P3 ≥ 40 · P2 ≥ 65 · P1 ≥ 85（满分 100）')).toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-pipeline-tick-P1')).toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-pipeline-tick-P2')).toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-pipeline-tick-P3')).toBeInTheDocument();
    // ⑦ 输出等级：P1 芯片 + 中文等级名
    expect(screen.getByTestId('rule-engine-pipeline-level')).toHaveAttribute('data-level', 'P1');
    expect(screen.getByText('重大风险 · 100 分')).toBeInTheDocument();
    // ① 信号输入：真实信源与近 30 天信号
    expect(screen.getByText(/OFAC SDN · 12 条/)).toBeInTheDocument();
    expect(screen.getByText(/近 30 天有信号的信源 1 个/)).toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-pipeline-guidance')).not.toBeInTheDocument();
  });

  it('样例选择后展示被选提醒编号', () => {
    renderPipeline({selectedSampleId: 42});

    expect(screen.getByText('真实样本 #42')).toBeInTheDocument();
  });

  it('registry_no+country 轨迹高亮「主体」「国家/区域」柱，未命中柱不误标', () => {
    renderPipeline();

    expect(screen.getByTestId('rule-engine-pipeline-column-entity')).toHaveAttribute('data-hit', 'true');
    expect(screen.getByTestId('rule-engine-pipeline-column-country')).toHaveAttribute('data-hit', 'true');
    expect(screen.getByTestId('rule-engine-pipeline-column-location')).toHaveAttribute('data-hit', 'false');
    expect(screen.getByTestId('rule-engine-pipeline-column-product')).toHaveAttribute('data-hit', 'false');
    expect(screen.getByTestId('rule-engine-pipeline-column-industry')).toHaveAttribute('data-hit', 'false');
    // 命中柱有文本标记，不只靠颜色
    expect(within(screen.getByTestId('rule-engine-pipeline-column-entity')).getByText(/主体（命中）/)).toBeInTheDocument();
    expect(screen.getByText(/命中方式：主体 \+ 国家\/区域（match_type: registry_no\+country）/)).toBeInTheDocument();
    expect(screen.getByText(/匹配证据 2 条/)).toBeInTheDocument();
  });

  it.each([
    ['registry_no', 'entity'],
    ['legal_name', 'entity'],
    ['alias', 'entity'],
    ['site_text', 'location'],
    ['site_distance', 'location'],
    ['product', 'product'],
    ['country', 'country'],
    ['industry', 'industry'],
  ] as const)('match_type=%s 经映射高亮「%s」柱（与匹配柱枚举不同名）', (matchType, column) => {
    const mapped = trace();
    mapped.match = {match_type: matchType, match_reasons: [], match_evidence: []};
    renderPipeline({trace: mapped});

    expect(screen.getByTestId(`rule-engine-pipeline-column-${column}`)).toHaveAttribute('data-hit', 'true');
    for (const other of ['entity', 'location', 'product', 'country', 'industry'].filter((item) => item !== column)) {
      expect(screen.getByTestId(`rule-engine-pipeline-column-${other}`)).toHaveAttribute('data-hit', 'false');
    }
  });

  it('level_cap 命中时封顶区块高亮并解释真实封顶语义', () => {
    renderPipeline();

    const cap = screen.getByTestId('rule-engine-pipeline-cap');
    expect(cap).toHaveAttribute('data-hit', 'true');
    expect(within(cap).getByText('等级封顶：已命中（weak_association_max_p2）')).toBeInTheDocument();
    expect(within(cap).getByText('未命中主体精确匹配')).toBeInTheDocument();
    expect(within(cap).getByText('没有命中供应商主体（注册号/法人全称/别名），等级最高只能是 P2。')).toBeInTheDocument();
  });

  it('forced_rule 命中时强制规则区块高亮并展示原等级/原分数', () => {
    renderPipeline();

    const forced = screen.getByTestId('rule-engine-pipeline-forced-rule');
    expect(forced).toHaveAttribute('data-hit', 'true');
    expect(within(forced).getByText('强制规则：已命中')).toBeInTheDocument();
    expect(within(forced).getByText('sanctions_entity_hit')).toBeInTheDocument();
    expect(within(forced).getByText('供应商主体直接命中制裁或合规事件')).toBeInTheDocument();
    expect(within(forced).getByText('原因：注册编号命中制裁清单，强制提升为 P1')).toBeInTheDocument();
    expect(within(forced).getByText('原等级 P2（93 分）→ 命中后按满分 100 计')).toBeInTheDocument();
    // 输出等级说明路径
    expect(screen.getByText(/命中强制规则「sanctions_entity_hit」/)).toBeInTheDocument();
  });

  it('未命中封顶/强制规则时显示未命中状态，不误标高亮', () => {
    const plainScore = {
      total: 72,
      level: 'P2',
      detail: {severity: 28, association: 25, source_credibility: 14, timeliness: 5, product_relevance: 0},
      level_cap: null,
      forced_rule: null,
    };
    renderPipeline({trace: trace({score: plainScore})});

    expect(screen.getByTestId('rule-engine-pipeline-cap')).toHaveAttribute('data-hit', 'false');
    expect(screen.getByTestId('rule-engine-pipeline-forced-rule')).toHaveAttribute('data-hit', 'false');
    expect(screen.getByTestId('rule-engine-pipeline-level')).toHaveAttribute('data-level', 'P2');
    expect(screen.getByText(/按总分与 P1\/P2\/P3 阈值进行常规分级/)).toBeInTheDocument();
  });

  it('API 显式返回 0 分时照常显示 0（与字段缺失的 — 区分）', () => {
    const zeroTrace = trace();
    zeroTrace.score = {
      total: 0,
      level: 'P4',
      detail: {severity: 0, association: 0, source_credibility: 0, timeliness: 0, product_relevance: 0},
      level_cap: null,
      forced_rule: null,
    };
    renderPipeline({trace: zeroTrace});

    expect(screen.getByTestId('rule-engine-pipeline-composition-severity')).toHaveAttribute('data-value', '0');
    expect(screen.getByText('0 分')).toBeInTheDocument();
    expect(screen.getByText(/五项合计 0 分；/)).toBeInTheDocument();
  });
});

describe('规则引擎观察态流水线：无数据与脏数据降级', () => {
  it('available=false 时渲染样例引导，并保留真实信源输入信息', () => {
    renderPipeline({trace: emptyTrace()});

    const root = screen.getByTestId('rule-engine-pipeline');
    expect(root).toHaveAttribute('data-available', 'false');
    const guidance = screen.getByTestId('rule-engine-pipeline-guidance');
    expect(guidance).toHaveTextContent(/当前无真实样本，请选择样例事件/);
    for (const label of STAGE_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // 轨迹缺失但输入健康度真实：阶段①仍展示真实信源，不是假数据
    expect(screen.getByText(/OFAC SDN · 12 条/)).toBeInTheDocument();
    expect(screen.getByText('等待样例事件 → 等待样例事件')).toBeInTheDocument();
    // 数值降级为占位：缺失渲染 —（不是伪造的 0），总分标记整体隐藏
    expect(screen.getByTestId('rule-engine-pipeline-level')).toHaveAttribute('data-level', '—');
    expect(screen.getByTestId('rule-engine-pipeline-composition-severity')).toHaveAttribute('data-value', '—');
    expect(screen.getByText(/得分明细缺失，五项合计 — 分/)).toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-pipeline-gauge')).not.toBeInTheDocument();
    expect(screen.queryByText('0 分')).not.toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-pipeline-guidance')).toHaveClass('dark:bg-amber-950/30');
  });

  it('轨迹缺字段（score/match/routing 为 null，字段为空）不崩溃并降级', () => {
    const malformed = {
      available: true,
      event: {event_type: 'weather', event_subtype: null, severity: 'low', summary: '', confidence: 0, published_at: null, source_name: null},
      routing: null,
      match: null,
      score: {total: null, level: null, detail: null, level_cap: null, forced_rule: null},
      samples: null,
    } as unknown as DimensionTraceRead;

    expect(() => renderPipeline({trace: malformed})).not.toThrow();
    for (const label of STAGE_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText('天气 → 未找到接管维度')).toBeInTheDocument();
    // routing 缺失时回退到维度自身的匹配柱配置，且全部标为未命中（不伪装命中）
    expect(screen.getByTestId('rule-engine-pipeline-column-entity')).toHaveAttribute('data-hit', 'false');
    expect(screen.getByText(/命中方式：未命中可用匹配柱/)).toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-pipeline-level')).toHaveAttribute('data-level', '—');
    // score.total=null：不渲染总分标记；五项明细 null：全部渲染 —
    expect(screen.queryByTestId('rule-engine-pipeline-gauge')).not.toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-pipeline-composition-severity')).toHaveAttribute('data-value', '—');
    expect(screen.getByText(/得分明细缺失，五项合计 — 分/)).toBeInTheDocument();
    expect(screen.getByTestId('rule-engine-pipeline-cap')).toHaveAttribute('data-hit', 'false');
    expect(screen.getByTestId('rule-engine-pipeline-forced-rule')).toHaveAttribute('data-hit', 'false');
  });

  it('forced_rule 命中但 original_score/original_level 缺失时不伪造 0 分', () => {
    const degraded = trace();
    degraded.score = {
      total: 100,
      level: 'P1',
      detail: {severity: 35, association: 30, source_credibility: 18, timeliness: 5, product_relevance: 5},
      level_cap: null,
      forced_rule: {name: 'sanctions_entity_hit', reason: '注册编号命中制裁清单，强制提升为 P1'},
    };
    renderPipeline({trace: degraded});

    const forced = screen.getByTestId('rule-engine-pipeline-forced-rule');
    expect(forced).toHaveAttribute('data-hit', 'true');
    expect(within(forced).getByText('原等级 —（— 分）→ 命中后按满分 100 计')).toBeInTheDocument();
  });

  it('轨迹为空对象 {} 时按无真实样本降级，不崩溃', () => {
    expect(() => renderPipeline({trace: {} as unknown as DimensionTraceRead})).not.toThrow();

    expect(screen.getByTestId('rule-engine-pipeline')).toHaveAttribute('data-available', 'false');
    expect(screen.getByTestId('rule-engine-pipeline-guidance')).toHaveTextContent(/当前无真实样本/);
  });

  it('维度缺少 thresholds 时显示「阈值未提供」，不伪造 85/65/40', () => {
    const malformedDimension = {...dimension(), thresholds: undefined} as unknown as MonitoringDimension;

    expect(() => renderPipeline({dimension: malformedDimension})).not.toThrow();
    expect(screen.getByText(/等级刻度：阈值未提供/)).toBeInTheDocument();
    // 不得回退到 85/65/40 的伪默认值
    expect(screen.queryByText('等级刻度：P3 ≥ 40 · P2 ≥ 65 · P1 ≥ 85（满分 100）')).not.toBeInTheDocument();
    // 缺失阈值时不画 P1/P2/P3 刻度线
    expect(screen.queryByTestId('rule-engine-pipeline-tick-P1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-pipeline-tick-P2')).not.toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-pipeline-tick-P3')).not.toBeInTheDocument();
  });

  it('输入健康度失败时降级为可读提示，其余阶段仍渲染', () => {
    renderPipeline({inputsError: '输入健康度服务不可用'});

    expect(screen.getByText('输入健康度加载失败：输入健康度服务不可用')).toBeInTheDocument();
    expect(screen.getByText('⑦ 输出等级')).toBeInTheDocument();
    expect(screen.getByText('地缘政治 → 地缘政治与安全')).toBeInTheDocument();
  });

  it('无已接入信源时给出显著提示', () => {
    renderPipeline({
      inputs: inputs({observed: [], declared_linked: 0, declared_enabled: 0, has_input: false}),
      dimension: dimension({dataSources: []}),
    });

    expect(screen.getByText('无已接入信源，当前不会产生提醒。')).toBeInTheDocument();
  });

  it('轨迹接口失败时以 role=alert 提示且不渲染阶段', () => {
    renderPipeline({trace: null, traceError: '轨迹服务不可用'});

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('运行轨迹加载失败：轨迹服务不可用');
    expect(screen.queryByText('① 信号输入')).not.toBeInTheDocument();
  });
});

describe('规则引擎观察态流水线：响应式、深色模式与减少动效', () => {
  it('窄屏结构：根容器 min-w-0，阶段区 overflow-x-auto，阶段正文可收缩', () => {
    const {container} = renderPipeline();

    expect(screen.getByTestId('rule-engine-pipeline')).toHaveClass('min-w-0');
    expect(screen.getByTestId('rule-engine-pipeline-scroll')).toHaveClass('overflow-x-auto');
    expect(screen.getByTestId('rule-engine-pipeline-gauge-scroll')).toHaveClass('overflow-x-auto');

    const stageBodies = container.querySelectorAll('[data-stage-body]');
    expect(stageBodies.length).toBe(7);
    stageBodies.forEach((body) => expect(body).toHaveClass('min-w-0'));
  });

  it('深色模式类名存在于容器与关键高亮/等级元素', () => {
    renderPipeline();

    expect(screen.getByTestId('rule-engine-pipeline').className).toContain('dark:');
    expect(screen.getByTestId('rule-engine-pipeline-level').className).toContain('dark:');
    expect(screen.getByTestId('rule-engine-pipeline-column-entity').className).toContain('dark:');
    expect(screen.getByTestId('rule-engine-pipeline-cap').className).toContain('dark:');
    expect(screen.getByTestId('rule-engine-pipeline-forced-rule').className).toContain('dark:');
    // 信源 chip 必须用非「兼容 shim 会 !important 重映射」的底色类，否则深色下仍是浅底
    const sourceChip = screen.getByTestId('rule-engine-pipeline-source');
    expect(sourceChip.className).toContain('dark:bg-slate-950/40');
    expect(sourceChip.className).not.toContain('bg-[#f7f9ff]');
  });

  it('总分处于边界（0/100）时标记标签按边界对齐，不被滚动容器裁剪', () => {
    renderPipeline();
    expect(screen.getByTestId('rule-engine-pipeline-gauge').className).toContain('-translate-x-full');

    cleanup();
    const zeroTrace = trace();
    zeroTrace.score = {
      total: 0,
      level: 'P4',
      detail: {severity: 0, association: 0, source_credibility: 0, timeliness: 0, product_relevance: 0},
      level_cap: null,
      forced_rule: null,
    };
    renderPipeline({trace: zeroTrace});

    const zeroGaugeClass = screen.getByTestId('rule-engine-pipeline-gauge').className;
    expect(zeroGaugeClass).not.toContain('-translate-x-full');
    expect(zeroGaugeClass).not.toContain('-translate-x-1/2');
  });

  it('useReducedMotion 为真：标记减少动效且不渲染任何位移动画（无 translate 内联样式）', () => {
    motionControl.reduced = true;
    const {container} = renderPipeline();

    expect(screen.getByTestId('rule-engine-pipeline')).toHaveAttribute('data-reduced-motion', 'true');
    expect(container.querySelectorAll('[style*="translate"]')).toHaveLength(0);
    expect(screen.getByTestId('rule-engine-pipeline-stage-signal').getAttribute('style')).toBeNull();
    expect(screen.getByTestId('rule-engine-pipeline-stage-level').getAttribute('style')).toBeNull();
  });

  it('useReducedMotion 为假：标记允许入场动画（对照组，证明上面不是硬编码）', () => {
    motionControl.reduced = false;
    const {container} = renderPipeline();

    expect(screen.getByTestId('rule-engine-pipeline')).toHaveAttribute('data-reduced-motion', 'false');
    expect(container.querySelectorAll('[style*="translate"]').length).toBeGreaterThan(0);
  });
});

describe('运行轨迹嵌入模式（#18：证据卡内嵌块，非第二张独立卡）', () => {
  it('embedded=true：无整卡描边/阴影/模糊，头部为「运行轨迹」+「7 阶段」块头，阶段语义不变', () => {
    renderPipeline({embedded: true});

    const root = screen.getByTestId('rule-engine-pipeline');
    expect(root).toHaveAttribute('data-embedded', 'true');
    // 不再是第二张完整独立仪表卡
    expect(root.className).not.toContain('rounded-2xl');
    expect(root.className).not.toContain('shadow-sm');
    expect(root.className).not.toContain('backdrop-blur-md');
    // demo 内嵌块头形态（原型 block-head + count-chip）
    expect(screen.getByText('运行轨迹')).toBeInTheDocument();
    expect(screen.getByText('7 阶段')).toBeInTheDocument();
    expect(screen.queryByText('规则运行流水线')).not.toBeInTheDocument();
    // 七阶段与真实数据全部保留（加载/错误/轨迹语义未被改变）
    for (const label of STAGE_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByTestId('rule-engine-pipeline-level')).toHaveAttribute('data-level', 'P1');
    expect(screen.getByTestId('rule-engine-pipeline-composition-severity')).toHaveAttribute('data-value', '35');
    expect(screen.getByText('真实样本（最近一条）')).toBeInTheDocument();
  });

  it('embedded=true 且 trace 不可用：保留无样本引导与七阶段占位，仍无整卡外壳', () => {
    renderPipeline({trace: emptyTrace(), embedded: true});

    const root = screen.getByTestId('rule-engine-pipeline');
    expect(root).toHaveAttribute('data-available', 'false');
    expect(root.className).not.toContain('rounded-2xl');
    expect(screen.getByTestId('rule-engine-pipeline-guidance')).toHaveTextContent(/当前无真实样本，请选择样例事件/);
    for (const label of STAGE_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it('embedded 缺省（false）：保持原独立卡形态向后兼容', () => {
    renderPipeline();

    const root = screen.getByTestId('rule-engine-pipeline');
    expect(root).toHaveAttribute('data-embedded', 'false');
    expect(root.className).toContain('rounded-2xl');
    expect(root.className).toContain('shadow-sm');
    expect(screen.getByText('规则运行流水线')).toBeInTheDocument();
    expect(screen.queryByText('7 阶段')).not.toBeInTheDocument();
  });
});

import React from 'react';
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {MemoryRouter} from 'react-router-dom';
import {api} from '../api';
import type {MonitoringDimension} from '../types';
import {DEFAULT_ASSOCIATION_SCORES, DEFAULT_SEVERITY_SCORES} from './RuleEngineContext';
import {RuleEngineExplainers} from './RuleEngineExplainers';
import {RuleEngineView} from './RuleEngineView';
import {SignalFilterSection} from './SignalFilterSection';
import {
  LEVEL_CAP_RULES,
  RULE_ENGINE_EXPLAINERS,
  SIGNAL_FILTER_EXPLAINER_COPY,
  TOTAL_SCORE_FORMULA,
} from './ruleEngineExplainerCopy';

const FILTER_CONFIG = {
  high_impact: ['cbam'],
  priority_countries: ['JP'],
  list_sources: ['ofac-sdn'],
  source: 'default' as const,
};

/**
 * todo 10 必需的六个说明模块（ID 与标题独立硬编码，不依赖生产数组枚举）。
 * 缺模块、改 ID 或标题漂移都会先在这里失败；生产数组须与本清单互为镜像。
 */
const REQUIRED_EXPLAINERS: ReadonlyArray<{id: string; title: string}> = [
  {id: 'match-columns', title: '匹配柱'},
  {id: 'event-types', title: '事件类型'},
  {id: 'severity-scores', title: '严重程度分值'},
  {id: 'association-scores', title: '关联类型分值'},
  {id: 'level-thresholds', title: '分级阈值'},
  {id: 'signal-filter', title: '信号过滤规则'},
];

/**
 * 总分公式分项字段 → 后端 ScoringSettings 默认值（scoring.py:44-47），显式硬编码。
 * 不从 `TOTAL_SCORE_FORMULA.parts` 推导；文案若把默认值写成固定值，此表会直接失败。
 */
const TOTAL_SCORE_FIELD_DEFAULTS: ReadonlyArray<{field: string; anchor: string; value: number}> = [
  {field: 'credibility_weight', anchor: '来源可信度', value: 0.2},
  {field: 'timeliness_with_date', anchor: '有发布时间', value: 10},
  {field: 'timeliness_without_date', anchor: '无发布时间', value: 5},
  {field: 'product_relevance_score', anchor: '产品相关性', value: 5},
];

/** viewer 壳路径渲染用的最小维度夹具（观察态数据全部由 mock 提供）。 */
const viewerDimension = (): MonitoringDimension => ({
  id: 'natural',
  name: '自然环境',
  icon: 'landscape',
  enabled: true,
  ruleId: 'natural-v1',
  severityScores: {...DEFAULT_SEVERITY_SCORES},
  associationScores: {...DEFAULT_ASSOCIATION_SCORES},
  thresholds: {p1: 85, p2: 65, p3: 40},
  matchColumns: ['entity', 'location', 'product'],
  eventTypes: ['weather'],
  forcedRules: [],
  contentItems: ['地震', '台风'],
  dataSources: [],
});

beforeEach(() => {
  vi.spyOn(api.filterConfig, 'get').mockResolvedValue(FILTER_CONFIG);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('规则引擎配置项语义说明层（todo 10）', () => {
  it('六个必需说明模块全部渲染：ID/标题独立核对，每卡含定义、例子与当前值', () => {
    render(<RuleEngineExplainers mode="observation" />);

    // 独立清单逐项核验：缺模块/改 ID/改标题都会在此失败，而不是靠生产数组自证
    expect(REQUIRED_EXPLAINERS).toHaveLength(6);
    for (const {id, title} of REQUIRED_EXPLAINERS) {
      const card = screen.getByTestId(`rule-engine-explainer-${id}`);
      expect(within(card).getByText(title)).toBeInTheDocument();
      expect(within(card).getByText(/^例：/)).toBeInTheDocument();
      expect(within(card).getByText(/^当前值：/)).toBeInTheDocument();
    }

    // 生产数组与独立清单互为镜像：既不许缺失，也不许出现计划外的第七项
    const productionPairs = RULE_ENGINE_EXPLAINERS.map((entry) => `${entry.id}:${entry.title}`).sort();
    const requiredPairs = REQUIRED_EXPLAINERS.map((entry) => `${entry.id}:${entry.title}`).sort();
    expect(productionPairs).toEqual(requiredPairs);
  });

  it('包含总分公式与两条封顶规则的关键词，公式默认值明确标注为「按当前配置/默认」', () => {
    render(<RuleEngineExplainers mode="observation" />);

    const formulaCard = screen.getByTestId('rule-engine-explainer-total-score');
    expect(screen.getByText('总分公式')).toBeInTheDocument();
    expect(within(formulaCard).getByText(TOTAL_SCORE_FORMULA.formula)).toBeInTheDocument();
    expect(within(formulaCard).getByText(TOTAL_SCORE_FORMULA.note)).toBeInTheDocument();

    // 字段 → 默认值映射独立硬编码核对；每个含默认数字的分项必须声明「按当前配置/默认」
    const formulaItems = within(formulaCard).getAllByRole('listitem').map((item) => item.textContent ?? '');
    for (const {field, anchor, value} of TOTAL_SCORE_FIELD_DEFAULTS) {
      const item = formulaItems.find((text) => text.includes(anchor));
      expect(item, `总分公式缺少字段 ${field}（锚点「${anchor}」）对应分项`).toBeDefined();
      expect(item ?? '', `字段 ${field} 的默认值 ${value} 应出现在文案中`).toContain(String(value));
      expect(item ?? '', `字段 ${field} 的默认值必须标注为按当前配置/默认，不能写成固定值`).toMatch(/按当前配置|默认/);
    }
    expect(formulaCard).toHaveTextContent('上限 100 分');

    expect(screen.getByTestId('rule-engine-explainer-level-caps')).toBeInTheDocument();
    expect(LEVEL_CAP_RULES).toHaveLength(2);
    expect(screen.getByText('只命中「国家」柱')).toBeInTheDocument();
    expect(screen.getByText('最高 P4')).toBeInTheDocument();
    expect(screen.getByText('未命中主体精确匹配')).toBeInTheDocument();
    expect(screen.getByText('最高 P2')).toBeInTheDocument();
  });

  it('分值/阈值文案与前后端共享默认常量一致（防止文案漂移）', () => {
    render(<RuleEngineExplainers mode="observation" />);

    const severityText = screen.getByTestId('rule-engine-explainer-severity-scores').textContent ?? '';
    for (const value of Object.values(DEFAULT_SEVERITY_SCORES)) {
      expect(severityText).toContain(String(value));
    }

    const associationText = screen.getByTestId('rule-engine-explainer-association-scores').textContent ?? '';
    for (const value of Object.values(DEFAULT_ASSOCIATION_SCORES)) {
      expect(associationText).toContain(String(value));
    }

    const thresholdText = screen.getByTestId('rule-engine-explainer-level-thresholds').textContent ?? '';
    expect(thresholdText).toContain('85 / 65 / 40');
  });

  it('信号过滤文案包含四个要点，viewer 观察态只读可读', async () => {
    expect(SIGNAL_FILTER_EXPLAINER_COPY).toContain('调用大模型之前');
    expect(SIGNAL_FILTER_EXPLAINER_COPY).toContain('放行');
    expect(SIGNAL_FILTER_EXPLAINER_COPY).toContain('过滤');
    expect(SIGNAL_FILTER_EXPLAINER_COPY).toContain('清单类信源');

    render(<SignalFilterSection role="viewer" mode="observation" />);

    const description = await screen.findByText(/调用大模型之前/);
    expect(description).toHaveTextContent('放行');
    expect(description).toHaveTextContent('过滤');
    expect(description).toHaveTextContent('清单类信源');
    expect(screen.getByText('只读查看')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '保存规则'})).not.toBeInTheDocument();
  });

  it('无 role 限制：说明层只依赖 mode，两种模式都可读', () => {
    const {rerender} = render(<RuleEngineExplainers mode="observation" />);
    expect(screen.getByTestId('rule-engine-explainers')).toHaveAttribute('data-mode', 'observation');

    rerender(<RuleEngineExplainers mode="config" />);
    expect(screen.getByTestId('rule-engine-explainers')).toHaveAttribute('data-mode', 'config');
    for (const entry of RULE_ENGINE_EXPLAINERS) {
      expect(screen.getByTestId(`rule-engine-explainer-${entry.id}`)).toBeInTheDocument();
    }
    expect(screen.getByText('总分公式')).toBeInTheDocument();
  });

  it('viewer 从 RuleEngineView 观察态真实只读路径可读说明层（无 role 门控）', async () => {
    vi.spyOn(api, 'dimensionInputs').mockResolvedValue({
      declared_total: 0, declared_linked: 0, declared_enabled: 0, observed: [], has_input: false,
    });
    vi.spyOn(api, 'ruleEngineOptions').mockResolvedValue({
      match_columns: ['entity', 'location', 'product', 'country', 'industry'],
      event_types: [{value: 'weather', label: '天气'}],
      event_subtypes: [],
    });
    vi.spyOn(api, 'dimensionTrace').mockResolvedValue({
      available: false, event: null, routing: null, match: null, score: null, samples: [],
    });
    vi.spyOn(api.globalConfig, 'get').mockResolvedValue({
      source: 'default', enabled: false, effective: {forced_rules: []}, defaults: {forced_rules: []},
      shadowed_by: {}, forced_rules_shadowed_by: [], dropped_dimension_rules: [],
    });

    render(
      <MemoryRouter>
        <RuleEngineView
          dimensions={[viewerDimension()]}
          onToggleDimension={vi.fn()}
          onUpdateDimension={vi.fn()}
          role="viewer"
        />
      </MemoryRouter>,
    );

    const section = await screen.findByTestId('rule-engine-explainers');
    expect(section).toHaveAttribute('data-mode', 'observation');
    expect(within(section).getByText('规则语义说明')).toBeInTheDocument();

    // 六个必需模块与总分公式/封顶规则在 viewer 真实壳路径全部可读
    for (const {id, title} of REQUIRED_EXPLAINERS) {
      const card = within(section).getByTestId(`rule-engine-explainer-${id}`);
      expect(within(card).getByText(title)).toBeInTheDocument();
      expect(within(card).getByText(/^例：/)).toBeInTheDocument();
      expect(within(card).getByText(/^当前值：/)).toBeInTheDocument();
    }
    expect(within(section).getByTestId('rule-engine-explainer-total-score')).toBeInTheDocument();
    expect(within(section).getByTestId('rule-engine-explainer-level-caps')).toBeInTheDocument();

    // viewer 无法进入配置态：切换按钮禁用，点击后说明层仍可读且不残留写控件
    const toggle = screen.getByTestId('rule-engine-mode-toggle');
    expect(toggle).toBeDisabled();
    fireEvent.click(toggle);
    expect(screen.getByTestId('rule-engine-explainers')).toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-config')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('过滤配置为空或缺省时，说明文案仍给出默认语义（malformed_input）', async () => {
    vi.mocked(api.filterConfig.get).mockResolvedValue({
      high_impact: [], priority_countries: [], list_sources: [], source: 'default',
    });
    render(<SignalFilterSection role="admin" mode="observation" />);

    const description = await screen.findByText(/调用大模型之前/);
    expect(description).toHaveTextContent('清单类信源');
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('过滤配置接口失败时说明仍渲染，不崩溃也不伪装成功（failure）', async () => {
    vi.mocked(api.filterConfig.get).mockRejectedValue(new Error('过滤配置服务不可用'));
    render(<SignalFilterSection role="viewer" mode="observation" />);

    expect(await screen.findByText(/调用大模型之前/)).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });
});

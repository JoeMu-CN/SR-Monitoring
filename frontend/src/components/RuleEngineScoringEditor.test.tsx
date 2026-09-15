import React from 'react';
import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {MemoryRouter} from 'react-router-dom';
import {api, ApiError, updateDimensionConfig} from '../api';
import type {
  DimensionInputsRead,
  DimensionRead,
  DimensionTraceRead,
  GlobalScoringConfigRead,
  GlobalScoringPatchPayload,
  RuleEngineOptions,
  SandboxCandidate,
  SandboxRequest,
  SandboxResult,
} from '../api';
import type {MonitoringDimension} from '../types';
import {
  RuleEngineScoringEditor,
  alignCandidatesBySupplierId,
  dimensionPatchFromDraft,
  validateDimensionDraft,
} from './RuleEngineScoringEditor';
import {RuleEngineContext, defaultSampleEvent} from './RuleEngineContext';
import type {RuleEngineContextValue} from './RuleEngineContext';
import {RuleEngineView} from './RuleEngineView';

/**
 * todo 7 组件测试：配置态评分与阈值可视化编辑 + 解算预览。
 *
 * 迁移自旧 `RuleEngineView.test.tsx:207-280` 的三个用例（8 个关联类型真实数值 /
 * 只改一项 patch 只含该项 / critical=40 阻止提交 / 保存 422 以 role=alert 呈现），
 * 并新增解算预览的两次 `/test` 语义、按 supplier_id 对齐、当前 vs 预览对比、
 * 未保存不写库、阈值顺序门控等断言。`RuleEngineView.test.tsx` 保持不动。
 *
 * 写入层约定：保存路径由壳组件头部「保存配置」触发；测试里的 `onUpdateDimension`
 * 与 `App.tsx` 的真实实现同构（`api.updateDimension(key, updateDimensionConfig(原值, 新值))`），
 * 因此「调用 api.updateDimension 而非 globalConfig.update」在本文件可被真实验证。
 */

const naturalDimension = (overrides: Partial<MonitoringDimension> = {}): MonitoringDimension => ({
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

/** 8 个关联类型（含 country/industry）用于迁移旧的「真实数值 + 只改一项」用例。 */
const geopoliticalDimension = (): MonitoringDimension => ({
  ...naturalDimension(),
  id: 'geopolitical',
  name: '地缘政治与安全',
  icon: 'public',
  ruleId: 'geopolitical-v1',
  associationScores: {registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12, country: 8, industry: 12},
  eventTypes: ['geopolitical'],
  contentItems: ['制裁', '出口管制'],
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
  declared_total: 1, declared_linked: 1, declared_enabled: 1, observed: [], has_input: true,
});

const emptyTrace = (): DimensionTraceRead => ({
  available: false, event: null, routing: null, match: null, score: null, samples: [],
});

const globalConfigMock = (): GlobalScoringConfigRead => ({
  source: 'default',
  enabled: false,
  effective: {
    severity_scores: {critical: 35, high: 28, medium: 20, low: 10},
    association_scores: {registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12, country: 8, industry: 12},
    p1_min: 85, p2_min: 65, p3_min: 40,
    forced_rules: [],
  },
  defaults: {forced_rules: []},
  shadowed_by: {},
  forced_rules_shadowed_by: [],
  dropped_dimension_rules: [],
});

/** 后端候选用例：数值刻意与阈值不自洽（score 97 + P2），用于证明前端不回算等级。 */
const candidate = (overrides: Partial<SandboxCandidate> & {supplier_id: number}): SandboxCandidate => ({
  supplier_name: `供应商 ${overrides.supplier_id}`,
  match_type: 'legal_name',
  association_score: 25,
  reasons: ['法人全称一致'],
  score: 60,
  level: 'P3',
  score_detail: {severity: 20, association: 25, source_credibility: 16, timeliness: 10, product_relevance: 0},
  ...overrides,
});

const sandboxResult = (candidates: SandboxCandidate[], label = '自然环境'): SandboxResult => ({
  dimension: {key: 'natural', label, match_columns: ['entity']},
  candidates,
});

const renderWithRouter = (ui: React.ReactElement) => render(<MemoryRouter>{ui}</MemoryRouter>);

/**
 * 渲染 admin 并切到配置态；`onUpdateDimension` 默认与 `App.tsx:506` 同构，
 * 因此「保存是否真的调用 api.updateDimension」能被真实断言。
 */
const renderAdminConfig = (dims: MonitoringDimension[]) => {
  const original = dims[0];
  const onUpdateDimension = async (updated: MonitoringDimension) => {
    await api.updateDimension(updated.id, updateDimensionConfig(original, updated));
  };
  const result = renderWithRouter(
    <RuleEngineView
      dimensions={dims}
      onToggleDimension={vi.fn()}
      onUpdateDimension={onUpdateDimension}
      role="admin"
    />,
  );
  fireEvent.click(screen.getByTestId('rule-engine-mode-toggle'));
  return result;
};

/**
 * 直接以受控 context 渲染评分编辑器：用于精确构造「非空全局层草稿」，
 * 不依赖 todo 8 强制规则编辑器的交互（并行任务，文件互不触碰）。
 */
const renderScoringEditorWithContext = (globalDraft: GlobalScoringPatchPayload) => {
  const dimension = naturalDimension();
  const value: RuleEngineContextValue = {
    mode: 'config',
    role: 'admin',
    dimension,
    draft: {
      severityScores: {...dimension.severityScores},
      associationScores: {...dimension.associationScores},
      thresholds: {...dimension.thresholds},
      matchColumns: [...dimension.matchColumns],
      eventTypes: [...dimension.eventTypes],
    },
    updateDraft: vi.fn(),
    resetDraft: vi.fn(),
    saving: false,
    configError: '',
    configConflicts: [],
    saveDraft: vi.fn(async () => {}),
    globalConfig: null,
    globalConfigError: '',
    refreshGlobalConfig: vi.fn(async () => {}),
    globalDraft,
    setGlobalDraft: vi.fn(),
    globalSaving: false,
    globalSaveError: '',
    saveGlobalConfig: vi.fn(async () => true),
    sample: defaultSampleEvent(),
    updateSample: vi.fn(),
    options: ruleEngineOptionsMock(),
    optionsError: '',
  };
  return render(
    <RuleEngineContext.Provider value={value}>
      <RuleEngineScoringEditor mode="config" />
    </RuleEngineContext.Provider>,
  );
};

const previewButton = () => screen.getByTestId('rule-engine-solve-preview');

/**
 * 解算预览依赖「事件类型 / 匹配柱」选项加载完成（壳层 hook 异步取数），
 * 先等按钮可用再点击，避免把"选项未就绪"误判成"门控禁用"。
 */
const clickSolvePreviewWhenReady = async () => {
  await waitFor(() => expect(previewButton()).toBeEnabled());
  fireEvent.click(previewButton());
};

/**
 * 勾选/取消壳层「匹配柱」里的某一柱。
 * 匹配柱标签内嵌 material 图标文本，`getByLabelText` 会把图标连字算进标签文本，
 * 因此定位到 label 元素后直接点击其内部 checkbox（与用户点击标签等价）。
 */
const toggleMatchColumn = (label: string) => {
  const section = screen.getByText('匹配柱').closest('section');
  if (section === null) throw new Error('未找到匹配柱区块');
  const labelNode = within(section).getByText(label).closest('label');
  const checkbox = labelNode?.querySelector('input[type="checkbox"]');
  if (!(checkbox instanceof HTMLInputElement)) throw new Error(`未找到匹配柱 ${label} 的勾选框`);
  fireEvent.click(checkbox);
};

beforeEach(() => {
  vi.spyOn(api, 'dimensionInputs').mockResolvedValue(inputsOk());
  vi.spyOn(api, 'ruleEngineOptions').mockResolvedValue(ruleEngineOptionsMock());
  vi.spyOn(api, 'dimensionTrace').mockResolvedValue(emptyTrace());
  vi.spyOn(api.globalConfig, 'get').mockResolvedValue(globalConfigMock());
  vi.spyOn(api.globalConfig, 'update').mockResolvedValue(globalConfigMock());
  vi.spyOn(api.globalConfig, 'reset').mockResolvedValue(globalConfigMock());
  vi.spyOn(api.filterConfig, 'get').mockResolvedValue({
    high_impact: [], priority_countries: [], list_sources: ['ofac-sdn'], source: 'default',
  });
  vi.spyOn(api, 'updateDimension').mockResolvedValue({key: 'natural'} as DimensionRead);
  vi.spyOn(api, 'testRuleEngine').mockResolvedValue(sandboxResult([]));
  vi.stubGlobal('alert', vi.fn());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('配置态评分与阈值可视化编辑（todo 7）', () => {
  it('滑杆 + 步进覆盖全部严重程度与关联类型，且每项都给出对总分的影响说明', () => {
    renderAdminConfig([geopoliticalDimension()]);

    const severity = screen.getByTestId('rule-engine-severity-editor');
    for (const label of ['严重', '高', '中', '低']) {
      expect(within(severity).getByLabelText(`严重程度 ${label}`)).toBeInTheDocument();
      expect(within(severity).getByLabelText(`严重程度 ${label} 滑杆`)).toHaveAttribute('max', '35');
      expect(within(severity).getByLabelText(`严重程度 ${label} 滑杆`)).toHaveAttribute('min', '0');
    }
    expect(within(severity).getByLabelText('严重程度 严重')).toHaveValue(35);
    // 每项都说明它如何作用于总分
    expect(within(severity).getAllByText(/直接计入总分，最高 35 分/)).toHaveLength(4);

    const association = screen.getByTestId('rule-engine-association-editor');
    for (const label of ['注册号', '法人全称', '别名', '地点距离', '地点文本', '产品', '国家', '行业']) {
      expect(within(association).getByLabelText(`关联类型 ${label}`)).toBeInTheDocument();
    }
    expect(within(association).getByLabelText('关联类型 国家 滑杆')).toHaveAttribute('max', '30');
    expect(within(association).getByLabelText('关联类型 国家')).toHaveValue(8);
    expect(within(association).getAllByText(/直接计入总分，最高 30 分/)).toHaveLength(8);

    // 0-100 刻度尺（拖拽 + 输入）
    const scale = screen.getByTestId('rule-engine-threshold-scale');
    expect(within(scale).getByText('P4')).toBeInTheDocument();
    expect(within(scale).getByText('P3')).toBeInTheDocument();
    expect(within(scale).getByText('P2')).toBeInTheDocument();
    expect(within(scale).getByText('P1')).toBeInTheDocument();
    for (const level of ['P1', 'P2', 'P3']) {
      expect(screen.getByLabelText(`${level} 触发阈值`)).toBeInTheDocument();
    }
    // 滑杆区间按 P1 > P2 > P3 动态收窄：拖拽在物理上不可能制造非法顺序
    expect(screen.getByLabelText('P1 触发阈值滑杆')).toHaveAttribute('min', '66');
    expect(screen.getByLabelText('P1 触发阈值滑杆')).toHaveAttribute('max', '100');
    expect(screen.getByLabelText('P2 触发阈值滑杆')).toHaveAttribute('min', '41');
    expect(screen.getByLabelText('P2 触发阈值滑杆')).toHaveAttribute('max', '84');
    expect(screen.getByLabelText('P3 触发阈值滑杆')).toHaveAttribute('min', '0');
    expect(screen.getByLabelText('P3 触发阈值滑杆')).toHaveAttribute('max', '64');
    expect(screen.getByLabelText('P1 触发阈值')).toHaveValue(85);
    expect(screen.getByLabelText('P3 触发阈值')).toHaveValue(40);
  });

  it('解释两条封顶规则：只命中国家柱 → 最高 P4；无主体精确匹配 → 最高 P2', () => {
    renderAdminConfig([naturalDimension()]);

    const countryCap = screen.getByTestId('rule-engine-level-cap-country_only_max_p4');
    expect(countryCap).toHaveTextContent('只命中「国家」柱');
    expect(countryCap).toHaveTextContent('最高 P4');

    const weakCap = screen.getByTestId('rule-engine-level-cap-weak_association_max_p2');
    expect(weakCap).toHaveTextContent('未命中主体精确匹配');
    expect(weakCap).toHaveTextContent('最高 P2');
  });

  it('迁移旧用例：只改 country 一项时 patch 只含该项，保存走 api.updateDimension 而非 globalConfig.update', async () => {
    renderAdminConfig([geopoliticalDimension()]);

    fireEvent.change(screen.getByLabelText('关联类型 国家'), {target: {value: '10'}});
    fireEvent.click(screen.getByRole('button', {name: '保存配置'}));

    await waitFor(() => expect(api.updateDimension).toHaveBeenCalledTimes(1));
    expect(api.updateDimension).toHaveBeenCalledWith('geopolitical', {association_scores: {country: 10}});
    // 其余关联类型未被顺带提交
    const patch = vi.mocked(api.updateDimension).mock.calls[0]?.[1] as Record<string, unknown>;
    expect(patch).not.toHaveProperty('severity_scores');
    expect(patch).not.toHaveProperty('association_scores.industry');
    expect(patch).not.toHaveProperty('p1_min');
    expect(api.globalConfig.update).not.toHaveBeenCalled();
  });

  it('迁移旧用例：critical 改到 40（超出 0-35）时即时提示并阻止提交', async () => {
    renderAdminConfig([naturalDimension()]);

    fireEvent.change(screen.getByLabelText('严重程度 严重'), {target: {value: '40'}});

    expect(screen.getByTestId('rule-engine-scoring-validation')).toHaveTextContent(/严重程度分值超出允许范围（0-35）/);

    fireEvent.click(screen.getByRole('button', {name: '保存配置'}));

    const alerts = await screen.findAllByRole('alert');
    expect(alerts.some((node) => /严重程度分值超出允许范围/.test(node.textContent ?? ''))).toBe(true);
    expect(api.updateDimension).not.toHaveBeenCalled();
    expect(api.globalConfig.update).not.toHaveBeenCalled();
  });

  it('迁移旧用例：保存接口 422 时错误以 role=alert 可访问地呈现', async () => {
    vi.spyOn(api, 'updateDimension').mockRejectedValue(new ApiError(422, '分值超出允许范围：严重程度 0-35'));

    renderAdminConfig([naturalDimension()]);
    fireEvent.change(screen.getByLabelText('严重程度 高'), {target: {value: '20'}});
    fireEvent.click(screen.getByRole('button', {name: '保存配置'}));

    const alerts = await screen.findAllByRole('alert');
    expect(alerts.some((node) => /超出允许范围/.test(node.textContent ?? ''))).toBe(true);
  });

  it('阈值顺序门控：P2 ≥ P1 时给出 role=alert，保存与预览均被阻止且不发任何请求', async () => {
    renderAdminConfig([naturalDimension()]);
    // 先等选项加载完成（此时按钮本应可用），再制造阈值乱序
    await waitFor(() => expect(previewButton()).toBeEnabled());

    fireEvent.change(screen.getByLabelText('P2 触发阈值'), {target: {value: '85'}});

    const validation = screen.getByTestId('rule-engine-scoring-validation');
    expect(validation).toHaveAttribute('role', 'alert');
    expect(validation).toHaveTextContent(/P1 > P2 > P3/);
    expect(validation).toHaveTextContent(/当前 P1=85、P2=85、P3=40/);

    // 解算预览被禁用：点击不发请求
    expect(previewButton()).toBeDisabled();
    fireEvent.click(previewButton());

    // 保存路径必须复用同一套完整校验：点击「保存配置」后同样不发任何请求
    await act(async () => {
      fireEvent.click(screen.getByRole('button', {name: '保存配置'}));
    });

    const alerts = screen.getAllByRole('alert');
    expect(alerts.some((node) => /P1 > P2 > P3/.test(node.textContent ?? ''))).toBe(true);
    expect(api.updateDimension).not.toHaveBeenCalled();
    expect(api.testRuleEngine).not.toHaveBeenCalled();
    expect(api.globalConfig.update).not.toHaveBeenCalled();
    expect(api.globalConfig.reset).not.toHaveBeenCalled();
  });

  it('0-100 边界：P3=-1 与 P1=101 都被保存路径阻止，且不发任何请求', async () => {
    renderAdminConfig([naturalDimension()]);
    await waitFor(() => expect(previewButton()).toBeEnabled());

    // P3=-1：仍然满足 P2 > P3，若不校验 0-100 会绕过顺序门控直接提交
    fireEvent.change(screen.getByLabelText('P3 触发阈值'), {target: {value: '-1'}});
    await act(async () => {
      fireEvent.click(screen.getByRole('button', {name: '保存配置'}));
    });

    expect(api.updateDimension).not.toHaveBeenCalled();
    expect(api.testRuleEngine).not.toHaveBeenCalled();
    expect(api.globalConfig.update).not.toHaveBeenCalled();
    expect(api.globalConfig.reset).not.toHaveBeenCalled();
    const lowValidation = screen.getByTestId('rule-engine-scoring-validation');
    expect(lowValidation).toHaveAttribute('role', 'alert');
    expect(lowValidation).toHaveTextContent(/分级阈值超出允许范围（0-100）/);
    expect(lowValidation).toHaveTextContent(/P3=-1/);

    // 恢复 P3 后把 P1 改到 101：顺序仍满足 101 > 65 > 40，但越界同样被阻止
    fireEvent.change(screen.getByLabelText('P3 触发阈值'), {target: {value: '40'}});
    fireEvent.change(screen.getByLabelText('P1 触发阈值'), {target: {value: '101'}});
    await act(async () => {
      fireEvent.click(screen.getByRole('button', {name: '保存配置'}));
    });

    expect(api.updateDimension).not.toHaveBeenCalled();
    expect(api.testRuleEngine).not.toHaveBeenCalled();
    expect(api.globalConfig.update).not.toHaveBeenCalled();
    expect(api.globalConfig.reset).not.toHaveBeenCalled();
    const highValidation = screen.getByTestId('rule-engine-scoring-validation');
    expect(highValidation).toHaveTextContent(/分级阈值超出允许范围（0-100）/);
    expect(highValidation).toHaveTextContent(/P1=101/);
  });

  it('解算预览调用 /test 恰好两次：先 baseline（不带草稿）后 preview（带 dimension_key + draft_config）', async () => {
    const testSpy = vi
      .spyOn(api, 'testRuleEngine')
      .mockResolvedValueOnce(sandboxResult([candidate({supplier_id: 1, score: 62, level: 'P3'})]))
      .mockResolvedValueOnce(sandboxResult([
        candidate({supplier_id: 1, score: 97, level: 'P2', score_detail: {severity: 30, association: 25, source_credibility: 16, timeliness: 10, product_relevance: 0, level_cap: 'weak_association_max_p2'}}),
      ]));

    renderAdminConfig([naturalDimension()]);
    fireEvent.change(screen.getByLabelText('严重程度 严重'), {target: {value: '30'}});
    await clickSolvePreviewWhenReady();

    await waitFor(() => expect(testSpy).toHaveBeenCalledTimes(2));
    const [firstPayload, secondPayload] = testSpy.mock.calls.map((call) => call[0] as SandboxRequest);

    // baseline：不带任何草稿（= 当前生效配置）
    expect(firstPayload.dimension_key).toBeUndefined();
    expect(firstPayload.draft_config).toBeUndefined();
    expect(firstPayload.global_config).toBeUndefined();
    expect(firstPayload.event_type).toBe('weather');

    // preview：携带维度 key 与只含改动项的草稿
    expect(secondPayload.dimension_key).toBe('natural');
    expect(secondPayload.draft_config).toEqual({severity_scores: {critical: 30}});

    // 两份返回都渲染：当前 62/P3，预览 97/P2（后端值原样呈现，前端不回算等级）
    const result = await screen.findByTestId('rule-engine-solve-preview-result');
    expect(result).toHaveTextContent('当前（页面已保存的配置）');
    expect(result).toHaveTextContent('预览（未保存草稿）');
    const row = within(result).getByTestId('rule-engine-preview-candidate-1');
    expect(row).toHaveTextContent('P3 · 62 分');
    expect(row).toHaveTextContent('P2 · 97 分');
    expect(row).toHaveTextContent('P3 62 分 → P2 97 分');
    // 分值构成来自后端 score_detail
    expect(row).toHaveTextContent('严重程度 20 → 30');
    expect(row).toHaveTextContent('关联强度 25');
    expect(row).toHaveTextContent('封顶：无主体精确匹配 → P2');
    // 解算输入披露改动的字段，并声明不写库
    expect(screen.getByTestId('rule-engine-solve-preview-meta')).toHaveTextContent('severity_scores');
    expect(screen.getByTestId('rule-engine-solve-preview-meta')).toHaveTextContent('预览结果不会写入数据库');
  });

  it('未保存的全局层草稿：非空 globalDraft 作为 global_config 只出现在第二次预览请求', async () => {
    const testSpy = vi.spyOn(api, 'testRuleEngine').mockResolvedValue(sandboxResult([]));
    const globalDraft: GlobalScoringPatchPayload = {severity_scores: {critical: 30}};

    renderScoringEditorWithContext(globalDraft);
    fireEvent.click(previewButton());

    await waitFor(() => expect(testSpy).toHaveBeenCalledTimes(2));
    const [firstPayload, secondPayload] = testSpy.mock.calls.map((call) => call[0] as SandboxRequest);

    // baseline 不携带任何草稿，global_config 只随 preview 载荷发出
    expect(firstPayload.global_config).toBeUndefined();
    expect(secondPayload.global_config).toEqual({severity_scores: {critical: 30}});
  });

  it('候选按 supplier_id 对齐：两次返回顺序不同也不会串行', async () => {
    const testSpy = vi
      .spyOn(api, 'testRuleEngine')
      .mockResolvedValueOnce(sandboxResult([
        candidate({supplier_id: 2, supplier_name: '乙公司', score: 55, level: 'P4'}),
        candidate({supplier_id: 1, supplier_name: '甲公司', score: 62, level: 'P3'}),
      ]))
      .mockResolvedValueOnce(sandboxResult([
        candidate({supplier_id: 1, supplier_name: '甲公司', score: 88, level: 'P1'}),
        candidate({supplier_id: 2, supplier_name: '乙公司', score: 55, level: 'P4'}),
      ]));

    renderAdminConfig([naturalDimension()]);
    await clickSolvePreviewWhenReady();
    await waitFor(() => expect(testSpy).toHaveBeenCalledTimes(2));

    const rowOne = await screen.findByTestId('rule-engine-preview-candidate-1');
    expect(rowOne).toHaveTextContent('甲公司');
    expect(rowOne).toHaveTextContent('P3 · 62 分');
    expect(rowOne).toHaveTextContent('P1 · 88 分');

    const rowTwo = screen.getByTestId('rule-engine-preview-candidate-2');
    expect(rowTwo).toHaveTextContent('乙公司');
    expect(rowTwo).toHaveTextContent('P4 · 55 分');
    expect(rowTwo).toHaveTextContent('分数与等级均无变化');
  });

  it('改动匹配柱进入草稿并反映到对比：候选新增/消失都按 supplier_id 正确标注', async () => {
    const testSpy = vi
      .spyOn(api, 'testRuleEngine')
      .mockResolvedValueOnce(sandboxResult([
        candidate({supplier_id: 7, supplier_name: '仅当前命中', score: 60, level: 'P3'}),
      ]))
      .mockResolvedValueOnce(sandboxResult([
        candidate({supplier_id: 8, supplier_name: '仅预览命中', score: 70, level: 'P2'}),
      ]));

    renderAdminConfig([naturalDimension()]);
    // 匹配柱选项由壳层异步加载：等控件就绪后再切换
    await waitFor(() => expect(screen.getByText('主体')).toBeInTheDocument());
    // 关闭「地点」柱、打开「国家/区域」柱（壳层匹配柱控件，写入同一草稿）
    toggleMatchColumn('地点');
    toggleMatchColumn('国家/区域');
    await clickSolvePreviewWhenReady();

    await waitFor(() => expect(testSpy).toHaveBeenCalledTimes(2));
    const secondPayload = testSpy.mock.calls[1]?.[0] as SandboxRequest;
    expect(secondPayload.draft_config?.match_columns).toEqual(['entity', 'product', 'country']);

    const result = await screen.findByTestId('rule-engine-solve-preview-result');
    expect(within(result).getByTestId('rule-engine-preview-candidate-7')).toHaveTextContent('预览不再命中');
    expect(within(result).getByTestId('rule-engine-preview-candidate-8')).toHaveTextContent('预览新增命中');
  });

  it('未保存时不写库：编辑并解算预览全程不调用 api.updateDimension', async () => {
    const testSpy = vi.spyOn(api, 'testRuleEngine').mockResolvedValue(sandboxResult([]));

    renderAdminConfig([naturalDimension()]);
    fireEvent.change(screen.getByLabelText('P1 触发阈值'), {target: {value: '90'}});
    fireEvent.change(screen.getByLabelText('关联类型 产品'), {target: {value: '18'}});
    await clickSolvePreviewWhenReady();
    await waitFor(() => expect(testSpy).toHaveBeenCalledTimes(2));

    expect(api.updateDimension).not.toHaveBeenCalled();
    expect(api.globalConfig.update).not.toHaveBeenCalled();
    expect(api.globalConfig.reset).not.toHaveBeenCalled();
  });

  it('stale_state：切换维度后不残留上一维度的解算结果', async () => {
    const testSpy = vi.spyOn(api, 'testRuleEngine').mockResolvedValue(sandboxResult([candidate({supplier_id: 1})]));

    renderAdminConfig([naturalDimension(), geopoliticalDimension()]);
    await clickSolvePreviewWhenReady();
    expect(await screen.findByTestId('rule-engine-solve-preview-result')).toBeInTheDocument();
    expect(testSpy).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByTestId('rule-engine-dimension-geopolitical'));

    await waitFor(() => expect(screen.queryByTestId('rule-engine-solve-preview-result')).not.toBeInTheDocument());
    expect(screen.queryByTestId('rule-engine-preview-candidate-1')).not.toBeInTheDocument();
  });

  it('stale_state：解算进行中切换维度，迟到的响应不会渲染出上一维度的结果', async () => {
    let releaseBaseline: (result: SandboxResult) => void = () => {};
    const pendingBaseline = new Promise<SandboxResult>((resolve) => { releaseBaseline = resolve; });
    const testSpy = vi
      .spyOn(api, 'testRuleEngine')
      .mockImplementationOnce(() => pendingBaseline)
      .mockResolvedValue(sandboxResult([]));

    renderAdminConfig([naturalDimension(), geopoliticalDimension()]);
    await waitFor(() => expect(previewButton()).toBeEnabled());
    fireEvent.click(previewButton());
    // baseline 仍在飞行中：此时切换到另一个维度（编辑器重挂载）
    expect(testSpy).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId('rule-engine-dimension-geopolitical'));

    await act(async () => { releaseBaseline(sandboxResult([candidate({supplier_id: 9})])); });

    expect(screen.queryByTestId('rule-engine-solve-preview-result')).not.toBeInTheDocument();
    expect(screen.queryByTestId('rule-engine-preview-candidate-9')).not.toBeInTheDocument();
    expect(api.updateDimension).not.toHaveBeenCalled();
  });

  it('malformed_input：预览接口 422 时展示冲突/校验错误，不渲染结果也不算已保存', async () => {
    vi.spyOn(api, 'testRuleEngine').mockRejectedValue(
      new ApiError(422, '事件类型「weather」已被维度「geopolitical」占用'),
    );

    renderAdminConfig([naturalDimension()]);
    await clickSolvePreviewWhenReady();

    const errorBox = await screen.findByTestId('rule-engine-solve-preview-error');
    expect(errorBox).toHaveAttribute('role', 'alert');
    expect(errorBox).toHaveTextContent('HTTP 422');
    expect(errorBox).toHaveTextContent('事件类型「weather」已被维度「geopolitical」占用');
    expect(errorBox).toHaveTextContent('没有任何配置被保存');
    expect(screen.queryByTestId('rule-engine-solve-preview-result')).not.toBeInTheDocument();
    expect(api.updateDimension).not.toHaveBeenCalled();
    expect(api.globalConfig.update).not.toHaveBeenCalled();
    expect(api.globalConfig.reset).not.toHaveBeenCalled();
  });
});

describe('评分编辑器纯函数（确定性单元断言）', () => {
  const draft = (overrides: Partial<Parameters<typeof validateDimensionDraft>[0]> = {}) => ({
    severityScores: {critical: 35, high: 28},
    associationScores: {country: 8},
    thresholds: {p1: 85, p2: 65, p3: 40},
    matchColumns: ['entity'],
    eventTypes: ['weather'],
    ...overrides,
  });

  it('validateDimensionDraft：越界 / 阈值顺序 / 空匹配柱各自独立报错', () => {
    expect(validateDimensionDraft(draft()).messages).toEqual([]);

    const outOfRange = validateDimensionDraft(draft({severityScores: {critical: 40}, associationScores: {country: 31}}));
    expect(outOfRange.severityOutOfRange).toEqual(['严重']);
    expect(outOfRange.associationOutOfRange).toEqual(['国家']);
    expect(outOfRange.messages.join('')).toMatch(/0-35/);
    expect(outOfRange.messages.join('')).toMatch(/0-30/);

    expect(validateDimensionDraft(draft({thresholds: {p1: 60, p2: 70, p3: 40}})).thresholdOrderInvalid).toBe(true);
    expect(validateDimensionDraft(draft({thresholds: {p1: 85, p2: 40, p3: 40}})).thresholdOrderInvalid).toBe(true);
    expect(validateDimensionDraft(draft({matchColumns: []})).matchColumnsEmpty).toBe(true);
  });

  it('validateDimensionDraft：0-100 边界独立于顺序校验，P1=101 与 P3=-1 都报越界', () => {
    const boundHigh = validateDimensionDraft(draft({thresholds: {p1: 101, p2: 65, p3: 40}}));
    expect(boundHigh.thresholdOrderInvalid).toBe(false);
    expect(boundHigh.thresholdOutOfRange).toEqual(['p1']);
    expect(boundHigh.messages.join('')).toMatch(/分级阈值超出允许范围（0-100）/);
    expect(boundHigh.messages.join('')).toMatch(/P1=101/);

    const boundLow = validateDimensionDraft(draft({thresholds: {p1: 85, p2: 65, p3: -1}}));
    expect(boundLow.thresholdOrderInvalid).toBe(false);
    expect(boundLow.thresholdOutOfRange).toEqual(['p3']);
    expect(boundLow.messages.join('')).toMatch(/P3=-1/);

    // 边界值本身合法
    expect(validateDimensionDraft(draft({thresholds: {p1: 100, p2: 65, p3: 0}})).thresholdOutOfRange).toEqual([]);
  });

  it('dimensionPatchFromDraft：无改动返回空 patch，改动只含该项（含阈值与匹配柱）', () => {
    const original = naturalDimension();
    expect(dimensionPatchFromDraft(original, {
      severityScores: {...original.severityScores},
      associationScores: {...original.associationScores},
      thresholds: {...original.thresholds},
      matchColumns: [...original.matchColumns],
      eventTypes: [...original.eventTypes],
    })).toEqual({});

    expect(dimensionPatchFromDraft(original, {
      severityScores: {...original.severityScores, critical: 30},
      associationScores: {...original.associationScores},
      thresholds: {...original.thresholds},
      matchColumns: [...original.matchColumns],
      eventTypes: [...original.eventTypes],
    })).toEqual({severity_scores: {critical: 30}});

    expect(dimensionPatchFromDraft(original, {
      severityScores: {...original.severityScores},
      associationScores: {...original.associationScores},
      thresholds: {p1: 90, p2: 65, p3: 40},
      matchColumns: ['entity', 'country'],
      eventTypes: [...original.eventTypes],
    })).toEqual({p1_min: 90, match_columns: ['entity', 'country']});
  });

  it('alignCandidatesBySupplierId：按 id 合并两次结果并标注新增/消失', () => {
    const rows = alignCandidatesBySupplierId(
      sandboxResult([candidate({supplier_id: 2}), candidate({supplier_id: 1})]),
      sandboxResult([candidate({supplier_id: 1}), candidate({supplier_id: 3})]),
    );
    expect(rows.map((row) => row.supplierId)).toEqual([2, 1, 3]);
    expect(rows[0].current?.supplier_id).toBe(2);
    expect(rows[0].preview).toBeNull();
    expect(rows[1].current?.supplier_id).toBe(1);
    expect(rows[1].preview?.supplier_id).toBe(1);
    expect(rows[2].current).toBeNull();
    expect(rows[2].preview?.supplier_id).toBe(3);
  });
});

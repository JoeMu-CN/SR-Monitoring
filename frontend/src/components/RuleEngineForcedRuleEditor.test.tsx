import React from 'react';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {MemoryRouter} from 'react-router-dom';
import {
  api,
  ForcedRuleRead,
  GlobalScoringConfigRead,
  GlobalScoringPatchPayload,
  RuleEngineOptions,
} from '../api';
import type {MonitoringDimension} from '../types';
import {
  RuleEngineContext,
  RuleEngineContextValue,
  defaultSampleEvent,
  draftFromDimension,
} from './RuleEngineContext';
import {RuleEngineForcedRules, buildSubmittableRules} from './RuleEngineForcedRules';
import {RuleEngineView} from './RuleEngineView';

/**
 * 配置态-全局强制规则编辑器（todo 8）测试。
 *
 * 覆盖计划验收标准与对抗性观察项：
 * - 新增/删除/编辑/排序与完整字段提交；名称非空且唯一、原因非空、等级必选 → role="alert" 阻止提交
 * - 删除与「恢复默认」二次确认；保存空表 / 移除当前生效规则必须带 confirm_disable_forced_rules
 * - 数据来源只有 GET /global-config 的全局层（含可编辑的维度追加规则、不含 policy_industry_hit）
 * - 对抗性输入：GET 全局层即使包含 policy_industry_hit，也不渲染、不回传，仅由 dropped 警告披露
 * - 写侧名称旁路：把既有行改名 / 新增为 policy_industry_hit 时校验以 role=alert 阻止提交且不发请求；
 *   载荷与 globalDraft 的边界映射独立兜底，即使校验被绕过该名称也绝不进入 PUT body 或预览草稿
 * - 未编辑字段逐字回传（含首尾空白），提交载荷不做 trim 等规范化
 * - dropped_dimension_rules 的非关闭式保存前提示；viewer 无编辑控件；422 不标记为已保存；保存后回读服务端
 */

const SECURITY_CRITICAL_RULE: ForcedRuleRead = {
  name: 'sanctions_entity_hit',
  description: '供应商主体直接命中制裁或合规事件',
  event_types: ['compliance', 'judicial'],
  event_subtypes: [],
  match_types: ['registry_no', 'legal_name', 'alias'],
  forced_level: 'P1',
  reason: '供应商主体直接命中制裁/合规事件，强制提升为 P1',
};

/** 维度追加规则：todo 1 起它们就是全局层的普通可编辑条目，必须原样回传 */
const GEOPOLITICAL_DIMENSION_RULE: ForcedRuleRead = {
  name: 'sanctions_geopolitical_entity_hit',
  description: '地缘政治事件命中供应商主体',
  event_types: ['geopolitical'],
  event_subtypes: [],
  match_types: ['registry_no'],
  forced_level: 'P1',
  reason: '地缘政治事件命中供应商主体，强制提升为 P1',
};

const PRODUCT_DIMENSION_RULE: ForcedRuleRead = {
  name: 'sanctions_product_hit',
  description: '供应产品命中制裁清单',
  event_types: ['trade_policy'],
  event_subtypes: ['export_control'],
  match_types: ['product'],
  forced_level: 'P2',
  reason: '供应产品命中制裁清单，强制升级为 P2',
};

/** 维度层规则：绝不允许进入编辑器初始值（本用例用它做反向断言） */
const DIMENSION_ONLY_RULE: ForcedRuleRead = {
  name: 'dimension_only_should_not_appear',
  description: '只存在于维度层增量',
  event_types: ['weather'],
  event_subtypes: [],
  match_types: ['country'],
  forced_level: 'P4',
  reason: '维度层增量不得作为编辑器初始值',
};

const GLOBAL_EFFECTIVE_RULES: ForcedRuleRead[] = [
  SECURITY_CRITICAL_RULE,
  GEOPOLITICAL_DIMENSION_RULE,
  PRODUCT_DIMENSION_RULE,
];

const DROPPED_RULE = {
  name: 'policy_industry_hit',
  description: '行业命中政策限制',
  event_types: [],
  event_subtypes: [],
  match_types: ['industry'],
  forced_level: 'P2',
  reason: '行业命中政策限制，强制升级为 P2',
};

/**
 * 对抗性全局层：后端 `ForcedRuleUpdate` 仅要求名称非空，且全局覆盖行的 forced_rules
 * 会整体成为生效列表——因此 policy_industry_hit 完全可能真的出现在 GET.effective 里。
 * 编辑器必须按名称硬排除它：既不渲染，也不回传。
 */
const ADVERSARIAL_EFFECTIVE_RULES: ForcedRuleRead[] = [
  SECURITY_CRITICAL_RULE,
  GEOPOLITICAL_DIMENSION_RULE,
  PRODUCT_DIMENSION_RULE,
  DROPPED_RULE,
];

/** 带首尾空白的全局层条目（服务端已有值）：未编辑字段必须逐字回传，不允许被 trim 静默改写 */
const WHITESPACE_EFFECTIVE_RULES: ForcedRuleRead[] = [
  SECURITY_CRITICAL_RULE,
  {
    ...GEOPOLITICAL_DIMENSION_RULE,
    name: '  sanctions_geopolitical_entity_hit  ',
    description: '  地缘政治事件命中供应商主体  ',
    reason: '  地缘政治事件命中供应商主体，强制提升为 P1  ',
  },
  {
    ...PRODUCT_DIMENSION_RULE,
    description: ' 供应产品命中制裁清单 ',
    reason: ' 供应产品命中制裁清单，强制升级为 P2 ',
  },
];

const globalConfigRead = (overrides: Partial<GlobalScoringConfigRead> = {}): GlobalScoringConfigRead => ({
  source: 'configured',
  enabled: true,
  effective: {
    severity_scores: {critical: 35, high: 28, medium: 20, low: 10},
    association_scores: {registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12, country: 8, industry: 12},
    p1_min: 85, p2_min: 65, p3_min: 40,
    forced_rules: GLOBAL_EFFECTIVE_RULES,
  },
  // 「恢复默认」只回写代码默认（不含维度追加的另外两条）
  defaults: {forced_rules: [SECURITY_CRITICAL_RULE], p1_min: 85},
  shadowed_by: {},
  forced_rules_shadowed_by: [],
  dropped_dimension_rules: [DROPPED_RULE],
  ...overrides,
});

/** 以指定 forced_rules 替换全局层生效列表的配置构造器（对抗性输入与空白回传用例共用） */
const withEffectiveRules = (rules: ForcedRuleRead[]): GlobalScoringConfigRead =>
  globalConfigRead({effective: {...globalConfigRead().effective, forced_rules: rules}});

const ruleEngineOptionsMock = (): RuleEngineOptions => ({
  match_columns: ['entity', 'location', 'product', 'country', 'industry'],
  event_types: [
    {value: 'weather', label: '天气'},
    {value: 'geopolitical', label: '地缘政治'},
    {value: 'trade_policy', label: '贸易政策'},
    {value: 'judicial', label: '司法'},
    {value: 'compliance', label: '合规'},
    {value: 'other', label: '其他'},
  ],
  event_subtypes: [{value: 'export_control', label: '出口管制'}],
});

const dimension = (overrides: Partial<MonitoringDimension> = {}): MonitoringDimension => ({
  id: 'geopolitical',
  name: '地缘政治与安全',
  icon: 'public',
  enabled: true,
  ruleId: 'geopolitical-v1',
  severityScores: {critical: 35, high: 28, medium: 20, low: 10},
  associationScores: {registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12, country: 8, industry: 12},
  thresholds: {p1: 85, p2: 65, p3: 40},
  matchColumns: ['entity', 'location'],
  eventTypes: ['geopolitical'],
  forcedRules: [DIMENSION_ONLY_RULE],
  contentItems: ['制裁'],
  dataSources: [],
  ...overrides,
});

// ---------------------------------------------------------------------------
// 壳层集成挂载（真实 context + 真实 api 客户端）
// ---------------------------------------------------------------------------

const renderShell = async (role: 'admin' | 'viewer' = 'admin', config: GlobalScoringConfigRead = globalConfigRead()) => {
  vi.spyOn(api, 'dimensionInputs').mockResolvedValue({
    declared_total: 0, declared_linked: 0, declared_enabled: 0, observed: [], has_input: false,
  });
  vi.spyOn(api, 'ruleEngineOptions').mockResolvedValue(ruleEngineOptionsMock());
  vi.spyOn(api, 'dimensionTrace').mockResolvedValue({available: false, event: null, routing: null, match: null, score: null, samples: []});
  vi.spyOn(api.globalConfig, 'get').mockResolvedValue(config);
  const updateSpy = vi.spyOn(api.globalConfig, 'update').mockResolvedValue(config);
  const resetSpy = vi.spyOn(api.globalConfig, 'reset').mockResolvedValue(config);

  render(
    <MemoryRouter>
      <RuleEngineView dimensions={[dimension()]} onToggleDimension={vi.fn()} onUpdateDimension={vi.fn()} role={role} />
    </MemoryRouter>,
  );

  if (role === 'admin') {
    fireEvent.click(screen.getByTestId('rule-engine-mode-toggle'));
    // 等待全局层加载完成（该提示只在拿到 GET /global-config 结果后渲染）
    await screen.findByTestId('forced-rules-notice');
    // 全局强制规则编辑器已迁入全局面板（rule-engine-global-layer，位于 rule-engine-tabpanel-global 内）：
    // 默认 Tab=维度视图，全局面板带 hidden，而 getByRole/toBeVisible 会忽略 hidden 元素。
    // 必须先切到「全局规则」Tab 让面板可见，后续 role 查询与可见性断言才成立。
    fireEvent.click(screen.getByTestId('rule-engine-tab-global'));
    // 行由「服务端快照到达」后的被动副作用播种，必须等播完再断言
    const expected = Array.isArray(config.effective.forced_rules) ? config.effective.forced_rules : [];
    await waitFor(() => expect(screen.queryAllByTestId('forced-rule-row')).toHaveLength(expected.length));
  }
  return {section: screen.getByTestId('rule-engine-forced-rules'), updateSpy, resetSpy};
};

/**
 * 独立挂载：不走壳组件，以显式全局配置直接渲染编辑器（同步播种行）。
 * 用于对抗性输入等需要与壳组件的「等待行数」逻辑解耦的断言。
 */
const renderEditorWithConfig = (
  config: GlobalScoringConfigRead,
  overrides: Partial<RuleEngineContextValue> = {},
) => {
  const value = contextValueFor(config, overrides);
  render(
    <RuleEngineContext.Provider value={value}>
      <RuleEngineForcedRules mode="config" />
    </RuleEngineContext.Provider>,
  );
  return value;
};

const editorSection = () => screen.getByTestId('rule-engine-forced-rules');
const allRows = () => screen.queryAllByTestId('forced-rule-row');
const rowsNamed = (name: string) => allRows().filter((row) => row.dataset.ruleName === name);
const rowNamed = (name: string): HTMLElement => {
  const matched = rowsNamed(name);
  if (matched.length !== 1) throw new Error(`期望恰好一行名为 ${name}，实际 ${matched.length} 行`);
  return matched[0]!;
};
const expandRow = (row: HTMLElement) => {
  fireEvent.click(within(row).getByTestId('forced-rule-expand'));
  return row;
};
const checkboxIn = (row: HTMLElement, group: string, label: string) =>
  within(within(row).getByRole('group', {name: group})).getByRole('checkbox', {name: label});
const saveButton = () => within(editorSection()).getByTestId('forced-rules-save');
const saveCalls = () => vi.mocked(api.globalConfig.update).mock.calls;
const submittedRules = (call = 0): ForcedRuleRead[] => {
  const payload = saveCalls()[call]?.[0] as GlobalScoringPatchPayload;
  return (payload.forced_rules ?? []) as ForcedRuleRead[];
};

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('全局强制规则编辑器：初始值与数据来源', () => {
  it('初始值来自 GET /global-config 全局层：维度追加规则可编辑、policy_industry_hit 不出现、维度层增量不出现', async () => {
    await renderShell();

    expect(api.globalConfig.get).toHaveBeenCalled();
    expect(allRows().map((row) => row.dataset.ruleName)).toEqual([
      'sanctions_entity_hit',
      'sanctions_geopolitical_entity_hit',
      'sanctions_product_hit',
    ]);
    // 维度层增量（selectedDim.forcedRules）绝不能作为初始值
    expect(rowsNamed(DIMENSION_ONLY_RULE.name)).toHaveLength(0);
    // policy_industry_hit 不在编辑列表中
    expect(rowsNamed('policy_industry_hit')).toHaveLength(0);

    // 维度追加规则是普通可编辑条目：展开后字段值原样来自全局层
    const geopoliticalRow = expandRow(rowNamed('sanctions_geopolitical_entity_hit'));
    expect(within(geopoliticalRow).getByLabelText('名称')).toHaveValue('sanctions_geopolitical_entity_hit');
    expect(within(geopoliticalRow).getByLabelText('原因')).toHaveValue('地缘政治事件命中供应商主体，强制提升为 P1');
    expect(within(geopoliticalRow).getByLabelText('强制等级')).toHaveValue('P1');
    expect(checkboxIn(geopoliticalRow, '事件类型', '地缘政治')).toBeChecked();
    expect(checkboxIn(geopoliticalRow, '匹配类型', '注册号')).toBeChecked();
  });

  it('收起态摘要把事件细类与匹配类型翻译为中文标签，不暴露原始枚举值', async () => {
    await renderShell();

    const row = rowNamed('sanctions_product_hit');
    expect(within(row).getByText(/事件细类：出口管制/)).toBeInTheDocument();
    expect(within(row).getByText(/匹配类型：产品/)).toBeInTheDocument();
    expect(within(row).queryByText(/export_control/)).not.toBeInTheDocument();
  });

  it('存在编辑控件、无「当前版本不可在界面编辑」只读文案', async () => {    await renderShell();

    expect(screen.queryByText(/当前版本不可在界面编辑/)).not.toBeInTheDocument();
    expect(screen.queryByText(/不可编辑/)).not.toBeInTheDocument();
    const section = editorSection();
    expect(within(section).getAllByTestId('forced-rule-expand').length).toBeGreaterThan(0);
    expect(within(section).getByTestId('forced-rules-save')).toBeInTheDocument();
    expect(within(section).getByRole('button', {name: /新增规则/})).toBeInTheDocument();
    expect(within(section).getByTestId('forced-rules-restore')).toBeInTheDocument();
  });

  it('提示命中即定级并记满分，且说明写操作有审计', async () => {
    await renderShell();

    expect(within(editorSection()).getByTestId('forced-rules-notice')).toHaveTextContent(
      '强制规则命中将直接定级为所选等级并记满分，绕过常规评分',
    );
    expect(within(editorSection()).getByText(/写入审计日志/)).toBeInTheDocument();
  });

  it('dropped_dimension_rules 的提示在保存按钮可见区域内可见且不可关闭', async () => {
    await renderShell();

    const warning = within(editorSection()).getByTestId('forced-rules-dropped-warning');
    // 配置面板有 motion 入场动画（初始 opacity:0，jsdom 下按真实帧推进到 1）：
    // 轮询等待到可见再断言，保持「保存前必然可见」的原始意图不变
    await waitFor(() => expect(warning).toBeVisible());
    expect(warning).toHaveTextContent('policy_industry_hit');
    // 位于保存按钮所在的动作区内（保存前必然可见）
    const actionArea = within(editorSection()).getByTestId('forced-rules-action-area');
    expect(actionArea).toContainElement(warning);
    expect(actionArea).toContainElement(saveButton());
    // 非关闭式：提示内没有任何可关闭/忽略控件
    expect(within(warning).queryAllByRole('button')).toHaveLength(0);
  });
});

describe('全局强制规则编辑器：对抗性输入（policy_industry_hit 真的出现在全局层）', () => {
  it('即使 GET.effective 包含 policy_industry_hit，也不渲染该规则，仍由 dropped_dimension_rules 警告披露', () => {
    renderEditorWithConfig(withEffectiveRules(ADVERSARIAL_EFFECTIVE_RULES));

    // (a) 编辑器只渲染三条合法全局条目，policy_industry_hit 被按名称硬排除
    expect(allRows().map((row) => row.dataset.ruleName)).toEqual([
      'sanctions_entity_hit',
      'sanctions_geopolitical_entity_hit',
      'sanctions_product_hit',
    ]);
    expect(rowsNamed('policy_industry_hit')).toHaveLength(0);

    // (c) 被排除的规则仍必须通过非关闭式警告告知管理员：保存全局表后它不再生效
    const warning = within(editorSection()).getByTestId('forced-rules-dropped-warning');
    expect(warning).toBeVisible();
    expect(warning).toHaveTextContent('policy_industry_hit');
  });

  it('即使 GET.effective 包含 policy_industry_hit，真实写入请求的载荷也绝不包含它', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 200, json: async () => globalConfigRead()});
    vi.stubGlobal('fetch', fetchMock);
    renderEditorWithConfig(withEffectiveRules(ADVERSARIAL_EFFECTIVE_RULES), {
      saveGlobalConfig: async (patch, confirm = false) => {
        await api.globalConfig.update(patch, confirm);
        return true;
      },
    });

    fireEvent.click(saveButton());

    // (b) 检查 PUT 真实请求体：只含三条合法规则，policy_industry_hit 绝不回传
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/rule-engine/global-config');
    expect(options.method).toBe('PUT');
    const body = JSON.parse(String(options.body)) as GlobalScoringPatchPayload;
    expect((body.forced_rules ?? []).map((rule) => rule.name)).toEqual([
      'sanctions_entity_hit',
      'sanctions_geopolitical_entity_hit',
      'sanctions_product_hit',
    ]);
    expect(String(options.body)).not.toContain('policy_industry_hit');
  });
});

describe('全局强制规则编辑器：增删改与排序', () => {
  it('新增规则后提交载荷包含该规则且字段完整，且只提交 forced_rules 字段', async () => {
    await renderShell();

    fireEvent.click(within(editorSection()).getByRole('button', {name: /新增规则/}));
    const rows = allRows();
    const newRow = rows[rows.length - 1]!; // 新增行默认展开
    fireEvent.change(within(newRow).getByLabelText('名称'), {target: {value: 'custom_export_control_hit'}});
    fireEvent.change(within(newRow).getByLabelText('说明'), {target: {value: '出口管制命中供应产品'}});
    fireEvent.change(within(newRow).getByLabelText('原因'), {target: {value: '命中出口管制清单，强制升级'}});
    fireEvent.change(within(newRow).getByLabelText('强制等级'), {target: {value: 'P2'}});
    fireEvent.click(checkboxIn(newRow, '事件类型', '贸易政策'));
    fireEvent.click(checkboxIn(newRow, '事件细类', '出口管制'));
    fireEvent.click(checkboxIn(newRow, '匹配类型', '产品'));

    fireEvent.click(saveButton());

    await waitFor(() => expect(api.globalConfig.update).toHaveBeenCalledTimes(1));
    const [payload, confirmFlag] = saveCalls()[0]!;
    expect(confirmFlag).toBe(false);
    expect(Object.keys(payload as GlobalScoringPatchPayload)).toEqual(['forced_rules']);
    const rules = submittedRules();
    expect(rules).toHaveLength(4);
    expect(rules[3]).toEqual({
      name: 'custom_export_control_hit',
      description: '出口管制命中供应产品',
      event_types: ['trade_policy'],
      event_subtypes: ['export_control'],
      match_types: ['product'],
      forced_level: 'P2',
      reason: '命中出口管制清单，强制升级',
    });
    // 原有全局条目原样回传（含两条维度追加规则），policy_industry_hit 绝不进入载荷
    expect(rules.map((rule) => rule.name)).toEqual([
      'sanctions_entity_hit', 'sanctions_geopolitical_entity_hit', 'sanctions_product_hit', 'custom_export_control_hit',
    ]);
    expect(JSON.stringify(payload)).not.toContain('policy_industry_hit');
    expect(JSON.stringify(payload)).not.toContain('dimension_only_should_not_appear');
  });

  it('编辑既有规则只改变该字段，提交按当前顺序回传', async () => {
    await renderShell();

    const row = expandRow(rowNamed('sanctions_product_hit'));
    fireEvent.change(within(row).getByLabelText('强制等级'), {target: {value: 'P1'}});
    fireEvent.click(checkboxIn(row, '事件类型', '地缘政治'));

    fireEvent.click(saveButton());

    await waitFor(() => expect(api.globalConfig.update).toHaveBeenCalledTimes(1));
    const rules = submittedRules();
    expect(rules[2]).toEqual({
      ...PRODUCT_DIMENSION_RULE,
      event_types: ['trade_policy', 'geopolitical'],
      forced_level: 'P1',
    });
    expect(rules[0]).toEqual(SECURITY_CRITICAL_RULE);
  });

  it('未编辑字段逐字回传（含首尾空白），提交载荷不做 trim 规范化', async () => {
    await renderShell('admin', withEffectiveRules(WHITESPACE_EFFECTIVE_RULES));

    // 不编辑任何字段直接保存：载荷必须与 GET 全局层逐字一致
    fireEvent.click(saveButton());

    await waitFor(() => expect(api.globalConfig.update).toHaveBeenCalledTimes(1));
    const rules = submittedRules();
    expect(rules).toEqual(WHITESPACE_EFFECTIVE_RULES);
    expect(rules[1]?.name).toBe('  sanctions_geopolitical_entity_hit  ');
    expect(rules[1]?.description).toBe('  地缘政治事件命中供应商主体  ');
    expect(rules[1]?.reason).toBe('  地缘政治事件命中供应商主体，强制提升为 P1  ');
    expect(rules[2]?.description).toBe(' 供应产品命中制裁清单 ');
    expect(rules[2]?.reason).toBe(' 供应产品命中制裁清单，强制升级为 P2 ');
  });

  it('排序改变提交顺序（顺序即优先级）', async () => {
    await renderShell();

    fireEvent.click(within(rowNamed('sanctions_product_hit')).getByTestId('forced-rule-move-up'));
    fireEvent.click(saveButton());

    await waitFor(() => expect(api.globalConfig.update).toHaveBeenCalledTimes(1));
    expect(submittedRules().map((rule) => rule.name)).toEqual([
      'sanctions_entity_hit', 'sanctions_product_hit', 'sanctions_geopolitical_entity_hit',
    ]);
  });

  it('删除需要二次确认，确认前不提交也不改变本地列表', async () => {
    await renderShell();

    const row = rowNamed('sanctions_product_hit');
    fireEvent.click(within(row).getByTestId('forced-rule-delete'));
    const deleteConfirm = within(row).getByTestId('forced-rule-delete-confirm');
    expect(deleteConfirm).toHaveTextContent('sanctions_product_hit');
    expect(rowsNamed('sanctions_product_hit')).toHaveLength(1);
    expect(api.globalConfig.update).not.toHaveBeenCalled();

    // 取消：行保留
    fireEvent.click(within(row).getByTestId('forced-rule-delete-confirm-cancel'));
    expect(rowsNamed('sanctions_product_hit')).toHaveLength(1);
    expect(api.globalConfig.update).not.toHaveBeenCalled();

    // 确认删除：行消失
    fireEvent.click(within(rowNamed('sanctions_product_hit')).getByTestId('forced-rule-delete'));
    fireEvent.click(within(rowNamed('sanctions_product_hit')).getByTestId('forced-rule-delete-confirm-submit'));
    expect(rowsNamed('sanctions_product_hit')).toHaveLength(0);
    expect(within(editorSection()).getByTestId('forced-rules-dirty-state')).toHaveTextContent('有未保存的更改');
  });
});

describe('全局强制规则编辑器：字段校验阻止提交', () => {
  it('名称为空或重复、原因为空、等级未选时以 role=alert 阻止提交', async () => {
    await renderShell();

    fireEvent.click(within(editorSection()).getByRole('button', {name: /新增规则/}));
    const newRow = allRows()[3]!;
    fireEvent.change(within(newRow).getByLabelText('名称'), {target: {value: 'sanctions_entity_hit'}});
    fireEvent.change(within(newRow).getByLabelText('原因'), {target: {value: '重复名称'}});
    fireEvent.change(within(newRow).getByLabelText('强制等级'), {target: {value: 'P3'}});
    fireEvent.click(saveButton());

    const alert = await within(editorSection()).findByTestId('forced-rules-validation');
    expect(alert).toHaveAttribute('role', 'alert');
    expect(alert).toHaveTextContent('规则名称不能重复');
    expect(api.globalConfig.update).not.toHaveBeenCalled();

    // 清空名称：提示变为「不能为空」，仍不提交
    fireEvent.change(within(allRows()[3]!).getByLabelText('名称'), {target: {value: '   '}});
    fireEvent.click(saveButton());
    expect(within(editorSection()).getByTestId('forced-rules-validation')).toHaveTextContent('规则名称不能为空');
    expect(api.globalConfig.update).not.toHaveBeenCalled();

    // 原因清空 + 等级未选：两条提示都出现
    fireEvent.change(within(allRows()[3]!).getByLabelText('原因'), {target: {value: ''}});
    fireEvent.change(within(allRows()[3]!).getByLabelText('强制等级'), {target: {value: ''}});
    fireEvent.click(saveButton());
    const alert2 = within(editorSection()).getByTestId('forced-rules-validation');
    expect(alert2).toHaveTextContent('必须为每条规则填写原因');
    expect(alert2).toHaveTextContent('必须为每条规则选择强制等级');
    expect(api.globalConfig.update).not.toHaveBeenCalled();

    // 修正后可以提交（校验不是永久锁死）
    fireEvent.change(within(allRows()[3]!).getByLabelText('名称'), {target: {value: 'fixed_rule'}});
    fireEvent.change(within(allRows()[3]!).getByLabelText('原因'), {target: {value: '修正后的原因'}});
    fireEvent.change(within(allRows()[3]!).getByLabelText('强制等级'), {target: {value: 'P4'}});
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.globalConfig.update).toHaveBeenCalledTimes(1));
    expect(within(editorSection()).queryByTestId('forced-rules-validation')).not.toBeInTheDocument();
  });

  it('把既有行改名为保留名称 policy_industry_hit：以 role=alert 阻止提交且不发任何写入请求', async () => {
    await renderShell();

    const row = expandRow(rowNamed('sanctions_product_hit'));
    fireEvent.change(within(row).getByLabelText('名称'), {target: {value: 'policy_industry_hit'}});
    // 行内必须即时标记非法，不能只在点击保存后才暴露
    expect(within(row).getByLabelText('名称')).toHaveAttribute('aria-invalid', 'true');

    fireEvent.click(saveButton());

    const alert = within(editorSection()).getByTestId('forced-rules-validation');
    expect(alert).toHaveAttribute('role', 'alert');
    expect(alert).toHaveTextContent('policy_industry_hit 为策略维度保留名称，不能作为全局强制规则');
    // 改名会移除原规则名，若不拦截会进入二次确认流程；保留名称校验必须早于确认与任何 API 调用
    expect(within(editorSection()).queryByTestId('forced-rules-confirm')).not.toBeInTheDocument();
    expect(api.globalConfig.update).not.toHaveBeenCalled();
  });

  it('新增行命名为保留名称（含首尾空白伪装）同样阻止提交', async () => {
    await renderShell();

    fireEvent.click(within(editorSection()).getByRole('button', {name: /新增规则/}));
    const newRow = allRows()[3]!;
    fireEvent.change(within(newRow).getByLabelText('名称'), {target: {value: '  policy_industry_hit  '}});
    fireEvent.change(within(newRow).getByLabelText('原因'), {target: {value: '试图以空白伪装绕过'}});
    fireEvent.change(within(newRow).getByLabelText('强制等级'), {target: {value: 'P1'}});
    fireEvent.click(saveButton());

    expect(within(editorSection()).getByTestId('forced-rules-validation')).toHaveTextContent(
      'policy_industry_hit 为策略维度保留名称，不能作为全局强制规则',
    );
    expect(api.globalConfig.update).not.toHaveBeenCalled();
  });
});

describe('全局强制规则编辑器：保留名称的载荷/草稿边界防线（校验之外的独立防线）', () => {
  /** 模拟「校验被绕过」：直接以含保留名称行的行状态喂给边界映射 */
  const rowsWithReservedName: Array<ForcedRuleRead & {rowId: string}> = [
    {...SECURITY_CRITICAL_RULE, rowId: 'r1'},
    {...DROPPED_RULE, rowId: 'r2'},
    {...DROPPED_RULE, rowId: 'r3', name: '  policy_industry_hit  '},
  ];

  it('边界映射在最后一刻丢弃保留名称行，PUT 真实请求体绝不包含它', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 200, json: async () => globalConfigRead()});
    vi.stubGlobal('fetch', fetchMock);

    const submitted = buildSubmittableRules(rowsWithReservedName);
    expect(submitted.map((rule) => rule.name)).toEqual(['sanctions_entity_hit']);
    expect(JSON.stringify(submitted)).not.toContain('policy_industry_hit');

    // 把边界输出接到真实 api 客户端：即使行状态里存在保留名称行，wire body 也绝不包含它
    await api.globalConfig.update({forced_rules: submitted}, false);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/rule-engine/global-config');
    expect(options.method).toBe('PUT');
    const body = JSON.parse(String(options.body)) as GlobalScoringPatchPayload;
    expect(body.forced_rules?.map((rule) => rule.name)).toEqual(['sanctions_entity_hit']);
    expect(String(options.body)).not.toContain('policy_industry_hit');
  });

  it('保留名称行存在时，写入 globalDraft 的预览草稿绝不含 policy_industry_hit', () => {
    let draft: GlobalScoringPatchPayload = {};
    const captured: ForcedRuleRead[][] = [];
    const setGlobalDraft = (action: React.SetStateAction<GlobalScoringPatchPayload>) => {
      draft = typeof action === 'function' ? action(draft) : action;
      captured.push((draft.forced_rules ?? []) as ForcedRuleRead[]);
    };
    renderEditorWithConfig(globalConfigRead(), {setGlobalDraft});

    // 初始有效状态已写入草稿（三条合法规则）
    const initialDraft = captured[captured.length - 1] ?? [];
    expect(initialDraft.map((rule) => rule.name)).toEqual([
      'sanctions_entity_hit', 'sanctions_geopolitical_entity_hit', 'sanctions_product_hit',
    ]);

    // 把一条行改名为保留名称：校验会拦截保存，草稿边界必须独立保证不外泄
    const row = expandRow(rowNamed('sanctions_product_hit'));
    fireEvent.change(within(row).getByLabelText('名称'), {target: {value: 'policy_industry_hit'}});

    expect(captured.length).toBeGreaterThan(0);
    expect(captured.every((rules) => rules.every((rule) => rule.name.trim() !== 'policy_industry_hit'))).toBe(true);
  });
});

describe('全局强制规则编辑器：viewer 只读', () => {
  const renderStandalone = (overrides: Partial<RuleEngineContextValue> = {}) => {
    const value = contextValueFor(globalConfigRead(), overrides);
    render(
      <RuleEngineContext.Provider value={value}>
        <RuleEngineForcedRules mode="config" />
      </RuleEngineContext.Provider>,
    );
    return value;
  };

  it('viewer 无任何编辑控件，只读摘要仍可读，无只读废弃文案', () => {
    renderStandalone({role: 'viewer'});

    const section = editorSection();
    expect(within(section).queryByRole('textbox')).not.toBeInTheDocument();
    expect(within(section).queryByRole('checkbox')).not.toBeInTheDocument();
    expect(within(section).queryByRole('combobox')).not.toBeInTheDocument();
    expect(within(section).queryByRole('button')).not.toBeInTheDocument();
    expect(within(section).queryByTestId('forced-rules-save')).not.toBeInTheDocument();
    expect(within(section).queryByTestId('forced-rules-restore')).not.toBeInTheDocument();
    expect(within(section).queryByTestId('forced-rule-delete')).not.toBeInTheDocument();
    expect(screen.queryByText(/当前版本不可在界面编辑/)).not.toBeInTheDocument();

    // 只读摘要（全局层三条规则）与只读说明仍在
    expect(section).toHaveTextContent('sanctions_entity_hit');
    expect(section).toHaveTextContent('sanctions_geopolitical_entity_hit');
    expect(section).toHaveTextContent('sanctions_product_hit');
    expect(within(section).getByText(/只读账号仅可查看/)).toBeInTheDocument();
    // 只读形态下也不展示保存前提示（无保存动作）
    expect(within(section).queryByTestId('forced-rules-dropped-warning')).not.toBeInTheDocument();
  });

  it('admin 存在编辑控件且提交走 globalConfig（不经维度层保存）', async () => {
    const value = renderStandalone();
    const section = editorSection();
    expect(within(section).getAllByRole('button').length).toBeGreaterThan(0);
    expandRow(rowNamed('sanctions_entity_hit'));
    expect(within(section).getAllByRole('textbox').length).toBeGreaterThan(0);
    expect(within(section).getAllByRole('checkbox').length).toBeGreaterThan(0);

    // 直接保存（无改动、无移除）→ 走 globalConfig.update 且不带确认
    fireEvent.click(saveButton());
    await waitFor(() => expect(value.saveGlobalConfig).toHaveBeenCalledTimes(1));
    expect(value.saveGlobalConfig).toHaveBeenCalledWith(
      {forced_rules: GLOBAL_EFFECTIVE_RULES},
      false,
    );
  });
});

describe('全局强制规则编辑器：恢复默认与二次确认', () => {
  it('恢复默认只回写默认 forced_rules（二次确认、带确认参数、不调用 reset、不动评分字段）', async () => {
    await renderShell();

    fireEvent.click(within(editorSection()).getByTestId('forced-rules-restore'));

    // 恢复默认同样需要二次确认，确认前不提交
    const confirmPanel = within(editorSection()).getByTestId('forced-rules-confirm');
    expect(confirmPanel).toHaveTextContent('恢复默认');
    expect(confirmPanel).toHaveTextContent('sanctions_geopolitical_entity_hit');
    expect(api.globalConfig.update).not.toHaveBeenCalled();

    // 取消不提交
    fireEvent.click(within(confirmPanel).getByTestId('forced-rules-confirm-cancel'));
    expect(api.globalConfig.update).not.toHaveBeenCalled();

    fireEvent.click(within(editorSection()).getByTestId('forced-rules-restore'));
    fireEvent.click(within(editorSection()).getByTestId('forced-rules-confirm-submit'));

    await waitFor(() => expect(api.globalConfig.update).toHaveBeenCalledTimes(1));
    const [payload, confirmFlag] = saveCalls()[0]!;
    expect(confirmFlag).toBe(true);
    expect(Object.keys(payload as GlobalScoringPatchPayload)).toEqual(['forced_rules']);
    expect(payload.forced_rules).toEqual([SECURITY_CRITICAL_RULE]);
    // 不改变全局评分字段
    const patch = payload as GlobalScoringPatchPayload;
    expect(patch.severity_scores).toBeUndefined();
    expect(patch.association_scores).toBeUndefined();
    expect(patch.p1_min).toBeUndefined();
    expect(patch.p2_min).toBeUndefined();
    expect(patch.strong_match_types).toBeUndefined();
    // 绝不使用 DELETE（会连带清空全局评分与阈值）
    expect(api.globalConfig.reset).not.toHaveBeenCalled();
  });

  it('默认列表读取失败时不提交并给出可访问错误（malformed_input）', async () => {
    render(
      <RuleEngineContext.Provider value={{
        ...contextValueFor(globalConfigRead({defaults: {p1_min: 85}})),
      }}>
        <RuleEngineForcedRules mode="config" />
      </RuleEngineContext.Provider>,
    );

    fireEvent.click(within(editorSection()).getByTestId('forced-rules-restore'));

    const alert = within(editorSection()).getByTestId('forced-rules-save-error');
    expect(alert).toHaveAttribute('role', 'alert');
    expect(alert).toHaveTextContent('无法读取系统默认强制规则');
    expect(within(editorSection()).queryByTestId('forced-rules-confirm')).not.toBeInTheDocument();
  });

  it('保存空表需要二次确认并以确认参数提交', async () => {
    await renderShell();

    // 逐条删除（每次删除都有独立二次确认）
    for (const name of ['sanctions_entity_hit', 'sanctions_geopolitical_entity_hit', 'sanctions_product_hit']) {
      fireEvent.click(within(rowNamed(name)).getByTestId('forced-rule-delete'));
      fireEvent.click(within(rowNamed(name)).getByTestId('forced-rule-delete-confirm-submit'));
    }
    expect(allRows()).toHaveLength(0);

    fireEvent.click(saveButton());
    const confirmPanel = within(editorSection()).getByTestId('forced-rules-confirm');
    expect(confirmPanel).toHaveTextContent('保存空表会禁用全部强制规则');
    expect(api.globalConfig.update).not.toHaveBeenCalled();

    fireEvent.click(within(confirmPanel).getByTestId('forced-rules-confirm-submit'));
    await waitFor(() => expect(api.globalConfig.update).toHaveBeenCalledTimes(1));
    const [payload, confirmFlag] = saveCalls()[0]!;
    expect(confirmFlag).toBe(true);
    expect(payload).toEqual({forced_rules: []});
  });

  it('移除安全关键规则 sanctions_entity_hit 需要二次确认并点名提示', async () => {
    await renderShell();

    fireEvent.click(within(rowNamed('sanctions_entity_hit')).getByTestId('forced-rule-delete'));
    fireEvent.click(within(rowNamed('sanctions_entity_hit')).getByTestId('forced-rule-delete-confirm-submit'));
    fireEvent.click(saveButton());

    const confirmPanel = within(editorSection()).getByTestId('forced-rules-confirm');
    expect(confirmPanel).toHaveTextContent('sanctions_entity_hit');
    expect(confirmPanel).toHaveTextContent('安全关键规则');
    expect(api.globalConfig.update).not.toHaveBeenCalled();

    fireEvent.click(within(confirmPanel).getByTestId('forced-rules-confirm-submit'));
    await waitFor(() => expect(api.globalConfig.update).toHaveBeenCalledTimes(1));
    const [payload, confirmFlag] = saveCalls()[0]!;
    expect(confirmFlag).toBe(true);
    expect(submittedRules().map((rule) => rule.name)).toEqual([
      'sanctions_geopolitical_entity_hit', 'sanctions_product_hit',
    ]);
  });

  it('无移除的保存不需要二次确认也不带确认参数', async () => {
    await renderShell();

    fireEvent.click(saveButton());

    await waitFor(() => expect(api.globalConfig.update).toHaveBeenCalledTimes(1));
    expect(within(editorSection()).queryByTestId('forced-rules-confirm')).not.toBeInTheDocument();
    expect(saveCalls()[0]?.[1]).toBe(false);
  });

  it('确认参数落在查询参数而不是请求体里', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 200, json: async () => globalConfigRead({source: 'default', enabled: false})});
    vi.stubGlobal('fetch', fetchMock);
    const saveGlobalConfig = vi.fn(async (patch: GlobalScoringPatchPayload, confirm = false) => {
      await api.globalConfig.update(patch, confirm);
      return true;
    });
    render(
      <RuleEngineContext.Provider value={contextValueFor(globalConfigRead(), {saveGlobalConfig})}>
        <RuleEngineForcedRules mode="config" />
      </RuleEngineContext.Provider>,
    );

    // 删除全部规则 → 保存空表（需要确认）
    for (const name of ['sanctions_entity_hit', 'sanctions_geopolitical_entity_hit', 'sanctions_product_hit']) {
      fireEvent.click(within(rowNamed(name)).getByTestId('forced-rule-delete'));
      fireEvent.click(within(rowNamed(name)).getByTestId('forced-rule-delete-confirm-submit'));
    }
    fireEvent.click(saveButton());
    fireEvent.click(within(editorSection()).getByTestId('forced-rules-confirm-submit'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/rule-engine/global-config?confirm_disable_forced_rules=true');
    expect(options.method).toBe('PUT');
    expect(JSON.parse(String(options.body))).toEqual({forced_rules: []});
    expect(String(options.body)).not.toContain('confirm_disable_forced_rules');
  });

  it('无移除的保存不带确认查询参数', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 200, json: async () => globalConfigRead()});
    vi.stubGlobal('fetch', fetchMock);
    render(
      <RuleEngineContext.Provider value={contextValueFor(globalConfigRead(), {
        saveGlobalConfig: async (patch, confirm = false) => {
          await api.globalConfig.update(patch, confirm);
          return true;
        },
      })}>
        <RuleEngineForcedRules mode="config" />
      </RuleEngineContext.Provider>,
    );

    fireEvent.click(saveButton());

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/rule-engine/global-config');
  });
});

describe('全局强制规则编辑器：失败与陈旧状态', () => {
  it('后端 422 时展示错误、不标记为已保存、不伪装成功', async () => {
    await renderShell();
    vi.mocked(api.globalConfig.update).mockRejectedValue(new Error('强制规则等级非法：P9'));

    const row = expandRow(rowNamed('sanctions_product_hit'));
    fireEvent.change(within(row).getByLabelText('原因'), {target: {value: '人为改动的原因'}});
    fireEvent.click(saveButton());

    await waitFor(() => expect(within(editorSection()).getByTestId('forced-rules-save-error')).toBeInTheDocument());
    const alert = within(editorSection()).getByTestId('forced-rules-save-error');
    expect(alert).toHaveAttribute('role', 'alert');
    expect(alert).toHaveTextContent('强制规则等级非法：P9');
    // 本地未标记已保存：仍是未保存状态，且页面无任何成功提示
    expect(within(editorSection()).getByTestId('forced-rules-dirty-state')).toHaveTextContent('有未保存的更改');
    expect(within(editorSection()).queryByText(/已保存|保存成功/)).not.toBeInTheDocument();
  });

  it('保存成功后列表回读服务端而不是保留本地草稿（stale_state）', async () => {
    await renderShell();
    // 服务端在保存后返回另一份全局层（例如另一名管理员同时改了规则）
    const serverAfterSave = globalConfigRead({
      effective: {...globalConfigRead().effective, forced_rules: [SECURITY_CRITICAL_RULE, GEOPOLITICAL_DIMENSION_RULE]},
    });
    vi.mocked(api.globalConfig.get).mockResolvedValue(serverAfterSave);

    fireEvent.click(within(rowNamed('sanctions_product_hit')).getByTestId('forced-rule-delete'));
    fireEvent.click(within(rowNamed('sanctions_product_hit')).getByTestId('forced-rule-delete-confirm-submit'));
    fireEvent.click(saveButton());
    fireEvent.click(within(editorSection()).getByTestId('forced-rules-confirm-submit'));

    await waitFor(() => expect(api.globalConfig.update).toHaveBeenCalledTimes(1));
    // 保存后重新拉取服务端全局层，界面以服务端为准
    await waitFor(() => expect(api.globalConfig.get).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(allRows().map((row) => row.dataset.ruleName)).toEqual([
      'sanctions_entity_hit', 'sanctions_geopolitical_entity_hit',
    ]));
    expect(within(editorSection()).getByTestId('forced-rules-dirty-state')).toHaveTextContent('与服务器一致');
  });
});

describe('全局强制规则编辑器：全局配置未加载时的草稿门控（F2 修复轮次 2）', () => {
  it('globalConfig 为 null 时保持非法且绝不写入 forced_rules: []；配置到达后才报告有效并写入真实行', async () => {
    const setGlobalDraft = vi.fn();
    const setGlobalDraftValidity = vi.fn();

    const {rerender} = render(
      <RuleEngineContext.Provider value={contextValueFor(globalConfigRead(), {
        globalConfig: null,
        globalConfigError: '网关超时',
        setGlobalDraft,
        setGlobalDraftValidity,
      })}>
        <RuleEngineForcedRules mode="config" />
      </RuleEngineContext.Provider>,
    );

    // 加载中/失败：绝不报告有效，也绝不把「尚未同步的空表」写成合法草稿
    // （否则解算预览会发送 forced_rules: []，静默禁用真实强制规则）
    expect(setGlobalDraftValidity).toHaveBeenCalledWith(false, expect.stringContaining('全局配置未加载'));
    expect(setGlobalDraft).not.toHaveBeenCalled();

    // 全局配置真正到达后才允许报告有效，并用加载到的真实行写入草稿
    rerender(
      <RuleEngineContext.Provider value={contextValueFor(globalConfigRead(), {setGlobalDraft, setGlobalDraftValidity})}>
        <RuleEngineForcedRules mode="config" />
      </RuleEngineContext.Provider>,
    );
    await waitFor(() => expect(setGlobalDraft).toHaveBeenCalled());
    expect(setGlobalDraftValidity).toHaveBeenLastCalledWith(true);
    const updater = setGlobalDraft.mock.calls[0]?.[0] as (current: GlobalScoringPatchPayload) => GlobalScoringPatchPayload;
    expect(updater({}).forced_rules?.map((rule) => rule.name)).toEqual([
      'sanctions_entity_hit', 'sanctions_geopolitical_entity_hit', 'sanctions_product_hit',
    ]);
  });
});

/** 独立挂载用的完整 context 值（不经过壳组件，用于 viewer 与查询参数用例） */
function contextValueFor(
  globalConfig: GlobalScoringConfigRead,
  overrides: Partial<RuleEngineContextValue> = {},
): RuleEngineContextValue {
  return {
    mode: 'config',
    role: 'admin',
    dimension: dimension(),
    draft: draftFromDimension(dimension()),
    updateDraft: vi.fn(),
    resetDraft: vi.fn(),
    saving: false,
    configError: '',
    configConflicts: [],
    saveDraft: vi.fn().mockResolvedValue(undefined),
    globalConfig,
    globalConfigError: '',
    refreshGlobalConfig: vi.fn().mockResolvedValue(undefined),
    globalDraft: {},
    setGlobalDraft: vi.fn(),
    globalDraftValid: true,
    globalDraftError: '',
    setGlobalDraftValidity: vi.fn(),
    // 受控 context：门控稳定且有效（强制规则编辑器本身不读本 getter，仅解算预览使用）
    readGlobalDraftGate: () => ({valid: true, error: '', revision: 0}),
    globalSaving: false,
    globalSaveError: '',
    saveGlobalConfig: vi.fn().mockResolvedValue(true),
    sample: defaultSampleEvent(),
    updateSample: vi.fn(),
    options: ruleEngineOptionsMock(),
    optionsError: '',
    ...overrides,
  };
}

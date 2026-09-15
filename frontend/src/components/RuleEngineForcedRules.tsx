import React from 'react';
import {ForcedRuleRead, GlobalScoringConfigRead} from '../api';
import {GLOBAL_DRAFT_NOT_LOADED_ERROR, RuleEngineMode, useRuleEngineContext} from './RuleEngineContext';

/** 匹配类型展示标签：与后端 MatchType 字面量一一对应（无独立选项接口，直接作为可选项） */
const MATCH_TYPE_LABELS: Record<string, string> = {
  registry_no: '注册号',
  legal_name: '法人全称',
  alias: '别名',
  site_distance: '地点距离',
  site_text: '地点文本',
  product: '产品',
  country: '国家',
  industry: '行业',
};

const MATCH_TYPE_OPTIONS = Object.keys(MATCH_TYPE_LABELS).map((value) => ({value, label: MATCH_TYPE_LABELS[value] ?? value}));

/** 强制等级：后端 Level 字面量 P1–P4；P1 最高 */
const FORCED_LEVEL_LABELS: Record<string, string> = {
  P1: 'P1（最高）',
  P2: 'P2（高）',
  P3: 'P3（中）',
  P4: 'P4（低）',
};
const FORCED_LEVEL_VALUES = Object.keys(FORCED_LEVEL_LABELS);

/** P1–P4 严格使用风险语义色（DESIGN.md「风险色专用规则」） */
const FORCED_LEVEL_CHIP_CLASSES: Record<string, string> = {
  P1: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  P2: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
  P3: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  P4: 'bg-slate-200 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
};

/** 安全关键强制规则：移除时在二次确认里点名强调（后端对任意生效规则的移除都要求显式确认） */
const SAFETY_CRITICAL_RULE_NAMES = new Set([
  'sanctions_entity_hit',
  'sanctions_geopolitical_entity_hit',
  'sanctions_product_hit',
]);

/**
 * 按名称硬排除的强制规则：`policy_industry_hit` 所属的 policy 维度未声明事件类型
 * （`event_types=()`；后端空元组表示匹配所有事件类型），全局化会造成过度升级。
 *
 * 不能只依赖数据来源：全局覆盖行的 `forced_rules` 会整体成为全局层生效值，
 * 而后端 `ForcedRuleUpdate` 仅要求名称非空，因此该名称可能在 GET 全局层真实出现。
 * 编辑器必须按名称硬排除——既不渲染，也不进入提交载荷。
 */
const EXCLUDED_FORCED_RULE_NAMES: ReadonlySet<string> = new Set(['policy_industry_hit']);

/**
 * 名称是否为保留名称：按 trim 比较，` policy_industry_hit ` 这类空白伪装同样视为保留。
 * 读层、表单校验、载荷/草稿边界三处共用同一判定，避免任一处口径不一致留下写侧旁路。
 */
function isReservedForcedRuleName(name: string): boolean {
  return EXCLUDED_FORCED_RULE_NAMES.has(name.trim());
}

const ICON_BUTTON_CLASSES =
  'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#c2c6d2] text-[#424751] ' +
  'transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-1 ' +
  'dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800';

const DANGER_ICON_BUTTON_CLASSES =
  'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#ffdad6] text-[#93000a] ' +
  'transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40 ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-1 ' +
  'dark:border-red-900/60 dark:text-red-300 dark:hover:bg-red-950/40';

const PRIMARY_BUTTON_CLASSES =
  'px-4 py-1.5 bg-[#004782] text-white rounded-lg text-[13px] font-bold shadow-sm transition-colors ' +
  'hover:bg-[#185fa5] disabled:opacity-60 disabled:cursor-not-allowed ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-2';

const SECONDARY_BUTTON_CLASSES =
  'px-3 py-1.5 border border-[#c2c6d2] text-[#424751] rounded-lg text-[13px] font-medium transition-colors ' +
  'hover:bg-slate-50 disabled:opacity-60 disabled:cursor-not-allowed ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-2 ' +
  'dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800';

const INPUT_CLASSES =
  'w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-medium mt-1 ' +
  'text-[#101d28] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] ' +
  'dark:bg-slate-900 dark:border-slate-700 dark:text-white';

/** 行内唯一标识：名称可重复、可编辑，绝不能用名称或下标作 React key */
interface ForcedRuleRow extends ForcedRuleRead {
  rowId: string;
}

interface RuleOption {
  value: string;
  label: string;
}

export interface RuleEngineForcedRulesProps {
  /** 由壳组件控制：仅配置态渲染 */
  mode: RuleEngineMode;
}

/**
 * 从全局层权威读模型安全提取强制规则列表。
 *
 * - `layer="effective"`：当前全局层生效值（编辑器初始值的唯一来源）
 * - `layer="defaults"`：「恢复默认」回写的代码默认值（含可全局化的维度追加）
 *
 * 两层都按 `EXCLUDED_FORCED_RULE_NAMES` 硬排除 `policy_industry_hit`：即使它出现在
 * GET 响应里，也绝不进入编辑器状态或提交载荷，仅由 `dropped_dimension_rules` 提示披露。
 *
 * 绝不用 `selectedDim.forcedRules`（维度层增量）初始化编辑器。
 */
export function readForcedRules(
  config: GlobalScoringConfigRead | null,
  layer: 'effective' | 'defaults' = 'effective',
): ForcedRuleRead[] {
  const raw = config?.[layer]?.forced_rules;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (typeof item !== 'object' || item === null) return [];
    const record = item as Record<string, unknown>;
    if (typeof record.name !== 'string') return [];
    if (isReservedForcedRuleName(record.name)) return [];
    const strings = (value: unknown): string[] =>
      Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];
    return [{
      name: record.name,
      description: typeof record.description === 'string' ? record.description : '',
      event_types: strings(record.event_types),
      event_subtypes: strings(record.event_subtypes),
      match_types: strings(record.match_types),
      forced_level: typeof record.forced_level === 'string' ? record.forced_level : 'P1',
      reason: typeof record.reason === 'string' ? record.reason : '',
    }];
  });
}

/** 已保存但不在选项接口内的值必须保留为可选项，避免编辑其它字段时静默丢数据 */
function withPreservedOptions(options: RuleOption[], selected: string[]): RuleOption[] {
  const known = new Set(options.map((option) => option.value));
  const extras = selected.filter((value) => !known.has(value)).map((value) => ({value, label: value}));
  return [...options, ...extras];
}

/**
 * 提交载荷的显式映射：只保留后端 ForcedRuleUpdate 的字段（绝不带上 rowId）。
 *
 * 未编辑字段必须逐字回传（全局层条目要求原样往返）：这里不做 trim 等规范化，
 * 否则服务端已有值里的首尾空白会被静默改写。校验只在临时副本上 trim
 * （见 `validationErrors` / `removedFrom`），不修改行状态。
 */
function toForcedRule(row: ForcedRuleRow): ForcedRuleRead {
  return {
    name: row.name,
    description: row.description,
    event_types: [...row.event_types],
    event_subtypes: [...row.event_subtypes],
    match_types: [...row.match_types],
    forced_level: row.forced_level,
    reason: row.reason,
  };
}

/**
 * 提交载荷与预览草稿的边界映射：保留名称行在最后一刻被丢弃，是表单校验之外的独立防线。
 *
 * 为什么需要：`readForcedRules` 只能在读取时排除 `policy_industry_hit`；管理员仍可能把
 * 任意行改名为它（或新增同名行）再保存。即使未来有人误删校验分支、或通过其它路径把该行
 * 注入 rows 状态，这一层也保证该名称永远不进入 PUT body 与 `globalDraft`（预览草稿）。
 */
export function buildSubmittableRules(rows: ReadonlyArray<ForcedRuleRow>): ForcedRuleRead[] {
  return rows.filter((row) => !isReservedForcedRuleName(row.name)).map(toForcedRule);
}

/** 顺序敏感签名：数组越靠前越先命中（apply_forced_rules 命中即返回），排序属于语义变更 */
function ruleSignature(rules: ForcedRuleRead[]): string {
  return JSON.stringify(rules.map((rule) => [
    rule.name, rule.description, rule.event_types, rule.event_subtypes, rule.match_types, rule.forced_level, rule.reason,
  ]));
}

const toggleValue = (values: string[], value: string): string[] =>
  values.includes(value) ? values.filter((item) => item !== value) : [...values, value];

interface RuleChipGroupProps {
  legend: string;
  hint?: string;
  options: RuleOption[];
  value: string[];
  disabled?: boolean;
  onToggle: (value: string) => void;
}

const RuleChipGroup: React.FC<RuleChipGroupProps> = ({legend, hint, options, value, disabled, onToggle}) => (
  <fieldset className="min-w-0 border-0 p-0 m-0">
    <legend className="text-[11px] font-bold text-[#424751] dark:text-slate-300">{legend}</legend>
    <div className="flex flex-wrap gap-1.5 mt-1">
      {options.length === 0 && <span className="text-[11px] text-slate-400 dark:text-slate-500">暂无可选项</span>}
      {options.map((option) => {
        const checked = value.includes(option.value);
        return (
          <label
            key={option.value}
            className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] transition-colors ${
              checked
                ? 'bg-[#004782] border-[#004782] text-white'
                : 'bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300'
            } ${disabled ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'}`}
          >
            <input
              type="checkbox"
              checked={checked}
              disabled={disabled}
              onChange={() => onToggle(option.value)}
              className="sr-only"
            />
            <span aria-hidden="true" className="material-symbols-outlined text-[14px]">
              {checked ? 'check_box' : 'check_box_outline_blank'}
            </span>
            {option.label}
          </label>
        );
      })}
    </div>
    {hint && <p className="text-[10px] text-slate-500 dark:text-slate-400 mt-1">{hint}</p>}
  </fieldset>
);

interface RuleTextFieldProps {
  id: string;
  label: string;
  value: string;
  placeholder?: string;
  invalid: boolean;
  onChange: (value: string) => void;
}

const RuleTextField: React.FC<RuleTextFieldProps> = ({id, label, value, placeholder, invalid, onChange}) => (
  <div className="min-w-0">
    <label htmlFor={id} className="text-[11px] font-bold text-[#424751] dark:text-slate-300">{label}</label>
    <input
      id={id}
      type="text"
      value={value}
      placeholder={placeholder}
      aria-invalid={invalid}
      onChange={(event) => onChange(event.target.value)}
      className={`${INPUT_CLASSES} ${invalid ? 'border-[#ff3b30] dark:border-red-500' : ''}`}
    />
  </div>
);

interface PlainRuleCardProps {
  rule: ForcedRuleRead;
  eventLabel: (value: string) => string;
  subtypeLabel: (value: string) => string;
}

/** 只读摘要：viewer 唯一可见形态，也用于配置态的收起态 */
const PlainRuleCard: React.FC<PlainRuleCardProps> = ({rule, eventLabel, subtypeLabel}) => (
  <>
    {rule.description !== '' && <p className="text-[11px] text-slate-600 dark:text-slate-300">{rule.description}</p>}
    <div className="text-[11px] text-slate-500 dark:text-slate-400 space-y-0.5">
      <p>事件类型：{rule.event_types.length === 0 ? '全部（未限定）' : rule.event_types.map(eventLabel).join('、')}</p>
      {rule.event_subtypes.length > 0 && <p>事件细类：{rule.event_subtypes.map(subtypeLabel).join('、')}</p>}
      <p>匹配类型：{rule.match_types.length === 0 ? '全部（未限定）' : rule.match_types.map((item) => MATCH_TYPE_LABELS[item] ?? item).join('、')}</p>
      {rule.reason !== '' && <p>原因：{rule.reason}</p>}
    </div>
  </>
);

/**
 * 配置态-全局强制规则编辑器（挂载点冻结于 todo 4）。
 *
 * 数据来源写死：初始值与保存后回填都只来自 `GET /global-config` 的**全局层**
 * （`effective` = 代码默认 ∪ 可全局化维度追加，已排除 policy_industry_hit；不含维度增量），
 * 绝不用 `selectedDim.forcedRules`。
 *
 * 保存走 `globalConfig.update`（PUT 合并语义，只提交 forced_rules，不会清空全局评分/阈值）；
 * 「恢复默认」用 `globalConfig.update` 只回写 `defaults.forced_rules`，**不调用** `globalConfig.reset`
 * （DELETE 会连带删除全局评分与阈值）。任何会移除当前生效规则的提交都带
 * `?confirm_disable_forced_rules=true`（由壳组件传入的 saveGlobalConfig 落到查询参数）。
 */
export const RuleEngineForcedRules: React.FC<RuleEngineForcedRulesProps> = ({mode}) => {
  const {
    role,
    globalConfig,
    globalConfigError,
    refreshGlobalConfig,
    globalSaving,
    globalSaveError,
    saveGlobalConfig,
    options,
    setGlobalDraft,
    setGlobalDraftValidity,
  } = useRuleEngineContext();

  const canEdit = role === 'admin';
  const serverRules = React.useMemo(() => readForcedRules(globalConfig, 'effective'), [globalConfig]);
  const defaultRules = React.useMemo(() => readForcedRules(globalConfig, 'defaults'), [globalConfig]);
  const droppedRules = React.useMemo(() => {
    const raw = globalConfig?.dropped_dimension_rules;
    if (!Array.isArray(raw)) return [] as string[];
    return raw.flatMap((item) => {
      if (typeof item !== 'object' || item === null) return [];
      const name = (item as Record<string, unknown>).name;
      return typeof name === 'string' && name !== '' ? [name] : [];
    });
  }, [globalConfig]);

  const rowIdRef = React.useRef(0);
  const nextRowId = React.useCallback(() => {
    rowIdRef.current += 1;
    return `fr${rowIdRef.current}`;
  }, []);
  const seedRows = React.useCallback(
    (rules: ForcedRuleRead[]): ForcedRuleRow[] => rules.map((rule) => ({
      ...rule,
      event_types: [...rule.event_types],
      event_subtypes: [...rule.event_subtypes],
      match_types: [...rule.match_types],
      rowId: nextRowId(),
    })),
    [nextRowId],
  );

  const [rows, setRows] = React.useState<ForcedRuleRow[]>(() => seedRows(serverRules));
  /**
   * rows 是否已由「真实加载到的 globalConfig」播种。
   *
   * 挂载时 globalConfig 已就绪则 useState 初始化器已按真实行播种，直接为 true；
   * globalConfig 为 null（加载中/失败）时初始 rows 为空，只是「尚未同步」——
   * 在服务端快照到达并写入 rows 之前，草稿门控必须保持非法（F2 窗口 1），
   * 否则空表会被当成合法草稿（forced_rules: []）发给解算预览。
   */
  const [rowsSeeded, setRowsSeeded] = React.useState(() => globalConfig !== null);
  const [dirty, setDirty] = React.useState(false);
  const [expandedIds, setExpandedIds] = React.useState<ReadonlySet<string>>(() => new Set<string>());
  const [pendingDeleteId, setPendingDeleteId] = React.useState<string | null>(null);
  const [pendingSubmit, setPendingSubmit] = React.useState<
    {kind: 'save' | 'restore'; rules: ForcedRuleRead[]; removed: string[]; criticalRemoved: string[]} | null
  >(null);
  const [showValidation, setShowValidation] = React.useState(false);
  const [actionError, setActionError] = React.useState('');
  const syncedFromRef = React.useRef<GlobalScoringConfigRead | null>(null);

  // 初始值与保存后的回填：只在服务端快照变化且本地无未保存修改时重读，保证列表始终反映服务端
  React.useEffect(() => {
    if (!globalConfig || syncedFromRef.current === globalConfig) return;
    if (dirty) return;
    syncedFromRef.current = globalConfig;
    setRows(seedRows(serverRules));
    setRowsSeeded(true);
    setDirty(false);
    setShowValidation(false);
  }, [dirty, globalConfig, seedRows, serverRules]);

  const eventTypeLabel = React.useCallback(
    (value: string) => options.event_types.find((option) => option.value === value)?.label ?? value,
    [options.event_types],
  );

  const eventSubtypeLabel = React.useCallback(
    (value: string) => options.event_subtypes.find((option) => option.value === value)?.label ?? value,
    [options.event_subtypes],
  );

  const validationErrors = React.useMemo(() => {
    const errors: string[] = [];
    if (rows.some((row) => row.name.trim() === '')) errors.push('规则名称不能为空');
    // 保留名称属于写侧旁路：改名或新增都可能让策略维度规则变成全局强制规则（过度升级）
    const reservedNames = [...EXCLUDED_FORCED_RULE_NAMES].filter(
      (name) => rows.some((row) => row.name.trim() === name),
    );
    if (reservedNames.length > 0) {
      errors.push(`${reservedNames.join('、')} 为策略维度保留名称，不能作为全局强制规则`);
    }
    const names = rows.map((row) => row.name.trim());
    if (new Set(names).size !== names.length) errors.push('规则名称不能重复（后端按名称识别强制规则）');
    if (rows.some((row) => row.reason.trim() === '')) errors.push('必须为每条规则填写原因');
    if (rows.some((row) => !FORCED_LEVEL_VALUES.includes(row.forced_level))) errors.push('必须为每条规则选择强制等级（P1–P4）');
    return errors;
  }, [rows]);

  const removedFrom = React.useCallback((next: ForcedRuleRead[]): string[] => {
    const submitted = new Set(next.map((rule) => rule.name.trim()));
    return serverRules.map((rule) => rule.name).filter((name) => !submitted.has(name.trim()));
  }, [serverRules]);

  const requiresConfirmation = React.useCallback(
    (next: ForcedRuleRead[]): boolean => next.length === 0 || removedFrom(next).length > 0,
    [removedFrom],
  );

  // 冻结的共享点：把当前（可能未保存的）强制规则表并入壳组件持有的全局草稿，供解算预览使用。
  // 合法时经 buildSubmittableRules 边界过滤后写入（保留名称绝不进入预览草稿）；
  // 非法时不上报旧草稿可用——显式标记为非法，让解算预览被禁用并给出可访问错误，
  // 绝不用上一次合法的旧规则冒充当前表格（缺陷 C）。
  React.useEffect(() => {
    // F2 窗口 1：globalConfig 尚未加载（加载中或加载失败）时 rows 为空只是「尚未同步」，
    // 不是「服务端就是空表」。此时必须保持非法且跳过 globalDraft 写入，
    // 否则解算预览会把 forced_rules: [] 当成合法草稿发出，静默禁用真实强制规则。
    if (globalConfig === null || !rowsSeeded) {
      setGlobalDraftValidity(false, GLOBAL_DRAFT_NOT_LOADED_ERROR);
      return;
    }
    if (validationErrors.length > 0) {
      setGlobalDraftValidity(false, validationErrors.join('；'));
      return;
    }
    setGlobalDraftValidity(true);
    setGlobalDraft((current) => ({...current, forced_rules: buildSubmittableRules(rows)}));
  }, [globalConfig, rowsSeeded, rows, setGlobalDraft, setGlobalDraftValidity, validationErrors]);

  const markEdited = () => {
    setDirty(true);
    setActionError('');
  };

  const updateRow = (rowId: string, patch: Partial<ForcedRuleRead>) => {
    setRows((current) => current.map((row) => (row.rowId === rowId ? {...row, ...patch} : row)));
    markEdited();
  };

  const toggleRowValues = (rowId: string, field: 'event_types' | 'event_subtypes' | 'match_types', value: string) => {
    setRows((current) => current.map((row) => {
      if (row.rowId !== rowId) return row;
      if (field === 'event_types') return {...row, event_types: toggleValue(row.event_types, value)};
      if (field === 'event_subtypes') return {...row, event_subtypes: toggleValue(row.event_subtypes, value)};
      return {...row, match_types: toggleValue(row.match_types, value)};
    }));
    markEdited();
  };

  const addRow = () => {
    const rowId = nextRowId();
    setRows((current) => [...current, {
      rowId, name: '', description: '', event_types: [], event_subtypes: [], match_types: [], forced_level: '', reason: '',
    }]);
    // 新增行默认展开（等级必选，未选等级时校验会阻止提交）
    setExpandedIds((current) => new Set(current).add(rowId));
    markEdited();
  };

  const removeRow = (rowId: string) => {
    setRows((current) => current.filter((row) => row.rowId !== rowId));
    setExpandedIds((current) => {
      const next = new Set(current);
      next.delete(rowId);
      return next;
    });
    setPendingDeleteId(null);
    markEdited();
  };

  const moveRow = (index: number, delta: -1 | 1) => {
    setRows((current) => {
      const target = index + delta;
      const moved = current[index];
      if (target < 0 || target >= current.length || !moved) return current;
      const next = current.filter((_, position) => position !== index);
      next.splice(target, 0, moved);
      return next;
    });
    markEdited();
  };

  const toggleExpanded = (rowId: string) => {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(rowId)) next.delete(rowId);
      else next.add(rowId);
      return next;
    });
  };

  const submit = React.useCallback(async (next: ForcedRuleRead[], confirm: boolean): Promise<boolean> => {
    setActionError('');
    const ok = await saveGlobalConfig({forced_rules: next}, confirm);
    if (ok) {
      // 成功仅由「服务端接受该载荷 + 壳组件回读全局配置」判定，不做任何本地成功提示
      setDirty(false);
      setPendingSubmit(null);
      setShowValidation(false);
    }
    return ok;
  }, [saveGlobalConfig]);

  const openConfirmation = (kind: 'save' | 'restore', next: ForcedRuleRead[]) => {
    const removed = removedFrom(next);
    setPendingSubmit({
      kind,
      rules: next,
      removed,
      criticalRemoved: removed.filter((name) => SAFETY_CRITICAL_RULE_NAMES.has(name)),
    });
  };

  const handleSave = async () => {
    setShowValidation(true);
    setActionError('');
    if (validationErrors.length > 0) return;
    // 校验通过才组装载荷；仍走边界映射，作为「校验被绕过」时的兜底
    const next = buildSubmittableRules(rows);
    if (requiresConfirmation(next)) {
      openConfirmation('save', next);
      return;
    }
    await submit(next, false);
  };

  const handleRestore = () => {
    setActionError('');
    if (!Array.isArray(globalConfig?.defaults.forced_rules)) {
      setActionError('无法读取系统默认强制规则，已取消「恢复默认」。请刷新全局配置后重试。');
      return;
    }
    openConfirmation('restore', defaultRules);
  };

  const confirmPending = async () => {
    if (!pendingSubmit) return;
    await submit(pendingSubmit.rules, requiresConfirmation(pendingSubmit.rules));
  };

  if (mode !== 'config') return null;

  const sectionClasses = 'rounded-xl bg-[#f7f9ff] dark:bg-slate-950/40 border border-slate-200 dark:border-slate-800 p-3';

  if (globalConfigError && globalConfig === null) {
    return (
      <section role="alert" data-testid="rule-engine-forced-rules" data-mode={mode} className={`${sectionClasses} text-[12px] text-red-700 dark:text-red-300`}>
        <p>全局强制规则加载失败：{globalConfigError}</p>
        <button type="button" onClick={() => void refreshGlobalConfig()} className={`${SECONDARY_BUTTON_CLASSES} mt-2`}>
          重试
        </button>
      </section>
    );
  }

  if (globalConfig === null) {
    return (
      <section data-testid="rule-engine-forced-rules" data-mode={mode} className={sectionClasses}>
        <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300 mb-2">强制规则</h3>
        <p className="flex items-center gap-1 text-[11px] text-slate-400 dark:text-slate-500">
          <span className="material-symbols-outlined animate-spin text-[12px] leading-none" aria-hidden="true">progress_activity</span>
          <span className="sr-only">全局强制规则加载中…</span>
        </p>
      </section>
    );
  }

  const signature = ruleSignature(rows.map(toForcedRule));
  const matchesDefaults = signature === ruleSignature(defaultRules);
  const dirtyLabel = dirty ? '有未保存的更改' : globalConfigError ? '尚未与服务器同步' : '与服务器一致';

  return (
    <section data-testid="rule-engine-forced-rules" data-mode={mode} className={sectionClasses}>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300">
          强制规则（全局表）
          {canEdit && <span className="ml-1 font-normal text-slate-400 dark:text-slate-500">· 顺序即优先级，越靠前越先命中</span>}
        </h3>
        <span className="text-[10px] text-slate-400 dark:text-slate-500">
          全局层 {globalConfig.source === 'configured' ? '已自定义' : '默认'}
          {canEdit && ` · ${matchesDefaults ? '与系统默认一致' : '与系统默认不同'}`}
          {canEdit && ` · ${dirtyLabel}`}
        </span>
      </div>

      <p data-testid="forced-rules-notice" className="text-[11px] text-slate-600 dark:text-slate-300">
        强制规则命中将直接定级为所选等级并记满分，绕过常规评分。
      </p>
      <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
        {canEdit ? '保存、删除与恢复默认都会写入审计日志（含操作人）。' : '只读账号仅可查看，不能编辑全局强制规则。'}
      </p>

      {globalConfigError !== '' && (
        <p role="alert" className="mt-2 text-[11px] text-red-700 dark:text-red-300">全局配置刷新失败：{globalConfigError}</p>
      )}

      {rows.length === 0 ? (
        <p className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">
          当前全局层无强制规则{canEdit ? '；保存空表会禁用全部强制规则，需要二次确认。' : '。'}
        </p>
      ) : (
        <div className="mt-2 space-y-2">
          {rows.map((row, index) => {
            const expanded = canEdit && expandedIds.has(row.rowId);
            const nameInvalid = row.name.trim() === ''
              || isReservedForcedRuleName(row.name)
              || rows.filter((item) => item.name.trim() === row.name.trim()).length > 1;
            const reasonInvalid = row.reason.trim() === '';
            const levelInvalid = !FORCED_LEVEL_VALUES.includes(row.forced_level);
            const displayName = row.name.trim() === '' ? '未命名规则' : row.name;
            return (
              <div
                key={row.rowId}
                data-testid="forced-rule-row"
                data-rule-name={row.name}
                className="rounded-lg bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 p-2.5 space-y-1"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="text-[10px] font-mono font-bold text-slate-400">{index + 1}</span>
                    <span className="truncate text-[12px] font-bold font-mono text-[#101d28] dark:text-white">{displayName}</span>
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${FORCED_LEVEL_CHIP_CLASSES[row.forced_level] ?? 'bg-slate-200 text-slate-600 dark:bg-slate-800 dark:text-slate-300'}`}>
                      {levelInvalid ? '未选等级' : row.forced_level}
                    </span>
                  </div>
                  {canEdit && (
                    <div className="flex shrink-0 items-center gap-1">
                      <button type="button" data-testid="forced-rule-move-up" aria-label={`上移规则 ${displayName}`} disabled={index === 0} onClick={() => moveRow(index, -1)} className={ICON_BUTTON_CLASSES}>
                        <span className="material-symbols-outlined text-[16px]" aria-hidden="true">arrow_upward</span>
                      </button>
                      <button type="button" data-testid="forced-rule-move-down" aria-label={`下移规则 ${displayName}`} disabled={index === rows.length - 1} onClick={() => moveRow(index, 1)} className={ICON_BUTTON_CLASSES}>
                        <span className="material-symbols-outlined text-[16px]" aria-hidden="true">arrow_downward</span>
                      </button>
                      <button
                        type="button"
                        data-testid="forced-rule-expand"
                        aria-expanded={expanded}
                        aria-controls={`forced-rule-panel-${row.rowId}`}
                        onClick={() => toggleExpanded(row.rowId)}
                        className={SECONDARY_BUTTON_CLASSES}
                      >
                        {expanded ? '收起' : '编辑'}
                      </button>
                      <button
                        type="button"
                        data-testid="forced-rule-delete"
                        aria-label={`删除规则 ${displayName}`}
                        onClick={() => setPendingDeleteId(pendingDeleteId === row.rowId ? null : row.rowId)}
                        className={DANGER_ICON_BUTTON_CLASSES}
                      >
                        <span className="material-symbols-outlined text-[16px]" aria-hidden="true">delete</span>
                      </button>
                    </div>
                  )}
                </div>

                {pendingDeleteId === row.rowId && canEdit && (
                  <div role="alert" data-testid="forced-rule-delete-confirm" className="rounded-lg border border-[#ffdad6] bg-red-50 p-2 space-y-2 dark:border-red-900/60 dark:bg-red-950/30">
                    <p className="text-[11px] font-bold text-[#93000a] dark:text-red-300">
                      确认删除规则「{displayName}」？删除后需保存才会生效，保存时会再次确认。
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" data-testid="forced-rule-delete-confirm-submit" onClick={() => removeRow(row.rowId)} className={PRIMARY_BUTTON_CLASSES}>
                        确认删除
                      </button>
                      <button type="button" data-testid="forced-rule-delete-confirm-cancel" onClick={() => setPendingDeleteId(null)} className={SECONDARY_BUTTON_CLASSES}>
                        取消
                      </button>
                    </div>
                  </div>
                )}

                {!expanded && <PlainRuleCard rule={row} eventLabel={eventTypeLabel} subtypeLabel={eventSubtypeLabel} />}

                {expanded && (
                  <div id={`forced-rule-panel-${row.rowId}`} data-testid="forced-rule-panel" className="space-y-3 border-t border-slate-100 dark:border-slate-800 pt-2">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      <RuleTextField
                        id={`forced-rule-name-${row.rowId}`}
                        label="名称"
                        value={row.name}
                        placeholder="例如 sanctions_entity_hit"
                        invalid={nameInvalid}
                        onChange={(value) => updateRow(row.rowId, {name: value})}
                      />
                      <div className="min-w-0">
                        <label htmlFor={`forced-rule-reason-${row.rowId}`} className="text-[11px] font-bold text-[#424751] dark:text-slate-300">原因</label>
                        <input
                          id={`forced-rule-reason-${row.rowId}`}
                          type="text"
                          value={row.reason}
                          placeholder="写入评分明细的强制升级原因"
                          aria-invalid={reasonInvalid}
                          onChange={(event) => updateRow(row.rowId, {reason: event.target.value})}
                          className={`${INPUT_CLASSES} ${reasonInvalid ? 'border-[#ff3b30] dark:border-red-500' : ''}`}
                        />
                      </div>
                      <RuleTextField
                        id={`forced-rule-description-${row.rowId}`}
                        label="说明"
                        value={row.description}
                        placeholder="给人看的一句话说明"
                        invalid={false}
                        onChange={(value) => updateRow(row.rowId, {description: value})}
                      />
                      <div className="min-w-0">
                        <label htmlFor={`forced-rule-level-${row.rowId}`} className="text-[11px] font-bold text-[#424751] dark:text-slate-300">强制等级</label>
                        <select
                          id={`forced-rule-level-${row.rowId}`}
                          value={row.forced_level}
                          aria-invalid={levelInvalid}
                          onChange={(event) => updateRow(row.rowId, {forced_level: event.target.value})}
                          className={`${INPUT_CLASSES} ${levelInvalid ? 'border-[#ff3b30] dark:border-red-500' : ''}`}
                        >
                          <option value="">请选择等级</option>
                          {FORCED_LEVEL_VALUES.map((level) => <option key={level} value={level}>{FORCED_LEVEL_LABELS[level]}</option>)}
                        </select>
                      </div>
                    </div>

                    <RuleChipGroup
                      legend="事件类型"
                      hint="不选择任何事件类型 = 匹配所有事件类型（容易被误伤，请谨慎留空）"
                      options={withPreservedOptions(options.event_types, row.event_types)}
                      value={row.event_types}
                      onToggle={(value) => toggleRowValues(row.rowId, 'event_types', value)}
                    />
                    <RuleChipGroup
                      legend="事件细类"
                      options={withPreservedOptions(options.event_subtypes, row.event_subtypes)}
                      value={row.event_subtypes}
                      onToggle={(value) => toggleRowValues(row.rowId, 'event_subtypes', value)}
                      hint="留空表示不限定细类"
                    />
                    <RuleChipGroup
                      legend="匹配类型"
                      hint="不选择任何匹配类型 = 匹配所有匹配类型"
                      options={withPreservedOptions(MATCH_TYPE_OPTIONS, row.match_types)}
                      value={row.match_types}
                      onToggle={(value) => toggleRowValues(row.rowId, 'match_types', value)}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {canEdit && (
        <div className="mt-3 space-y-2" data-testid="forced-rules-action-area">
          {droppedRules.length > 0 && (
            <div
              role="note"
              data-testid="forced-rules-dropped-warning"
              className="rounded-lg border border-amber-300 bg-amber-50 p-2.5 text-[11px] text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
            >
              <p className="font-bold">保存本表前请确认：以下维度规则的维度未声明具体事件类型，无法全局化，保存后将不再生效。</p>
              <ul className="mt-1 list-disc pl-4 space-y-0.5">
                {droppedRules.map((name) => (
                  <li key={name}><span className="font-mono font-bold">{name}</span>：其所属维度的事件类型为空（空事件类型表示匹配所有类型），保存全局强制规则表会整体跳过维度追加规则。</li>
                ))}
              </ul>
            </div>
          )}

          {(actionError !== '' || globalSaveError !== '') && (
            <div role="alert" data-testid="forced-rules-save-error" className="rounded-lg border border-[#ffdad6] bg-red-50 p-2.5 text-[11px] text-[#93000a] dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
              <p className="font-bold">强制规则未保存：{actionError !== '' ? actionError : globalSaveError}</p>
              <p>服务端未接受该载荷，本地列表仍标记为未保存；请修正后重试。</p>
            </div>
          )}

          {showValidation && validationErrors.length > 0 && (
            <div role="alert" data-testid="forced-rules-validation" className="rounded-lg border border-[#ffdad6] bg-red-50 p-2.5 text-[11px] text-[#93000a] dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
              <p className="font-bold">请先修正以下问题，再保存：</p>
              <ul className="mt-1 list-disc pl-4 space-y-0.5">
                {validationErrors.map((error) => <li key={error}>{error}</li>)}
              </ul>
            </div>
          )}

          {pendingSubmit && (
            <div role="alert" data-testid="forced-rules-confirm" className="rounded-lg border border-amber-300 bg-amber-50 p-2.5 space-y-2 text-[11px] text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
              <p className="font-bold">
                二次确认：{pendingSubmit.kind === 'restore' ? '恢复默认会覆盖当前全局强制规则表' : '本次保存会减少当前生效的强制规则'}
              </p>
              <p>
                {pendingSubmit.kind === 'restore'
                  ? `将把全局强制规则替换为系统默认的 ${pendingSubmit.rules.length} 条规则。`
                  : pendingSubmit.rules.length === 0
                    ? '保存空表会禁用全部强制规则：此后不再有事件自动升级为 P1/P2。'
                    : `保存后全局强制规则表剩余 ${pendingSubmit.rules.length} 条。`}
                {pendingSubmit.removed.length > 0 && ` 移除的当前生效规则：${pendingSubmit.removed.join('、')}。`}
                {pendingSubmit.criticalRemoved.length > 0 && ` 其中含安全关键规则：${pendingSubmit.criticalRemoved.join('、')}，移除后此类命中不再强制升级。`}
              </p>
              <p>提交时会携带确认参数 ?confirm_disable_forced_rules=true；后端只在显式确认时才允许移除强制规则。</p>
              <div className="flex flex-wrap gap-2">
                <button type="button" data-testid="forced-rules-confirm-submit" disabled={globalSaving} onClick={() => void confirmPending()} className={PRIMARY_BUTTON_CLASSES}>
                  {globalSaving ? '提交中…' : pendingSubmit.kind === 'restore' ? '确认恢复默认' : '确认并保存'}
                </button>
                <button type="button" data-testid="forced-rules-confirm-cancel" disabled={globalSaving} onClick={() => setPendingSubmit(null)} className={SECONDARY_BUTTON_CLASSES}>
                  取消
                </button>
              </div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={addRow} className={SECONDARY_BUTTON_CLASSES}>
              <span aria-hidden="true" className="material-symbols-outlined mr-1 align-middle text-[16px]">add</span>
              新增规则
            </button>
            <button type="button" data-testid="forced-rules-save" disabled={globalSaving} onClick={() => void handleSave()} className={PRIMARY_BUTTON_CLASSES}>
              {globalSaving ? '保存中…' : '保存强制规则'}
            </button>
            <button type="button" data-testid="forced-rules-restore" disabled={globalSaving} onClick={handleRestore} className={SECONDARY_BUTTON_CLASSES}>
              恢复默认
            </button>
            <span data-testid="forced-rules-dirty-state" className="text-[11px] text-slate-500 dark:text-slate-400">{dirtyLabel}</span>
          </div>
        </div>
      )}
    </section>
  );
};

import React, {useMemo, useState} from 'react';
import type {DimensionTraceSampleRead, ForcedRuleRead, RuleEngineOptions} from '../api';
import type {MonitoringDimension} from '../types';
import {defaultSampleEvent} from './RuleEngineContext';

export interface RuleEngineRuleMatrixProps {
  dimensions: MonitoringDimension[];
  options: RuleEngineOptions;
  optionsError: string;
  samples: DimensionTraceSampleRead[];
  selectedSampleId: number | null;
  onSelectSample: (sampleId: number | null) => void;
  activeEventType: string | null;
}

const MATCH_COLUMN_LABELS: Record<string, string> = {
  entity: '主体',
  location: '地点',
  product: '产品',
  country: '国家/区域',
  industry: '行业/原材料',
};

const SEVERITY_LABELS: Record<string, string> = {critical: '严重', high: '高', medium: '中', low: '低'};
const SEVERITY_ORDER: ReadonlyArray<string> = ['critical', 'high', 'medium', 'low'];

/**
 * 行内详情「关联分值（6 项，取最高计入）」的展示顺序与标签（方案 B 原型
 * `scheme-b-two-tabs.html` 的 `rd-block`：8 个关联键合并为 6 组，组内取最高分计入）。
 * legal_name/alias 合为「法人全称 / 别名」，site_distance/site_text 合为「地点」。
 */
const ASSOCIATION_GROUPS: ReadonlyArray<{label: string; keys: ReadonlyArray<string>}> = [
  {label: '注册号', keys: ['registry_no']},
  {label: '法人全称 / 别名', keys: ['legal_name', 'alias']},
  {label: '地点', keys: ['site_distance', 'site_text']},
  {label: '产品', keys: ['product']},
  {label: '行业', keys: ['industry']},
  {label: '国家', keys: ['country']},
];

const LEVEL_CHIP_CLASS: Record<string, string> = {
  P1: 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300',
  P2: 'bg-amber-100 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300',
  P3: 'bg-blue-100 text-[#004782] dark:bg-blue-950/60 dark:text-blue-300',
  P4: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
};

const CHIP_BASE =
  'max-w-[220px] truncate rounded-md border px-2 py-1 text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-1 dark:focus-visible:ring-blue-300';
const CHIP_ACTIVE =
  'border-[#004782] bg-[#eef6ff] font-bold text-[#004782] dark:border-blue-300 dark:bg-slate-800 dark:text-blue-300';
const CHIP_IDLE =
  'border-slate-200 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300';

/** 样例时间展示；无法解析时回退原值，不抛错。 */
function formatSampleTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
}

/** 矩阵表一行的数据单元：事件类型选项 + 接管它的启用维度（无接管为 undefined）。 */
interface MatrixRow {
  option: RuleEngineOptions['event_types'][number];
  owner: MonitoringDimension | undefined;
}

interface SourceSummary {
  declaredTotal: number;
  linkedCount: number;
  validSignalCount: number;
}

/** 信源可用性按接管维度汇总：只统计 declared 信源中已接入（linked）的数量。 */
function summarizeSources(dimension: MonitoringDimension): SourceSummary {
  const sources = dimension.dataSources ?? [];
  const linked = sources.filter((source) => source.linked);
  return {
    declaredTotal: sources.length,
    linkedCount: linked.length,
    validSignalCount: linked.reduce((total, source) => total + (source.validSignalCount ?? 0), 0),
  };
}

/** 分值摘要：严重程度四项按 SEVERITY_ORDER 以「·」连接，关联类型取最高分；无值时以「—」占位。 */
function summarizeScores(dimension: MonitoringDimension): {severity: string; associationMax: string} {
  const severity = SEVERITY_ORDER.map((key) => dimension.severityScores[key] ?? '—').join('·');
  const associationValues = Object.values(dimension.associationScores);
  const associationMax = associationValues.length > 0 ? String(Math.max(...associationValues)) : '—';
  return {severity, associationMax};
}

/** 行内详情关联分值：按 6 组取组内有效键的最大值（「取最高计入」）；组内全部缺失时以「—」占位。 */
function groupAssociationScores(
  associationScores: Record<string, number>,
): ReadonlyArray<{label: string; value: string}> {
  return ASSOCIATION_GROUPS.map((group) => {
    const values = group.keys
      .map((key) => associationScores[key])
      .filter((value) => typeof value === 'number' && Number.isFinite(value));
    return {label: group.label, value: values.length > 0 ? String(Math.max(...values)) : '—'};
  });
}

/** 与该事件类型相关的强制规则：空 event_types 表示匹配全部事件类型。 */
function relevantForcedRules(rules: ForcedRuleRead[], eventType: string): ForcedRuleRead[] {
  return rules.filter((rule) => rule.event_types.length === 0 || rule.event_types.includes(eventType));
}

/**
 * 观察态-规则矩阵表与样例事件选择器（组件归属 todo 6）。
 *
 * 矩阵语义：行=事件类型（权威来源 `GET /rule-engine/match-columns` 的 event_types），
 * 列=接管维度/分值摘要/信源可用性/展开。分值摘要给出严重程度四项与关联类型最高分；
 * 「展开」列的行内详情按需承载启用匹配柱、严重程度与关联类型完整分值、分级阈值与相关强制规则
 * （默认折叠，由 hidden 属性控制；无接管行同样可展开，各项以「—」占位）。
 * 一个事件类型只由「一个启用维度」接管（后端保存时校验占用冲突），没有启用维度接管时
 * 明确标注「当前无启用维度接管」。
 *
 * 样例驱动：真实样例来自轨迹接口 samples，点击后由壳组件带着 alert_id 重取轨迹，
 * 再把新的 event_type 通过 activeEventType 回传，本组件据此高亮对应行（壳组件 props
 * 已冻结，矩阵不直接取数）。没有任何真实样例时使用内置样例事件（沿用 RuleEngineContext
 * 的沙箱字段结构）兜底演示，并显式标注「非真实数据」，绝不伪造真实轨迹。
 */
export const RuleEngineRuleMatrix: React.FC<RuleEngineRuleMatrixProps> = ({
  dimensions,
  options,
  optionsError,
  samples,
  selectedSampleId,
  onSelectSample,
  activeEventType,
}) => {
  const builtinSample = useMemo(
    () => defaultSampleEvent(options.event_types[0]?.value),
    [options.event_types],
  );

  // 无真实样例时才启用内置样例兜底；有真实轨迹时以壳组件回传的 activeEventType 为准。
  const usesBuiltinSample = samples.length === 0 && !optionsError;
  const highlightSource: 'trace' | 'builtin' | null = activeEventType
    ? 'trace'
    : usesBuiltinSample
      ? 'builtin'
      : null;
  const highlightedEventType = highlightSource === 'trace' ? activeEventType : highlightSource === 'builtin' ? builtinSample.eventType : null;

  const owningDimension = (eventType: string): MonitoringDimension | undefined =>
    dimensions.find((dim) => dim.enabled && dim.eventTypes.includes(eventType));

  const rows: MatrixRow[] = options.event_types.map((option) => ({option, owner: owningDimension(option.value)}));
  // todo 8 无接管折叠：表格主体只保留有启用维度接管的事件类型（ownedRows）；
  // 无接管事件类型（unownedRows）收进表尾汇总行，默认折叠、按需展开，避免大量空行淹没矩阵表。
  // owner 判定口径不变（owningDimension 仅在 dim.enabled 时接管），这里只把展示拆成两组。
  const ownedRows = rows.filter((row) => row.owner !== undefined);
  const unownedRows = rows.filter((row) => row.owner === undefined);
  const ownedCount = ownedRows.length;
  // 无接管行的展开状态：默认折叠，避免长表噪声。
  const [unownedExpanded, setUnownedExpanded] = useState(false);
  // 行内详情的展开状态：按事件类型 value 独立记录（Set 支持多行同时展开），默认全部折叠。
  const [expandedRowDetails, setExpandedRowDetails] = useState<Set<string>>(() => new Set());
  const toggleRowDetail = (value: string): void => {
    setExpandedRowDetails((previous) => {
      const next = new Set(previous);
      if (next.has(value)) {
        next.delete(value);
      } else {
        next.add(value);
      }
      return next;
    });
  };
  // 汇总行文案为单文本节点：N 与名称列表都从 unownedRows 动态计算，不硬编码任何数字或名称。
  const unownedSummaryText = `另有 ${unownedRows.length} 个事件类型当前无启用维度接管：${unownedRows
    .map(({option}) => option.label)
    .join('、')}`;
  // aria-controls 指向展开后渲染的明细行 id；折叠时明细行不渲染，属性仍声明展开态的目标。
  const unownedDetailIds = unownedRows
    .map(({option}) => `rule-matrix-unowned-detail-${option.value}`)
    .join(' ');
  const toggleUnowned = (): void => setUnownedExpanded((expanded) => !expanded);
  const builtinEventLabel =
    options.event_types.find((option) => option.value === builtinSample.eventType)?.label ?? builtinSample.eventType;

  const selectedSample = samples.find((sample) => sample.id === selectedSampleId) ?? null;

  const sampleSelector = (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="font-bold text-[15px] text-[#101d28] dark:text-white">样例事件</h2>
        <span className="text-[11px] text-slate-500 dark:text-slate-400">
          选择一条最近的真实提醒，驱动流水线图与矩阵表高亮
        </span>
      </div>

      {samples.length === 0 ? (
        <div className="mt-1.5 space-y-1.5">
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="样例事件选择">
            <button
              type="button"
              data-testid="rule-matrix-sample-builtin"
              aria-pressed={usesBuiltinSample}
              onClick={() => onSelectSample(null)}
              title="无真实样例时使用的内置演示事件（沿用沙箱测试字段结构，非真实数据）"
              className={`${CHIP_BASE} ${usesBuiltinSample ? CHIP_ACTIVE : CHIP_IDLE}`}
            >
              内置样例事件
            </button>
          </div>
          {usesBuiltinSample ? (
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              当前无可选的真实样例事件，已用内置样例事件演示（非真实数据）：事件类型「{builtinEventLabel}」· 严重程度
              「{SEVERITY_LABELS[builtinSample.severity] ?? builtinSample.severity}」· 来源可信度
              {builtinSample.credibility}。产生真实提醒后可在此切换。
            </p>
          ) : null}
        </div>
      ) : (
        <div className="mt-1.5 space-y-1.5">
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="样例事件选择">
            <button
              type="button"
              data-testid="rule-matrix-sample-latest"
              aria-pressed={selectedSampleId === null}
              onClick={() => onSelectSample(null)}
              className={`${CHIP_BASE} ${selectedSampleId === null ? CHIP_ACTIVE : CHIP_IDLE}`}
            >
              最近一条
            </button>
            {samples.map((sample) => (
              <button
                key={sample.id}
                type="button"
                data-testid={`rule-matrix-sample-${sample.id}`}
                aria-pressed={selectedSampleId === sample.id}
                onClick={() => onSelectSample(sample.id)}
                title={`${sample.event_summary}（${formatSampleTime(sample.updated_at)}）`}
                className={`${CHIP_BASE} ${selectedSampleId === sample.id ? CHIP_ACTIVE : CHIP_IDLE}`}
              >
                {sample.supplier_name} · {sample.level}
              </button>
            ))}
          </div>
          {selectedSample ? (
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              已选样例：{selectedSample.supplier_name} · {selectedSample.level} · {selectedSample.event_summary}
            </p>
          ) : (
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              当前展示该维度最近一条提醒的轨迹；选择其它样例会按该提醒重新加载。
            </p>
          )}
        </div>
      )}
    </div>
  );

  if (optionsError) {
    return (
      <section
        data-testid="rule-engine-rule-matrix"
        className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60"
      >
        <div>
          <h2 className="font-bold text-[15px] text-[#101d28] dark:text-white">规则矩阵表</h2>
        </div>
        <div
          role="alert"
          data-testid="rule-matrix-options-error"
          className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300"
        >
          规则矩阵表加载失败：{optionsError}。事件类型与匹配柱选项暂不可用，请稍后重试或检查规则工作台接口。
        </div>
        {samples.length > 0 ? sampleSelector : null}
      </section>
    );
  }

  return (
    <section
      data-testid="rule-engine-rule-matrix"
      className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60"
    >
      {sampleSelector}

      {/*
        矩阵卡内头部（对齐 demo `:1065-1071` 的 `matrix-head`）：徽标 + 标题 + 说明。
        取舍：矩阵表是「全维度共用」视角，demo 在矩阵卡内也补了一枚「全维度共用」徽标；
        全局层顶部的同款徽标属于 `rule-engine-global-layer`（RuleEngineView 管理），此处叠加第二枚
        仅补齐 demo 形态，不改动全局层。计数摘要 `rule-matrix-summary` 保持在标题行右侧。
      */}
      <header className="flex flex-wrap items-start gap-3">
        <span className="inline-flex shrink-0 items-center self-start rounded-full border border-blue-200 bg-blue-50 px-2.5 py-0.5 text-[11px] font-bold tracking-wide text-[#004782] dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300">
          全维度共用
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <h2 className="font-bold text-[16px] text-[#101d28] dark:text-white">规则矩阵表</h2>
            <span data-testid="rule-matrix-summary" className="text-[11px] text-slate-500 dark:text-slate-400">
              共 {options.event_types.length} 个事件类型：{ownedCount} 个由启用维度接管，
              {options.event_types.length - ownedCount} 个当前无接管
            </span>
          </div>
          <p className="mt-1 text-[11.5px] text-slate-500 dark:text-slate-400">
            每行一个事件类型，只由一个启用维度接管；无接管事件类型折叠为一行汇总，行详情按需展开，分值来自接管维度的当前生效配置。
          </p>
        </div>
      </header>

      {options.event_types.length === 0 ? (
        <p className="rounded-xl border border-slate-200 bg-[#f8fafc] px-3 py-2 text-[12px] text-slate-500 dark:border-slate-700 dark:bg-slate-950/40 dark:text-slate-400">
          当前没有可展示的事件类型。
        </p>
      ) : (
        // 全表唯一横向滚动容器（todo 10）：表格所有需要横向滚动的内容都收敛到这一个容器内，
        // 避免出现嵌套/双重滚动条；窄屏只在这里滚动。
        // 表格不设强制最小宽度，长内容（分值摘要、信源可用性、维度名）均允许换行，
        // 因此 ≥1280px 视口下按 w-full 自然铺满、不产生横向滚动；窄屏由本容器兜底。
        <div
          data-testid="rule-engine-rule-matrix-scroll"
          role="region"
          aria-label="规则矩阵表（窄屏可横向滚动）"
          tabIndex={0}
          className="overflow-x-auto rounded-xl border border-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] dark:border-slate-700"
        >
          <table className="w-full border-collapse text-left text-[12px]">
            <caption className="sr-only">
              事件类型规则矩阵：每行一个事件类型，列为接管维度、分值摘要（严重程度四项与关联类型最高分）、信源可用性与展开
            </caption>
            <thead>
              <tr className="border-b border-slate-200 bg-[#f8fafc] text-[11px] text-slate-500 dark:border-slate-700 dark:bg-slate-950/40 dark:text-slate-400">
                <th scope="col" className="px-3 py-2 font-bold">事件类型</th>
                <th scope="col" className="px-3 py-2 font-bold">接管维度</th>
                <th scope="col" className="px-3 py-2 font-bold">分值摘要</th>
                <th scope="col" className="px-3 py-2 font-bold">信源可用性</th>
                <th scope="col" className="px-3 py-2 font-bold">展开</th>
              </tr>
            </thead>
            <tbody>
              {/*
                todo 8：主体只渲染 ownedRows；无接管事件类型默认折叠为汇总行，
                展开时才紧随汇总行渲染（渲染顺序：接管行 → 汇总行 → 展开的无接管行）。
              */}
              {[
                ...ownedRows.map((row) => ({kind: 'row' as const, row})),
                ...(unownedRows.length > 0 ? [{kind: 'summary' as const}] : []),
                ...(unownedExpanded ? unownedRows.map((row) => ({kind: 'row' as const, row})) : []),
              ].map((item) => {
                if (item.kind === 'summary') {
                  // 汇总行整行可点击；键盘用户经内部按钮 Enter/Space 操作（按钮同样声明 aria-expanded）。
                  return (
                    <tr
                      key="rule-matrix-unowned-summary"
                      data-testid="rule-matrix-unowned-summary"
                      aria-expanded={unownedExpanded}
                      aria-controls={unownedDetailIds}
                      onClick={toggleUnowned}
                      className="cursor-pointer border-b border-slate-100 dark:border-slate-800"
                    >
                      <td colSpan={5} className="px-3 py-2">
                        {/* 全宽按钮与行点击共用同一个 toggle；按钮内 stopPropagation 防止一次点击切换两次。 */}
                        <button
                          type="button"
                          aria-expanded={unownedExpanded}
                          aria-controls={unownedDetailIds}
                          onClick={(event) => {
                            event.stopPropagation();
                            toggleUnowned();
                          }}
                          className="w-full rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-2 text-left text-[11px] text-slate-600 transition-colors hover:border-[#004782] hover:text-[#004782] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-1 dark:border-slate-600 dark:bg-slate-900/60 dark:text-slate-300 dark:hover:border-blue-300 dark:hover:text-blue-300 dark:focus-visible:ring-blue-300"
                        >
                          {unownedSummaryText}
                        </button>
                      </td>
                    </tr>
                  );
                }

                const {option, owner} = item.row;
                const isHighlighted = highlightSource !== null && highlightedEventType === option.value;
                const forcedRules = owner ? relevantForcedRules(owner.forcedRules, option.value) : [];
                const sources = owner ? summarizeSources(owner) : null;
                const scores = owner ? summarizeScores(owner) : null;
                // 行内详情锚点：toggle 的 aria-controls 与详情行 id 一一对应，共用同一 value 派生。
                const detailId = `rule-matrix-row-detail-${option.value}`;
                const detailExpanded = expandedRowDetails.has(option.value);
                // 用 Fragment 把「数据行 + 详情行」成对渲染，保证详情行是 <tbody> 的直接子 <tr>。
                return (
                  <React.Fragment key={option.value}>
                  <tr
                    // 无接管行带明细锚点 id，供汇总按钮/行的 aria-controls 指向；接管行无此 id。
                    id={owner === undefined ? `rule-matrix-unowned-detail-${option.value}` : undefined}
                    data-testid={`rule-matrix-row-${option.value}`}
                    data-event-type={option.value}
                    data-active={isHighlighted ? 'true' : 'false'}
                    className={`border-b border-slate-100 align-top last:border-0 dark:border-slate-800 ${
                      isHighlighted
                        ? highlightSource === 'builtin'
                          ? 'bg-amber-50 dark:bg-amber-950/20'
                          : 'bg-[#eef6ff] dark:bg-slate-800/80'
                        : ''
                    }`}
                  >
                    <td className="px-3 py-2">
                      <span className="font-bold text-slate-800 dark:text-slate-100">{option.label}</span>
                      {/* 事件 code（demo `.mx-code`）：中文标签旁的 mono 小字，值直接取接口选项的 value，不新增映射表。 */}
                      <span className="ml-1 font-mono text-[10.5px] font-medium text-slate-400 dark:text-slate-500">
                        {option.value}
                      </span>
                      {isHighlighted ? (
                        <span
                          data-testid="rule-matrix-active-marker"
                          className={`ml-1.5 inline-block rounded-full px-1.5 py-0.5 text-[10px] font-bold ${
                            highlightSource === 'builtin'
                              ? 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-200'
                              : 'bg-[#004782] text-white dark:bg-blue-300 dark:text-slate-900'
                          }`}
                        >
                          {highlightSource === 'builtin' ? '内置样例' : '当前样例'}
                        </span>
                      ) : null}
                    </td>

                    <td className="px-3 py-2">
                      {owner ? (
                        /* 接管维度胶囊（demo `.dim-chip-sm`：浅底 + 描边 + 全圆角）；
                           文案保持 owner.name，无接管时文案不变。 */
                        <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-semibold text-slate-700 dark:border-slate-700 dark:bg-slate-950/50 dark:text-slate-200">
                          {owner.name}
                        </span>
                      ) : (
                        <span className="text-slate-400 dark:text-slate-500">当前无启用维度接管</span>
                      )}
                    </td>

                    <td className="px-3 py-2">
                      {scores ? (
                        <span
                          data-testid="rule-matrix-score-summary"
                          className="flex flex-col gap-y-0.5 font-mono text-[11px] text-slate-600 dark:text-slate-300"
                        >
                          <span className="whitespace-nowrap">{`严重 ${scores.severity}`}</span>
                          <span className="whitespace-nowrap">{`关联最高 ${scores.associationMax}`}</span>
                        </span>
                      ) : (
                        <span className="text-slate-400 dark:text-slate-500">—</span>
                      )}
                    </td>

                    <td className="px-3 py-2">
                      {sources === null ? (
                        <span className="text-slate-400 dark:text-slate-500">—</span>
                      ) : sources.linkedCount > 0 ? (
                        /* 信源可用性（demo `:1089`）：主文案 mono「N/M 已接入」+ 圆点状态。
                           状态严格映射现有数据口径：validSignalCount>0 → 有信号（品牌色点）；
                           已接入但 0 有效信号 → 无输入（灰点）；不改动统计来源。 */
                        <span className="flex flex-col gap-y-0.5">
                          <span className="font-mono text-[11px] font-bold text-slate-700 dark:text-slate-200">
                            {sources.linkedCount}/{sources.declaredTotal} 已接入
                          </span>
                          <span className="inline-flex items-center gap-1.5 text-[10.5px] text-slate-500 dark:text-slate-400">
                            <span
                              aria-hidden="true"
                              className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                                sources.validSignalCount > 0 ? 'bg-[#007aff] dark:bg-blue-400' : 'bg-slate-300 dark:bg-slate-600'
                              }`}
                            />
                            {sources.validSignalCount > 0 ? '有信号' : '无输入'}
                          </span>
                        </span>
                      ) : sources.declaredTotal > 0 ? (
                        /* 已声明但 0 接入：主文案仍为「0/M 已接入」，状态点用现有文案「无已接入信源」（灰点）。 */
                        <span className="flex flex-col gap-y-0.5">
                          <span className="font-mono text-[11px] font-bold text-slate-700 dark:text-slate-200">
                            0/{sources.declaredTotal} 已接入
                          </span>
                          <span className="inline-flex items-center gap-1.5 text-[10.5px] text-slate-500 dark:text-slate-400">
                            <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300 dark:bg-slate-600" />
                            无已接入信源
                          </span>
                        </span>
                      ) : (
                        <span className="text-slate-400 dark:text-slate-500">未声明信源</span>
                      )}
                    </td>

                    {/* 展开列：行内详情切换按钮（原生 button 天然支持 Enter/Space；深浅色样式齐全）。
                        demo `.row-toggle` 为全圆角胶囊 + chevron；chevron 复用页面现成的
                        material-symbols-outlined 图标（expand_more/expand_less），随展开态切换方向；
                        aria-expanded=true 时按 demo 使用选中底色（aria-expanded: 变体）。 */}
                    <td className="px-3 py-2">
                      <button
                        type="button"
                        data-testid="rule-matrix-row-toggle"
                        aria-expanded={detailExpanded}
                        aria-controls={detailId}
                        onClick={() => toggleRowDetail(option.value)}
                        className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-600 transition-colors hover:border-[#004782] hover:text-[#004782] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-1 aria-expanded:border-blue-200 aria-expanded:bg-[#eef6ff] aria-expanded:text-[#004782] dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:border-blue-300 dark:hover:text-blue-300 dark:aria-expanded:border-blue-900 dark:aria-expanded:bg-slate-800 dark:aria-expanded:text-blue-300 dark:focus-visible:ring-blue-300"
                      >
                        {detailExpanded ? '收起' : '展开'}
                        <span className="material-symbols-outlined text-[14px]" aria-hidden="true">
                          {detailExpanded ? 'expand_less' : 'expand_more'}
                        </span>
                      </button>
                    </td>
                  </tr>

                  {/*
                    行内详情行（todo 9）：默认带 hidden HTML 属性（折叠态仍在 DOM，仅隐藏；展开时移除）。
                    详情行与数据行成对渲染、是 <tbody> 的直接子 <tr>；hidden 同时声明在行与内容容器上：
                    行级隐藏让浏览器只渲染数据行，容器级隐藏让 testid 锚点能自证折叠态。
                  */}
                  <tr
                    id={detailId}
                    hidden={!detailExpanded}
                    className="border-b border-slate-100 bg-slate-50/70 last:border-0 dark:border-slate-800 dark:bg-slate-950/30"
                  >
                    <td colSpan={5} className="px-3 py-3">
                      {/*
                        行内详情（demo `.row-detail` `:1094-1122`，CSS `:500`）：≥1280px 4 列网格、
                        <1280px 2 列（Tailwind xl=1280px 与 demo 媒体查询断点一一对应）。
                        demo 的 4 块 = 启用匹配柱 / 关联分值（6 项）/ 分级阈值 / 相关强制规则；
                        「严重程度分值」按 demo 并入分值块上下文，但独立 testid
                        `rule-matrix-severity-scores` 保留在此块内（不得删除）。
                      */}
                      <div
                        data-testid="rule-matrix-row-detail"
                        hidden={!detailExpanded}
                        className="grid grid-cols-2 gap-4 text-left xl:grid-cols-4"
                      >
                        <div className="min-w-0">
                          <h4 className="text-[11.5px] font-bold text-slate-700 dark:text-slate-200">启用匹配柱</h4>
                          <div className="mt-1 text-[12px] text-slate-600 dark:text-slate-300">
                            {owner ? (
                              owner.matchColumns.map((column) => MATCH_COLUMN_LABELS[column] ?? column).join('、')
                            ) : (
                              <span className="text-slate-400 dark:text-slate-500">—</span>
                            )}
                          </div>
                        </div>

                        {/* 分值块：严重程度（作为上下文并入）+ 关联分值 6 项两列列表（demo `.assoc-list`）。 */}
                        <div className="min-w-0">
                          <h4 className="text-[11.5px] font-bold text-slate-700 dark:text-slate-200">分值明细</h4>
                          <div className="mt-1 space-y-1.5">
                            <div>
                              <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500">严重程度</span>
                              <div className="mt-0.5">
                                {owner ? (
                                  <span data-testid="rule-matrix-severity-scores" className="flex flex-wrap gap-x-2 font-mono text-[11px]">
                                    {SEVERITY_ORDER.map((key) => (
                                      <span key={key} className="whitespace-nowrap">
                                        <span className="text-slate-400 dark:text-slate-500">{SEVERITY_LABELS[key] ?? key}</span>{' '}
                                        <span className="font-bold text-slate-700 dark:text-slate-200">
                                          {owner.severityScores[key] ?? '—'}
                                        </span>
                                      </span>
                                    ))}
                                  </span>
                                ) : (
                                  <span className="text-slate-400 dark:text-slate-500">—</span>
                                )}
                              </div>
                            </div>
                            <div>
                              <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500">
                                关联分值（6 项，取最高计入）
                              </span>
                              <div className="mt-0.5">
                                {owner ? (
                                  <span
                                    data-testid="rule-matrix-association-scores"
                                    className="grid grid-cols-1 gap-x-4 gap-y-0.5 font-mono text-[11px] sm:grid-cols-2"
                                  >
                                    {groupAssociationScores(owner.associationScores).map((group) => (
                                      <span
                                        key={group.label}
                                        className="flex items-baseline justify-between gap-2 border-b border-dashed border-slate-200 pb-0.5 dark:border-slate-700"
                                      >
                                        <span className="truncate text-slate-400 dark:text-slate-500">{group.label}</span>{' '}
                                        <span className="shrink-0 font-bold text-slate-700 dark:text-slate-200">{group.value}</span>
                                      </span>
                                    ))}
                                  </span>
                                ) : (
                                  <span className="text-slate-400 dark:text-slate-500">—</span>
                                )}
                              </div>
                            </div>
                          </div>
                        </div>

                        <div className="min-w-0">
                          <h4 className="text-[11.5px] font-bold text-slate-700 dark:text-slate-200">分级阈值</h4>
                          <div className="mt-1">
                            {owner ? (
                              <span className="flex flex-wrap gap-x-2 whitespace-nowrap font-mono">
                                <span>
                                  <span className="font-bold text-red-600 dark:text-red-400">P1</span>
                                  <span className="text-slate-600 dark:text-slate-300">≥{owner.thresholds.p1}</span>
                                </span>
                                <span>
                                  <span className="font-bold text-amber-600 dark:text-amber-400">P2</span>
                                  <span className="text-slate-600 dark:text-slate-300">≥{owner.thresholds.p2}</span>
                                </span>
                                <span>
                                  <span className="font-bold text-[#007aff]">P3</span>
                                  <span className="text-slate-600 dark:text-slate-300">≥{owner.thresholds.p3}</span>
                                </span>
                              </span>
                            ) : (
                              <span className="text-slate-400 dark:text-slate-500">—</span>
                            )}
                          </div>
                        </div>

                        <div className="min-w-0">
                          <h4 className="text-[11.5px] font-bold text-slate-700 dark:text-slate-200">相关强制规则</h4>
                          <div className="mt-1">
                            {forcedRules.length === 0 ? (
                              <span className="text-slate-400 dark:text-slate-500">无</span>
                            ) : (
                              <ul className="space-y-0.5">
                                {forcedRules.map((rule) => (
                                  <li
                                    key={rule.name}
                                    data-testid={`rule-matrix-forced-${rule.name}`}
                                    className="flex items-center gap-1.5"
                                  >
                                    <span
                                      className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-bold ${
                                        LEVEL_CHIP_CLASS[rule.forced_level] ?? LEVEL_CHIP_CLASS.P4
                                      }`}
                                    >
                                      {rule.forced_level}
                                    </span>
                                    <span
                                      className="font-mono text-[10px] text-slate-600 dark:text-slate-300"
                                      title={rule.description || rule.reason}
                                    >
                                      {rule.name}
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[11px] text-slate-500 dark:text-slate-400">
        相关强制规则只列出与该事件类型相关（或匹配全部事件类型）的规则；命中后将直接定级并记满分，绕过常规评分。
      </p>
    </section>
  );
};

import React, {useMemo} from 'react';
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

const ASSOCIATION_LABELS: Record<string, string> = {
  registry_no: '注册号',
  legal_name: '法人全称',
  alias: '别名',
  site_distance: '地点距离',
  site_text: '地点文本',
  product: '产品',
  country: '国家',
  industry: '行业',
};
const ASSOCIATION_ORDER: ReadonlyArray<string> = [
  'registry_no',
  'legal_name',
  'alias',
  'site_distance',
  'site_text',
  'product',
  'country',
  'industry',
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
  'border-[#004782] bg-[#ecf4ff] font-bold text-[#004782] dark:border-blue-300 dark:bg-slate-800 dark:text-blue-300';
const CHIP_IDLE =
  'border-slate-200 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300';

/** 样例时间展示；无法解析时回退原值，不抛错。 */
function formatSampleTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
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

/** 与该事件类型相关的强制规则：空 event_types 表示匹配全部事件类型。 */
function relevantForcedRules(rules: ForcedRuleRead[], eventType: string): ForcedRuleRead[] {
  return rules.filter((rule) => rule.event_types.length === 0 || rule.event_types.includes(eventType));
}

/**
 * 观察态-规则矩阵表与样例事件选择器（组件归属 todo 6）。
 *
 * 矩阵语义：行=事件类型（权威来源 `GET /rule-engine/match-columns` 的 event_types），
 * 列=接管维度/启用匹配柱/严重程度分值/关联类型分值/分级阈值/相关强制规则/信源可用性。
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

  const rows = options.event_types.map((option) => ({option, owner: owningDimension(option.value)}));
  const ownedCount = rows.filter((row) => row.owner !== undefined).length;
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

      <div>
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h2 className="font-bold text-[15px] text-[#101d28] dark:text-white">规则矩阵表</h2>
          <span data-testid="rule-matrix-summary" className="text-[11px] text-slate-500 dark:text-slate-400">
            共 {options.event_types.length} 个事件类型：{ownedCount} 个由启用维度接管，
            {options.event_types.length - ownedCount} 个当前无接管
          </span>
        </div>
        <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
          每个事件类型只由一个启用维度接管；下表展示接管该事件类型的规则口径，分值来自该维度的当前生效配置。
        </p>
      </div>

      {options.event_types.length === 0 ? (
        <p className="rounded-xl border border-slate-200 bg-[#f7f9ff] px-3 py-2 text-[12px] text-slate-500 dark:border-slate-700 dark:bg-slate-950/40 dark:text-slate-400">
          当前没有可展示的事件类型。
        </p>
      ) : (
        // 唯一的横向滚动容器：窄屏只在这里滚动，避免嵌套滚动条。
        <div
          data-testid="rule-engine-rule-matrix-scroll"
          role="region"
          aria-label="规则矩阵表（窄屏可横向滚动）"
          tabIndex={0}
          className="overflow-x-auto rounded-xl border border-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] dark:border-slate-700"
        >
          <table className="w-full min-w-[1080px] border-collapse text-left text-[12px]">
            <caption className="sr-only">
              事件类型规则矩阵：每行一个事件类型，列为接管维度、启用匹配柱、严重程度分值、关联类型分值、分级阈值、相关强制规则与信源可用性
            </caption>
            <thead>
              <tr className="border-b border-slate-200 bg-[#f7f9ff] text-[11px] text-slate-500 dark:border-slate-700 dark:bg-slate-950/40 dark:text-slate-400">
                <th scope="col" className="px-3 py-2 font-bold">事件类型</th>
                <th scope="col" className="px-3 py-2 font-bold">接管维度</th>
                <th scope="col" className="px-3 py-2 font-bold">启用匹配柱</th>
                <th scope="col" className="px-3 py-2 font-bold">严重程度分值</th>
                <th scope="col" className="px-3 py-2 font-bold">关联类型分值</th>
                <th scope="col" className="px-3 py-2 font-bold">分级阈值</th>
                <th scope="col" className="px-3 py-2 font-bold">相关强制规则</th>
                <th scope="col" className="px-3 py-2 font-bold">信源可用性</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({option, owner}) => {
                const isHighlighted = highlightSource !== null && highlightedEventType === option.value;
                const forcedRules = owner ? relevantForcedRules(owner.forcedRules, option.value) : [];
                const sources = owner ? summarizeSources(owner) : null;
                return (
                  <tr
                    key={option.value}
                    data-testid={`rule-matrix-row-${option.value}`}
                    data-event-type={option.value}
                    data-active={isHighlighted ? 'true' : 'false'}
                    className={`border-b border-slate-100 align-top last:border-0 dark:border-slate-800 ${
                      isHighlighted
                        ? highlightSource === 'builtin'
                          ? 'bg-amber-50 dark:bg-amber-950/20'
                          : 'bg-[#ecf4ff] dark:bg-slate-800/80'
                        : ''
                    }`}
                  >
                    <td className="px-3 py-2">
                      <span className="font-bold text-slate-800 dark:text-slate-100">{option.label}</span>
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
                        <span className="font-bold text-[#004782] dark:text-blue-300">{owner.name}</span>
                      ) : (
                        <span className="text-slate-400 dark:text-slate-500">当前无启用维度接管</span>
                      )}
                    </td>

                    <td className="px-3 py-2 text-slate-600 dark:text-slate-300">
                      {owner ? (
                        owner.matchColumns.map((column) => MATCH_COLUMN_LABELS[column] ?? column).join('、')
                      ) : (
                        <span className="text-slate-400 dark:text-slate-500">—</span>
                      )}
                    </td>

                    <td className="px-3 py-2">
                      {owner ? (
                        <span data-testid="rule-matrix-severity-scores" className="flex flex-wrap gap-x-2 font-mono">
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
                    </td>

                    <td className="px-3 py-2">
                      {owner ? (
                        <span data-testid="rule-matrix-association-scores" className="flex flex-wrap gap-x-2 font-mono">
                          {ASSOCIATION_ORDER.map((key) => (
                            <span key={key} className="whitespace-nowrap">
                              <span className="text-slate-400 dark:text-slate-500">{ASSOCIATION_LABELS[key] ?? key}</span>{' '}
                              <span className="font-bold text-slate-700 dark:text-slate-200">
                                {owner.associationScores[key] ?? '—'}
                              </span>
                            </span>
                          ))}
                        </span>
                      ) : (
                        <span className="text-slate-400 dark:text-slate-500">—</span>
                      )}
                    </td>

                    <td className="px-3 py-2">
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
                    </td>

                    <td className="px-3 py-2">
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
                    </td>

                    <td className="px-3 py-2">
                      {sources === null ? (
                        <span className="text-slate-400 dark:text-slate-500">—</span>
                      ) : sources.linkedCount > 0 ? (
                        <span className="text-slate-600 dark:text-slate-300">
                          已接入 {sources.linkedCount}/{sources.declaredTotal} 个信源
                          <span className="block font-mono text-[10px] text-slate-400 dark:text-slate-500">
                            有效信号 {sources.validSignalCount}
                          </span>
                        </span>
                      ) : sources.declaredTotal > 0 ? (
                        <span className="text-amber-700 dark:text-amber-300">
                          无已接入信源
                          <span className="block text-[10px] text-slate-400 dark:text-slate-500">
                            声明 {sources.declaredTotal} 个，均未接入
                          </span>
                        </span>
                      ) : (
                        <span className="text-slate-400 dark:text-slate-500">未声明信源</span>
                      )}
                    </td>
                  </tr>
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

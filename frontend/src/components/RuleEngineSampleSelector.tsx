import React, {useMemo} from 'react';
import type {DimensionTraceSampleRead, RuleEngineOptions} from '../api';
import {defaultSampleEvent} from './RuleEngineContext';

/**
 * 样例事件选择器（#17）：从规则矩阵表迁回维度级『证据』卡内，紧邻它驱动的运行轨迹与矩阵高亮。
 *
 * - 真实样例来自轨迹接口 `samples`；点击后由壳组件（RuleEngineView）带 sampleId 重取轨迹，
 *   再把新的 event_type 通过 activeEventType 回传高亮矩阵行——本组件不取数、不持有独立状态。
 * - 没有任何真实样例时使用内置样例事件（沿用沙箱字段结构）兜底演示，并显式标注「非真实数据」，
 *   绝不伪造真实轨迹。
 * - `selectedSampleId` / `onSelectSample` 由壳组件下发，全页唯一状态源，避免出现第二个选择器。
 */
export interface RuleEngineSampleSelectorProps {
  options: RuleEngineOptions;
  samples: DimensionTraceSampleRead[];
  selectedSampleId: number | null;
  onSelectSample: (sampleId: number | null) => void;
}

const SEVERITY_LABELS: Record<string, string> = {critical: '严重', high: '高', medium: '中', low: '低'};

const CHIP_BASE =
  'max-w-[220px] truncate rounded-xl border px-2 py-1 text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-1 dark:focus-visible:ring-blue-300';
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

export const RuleEngineSampleSelector: React.FC<RuleEngineSampleSelectorProps> = ({
  options,
  samples,
  selectedSampleId,
  onSelectSample,
}) => {
  const builtinSample = useMemo(
    () => defaultSampleEvent(options.event_types[0]?.value),
    [options.event_types],
  );
  const usesBuiltinSample = samples.length === 0;
  const builtinEventLabel =
    options.event_types.find((option) => option.value === builtinSample.eventType)?.label ?? builtinSample.eventType;
  const selectedSample = samples.find((sample) => sample.id === selectedSampleId) ?? null;

  return (
    <div data-testid="rule-engine-sample-selector" className="min-w-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="text-[12.5px] font-bold text-[#101d28] dark:text-white">样例事件</h3>
        <span className="text-[11px] text-slate-500 dark:text-slate-400">
          选择一条最近的真实提醒，驱动运行轨迹与矩阵表高亮
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
};

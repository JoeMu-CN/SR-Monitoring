import React from 'react';
import type {DimensionTraceSampleRead, RuleEngineOptions} from '../api';
import type {MonitoringDimension} from '../types';

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

/**
 * 观察态-规则矩阵表与样例事件选择器挂载点（组件归属 todo 6，本文件由 todo 4 先落最小可用实现）。
 *
 * 行=事件类型（来自 /match-columns），列=接管维度/匹配柱/分级阈值/强制规则；
 * 样例来自轨迹接口 samples，选择后由壳组件带着 alert_id 重取轨迹。
 * todo 6 将在本文件内补齐严重程度/关联分值/信源可用性列与高亮联动。
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
  if (optionsError) {
    return (
      <section
        role="alert"
        data-testid="rule-engine-rule-matrix"
        className="rounded-2xl border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-4 text-[12px] text-red-700 dark:text-red-300"
      >
        规则选项加载失败：{optionsError}
      </section>
    );
  }

  const receiverFor = (eventType: string) =>
    dimensions.find((dim) => dim.enabled && dim.eventTypes.includes(eventType));

  return (
    <section
      data-testid="rule-engine-rule-matrix"
      className="space-y-3 rounded-2xl border border-slate-200/80 dark:border-slate-700/60 bg-white/80 dark:bg-slate-800/60 p-4 shadow-sm"
    >
      <div>
        <h2 className="font-bold text-[15px] text-[#101d28] dark:text-white">样例事件</h2>
        {samples.length === 0 ? (
          <p className="mt-1 text-[12px] text-slate-500 dark:text-slate-400">
            当前无真实样例事件；流水线展示该维度最近一条提醒。
          </p>
        ) : (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <button
              type="button"
              aria-pressed={selectedSampleId === null}
              onClick={() => onSelectSample(null)}
              className={`rounded-md border px-2 py-1 text-[11px] font-bold transition-colors ${
                selectedSampleId === null
                  ? 'border-[#004782] bg-[#ecf4ff] text-[#004782] dark:border-blue-300 dark:bg-slate-800 dark:text-blue-300'
                  : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300'
              }`}
            >
              最近一条
            </button>
            {samples.map((sample) => (
              <button
                key={sample.id}
                type="button"
                aria-pressed={selectedSampleId === sample.id}
                onClick={() => onSelectSample(sample.id)}
                className={`max-w-full truncate rounded-md border px-2 py-1 text-[11px] transition-colors ${
                  selectedSampleId === sample.id
                    ? 'border-[#004782] bg-[#ecf4ff] text-[#004782] dark:border-blue-300 dark:bg-slate-800 dark:text-blue-300'
                    : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300'
                }`}
              >
                {sample.supplier_name} · {sample.level}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-[12px]">
          <thead>
            <tr className="border-b border-slate-200 text-left text-[11px] text-slate-500 dark:border-slate-700">
              <th className="px-3 py-2 font-bold">事件类型</th>
              <th className="px-3 py-2 font-bold">接管维度</th>
              <th className="px-3 py-2 font-bold">匹配柱</th>
              <th className="px-3 py-2 font-bold">分级阈值</th>
              <th className="px-3 py-2 font-bold">强制规则</th>
            </tr>
          </thead>
          <tbody>
            {options.event_types.map((option) => {
              const receiver = receiverFor(option.value);
              const isActive = activeEventType === option.value;
              return (
                <tr
                  key={option.value}
                  data-active={isActive ? 'true' : 'false'}
                  className={`border-b border-slate-100 dark:border-slate-800 last:border-0 ${
                    isActive ? 'bg-[#ecf4ff] dark:bg-slate-800/80' : ''
                  }`}
                >
                  <td className="px-3 py-2 text-slate-700 dark:text-slate-300">{option.label}</td>
                  <td className="px-3 py-2 text-slate-700 dark:text-slate-300">
                    {receiver ? receiver.name : <span className="text-slate-400 dark:text-slate-500">当前无启用维度接管</span>}
                  </td>
                  <td className="px-3 py-2 text-slate-500 dark:text-slate-400">
                    {receiver
                      ? receiver.matchColumns.map((column) => MATCH_COLUMN_LABELS[column] ?? column).join('、')
                      : '—'}
                  </td>
                  <td className="px-3 py-2 font-mono text-slate-500 dark:text-slate-400">
                    {receiver ? `P1≥${receiver.thresholds.p1} / P2≥${receiver.thresholds.p2} / P3≥${receiver.thresholds.p3}` : '—'}
                  </td>
                  <td className="px-3 py-2 text-slate-500 dark:text-slate-400">
                    {receiver ? receiver.forcedRules.length : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
};

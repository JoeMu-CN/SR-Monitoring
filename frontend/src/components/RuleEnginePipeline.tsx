import React from 'react';
import type {DimensionInputsRead, DimensionTraceRead} from '../api';
import type {MonitoringDimension} from '../types';

const SCORE_STAGE_LABELS: Array<[string, string]> = [
  ['severity', '严重程度'],
  ['association', '关联强度'],
  ['source_credibility', '来源可信度'],
  ['timeliness', '时效性'],
  ['product_relevance', '产品相关性'],
];

const MATCH_COLUMN_LABELS: Record<string, string> = {
  entity: '主体',
  location: '地点',
  product: '产品',
  country: '国家/区域',
  industry: '行业/原材料',
};

export interface RuleEnginePipelineProps {
  dimension: MonitoringDimension;
  trace: DimensionTraceRead | null;
  traceError: string;
  inputs: DimensionInputsRead | null;
  inputsError: string;
  selectedSampleId: number | null;
}

/**
 * 观察态-规则运行流水线挂载点（组件归属 todo 5，本文件由 todo 4 先落最小可用实现）。
 *
 * 数据全部来自真实接口：signal → 事件路由 → 匹配柱 → 得分构成 → 总分 →
 * 封顶/强制规则 → 输出等级。todo 5 将在本文件内扩展为 SVG/CSS + motion 的完整可视化。
 */
export const RuleEnginePipeline: React.FC<RuleEnginePipelineProps> = ({
  dimension,
  trace,
  traceError,
  inputs,
  inputsError,
  selectedSampleId,
}) => {
  if (traceError) {
    return (
      <section
        role="alert"
        data-testid="rule-engine-pipeline"
        className="rounded-2xl border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-4 text-[12px] text-red-700 dark:text-red-300"
      >
        运行轨迹加载失败：{traceError}
      </section>
    );
  }

  if (trace === null) {
    return (
      <section
        data-testid="rule-engine-pipeline"
        className="rounded-2xl border border-slate-200/80 dark:border-slate-700/60 bg-white/80 dark:bg-slate-800/60 p-4 shadow-sm"
      >
        <h2 className="font-bold text-[15px] text-[#101d28] dark:text-white">规则运行流水线</h2>
        <p className="mt-2 flex items-center gap-1.5 text-[12px] text-slate-400 dark:text-slate-500">
          <span className="material-symbols-outlined animate-spin text-[14px] leading-none" aria-hidden="true">progress_activity</span>
          <span className="sr-only">运行轨迹加载中…</span>
        </p>
      </section>
    );
  }

  const {event, routing, match, score} = trace;
  const stageBase = 'border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 rounded-xl p-3';

  return (
    <section
      data-testid="rule-engine-pipeline"
      data-available={trace.available ? 'true' : 'false'}
      className="space-y-3 rounded-2xl border border-slate-200/80 dark:border-slate-700/60 bg-white/80 dark:bg-slate-800/60 p-4 shadow-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-bold text-[15px] text-[#101d28] dark:text-white">规则运行流水线</h2>
        <span className="text-[11px] text-slate-500 dark:text-slate-400">
          {trace.available
            ? `真实样本：${event?.summary ?? '—'}${selectedSampleId ? `（提醒 #${selectedSampleId}）` : '（最近一条）'}`
            : '当前无真实样本'}
        </span>
      </div>

      {!trace.available && (
        <p className="rounded-xl border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-[12px] text-amber-800 dark:text-amber-200">
          {dimension.name}当前没有可用的真实提醒轨迹，请选择样例事件或等待新的信号进入。
        </p>
      )}

      <ol className="space-y-2">
        <li className={stageBase}>
          <p className="text-[11px] font-bold text-[#004782] dark:text-blue-300">① 信号输入</p>
          <p className="text-[12px] text-slate-700 dark:text-slate-300 mt-1">
            {event?.source_name ? `${event.source_name}` : '来源未披露'}
            {inputs && !inputsError ? ` · 近 30 天 ${inputs.observed.length} 个信源有信号` : ''}
          </p>
          {inputsError && <p className="text-[11px] text-red-700 dark:text-red-300 mt-1">输入健康度加载失败：{inputsError}</p>}
        </li>
        <li className={stageBase}>
          <p className="text-[11px] font-bold text-[#004782] dark:text-blue-300">② 事件路由</p>
          <p className="text-[12px] text-slate-700 dark:text-slate-300 mt-1">
            {event?.event_type ?? '—'}
            {event?.event_subtype ? ` / ${event.event_subtype}` : ''} → {routing?.label ?? '未找到接管维度'}
          </p>
        </li>
        <li className={stageBase}>
          <p className="text-[11px] font-bold text-[#004782] dark:text-blue-300">③ 匹配柱</p>
          <p className="text-[12px] text-slate-700 dark:text-slate-300 mt-1">
            启用柱：{(routing?.match_columns ?? []).map((column) => MATCH_COLUMN_LABELS[column] ?? column).join('、') || '—'}
          </p>
          <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
            命中方式：{match?.match_type ?? '—'}
            {match?.match_reasons.length ? ` · ${match.match_reasons.join('；')}` : ''}
          </p>
        </li>
        <li className={stageBase}>
          <p className="text-[11px] font-bold text-[#004782] dark:text-blue-300">④ 得分构成</p>
          <ul className="mt-1 space-y-0.5">
            {SCORE_STAGE_LABELS.map(([key, label]) => (
              <li key={key} className="flex items-center justify-between text-[11px] text-slate-600 dark:text-slate-300">
                <span>{label}</span>
                <span className="font-mono font-bold">{String(score?.detail[key] ?? 0)}</span>
              </li>
            ))}
          </ul>
        </li>
        <li className={stageBase}>
          <p className="text-[11px] font-bold text-[#004782] dark:text-blue-300">⑤ 总分层</p>
          <p className="text-[12px] text-slate-700 dark:text-slate-300 mt-1 font-mono font-bold">
            {score?.total ?? 0} / 100
          </p>
        </li>
        <li className={stageBase}>
          <p className="text-[11px] font-bold text-[#004782] dark:text-blue-300">⑥ 封顶与强制规则</p>
          <p className="text-[12px] text-slate-700 dark:text-slate-300 mt-1">
            等级封顶：{score?.level_cap ?? '未命中'}
          </p>
          <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
            强制规则：{score?.forced_rule ? Object.keys(score.forced_rule).join('、') : '未命中'}
          </p>
        </li>
        <li className={stageBase}>
          <p className="text-[11px] font-bold text-[#004782] dark:text-blue-300">⑦ 输出等级</p>
          <p className="text-[13px] font-black text-[#101d28] dark:text-white mt-1 font-mono">{score?.level ?? '—'}</p>
        </li>
      </ol>
    </section>
  );
};

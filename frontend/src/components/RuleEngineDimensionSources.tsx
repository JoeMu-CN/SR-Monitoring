import React from 'react';
import {Link} from 'react-router-dom';
import type {DimensionInputsRead} from '../api';
import {routePaths, sourceSignalsPath} from '../routes';
import type {MonitoringDimension} from '../types';

interface RuleEngineDimensionSourcesProps {
  dimension: MonitoringDimension;
  inputs: DimensionInputsRead | null;
  inputsError: string;
}

const formatDateTime = (value: string | null): string => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
};

/**
 * 维度引用信息源（观察态/配置态共用，只读）。
 *
 * 信息架构精简（todo 9）：
 * - 默认只渲染已接入（linked=true）的声明信源，保留原有来源深链与启用状态；
 * - 未接入声明项折叠为一行「另有 N 个声明信源未接入」+「去信息源接入」链接（routePaths.sources）；
 * - 零接入维度（`declared_linked === 0`）显著标注「无已接入信源，当前不会产生提醒」；
 * - 输入健康度区块保留；折叠计数与零接入判定以 `api.dimensionInputs` 的声明计数为权威口径，
 *   `dimension.dataSources` 快照仅在接口未返回（加载中/失败）时兜底，不覆盖接口计数。
 */
export const RuleEngineDimensionSources: React.FC<RuleEngineDimensionSourcesProps> = ({dimension, inputs, inputsError}) => {
  const linkedSources = dimension.dataSources.filter((source) => source.linked);
  // 折叠计数与零接入判定以接口声明计数（declared_total/declared_linked）为准：维度快照与接口计数
  // 来自相互独立的请求，接口可用时它就是唯一权威口径。快照仅在接口未返回（inputs===null，
  // 加载中/失败）时兜底，绝不覆盖接口值。
  const unlinkedCount = inputs !== null
    ? Math.max(0, inputs.declared_total - inputs.declared_linked)
    : dimension.dataSources.length - linkedSources.length;
  const noLinkedSources = inputs !== null
    ? inputs.declared_linked === 0
    : linkedSources.length === 0;

  return (
    <section
      data-testid="rule-engine-dimension-sources"
      className="rounded-xl bg-[#f7f9ff] dark:bg-slate-950/40 border border-slate-200 dark:border-slate-800 p-3"
    >
      <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300 mb-2">引用信息源</h3>

      {noLinkedSources && (
        <p
          data-testid="rule-engine-no-linked-sources"
          className="mb-2 flex items-start gap-1.5 rounded-lg border border-amber-300 bg-amber-50 px-2 py-1.5 text-[11px] font-bold text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300"
        >
          <span className="material-symbols-outlined text-[14px] leading-none" aria-hidden="true">warning</span>
          <span>无已接入信源，当前不会产生提醒</span>
        </p>
      )}

      <div className="space-y-1.5">
        {linkedSources.map((source) => {
          const enabled = source.enabled === true;
          return (
            <div key={source.code} className="flex items-center justify-between gap-2 text-[11px]">
              <Link
                to={sourceSignalsPath(source.code, 'valid')}
                aria-label={`${source.name} 有效信号 ${source.validSignalCount ?? 0} 条`}
                className="rounded-sm text-slate-700 underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-slate-300"
              >
                {source.name}
              </Link>
              <span className="flex items-center gap-1.5">
                <span className={`shrink-0 rounded-full px-2 py-0.5 font-bold ${
                  enabled ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' : 'bg-slate-200 text-[#424751] dark:bg-slate-800 dark:text-slate-300'
                }`}>
                  {enabled ? '已启用' : '已停用'}
                </span>
                <span className="shrink-0 font-mono text-slate-500 dark:text-slate-400" title="最近采集时间">
                  {formatDateTime(source.lastCollectedAt)}
                </span>
                <span className="shrink-0 font-mono text-slate-500 dark:text-slate-400" title="有效信号数">
                  {source.validSignalCount ?? 0} 条
                </span>
              </span>
            </div>
          );
        })}
      </div>

      {unlinkedCount > 0 && (
        <div
          data-testid="rule-engine-unlinked-sources-summary"
          className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500 dark:text-slate-400"
        >
          <span>另有 {unlinkedCount} 个声明信源未接入</span>
          <Link
            to={routePaths.sources}
            className="shrink-0 rounded-sm font-bold text-[#004782] underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
          >
            去信息源接入
          </Link>
        </div>
      )}

      {/* 输入健康度摘要 */}
      <div
        data-testid="rule-engine-input-health"
        className="mt-2 pt-2 border-t border-slate-200 dark:border-slate-800"
      >
        {inputsError ? (
          <p role="alert" className="text-[11px] text-red-700 bg-red-50 border border-red-200 rounded-lg px-2 py-1 dark:text-red-300 dark:bg-red-950/30 dark:border-red-900">
            输入健康度加载失败：{inputsError}
          </p>
        ) : inputs === null ? (
          <p className="flex items-center gap-1 text-[11px] text-slate-400 dark:text-slate-500"><span className="sr-only">输入健康度加载中…</span></p>
        ) : inputs.has_input ? (
          <div className="space-y-1">
            <p className="text-[11px] font-bold text-slate-600 dark:text-slate-300">
              近 30 天输入：{inputs.observed.length} 个信源有信号
            </p>
            {inputs.observed.map((item) => (
              <div key={item.code} className="flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
                <span>{item.name}</span>
                <span className="font-mono">{item.signal_count} 条 · {formatDateTime(item.latest_at)}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[11px] text-slate-500 dark:text-slate-400">当前无输入</p>
        )}
      </div>
    </section>
  );
};

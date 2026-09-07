import React from 'react';
import {Link} from 'react-router-dom';
import type {DimensionInputsRead} from '../api';
import {sourceSignalsPath} from '../routes';
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

export const RuleEngineDimensionSources: React.FC<RuleEngineDimensionSourcesProps> = ({dimension, inputs, inputsError}) => {
  return (
    <section className="rounded-xl bg-[#f7f9ff] dark:bg-slate-950/40 border border-slate-200 dark:border-slate-800 p-3">
      <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300 mb-2">引用数据源</h3>
      <div className="space-y-1.5">
        {dimension.dataSources.map((source) => {
          if (!source.linked) {
            return (
              <div key={source.code} className="flex items-center justify-between gap-2 text-[11px]">
                <span className="text-slate-500 dark:text-slate-400">{source.name}</span>
                <span className="shrink-0 rounded-full bg-slate-200 px-2 py-0.5 font-bold text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                  未创建
                </span>
              </div>
            );
          }
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

      {/* 输入健康度摘要 */}
      <div className="mt-2 pt-2 border-t border-slate-200 dark:border-slate-800">
        {inputsError ? (
          <p role="alert" className="text-[11px] text-red-700 bg-red-50 border border-red-200 rounded-lg px-2 py-1 dark:text-red-300 dark:bg-red-950/30 dark:border-red-900">
            输入健康度加载失败：{inputsError}
          </p>
        ) : inputs === null ? (
          <p className="text-[11px] text-slate-400 dark:text-slate-500">输入健康度加载中…</p>
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

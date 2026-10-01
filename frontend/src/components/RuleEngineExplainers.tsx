import React from 'react';
import type {RuleEngineMode} from './RuleEngineContext';
import {
  LEVEL_CAP_NOTE,
  LEVEL_CAP_RULES,
  RULE_ENGINE_EXPLAINERS,
  TOTAL_SCORE_FORMULA,
} from './ruleEngineExplainerCopy';

export interface RuleEngineExplainersProps {
  /** 观察态与配置态都可读；说明层无 role 门控（viewer 也可读） */
  mode: RuleEngineMode;
}

/**
 * 配置项语义说明层（组件归属 todo 10）。
 *
 * 为匹配柱、事件类型、严重程度分值、关联类型分值、分级阈值、信号过滤规则
 * 各提供「一句定义 + 一个例子 + 当前值含义」，并解释总分公式与两条封顶规则。
 * 文案集中在 `ruleEngineExplainerCopy.ts`，本文件只负责紧凑渲染：
 * 定义行内常显，例子与当前值收在可展开的 details 里，避免淹没界面。
 */
export const RuleEngineExplainers: React.FC<RuleEngineExplainersProps> = ({mode}) => {
  return (
    <section
      data-testid="rule-engine-explainers"
      data-mode={mode}
      className="space-y-3 rounded-2xl border border-slate-200/80 dark:border-slate-700/60 bg-white/80 dark:bg-slate-800/60 p-4 shadow-sm"
    >
      <div>
        <h2 className="font-bold text-[15px] text-[#101d28] dark:text-white">规则语义说明</h2>
        <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
          每个配置项一句定义、一个例子与当前值含义；展开「例与当前值」看细节，只读可查。
        </p>
      </div>

      <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
        {RULE_ENGINE_EXPLAINERS.map((entry) => (
          <article
            key={entry.id}
            data-testid={`rule-engine-explainer-${entry.id}`}
            className="min-w-0 rounded-xl border border-slate-200 dark:border-slate-700 bg-[#f8fafc] dark:bg-slate-950/40 p-3"
          >
            <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300">{entry.title}</h3>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-600 dark:text-slate-300">{entry.definition}</p>
            <details className="mt-1.5">
              <summary className="cursor-pointer select-none text-[11px] font-bold text-[#004782] dark:text-blue-300">
                例与当前值
              </summary>
              <p className="mt-1 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">例：{entry.example}</p>
              <p className="mt-1 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">当前值：{entry.current}</p>
            </details>
          </article>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
        <article
          data-testid="rule-engine-explainer-total-score"
          className="min-w-0 rounded-xl border border-slate-200 dark:border-slate-700 bg-[#f8fafc] dark:bg-slate-950/40 p-3"
        >
          <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300">{TOTAL_SCORE_FORMULA.title}</h3>
          <p className="mt-1 text-[11px] leading-relaxed font-bold text-slate-600 dark:text-slate-300">
            {TOTAL_SCORE_FORMULA.formula}
          </p>
          <ul className="mt-1 list-disc pl-4 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
            {TOTAL_SCORE_FORMULA.parts.map((part) => (
              <li key={part}>{part}</li>
            ))}
          </ul>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">{TOTAL_SCORE_FORMULA.note}</p>
        </article>

        <article
          data-testid="rule-engine-explainer-level-caps"
          className="min-w-0 rounded-xl border border-slate-200 dark:border-slate-700 bg-[#f8fafc] dark:bg-slate-950/40 p-3"
        >
          <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300">等级封顶规则</h3>
          <div className="mt-1 space-y-1.5">
            {LEVEL_CAP_RULES.map((rule) => (
              <div key={rule.id}>
                <p className="flex flex-wrap items-center gap-1.5 text-[11px] font-bold text-slate-600 dark:text-slate-300">
                  <span>{rule.title}</span>
                  <span className="rounded-full bg-[#eef6ff] px-2 py-0.5 text-[10px] font-bold text-[#004782] dark:bg-blue-950/40 dark:text-blue-300">
                    {rule.cap}
                  </span>
                </p>
                <p className="mt-0.5 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">{rule.explanation}</p>
              </div>
            ))}
          </div>
          <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">{LEVEL_CAP_NOTE}</p>
        </article>
      </div>
    </section>
  );
};

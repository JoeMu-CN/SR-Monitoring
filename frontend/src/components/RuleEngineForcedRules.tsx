import React from 'react';
import {ForcedRuleRead, GlobalScoringConfigRead} from '../api';
import {RuleEngineMode, useRuleEngineContext} from './RuleEngineContext';

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

export interface RuleEngineForcedRulesProps {
  /** 由壳组件控制：仅配置态渲染 */
  mode: RuleEngineMode;
}

/**
 * 从全局层读模型安全提取强制规则列表（effective.forced_rules 为 Record 数组）。
 * todo 8 的编辑器同样以本函数为初始值来源，绝不用维度增量初始化。
 */
export function readForcedRules(config: GlobalScoringConfigRead | null): ForcedRuleRead[] {
  const raw = config?.effective?.forced_rules;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (typeof item !== 'object' || item === null) return [];
    const record = item as Record<string, unknown>;
    if (typeof record.name !== 'string') return [];
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

/**
 * 配置态-强制规则区块（挂载点冻结于 todo 4）。
 *
 * 当前为只读展示：数据来源是 GET /global-config 的全局层
 * （代码默认 ∪ 可全局化维度追加；不含维度增量，也与 selectedDim.forcedRules 无关）。
 * todo 8 将在本文件内替换为可增删改的编辑器并接 globalConfig.update。
 */
export const RuleEngineForcedRules: React.FC<RuleEngineForcedRulesProps> = ({mode}) => {
  const {globalConfig, globalConfigError} = useRuleEngineContext();

  if (mode !== 'config') return null;

  if (globalConfigError) {
    return (
      <section
        role="alert"
        data-testid="rule-engine-forced-rules"
        data-mode={mode}
        className="rounded-xl bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 p-3 text-[12px] text-red-700 dark:text-red-300"
      >
        全局强制规则加载失败：{globalConfigError}
      </section>
    );
  }

  if (globalConfig === null) {
    return (
      <section
        data-testid="rule-engine-forced-rules"
        data-mode={mode}
        className="rounded-xl bg-[#f7f9ff] dark:bg-slate-950/40 border border-slate-200 dark:border-slate-800 p-3"
      >
        <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300 mb-2">强制规则</h3>
        <p className="flex items-center gap-1 text-[11px] text-slate-400 dark:text-slate-500">
          <span className="material-symbols-outlined animate-spin text-[12px] leading-none" aria-hidden="true">progress_activity</span>
          <span className="sr-only">全局强制规则加载中…</span>
        </p>
      </section>
    );
  }

  const rules = readForcedRules(globalConfig);

  return (
    <section
      data-testid="rule-engine-forced-rules"
      data-mode={mode}
      className="rounded-xl bg-[#f7f9ff] dark:bg-slate-950/40 border border-slate-200 dark:border-slate-800 p-3"
    >
      <div className="flex items-center justify-between gap-2 mb-2">
        <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300">强制规则</h3>
        <span className="text-[10px] text-slate-400 dark:text-slate-500">
          全局层 {globalConfig.source === 'configured' ? '已自定义' : '默认'} · 命中后直接定级并记满分
        </span>
      </div>
      {rules.length === 0 ? (
        <p className="text-[11px] text-slate-500 dark:text-slate-400">当前全局层无强制规则。</p>
      ) : (
        <div className="space-y-2">
          {rules.map((rule) => (
            <div key={rule.name} className="rounded-lg bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 p-2.5 space-y-1">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[12px] font-bold text-[#101d28] dark:text-white font-mono">{rule.name}</span>
                <span className="shrink-0 rounded-full bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300 px-2 py-0.5 text-[10px] font-bold">
                  {rule.forced_level}
                </span>
              </div>
              {rule.description && <p className="text-[11px] text-slate-600 dark:text-slate-300">{rule.description}</p>}
              <div className="text-[11px] text-slate-500 dark:text-slate-400 space-y-0.5">
                {rule.event_types.length > 0 && <p>事件类型：{rule.event_types.join('、')}</p>}
                {rule.event_subtypes.length > 0 && <p>事件细类：{rule.event_subtypes.join('、')}</p>}
                {rule.match_types.length > 0 && (
                  <p>匹配类型：{rule.match_types.map((item) => MATCH_TYPE_LABELS[item] ?? item).join('、')}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
};

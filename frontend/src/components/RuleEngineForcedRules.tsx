import React from 'react';
import type {ForcedRuleRead} from '../api';

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

interface RuleEngineForcedRulesProps {
  rules: ForcedRuleRead[];
}

export const RuleEngineForcedRules: React.FC<RuleEngineForcedRulesProps> = ({rules}) => {
  if (rules.length === 0) {
    return (
      <section className="rounded-xl bg-[#f7f9ff] dark:bg-slate-950/40 border border-slate-200 dark:border-slate-800 p-3">
        <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300 mb-2">强制规则</h3>
        <p className="text-[11px] text-slate-500 dark:text-slate-400">当前维度无强制规则。</p>
      </section>
    );
  }

  return (
    <section className="rounded-xl bg-[#f7f9ff] dark:bg-slate-950/40 border border-slate-200 dark:border-slate-800 p-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300">强制规则</h3>
        <span className="text-[10px] text-slate-400 dark:text-slate-500">当前版本不可在界面编辑</span>
      </div>
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
              {rule.event_types.length > 0 && (
                <p>事件类型：{rule.event_types.join('、')}</p>
              )}
              {rule.event_subtypes.length > 0 && (
                <p>事件细类：{rule.event_subtypes.join('、')}</p>
              )}
              {rule.match_types.length > 0 && (
                <p>匹配类型：{rule.match_types.map((item) => MATCH_TYPE_LABELS[item] ?? item).join('、')}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
};

import React from 'react';
import {
  ASSOCIATION_MAX,
  DEFAULT_ASSOCIATION_SCORES,
  DEFAULT_SEVERITY_SCORES,
  RuleEngineMode,
  SEVERITY_MAX,
  useRuleEngineContext,
} from './RuleEngineContext';

// 严重程度与关联类型的展示标签（与后端 Severity / MatchType 对齐）
const SEVERITY_LABELS: Record<string, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
};

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

export interface RuleEngineScoringEditorProps {
  /** 由壳组件控制：仅配置态渲染编辑器本体 */
  mode: RuleEngineMode;
}

/**
 * 配置态-评分与阈值编辑器（挂载点冻结于 todo 4）。
 *
 * 当前实现为原「严重程度分值 / 关联类型分值 / 风险等级触发」三段表格的原样迁移：
 * 数值读写壳组件持有的维度草稿（context.draft），保存仍由壳组件头部按钮触发。
 * todo 7 将在本文件内替换为滑杆/步进 + 影响说明 + 解算预览，不改壳组件签名。
 */
export const RuleEngineScoringEditor: React.FC<RuleEngineScoringEditorProps> = ({mode}) => {
  const {draft, updateDraft} = useRuleEngineContext();

  if (mode !== 'config') return null;

  return (
    <div className="space-y-5" data-testid="rule-engine-scoring-editor" data-mode={mode}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <section className="space-y-2">
          <div className="flex justify-between text-[13px]">
            <span className="font-bold text-[#424751] dark:text-slate-300">严重程度分值 (0-{SEVERITY_MAX})</span>
          </div>
          <div className="rounded-xl bg-[#f7f9ff] dark:bg-slate-950/40 border border-slate-200 dark:border-slate-800 overflow-hidden">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-left text-[11px] text-slate-500 border-b border-slate-200 dark:border-slate-800">
                  <th className="px-3 py-2 font-bold">严重程度</th>
                  <th className="px-3 py-2 font-bold text-right">当前值</th>
                  <th className="px-3 py-2 font-bold text-right">默认值</th>
                </tr>
              </thead>
              <tbody>
                {Object.keys(draft.severityScores).map((key) => (
                  <tr key={key} className="border-b border-slate-100 dark:border-slate-800 last:border-0">
                    <td className="px-3 py-2 text-slate-700 dark:text-slate-300">{SEVERITY_LABELS[key] ?? key}</td>
                    <td className="px-3 py-2 text-right">
                      <input
                        type="number"
                        aria-label={`严重程度 ${SEVERITY_LABELS[key] ?? key}`}
                        min="0"
                        max={SEVERITY_MAX}
                        value={draft.severityScores[key]}
                        onChange={(e) => updateDraft({severityScores: {...draft.severityScores, [key]: Number(e.target.value)}})}
                        className="w-20 bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-700 rounded px-2 py-0.5 text-right font-mono font-bold text-[#101d28] dark:text-white"
                      />
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-slate-500">{DEFAULT_SEVERITY_SCORES[key] ?? draft.severityScores[key]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="space-y-2">
          <div className="flex justify-between text-[13px]">
            <span className="font-bold text-[#424751] dark:text-slate-300">关联类型分值 (0-{ASSOCIATION_MAX})</span>
          </div>
          <div className="rounded-xl bg-[#f7f9ff] dark:bg-slate-950/40 border border-slate-200 dark:border-slate-800 overflow-hidden">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-left text-[11px] text-slate-500 border-b border-slate-200 dark:border-slate-800">
                  <th className="px-3 py-2 font-bold">关联类型</th>
                  <th className="px-3 py-2 font-bold text-right">当前值</th>
                  <th className="px-3 py-2 font-bold text-right">默认值</th>
                </tr>
              </thead>
              <tbody>
                {Object.keys(draft.associationScores).map((key) => (
                  <tr key={key} className="border-b border-slate-100 dark:border-slate-800 last:border-0">
                    <td className="px-3 py-2 text-slate-700 dark:text-slate-300">{ASSOCIATION_LABELS[key] ?? key}</td>
                    <td className="px-3 py-2 text-right">
                      <input
                        type="number"
                        aria-label={`关联类型 ${ASSOCIATION_LABELS[key] ?? key}`}
                        min="0"
                        max={ASSOCIATION_MAX}
                        value={draft.associationScores[key]}
                        onChange={(e) => updateDraft({associationScores: {...draft.associationScores, [key]: Number(e.target.value)}})}
                        className="w-20 bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-700 rounded px-2 py-0.5 text-right font-mono font-bold text-[#101d28] dark:text-white"
                      />
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-slate-500">{DEFAULT_ASSOCIATION_SCORES[key] ?? draft.associationScores[key]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {/* Risk Level Trigger Thresholds */}
      <div className="space-y-2">
        <label className="text-[13px] font-bold text-[#424751] dark:text-slate-300">
          风险等级触发 (P1-P3)
        </label>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="p-3 border-2 border-red-200 bg-red-50/40 rounded-xl space-y-1">
            <div className="text-[11px] font-bold text-[#C92A2A]">P1 极高风险</div>
            <div className="flex items-center gap-1 font-mono font-bold text-[14px]">
              <span>&ge;</span>
              <input
                type="number"
                aria-label="P1 触发阈值"
                value={draft.thresholds.p1}
                onChange={(e) => updateDraft({thresholds: {...draft.thresholds, p1: Number(e.target.value)}})}
                className="w-full bg-white border border-red-300 rounded px-2 py-0.5 text-center text-[#C92A2A]"
              />
            </div>
          </div>

          <div className="p-3 border-2 border-amber-200 bg-amber-50/40 rounded-xl space-y-1">
            <div className="text-[11px] font-bold text-[#D97706]">P2 高风险</div>
            <div className="flex items-center gap-1 font-mono font-bold text-[14px]">
              <span>&ge;</span>
              <input
                type="number"
                aria-label="P2 触发阈值"
                value={draft.thresholds.p2}
                onChange={(e) => updateDraft({thresholds: {...draft.thresholds, p2: Number(e.target.value)}})}
                className="w-full bg-white border border-amber-300 rounded px-2 py-0.5 text-center text-[#D97706]"
              />
            </div>
          </div>

          <div className="p-3 border-2 border-blue-200 bg-blue-50/40 rounded-xl space-y-1">
            <div className="text-[11px] font-bold text-[#2563EB]">P3 中等风险</div>
            <div className="flex items-center gap-1 font-mono font-bold text-[14px]">
              <span>&ge;</span>
              <input
                type="number"
                aria-label="P3 触发阈值"
                value={draft.thresholds.p3}
                onChange={(e) => updateDraft({thresholds: {...draft.thresholds, p3: Number(e.target.value)}})}
                className="w-full bg-white border border-blue-300 rounded px-2 py-0.5 text-center text-[#2563EB]"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

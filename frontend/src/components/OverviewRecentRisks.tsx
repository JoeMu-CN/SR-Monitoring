import {ChevronRight, Info, ShieldCheck} from 'lucide-react';
import type {RiskItem} from '../types';

interface OverviewRecentRisksProps {
  readonly recentRisks: readonly RiskItem[];
  readonly onSelectRisk: (item: RiskItem) => void;
  readonly onViewAllRisks: () => void;
}

const RiskBadge = ({level}: {readonly level: RiskItem['level']}) => (
  <span className={`rounded-md px-2 py-0.5 text-[10px] font-bold text-white shadow-sm ${level === 'P1' ? 'bg-[#C92A2A]' : level === 'P2' ? 'bg-[#D97706]' : level === 'P3' ? 'bg-[#2563EB]' : 'bg-[#64748B]'}`}>{level}</span>
);

// 「最近风险提醒」区块：从 OverviewView 抽出的职责单一子组件（任务8 LOC 约束）。
// 渲染行为与抽取前逐字一致：桌面表格 + 移动端卡片列表。
export const OverviewRecentRisks = ({recentRisks, onSelectRisk, onViewAllRisks}: OverviewRecentRisksProps) => (
  <section className="flex flex-col overflow-hidden rounded-2xl border border-slate-200/80 bg-white/80 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60 lg:col-span-8">
    <div className="flex items-center justify-between border-b border-slate-200/80 bg-slate-50/60 p-3.5 dark:border-slate-700/60 dark:bg-slate-900/30">
      <div className="flex items-center gap-2">
        <h2 className="text-[15px] font-bold text-slate-900 dark:text-white">最近风险提醒</h2>
        <span className="rounded-full bg-[#185fa5] px-2 py-0.5 text-[10px] font-extrabold text-white">{recentRisks.length} 条</span>
      </div>
      <button type="button" onClick={onViewAllRisks} className="flex items-center gap-0.5 text-[12px] font-bold text-[#185fa5] hover:underline">查看完整风险中心<ChevronRight className="h-4 w-4"/></button>
    </div>
    {recentRisks.length === 0 ? (
      <div className="my-auto flex flex-col items-center justify-center p-10 text-center">
        <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl border border-blue-100 bg-blue-50 text-[#185fa5] dark:border-slate-700 dark:bg-slate-800"><ShieldCheck className="h-7 w-7"/></div>
        <h3 className="text-[15px] font-bold text-slate-900 dark:text-white">暂无当前风险提醒</h3>
        <p className="mt-1 max-w-sm text-[12px] text-slate-500">完成信息源采集后，新的风险信号会显示在这里。</p>
      </div>
    ) : (
      <>
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full border-collapse text-left">
            <thead className="border-b border-slate-200/80 bg-slate-100/70 text-[11px] font-bold uppercase text-slate-500 dark:border-slate-700/60 dark:bg-slate-800/80 dark:text-slate-400">
              <tr>
                <th className="p-3 pl-4">供应商主体</th>
                <th className="p-3">级别</th>
                <th className="p-3">风险类型</th>
                <th className="p-3 text-right">AI 置信度</th>
                <th className="p-3">更新时间</th>
                <th className="p-3 pr-4 text-center">详情</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-[13px] dark:divide-slate-800/60">
              {recentRisks.map((item) => (
                <tr key={item.id} onClick={() => onSelectRisk(item)} className="cursor-pointer transition-colors hover:bg-[#185fa5]/5 dark:hover:bg-slate-700/40">
                  <td className="p-3 pl-4 font-bold text-slate-900 dark:text-white"><span className="block max-w-[180px] truncate sm:max-w-none">{item.companyName}</span></td>
                  <td className="p-3"><RiskBadge level={item.level}/></td>
                  <td className="p-3 font-medium text-slate-600 dark:text-slate-300">{item.riskType}</td>
                  <td className="p-3 text-right font-mono font-bold text-[#185fa5] dark:text-blue-400">{item.aiConfidence}%</td>
                  <td className="p-3 text-[12px] font-mono text-slate-400">{item.updatedTime}</td>
                  <td className="p-3 pr-4 text-center">
                    <button type="button" title="查看详情" onClick={(event) => { event.stopPropagation(); onSelectRisk(item); }} className="rounded-lg p-1 text-[#185fa5] hover:bg-blue-100/60"><Info className="h-4 w-4"/></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="divide-y divide-slate-100 md:hidden dark:divide-slate-800/60">
          {recentRisks.map((item) => (
            <button key={item.id} type="button" onClick={() => onSelectRisk(item)} className="flex w-full flex-col gap-2 p-3.5 text-left transition-colors hover:bg-[#185fa5]/5 dark:hover:bg-slate-700/40">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate font-bold text-slate-900 dark:text-white">{item.companyName}</span>
                <RiskBadge level={item.level}/>
              </div>
              <div className="flex items-center justify-between gap-2 text-[12px] text-slate-500 dark:text-slate-400">
                <span className="min-w-0 truncate">{item.riskType}</span>
                <span className="shrink-0 font-mono font-bold text-[#185fa5] dark:text-blue-400">AI {item.aiConfidence}%</span>
              </div>
              <span className="font-mono text-[11px] text-slate-400">{item.updatedTime}</span>
            </button>
          ))}
        </div>
      </>
    )}
  </section>
);

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {RiskItem, DataSource, Supplier} from '../types';

interface OverviewViewProps {
  riskItems: RiskItem[];
  suppliers: Supplier[];
  dataSources: DataSource[];
  onSelectRisk: (item: RiskItem) => void;
  onViewAllRisks: () => void;
}

export const OverviewView: React.FC<OverviewViewProps> = ({
  riskItems,
  suppliers,
  dataSources,
  onSelectRisk,
  onViewAllRisks,
}) => {
  const [warningDismissed, setWarningDismissed] = useState(false);
  const recentRisks = riskItems.slice(0, 5);
  const levelCounts = Object.fromEntries(['P1', 'P2', 'P3', 'P4'].map((level) => [level, riskItems.filter((item) => item.level === level).length]));
  const delayedSources = dataSources.filter((source) => source.status !== 'normal');
  const typeCounts = Object.entries(riskItems.reduce<Record<string, number>>((counts, item) => {
    counts[item.riskType] = (counts[item.riskType] ?? 0) + 1;
    return counts;
  }, {})).sort((a, b) => b[1] - a[1]).slice(0, 3);

  return (
    <div className="space-y-6 pb-20 lg:pb-8">
      {/* Top Title & Time Range Filter */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[#101d28] dark:text-white tracking-tight">
            风险总览
          </h1>
          <p className="text-xs text-[#424751] dark:text-slate-400 mt-0.5">
            全球供应链网络实时风险监控与预警平台
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 bg-white dark:bg-slate-800 border border-[#c2c6d2] dark:border-slate-700 px-3 py-1.5 rounded-lg text-xs font-medium shadow-2xs">
            <span className="material-symbols-outlined text-[16px] text-[#004782]">calendar_month</span>
            <span>当前有效风险</span>
          </div>
          <span className="text-xs text-[#727782] dark:text-slate-400 font-mono hidden md:inline">
            最后更新: {riskItems[0]?.updatedTime ?? '暂无数据'}
          </span>
        </div>
      </div>

      {/* Warning Banner (Conditional) */}
      {!warningDismissed && delayedSources.length > 0 && (
        <div className="bg-[#ffdad6] border border-[#ba1a1a] rounded-xl p-3.5 flex items-start justify-between gap-3 shadow-xs animate-in fade-in">
          <div className="flex items-start gap-3">
            <span className="material-symbols-outlined text-[#ba1a1a] text-[22px] mt-0.5 flex-shrink-0">
              error
            </span>
            <div>
              <h4 className="font-bold text-[14px] text-[#93000a]">数据源运行提示</h4>
              <p className="text-[12px] text-[#93000a]/90 mt-0.5 leading-relaxed">
                {delayedSources.map((source) => `${source.name}：${source.latency}`).join('；')}
              </p>
            </div>
          </div>
          <button
            onClick={() => setWarningDismissed(true)}
            className="text-[#93000a] hover:bg-[#ba1a1a]/10 p-1 rounded-lg transition-colors"
            title="关闭警告"
          >
            <span className="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>
      )}

      {/* Stat Cards Row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <motion.div
          whileHover={{ y: -4, transition: { duration: 0.2 } }}
          className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 shadow-2xs cursor-default"
        >
          <div className="text-[12px] font-medium text-[#424751] dark:text-slate-400">活跃供应商</div>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-2xl lg:text-3xl font-extrabold font-mono text-[#101d28] dark:text-white">
              {suppliers.filter((supplier) => supplier.monitoringStatus !== 'paused').length}
            </span>
            <span className="text-xs font-bold text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded">
              监控中
            </span>
          </div>
        </motion.div>

        <motion.div
          whileHover={{ y: -4, transition: { duration: 0.2 } }}
          className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 border-l-4 border-l-[#C92A2A] rounded-xl p-4 shadow-2xs cursor-default"
        >
          <div className="text-[12px] font-medium text-[#424751] dark:text-slate-400">高危预警 (P1)</div>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-2xl lg:text-3xl font-extrabold font-mono text-[#C92A2A]">
              {levelCounts.P1}
            </span>
            <span className="text-xs font-bold text-[#C92A2A] bg-red-50 px-1.5 py-0.5 rounded animate-pulse">
              当前有效
            </span>
          </div>
        </motion.div>

        <motion.div
          whileHover={{ y: -4, transition: { duration: 0.2 } }}
          className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 border-l-4 border-l-[#D97706] rounded-xl p-4 shadow-2xs cursor-default"
        >
          <div className="text-[12px] font-medium text-[#424751] dark:text-slate-400">高风险 (P2)</div>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-2xl lg:text-3xl font-extrabold font-mono text-[#D97706]">
              {levelCounts.P2}
            </span>
            <span className="text-xs font-bold text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded">
              当前有效
            </span>
          </div>
        </motion.div>

        <motion.div
          whileHover={{ y: -4, transition: { duration: 0.2 } }}
          className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 shadow-2xs cursor-default"
        >
          <div className="text-[12px] font-medium text-[#424751] dark:text-slate-400">当前风险总计</div>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-2xl lg:text-3xl font-extrabold font-mono text-[#004782] dark:text-blue-400">
              {riskItems.length}
            </span>
            <span className="text-xs font-medium text-slate-500">只读提醒</span>
          </div>
        </motion.div>
      </div>

      {/* 全网风险刻度带 */}
      <section className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 lg:p-5 shadow-2xs">
        <h2 className="font-bold text-[16px] text-[#101d28] dark:text-white mb-3">
          全网风险刻度带
        </h2>
        <div className="flex w-full h-16 rounded-lg overflow-hidden shadow-inner text-white font-mono">
          {/* P1 */}
          <div className="flex-1 bg-[#C92A2A] flex flex-col justify-center items-center border-r border-white/20 px-2 transition-all hover:opacity-90">
            <span className="text-[11px] font-bold tracking-wider">P1 严重</span>
            <div className="flex items-baseline gap-1 mt-0.5">
              <span className="text-xl font-black">{levelCounts.P1}</span>
            </div>
          </div>
          {/* P2 */}
          <div className="flex-[2] bg-[#D97706] flex flex-col justify-center items-center border-r border-white/20 px-2 transition-all hover:opacity-90">
            <span className="text-[11px] font-bold tracking-wider">P2 高风险</span>
            <div className="flex items-baseline gap-1 mt-0.5">
              <span className="text-xl font-black">{levelCounts.P2}</span>
            </div>
          </div>
          {/* P3 */}
          <div className="flex-[4] bg-[#2563EB] flex flex-col justify-center items-center border-r border-white/20 px-2 transition-all hover:opacity-90">
            <span className="text-[11px] font-bold tracking-wider">P3 中度</span>
            <div className="flex items-baseline gap-1 mt-0.5">
              <span className="text-xl font-black">{levelCounts.P3}</span>
            </div>
          </div>
          {/* P4 */}
          <div className="flex-[8] bg-[#64748B] flex flex-col justify-center items-center px-2 transition-all hover:opacity-90">
            <span className="text-[11px] font-bold tracking-wider">P4 轻微关注</span>
            <div className="flex items-baseline gap-1 mt-0.5">
              <span className="text-xl font-black">{levelCounts.P4}</span>
            </div>
          </div>
        </div>
        <div className="mt-3 flex justify-between text-[12px] text-[#424751] dark:text-slate-400 font-medium">
          <span>总监控企业: <strong className="font-mono text-[#101d28] dark:text-white">{suppliers.length}</strong> 家</span>
          <span>当前风险提醒: <strong className="font-mono text-[#C92A2A]">{riskItems.length}</strong> 条</span>
        </div>
      </section>

      {/* Dashboard Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: Recent Alerts Table (8 cols) */}
        <div className="lg:col-span-8 bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl flex flex-col overflow-hidden shadow-2xs">
          <div className="p-4 border-b border-[#c2c6d2] dark:border-slate-800 flex justify-between items-center bg-[#f7f9ff] dark:bg-slate-800/50">
            <div className="flex items-center gap-2">
              <h2 className="font-bold text-[16px] text-[#101d28] dark:text-white">最近风险提醒</h2>
              <span className="bg-[#185fa5] text-white text-[11px] font-bold px-2 py-0.5 rounded-full">
                {recentRisks.length} 条
              </span>
            </div>
            <button
              onClick={onViewAllRisks}
              className="text-[13px] font-bold text-[#004782] dark:text-blue-400 hover:underline flex items-center gap-1"
            >
              <span>查看全部</span>
              <span className="material-symbols-outlined text-[16px]">arrow_forward</span>
            </button>
          </div>

          {/* Table or Empty State */}
          {recentRisks.length === 0 ? (
            <div className="p-10 flex flex-col items-center justify-center text-center my-auto">
              <div className="w-16 h-16 rounded-full bg-blue-50 dark:bg-slate-800 flex items-center justify-center text-[#004782] mb-3">
                <span className="material-symbols-outlined text-[32px]">verified_user</span>
              </div>
              <h3 className="font-bold text-[16px] text-[#101d28] dark:text-white">暂无当前风险提醒</h3>
              <p className="text-[12px] text-slate-500 max-w-sm mt-1">
                完成风险信号导入和处理后，提醒将显示在这里。
              </p>
              <button
                onClick={onViewAllRisks}
                className="mt-4 px-4 py-2 bg-[#004782] text-white rounded-lg text-xs font-medium hover:bg-[#185fa5] transition-colors flex items-center gap-1.5"
              >
                <span className="material-symbols-outlined text-[16px]">refresh</span>
                <span>刷新状态 / 加载数据</span>
              </button>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead className="bg-[#dceaf9]/50 dark:bg-slate-800 text-[11px] font-bold uppercase tracking-wider text-[#424751] dark:text-slate-300 border-b border-[#c2c6d2]">
                  <tr>
                    <th className="p-3 pl-4">供应商主体</th>
                    <th className="p-3">级别</th>
                    <th className="p-3">风险类型</th>
                    <th className="p-3 text-right">AI置信度</th>
                    <th className="p-3">更新时间</th>
                    <th className="p-3 pr-4 text-center">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#c2c6d2]/50 text-[13px]">
                  {recentRisks.map((item) => {
                    let levelColor = 'bg-[#64748B]';
                    if (item.level === 'P1') levelColor = 'bg-[#C92A2A]';
                    if (item.level === 'P2') levelColor = 'bg-[#D97706]';
                    if (item.level === 'P3') levelColor = 'bg-[#2563EB]';

                    return (
                      <tr
                        key={item.id}
                        onClick={() => onSelectRisk(item)}
                        className="hover:bg-[#ecf4ff]/60 dark:hover:bg-slate-800/80 transition-colors cursor-pointer"
                      >
                        <td className="p-3 pl-4 font-bold text-[#101d28] dark:text-white flex items-center gap-2">
                          <span className="truncate max-w-[180px] sm:max-w-none">{item.companyName}</span>
                        </td>
                        <td className="p-3">
                          <span className={`${levelColor} text-white font-bold text-[11px] px-2 py-0.5 rounded shadow-2xs`}>
                            {item.level}
                          </span>
                        </td>
                        <td className="p-3 text-[#424751] dark:text-slate-300 font-medium">
                          {item.riskType}
                        </td>
                        <td className="p-3 text-right font-mono font-bold text-[#004782] dark:text-blue-400">
                          {item.aiConfidence}%
                        </td>
                        <td className="p-3 text-[12px] text-slate-500 font-mono">
                          {item.updatedTime}
                        </td>
                        <td className="p-3 pr-4 text-center">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelectRisk(item);
                            }}
                            className="text-[#004782] dark:text-blue-400 hover:bg-[#d6e4f3] p-1.5 rounded-lg transition-colors"
                            title="查看详情"
                          >
                            <span className="material-symbols-outlined text-[18px]">visibility</span>
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Right Column: Distribution & Data Source Status (4 cols) */}
        <div className="lg:col-span-4 space-y-6">
          {/* Risk Type Distribution Chart */}
          <div className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 shadow-2xs">
            <h3 className="font-bold text-[15px] text-[#101d28] dark:text-white mb-3">
              风险类型分布
            </h3>
            <div className="space-y-3">
              {typeCounts.length === 0 ? <div className="text-[12px] text-slate-400">暂无当前风险分布</div> : typeCounts.map(([type, count], index) => {
                const percent = Math.round((count / Math.max(1, riskItems.length)) * 100);
                const colors = ['bg-[#004782]', 'bg-[#555f6b]', 'bg-[#727782]'];
                return <div key={type}>
                  <div className="flex justify-between text-[12px] font-medium mb-1"><span>{type}</span><span className="font-mono font-bold">{percent}%</span></div>
                  <div className="w-full bg-slate-100 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
                    <div className={`${colors[index]} h-full rounded-full`} style={{width: `${percent}%`}}></div>
                  </div>
                </div>;
              })}
            </div>
          </div>

          {/* Data Source Status */}
          <div className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 shadow-2xs">
            <div className="flex justify-between items-center mb-3">
              <h3 className="font-bold text-[15px] text-[#101d28] dark:text-white">数据源状态</h3>
              <span className="material-symbols-outlined text-slate-400 text-[18px] animate-spin-slow">
                sync
              </span>
            </div>

            <div className="space-y-2">
              {dataSources.slice(0, 2).map((ds) => (
                <div
                  key={ds.id}
                  className={`flex justify-between items-center p-2.5 border rounded-lg text-[12px] font-medium ${
                      ds.status !== 'normal'
                      ? 'border-[#ba1a1a] bg-[#ffdad6]/40 text-[#93000a]'
                      : 'border-[#c2c6d2] bg-[#f7f9ff] dark:bg-slate-800 text-[#101d28] dark:text-white'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <div
                      className={`w-2 h-2 rounded-full ${
                        ds.status !== 'normal' ? 'bg-[#ba1a1a]' : 'bg-[#10B981]'
                      }`}
                    ></div>
                    <span>{ds.name}</span>
                  </div>
                  <span className={ds.status !== 'normal' ? 'font-bold' : 'text-slate-500'}>
                    {ds.latency}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

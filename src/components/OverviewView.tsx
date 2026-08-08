import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { RiskItem, DataSource } from '../types';

interface OverviewViewProps {
  riskItems: RiskItem[];
  dataSources: DataSource[];
  onSelectRisk: (item: RiskItem) => void;
  onViewAllRisks: () => void;
  isSimulatedEmpty: boolean;
  setIsSimulatedEmpty: (val: boolean) => void;
}

export const OverviewView: React.FC<OverviewViewProps> = ({
  riskItems,
  dataSources,
  onSelectRisk,
  onViewAllRisks,
  isSimulatedEmpty,
  setIsSimulatedEmpty,
}) => {
  const [warningDismissed, setWarningDismissed] = useState(false);
  const [timeRange, setTimeRange] = useState('30');

  // Filter top recent risks
  const recentRisks = isSimulatedEmpty ? [] : riskItems.slice(0, 5);

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
            <span>最近</span>
            <select
              value={timeRange}
              onChange={(e) => setTimeRange(e.target.value)}
              className="bg-transparent font-bold text-[#004782] dark:text-blue-400 focus:outline-none cursor-pointer"
            >
              <option value="7">7 天</option>
              <option value="30">30 天</option>
              <option value="90">90 天</option>
            </select>
          </div>
          <span className="text-xs text-[#727782] dark:text-slate-400 font-mono hidden md:inline">
            最后更新: 2023-10-27 14:32:01
          </span>
        </div>
      </div>

      {/* Warning Banner (Conditional) */}
      {!warningDismissed && (
        <div className="bg-[#ffdad6] border border-[#ba1a1a] rounded-xl p-3.5 flex items-start justify-between gap-3 shadow-xs animate-in fade-in">
          <div className="flex items-start gap-3">
            <span className="material-symbols-outlined text-[#ba1a1a] text-[22px] mt-0.5 flex-shrink-0">
              error
            </span>
            <div>
              <h4 className="font-bold text-[14px] text-[#93000a]">数据源同步延迟警告</h4>
              <p className="text-[12px] text-[#93000a]/90 mt-0.5 leading-relaxed">
                天眼查API接口响应时间超过阈值(5000ms)，目前切换至备用缓存节点，部分数据可能存在最高2小时延迟。
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
              1,248
            </span>
            <span className="text-xs font-bold text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded">
              +12
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
              14
            </span>
            <span className="text-xs font-bold text-[#C92A2A] bg-red-50 px-1.5 py-0.5 rounded animate-pulse">
              +3
            </span>
          </div>
        </motion.div>

        <motion.div
          whileHover={{ y: -4, transition: { duration: 0.2 } }}
          className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 border-l-4 border-l-[#D97706] rounded-xl p-4 shadow-2xs cursor-default"
        >
          <div className="text-[12px] font-medium text-[#424751] dark:text-slate-400">中度风险 (P2)</div>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-2xl lg:text-3xl font-extrabold font-mono text-[#D97706]">
              86
            </span>
            <span className="text-xs font-bold text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded">
              -5
            </span>
          </div>
        </motion.div>

        <motion.div
          whileHover={{ y: -4, transition: { duration: 0.2 } }}
          className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 shadow-2xs cursor-default"
        >
          <div className="text-[12px] font-medium text-[#424751] dark:text-slate-400">自动缓解率</div>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-2xl lg:text-3xl font-extrabold font-mono text-[#004782] dark:text-blue-400">
              74%
            </span>
            <span className="text-xs font-medium text-slate-500">模型评估</span>
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
              <span className="text-xl font-black">4</span>
              <span className="text-[11px] opacity-80">+1</span>
            </div>
          </div>
          {/* P2 */}
          <div className="flex-[2] bg-[#D97706] flex flex-col justify-center items-center border-r border-white/20 px-2 transition-all hover:opacity-90">
            <span className="text-[11px] font-bold tracking-wider">P2 高风险</span>
            <div className="flex items-baseline gap-1 mt-0.5">
              <span className="text-xl font-black">12</span>
              <span className="text-[11px] opacity-80">+3</span>
            </div>
          </div>
          {/* P3 */}
          <div className="flex-[4] bg-[#2563EB] flex flex-col justify-center items-center border-r border-white/20 px-2 transition-all hover:opacity-90">
            <span className="text-[11px] font-bold tracking-wider">P3 中度</span>
            <div className="flex items-baseline gap-1 mt-0.5">
              <span className="text-xl font-black">45</span>
              <span className="text-[11px] opacity-80">+12</span>
            </div>
          </div>
          {/* P4 */}
          <div className="flex-[8] bg-[#64748B] flex flex-col justify-center items-center px-2 transition-all hover:opacity-90">
            <span className="text-[11px] font-bold tracking-wider">P4 轻微关注</span>
            <div className="flex items-baseline gap-1 mt-0.5">
              <span className="text-xl font-black">128</span>
              <span className="text-[11px] opacity-80">+24</span>
            </div>
          </div>
        </div>
        <div className="mt-3 flex justify-between text-[12px] text-[#424751] dark:text-slate-400 font-medium">
          <span>总监控企业: <strong className="font-mono text-[#101d28] dark:text-white">4,592</strong> 家</span>
          <span>今日新增提醒: <strong className="font-mono text-[#C92A2A]">40</strong> 条</span>
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
                onClick={() => setIsSimulatedEmpty(false)}
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
              <div>
                <div className="flex justify-between text-[12px] font-medium mb-1">
                  <span>司法诉讼</span>
                  <span className="font-mono font-bold">42%</span>
                </div>
                <div className="w-full bg-slate-100 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
                  <div className="bg-[#004782] h-full rounded-full" style={{ width: '42%' }}></div>
                </div>
              </div>

              <div>
                <div className="flex justify-between text-[12px] font-medium mb-1">
                  <span>经营异常</span>
                  <span className="font-mono font-bold">28%</span>
                </div>
                <div className="w-full bg-slate-100 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
                  <div className="bg-[#555f6b] h-full rounded-full" style={{ width: '28%' }}></div>
                </div>
              </div>

              <div>
                <div className="flex justify-between text-[12px] font-medium mb-1">
                  <span>行政处罚</span>
                  <span className="font-mono font-bold">15%</span>
                </div>
                <div className="w-full bg-slate-100 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
                  <div className="bg-[#727782] h-full rounded-full" style={{ width: '15%' }}></div>
                </div>
              </div>
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
                    ds.status === 'warning'
                      ? 'border-[#ba1a1a] bg-[#ffdad6]/40 text-[#93000a]'
                      : 'border-[#c2c6d2] bg-[#f7f9ff] dark:bg-slate-800 text-[#101d28] dark:text-white'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <div
                      className={`w-2 h-2 rounded-full ${
                        ds.status === 'warning' ? 'bg-[#ba1a1a]' : 'bg-[#10B981]'
                      }`}
                    ></div>
                    <span>{ds.name}</span>
                  </div>
                  <span className={ds.status === 'warning' ? 'font-bold' : 'text-slate-500'}>
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

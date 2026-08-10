import React, { useState } from 'react';
import { motion } from 'motion/react';
import { AlertTriangle, X, ChevronRight, ShieldCheck, RefreshCw, Info, Radio } from 'lucide-react';
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
    <div className="space-y-5 pb-20 lg:pb-8">
      {/* Top Header Controls with macOS Segmented Time Selector */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
        <div>
          <h1 className="text-xl lg:text-2xl font-black text-slate-900 dark:text-white tracking-tight">
            全网供应链风险概览
          </h1>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            实时监控全球 Tier-1/2 供应商的法律诉讼、制裁和财务异动
          </p>
        </div>

        <div className="flex items-center gap-3">
          {/* macOS Segmented Time Range Pill */}
          <div className="flex items-center bg-slate-200/70 dark:bg-slate-800/80 p-1 rounded-xl border border-black/5 dark:border-white/5 text-xs font-semibold text-slate-700 dark:text-slate-300">
            {['7', '30', '90'].map((range) => (
              <button
                key={range}
                onClick={() => setTimeRange(range)}
                className={`px-3 py-1 rounded-lg transition-all cursor-pointer ${
                  timeRange === range
                    ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-white shadow-2xs font-bold'
                    : 'hover:text-slate-900 dark:hover:text-white'
                }`}
              >
                {range} 天
              </button>
            ))}
          </div>

          <span className="text-[11px] text-slate-400 font-mono hidden md:inline">
            上次全网巡检: 刚刚
          </span>
        </div>
      </div>

      {/* Warning Banner (macOS Alert Sheet style) */}
      {!warningDismissed && (
        <motion.div
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          className="bg-amber-500/10 dark:bg-amber-950/40 border border-amber-300/80 dark:border-amber-800/60 rounded-2xl p-3.5 flex items-start justify-between gap-3 shadow-2xs"
        >
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-[#ff9500] mt-0.5 flex-shrink-0" />
            <div>
              <h4 className="font-bold text-[13px] text-amber-900 dark:text-amber-200">
                数据源同步延迟提醒
              </h4>
              <p className="text-[12px] text-amber-800/90 dark:text-amber-300/80 mt-0.5 leading-relaxed">
                天眼查 API 响应时间增至 3200ms，系统已无缝启动边缘缓存节点，数据保持实时且准确。
              </p>
            </div>
          </div>
          <button
            onClick={() => setWarningDismissed(true)}
            className="text-amber-700 dark:text-amber-400 hover:bg-amber-500/20 p-1 rounded-lg transition-colors cursor-pointer"
            title="关闭"
          >
            <X className="w-4 h-4" />
          </button>
        </motion.div>
      )}

      {/* macOS Widget Stat Cards Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3.5">
        <motion.div
          whileHover={{ y: -2 }}
          className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 shadow-2xs"
        >
          <div className="flex justify-between items-center">
            <span className="text-[12px] font-semibold text-slate-500 dark:text-slate-400">在册供应商</span>
            <span className="w-2 h-2 rounded-full bg-[#007aff]" />
          </div>
          <div className="flex items-baseline gap-2 mt-2">
            <span className="text-2xl lg:text-3xl font-black font-mono text-slate-900 dark:text-white">
              1,248
            </span>
            <span className="text-[11px] font-bold text-[#34c759] bg-emerald-50 dark:bg-emerald-950/60 px-1.5 py-0.5 rounded-md">
              +12
            </span>
          </div>
        </motion.div>

        <motion.div
          whileHover={{ y: -2 }}
          className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-red-200/80 dark:border-red-900/40 rounded-2xl p-4 shadow-2xs"
        >
          <div className="flex justify-between items-center">
            <span className="text-[12px] font-semibold text-[#ff3b30]">高危预警 (P1)</span>
            <span className="w-2 h-2 rounded-full bg-[#ff3b30] animate-ping" />
          </div>
          <div className="flex items-baseline gap-2 mt-2">
            <span className="text-2xl lg:text-3xl font-black font-mono text-[#ff3b30]">
              14
            </span>
            <span className="text-[11px] font-bold text-white bg-[#ff3b30] px-1.5 py-0.5 rounded-md">
              紧急
            </span>
          </div>
        </motion.div>

        <motion.div
          whileHover={{ y: -2 }}
          className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-amber-200/80 dark:border-amber-900/40 rounded-2xl p-4 shadow-2xs"
        >
          <div className="flex justify-between items-center">
            <span className="text-[12px] font-semibold text-amber-600 dark:text-amber-400">中度风险 (P2)</span>
            <span className="w-2 h-2 rounded-full bg-[#ff9500]" />
          </div>
          <div className="flex items-baseline gap-2 mt-2">
            <span className="text-2xl lg:text-3xl font-black font-mono text-[#ff9500]">
              86
            </span>
            <span className="text-[11px] font-bold text-[#34c759] bg-emerald-50 dark:bg-emerald-950/60 px-1.5 py-0.5 rounded-md">
              -5
            </span>
          </div>
        </motion.div>

        <motion.div
          whileHover={{ y: -2 }}
          className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 shadow-2xs"
        >
          <div className="flex justify-between items-center">
            <span className="text-[12px] font-semibold text-slate-500 dark:text-slate-400">AI 智能处置率</span>
            <span className="w-2 h-2 rounded-full bg-[#af52de]" />
          </div>
          <div className="flex items-baseline gap-2 mt-2">
            <span className="text-2xl lg:text-3xl font-black font-mono text-[#007aff] dark:text-blue-400">
              74%
            </span>
            <span className="text-[11px] font-medium text-slate-400">模型打分</span>
          </div>
        </motion.div>
      </div>

      {/* macOS Full Network Risk Ribbon */}
      <section className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 lg:p-5 shadow-2xs">
        <div className="flex justify-between items-center mb-3">
          <h2 className="font-bold text-[15px] text-slate-900 dark:text-white">
            全网风险严重程度分布
          </h2>
          <span className="text-[11px] font-mono text-slate-400">四级联动防护</span>
        </div>

        <div className="flex w-full h-12 rounded-xl overflow-hidden shadow-xs text-white font-mono">
          <div className="flex-1 bg-[#ff3b30] flex flex-col justify-center items-center border-r border-white/20 px-2">
            <span className="text-[10px] font-extrabold tracking-wider">P1 极高</span>
            <span className="text-base font-black leading-none">4</span>
          </div>
          <div className="flex-[2] bg-[#ff9500] flex flex-col justify-center items-center border-r border-white/20 px-2">
            <span className="text-[10px] font-extrabold tracking-wider">P2 高危</span>
            <span className="text-base font-black leading-none">12</span>
          </div>
          <div className="flex-[4] bg-[#007aff] flex flex-col justify-center items-center border-r border-white/20 px-2">
            <span className="text-[10px] font-extrabold tracking-wider">P3 中度</span>
            <span className="text-base font-black leading-none">45</span>
          </div>
          <div className="flex-[8] bg-[#8e8e93] flex flex-col justify-center items-center px-2">
            <span className="text-[10px] font-extrabold tracking-wider">P4 低危关注</span>
            <span className="text-base font-black leading-none">128</span>
          </div>
        </div>
      </section>

      {/* macOS Main Views Dashboard Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
        {/* Left Column: Recent Alerts Finder List (8 cols) */}
        <div className="lg:col-span-8 bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl flex flex-col overflow-hidden shadow-2xs">
          <div className="p-3.5 border-b border-slate-200/80 dark:border-slate-700/60 flex justify-between items-center bg-slate-50/50 dark:bg-slate-900/30">
            <div className="flex items-center gap-2">
              <h2 className="font-bold text-[15px] text-slate-900 dark:text-white">最新风险提醒列表</h2>
              <span className="bg-[#007aff] text-white text-[10px] font-extrabold px-2 py-0.5 rounded-full">
                {recentRisks.length} 条
              </span>
            </div>
            <button
              onClick={onViewAllRisks}
              className="text-[12px] font-bold text-[#007aff] hover:underline flex items-center gap-0.5 cursor-pointer"
            >
              <span>查看完整风险中心</span>
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>

          {recentRisks.length === 0 ? (
            <div className="p-10 flex flex-col items-center justify-center text-center my-auto">
              <div className="w-14 h-14 rounded-2xl bg-blue-50 dark:bg-slate-800 flex items-center justify-center text-[#007aff] mb-3 border border-blue-100 dark:border-slate-700">
                <ShieldCheck className="w-7 h-7" />
              </div>
              <h3 className="font-bold text-[15px] text-slate-900 dark:text-white">系统安全：无未处理高危事件</h3>
              <p className="text-[12px] text-slate-500 max-w-sm mt-1">
                全网数据源正常运行，所有监测事件均已处理或处于观察状态。
              </p>
              <button
                onClick={() => setIsSimulatedEmpty(false)}
                className="mt-4 px-4 py-1.5 bg-[#007aff] text-white rounded-lg text-xs font-medium hover:bg-[#0062cc] transition-colors flex items-center gap-1.5 cursor-pointer shadow-xs"
              >
                <RefreshCw className="w-4 h-4" />
                <span>恢复模拟事件数据</span>
              </button>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead className="bg-slate-100/70 dark:bg-slate-800/80 text-[11px] font-bold uppercase text-slate-500 dark:text-slate-400 border-b border-slate-200/80 dark:border-slate-700/60">
                  <tr>
                    <th className="p-3 pl-4">供应商名称</th>
                    <th className="p-3">级别</th>
                    <th className="p-3">风险类别</th>
                    <th className="p-3 text-right">AI 置信度</th>
                    <th className="p-3">更新时间</th>
                    <th className="p-3 pr-4 text-center">详情</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60 text-[13px]">
                  {recentRisks.map((item) => {
                    let levelBadge = 'bg-slate-500 text-white';
                    if (item.level === 'P1') levelBadge = 'bg-[#ff3b30] text-white';
                    if (item.level === 'P2') levelBadge = 'bg-[#ff9500] text-white';
                    if (item.level === 'P3') levelBadge = 'bg-[#007aff] text-white';

                    return (
                      <tr
                        key={item.id}
                        onClick={() => onSelectRisk(item)}
                        className="hover:bg-[#007aff]/5 dark:hover:bg-slate-700/40 transition-colors cursor-pointer"
                      >
                        <td className="p-3 pl-4 font-bold text-slate-900 dark:text-white">
                          <span className="truncate max-w-[180px] sm:max-w-none block">{item.companyName}</span>
                        </td>
                        <td className="p-3">
                          <span className={`${levelBadge} font-bold text-[10px] px-2 py-0.5 rounded-md shadow-2xs`}>
                            {item.level}
                          </span>
                        </td>
                        <td className="p-3 text-slate-600 dark:text-slate-300 font-medium">
                          {item.riskType}
                        </td>
                        <td className="p-3 text-right font-mono font-bold text-[#007aff] dark:text-blue-400">
                          {item.aiConfidence}%
                        </td>
                        <td className="p-3 text-[12px] text-slate-400 font-mono">
                          {item.updatedTime}
                        </td>
                        <td className="p-3 pr-4 text-center">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelectRisk(item);
                            }}
                            className="text-[#007aff] hover:bg-blue-100/60 dark:hover:bg-slate-700 p-1 rounded-lg transition-colors cursor-pointer"
                            title="展开 Inspector 详单"
                          >
                            <Info className="w-4 h-4" />
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

        {/* Right Column: Distribution & Data Source Widget (4 cols) */}
        <div className="lg:col-span-4 space-y-5">
          {/* Risk Type Bar Widget */}
          <div className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 shadow-2xs">
            <h3 className="font-bold text-[14px] text-slate-900 dark:text-white mb-3">
              风险分布与占比
            </h3>
            <div className="space-y-3">
              <div>
                <div className="flex justify-between text-[12px] font-medium mb-1">
                  <span className="text-slate-700 dark:text-slate-300">司法诉讼 / 破产</span>
                  <span className="font-mono font-bold text-[#007aff]">42%</span>
                </div>
                <div className="w-full bg-slate-100 dark:bg-slate-700 h-2 rounded-full overflow-hidden">
                  <div className="bg-[#007aff] h-full rounded-full" style={{ width: '42%' }} />
                </div>
              </div>

              <div>
                <div className="flex justify-between text-[12px] font-medium mb-1">
                  <span className="text-slate-700 dark:text-slate-300">经营与税务异常</span>
                  <span className="font-mono font-bold text-[#ff9500]">28%</span>
                </div>
                <div className="w-full bg-slate-100 dark:bg-slate-700 h-2 rounded-full overflow-hidden">
                  <div className="bg-[#ff9500] h-full rounded-full" style={{ width: '28%' }} />
                </div>
              </div>

              <div>
                <div className="flex justify-between text-[12px] font-medium mb-1">
                  <span className="text-slate-700 dark:text-slate-300">国际制裁与合规</span>
                  <span className="font-mono font-bold text-[#ff3b30]">15%</span>
                </div>
                <div className="w-full bg-slate-100 dark:bg-slate-700 h-2 rounded-full overflow-hidden">
                  <div className="bg-[#ff3b30] h-full rounded-full" style={{ width: '15%' }} />
                </div>
              </div>
            </div>
          </div>

          {/* Data Sources macOS Preference Widget */}
          <div className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 shadow-2xs">
            <div className="flex justify-between items-center mb-3">
              <h3 className="font-bold text-[14px] text-slate-900 dark:text-white">数据源节点</h3>
              <Radio className="w-4 h-4 text-slate-400" />
            </div>

            <div className="space-y-2">
              {dataSources.slice(0, 2).map((ds) => (
                <div
                  key={ds.id}
                  className={`flex justify-between items-center p-2.5 border rounded-xl text-[12px] font-medium ${
                    ds.status === 'warning'
                      ? 'border-amber-300 bg-amber-50 dark:bg-amber-950/30 text-amber-900 dark:text-amber-200'
                      : 'border-slate-200/80 dark:border-slate-700 bg-slate-50/50 dark:bg-slate-900/40 text-slate-800 dark:text-slate-200'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={`w-2 h-2 rounded-full ${
                        ds.status === 'warning' ? 'bg-[#ff9500]' : 'bg-[#34c759]'
                      }`}
                    />
                    <span>{ds.name}</span>
                  </div>
                  <span className="font-mono text-slate-500 dark:text-slate-400 text-[11px]">
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



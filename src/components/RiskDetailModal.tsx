import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { RiskItem } from '../types';

interface RiskDetailModalProps {
  risk: RiskItem | null;
  onClose: () => void;
  onExportReport: (item: RiskItem) => void;
  onAskAssistant: (query: string) => void;
}

export const RiskDetailModal: React.FC<RiskDetailModalProps> = ({
  risk,
  onClose,
  onExportReport,
  onAskAssistant,
}) => {
  const [isMonitored, setIsMonitored] = useState(true);
  const score = risk?.overallScore || 92.5;

  return (
    <AnimatePresence>
      {risk && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-2 sm:p-4 overflow-y-auto"
          onClick={onClose}
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 15 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 15 }}
            transition={{ type: 'spring', stiffness: 350, damping: 28 }}
            className="bg-[#f7f9ff] dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-2xl w-full max-w-5xl max-h-[92vh] flex flex-col shadow-2xl overflow-hidden my-auto"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Top Sticky Bar */}
        <div className="bg-white dark:bg-slate-950 px-6 py-4 border-b border-[#c2c6d2] dark:border-slate-800 flex justify-between items-center sticky top-0 z-10">
          <button
            onClick={onClose}
            className="flex items-center gap-1.5 text-[14px] font-bold text-[#004782] dark:text-blue-400 hover:underline"
          >
            <span className="material-symbols-outlined text-[20px]">arrow_back</span>
            <span>返回列表</span>
          </button>

          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                onClose();
                onAskAssistant(`请深度分析【${risk.companyName}】的核心风险事件、影响程度及采购规避建议。`);
              }}
              className="px-3.5 py-1.5 bg-[#ecf4ff] dark:bg-blue-950/80 hover:bg-[#d6e4f3] dark:hover:bg-blue-900 border border-[#004782]/30 dark:border-blue-700/50 text-[#004782] dark:text-blue-300 rounded-lg text-[13px] font-bold flex items-center gap-1.5 transition-all shadow-2xs hover:shadow-xs"
              title="跳转至 AI 风险助手进行深入研判"
            >
              <span className="material-symbols-outlined text-[18px] text-[#004782] dark:text-blue-400">smart_toy</span>
              <span>询问风险助手</span>
            </button>

            <button
              onClick={() => onExportReport(risk)}
              className="px-3.5 py-1.5 border border-[#004782] text-[#004782] dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-slate-800 rounded-lg text-[13px] font-bold flex items-center gap-1.5 transition-colors"
            >
              <span className="material-symbols-outlined text-[18px]">download</span>
              <span>导出报告</span>
            </button>

            <button
              onClick={() => setIsMonitored(!isMonitored)}
              className={`px-3.5 py-1.5 rounded-lg text-[13px] font-bold flex items-center gap-1.5 transition-colors ${
                isMonitored
                  ? 'bg-[#004782] text-white hover:bg-[#185fa5]'
                  : 'bg-emerald-600 text-white hover:bg-emerald-700'
              }`}
            >
              <span className="material-symbols-outlined text-[18px]">
                {isMonitored ? 'notifications_active' : 'add_alert'}
              </span>
              <span>{isMonitored ? '已添加监控' : '添加监控'}</span>
            </button>
          </div>
        </div>

        {/* Modal Scrollable Content */}
        <div className="p-6 space-y-6 overflow-y-auto flex-1">
          {/* Header Title Section */}
          <div>
            <div className="flex flex-wrap items-center gap-2 mb-1.5">
              <span className="bg-[#C92A2A] text-white text-[12px] font-bold px-2.5 py-0.5 rounded shadow-2xs">
                {risk.level} {risk.levelName}风险
              </span>
              <span className="text-[12px] font-mono text-slate-500 font-bold">
                ID: {risk.vendorId || 'VEND-2023-8891A'}
              </span>
            </div>

            <h1 className="text-2xl font-black text-[#101d28] dark:text-white">
              {risk.companyName}
            </h1>

            <div className="flex flex-wrap items-center gap-4 mt-2 text-[12px] text-[#424751] dark:text-slate-400">
              <span className="flex items-center gap-1">
                <span className="material-symbols-outlined text-[16px]">domain</span>
                一级供应商
              </span>
              {risk.location && (
                <span className="flex items-center gap-1">
                  <span className="material-symbols-outlined text-[16px]">location_on</span>
                  {risk.location}
                </span>
              )}
              <span className="flex items-center gap-1">
                <span className="material-symbols-outlined text-[16px]">category</span>
                微电子与半导体
              </span>
            </div>

            <p className="text-[14px] text-slate-600 dark:text-slate-300 mt-3 bg-white dark:bg-slate-800 p-3.5 rounded-xl border border-slate-200 dark:border-slate-700 leading-relaxed">
              {risk.summary}
            </p>
          </div>

          {/* Overall Risk Evaluation Box */}
          <div className="bg-white dark:bg-slate-950 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-5 shadow-2xs grid grid-cols-1 md:grid-cols-12 gap-6 items-center">
            <div className="md:col-span-8 space-y-3">
              <div className="flex items-center gap-2 text-[#ba1a1a] font-bold text-[15px]">
                <span className="material-symbols-outlined text-[22px]">warning</span>
                <span>综合风险评估</span>
              </div>
              <p className="text-[13px] text-slate-600 dark:text-slate-400">
                系统基于多维度数据源的实时分析。当前评级要求立即采取干预措施。
              </p>

              {/* Risk Band Graphic */}
              <div className="space-y-1 pt-1">
                <div className="flex h-6 rounded-md overflow-hidden text-[10px] font-bold font-mono text-white">
                  <div className="flex-1 bg-[#64748B] flex items-center justify-center">P4 (低)</div>
                  <div className="flex-1 bg-[#2563EB] flex items-center justify-center">P3 (中)</div>
                  <div className="flex-1 bg-[#D97706] flex items-center justify-center">P2 (高)</div>
                  <div className="flex-1 bg-[#C92A2A] flex items-center justify-center ring-2 ring-red-900 shadow-md">
                    P1 (严重)
                  </div>
                </div>
              </div>

              {/* Ask Assistant Prompt Banner */}
              <div className="pt-2">
                <button
                  onClick={() => {
                    onClose();
                    onAskAssistant(`请分析【${risk.companyName}】的核心风险，并提供同类型备选供应商和规避方案。`);
                  }}
                  className="w-full flex items-center justify-between p-2.5 rounded-lg bg-[#ecf4ff]/80 dark:bg-slate-800 hover:bg-[#d6e4f3] dark:hover:bg-slate-700 transition-all text-[12px] font-bold text-[#004782] dark:text-blue-300 border border-[#004782]/20"
                >
                  <div className="flex items-center gap-2">
                    <span className="material-symbols-outlined text-[18px]">psychology</span>
                    <span>需要备选供应商预案或更深度的合规问答？</span>
                  </div>
                  <div className="flex items-center gap-1 text-[11px] font-bold">
                    <span>询问助手</span>
                    <span className="material-symbols-outlined text-[14px]">arrow_forward</span>
                  </div>
                </button>
              </div>
            </div>

            {/* Score Big Circle Box */}
            <div className="md:col-span-4 flex flex-col items-center justify-center bg-red-50 dark:bg-red-950/30 border-2 border-red-200 dark:border-red-900/50 rounded-2xl p-4 text-center">
              <div className="text-4xl font-black font-mono text-[#C92A2A]">{score}</div>
              <div className="text-[12px] font-bold text-[#93000a] mt-0.5">综合风险分 / 100</div>
            </div>
          </div>

          {/* 风险证据链 Step Chain */}
          <div className="bg-white dark:bg-slate-950 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-5 shadow-2xs space-y-4">
            <div className="flex items-center gap-2 font-bold text-[15px] text-[#101d28] dark:text-white">
              <span className="material-symbols-outlined text-[#004782] text-[22px]">hub</span>
              <span>风险证据链</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3 relative">
              <div className="bg-[#f7f9ff] dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-3.5 text-center space-y-1">
                <div className="w-10 h-10 rounded-full bg-blue-100 dark:bg-blue-900/40 text-[#004782] dark:text-blue-300 flex items-center justify-center mx-auto mb-2">
                  <span className="material-symbols-outlined text-[20px]">language</span>
                </div>
                <div className="text-[12px] font-bold">1. 原始来源</div>
                <div className="text-[11px] text-slate-500">{risk.source || '行业资讯/官报'}</div>
              </div>

              <div className="bg-[#f7f9ff] dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-3.5 text-center space-y-1">
                <div className="w-10 h-10 rounded-full bg-red-100 dark:bg-red-900/40 text-[#C92A2A] flex items-center justify-center mx-auto mb-2">
                  <span className="material-symbols-outlined text-[20px]">bolt</span>
                </div>
                <div className="text-[12px] font-bold">2. 风险事件</div>
                <div className="text-[11px] text-slate-500">{risk.eventCategory || '供应链中断/受阻'}</div>
              </div>

              <div className="bg-[#f7f9ff] dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-3.5 text-center space-y-1">
                <div className="w-10 h-10 rounded-full bg-blue-100 dark:bg-blue-900/40 text-[#004782] dark:text-blue-300 flex items-center justify-center mx-auto mb-2">
                  <span className="material-symbols-outlined text-[20px]">find_in_page</span>
                </div>
                <div className="text-[12px] font-bold">3. 供应商匹配</div>
                <div className="text-[11px] text-slate-500">精准匹配核心库</div>
              </div>

              <div className="bg-[#f7f9ff] dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-3.5 text-center space-y-1">
                <div className="w-10 h-10 rounded-full bg-amber-100 dark:bg-amber-900/40 text-[#D97706] flex items-center justify-center mx-auto mb-2">
                  <span className="material-symbols-outlined text-[20px]">rule</span>
                </div>
                <div className="text-[12px] font-bold">4. 规则评分</div>
                <div className="text-[11px] text-slate-500 font-bold text-red-600">
                  触发P1预警 ({score}分)
                </div>
              </div>
            </div>
          </div>

          {/* Timeline & Details Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            {/* Left: Timeline & Match Reasons (6 cols) */}
            <div className="lg:col-span-6 space-y-6">
              {/* Event Info */}
              <div className="bg-white dark:bg-slate-950 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 space-y-3">
                <div className="flex items-center gap-2 font-bold text-[14px]">
                  <span className="material-symbols-outlined text-[20px] text-[#004782]">info</span>
                  <span>事件信息</span>
                </div>
                <div className="space-y-2 text-[12px]">
                  <div>
                    <span className="text-slate-400">事件分类:</span>
                    <span className="ml-2 bg-blue-50 text-[#004782] px-2 py-0.5 rounded font-bold">
                      {risk.eventCategory || '供应链 - 产能下降'}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-400">算法置信度:</span>
                    <span className="ml-2 font-bold font-mono">{risk.aiConfidence}%</span>
                    <div className="w-full bg-slate-100 h-1.5 rounded-full overflow-hidden mt-1">
                      <div
                        className="bg-[#004782] h-full"
                        style={{ width: `${risk.aiConfidence}%` }}
                      ></div>
                    </div>
                  </div>
                  <div>
                    <span className="text-slate-400">影响范围预估:</span>
                    <div className="mt-1 font-medium text-slate-700 dark:text-slate-300">
                      {risk.impactScope || '影响华东及周边交付延迟 2-4 周'}
                    </div>
                  </div>
                </div>
              </div>

              {/* Match Reasons */}
              <div className="bg-white dark:bg-slate-950 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 space-y-3">
                <div className="flex items-center gap-2 font-bold text-[14px]">
                  <span className="material-symbols-outlined text-[20px] text-[#004782]">
                    fingerprint
                  </span>
                  <span>匹配理由</span>
                </div>
                <div className="space-y-2 text-[12px]">
                  <div className="p-2 bg-blue-50/60 dark:bg-slate-900 border border-blue-100 dark:border-slate-800 rounded-lg">
                    <span className="font-bold text-[#004782]">实体名称匹配: </span>
                    <span>{risk.companyName}</span>
                  </div>
                  <div className="p-2 bg-blue-50/60 dark:bg-slate-900 border border-blue-100 dark:border-slate-800 rounded-lg">
                    <span className="font-bold text-[#004782]">事发地点匹配: </span>
                    <span>{risk.location || '中国/国际地名匹配'}</span>
                  </div>
                  <div className="p-2 bg-blue-50/60 dark:bg-slate-900 border border-blue-100 dark:border-slate-800 rounded-lg">
                    <span className="font-bold text-[#004782]">命中风险关键词: </span>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {risk.tags?.map((t, idx) => (
                        <span key={idx} className="bg-red-100 text-red-700 px-1.5 py-0.5 rounded text-[10px] font-bold">
                          {t}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Right: Score Breakdown & Original Evidence (6 cols) */}
            <div className="lg:col-span-6 space-y-6">
              {/* Detailed Rating Breakdown */}
              <div className="bg-white dark:bg-slate-950 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 space-y-3">
                <div className="flex justify-between items-center font-bold text-[14px]">
                  <div className="flex items-center gap-2">
                    <span className="material-symbols-outlined text-[20px] text-[#004782]">
                      analytics
                    </span>
                    <span>评分明细</span>
                  </div>
                  <span className="text-red-600 font-mono">总分: {score}</span>
                </div>

                <div className="space-y-3 pt-1">
                  {(
                    risk.scoreBreakdown || [
                      { category: '合规与制裁风险', score: 45.0, maxScore: 50, weightPercent: 40, contribution: 36.0 },
                      { category: '财务稳定性风险', score: 28.5, maxScore: 30, weightPercent: 25, contribution: 7.1 },
                      { category: '运营连续性风险', score: 12.0, maxScore: 20, weightPercent: 20, contribution: 2.4 },
                      { category: '网络安全风险', score: 6.5, maxScore: 15, weightPercent: 15, contribution: 1.0 },
                    ]
                  ).map((item, idx) => (
                    <div key={idx} className="text-[12px] space-y-1 border-b border-slate-100 dark:border-slate-800 pb-2">
                      <div className="flex justify-between font-bold">
                        <span>{item.category}</span>
                        <span className="font-mono text-red-600">
                          {item.score} / {item.maxScore}
                        </span>
                      </div>
                      <div className="w-full bg-slate-100 h-1.5 rounded-full overflow-hidden">
                        <div
                          className="bg-[#C92A2A] h-full"
                          style={{ width: `${(item.score / item.maxScore) * 100}%` }}
                        ></div>
                      </div>
                      <div className="flex justify-between text-[10px] text-slate-400">
                        <span>权重: {item.weightPercent}%</span>
                        <span>贡献度: +{item.contribution}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Original Evidence List */}
              <div className="bg-white dark:bg-slate-950 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 space-y-3">
                <div className="flex justify-between items-center font-bold text-[14px]">
                  <div className="flex items-center gap-2">
                    <span className="material-symbols-outlined text-[20px] text-[#004782]">
                      article
                    </span>
                    <span>原始证据</span>
                  </div>
                  <span className="text-xs text-slate-400 font-mono">共 2 条</span>
                </div>

                <div className="space-y-2 text-[12px]">
                  {(
                    risk.originalSignals || [
                      { title: '《半导体设备产业链三季度产能分析报告》', source: '芯智库', time: '2023-10-24 10:15' },
                      { title: '某微电子核心零件供应商因环保问题停产审查', source: '地方环保局公告网', time: '2023-10-23 16:00' },
                    ]
                  ).map((sig, idx) => (
                    <div
                      key={idx}
                      className="p-2.5 bg-[#f7f9ff] dark:bg-slate-900 border border-[#c2c6d2]/60 dark:border-slate-800 rounded-lg space-y-1"
                    >
                      <div className="font-bold text-[#004782] dark:text-blue-300 hover:underline cursor-pointer">
                        {sig.title}
                      </div>
                      <div className="flex justify-between text-[10px] text-slate-400">
                        <span>来源: {sig.source}</span>
                        <span className="font-mono">{sig.time}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </motion.div>
    </motion.div>
  )}
</AnimatePresence>
  );
};

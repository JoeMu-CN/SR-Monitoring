import React, { useState } from 'react';
import { RiskItem } from '../types';

interface ExportReportModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedRisk?: RiskItem | null;
  riskItems: RiskItem[];
}

export const ExportReportModal: React.FC<ExportReportModalProps> = ({
  isOpen,
  onClose,
  selectedRisk,
  riskItems,
}) => {
  const [format, setFormat] = useState<'pdf' | 'csv' | 'json'>('pdf');
  const [includeEvidence, setIncludeEvidence] = useState(true);
  const [includeScore, setIncludeScore] = useState(true);

  if (!isOpen) return null;

  const reportItems = selectedRisk ? [selectedRisk] : riskItems;

  const downloadBlob = (content: string, type: string, extension: string) => {
    const blob = new Blob([content], {type});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${selectedRisk?.companyName ?? 'SR全量风险'}_风险监控报告.${extension}`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const handleDownload = () => {
    if (format === 'pdf') {
      window.print();
      return;
    }
    if (format === 'csv') {
      const header = ['风险ID', '供应商', '等级', '风险类型', '摘要', '风险分', '置信度', '更新时间'];
      const escape = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`;
      const rows = reportItems.map((risk) => [risk.id, risk.companyName, risk.level, risk.riskType, risk.summary,
        includeScore ? risk.overallScore ?? '' : '', risk.aiConfidence, risk.updatedTime].map(escape).join(','));
      downloadBlob(`\uFEFF${[header.map(escape).join(','), ...rows].join('\n')}`, 'text/csv;charset=utf-8', 'csv');
    } else {
      const payload = reportItems.map((risk) => ({
        ...risk,
        evidenceChain: includeEvidence ? risk.evidenceChain : undefined,
        timeline: includeEvidence ? risk.timeline : undefined,
        originalSignals: includeEvidence ? risk.originalSignals : undefined,
        scoreBreakdown: includeScore ? risk.scoreBreakdown : undefined,
      }));
      downloadBlob(JSON.stringify(payload, null, 2), 'application/json;charset=utf-8', 'json');
    }
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in">
      <div data-print-report className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-2xl w-full max-w-lg shadow-2xl overflow-hidden p-6 space-y-5">
        <div className="flex justify-between items-center pb-3 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-2 font-bold text-[18px] text-[#101d28] dark:text-white">
            <span className="material-symbols-outlined text-[#004782] text-[22px]">download</span>
            <span>导出风险监控报告</span>
          </div>
          <button onClick={onClose} className="print-hidden p-1 hover:bg-slate-100 rounded-lg">
            <span className="material-symbols-outlined text-[20px] text-slate-400">close</span>
          </button>
        </div>

        {selectedRisk && (
          <div className="p-3 bg-blue-50/80 border border-blue-200 rounded-xl text-[13px] space-y-1">
            <div className="font-bold text-[#004782]">当前选中对象:</div>
            <div className="font-bold text-slate-800">{selectedRisk.companyName}</div>
            <div className="text-[11px] text-slate-500">ID: {selectedRisk.vendorId} | 风险等级: {selectedRisk.level}</div>
          </div>
        )}

        <div className="print-only hidden space-y-4 text-[12px]">
          <div className="text-slate-500">生成时间：{new Date().toLocaleString('zh-CN')}</div>
          {reportItems.map((risk) => (
            <article key={risk.id} className="border border-slate-300 rounded-xl p-4 space-y-2 break-inside-avoid">
              <div className="flex items-center justify-between gap-3">
                <h2 className="font-bold text-[16px]">{risk.companyName}</h2>
                <span className="font-bold">{risk.level} · {risk.overallScore ?? '未评分'} 分</span>
              </div>
              <p>{risk.riskType}：{risk.summary}</p>
              <div className="text-slate-500">更新时间：{risk.updatedTime}　置信度：{risk.aiConfidence}%</div>
              {includeEvidence && risk.evidenceChain && (
                <div>匹配依据：{risk.evidenceChain.matchStatus}；规则：{risk.evidenceChain.ruleTriggered}</div>
              )}
              {includeScore && risk.scoreBreakdown && (
                <div>评分明细：{risk.scoreBreakdown.map((item) => `${item.category} ${item.score}/${item.maxScore}`).join('；')}</div>
              )}
            </article>
          ))}
        </div>

        {/* Format Selection */}
        <div className="space-y-2">
          <label className="text-[13px] font-bold text-slate-700 dark:text-slate-300">
            导出文件格式
          </label>
          <div className="grid grid-cols-3 gap-3">
            <button
              onClick={() => setFormat('pdf')}
              className={`p-3 border rounded-xl font-bold text-[13px] flex flex-col items-center gap-1 transition-all ${
                format === 'pdf'
                  ? 'border-[#004782] bg-blue-50 text-[#004782] shadow-2xs'
                  : 'border-[#c2c6d2] text-slate-600 hover:bg-slate-50'
              }`}
            >
              <span className="material-symbols-outlined text-[22px]">picture_as_pdf</span>
              <span>PDF 矢量报告</span>
            </button>

            <button
              onClick={() => setFormat('csv')}
              className={`p-3 border rounded-xl font-bold text-[13px] flex flex-col items-center gap-1 transition-all ${
                format === 'csv'
                  ? 'border-[#004782] bg-blue-50 text-[#004782] shadow-2xs'
                  : 'border-[#c2c6d2] text-slate-600 hover:bg-slate-50'
              }`}
            >
              <span className="material-symbols-outlined text-[22px]">table_chart</span>
              <span>CSV 数据表格</span>
            </button>

            <button
              onClick={() => setFormat('json')}
              className={`p-3 border rounded-xl font-bold text-[13px] flex flex-col items-center gap-1 transition-all ${
                format === 'json'
                  ? 'border-[#004782] bg-blue-50 text-[#004782] shadow-2xs'
                  : 'border-[#c2c6d2] text-slate-600 hover:bg-slate-50'
              }`}
            >
              <span className="material-symbols-outlined text-[22px]">code</span>
              <span>JSON 原始信号</span>
            </button>
          </div>
        </div>

        {/* Options */}
        <div className="space-y-2">
          <label className="text-[13px] font-bold text-slate-700 dark:text-slate-300">
            报告包含内容
          </label>
          <div className="space-y-2 text-[13px]">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={includeEvidence}
                onChange={(e) => setIncludeEvidence(e.target.checked)}
                className="w-4 h-4 accent-[#004782]"
              />
              <span>包含完整风险证据链及AI规则打分推导记录</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={includeScore} onChange={(e) => setIncludeScore(e.target.checked)} className="w-4 h-4 accent-[#004782]" />
              <span>包含真实规则评分明细</span>
            </label>
          </div>
        </div>

        {/* Action Button */}
        <div className="print-hidden pt-2 flex justify-end gap-3 border-t border-slate-100 dark:border-slate-800">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-[#c2c6d2] rounded-xl text-[13px] font-medium text-slate-600 hover:bg-slate-50"
          >
            取消
          </button>
          <button
            onClick={handleDownload}
            disabled={reportItems.length === 0}
            className="px-5 py-2 bg-[#004782] hover:bg-[#185fa5] text-white font-bold text-[13px] rounded-xl shadow-sm flex items-center gap-2 disabled:opacity-50"
          >
            <span className="material-symbols-outlined text-[18px]">{format === 'pdf' ? 'print' : 'download'}</span>
            <span>{format === 'pdf' ? '打印 / 存为 PDF' : '生成并下载报告'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};

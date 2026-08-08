import React, { useState } from 'react';
import { RiskItem } from '../types';

interface ExportReportModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedRisk?: RiskItem | null;
}

export const ExportReportModal: React.FC<ExportReportModalProps> = ({
  isOpen,
  onClose,
  selectedRisk,
}) => {
  const [format, setFormat] = useState<'pdf' | 'excel' | 'json'>('pdf');
  const [includeEvidence, setIncludeEvidence] = useState(true);
  const [isExporting, setIsExporting] = useState(false);

  if (!isOpen) return null;

  const handleDownload = () => {
    setIsExporting(true);
    setTimeout(() => {
      setIsExporting(false);
      alert(
        `[导出成功] 已成功生成并下载 ${
          selectedRisk ? selectedRisk.companyName : 'SR全量供应商'
        }_风险分析报告.${format.toUpperCase()}`
      );
      onClose();
    }, 1500);
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in">
      <div className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-2xl w-full max-w-lg shadow-2xl overflow-hidden p-6 space-y-5">
        <div className="flex justify-between items-center pb-3 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-2 font-bold text-[18px] text-[#101d28] dark:text-white">
            <span className="material-symbols-outlined text-[#004782] text-[22px]">download</span>
            <span>导出风险监控报告</span>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-slate-100 rounded-lg">
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
              onClick={() => setFormat('excel')}
              className={`p-3 border rounded-xl font-bold text-[13px] flex flex-col items-center gap-1 transition-all ${
                format === 'excel'
                  ? 'border-[#004782] bg-blue-50 text-[#004782] shadow-2xs'
                  : 'border-[#c2c6d2] text-slate-600 hover:bg-slate-50'
              }`}
            >
              <span className="material-symbols-outlined text-[22px]">table_chart</span>
              <span>Excel 数据表格</span>
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
              <input type="checkbox" defaultChecked className="w-4 h-4 accent-[#004782]" />
              <span>包含替代供应商与缓解预案建议</span>
            </label>
          </div>
        </div>

        {/* Action Button */}
        <div className="pt-2 flex justify-end gap-3 border-t border-slate-100 dark:border-slate-800">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-[#c2c6d2] rounded-xl text-[13px] font-medium text-slate-600 hover:bg-slate-50"
          >
            取消
          </button>
          <button
            onClick={handleDownload}
            disabled={isExporting}
            className="px-5 py-2 bg-[#004782] hover:bg-[#185fa5] text-white font-bold text-[13px] rounded-xl shadow-sm flex items-center gap-2 disabled:opacity-50"
          >
            <span className={`material-symbols-outlined text-[18px] ${isExporting ? 'animate-spin' : ''}`}>
              {isExporting ? 'sync' : 'download'}
            </span>
            <span>{isExporting ? '报告生成中...' : '生成并下载报告'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};

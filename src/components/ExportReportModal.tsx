import React, { useState } from 'react';
import { Download, X, FileText, Table, Code, Loader2 } from 'lucide-react';
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
      <div className="bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 rounded-2xl w-full max-w-lg shadow-2xl overflow-hidden p-6 space-y-5">
        <div className="flex justify-between items-center pb-3 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-2 font-bold text-[18px] text-slate-900 dark:text-white">
            <Download className="w-5 h-5 text-[#007aff]" />
            <span>导出风险监控报告</span>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg cursor-pointer">
            <X className="w-5 h-5 text-slate-400" />
          </button>
        </div>

        {selectedRisk && (
          <div className="p-3 bg-blue-50/80 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-900 rounded-xl text-[13px] space-y-1">
            <div className="font-bold text-[#007aff] dark:text-blue-300">当前选中对象:</div>
            <div className="font-bold text-slate-800 dark:text-slate-100">{selectedRisk.companyName}</div>
            <div className="text-[11px] text-slate-500 dark:text-slate-400">ID: {selectedRisk.vendorId} | 风险等级: {selectedRisk.level}</div>
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
              className={`p-3 border rounded-xl font-bold text-[13px] flex flex-col items-center gap-1 transition-all cursor-pointer ${
                format === 'pdf'
                  ? 'border-[#007aff] bg-blue-50/80 dark:bg-blue-950/60 text-[#007aff] dark:text-blue-300 shadow-2xs'
                  : 'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800'
              }`}
            >
              <FileText className="w-6 h-6" />
              <span>PDF 矢量报告</span>
            </button>

            <button
              onClick={() => setFormat('excel')}
              className={`p-3 border rounded-xl font-bold text-[13px] flex flex-col items-center gap-1 transition-all cursor-pointer ${
                format === 'excel'
                  ? 'border-[#007aff] bg-blue-50/80 dark:bg-blue-950/60 text-[#007aff] dark:text-blue-300 shadow-2xs'
                  : 'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800'
              }`}
            >
              <Table className="w-6 h-6" />
              <span>Excel 数据表格</span>
            </button>

            <button
              onClick={() => setFormat('json')}
              className={`p-3 border rounded-xl font-bold text-[13px] flex flex-col items-center gap-1 transition-all cursor-pointer ${
                format === 'json'
                  ? 'border-[#007aff] bg-blue-50/80 dark:bg-blue-950/60 text-[#007aff] dark:text-blue-300 shadow-2xs'
                  : 'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800'
              }`}
            >
              <Code className="w-6 h-6" />
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
                className="w-4 h-4 accent-[#007aff] rounded cursor-pointer"
              />
              <span className="text-slate-700 dark:text-slate-300">包含完整风险证据链及AI规则打分推导记录</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" defaultChecked className="w-4 h-4 accent-[#007aff] rounded cursor-pointer" />
              <span className="text-slate-700 dark:text-slate-300">包含替代供应商与缓解预案建议</span>
            </label>
          </div>
        </div>

        {/* Action Button */}
        <div className="pt-2 flex justify-end gap-3 border-t border-slate-100 dark:border-slate-800">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-slate-200/80 dark:border-slate-700 rounded-xl text-[13px] font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 cursor-pointer"
          >
            取消
          </button>
          <button
            onClick={handleDownload}
            disabled={isExporting}
            className="px-5 py-2 bg-[#007aff] hover:bg-[#0062cc] text-white font-bold text-[13px] rounded-xl shadow-2xs flex items-center gap-2 disabled:opacity-50 cursor-pointer"
          >
            {isExporting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Download className="w-4 h-4" />
            )}
            <span>{isExporting ? '报告生成中...' : '生成并下载报告'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};


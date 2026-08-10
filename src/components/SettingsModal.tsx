import React, { useState } from 'react';
import { Settings, X } from 'lucide-react';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  isDarkMode?: boolean;
  setIsDarkMode?: (val: boolean) => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  isDarkMode = true,
  setIsDarkMode,
}) => {
  const [email, setEmail] = useState('cro-alerts@company.com');
  const [sensitivity, setSensitivity] = useState('strict');
  const [autoMitigation, setAutoMitigation] = useState(true);

  if (!isOpen) return null;

  const handleSave = () => {
    alert('系统配置更新成功！');
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in">
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl w-full max-w-md shadow-2xl overflow-hidden p-6 space-y-5">
        <div className="flex justify-between items-center pb-3 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-2 font-bold text-[18px] text-slate-900 dark:text-white">
            <Settings className="w-5 h-5 text-[#007aff]" />
            <span>系统设置</span>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg cursor-pointer">
            <X className="w-5 h-5 text-slate-400" />
          </button>
        </div>

        <div className="space-y-4 text-[13px]">
          <div>
            <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
              风险通知接收邮箱 / Webhook
            </label>
            <input
              type="text"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full bg-slate-100/80 dark:bg-slate-800 border border-slate-200/80 dark:border-slate-700 rounded-xl p-2.5 font-mono text-[12px] text-slate-900 dark:text-white"
            />
          </div>

          <div>
            <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
              界面外观与主题
            </label>
            <div className="grid grid-cols-2 gap-2 bg-slate-100/80 dark:bg-slate-800 p-1 rounded-xl border border-slate-200/80 dark:border-slate-700">
              <button
                type="button"
                onClick={() => setIsDarkMode && setIsDarkMode(false)}
                className={`py-2 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                  !isDarkMode
                    ? 'bg-white text-slate-900 shadow-2xs'
                    : 'text-slate-500 hover:text-slate-900 dark:hover:text-white'
                }`}
              >
                浅色模式 (Light)
              </button>
              <button
                type="button"
                onClick={() => setIsDarkMode && setIsDarkMode(true)}
                className={`py-2 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                  isDarkMode
                    ? 'bg-slate-700 text-white shadow-2xs'
                    : 'text-slate-500 hover:text-slate-900 dark:hover:text-white'
                }`}
              >
                深色模式 (Dark)
              </button>
            </div>
          </div>

          <div>
            <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
              预警模型敏感度
            </label>
            <select
              value={sensitivity}
              onChange={(e) => setSensitivity(e.target.value)}
              className="w-full bg-slate-100/80 dark:bg-slate-800 border border-slate-200/80 dark:border-slate-700 rounded-xl p-2.5 font-bold text-[#007aff] dark:text-blue-300"
            >
              <option value="strict">严格模式 (捕捉微弱舆情与轻微异动)</option>
              <option value="standard">标准模式 (平衡误报率与漏报率)</option>
              <option value="relaxed">宽容模式 (仅对重大司法与制裁警报响应)</option>
            </select>
          </div>

          <div className="pt-2 border-t border-slate-100 dark:border-slate-800">
            <label className="flex items-center gap-2 cursor-pointer text-slate-700 dark:text-slate-300">
              <input
                type="checkbox"
                checked={autoMitigation}
                onChange={(e) => setAutoMitigation(e.target.checked)}
                className="w-4 h-4 accent-[#007aff] rounded cursor-pointer"
              />
              <span className="font-bold">触发 P1 极高风险时自动准备备用 RFQ 采购草稿</span>
            </label>
          </div>
        </div>

        <div className="pt-3 flex justify-end gap-3 border-t border-slate-100 dark:border-slate-800">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-slate-200 dark:border-slate-700 rounded-xl font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 cursor-pointer"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            className="px-5 py-2 bg-[#007aff] hover:bg-[#0062cc] text-white font-bold rounded-xl shadow-2xs cursor-pointer"
          >
            保存设置
          </button>
        </div>
      </div>
    </div>
  );
};


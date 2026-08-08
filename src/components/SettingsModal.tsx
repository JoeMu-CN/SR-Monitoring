import React, { useState } from 'react';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({ isOpen, onClose }) => {
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
      <div className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-2xl w-full max-w-md shadow-2xl overflow-hidden p-6 space-y-5">
        <div className="flex justify-between items-center pb-3 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-2 font-bold text-[18px] text-[#101d28] dark:text-white">
            <span className="material-symbols-outlined text-[#004782] text-[22px]">settings</span>
            <span>系统设置</span>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-slate-100 rounded-lg">
            <span className="material-symbols-outlined text-[20px] text-slate-400">close</span>
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
              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-mono text-[12px]"
            />
          </div>

          <div>
            <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
              预警模型敏感度
            </label>
            <select
              value={sensitivity}
              onChange={(e) => setSensitivity(e.target.value)}
              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-bold text-[#004782]"
            >
              <option value="strict">严格模式 (捕捉微弱舆情与轻微异动)</option>
              <option value="standard">标准模式 (平衡误报率与漏报率)</option>
              <option value="relaxed">宽容模式 (仅对重大司法与制裁警报响应)</option>
            </select>
          </div>

          <div className="pt-2 border-t border-slate-100">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={autoMitigation}
                onChange={(e) => setAutoMitigation(e.target.checked)}
                className="w-4 h-4 accent-[#004782]"
              />
              <span className="font-bold">触发 P1 极高风险时自动准备备用 RFQ 采购草稿</span>
            </label>
          </div>
        </div>

        <div className="pt-3 flex justify-end gap-3 border-t border-slate-100 dark:border-slate-800">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-[#c2c6d2] rounded-xl font-medium text-slate-600 hover:bg-slate-50"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            className="px-5 py-2 bg-[#004782] hover:bg-[#185fa5] text-white font-bold rounded-xl shadow-sm"
          >
            保存设置
          </button>
        </div>
      </div>
    </div>
  );
};

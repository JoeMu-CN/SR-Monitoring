import React, { useState } from 'react';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({ isOpen, onClose }) => {
  const [theme, setTheme] = useState(() => localStorage.getItem('sr-theme') ?? 'system');
  const [reduceMotion, setReduceMotion] = useState(() => localStorage.getItem('sr-reduce-motion') === 'true');

  if (!isOpen) return null;

  const handleSave = () => {
    localStorage.setItem('sr-theme', theme);
    localStorage.setItem('sr-reduce-motion', String(reduceMotion));
    const useDark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.classList.toggle('dark', useDark);
    document.documentElement.classList.toggle('light', !useDark);
    document.documentElement.classList.toggle('reduce-motion', reduceMotion);
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
              页面主题
            </label>
            <select
              value={theme}
              onChange={(e) => setTheme(e.target.value)}
              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-bold text-[#004782]"
            >
              <option value="system">跟随系统</option>
              <option value="light">浅色模式</option>
              <option value="dark">深色模式</option>
            </select>
            <p className="mt-1.5 text-[11px] text-slate-500">仅影响当前浏览器的视觉显示，不改变风险规则或业务数据。</p>
          </div>

          <div className="pt-2 border-t border-slate-100">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={reduceMotion}
                onChange={(e) => setReduceMotion(e.target.checked)}
                className="w-4 h-4 accent-[#004782]"
              />
              <span className="font-bold">减少页面过渡与脉冲动效</span>
            </label>
            <p className="mt-1.5 pl-6 text-[11px] text-slate-500">适合对动态效果敏感或希望降低设备资源占用的用户。</p>
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

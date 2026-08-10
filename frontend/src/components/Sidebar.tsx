import React from 'react';
import { motion } from 'motion/react';
import { ActiveTab } from '../types';

interface SidebarProps {
  activeTab: ActiveTab;
  setActiveTab: (tab: ActiveTab) => void;
  onOpenExportModal: () => void;
  onOpenSettingsModal: () => void;
  p1RiskCount: number;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  setActiveTab,
  onOpenExportModal,
  onOpenSettingsModal,
  p1RiskCount,
}) => {
  const navItems: { id: ActiveTab; label: string; icon: string; badge?: number }[] = [
    { id: 'overview', label: '总览', icon: 'dashboard' },
    { id: 'current-risks', label: '当前风险', icon: 'warning', badge: p1RiskCount },
    { id: 'risk-assistant', label: '风险查询助手', icon: 'smart_toy' },
    { id: 'suppliers', label: '供应商', icon: 'factory' },
    { id: 'data-sources', label: '数据源', icon: 'database' },
    { id: 'rules', label: '规则引擎', icon: 'rule' },
  ];

  return (
    <nav className="bg-[#ecf4ff] dark:bg-[#101d28] border-r border-[#c2c6d2] dark:border-slate-800 fixed left-0 top-0 h-screen w-[240px] hidden lg:flex flex-col p-4 gap-4 z-30 transition-colors">
      {/* Brand & Logo */}
      <div className="flex items-center gap-3 mb-2 pt-1">
        <motion.div
          whileHover={{ scale: 1.05, rotate: 3 }}
          whileTap={{ scale: 0.95 }}
          className="w-10 h-10 rounded-xl bg-[#004782] text-white flex items-center justify-center font-black shadow-md flex-shrink-0 cursor-pointer"
        >
          <img src="/logo.svg" alt="SR Monitoring" className="w-10 h-10 rounded-xl" />
        </motion.div>
        <div>
          <div className="font-extrabold text-[18px] text-[#101d28] dark:text-white leading-tight">
            SR Monitoring
          </div>
          <div className="text-[11px] font-bold text-[#424751] dark:text-slate-400 tracking-wider uppercase">
            Enterprise Risk Intelligence
          </div>
        </div>
      </div>

      {/* Primary Navigation Links */}
      <div className="flex flex-col gap-1.5 flex-1 relative">
        {navItems.map((item) => {
          const isActive = activeTab === item.id;
          return (
            <motion.button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              whileHover={{ x: 3 }}
              whileTap={{ scale: 0.98 }}
              className={`relative flex items-center justify-between p-3 rounded-xl font-medium text-[14px] transition-colors text-left overflow-hidden ${
                isActive
                  ? 'text-white font-bold shadow-sm'
                  : 'text-[#424751] dark:text-slate-300 hover:bg-[#d6e4f3]/60 dark:hover:bg-slate-800'
              }`}
            >
              {/* Active Tab Background Pill */}
              {isActive && (
                <motion.div
                  layoutId="sidebarTabHighlight"
                  className="absolute inset-0 bg-[#185fa5] rounded-xl z-0"
                  transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                />
              )}

              <div className="relative z-10 flex items-center gap-3">
                <span className={`material-symbols-outlined text-[20px] ${isActive ? 'filled' : ''}`}>
                  {item.icon}
                </span>
                <span>{item.label}</span>
              </div>

              {item.badge && item.badge > 0 ? (
                <motion.span
                  initial={{ scale: 0.8 }}
                  animate={{ scale: [1, 1.1, 1] }}
                  transition={{ repeat: Infinity, duration: 2, ease: 'easeInOut' }}
                  className={`relative z-10 text-[11px] font-bold px-2 py-0.5 rounded-full ${
                    isActive ? 'bg-white text-[#004782]' : 'bg-[#C92A2A] text-white shadow-xs'
                  }`}
                >
                  {item.badge}
                </motion.span>
              ) : null}
            </motion.button>
          );
        })}
      </div>

      {/* Bottom Actions */}
      <div className="mt-auto flex flex-col gap-1.5 pt-4 border-t border-[#c2c6d2]/50">
        <motion.button
          onClick={onOpenExportModal}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.97 }}
          className="w-full bg-[#004782] hover:bg-[#185fa5] text-white font-medium text-[14px] py-2.5 px-3 rounded-xl shadow-sm transition-all flex items-center justify-center gap-2"
        >
          <span className="material-symbols-outlined text-[18px]">download</span>
          <span>导出风险报告</span>
        </motion.button>

        <motion.button
          onClick={onOpenSettingsModal}
          whileHover={{ x: 2 }}
          whileTap={{ scale: 0.98 }}
          className="flex items-center gap-3 p-2.5 text-[#424751] dark:text-slate-300 hover:bg-[#d6e4f3]/60 dark:hover:bg-slate-800 transition-all rounded-xl text-[14px]"
        >
          <span className="material-symbols-outlined text-[20px]">settings</span>
          <span>系统设置</span>
        </motion.button>

        <motion.button
          onClick={() => alert('如需技术支持，请联系 SR 风险监控团队 support@srmonitoring.com')}
          whileHover={{ x: 2 }}
          whileTap={{ scale: 0.98 }}
          className="flex items-center gap-3 p-2.5 text-[#424751] dark:text-slate-300 hover:bg-[#d6e4f3]/60 dark:hover:bg-slate-800 transition-all rounded-xl text-[14px]"
        >
          <span className="material-symbols-outlined text-[20px]">help</span>
          <span>技术支持</span>
        </motion.button>
      </div>
    </nav>
  );
};

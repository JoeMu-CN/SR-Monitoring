import React from 'react';
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
    { id: 'risk-assistant', label: '风险助手', icon: 'smart_toy' },
    { id: 'suppliers', label: '供应商', icon: 'factory' },
    { id: 'data-sources', label: '数据源', icon: 'database' },
    { id: 'rules', label: '规则引擎', icon: 'rule' },
  ];

  return (
    <nav className="bg-[#ecf4ff] dark:bg-[#101d28] border-r border-[#c2c6d2] dark:border-slate-800 fixed left-0 top-0 h-screen w-[240px] hidden lg:flex flex-col p-4 gap-4 z-30 transition-all">
      {/* Brand & Logo */}
      <div className="flex items-center gap-3 mb-4 pt-1">
        <div className="w-10 h-10 rounded-xl bg-[#004782] text-white flex items-center justify-center font-black shadow-md flex-shrink-0">
          <span className="material-symbols-outlined text-[24px]">shield_with_house</span>
        </div>
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
      <div className="flex flex-col gap-1.5 flex-1">
        {navItems.map((item) => {
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`flex items-center justify-between p-3 rounded-xl font-medium text-[14px] transition-all duration-200 text-left ${
                isActive
                  ? 'bg-[#185fa5] text-white shadow-sm font-bold'
                  : 'text-[#424751] dark:text-slate-300 hover:bg-[#d6e4f3]/60 dark:hover:bg-slate-800'
              }`}
            >
              <div className="flex items-center gap-3">
                <span className={`material-symbols-outlined text-[20px] ${isActive ? 'filled' : ''}`}>
                  {item.icon}
                </span>
                <span>{item.label}</span>
              </div>
              {item.badge && item.badge > 0 ? (
                <span
                  className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${
                    isActive ? 'bg-white text-[#004782]' : 'bg-[#C92A2A] text-white'
                  }`}
                >
                  {item.badge}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      {/* Bottom Actions */}
      <div className="mt-auto flex flex-col gap-1.5 pt-4 border-t border-[#c2c6d2]/50">
        <button
          onClick={onOpenExportModal}
          className="w-full bg-[#004782] hover:bg-[#185fa5] text-white font-medium text-[14px] py-2.5 px-3 rounded-xl shadow-sm transition-all flex items-center justify-center gap-2"
        >
          <span className="material-symbols-outlined text-[18px]">download</span>
          <span>导出风险报告</span>
        </button>

        <button
          onClick={onOpenSettingsModal}
          className="flex items-center gap-3 p-2.5 text-[#424751] dark:text-slate-300 hover:bg-[#d6e4f3]/60 dark:hover:bg-slate-800 transition-all rounded-xl text-[14px]"
        >
          <span className="material-symbols-outlined text-[20px]">settings</span>
          <span>系统设置</span>
        </button>

        <button
          onClick={() => alert('如需技术支持，请联系 SR 风险监控团队 support@srmonitoring.com')}
          className="flex items-center gap-3 p-2.5 text-[#424751] dark:text-slate-300 hover:bg-[#d6e4f3]/60 dark:hover:bg-slate-800 transition-all rounded-xl text-[14px]"
        >
          <span className="material-symbols-outlined text-[20px]">help</span>
          <span>技术支持</span>
        </button>
      </div>
    </nav>
  );
};

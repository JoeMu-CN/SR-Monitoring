import React from 'react';
import { ActiveTab } from '../types';

interface MobileNavProps {
  activeTab: ActiveTab;
  setActiveTab: (tab: ActiveTab) => void;
  p1RiskCount: number;
}

export const MobileNav: React.FC<MobileNavProps> = ({
  activeTab,
  setActiveTab,
  p1RiskCount,
}) => {
  const tabs: { id: ActiveTab; label: string; icon: string; badge?: number }[] = [
    { id: 'overview', label: '总览', icon: 'dashboard' },
    { id: 'current-risks', label: '风险', icon: 'warning', badge: p1RiskCount },
    { id: 'risk-assistant', label: '查询AI', icon: 'smart_toy' },
    { id: 'suppliers', label: '供应商', icon: 'factory' },
    { id: 'data-sources', label: '数据', icon: 'database' },
    { id: 'rules', label: '规则', icon: 'rule' },
  ];

  return (
    <nav className="lg:hidden fixed bottom-0 left-0 w-full bg-white dark:bg-[#101d28] border-t border-[#c2c6d2] dark:border-slate-800 z-50 flex justify-around items-center px-2 py-2 shadow-lg">
      {tabs.map((tab) => {
        const isActive = activeTab === tab.id;
        return (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`relative flex flex-1 min-w-0 flex-col items-center justify-center px-1 py-1 rounded-full transition-all duration-150 active:scale-95 ${
              isActive
                ? 'bg-[#185fa5] text-white font-bold'
                : 'text-[#424751] dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800'
            }`}
          >
            <span className={`material-symbols-outlined text-[20px] ${isActive ? 'filled' : ''}`}>
              {tab.icon}
            </span>
            <span className="text-[11px] font-medium leading-none mt-1">{tab.label}</span>
            {tab.badge && tab.badge > 0 ? (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-[#C92A2A] text-white text-[9px] font-bold rounded-full flex items-center justify-center">
                {tab.badge}
              </span>
            ) : null}
          </button>
        );
      })}
    </nav>
  );
};

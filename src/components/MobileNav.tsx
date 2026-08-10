import React from 'react';
import { motion } from 'motion/react';
import { LayoutDashboard, AlertTriangle, Bot, Building2, Database, Sliders } from 'lucide-react';
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
  const tabs: { id: ActiveTab; label: string; icon: React.ReactNode; badge?: number }[] = [
    { id: 'overview', label: '总览', icon: <LayoutDashboard className="w-5 h-5" /> },
    { id: 'current-risks', label: '风险', icon: <AlertTriangle className="w-5 h-5" />, badge: p1RiskCount },
    { id: 'risk-assistant', label: '助手', icon: <Bot className="w-5 h-5" /> },
    { id: 'suppliers', label: '供应商', icon: <Building2 className="w-5 h-5" /> },
    { id: 'data-sources', label: '数据', icon: <Database className="w-5 h-5" /> },
    { id: 'rules', label: '规则', icon: <Sliders className="w-5 h-5" /> },
  ];

  return (
    <div className="lg:hidden fixed bottom-3 left-1/2 -translate-x-1/2 z-50 w-[92%] max-w-md">
      <nav className="bg-white/80 dark:bg-slate-900/80 backdrop-blur-2xl border border-white/50 dark:border-slate-800 rounded-2xl p-1.5 shadow-2xl flex justify-around items-center gap-1 select-none">
        {tabs.map((tab) => {
          const isActive = activeTab === tab.id;
          return (
            <motion.button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              whileTap={{ scale: 0.85 }}
              className={`relative flex flex-col items-center justify-center p-2 rounded-xl transition-all cursor-pointer ${
                isActive
                  ? 'bg-[#007aff] text-white shadow-xs font-bold'
                  : 'text-slate-600 dark:text-slate-400 hover:bg-slate-200/50 dark:hover:bg-slate-800'
              }`}
            >
              {tab.icon}
              <span className="text-[10px] font-medium leading-none mt-1">{tab.label}</span>

              {/* Active macOS Indicator Dot underneath icon */}
              {isActive && (
                <span className="w-1 h-1 bg-white rounded-full mt-0.5 shadow-xs" />
              )}

              {tab.badge && tab.badge > 0 ? (
                <span className="absolute -top-1 -right-1 w-4 h-4 bg-[#ff3b30] text-white text-[9px] font-bold rounded-full flex items-center justify-center shadow-xs">
                  {tab.badge}
                </span>
              ) : null}
            </motion.button>
          );
        })}
      </nav>
    </div>
  );
};



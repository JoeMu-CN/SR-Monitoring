import React from 'react';
import {motion} from 'motion/react';
import {LayoutDashboard, AlertTriangle, Bot, Building2, Compass, Database, SlidersHorizontal} from 'lucide-react';
import type {ActiveTab} from '../types';

interface MobileNavProps { activeTab: ActiveTab; setActiveTab: (tab: ActiveTab) => void; p1RiskCount: number; canUseRiskAssistant: boolean; canUseResearch: boolean; }

export const MobileNav: React.FC<MobileNavProps> = ({activeTab, setActiveTab, p1RiskCount, canUseRiskAssistant, canUseResearch}) => {
  const tabs: {id: ActiveTab; label: string; icon: React.ReactNode; badge?: number}[] = [
    {id: 'overview', label: '总览', icon: <LayoutDashboard className="h-5 w-5"/>},
    {id: 'current-risks', label: '风险', icon: <AlertTriangle className="h-5 w-5"/>, badge: p1RiskCount},
    ...(canUseRiskAssistant ? [{id: 'risk-assistant' as ActiveTab, label: '助手', icon: <Bot className="h-5 w-5"/>}] : []),
    ...(canUseResearch ? [{id: 'research' as ActiveTab, label: '研究', icon: <Compass className="h-5 w-5"/>}] : []),
    {id: 'suppliers', label: '供应商', icon: <Building2 className="h-5 w-5"/>},
    {id: 'data-sources', label: '数据', icon: <Database className="h-5 w-5"/>},
    {id: 'rules', label: '规则', icon: <SlidersHorizontal className="h-5 w-5"/>},
  ];
  return <div className="fixed bottom-3 left-1/2 z-50 w-[92%] max-w-md -translate-x-1/2 lg:hidden"><nav className="flex items-center justify-around gap-1 rounded-2xl border border-white/60 bg-white/80 p-1.5 shadow-2xl backdrop-blur-2xl dark:border-slate-800 dark:bg-slate-900/80">{tabs.map((tab) => { const active = activeTab === tab.id; return <motion.button key={tab.id} type="button" onClick={() => setActiveTab(tab.id)} whileTap={{scale: 0.85}} className={`relative flex min-w-0 flex-1 flex-col items-center justify-center rounded-xl p-2 transition-all ${active ? 'bg-[#185fa5] font-bold text-white shadow-sm' : 'text-slate-600 hover:bg-slate-200/60 dark:text-slate-400 dark:hover:bg-slate-800'}`}>{tab.icon}<span className="mt-1 text-[10px] font-medium leading-none">{tab.label}</span>{active && <span className="mt-0.5 h-1 w-1 rounded-full bg-white"/>}{tab.badge && tab.badge > 0 ? <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-[#C92A2A] text-[9px] font-bold text-white">{tab.badge}</span> : null}</motion.button>; })}</nav></div>;
};

import React from 'react';
import {motion} from 'motion/react';
import {
  LayoutDashboard,
  ShieldAlert,
  Bot,
  Building2,
  Database,
  SlidersHorizontal,
  Settings,
  HelpCircle,
} from 'lucide-react';
import type {ActiveTab} from '../types';

interface SidebarProps {
  activeTab: ActiveTab;
  setActiveTab: (tab: ActiveTab) => void;
  onOpenSettingsModal: () => void;
  p1RiskCount: number;
  canUseRiskAssistant: boolean;
}

// "智能研究" 与 "数据源接入助手" 功能已暂停，入口暂时隐藏；导出风险报告按钮也同步隐藏。
export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  setActiveTab,
  onOpenSettingsModal,
  p1RiskCount,
  canUseRiskAssistant,
}) => {
  const mainItems: {id: ActiveTab; label: string; icon: React.ReactNode; badge?: number}[] = [
    {id: 'overview', label: '风险总览', icon: <LayoutDashboard className="h-[18px] w-[18px]"/>},
    {id: 'current-risks', label: '当前风险监控', icon: <ShieldAlert className="h-[18px] w-[18px]"/>, badge: p1RiskCount},
    ...(canUseRiskAssistant ? [{id: 'risk-assistant' as ActiveTab, label: '风险查询助手', icon: <Bot className="h-[18px] w-[18px]"/>}] : []),
    {id: 'suppliers', label: '供应商名录', icon: <Building2 className="h-[18px] w-[18px]"/>},
  ];
  const systemItems: {id: ActiveTab; label: string; icon: React.ReactNode}[] = [
    {id: 'data-sources', label: '数据源列表', icon: <Database className="h-[18px] w-[18px]"/>},
    {id: 'rules', label: '规则引擎', icon: <SlidersHorizontal className="h-[18px] w-[18px]"/>},
  ];

  const renderItem = (item: {id: ActiveTab; label: string; icon: React.ReactNode; badge?: number}) => {
    const active = activeTab === item.id;
    return (
      <motion.button
        key={item.id}
        type="button"
        onClick={() => setActiveTab(item.id)}
        whileTap={{scale: 0.98}}
        className={`relative flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-[13px] font-semibold transition-all ${
          active ? 'text-white' : 'text-slate-700 hover:bg-slate-200/70 dark:text-slate-300 dark:hover:bg-slate-800/70'
        }`}
      >
        {active && <motion.span layoutId="sidebarTabHighlight" className="absolute inset-0 rounded-lg bg-[#185fa5] shadow-sm" transition={{type: 'spring', stiffness: 500, damping: 35}}/>}
        <span className="relative z-10 flex items-center gap-2.5">{item.icon}<span>{item.label}</span></span>
        {item.badge && item.badge > 0 ? <span className={`relative z-10 rounded-full px-1.5 py-0.5 text-[10px] font-bold ${active ? 'bg-white text-[#C92A2A]' : 'bg-[#C92A2A] text-white'}`}>{item.badge}</span> : null}
      </motion.button>
    );
  };

  return (
    <nav className="fixed left-0 top-0 z-30 hidden h-screen w-[240px] select-none flex-col gap-4 border-r border-slate-200/80 bg-slate-100/90 p-3.5 backdrop-blur-2xl dark:border-slate-800/80 dark:bg-[#0c1420]/90 lg:flex">
      <div className="flex items-center gap-2.5 rounded-xl border border-black/5 bg-white/70 px-2.5 py-2 shadow-sm dark:border-white/5 dark:bg-slate-800/60">
        <img src="/logo.svg" alt="供应商风险监控平台" className="h-8 w-8 rounded-lg object-cover"/>
        <div className="min-w-0">
          <div className="truncate text-[13px] font-extrabold leading-tight text-slate-900 dark:text-white">SR Risk Studio</div>
          <div className="truncate text-[10px] font-medium text-slate-500 dark:text-slate-400">企业级供应链风险系统</div>
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-4 overflow-y-auto pr-0.5">
        <div className="space-y-1">
          {mainItems.map(renderItem)}
        </div>
        <div className="space-y-1">
          {systemItems.map(renderItem)}
        </div>
      </div>

      <div className="mt-auto flex flex-col gap-1 border-t border-slate-200/80 pt-3 dark:border-slate-800">
        <div className="grid grid-cols-2 gap-1">
          <button type="button" onClick={onOpenSettingsModal} className="flex items-center justify-center gap-1.5 rounded-lg p-1.5 text-[12px] font-medium text-slate-600 transition-colors hover:bg-slate-200/70 dark:text-slate-300 dark:hover:bg-slate-800"><Settings className="h-[15px] w-[15px]"/><span>设置</span></button>
          <button type="button" onClick={() => alert('如需技术支持，请联系 SR 风险监控团队 support@srmonitoring.com')} className="flex items-center justify-center gap-1.5 rounded-lg p-1.5 text-[12px] font-medium text-slate-600 transition-colors hover:bg-slate-200/70 dark:text-slate-300 dark:hover:bg-slate-800"><HelpCircle className="h-[15px] w-[15px]"/><span>帮助</span></button>
        </div>
      </div>
    </nav>
  );
};

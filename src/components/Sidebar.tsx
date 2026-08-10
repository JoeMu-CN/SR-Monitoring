import React from 'react';
import { motion } from 'motion/react';
import {
  LayoutDashboard,
  ShieldAlert,
  Bot,
  Building2,
  Database,
  SlidersHorizontal,
  Download,
  Settings,
  HelpCircle,
  Shield
} from 'lucide-react';
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
  const mainNavItems: { id: ActiveTab; label: string; icon: React.ReactNode; badge?: number }[] = [
    { id: 'overview', label: '风险总览', icon: <LayoutDashboard className="w-[18px] h-[18px]" /> },
    { id: 'current-risks', label: '当前风险监控', icon: <ShieldAlert className="w-[18px] h-[18px]" />, badge: p1RiskCount },
    { id: 'risk-assistant', label: 'AI 风险助手', icon: <Bot className="w-[18px] h-[18px]" /> },
    { id: 'suppliers', label: '供应商名录', icon: <Building2 className="w-[18px] h-[18px]" /> },
  ];

  const systemNavItems: { id: ActiveTab; label: string; icon: React.ReactNode }[] = [
    { id: 'data-sources', label: '数据源同步', icon: <Database className="w-[18px] h-[18px]" /> },
    { id: 'rules', label: '规则引擎', icon: <SlidersHorizontal className="w-[18px] h-[18px]" /> },
  ];

  return (
    <nav className="bg-slate-100/90 dark:bg-[#0c1420]/90 backdrop-blur-2xl border-r border-slate-200/80 dark:border-slate-800/80 fixed left-0 top-0 h-screen w-[240px] hidden lg:flex flex-col p-3.5 gap-4 z-30 transition-colors select-none">
      {/* macOS Traffic Lights Header */}
      <div className="flex items-center justify-between pt-1 px-1.5 pb-2">
        <div className="flex items-center gap-2 group">
          <button
            onClick={() => alert('macOS 窗口：SR 风险监控组件处于全屏运行状态')}
            className="w-3 h-3 rounded-full bg-[#ff5f56] border border-[#e0443e] flex items-center justify-center text-[8px] font-bold text-[#800000] opacity-90 hover:opacity-100 transition-opacity cursor-pointer"
            title="关闭窗口"
          >
            <span className="hidden group-hover:inline">✕</span>
          </button>
          <button
            onClick={() => alert('macOS 窗口：最小化')}
            className="w-3 h-3 rounded-full bg-[#ffbd2e] border border-[#dea123] flex items-center justify-center text-[8px] font-bold text-[#996000] opacity-90 hover:opacity-100 transition-opacity cursor-pointer"
            title="最小化"
          >
            <span className="hidden group-hover:inline">−</span>
          </button>
          <button
            onClick={() => alert('macOS 窗口：进入全屏模式')}
            className="w-3 h-3 rounded-full bg-[#27c93f] border border-[#1aab29] flex items-center justify-center text-[8px] font-bold text-[#006600] opacity-90 hover:opacity-100 transition-opacity cursor-pointer"
            title="全屏"
          >
            <span className="hidden group-hover:inline">+</span>
          </button>
        </div>

        <span className="text-[10px] font-mono font-bold text-slate-400 dark:text-slate-500 tracking-tight">
          macOS Sonoma
        </span>
      </div>

      {/* Brand & Logo */}
      <div className="flex items-center gap-2.5 px-2.5 py-2 bg-white/70 dark:bg-slate-800/60 backdrop-blur-md rounded-xl border border-black/5 dark:border-white/5 shadow-2xs">
        <div className="w-8 h-8 rounded-lg bg-[#007aff] text-white flex items-center justify-center font-black shadow-xs flex-shrink-0">
          <Shield className="w-5 h-5 fill-white text-[#007aff]" />
        </div>
        <div>
          <div className="font-extrabold text-[13px] text-slate-900 dark:text-white leading-tight">
            SR Risk Studio
          </div>
          <div className="text-[10px] font-medium text-slate-500 dark:text-slate-400">
            企业级供应链风险系统
          </div>
        </div>
      </div>

      {/* Navigation Sections */}
      <div className="flex flex-col gap-4 flex-1 overflow-y-auto pr-0.5">
        {/* Section 1: Main */}
        <div className="space-y-1">
          <div className="px-2 text-[10px] font-extrabold uppercase tracking-wider text-slate-400 dark:text-slate-500 mb-1">
            核心监控 MONITORS
          </div>

          {mainNavItems.map((item) => {
            const isActive = activeTab === item.id;
            return (
              <motion.button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                whileTap={{ scale: 0.98 }}
                className={`relative w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-[13px] font-semibold transition-all text-left cursor-pointer ${
                  isActive
                    ? 'text-white'
                    : 'text-slate-700 dark:text-slate-300 hover:bg-slate-200/60 dark:hover:bg-slate-800/60'
                }`}
              >
                {/* macOS Active Blue Pill Background */}
                {isActive && (
                  <motion.div
                    layoutId="macOSSidebarHighlight"
                    className="absolute inset-0 bg-[#007aff] rounded-lg shadow-xs z-0"
                    transition={{ type: 'spring', stiffness: 500, damping: 35 }}
                  />
                )}

                <div className="relative z-10 flex items-center gap-2.5">
                  {item.icon}
                  <span>{item.label}</span>
                </div>

                {item.badge && item.badge > 0 ? (
                  <motion.span
                    initial={{ scale: 0.9 }}
                    animate={{ scale: [1, 1.1, 1] }}
                    transition={{ repeat: Infinity, duration: 2, ease: 'easeInOut' }}
                    className={`relative z-10 text-[10px] font-bold px-1.5 py-0.2 rounded-full ${
                      isActive ? 'bg-white text-[#ff3b30]' : 'bg-[#ff3b30] text-white shadow-2xs'
                    }`}
                  >
                    {item.badge}
                  </motion.span>
                ) : null}
              </motion.button>
            );
          })}
        </div>

        {/* Section 2: System */}
        <div className="space-y-1">
          <div className="px-2 text-[10px] font-extrabold uppercase tracking-wider text-slate-400 dark:text-slate-500 mb-1">
            系统与服务 SYSTEM
          </div>

          {systemNavItems.map((item) => {
            const isActive = activeTab === item.id;
            return (
              <motion.button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                whileTap={{ scale: 0.98 }}
                className={`relative w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-[13px] font-semibold transition-all text-left cursor-pointer ${
                  isActive
                    ? 'text-white'
                    : 'text-slate-700 dark:text-slate-300 hover:bg-slate-200/60 dark:hover:bg-slate-800/60'
                }`}
              >
                {isActive && (
                  <motion.div
                    layoutId="macOSSidebarHighlight"
                    className="absolute inset-0 bg-[#007aff] rounded-lg shadow-xs z-0"
                    transition={{ type: 'spring', stiffness: 500, damping: 35 }}
                  />
                )}

                <div className="relative z-10 flex items-center gap-2.5">
                  {item.icon}
                  <span>{item.label}</span>
                </div>
              </motion.button>
            );
          })}
        </div>
      </div>

      {/* Bottom macOS Actions */}
      <div className="mt-auto flex flex-col gap-1 pt-3 border-t border-slate-200/80 dark:border-slate-800">
        <motion.button
          onClick={onOpenExportModal}
          whileTap={{ scale: 0.97 }}
          className="w-full bg-[#007aff] hover:bg-[#0062cc] text-white font-medium text-[13px] py-2 px-3 rounded-lg shadow-xs transition-all flex items-center justify-center gap-2 cursor-pointer"
        >
          <Download className="w-[16px] h-[16px]" />
          <span>导出风险报告</span>
        </motion.button>

        <div className="grid grid-cols-2 gap-1 mt-1">
          <button
            onClick={onOpenSettingsModal}
            className="flex items-center justify-center gap-1.5 p-1.5 text-slate-600 dark:text-slate-300 hover:bg-slate-200/60 dark:hover:bg-slate-800 transition-all rounded-lg text-[12px] font-medium cursor-pointer"
          >
            <Settings className="w-[15px] h-[15px]" />
            <span>设置</span>
          </button>

          <button
            onClick={() => alert('如需技术支持，请联系 SR 风险监控团队 support@srmonitoring.com')}
            className="flex items-center justify-center gap-1.5 p-1.5 text-slate-600 dark:text-slate-300 hover:bg-slate-200/60 dark:hover:bg-slate-800 transition-all rounded-lg text-[12px] font-medium cursor-pointer"
          >
            <HelpCircle className="w-[15px] h-[15px]" />
            <span>帮助</span>
          </button>
        </div>
      </div>
    </nav>
  );
};



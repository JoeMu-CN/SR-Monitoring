import React, { useState } from 'react';
import { Search, Bell, Zap, Bot, Globe, Shield, Sun, Moon } from 'lucide-react';
import { ActiveTab } from '../types';

interface HeaderProps {
  activeTab: ActiveTab;
  unreadCount: number;
  onSearch?: (term: string) => void;
  isSimulatedEmpty: boolean;
  setIsSimulatedEmpty: (val: boolean) => void;
  isDarkMode?: boolean;
  onToggleDarkMode?: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  unreadCount,
  onSearch,
  isSimulatedEmpty,
  setIsSimulatedEmpty,
  isDarkMode = true,
  onToggleDarkMode,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [showNotifications, setShowNotifications] = useState(false);
  const [showProfile, setShowProfile] = useState(false);

  const getTabTitle = (tab: ActiveTab) => {
    switch (tab) {
      case 'overview':
        return '风险总览';
      case 'current-risks':
        return '当前风险监控';
      case 'risk-assistant':
        return 'AI 风险助手';
      case 'suppliers':
        return '供应商名录';
      case 'data-sources':
        return '数据源同步状态';
      case 'rules':
        return '规则引擎与沙箱';
      default:
        return 'SR 供应商风险监控';
    }
  };

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchTerm(e.target.value);
    if (onSearch) {
      onSearch(e.target.value);
    }
  };

  return (
    <header className="bg-white/70 dark:bg-[#0e1726]/70 backdrop-blur-xl border-b border-slate-200/80 dark:border-slate-800 sticky top-0 z-20 px-4 lg:px-6 py-2.5 transition-all select-none">
      <div className="max-w-[1440px] mx-auto flex items-center justify-between gap-4">
        {/* Left macOS Window Title & Breadcrumb */}
        <div className="flex items-center gap-3">
          {/* Mobile Traffic Lights */}
          <div className="lg:hidden flex items-center gap-1.5 mr-1">
            <span className="w-2.5 h-2.5 rounded-full bg-[#ff5f56]" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#ffbd2e]" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#27c93f]" />
          </div>

          <div>
            <div className="text-[10px] font-bold tracking-wider text-[#007aff] dark:text-blue-400 uppercase flex items-center gap-1">
              <Shield className="w-3 h-3 text-[#007aff]" />
              <span>SR Risk Studio / {getTabTitle(activeTab)}</span>
            </div>
            <h1 className="font-black text-[16px] lg:text-[18px] text-slate-900 dark:text-white leading-tight">
              {getTabTitle(activeTab)}
            </h1>
          </div>
        </div>

        {/* Middle macOS Status Segment Pills */}
        <div className="hidden xl:flex items-center gap-2 bg-slate-100/80 dark:bg-slate-800/80 border border-black/5 dark:border-white/5 rounded-xl px-3 py-1 text-[11px]">
          {/* Model Status Indicator */}
          <div className="flex items-center gap-2 pr-2.5 border-r border-slate-200 dark:border-slate-700">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
            </span>
            <Bot className="w-3.5 h-3.5 text-slate-600 dark:text-slate-300" />
            <span className="font-semibold text-slate-700 dark:text-slate-200">Gemini 3.6 AI 直连</span>
          </div>

          {/* External Check Status Indicator (TianYanCha) */}
          <div className="flex items-center gap-2 pr-2.5 border-r border-slate-200 dark:border-slate-700">
            <Globe className="w-3.5 h-3.5 text-[#007aff]" />
            <span className="font-semibold text-slate-700 dark:text-slate-200">天眼查 API 实时节点</span>
          </div>

          {/* Latency */}
          <div className="flex items-center gap-1 font-mono text-[10px] text-emerald-600 dark:text-emerald-400 font-bold">
            <Zap className="w-3 h-3 text-emerald-500 fill-emerald-500" />
            <span>&lt; 18ms</span>
          </div>
        </div>

        {/* Right Search & Controls */}
        <div className="flex items-center gap-2.5">
          {/* Quick Search Bar (macOS Style with ⌘K badge) */}
          <div className="relative hidden md:block w-56 lg:w-72">
            <Search className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={searchTerm}
              onChange={handleSearchChange}
              placeholder="搜索供应商、风险、分类..."
              className="w-full bg-slate-100/90 dark:bg-slate-800/90 border border-slate-200/80 dark:border-slate-700 rounded-lg pl-8 pr-12 py-1.5 text-[12px] text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-[#007aff] transition-all"
            />
            <kbd className="absolute right-2 top-1/2 -translate-y-1/2 bg-white dark:bg-slate-700 text-slate-500 dark:text-slate-300 text-[10px] font-mono font-bold px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-600 shadow-2xs">
              ⌘K
            </kbd>
          </div>

          {/* Segmented Mode Switcher (Live vs Empty State) */}
          <button
            onClick={() => setIsSimulatedEmpty(!isSimulatedEmpty)}
            title="切换空状态与真实数据预览"
            className={`hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-bold border transition-all cursor-pointer ${
              isSimulatedEmpty
                ? 'bg-amber-500/10 text-amber-600 border-amber-300/60 dark:text-amber-300'
                : 'bg-emerald-500/10 text-emerald-700 border-emerald-300/60 dark:text-emerald-300'
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                isSimulatedEmpty ? 'bg-amber-500 animate-ping' : 'bg-emerald-500'
              }`}
            />
            <span>{isSimulatedEmpty ? '演示空状态' : '实时数据'}</span>
          </button>

          {/* Theme Switcher Button (Light / Dark Mode) */}
          {onToggleDarkMode && (
            <button
              onClick={onToggleDarkMode}
              className="p-1.5 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-colors cursor-pointer"
              title={isDarkMode ? '切换至浅色模式 (Light Mode)' : '切换至深色模式 (Dark Mode)'}
            >
              {isDarkMode ? (
                <Sun className="w-5 h-5 text-amber-400" />
              ) : (
                <Moon className="w-5 h-5 text-indigo-600" />
              )}
            </button>
          )}

          {/* Notifications Dropdown */}
          <div className="relative">
            <button
              onClick={() => {
                setShowNotifications(!showNotifications);
                setShowProfile(false);
              }}
              className="relative p-1.5 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-colors cursor-pointer"
            >
              <Bell className="w-5 h-5 text-slate-700 dark:text-slate-200" />
              {unreadCount > 0 && (
                <span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-[#ff3b30] ring-2 ring-white dark:ring-slate-900" />
              )}
            </button>

            {showNotifications && (
              <div className="absolute right-0 mt-2 w-80 bg-white/95 dark:bg-slate-900/95 backdrop-blur-xl border border-slate-200 dark:border-slate-800 rounded-xl shadow-2xl p-3 z-50 animate-in fade-in zoom-in-95 duration-150">
                <div className="flex justify-between items-center pb-2 border-b border-slate-100 dark:border-slate-800 mb-2">
                  <span className="font-bold text-[13px] text-slate-900 dark:text-white">风险通知中心</span>
                  <span className="text-[10px] bg-[#ff3b30] text-white px-2 py-0.2 rounded-full font-bold">
                    {unreadCount} 条紧急预警
                  </span>
                </div>
                <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
                  <div className="p-2.5 rounded-lg bg-red-50/80 dark:bg-red-950/40 text-[12px] border border-red-200/60 dark:border-red-900/40">
                    <div className="font-bold text-[#ff3b30] flex justify-between">
                      <span>P1 极高风险: 深圳市鼎盛科技</span>
                      <span className="text-[10px] text-slate-500">10分钟前</span>
                    </div>
                    <p className="text-slate-600 dark:text-slate-300 mt-1">
                      命中 OFAC/BIS 全球制裁名单，建议立即启动供应替代预案。
                    </p>
                  </div>
                  <div className="p-2.5 rounded-lg bg-amber-50/80 dark:bg-amber-950/40 text-[12px] border border-amber-200/60 dark:border-amber-900/40">
                    <div className="font-bold text-[#ff9500] flex justify-between">
                      <span>P2 高风险: Global Logistics Corp</span>
                      <span className="text-[10px] text-slate-500">2小时前</span>
                    </div>
                    <p className="text-slate-600 dark:text-slate-300 mt-1">
                      苏伊士运河严重拥堵，预计运力延迟 7-10 天。
                    </p>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* User Profile macOS Avatar Button */}
          <div className="relative">
            <button
              onClick={() => {
                setShowProfile(!showProfile);
                setShowNotifications(false);
              }}
              className="flex items-center gap-1.5 p-1 rounded-full hover:ring-2 hover:ring-[#007aff]/50 transition-all cursor-pointer"
            >
              <img
                src="https://lh3.googleusercontent.com/aida-public/AB6AXuAmzD0QNYwrpruEDeD7iTgFZByV4wcodVpBQWTlWW9y1b_AsztsS7QPp6WdjG-5ePvQDmjtkBKIrSZxQ8Cb19aptTZJBYM3ouD8fnQOrWOPxbWBFtsoFNrZODRfnBbLqGiMiix-05IpWdBQWzbdVIeFqxi0AOHIGssuUpbDzwNH-l1uGZoOylr-WoRaImkzXcAsXnlGk0lFwBI-y0MCtLy0T9qyCMaH6t3kjfI_KYX7lCoei6NZ1jA6"
                alt="User Avatar"
                className="w-7 h-7 rounded-full object-cover border border-slate-200 dark:border-slate-700"
              />
            </button>

            {showProfile && (
              <div className="absolute right-0 mt-2 w-56 bg-white/95 dark:bg-slate-900/95 backdrop-blur-xl border border-slate-200 dark:border-slate-800 rounded-xl shadow-2xl p-3 z-50">
                <div className="pb-2 border-b border-slate-100 dark:border-slate-800 mb-2">
                  <div className="font-bold text-[13px] text-slate-900 dark:text-white">首席风险官 (CRO)</div>
                  <div className="text-[11px] text-slate-500 truncate">jojo19920612@gmail.com</div>
                </div>
                <div className="space-y-1 text-[12px]">
                  <button className="w-full text-left px-2 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg text-slate-700 dark:text-slate-300 cursor-pointer">
                    账号与角色配置
                  </button>
                  <button className="w-full text-left px-2 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg text-slate-700 dark:text-slate-300 cursor-pointer">
                    API 访问密钥 (Key)
                  </button>
                  <button className="w-full text-left px-2 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg text-[#ff3b30] font-bold cursor-pointer">
                    退出登录
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
};



import React, { useState } from 'react';
import { ActiveTab } from '../types';

interface HeaderProps {
  activeTab: ActiveTab;
  unreadCount: number;
  onSearch?: (term: string) => void;
  isSimulatedEmpty: boolean;
  setIsSimulatedEmpty: (val: boolean) => void;
}

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  unreadCount,
  onSearch,
  isSimulatedEmpty,
  setIsSimulatedEmpty,
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
        return '风险查询助手';
      case 'suppliers':
        return '供应商列表';
      case 'data-sources':
        return '数据源与同步状态';
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
    <header className="bg-white dark:bg-[#101d28] border-b border-[#c2c6d2] dark:border-slate-800 sticky top-0 z-20 px-4 lg:px-8 py-3 transition-all shadow-xs">
      <div className="max-w-[1440px] mx-auto flex items-center justify-between gap-4">
        {/* Left Breadcrumb & Page Title */}
        <div className="flex items-center gap-2">
          <div className="lg:hidden flex items-center gap-2 mr-2">
            <div className="w-8 h-8 rounded-lg bg-[#004782] text-white flex items-center justify-center font-black">
              <span className="material-symbols-outlined text-[18px]">shield_with_house</span>
            </div>
          </div>
          <div>
            <div className="text-[12px] font-bold text-[#185fa5] dark:text-blue-400 flex items-center gap-1">
              <span>SR / 供应商风险监控</span>
            </div>
            <h1 className="font-bold text-[18px] lg:text-[20px] text-[#101d28] dark:text-white leading-tight">
              {getTabTitle(activeTab)}
            </h1>
          </div>
        </div>

        {/* Right Search & Controls */}
        <div className="flex items-center gap-3">
          {/* Quick Search */}
          <div className="relative hidden md:block w-64 lg:w-80">
            <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[#727782] text-[20px]">
              search
            </span>
            <input
              type="text"
              value={searchTerm}
              onChange={handleSearchChange}
              placeholder="搜索供应商、风险类型或编号..."
              className="w-full bg-[#f7f9ff] dark:bg-slate-800 border border-[#c2c6d2] dark:border-slate-700 rounded-lg pl-9 pr-3 py-1.5 text-[13px] text-[#101d28] dark:text-white focus:outline-none focus:ring-2 focus:ring-[#004782] transition-all"
            />
          </div>

          {/* Mode Toggle: Live vs Empty State Preview */}
          <button
            onClick={() => setIsSimulatedEmpty(!isSimulatedEmpty)}
            title="切换空状态与真实数据预览"
            className={`hidden sm:flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium border transition-all ${
              isSimulatedEmpty
                ? 'bg-amber-50 text-amber-700 border-amber-300 dark:bg-amber-950 dark:text-amber-200'
                : 'bg-[#ecf4ff] text-[#004782] border-[#c2c6d2] dark:bg-slate-800 dark:text-blue-300'
            }`}
          >
            <span className="material-symbols-outlined text-[16px]">
              {isSimulatedEmpty ? 'hourglass_empty' : 'graphic_eq'}
            </span>
            <span>{isSimulatedEmpty ? '空状态模式' : '实时监控模式'}</span>
          </button>

          {/* Notifications Dropdown */}
          <div className="relative">
            <button
              onClick={() => {
                setShowNotifications(!showNotifications);
                setShowProfile(false);
              }}
              className="relative p-2 text-[#424751] dark:text-slate-300 hover:bg-[#f7f9ff] dark:hover:bg-slate-800 rounded-lg transition-colors"
            >
              <span className="material-symbols-outlined text-[22px]">notifications</span>
              {unreadCount > 0 && (
                <span className="absolute top-1.5 right-1.5 w-2.5 h-2.5 rounded-full bg-[#C92A2A] ring-2 ring-white"></span>
              )}
            </button>

            {showNotifications && (
              <div className="absolute right-0 mt-2 w-80 bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-700 rounded-xl shadow-xl p-3 z-50 animate-in fade-in zoom-in-95 duration-150">
                <div className="flex justify-between items-center pb-2 border-b border-slate-100 dark:border-slate-800 mb-2">
                  <span className="font-bold text-[14px]">风险提醒通知</span>
                  <span className="text-[11px] bg-[#C92A2A] text-white px-2 py-0.5 rounded-full font-bold">
                    {unreadCount} 未读
                  </span>
                </div>
                <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
                  <div className="p-2 rounded-lg bg-red-50 dark:bg-red-950/40 text-[12px]">
                    <div className="font-bold text-[#C92A2A] flex justify-between">
                      <span>P1 极高风险: 深圳市鼎盛科技</span>
                      <span className="text-[10px] text-slate-500">10分钟前</span>
                    </div>
                    <p className="text-slate-600 dark:text-slate-300 mt-1">
                      命中 OFAC/BIS 全球制裁名单，建议立即启动供应替代预案。
                    </p>
                  </div>
                  <div className="p-2 rounded-lg bg-amber-50 dark:bg-amber-950/40 text-[12px]">
                    <div className="font-bold text-[#D97706] flex justify-between">
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

          {/* User Profile */}
          <div className="relative">
            <button
              onClick={() => {
                setShowProfile(!showProfile);
                setShowNotifications(false);
              }}
              className="flex items-center gap-2 p-1 rounded-full border border-[#c2c6d2] hover:ring-2 hover:ring-[#004782] transition-all"
            >
              <img
                src="https://lh3.googleusercontent.com/aida-public/AB6AXuAmzD0QNYwrpruEDeD7iTgFZByV4wcodVpBQWTlWW9y1b_AsztsS7QPp6WdjG-5ePvQDmjtkBKIrSZxQ8Cb19aptTZJBYM3ouD8fnQOrWOPxbWBFtsoFNrZODRfnBbLqGiMiix-05IpWdBQWzbdVIeFqxi0AOHIGssuUpbDzwNH-l1uGZoOylr-WoRaImkzXcAsXnlGk0lFwBI-y0MCtLy0T9qyCMaH6t3kjfI_KYX7lCoei6NZ1jA6"
                alt="User Avatar"
                className="w-8 h-8 rounded-full object-cover"
              />
            </button>

            {showProfile && (
              <div className="absolute right-0 mt-2 w-56 bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-700 rounded-xl shadow-xl p-3 z-50">
                <div className="pb-2 border-b border-slate-100 dark:border-slate-800 mb-2">
                  <div className="font-bold text-[14px]">首席风险官 (CRO)</div>
                  <div className="text-[12px] text-slate-500 truncate">jojo19920612@gmail.com</div>
                </div>
                <div className="space-y-1 text-[13px]">
                  <button className="w-full text-left px-2 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg">
                    个人账号配置
                  </button>
                  <button className="w-full text-left px-2 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg">
                    API 访问密钥 (Key)
                  </button>
                  <button className="w-full text-left px-2 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg text-red-600">
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

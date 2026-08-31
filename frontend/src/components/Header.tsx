import {useState} from 'react';
import {Bell} from 'lucide-react';
import type {AuthUser} from '../api';
import type {RiskItem} from '../types';

interface HeaderProps {
  unreadCount: number;
  riskItems: RiskItem[];
  user: AuthUser;
  onLogout: () => void;
}

// 顶部状态栏胶囊（模型 / 数据流 / 服务健康）和搜索框已移除，保留主题、提醒中心与账号菜单。
export const Header = ({unreadCount, riskItems, user, onLogout}: HeaderProps) => {
  const [showNotifications, setShowNotifications] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const currentRisks = riskItems.filter((item) => item.level === 'P1' || item.level === 'P2').slice(0, 5);

  return (
    <header className="sticky top-0 z-20 border-b border-slate-200/80 bg-white/75 px-4 py-2.5 backdrop-blur-xl dark:border-slate-800 dark:bg-[#0e1726]/75 lg:px-6">
      <div className="mx-auto flex max-w-[1440px] items-center justify-between gap-4">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center gap-2 lg:hidden"><img src="/logo.svg" alt="供应商风险监控平台" className="h-8 w-8 rounded-lg"/></div>
          <div className="text-[16px] font-bold leading-tight text-[#185fa5] dark:text-blue-400 lg:text-[18px]">供应商风险监控平台</div>
        </div>

        <div className="flex items-center gap-2.5">
          <div className="relative"><button type="button" aria-label="查看风险提醒" onClick={() => {setShowNotifications((value) => !value); setShowProfile(false);}} className="relative rounded-lg p-1.5 text-slate-600 transition hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"><Bell className="h-5 w-5"/>{unreadCount > 0 && <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-[#C92A2A] ring-2 ring-white dark:ring-slate-900"/>}</button>{showNotifications && <div className="absolute right-0 z-50 mt-2 w-80 rounded-xl border border-slate-200 bg-white/95 p-3 shadow-2xl backdrop-blur-xl dark:border-slate-800 dark:bg-slate-900/95"><div className="mb-2 flex items-center justify-between border-b border-slate-100 pb-2 dark:border-slate-800"><span className="text-[13px] font-bold">风险通知中心</span><span className="rounded-full bg-[#C92A2A] px-2 py-0.5 text-[10px] font-bold text-white">{unreadCount} 条 P1</span></div><div className="max-h-64 space-y-2 overflow-y-auto pr-1">{currentRisks.length === 0 ? <div className="p-4 text-center text-[12px] text-slate-400">暂无 P1/P2 当前风险</div> : currentRisks.map((risk) => <div key={risk.id} className={`rounded-lg p-2.5 text-[12px] ${risk.level === 'P1' ? 'bg-red-50 dark:bg-red-950/40' : 'bg-amber-50 dark:bg-amber-950/40'}`}><div className={`flex justify-between gap-2 font-bold ${risk.level === 'P1' ? 'text-[#C92A2A]' : 'text-[#D97706]'}`}><span>{risk.level} · {risk.companyName}</span><span className="text-[10px] text-red-700/70 dark:text-red-300/70">{risk.updatedTime}</span></div><p className="mt-1 line-clamp-2 text-red-900/80 dark:text-red-100/80">{risk.summary}</p></div>)}</div></div>}</div>
          <div className="relative"><button type="button" aria-label="打开本地系统菜单" onClick={() => {setShowProfile((value) => !value); setShowNotifications(false);}} className="flex h-8 w-8 items-center justify-center rounded-full bg-[#004782] text-[11px] font-black text-white transition hover:ring-2 hover:ring-[#185fa5]/50">{(user.display_name || user.username || 'SR').slice(0, 2).toUpperCase()}</button>{showProfile && <div className="absolute right-0 z-50 mt-2 w-56 rounded-xl border border-slate-200 bg-white/95 p-3 shadow-2xl backdrop-blur-xl dark:border-slate-800 dark:bg-slate-900/95"><div className="mb-2 border-b border-slate-100 pb-2 dark:border-slate-800"><div className="truncate text-[13px] font-bold">{user.display_name || user.username}</div><div className="text-[11px] text-slate-500">{user.role}</div></div><a href="/api/docs" target="_blank" rel="noreferrer" className="block rounded-lg px-2 py-1.5 text-[12px] hover:bg-slate-100 dark:hover:bg-slate-800">打开接口文档</a><button type="button" onClick={onLogout} className="mt-1 block w-full rounded-lg px-2 py-1.5 text-left text-[12px] text-red-700 hover:bg-red-50 dark:text-red-300 dark:hover:bg-red-950/30">退出登录</button></div>}</div>
        </div>
      </div>
    </header>
  );
};

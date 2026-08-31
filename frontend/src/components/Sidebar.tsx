import type {ReactNode} from 'react';
import {motion} from 'motion/react';
import {NavLink} from 'react-router-dom';
import {Building2, Bot, Database, HelpCircle, LayoutDashboard, Settings, ShieldAlert, SlidersHorizontal, Users} from 'lucide-react';
import {visibleNavigationRoutes, type NavigationIcon, type NavigationRoute} from '../routes';

interface SidebarProps {
  readonly permissions: readonly string[];
  readonly onOpenSettingsModal: () => void;
  readonly p1RiskCount: number;
}

const icons = {
  overview: <LayoutDashboard className="h-[18px] w-[18px]" />,
  risks: <ShieldAlert className="h-[18px] w-[18px]" />,
  assistant: <Bot className="h-[18px] w-[18px]" />,
  suppliers: <Building2 className="h-[18px] w-[18px]" />,
  sources: <Database className="h-[18px] w-[18px]" />,
  rules: <SlidersHorizontal className="h-[18px] w-[18px]" />,
  userSettings: <Users className="h-[18px] w-[18px]" />,
} satisfies Record<NavigationIcon, ReactNode>;

const SidebarItem = ({route, p1RiskCount}: {readonly route: NavigationRoute; readonly p1RiskCount: number}) => (
  <NavLink
    end={route.navigation.end}
    to={route.path}
    className={({isActive}) => `relative flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-[13px] font-semibold transition-all ${
      isActive ? 'text-white' : 'text-slate-700 hover:bg-slate-200/70 dark:text-slate-300 dark:hover:bg-slate-800/70'
    }`}
  >
    {({isActive}) => (
      <motion.span whileTap={{scale: 0.98}} className="flex w-full items-center justify-between">
        {isActive && <motion.span layoutId="sidebarTabHighlight" className="absolute inset-0 rounded-lg bg-[#185fa5] shadow-sm" transition={{type: 'spring', stiffness: 500, damping: 35}} />}
        <span className="relative z-10 flex items-center gap-2.5">{icons[route.navigation.icon]}<span>{route.navigation.desktopLabel}</span></span>
        {route.id === 'risks' && p1RiskCount > 0 ? <span className={`relative z-10 rounded-full px-1.5 py-0.5 text-[10px] font-bold ${isActive ? 'bg-white text-[#C92A2A]' : 'bg-[#C92A2A] text-white'}`}>{p1RiskCount}</span> : null}
      </motion.span>
    )}
  </NavLink>
);

export const Sidebar = ({permissions, onOpenSettingsModal, p1RiskCount}: SidebarProps) => {
  const desktopRoutes = visibleNavigationRoutes(permissions, 'desktop');
  const mainItems = desktopRoutes.filter((route) => route.navigation.section === 'main');
  const systemItems = desktopRoutes.filter((route) => route.navigation.section === 'system');

  return (
    <nav className="fixed left-0 top-0 z-30 hidden h-screen w-[240px] select-none flex-col gap-4 border-r border-slate-200/80 bg-slate-100/90 p-3.5 backdrop-blur-2xl dark:border-slate-800/80 dark:bg-[#0c1420]/90 lg:flex">
      <div className="flex items-center gap-2.5 rounded-xl border border-black/5 bg-white/70 px-2.5 py-2 shadow-sm dark:border-white/5 dark:bg-slate-800/60">
        <img src="/logo.svg" alt="供应商风险监控平台" className="h-8 w-8 rounded-lg object-cover" />
        <div className="min-w-0">
          <div className="truncate text-[13px] font-extrabold leading-tight text-slate-900 dark:text-white">SR Risk Studio</div>
          <div className="truncate text-[10px] font-medium text-slate-500 dark:text-slate-400">企业级供应链风险系统</div>
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-4 overflow-y-auto pr-0.5">
        <div className="space-y-1">{mainItems.map((route) => <SidebarItem key={route.id} route={route} p1RiskCount={p1RiskCount} />)}</div>
        <div className="space-y-1">{systemItems.map((route) => <SidebarItem key={route.id} route={route} p1RiskCount={p1RiskCount} />)}</div>
      </div>

      <div className="mt-auto flex flex-col gap-1 border-t border-slate-200/80 pt-3 dark:border-slate-800">
        <div className="grid grid-cols-2 gap-1">
          <button type="button" onClick={onOpenSettingsModal} className="flex items-center justify-center gap-1.5 rounded-lg p-1.5 text-[12px] font-medium text-slate-600 transition-colors hover:bg-slate-200/70 dark:text-slate-300 dark:hover:bg-slate-800"><Settings className="h-[15px] w-[15px]" /><span>设置</span></button>
          <button type="button" onClick={() => alert('如需技术支持，请联系 SR 风险监控团队 support@srmonitoring.com')} className="flex items-center justify-center gap-1.5 rounded-lg p-1.5 text-[12px] font-medium text-slate-600 transition-colors hover:bg-slate-200/70 dark:text-slate-300 dark:hover:bg-slate-800"><HelpCircle className="h-[15px] w-[15px]" /><span>帮助</span></button>
        </div>
      </div>
    </nav>
  );
};

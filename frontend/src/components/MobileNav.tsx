import type {ReactNode} from 'react';
import {motion} from 'motion/react';
import {NavLink} from 'react-router-dom';
import {AlertTriangle, Bot, Building2, Database, LayoutDashboard, SlidersHorizontal, Users} from 'lucide-react';
import {visibleNavigationRoutes, type NavigationIcon, type NavigationRoute} from '../routes';

interface MobileNavProps {
  readonly permissions: readonly string[];
  readonly p1RiskCount: number;
}

const icons = {
  overview: <LayoutDashboard className="h-5 w-5" />,
  risks: <AlertTriangle className="h-5 w-5" />,
  assistant: <Bot className="h-5 w-5" />,
  suppliers: <Building2 className="h-5 w-5" />,
  sources: <Database className="h-5 w-5" />,
  rules: <SlidersHorizontal className="h-5 w-5" />,
  userSettings: <Users className="h-5 w-5" />,
} satisfies Record<NavigationIcon, ReactNode>;

const MobileNavItem = ({route, p1RiskCount}: {readonly route: NavigationRoute; readonly p1RiskCount: number}) => (
  <NavLink
    end={route.navigation.end}
    to={route.path}
    className={({isActive}) => `relative flex min-w-0 flex-1 flex-col items-center justify-center rounded-xl p-2 transition-all ${
      isActive ? 'bg-[#185fa5] font-bold text-white shadow-sm' : 'text-slate-600 hover:bg-slate-200/60 dark:text-slate-400 dark:hover:bg-slate-800'
    }`}
  >
    {({isActive}) => (
      <motion.span whileTap={{scale: 0.85}} className="relative flex min-w-0 flex-col items-center">
        {icons[route.navigation.icon]}
        <span className="mt-1 text-[10px] font-medium leading-none">{route.navigation.mobileLabel}</span>
        {isActive && <span className="mt-0.5 h-1 w-1 rounded-full bg-white" />}
        {route.id === 'risks' && p1RiskCount > 0 ? <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-[#C92A2A] text-[9px] font-bold text-white">{p1RiskCount}</span> : null}
      </motion.span>
    )}
  </NavLink>
);

export const MobileNav = ({permissions, p1RiskCount}: MobileNavProps) => {
  const mobileRoutes = visibleNavigationRoutes(permissions, 'mobile');

  return <div className="fixed bottom-3 left-1/2 z-50 w-[92%] max-w-md -translate-x-1/2 lg:hidden"><nav className="flex items-center justify-around gap-1 rounded-2xl border border-white/60 bg-white/80 p-1.5 shadow-2xl backdrop-blur-2xl dark:border-slate-800 dark:bg-slate-900/80">{mobileRoutes.map((route) => <MobileNavItem key={route.id} route={route} p1RiskCount={p1RiskCount} />)}</nav></div>;
};

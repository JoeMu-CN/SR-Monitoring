import {useEffect, useState, type ReactNode} from 'react';
import {motion} from 'motion/react';
import {NavLink} from 'react-router-dom';
import {Activity, Building2, Bot, Database, HelpCircle, LayoutDashboard, Newspaper, Settings, ShieldAlert, SlidersHorizontal, Users} from 'lucide-react';
import type {AuthUser} from '../api';
import {visibleNavigationRoutes, type NavigationIcon, type NavigationRoute} from '../routes';
import {UserMenu} from './UserMenu';

export type SidebarStage = 0 | 1 | 2;

interface SidebarProps {
  readonly permissions: readonly string[];
  readonly onOpenSettingsModal: () => void;
  readonly p1RiskCount: number;
  readonly user: AuthUser;
  readonly onLogout: () => void;
  readonly stage: SidebarStage;
  readonly onAdvanceStage: () => void;
}

const icons = {
  overview: <LayoutDashboard className="h-[18px] w-[18px]" />,
  risks: <ShieldAlert className="h-[18px] w-[18px]" />,
  intelligence: <Newspaper className="h-[18px] w-[18px]" />,
  assistant: <Bot className="h-[18px] w-[18px]" />,
  suppliers: <Building2 className="h-[18px] w-[18px]" />,
  sources: <Database className="h-[18px] w-[18px]" />,
  scheduler: <Activity className="h-[18px] w-[18px]" />,
  rules: <SlidersHorizontal className="h-[18px] w-[18px]" />,
  userSettings: <Users className="h-[18px] w-[18px]" />,
} satisfies Record<NavigationIcon, ReactNode>;

const SidebarItem = ({
  route,
  p1RiskCount,
  compact,
}: {
  readonly route: NavigationRoute;
  readonly p1RiskCount: number;
  readonly compact: boolean;
}) => {
  const label = route.navigation.desktopLabel;

  return (
    <NavLink
      end={route.navigation.end}
      to={route.path}
      aria-label={compact ? label : undefined}
      title={compact ? label : undefined}
      className={({isActive}) => `relative flex w-full items-center rounded-xl text-left text-[13px] font-semibold transition-all ${
        compact ? 'justify-center p-2' : 'justify-between px-3 py-2.5'
      } ${
        isActive
          ? 'bg-[#185fa5] text-white shadow-sm'
          : 'text-slate-700 hover:bg-slate-200/70 dark:text-slate-300 dark:hover:bg-slate-800/70'
      }`}
    >
      {({isActive}) => (
        <motion.span whileTap={{scale: 0.98}} className={`relative flex w-full items-center ${compact ? 'justify-center' : 'justify-between'}`}>
          <span className={`relative z-10 flex items-center ${compact ? 'justify-center' : 'gap-2.5'}`}>
            {icons[route.navigation.icon]}
            {!compact && <span className="whitespace-nowrap">{label}</span>}
          </span>
          {route.id === 'risks' && p1RiskCount > 0 ? (
            <span
              className={`z-10 rounded-full px-1.5 py-0.5 text-[10px] font-bold ${
                compact ? 'absolute right-0 top-0' : 'relative'
              } ${isActive ? 'bg-white text-[#C92A2A]' : 'bg-[#C92A2A] text-white'}`}
            >
              {p1RiskCount}
            </span>
          ) : null}
        </motion.span>
      )}
    </NavLink>
  );
};

export const Sidebar = ({
  permissions,
  onOpenSettingsModal,
  p1RiskCount,
  user,
  onLogout,
  stage,
  onAdvanceStage,
}: SidebarProps) => {
  const desktopRoutes = visibleNavigationRoutes(permissions, 'desktop');
  const mainItems = desktopRoutes.filter((route) => route.navigation.section === 'main');
  const systemItems = desktopRoutes.filter((route) => route.navigation.section === 'system');
  const compact = stage > 0;
  const [hovered, setHovered] = useState(false);
  const [toggleVisible, setToggleVisible] = useState(true);

  useEffect(() => {
    if (hovered) {
      setToggleVisible(true);
      return undefined;
    }
    setToggleVisible(true);
    const timer = window.setTimeout(() => setToggleVisible(false), 2000);
    return () => window.clearTimeout(timer);
  }, [hovered, stage]);

  // 进入二级折叠后侧栏会被卸载，主动清掉悬停态，避免恢复手柄一直可见。
  useEffect(() => {
    if (stage === 2) setHovered(false);
  }, [stage]);

  const toggleLabel = stage === 0 ? '收起导航到一级' : stage === 1 ? '继续收起到二级' : '展开导航';
  const widthClass = stage === 0 ? 'w-[240px] p-3.5' : 'w-[72px] p-2';

  const toggleButton = (placement: string) => (
    <button
      type="button"
      onClick={onAdvanceStage}
      aria-label={toggleLabel}
      aria-expanded={stage === 0}
      aria-controls="desktop-sidebar"
      title={toggleLabel}
      data-stage={stage}
      className={`group absolute top-1/2 z-40 flex h-16 w-6 -translate-y-1/2 items-center justify-center rounded-full transition-opacity duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300/70 ${placement} ${
        toggleVisible ? 'opacity-100' : 'pointer-events-none opacity-0'
      }`}
    >
      <span className="h-14 w-1.5 rounded-full bg-sky-400/50 shadow-[0_0_10px_rgba(56,189,248,0.3)] transition-all duration-200 group-hover:w-2 group-hover:bg-sky-300/80" />
    </button>
  );

  if (stage === 2) {
    return (
      <div
        className="fixed left-0 top-0 z-40 hidden h-screen w-4 lg:block"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onFocus={() => setHovered(true)}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setHovered(false);
        }}
      >
        {toggleButton('left-0')}
      </div>
    );
  }

  return (
    <nav
      id="desktop-sidebar"
      aria-label="主导航"
      data-stage={stage}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setHovered(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setHovered(false);
      }}
      className={`fixed left-0 top-0 z-30 hidden h-screen select-none flex-col gap-4 border-r border-slate-200/80 bg-slate-100/90 backdrop-blur-2xl transition-[width,padding] duration-200 dark:border-slate-800/80 dark:bg-[#0c1420]/90 lg:flex ${widthClass}`}
    >
      {toggleButton('-right-3')}

      <div className={`flex items-center rounded-xl border border-black/5 bg-white/70 shadow-sm dark:border-white/5 dark:bg-slate-800/60 ${
        compact ? 'justify-center px-1.5 py-2' : 'gap-2.5 px-2.5 py-2'
      }`}>
        <img src="/logo.svg" alt="供应商风险监控平台" className="h-8 w-8 shrink-0 rounded-lg object-cover" />
        {!compact && (
          <div className="min-w-0">
            <div className="truncate text-[13px] font-extrabold leading-tight text-slate-900 dark:text-white">SR Risk Studio</div>
            <div className="truncate text-[10px] font-medium text-slate-500 dark:text-slate-400">企业级供应链风险系统</div>
          </div>
        )}
      </div>

      <div className={`flex flex-1 flex-col gap-4 overflow-y-auto ${compact ? 'items-center' : 'pr-0.5'}`}>
        <div className={`space-y-1 ${compact ? 'w-full' : ''}`}>
          {mainItems.map((route) => <SidebarItem key={route.id} route={route} p1RiskCount={p1RiskCount} compact={compact} />)}
        </div>
        <div className={`space-y-1 ${compact ? 'w-full' : ''}`}>
          {systemItems.map((route) => <SidebarItem key={route.id} route={route} p1RiskCount={p1RiskCount} compact={compact} />)}
        </div>
      </div>

      <div className={`mt-auto flex border-t border-slate-200/80 pt-3 dark:border-slate-800 ${
        compact ? 'flex-col items-center gap-1.5' : 'items-center gap-1'
      }`}>
        <button
          type="button"
          onClick={onOpenSettingsModal}
          aria-label={compact ? '设置' : undefined}
          title={compact ? '设置' : undefined}
          className={`flex items-center rounded-lg text-slate-600 transition-colors hover:bg-slate-200/70 dark:text-slate-300 dark:hover:bg-slate-800 ${
            compact ? 'h-9 w-9 justify-center' : 'flex-1 justify-center gap-1.5 p-1.5 text-[12px] font-medium'
          }`}
        >
          <Settings className="h-[15px] w-[15px]" />
          {!compact && <span>设置</span>}
        </button>
        <button
          type="button"
          aria-label="帮助"
          title="帮助"
          onClick={() => alert('如需技术支持，请联系 SR 风险监控团队 support@srmonitoring.com')}
          className="grid h-8 w-8 place-items-center rounded-lg text-slate-600 transition-colors hover:bg-slate-200/70 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <HelpCircle className="h-[15px] w-[15px]" />
        </button>
        <UserMenu user={user} onLogout={onLogout} compact menuAlign={compact ? 'left' : 'right'} />
      </div>
    </nav>
  );
};

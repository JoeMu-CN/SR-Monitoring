import type {ReactNode} from 'react';
import {Navigate, Route, Routes, type RoutesProps} from 'react-router-dom';
import {hasRoutePermission, routeDefinitions, routePaths, type RouteId} from './routes';

export type RouteViews = {readonly [Route in RouteId]: ReactNode};

interface AppRoutesProps {
  readonly permissions: readonly string[];
  readonly views: RouteViews;
  // 显式匹配位置：动画退出壳必须固定创建时的 location，避免用最新的路由位置重新匹配。
  readonly location?: RoutesProps['location'];
}

const RouteState = ({title, detail}: {readonly title: string; readonly detail: string}) => (
  <section className="mx-auto flex min-h-[50vh] max-w-xl flex-col items-start justify-center gap-3 rounded-2xl border border-slate-200/80 bg-white/80 p-6 shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60" role="alert">
    <h1 className="text-xl font-black tracking-tight text-slate-900 dark:text-white">{title}</h1>
    <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">{detail}</p>
  </section>
);

const PermissionGuard = ({allowed, children}: {readonly allowed: boolean; readonly children: ReactNode}) => (
  allowed
    ? <>{children}</>
    : <RouteState title="无权访问" detail="当前账号没有访问此页面所需的权限。" />
);

export const AppRoutes = ({permissions, views, location}: AppRoutesProps) => (
  <Routes location={location}>
    <Route path="/" element={<Navigate replace to={routePaths.overview} />} />
    {routeDefinitions.map((route) => (
      <Route
        key={route.id}
        path={route.path}
        element={<PermissionGuard allowed={hasRoutePermission(route, permissions)}>{views[route.id]}</PermissionGuard>}
      />
    ))}
    <Route path="*" element={<RouteState title="页面不存在" detail="请从导航中选择可访问的页面。" />} />
  </Routes>
);

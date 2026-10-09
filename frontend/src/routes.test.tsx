import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter, useLocation, useNavigate} from 'react-router-dom';
import {afterEach, describe, expect, it} from 'vitest';
import {AppRoutes, type RouteViews} from './AppRoutes';
import {
  allRoutePermissions,
  isSupplierStatusFilter,
  routeDefinitions,
  routePaths,
  routePermissions,
  supplierSearchParams,
  suppliersPath,
  visibleNavigationRoutes,
} from './routes';

const routeViews: RouteViews = {
  overview: <div>总览页面</div>,
  risks: <div>风险页面</div>,
  riskDetail: <div>风险详情页面</div>,
  assistant: <div>助手页面</div>,
  suppliers: <div>供应商页面</div>,
  sources: <div>信息源页面</div>,
  sourceSignals: <div>采集记录页面</div>,
  scheduler: <div>调度器页面</div>,
  rules: <div>规则页面</div>,
  userSettings: <div>用户设置页面</div>,
};

const LocationProbe = () => {
  const location = useLocation();
  return <output aria-label="当前路径">{location.pathname}</output>;
};

const HistoryBack = () => {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate(-1)}>后退</button>;
};

const HistoryForward = () => {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate(1)}>前进</button>;
};

const renderRoute = (initialEntries: readonly string[], permissions: readonly string[] = allRoutePermissions, initialIndex?: number) => {
  render(
    <MemoryRouter initialEntries={[...initialEntries]} initialIndex={initialIndex}>
      <AppRoutes permissions={permissions} views={routeViews} />
      <HistoryBack />
      <HistoryForward />
      <LocationProbe />
    </MemoryRouter>,
  );
};

afterEach(cleanup);

describe('显式路由白名单', () => {
  it.each([
    ['/overview', '总览页面'],
    ['/risks', '风险页面'],
    ['/risks/42', '风险详情页面'],
    ['/assistant', '助手页面'],
    ['/suppliers', '供应商页面'],
    ['/sources', '信息源页面'],
    ['/sources/17/signals?scope=valid&page=1', '采集记录页面'],
    ['/scheduler', '调度器页面'],
    ['/rules', '规则页面'],
    ['/settings/users', '用户设置页面'],
  ])('在拥有准确权限时渲染 %s', (path, page) => {
    renderRoute([path]);

    expect(screen.getByText(page)).toBeInTheDocument();
  });

  it('将根路径替换为规范总览路径', () => {
    renderRoute(['/']);

    expect(screen.getByLabelText('当前路径')).toHaveTextContent('/overview');
    expect(screen.getByText('总览页面')).toBeInTheDocument();
  });

  it('对直接访问使用精确后端权限而不是 source_manage', () => {
    renderRoute(['/assistant'], ['source_manage']);

    expect(screen.getByRole('alert')).toHaveTextContent('无权访问');
    expect(screen.queryByText('助手页面')).not.toBeInTheDocument();
  });

  it.each([
    ['/risks', ['supplier_view']],
    ['/suppliers', ['risk_view']],
    ['/sources', ['rule_summary_view']],
    ['/sources/17/signals?scope=all&page=2', ['rule_summary_view']],
    ['/scheduler', ['rule_summary_view']],
    ['/rules', ['source_status_view']],
    ['/settings/users', ['source_manage']],
  ])('拒绝缺少准确权限的直接访问：%s', (path, permissions) => {
    renderRoute([path], permissions);

    expect(screen.getByRole('alert')).toHaveTextContent('无权访问');
  });

  it('仅持有 rule_summary_view 时允许直接访问 /rules', () => {
    renderRoute(['/rules'], [routePermissions.ruleSummaryView]);

    expect(screen.getByText('规则页面')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('持有其它任一页面权限但缺少 rule_summary_view 时拒绝 /rules', () => {
    renderRoute(['/rules'], [routePermissions.ruleManage]);

    expect(screen.getByRole('alert')).toHaveTextContent('无权访问');
    expect(screen.queryByText('规则页面')).not.toBeInTheDocument();
  });

  it.each(['/missing', '/research', '/source-agent'])('将未知或冻结路径 %s 渲染为 404', (path) => {
    renderRoute([path]);

    expect(screen.getByRole('alert')).toHaveTextContent('页面不存在');
    expect(screen.queryByText('研究页面')).not.toBeInTheDocument();
    expect(screen.queryByText('信息源接入助手页面')).not.toBeInTheDocument();
  });

  it('在历史记录中后退时恢复前一个路由页面', async () => {
    const user = userEvent.setup();
    renderRoute(['/overview', '/risks'], allRoutePermissions, 1);

    expect(screen.getByText('风险页面')).toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: '后退'}));

    expect(screen.getByText('总览页面')).toBeInTheDocument();
    expect(screen.getByLabelText('当前路径')).toHaveTextContent('/overview');
  });

  it('在历史记录中前进时恢复后一个路由页面', async () => {
    const user = userEvent.setup();
    renderRoute(['/overview', '/risks'], allRoutePermissions, 0);

    expect(screen.getByText('总览页面')).toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: '前进'}));

    expect(screen.getByText('风险页面')).toBeInTheDocument();
    expect(screen.getByLabelText('当前路径')).toHaveTextContent('/risks');
  });
});

describe('路由元数据', () => {
  it('只声明了允许页面且每个导航项使用同一份权限定义', () => {
    expect(routeDefinitions.map((route) => route.path)).toEqual([
      '/overview',
      '/risks',
      '/risks/:alertId',
      '/assistant',
      '/suppliers',
      '/sources',
      '/sources/:sourceId/signals',
      '/scheduler',
      '/rules',
      '/settings/users',
    ]);
    expect(routeDefinitions.find((route) => route.id === 'assistant')?.permission).toBe('risk_query_use');
    expect(routeDefinitions.find((route) => route.id === 'userSettings')?.permission).toBe('user_manage');
    // /rules 的页面权限锚定为 rule_summary_view（不是 rule_manage/source_status_view）
    expect(routePermissions.ruleSummaryView).toBe('rule_summary_view');
    expect(routeDefinitions.find((route) => route.id === 'rules')?.path).toBe('/rules');
    expect(routeDefinitions.find((route) => route.id === 'rules')?.permission).toBe(
      routePermissions.ruleSummaryView,
    );
  });

  it('调度器实况是独立路由：路径、source_status_view 权限与菜单标签唯一来源', () => {
    expect(routePaths.scheduler).toBe('/scheduler');
    const scheduler = routeDefinitions.find((route) => route.id === 'scheduler');
    expect(scheduler?.path).toBe('/scheduler');
    expect(scheduler?.permission).toBe(routePermissions.sourceStatusView);
    expect(scheduler?.navigation?.desktopLabel).toBe('调度器实况');
  });

  it('桌面 system 导航顺序为信息源 → 调度器实况 → 规则引擎，且调度器不进入移动底栏', () => {
    const desktopSystem = visibleNavigationRoutes(allRoutePermissions, 'desktop')
      .filter((route) => route.navigation.section === 'system')
      .map((route) => route.id);
    expect(desktopSystem).toEqual(['sources', 'scheduler', 'rules', 'userSettings']);

    // 移动端保持路由可达但不得挤进底部导航：底栏入口维持现有 7 项。
    const mobileIds = visibleNavigationRoutes(allRoutePermissions, 'mobile').map((route) => route.id);
    expect(mobileIds).toEqual(['overview', 'risks', 'assistant', 'suppliers', 'sources', 'rules', 'userSettings']);
    expect(mobileIds).not.toContain('scheduler');
  });

  it('导航可见性与路由权限一致：缺少 source_status_view 时调度器既不可见也被路由守卫拒绝', () => {
    const permissionsWithoutSourceStatus = allRoutePermissions.filter(
      (permission) => permission !== routePermissions.sourceStatusView,
    );
    const desktopIds = visibleNavigationRoutes(permissionsWithoutSourceStatus, 'desktop').map((route) => route.id);
    expect(desktopIds).not.toContain('scheduler');

    renderRoute(['/scheduler'], permissionsWithoutSourceStatus);
    expect(screen.getByRole('alert')).toHaveTextContent('无权访问');
    expect(screen.queryByText('调度器页面')).not.toBeInTheDocument();
  });

  it('供应商清单地址省略默认值，只为非默认状态保留查询参数且不含页码', () => {
    expect(suppliersPath()).toBe('/suppliers');
    expect(suppliersPath('', 'all')).toBe('/suppliers');
    expect(suppliersPath('钢材', 'paused')).toBe('/suppliers?q=%E9%92%A2%E6%9D%90&status=paused');
    expect(suppliersPath('', 'high_risk')).toBe('/suppliers?status=high_risk');
    expect(supplierSearchParams('钢材', 'all').toString()).toBe('q=%E9%92%A2%E6%9D%90');
  });

  it('只接受四种监控状态筛选值', () => {
    expect(['all', 'normal', 'high_risk', 'paused'].every(isSupplierStatusFilter)).toBe(true);
    expect(isSupplierStatusFilter('bogus')).toBe(false);
    expect(isSupplierStatusFilter(null)).toBe(false);
  });
});

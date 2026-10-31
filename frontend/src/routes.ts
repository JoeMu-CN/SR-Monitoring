export const routePermissions = {
  riskView: 'risk_view',
  supplierView: 'supplier_view',
  sourceStatusView: 'source_status_view',
  ruleSummaryView: 'rule_summary_view',
  riskQueryUse: 'risk_query_use',
  userManage: 'user_manage',
  sourceManage: 'source_manage',
  supplierManage: 'supplier_manage',
  ruleManage: 'rule_manage',
} as const;

export type RoutePermission = typeof routePermissions[keyof typeof routePermissions];
export type RouteId = 'overview' | 'risks' | 'intelligence' | 'riskDetail' | 'eventDetail' | 'assistant' | 'suppliers' | 'sources' | 'sourceSignals' | 'scheduler' | 'rules' | 'userSettings';
export type NavigationSurface = 'desktop' | 'mobile';
export type NavigationSection = 'main' | 'system';
export type NavigationIcon = 'overview' | 'risks' | 'intelligence' | 'assistant' | 'suppliers' | 'sources' | 'scheduler' | 'rules' | 'userSettings';

interface NavigationMetadata {
  readonly surfaces: readonly NavigationSurface[];
  readonly section: NavigationSection;
  readonly desktopLabel: string;
  readonly mobileLabel: string;
  readonly icon: NavigationIcon;
  readonly end: boolean;
}

export interface RouteDefinition {
  readonly id: RouteId;
  readonly path: string;
  readonly permission: RoutePermission;
  readonly navigation?: NavigationMetadata;
}

export interface NavigationRoute extends RouteDefinition {
  readonly navigation: NavigationMetadata;
}

export const routePaths = {
  overview: '/overview',
  risks: '/risks',
  intelligence: '/intelligence',
  riskDetail: '/risks/:alertId',
  // 事件详情：从风险事件视图的成员进入，独立于供应商维度的 /risks/:alertId。
  eventDetail: '/events/:eventId',
  assistant: '/assistant',
  suppliers: '/suppliers',
  sources: '/sources',
  sourceSignals: '/sources/:sourceId/signals',
  scheduler: '/scheduler',
  rules: '/rules',
  userSettings: '/settings/users',
} as const;

export const routeDefinitions: readonly RouteDefinition[] = [
  {
    id: 'overview',
    path: routePaths.overview,
    permission: routePermissions.riskView,
    navigation: {surfaces: ['desktop', 'mobile'], section: 'main', desktopLabel: '风险总览', mobileLabel: '总览', icon: 'overview', end: true},
  },
  {
    id: 'risks',
    path: routePaths.risks,
    permission: routePermissions.riskView,
    navigation: {surfaces: ['desktop', 'mobile'], section: 'main', desktopLabel: '风险事件', mobileLabel: '风险', icon: 'risks', end: false},
  },
  {
    // 情报聚合：政策/行业维度的宏观报告页。两个 Tab 是页内状态，不是独立路径，
    // 因此 end=true 且只进入桌面主导航（移动底栏维持现有 7 个入口）。
    id: 'intelligence',
    path: routePaths.intelligence,
    permission: routePermissions.riskView,
    navigation: {surfaces: ['desktop'], section: 'main', desktopLabel: '情报聚合', mobileLabel: '情报', icon: 'intelligence', end: true},
  },
  {id: 'riskDetail', path: routePaths.riskDetail, permission: routePermissions.riskView},
  // 事件详情只经站内跳转到达，不进导航。
  {id: 'eventDetail', path: routePaths.eventDetail, permission: routePermissions.riskView},
  {
    id: 'assistant',
    path: routePaths.assistant,
    permission: routePermissions.riskQueryUse,
    navigation: {surfaces: ['desktop', 'mobile'], section: 'main', desktopLabel: '风险查询助手', mobileLabel: '助手', icon: 'assistant', end: true},
  },
  {
    id: 'suppliers',
    path: routePaths.suppliers,
    permission: routePermissions.supplierView,
    navigation: {surfaces: ['desktop', 'mobile'], section: 'main', desktopLabel: '供应商名录', mobileLabel: '供应商', icon: 'suppliers', end: true},
  },
  {
    id: 'sources',
    path: routePaths.sources,
    permission: routePermissions.sourceStatusView,
    navigation: {surfaces: ['desktop', 'mobile'], section: 'system', desktopLabel: '信息源列表', mobileLabel: '数据', icon: 'sources', end: true},
  },
  {id: 'sourceSignals', path: routePaths.sourceSignals, permission: routePermissions.sourceStatusView},
  {
    id: 'scheduler',
    path: routePaths.scheduler,
    permission: routePermissions.sourceStatusView,
    // 独立调度器实况页只进入桌面 system 导航；移动端保持路由可达（直接访问/站内跳转），
    // 但不挤入底部导航的第 8 个入口，也不新增「更多」菜单。
    navigation: {surfaces: ['desktop'], section: 'system', desktopLabel: '调度器实况', mobileLabel: '调度器', icon: 'scheduler', end: true},
  },
  {
    id: 'rules',
    path: routePaths.rules,
    permission: routePermissions.ruleSummaryView,
    navigation: {surfaces: ['desktop', 'mobile'], section: 'system', desktopLabel: '规则引擎', mobileLabel: '规则', icon: 'rules', end: true},
  },
  {
    id: 'userSettings',
    path: routePaths.userSettings,
    permission: routePermissions.userManage,
    navigation: {surfaces: ['desktop', 'mobile'], section: 'system', desktopLabel: '用户管理', mobileLabel: '用户', icon: 'userSettings', end: true},
  },
] as const;

export const allRoutePermissions: readonly RoutePermission[] = Object.values(routePermissions);

export const riskDetailPath = (alertId: string) => `${routePaths.risks}/${alertId}`;

/** 事件详情路径：与供应商维度详情一样只做站内跳转，不进入导航。 */
export const eventDetailPath = (eventId: number | string) => `/events/${eventId}`;

export type SourceSignalScope = 'valid' | 'all';

export const sourceSignalsPath = (sourceId: string, scope: SourceSignalScope, page = 1) => (
  `${routePaths.sources}/${sourceId}/signals?scope=${scope}&page=${page}`
);

export type SupplierStatusFilter = 'all' | 'normal' | 'high_risk' | 'paused';

const supplierStatusFilters: readonly SupplierStatusFilter[] = ['all', 'normal', 'high_risk', 'paused'];

export const isSupplierStatusFilter = (value: string | null): value is SupplierStatusFilter => (
  value !== null && supplierStatusFilters.includes(value as SupplierStatusFilter)
);

/** 供应商清单的规范查询串：单页展示全部供应商，省略默认值并保证同一视图状态只有一个 URL。 */
export const supplierSearchParams = (query: string, status: SupplierStatusFilter) => {
  const params = new URLSearchParams();
  if (query) params.set('q', query);
  if (status !== 'all') params.set('status', status);
  return params;
};

export const suppliersPath = (query = '', status: SupplierStatusFilter = 'all') => {
  const search = supplierSearchParams(query, status).toString();
  return search ? `${routePaths.suppliers}?${search}` : routePaths.suppliers;
};

export const hasRoutePermission = (route: RouteDefinition, permissions: readonly string[]) => permissions.includes(route.permission);

const isNavigationRoute = (route: RouteDefinition): route is NavigationRoute => route.navigation !== undefined;

export const visibleNavigationRoutes = (permissions: readonly string[], surface: NavigationSurface): readonly NavigationRoute[] => (
  routeDefinitions
    .filter(isNavigationRoute)
    .filter((route) => route.navigation.surfaces.includes(surface))
    .filter((route) => hasRoutePermission(route, permissions))
);

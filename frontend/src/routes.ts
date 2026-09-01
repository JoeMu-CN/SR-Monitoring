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
export type RouteId = 'overview' | 'risks' | 'riskDetail' | 'assistant' | 'suppliers' | 'sources' | 'sourceSignals' | 'rules' | 'userSettings';
export type NavigationSurface = 'desktop' | 'mobile';
export type NavigationSection = 'main' | 'system';
export type NavigationIcon = 'overview' | 'risks' | 'assistant' | 'suppliers' | 'sources' | 'rules' | 'userSettings';

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
  riskDetail: '/risks/:alertId',
  assistant: '/assistant',
  suppliers: '/suppliers',
  sources: '/sources',
  sourceSignals: '/sources/:sourceId/signals',
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
    navigation: {surfaces: ['desktop', 'mobile'], section: 'main', desktopLabel: '当前风险监控', mobileLabel: '风险', icon: 'risks', end: false},
  },
  {id: 'riskDetail', path: routePaths.riskDetail, permission: routePermissions.riskView},
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
    navigation: {surfaces: ['desktop', 'mobile'], section: 'system', desktopLabel: '数据源列表', mobileLabel: '数据', icon: 'sources', end: true},
  },
  {id: 'sourceSignals', path: routePaths.sourceSignals, permission: routePermissions.sourceStatusView},
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

export type SourceSignalScope = 'valid' | 'all';

export const sourceSignalsPath = (sourceId: string, scope: SourceSignalScope, page = 1) => (
  `${routePaths.sources}/${sourceId}/signals?scope=${scope}&page=${page}`
);

export const hasRoutePermission = (route: RouteDefinition, permissions: readonly string[]) => permissions.includes(route.permission);

const isNavigationRoute = (route: RouteDefinition): route is NavigationRoute => route.navigation !== undefined;

export const visibleNavigationRoutes = (permissions: readonly string[], surface: NavigationSurface): readonly NavigationRoute[] => (
  routeDefinitions
    .filter(isNavigationRoute)
    .filter((route) => route.navigation.surfaces.includes(surface))
    .filter((route) => hasRoutePermission(route, permissions))
);

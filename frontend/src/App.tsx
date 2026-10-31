import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {AnimatePresence, motion} from 'motion/react';
import {useLocation, useNavigate} from 'react-router-dom';
import {
  api,
  ApiError,
  mapDataSource,
  mapDimension,
  mapRiskAlert,
  mapSupplier,
  mapSupplierListItem,
  updateDimensionConfig,
  type AuthMeResponse,
  type AuthUser,
  type AgentStatusRead,
  type RiskAlertSummary,
  type RiskEventClusterListResponse,
  type RiskEventClusterSummary,
  type RiskEventState,
  type SupplierDeletionImpactRead,
  type SupplierRead,
  type SystemHealth,
} from './api';
import type {DataSource, MonitoringDimension, RiskItem, Supplier} from './types';
import {buildEditPayload, normalizeSettlementCurrencyInput, type SupplierFormSubmission} from './supplierEdit';
import {AppRoutes, type RouteViews} from './AppRoutes';
import {RiskRouteView} from './RiskRouteView';
import {ExportReportModal} from './components/ExportReportModal';
import {MobileNav} from './components/MobileNav';
import {NewSupplierModal} from './components/NewSupplierModal';
import {SettingsModal} from './components/SettingsModal';
import {Sidebar, type SidebarStage} from './components/Sidebar';
import {SystemSplashScreen} from './components/SystemSplashScreen';
import {UserMenu} from './components/UserMenu';
import {LoginView} from './components/LoginView';
import {DataSourcesView, type RefreshSourcesIntent} from './components/DataSourcesView';
import {SourceSignalsView} from './components/SourceSignalsView';
import {SupplierImportModal} from './components/SupplierImportModal';
import {OverviewView} from './components/OverviewView';
import {RiskEventsView, RiskViewModeTabs, riskViewPanelId, riskViewTabId, type RiskViewMode} from './components/RiskEventsView';
import {EventDetailView} from './components/EventDetailView';
import {PolicyIntelligenceView} from './components/PolicyIntelligenceView';
import {RiskAssistantView} from './components/RiskAssistantView';
import {RuleEngineView} from './components/RuleEngineView';
import {SCHEDULER_LOG_PERMISSION, SchedulerLiveView} from './components/SchedulerLiveView';
import {SuppliersView} from './components/SuppliersView';
import {UsersManagementView} from './components/UsersManagementView';
import {eventDetailPath, riskDetailPath, routePaths, routePermissions} from './routes';
import {monitoringHealthRefreshMsForPath, useMonitoringHealth} from './useMonitoringHealth';

const SIDEBAR_STAGE_STORAGE_KEY = 'sr-sidebar-collapsed';

const readSidebarStage = (): SidebarStage => {
  try {
    const stored = window.localStorage.getItem(SIDEBAR_STAGE_STORAGE_KEY);
    if (stored === '2') return 2;
    if (stored === '1' || stored === 'true') return 1;
    return 0;
  } catch {
    return 0;
  }
};

export function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [auth, setAuth] = useState<AuthMeResponse | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [riskItems, setRiskItems] = useState<RiskItem[]>([]);
  // 当前提醒全量计数：与 riskItems 的单页（limit=100）列表口径分离，供风险监控页概览卡展示。
  // null 表示汇总尚不可用，视图据此回落已加载页计数。
  const [riskAlertSummary, setRiskAlertSummary] = useState<RiskAlertSummary | null>(null);
  // 风险事件视图的口径与状态：默认「按事件」+ 当前有效；已失效数据切换时懒加载。
  const [riskViewMode, setRiskViewMode] = useState<RiskViewMode>('events');
  const [riskState, setRiskState] = useState<RiskEventState>('current');
  // 事件簇列表按状态缓存：切回已加载过的状态不再重复请求。
  const [eventClusterPages, setEventClusterPages] = useState<Partial<Record<RiskEventState, RiskEventClusterListResponse>>>({});
  // 事件簇汇总固定当前口径，供统计条与总览主 KPI 使用；null 表示尚不可用。
  const [eventClusterSummary, setEventClusterSummary] = useState<RiskEventClusterSummary | null>(null);
  const [eventClustersLoading, setEventClustersLoading] = useState(false);
  const [eventClustersError, setEventClustersError] = useState<string | null>(null);
  const [eventClustersRetryToken, setEventClustersRetryToken] = useState(0);
  // 供应商视图的已失效数据：独立懒加载，避免把历史行混进当前主数据。
  const [expiredRiskItems, setExpiredRiskItems] = useState<RiskItem[]>([]);
  const [expiredRiskTotal, setExpiredRiskTotal] = useState<number | null>(null);
  const [expiredRiskLoading, setExpiredRiskLoading] = useState(false);
  const [expiredRiskError, setExpiredRiskError] = useState<string | null>(null);
  const [expiredRiskLoaded, setExpiredRiskLoaded] = useState(false);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [dimensions, setDimensions] = useState<MonitoringDimension[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentStatusRead | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingAssistantQuery, setPendingAssistantQuery] = useState<string | null>(null);
  const [isExportModalOpen, setIsExportModalOpen] = useState(false);
  const [reportRisk, setReportRisk] = useState<RiskItem | null>(null);
  const [supplierRefreshToken, setSupplierRefreshToken] = useState(0);
  // 监控健康诊断的刷新版本号：单源刷新/核查成功时递增，useMonitoringHealth 依赖变化后立即重建请求周期，
  // 不必等待 60 秒轮询到点；列表刷新等入口不递增，保持原有诊断节奏。
  const [monitoringHealthRefreshVersion, setMonitoringHealthRefreshVersion] = useState(0);
  const [isNewSupplierModalOpen, setIsNewSupplierModalOpen] = useState(false);
  const [isSupplierImportModalOpen, setIsSupplierImportModalOpen] = useState(false);
  const [editingSupplier, setEditingSupplier] = useState<Supplier | null>(null);
  const [editingDetail, setEditingDetail] = useState<SupplierRead | null>(null);
  const [deletionImpact, setDeletionImpact] = useState<SupplierDeletionImpactRead | null>(null);
  const [deletionImpactError, setDeletionImpactError] = useState<string | null>(null);
  const editSupplierAbortRef = useRef<AbortController | null>(null);
  const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);
  const [sidebarStage, setSidebarStage] = useState<SidebarStage>(readSidebarStage);
  const permissions = auth?.permissions ?? [];
  const canManageSources = permissions.includes(routePermissions.sourceManage);
  const canManageSuppliers = permissions.includes(routePermissions.supplierManage);
  const canManageRules = permissions.includes(routePermissions.ruleManage);
  const canUseRiskAssistant = permissions.includes(routePermissions.riskQueryUse);
  // /overview 自管 dashboardSummary 请求，独立于 loadData 的 Promise.all 与全局 loading/splash：
  // 其他核心资源仍 pending 或失败时，总览页也要能挂载并显示自己的汇总。
  const onOverviewRoute = location.pathname === routePaths.overview;

  useEffect(() => {
    const savedTheme = localStorage.getItem('sr-theme') ?? 'light';
    const useDark = savedTheme === 'dark' || (savedTheme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.classList.toggle('dark', useDark);
    document.documentElement.classList.toggle('light', !useDark);
    document.documentElement.classList.toggle('reduce-motion', localStorage.getItem('sr-reduce-motion') === 'true');
  }, []);

  useEffect(() => {
    api.auth.me()
      .then(setAuth)
      .catch((caught) => {
        if (!(caught instanceof ApiError && caught.status === 401)) {
          setAuthError(caught instanceof Error ? caught.message : '登录状态检查失败');
        }
      })
      .finally(() => setAuthLoading(false));
  }, []);

  const loadData = useCallback(async () => {
    setError(null);
    // 当前提醒全量汇总与核心主数据同批发起，但独立降级：失败只让风险监控页概览卡
    // 回落已加载页计数，不得把整页降级为错误横幅，也不得清空上一次可用口径。
    const summaryPromise = api.alertSummary().catch(() => null);
    try {
      // 核心主数据聚合：这些才是页面主体依赖的真正数据，任一失败都属于数据加载失败，统一进入全局错误横幅。
      const [alertsResponse, suppliersResponse, sourcesResponse, runsResponse, dimensionResponse, healthResponse] = await Promise.all([
        api.alerts(), api.suppliers(), canManageSources ? api.sourcesAdmin() : api.sources(), api.collectionRuns(), api.dimensions(), api.health(),
      ]);
      setRiskItems(alertsResponse.items.map(mapRiskAlert));
      setSuppliers(suppliersResponse.items.map(mapSupplierListItem));
      setDataSources(sourcesResponse.map((source) => mapDataSource(source, runsResponse.items)));
      setDimensions(dimensionResponse.map(mapDimension));
      setHealth(healthResponse);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        setAuth(null);
        setAuthError('登录已失效，请重新登录');
        return;
      }
      setError(caught instanceof Error ? caught.message : '页面数据加载失败');
      return;
    } finally {
      setLoading(false);
    }

    const summaryResponse = await summaryPromise;
    if (summaryResponse) setRiskAlertSummary(summaryResponse);

    // agentStatus 是独立、非核心的请求：/api/v1/agent/status 需要 risk_query_use 权限，
    // viewer 等只读角色按后端设计会被 403 拒绝，属预期而非错误。此接口失败不得阻塞上方
    // 主数据渲染，也不得触发全局「权限不足」横幅——显式复位为 null，由依赖方（风险查询助手等）
    // 按 null 优雅降级或隐藏。核心主数据已失败时（catch 已 return）不再发起本请求。
    try {
      setAgentStatus(await api.agentStatus());
    } catch {
      setAgentStatus(null);
    }
  }, [canManageSources]);

  useEffect(() => { if (auth) void loadData(); }, [auth, loadData]);

  // 事件簇汇总独立于 loadData：失败只让统计条显示占位，不得把整页降级为错误横幅。
  useEffect(() => {
    if (!auth) return;
    let cancelled = false;
    api.riskEventClusterSummary()
      .then((summary) => { if (!cancelled) setEventClusterSummary(summary); })
      .catch(() => { if (!cancelled) setEventClusterSummary(null); });
    return () => { cancelled = true; };
  }, [auth]);

  // 事件簇列表：当前口径首屏加载，已失效口径首次切换时懒加载；加载成功的口径走缓存。
  useEffect(() => {
    if (!auth) return;
    if (eventClusterPages[riskState]) return;
    let cancelled = false;
    setEventClustersLoading(true);
    setEventClustersError(null);
    api.riskEventClusters(riskState)
      .then((response) => {
        if (cancelled) return;
        setEventClusterPages((current) => ({...current, [riskState]: response}));
      })
      .catch((caught) => {
        if (cancelled) return;
        if (caught instanceof ApiError && caught.status === 401) {
          setAuth(null);
          setAuthError('登录已失效，请重新登录');
          return;
        }
        setEventClustersError(caught instanceof Error ? caught.message : '风险事件加载失败');
      })
      .finally(() => { if (!cancelled) setEventClustersLoading(false); });
    return () => { cancelled = true; };
  }, [auth, riskState, eventClusterPages, eventClustersRetryToken]);

  // 重试：清掉当前口径缓存后重新拉取；汇总缺失时一并重试，避免统计条长期停在占位。
  const reloadEventClusters = useCallback(() => {
    setEventClusterPages((current) => {
      const next = {...current};
      delete next[riskState];
      return next;
    });
    setEventClustersRetryToken((token) => token + 1);
    api.riskEventClusterSummary()
      .then(setEventClusterSummary)
      .catch(() => { /* 统计条继续显示占位，不阻塞列表重试 */ });
  }, [riskState]);

  // 供应商视图「已失效」懒加载：只在用户首次切到该口径时请求一次，失败保留重试入口。
  const loadExpiredRisks = useCallback(() => {
    if (!auth || expiredRiskLoading || expiredRiskLoaded) return;
    setExpiredRiskLoading(true);
    setExpiredRiskError(null);
    api.alertHistory()
      .then((response) => {
        setExpiredRiskItems(response.items.map(mapRiskAlert));
        setExpiredRiskTotal(response.total);
        setExpiredRiskLoaded(true);
      })
      .catch((caught) => {
        if (caught instanceof ApiError && caught.status === 401) {
          setAuth(null);
          setAuthError('登录已失效，请重新登录');
          return;
        }
        setExpiredRiskError(caught instanceof Error ? caught.message : '已失效事件加载失败');
      })
      .finally(() => setExpiredRiskLoading(false));
  }, [auth, expiredRiskLoaded, expiredRiskLoading]);

  const handleLogin = async (username: string, password: string) => {
    setAuthError(null);
    try {
      await api.auth.login(username, password);
      setAuth(await api.auth.me());
    } catch (caught) {
      setAuthError(caught instanceof Error ? caught.message : '登录失败');
      throw caught;
    }
  };

  const handleLogout = async () => {
    try { await api.auth.logout(); } catch { /* 会话已失效时仍清理前端状态 */ }
    setAuth(null);
    setAuthError(null);
    setRiskItems([]);
    setRiskAlertSummary(null);
    setEventClusterPages({});
    setEventClusterSummary(null);
    setEventClustersError(null);
    setExpiredRiskItems([]);
    setExpiredRiskTotal(null);
    setExpiredRiskError(null);
    setExpiredRiskLoaded(false);
    setRiskViewMode('events');
    setRiskState('current');
    setSuppliers([]);
    setDataSources([]);
    setDimensions([]);
  };

  const advanceSidebarStage = () => {
    setSidebarStage((current) => {
      const next: SidebarStage = current === 0 ? 1 : current === 1 ? 2 : 0;
      try {
        window.localStorage.setItem(SIDEBAR_STAGE_STORAGE_KEY, String(next));
      } catch {
        // 隐私模式/存储被禁用时只影响下次打开，不阻塞本次折叠。
      }
      return next;
    });
  };

  const handleCurrentUserUpdated = (updatedUser: AuthUser) => {
    setAuth((current) => current ? {...current, user: updatedUser} : current);
  };

  const p1RiskCount = useMemo(
    () => riskItems.filter((item) => item.level === 'P1').length,
    [riskItems],
  );

  // 助手清单内核查会真实创建/更新正式告警（后端同一事务内已提交）：回答落地后重取当前提醒，
  // 使风险监控页与 P1 计数立即反映核查结果，不再依赖浏览器刷新。失败由调用方降级提示。
  const refreshRiskAlerts = useCallback(async () => {
    const alertsResponse = await api.alerts();
    setRiskItems(alertsResponse.items.map(mapRiskAlert));
    // 全量汇总与列表同批刷新，但失败保留上一次可用口径，绝不把概览卡清空或回落成本页条数。
    try {
      setRiskAlertSummary(await api.alertSummary());
    } catch {
      /* 保留既有口径，视图继续按全量计数展示 */
    }
  }, []);

  const handleAskAssistant = (query: string) => {
    if (!canUseRiskAssistant) {
      setError('当前账号没有使用风险查询助手的权限');
      return;
    }
    setPendingAssistantQuery(query);
    navigate(routePaths.assistant);
  };

  const handleSelectSupplier = (supplier: Supplier) => {
    const matchingRisk = riskItems.find(
      (risk) => risk.companyName === supplier.legalName || risk.vendorId === supplier.id,
    );
    if (matchingRisk) navigate(riskDetailPath(matchingRisk.id));
    else handleAskAssistant(`查询供应商【${supplier.legalName}】当前是否存在有效风险，并列出生产地点与供应产品。`);
  };

  // 供应商清单已改为服务端分页，任何写操作后统一重查，避免前端拼接出与当前页码不符的列表。
  const refreshSuppliers = useCallback(async () => {
    const response = await api.suppliers();
    setSuppliers(response.items.map(mapSupplierListItem));
    setSupplierRefreshToken((value) => value + 1);
  }, []);

  const handleToggleSupplierStatus = async (supplier: Supplier) => {
    try {
      await api.toggleSupplier(Number(supplier.id), supplier.monitoringStatus === 'paused');
      await refreshSuppliers();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '供应商监控状态更新失败');
    }
  };

  const handleAddSupplier = async (supplier: Supplier) => {
    const countryCode = /^[A-Za-z]{2}$/.test(supplier.countryRegion ?? '')
      ? String(supplier.countryRegion).toUpperCase()
      : 'CN';
    // 结算币种：表单留空 → null（显式不设置）；非空已由表单校验为三位大写
    const settlementCurrency = normalizeSettlementCurrencyInput((supplier as SupplierFormSubmission).settlementCurrency);
    try {
      await api.createSupplier({
        supplier_code: supplier.code,
        legal_name: supplier.legalName,
        country_code: countryCode,
        registry_no: supplier.registrationNo || null,
        registration_address: supplier.registrationAddress?.trim() || null,
        industry: supplier.category || null,
        settlement_currency: settlementCurrency,
        raw_materials: [],
        enabled: true,
        aliases: [],
        sites: supplier.productionLocation ? [{
          site_name: supplier.productionLocation,
          country_code: countryCode,
          region: supplier.productionRegion?.trim() || null,
          city: supplier.productionCity?.trim() || null,
          district: supplier.productionDistrict?.trim() || null,
          address: supplier.productionAddress?.trim() || supplier.productionLocation,
          latitude: null,
          longitude: null,
        }] : [],
        products: supplier.suppliedProduct ? [{name: supplier.suppliedProduct, keywords: []}] : [],
      });
      await refreshSuppliers();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '新增供应商失败');
      throw caught;
    }
  };

  const handleEditSupplier = async (supplier: Supplier) => {
    // 取消前一次尚未完成的详情请求，防止竞态覆盖
    editSupplierAbortRef.current?.abort();
    const controller = new AbortController();
    editSupplierAbortRef.current = controller;
    setDeletionImpact(null);
    setDeletionImpactError(null);
    try {
      const detail = await api.getSupplier(Number(supplier.id), controller.signal);
      // 仅当未被取消时才更新状态
      if (!controller.signal.aborted) {
        setEditingDetail(detail);
        setEditingSupplier(mapSupplier(detail));
        setIsNewSupplierModalOpen(true);
      }
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return;
      if (controller.signal.aborted) return;
      setError(caught instanceof Error ? caught.message : '获取供应商详情失败');
      return;
    }
    // 删除影响仅编辑态且有权管理时拉取；失败不阻塞编辑，弹窗内提示服务端仍会检查。
    if (canManageSuppliers && !controller.signal.aborted) {
      try {
        setDeletionImpact(await api.supplierDeletionImpact(Number(supplier.id)));
      } catch (caught) {
        setDeletionImpactError(caught instanceof Error ? caught.message : '删除影响获取失败');
      }
    }
  };

  const openNewSupplierModal = () => {
    setEditingSupplier(null);
    setIsNewSupplierModalOpen(true);
  };

  // NewSupplierModal 在 create/edit 模式共用的 onSave：根据当前 modal 模式分发到 create / update。
  const handleSaveSupplier = async (supplier: Supplier) => {
    if (editingSupplier && editingDetail) {
      // edit 分支：基于完整详情深拷贝构造无损 payload，保留所有未编辑字段
      const countryCode = /^[A-Za-z]{2}$/.test(supplier.countryRegion ?? '')
        ? String(supplier.countryRegion).toUpperCase()
        : 'CN';
      // 结算币种：表单值即用户意图，留空显式传 null（清空），非空已由表单校验为三位大写
      const settlementCurrency = normalizeSettlementCurrencyInput((supplier as SupplierFormSubmission).settlementCurrency);
      const payload = buildEditPayload(editingDetail, {
        legal_name: supplier.legalName,
        country_code: countryCode,
        registry_no: supplier.registrationNo || null,
        registration_address: supplier.registrationAddress?.trim() || null,
        industry: supplier.category || null,
        settlement_currency: settlementCurrency,
        // 地点仅回传表单中实际可编辑的四个字段；site_name/国家/坐标不由供应商字段推断：
        // 未编辑字段保留详情原值（不把供应商国家当生产地点国家，不静默清空坐标与地点名称）。
        site_overrides: {
          region: supplier.productionRegion?.trim() || null,
          city: supplier.productionCity?.trim() || null,
          district: supplier.productionDistrict?.trim() || null,
          address: supplier.productionAddress?.trim() || undefined,
        },
        product_overrides: {
          name: supplier.suppliedProduct,
        },
      });
      try {
        await api.updateSupplier(Number(editingDetail.id), payload);
        await refreshSuppliers();
      } catch (caught) {
        // 409 并发冲突：保留用户输入，但不自动重拉详情刷新 expected_updated_at——
        // 把新令牌配给基于旧详情构造的输入，会把过期内容静默覆盖到最新版本上。
        // 明确重载路径：关闭弹窗后重新打开该供应商，重载时完整详情与表单一次性同步。
        if (caught instanceof ApiError && caught.status === 409) {
          throw new Error('供应商资料已被其他用户修改。为避免用过期内容覆盖新版本，未自动刷新并发令牌；请关闭后重新打开该供应商以载入最新数据（您的输入已保留）。');
        }
        setError(caught instanceof Error ? caught.message : '供应商修改失败');
        throw caught;
      }
    } else {
      await handleAddSupplier(supplier);
    }
  };

  const handleDeleteSupplier = async (supplierId: string) => {
    try {
      await api.deleteSupplier(Number(supplierId));
      await refreshSuppliers();
      setEditingSupplier(null);
      setEditingDetail(null);
      setIsNewSupplierModalOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '供应商删除失败');
      throw caught;
    }
  };

  const closeSupplierModal = () => {
    editSupplierAbortRef.current?.abort();
    setIsNewSupplierModalOpen(false);
    setEditingSupplier(null);
    setEditingDetail(null);
    setDeletionImpact(null);
    setDeletionImpactError(null);
  };

  const handleToggleDimension = async (dimensionId: string) => {
    const dimension = dimensions.find((item) => item.id === dimensionId);
    if (!dimension) return;
    try {
      const updated = await api.toggleDimension(dimensionId, !dimension.enabled);
      setDimensions((current) => current.map((item) => item.id === dimensionId ? mapDimension(updated) : item));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '监控维度启停失败');
    }
  };

  const handleUpdateDimension = async (updatedDimension: MonitoringDimension) => {
    const original = dimensions.find((item) => item.id === updatedDimension.id);
    if (!original) return;
    try {
      const updated = await api.updateDimension(updatedDimension.id, updateDimensionConfig(original, updatedDimension));
      setDimensions((current) => current.map((item) => item.id === updatedDimension.id ? mapDimension(updated) : item));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '规则配置保存失败');
      throw caught;
    }
  };

  const refreshSources = async (intent?: RefreshSourcesIntent) => {
    // 单源刷新成功意图：先递增诊断刷新版本号，让 useMonitoringHealth 立即重建请求周期；
    // 健康请求在 hook effect 内发起，不等待下方 sources/collection-runs 完成。
    // 列表请求失败时版本号已递增：诊断反映的是采集已成功的事实，不应被列表重载失败回退。
    if (intent?.refreshMonitoringHealth) setMonitoringHealthRefreshVersion((version) => version + 1);
    const [sourcesResponse, runsResponse] = await Promise.all([canManageSources ? api.sourcesAdmin() : api.sources(), api.collectionRuns()]);
    setDataSources(sourcesResponse.map((source) => mapDataSource(source, runsResponse.items)));
  };

  const handleUpdateSource = async (id: string, payload: Parameters<typeof api.updateSource>[1]) => {
    await api.updateSource(Number(id), payload);
    await refreshSources();
  };

  const selectRisk = (risk: RiskItem) => navigate(riskDetailPath(risk.id));
  const handleDetailRequestError = useCallback((caught: ApiError) => {
    if (caught.status === 401) {
      setAuth(null);
      setAuthError('登录已失效，请重新登录');
      return;
    }
    if (caught.status === 403) setError(caught.message);
  }, []);
  // 任务8 只读诊断：按 source_status_view 权限请求，仅在展示它的页面（总览/信息源/调度器实况）轮询。
  // 调度器实况页经 monitoringHealthRefreshMsForPath 把同一 hook 的周期缩短为 5 秒（仍只有一个计时器）；
  // 403 由 hook 内部隐藏，403 不进入全局错误门；401 经 handleDetailRequestError 统一登出。
  const onSchedulerRoute = location.pathname === routePaths.scheduler;
  const canViewSourceStatus = permissions.includes(routePermissions.sourceStatusView);
  const monitoringHealth = useMonitoringHealth({
    enabled: canViewSourceStatus,
    active: onOverviewRoute || location.pathname === routePaths.sources || onSchedulerRoute,
    refreshVersion: monitoringHealthRefreshVersion,
    refreshIntervalMs: monitoringHealthRefreshMsForPath(location.pathname),
    onRequestError: handleDetailRequestError,
  });
  const riskRouteView = <RiskRouteView riskItems={riskItems} summary={riskAlertSummary} expiredItems={expiredRiskItems} expiredTotal={expiredRiskTotal} expiredLoading={expiredRiskLoading} expiredError={expiredRiskError} onLoadExpired={loadExpiredRisks} onAskAssistant={handleAskAssistant} onCloseDetail={() => navigate(routePaths.risks)} onExportReport={(risk) => { setReportRisk(risk); setIsExportModalOpen(true); navigate(routePaths.risks); }} onSelectRisk={selectRisk} onRequestError={handleDetailRequestError} />;
  const eventClusterPage = eventClusterPages[riskState] ?? null;
  const routeViews: RouteViews = {
    overview: <OverviewView onSelectRisk={selectRisk} onViewAllRisks={() => navigate(routePaths.risks)} onRequestError={handleDetailRequestError} monitoringHealth={monitoringHealth} eventSummary={eventClusterSummary} />,
    // /risks 默认「事件」视图；「供应商」视图复用现有供应商维度列表（含 /risks/:alertId 详情）。
    // 标题与页签在 App 层只渲染一份、位置固定：切换子视图只替换下方面板，导航不会跳动。
    risks: (
      <div className="space-y-3 pb-20 sm:space-y-5 lg:pb-8">
        <div>
          <h1 className="text-xl font-black tracking-tight text-slate-900 dark:text-white lg:text-2xl">风险事件</h1>
        </div>
        {/* 与情报聚合页同构：一份外层卡片同时包住页签与面板，避免页签单独成卡造成割裂。 */}
        <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
          <RiskViewModeTabs mode={riskViewMode} onModeChange={setRiskViewMode} />
          <div className="pt-3">
            {riskViewMode === 'events' ? (
              <div role="tabpanel" id={riskViewPanelId('events')} aria-labelledby={riskViewTabId('events')}>
                <RiskEventsView
                  state={riskState}
                  onStateChange={setRiskState}
                  clusters={eventClusterPage?.items ?? []}
                  total={eventClusterPage?.total ?? 0}
                  summary={eventClusterSummary}
                  loading={eventClustersLoading}
                  error={eventClustersError}
                  onRetry={reloadEventClusters}
                  onSelectEvent={(eventId) => navigate(eventDetailPath(eventId))}
                />
              </div>
            ) : (
              <div role="tabpanel" id={riskViewPanelId('suppliers')} aria-labelledby={riskViewTabId('suppliers')}>
                {riskRouteView}
              </div>
            )}
          </div>
        </section>
      </div>
    ),
    riskDetail: riskRouteView,
    // 事件详情：只经风险事件视图进入，返回回到 /risks。
    eventDetail: (
      <EventDetailView
        onBack={() => navigate(routePaths.risks)}
        onSelectAlert={(alertId) => navigate(riskDetailPath(alertId))}
        onRequestError={handleDetailRequestError}
      />
    ),

    intelligence: <PolicyIntelligenceView />,
    assistant: <RiskAssistantView riskItems={riskItems} suppliers={suppliers} agentStatus={agentStatus} onSelectRisk={selectRisk} onSelectSupplier={handleSelectSupplier} pendingQuery={pendingAssistantQuery} onClearPendingQuery={() => setPendingAssistantQuery(null)} onAlertsChanged={refreshRiskAlerts} />,
    suppliers: <SuppliersView refreshToken={supplierRefreshToken} onOpenImportModal={() => setIsSupplierImportModalOpen(true)} onOpenNewSupplierModal={openNewSupplierModal} onEditSupplier={handleEditSupplier} onToggleStatus={(supplier) => void handleToggleSupplierStatus(supplier)} onAskAssistant={handleAskAssistant} onRequestError={handleDetailRequestError} role={canManageSuppliers ? 'admin' : 'viewer'} />,
    sources: <DataSourcesView dataSources={dataSources} role={canManageSources ? 'admin' : 'viewer'} onUpdateSource={handleUpdateSource} onRefreshSources={refreshSources} monitoringHealth={monitoringHealth} />,
    // 独立调度器实况页：只消费同一 hook 的 monitoringHealth 快照；原始日志入口单独鉴权。
    scheduler: (
      <SchedulerLiveView
        snapshot={monitoringHealth}
        canViewLogs={permissions.includes(SCHEDULER_LOG_PERMISSION)}
        onRequestError={handleDetailRequestError}
      />
    ),
    sourceSignals: <SourceSignalsView onRequestError={handleDetailRequestError} />,
    rules: <RuleEngineView dimensions={dimensions} onToggleDimension={handleToggleDimension} onUpdateDimension={handleUpdateDimension} role={canManageRules ? 'admin' : 'viewer'} />,
    userSettings: auth ? <UsersManagementView currentUser={auth.user} onRequestError={handleDetailRequestError} onCurrentUserUpdated={handleCurrentUserUpdated} /> : null,
  };

  // 会话校验与数据加载共用同一个简单开屏；/overview 自管汇总请求，不需要该覆盖层。
  const showSimpleSplash = auth !== null && loading && !onOverviewRoute;

  if (authLoading) {
    return <SystemSplashScreen />;
  }
  if (!auth) return <LoginView onSubmit={handleLogin} error={authError} />;

  return (
    <div className="flex min-h-screen flex-col bg-slate-100/90 font-sans text-[#101d28] antialiased dark:bg-[#0b131e] dark:text-slate-100">
      <Sidebar
        permissions={permissions}
        onOpenSettingsModal={() => setIsSettingsModalOpen(true)}
        p1RiskCount={p1RiskCount}
        user={auth.user}
        onLogout={() => void handleLogout()}
        stage={sidebarStage}
        onAdvanceStage={advanceSidebarStage}
      />
      <div className="fixed right-3 top-3 z-40 lg:hidden">
        <UserMenu user={auth.user} onLogout={() => void handleLogout()} compact menuPlacement="down" />
      </div>

      <div className={`flex min-w-0 flex-1 flex-col transition-all ${
        sidebarStage === 0 ? 'lg:pl-[240px]' : sidebarStage === 1 ? 'lg:pl-[72px]' : 'lg:pl-0'
      }`}>
        <main className={`mx-auto w-full flex-1 overflow-x-clip p-4 pb-28 sm:p-6 sm:pb-24 lg:p-6 ${sidebarStage === 2 ? 'max-w-none' : 'max-w-[1440px]'}`}>
          {error && (
            <div className="mb-4 bg-[#ffdad6] border border-[#ba1a1a] text-[#93000a] rounded-xl px-4 py-3 flex items-center justify-between gap-3 text-[13px]">
              <span>{error}</span>
              <button className="font-bold hover:underline" onClick={() => void loadData()}>重新加载</button>
            </div>
          )}
          {loading && !onOverviewRoute ? (
            <div className="min-h-[50vh]" aria-hidden="true" />
          ) : (
            <AnimatePresence mode="wait">
              <motion.div
                key={location.pathname}
                initial={{opacity: 0, y: 12}}
                animate={{opacity: 1, y: 0}}
                exit={{opacity: 0, y: -12}}
                transition={{duration: 0.2, ease: 'easeOut'}}
                className="h-full w-full"
                data-testid="route-content"
              >
                <AppRoutes permissions={permissions} views={routeViews} location={location} />
              </motion.div>
            </AnimatePresence>
          )}
        </main>
      </div>

      <MobileNav permissions={permissions} p1RiskCount={p1RiskCount} />
      <ExportReportModal isOpen={isExportModalOpen} onClose={() => setIsExportModalOpen(false)} selectedRisk={reportRisk} riskItems={riskItems} />
      <NewSupplierModal isOpen={isNewSupplierModalOpen} onClose={closeSupplierModal}
        mode={editingSupplier ? 'edit' : 'create'} initialSupplier={editingSupplier ?? undefined}
        onSave={handleSaveSupplier} onDelete={handleDeleteSupplier}
        updatedAt={editingDetail?.updated_at}
        initialSettlementCurrency={editingDetail ? editingDetail.settlement_currency ?? null : null}
        extraSiteCount={editingDetail ? Math.max(0, editingDetail.sites.length - 1) : 0}
        extraProductCount={editingDetail ? Math.max(0, editingDetail.products.length - 1) : 0}
        deletionImpact={deletionImpact}
        deletionImpactError={deletionImpactError}
      />
      <SupplierImportModal
        isOpen={isSupplierImportModalOpen}
        onClose={() => setIsSupplierImportModalOpen(false)}
        onImported={refreshSuppliers}
        onRequestError={handleDetailRequestError}
      />
      <SettingsModal isOpen={isSettingsModalOpen} onClose={() => setIsSettingsModalOpen(false)} />
      <AnimatePresence>
        {showSimpleSplash && <SystemSplashScreen />}
      </AnimatePresence>
    </div>
  );
}

export default App;

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
  type MonitoringHealthRead,
  type SupplierDeletionImpactRead,
  type SupplierRead,
  type SystemHealth,
} from './api';
import type {DataSource, MonitoringDimension, RiskItem, Supplier} from './types';
import {buildEditPayload} from './supplierEdit';
import {AppRoutes, type RouteViews} from './AppRoutes';
import {RiskRouteView} from './RiskRouteView';
import {ExportReportModal} from './components/ExportReportModal';
import {Header} from './components/Header';
import {MobileNav} from './components/MobileNav';
import {NewSupplierModal} from './components/NewSupplierModal';
import {SettingsModal} from './components/SettingsModal';
import {Sidebar} from './components/Sidebar';
import {SystemSplashScreen, type SelfCheckItem, type SelfCheckState} from './components/SystemSplashScreen';
import {readLastSelfCheckAt, SELF_CHECK_TTL_MS, shouldRunFullSelfCheck, writeLastSelfCheckAt} from './selfCheck';
import {LoginView} from './components/LoginView';
import {DataSourcesView} from './components/DataSourcesView';
import {SourceSignalsView} from './components/SourceSignalsView';
import {SupplierImportModal} from './components/SupplierImportModal';
import {OverviewView} from './components/OverviewView';
import {RiskAssistantView} from './components/RiskAssistantView';
import {RuleEngineView} from './components/RuleEngineView';
import {SuppliersView} from './components/SuppliersView';
import {UsersManagementView} from './components/UsersManagementView';
import {riskDetailPath, routePaths, routePermissions} from './routes';
import {useMonitoringHealth} from './useMonitoringHealth';

// 完整自检的观感与容错预算：既让状态变化可感知，也不让开屏长时间停留。
const SELF_CHECK_MIN_DISPLAY_MS = 3000;
const SELF_CHECK_ITEM_TIMEOUT_MS = 4000;

export function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [auth, setAuth] = useState<AuthMeResponse | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [riskItems, setRiskItems] = useState<RiskItem[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [dimensions, setDimensions] = useState<MonitoringDimension[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentStatusRead | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [loading, setLoading] = useState(true);
  // 开屏模式：full = 登录后或距上次完整自检 ≥30 分钟，展示真实逐项自检；simple = 仅品牌加载动画。
  const [selfCheckMode, setSelfCheckMode] = useState<'full' | 'simple'>('simple');
  const [selfCheckDone, setSelfCheckDone] = useState(true);
  const [selfCheckItems, setSelfCheckItems] = useState<SelfCheckItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pendingAssistantQuery, setPendingAssistantQuery] = useState<string | null>(null);
  const [isExportModalOpen, setIsExportModalOpen] = useState(false);
  const [reportRisk, setReportRisk] = useState<RiskItem | null>(null);
  const [supplierRefreshToken, setSupplierRefreshToken] = useState(0);
  const [isNewSupplierModalOpen, setIsNewSupplierModalOpen] = useState(false);
  const [isSupplierImportModalOpen, setIsSupplierImportModalOpen] = useState(false);
  const [editingSupplier, setEditingSupplier] = useState<Supplier | null>(null);
  const [editingDetail, setEditingDetail] = useState<SupplierRead | null>(null);
  const [deletionImpact, setDeletionImpact] = useState<SupplierDeletionImpactRead | null>(null);
  const [deletionImpactError, setDeletionImpactError] = useState<string | null>(null);
  const editSupplierAbortRef = useRef<AbortController | null>(null);
  const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);
  // 自检只启动一次（含 StrictMode 双跑保护）。
  const selfCheckStartedRef = useRef(false);
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

  // 统一会话建立入口：登录成功或会话恢复后决定开屏模式，并允许本次会话重新启动一次自检。
  const establishAuth = useCallback((response: AuthMeResponse, forceFull: boolean) => {
    selfCheckStartedRef.current = false;
    const due = forceFull || shouldRunFullSelfCheck(Date.now(), readLastSelfCheckAt(), SELF_CHECK_TTL_MS);
    setSelfCheckMode(due ? 'full' : 'simple');
    setSelfCheckDone(!due);
    setAuth(response);
  }, []);

  useEffect(() => {
    api.auth.me()
      .then((response) => establishAuth(response, false))
      .catch((caught) => {
        if (!(caught instanceof ApiError && caught.status === 401)) {
          setAuthError(caught instanceof Error ? caught.message : '登录状态检查失败');
        }
      })
      .finally(() => setAuthLoading(false));
  }, [establishAuth]);

  const loadData = useCallback(async () => {
    setError(null);
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

  // 完整自检：按权限组装真实检查项，调用真实后端接口逐项更新；401 统一退回登录页，
  // 单项失败只影响该项（绝不触发全局错误横幅），最短展示 + 单项超时保证开屏不会长时间卡住。
  const runSelfCheck = useCallback(async (session: AuthMeResponse) => {
    const canViewSourceStatus = session.permissions.includes(routePermissions.sourceStatusView);
    const canUseRiskQuery = session.permissions.includes(routePermissions.riskQueryUse);

    const initialItems: SelfCheckItem[] = [
      {id: 'database', label: '数据库连接', detail: '正在检查…', state: 'pending'},
    ];
    if (canViewSourceStatus) {
      initialItems.push(
        {id: 'scheduler', label: '调度器心跳', detail: '正在检查…', state: 'pending'},
        {id: 'sources', label: '数据源状态', detail: '正在检查…', state: 'pending'},
      );
    }
    if (canUseRiskQuery) {
      initialItems.push({id: 'ai', label: 'AI 引擎', detail: '正在检查…', state: 'pending'});
    }
    setSelfCheckItems(initialItems);

    let sessionExpired = false;
    const updateItem = (id: string, state: SelfCheckState, detail: string) => {
      setSelfCheckItems((current) => current.map((item) => item.id === id ? {...item, state, detail} : item));
    };
    const expireSession = () => {
      sessionExpired = true;
      setAuth(null);
      setAuthError('登录已失效，请重新登录');
    };

    type SelfCheckOutcome = {readonly state: SelfCheckState; readonly detail: string};

    // scheduler 与 sources 共享同一次 monitoring-health 结果，避免重复请求。
    let monitoringPromise: Promise<MonitoringHealthRead> | null = null;
    const loadMonitoringHealth = () => {
      monitoringPromise ??= api.monitoringHealth();
      return monitoringPromise;
    };

    const runItem = (id: string, failureDetail: string, check: () => Promise<SelfCheckOutcome>): Promise<void> =>
      new Promise((resolve) => {
        let finished = false;
        const finish = () => {
          finished = true;
          resolve();
        };
        const timer = window.setTimeout(() => {
          if (finished) return;
          updateItem(id, 'unavailable', '检查超时');
          finish();
        }, SELF_CHECK_ITEM_TIMEOUT_MS);
        void check().then(
          (outcome) => {
            window.clearTimeout(timer);
            if (finished) return;
            updateItem(id, outcome.state, outcome.detail);
            finish();
          },
          (caught: unknown) => {
            window.clearTimeout(timer);
            if (caught instanceof ApiError && caught.status === 401) {
              // 401 无论是否已超时都必须回退登录页，不能让开屏永久停留。
              expireSession();
              if (!finished) finish();
              return;
            }
            if (finished) return;
            updateItem(id, 'error', failureDetail);
            finish();
          },
        );
      });

    const tasks: Array<Promise<void>> = [
      runItem('database', '数据库检查失败', async () => {
        const health = await api.health();
        if (health.status === 'ok' && health.database === 'ok') {
          return {state: 'ok', detail: 'PostgreSQL 连接正常'};
        }
        return {state: 'error', detail: '数据库不可用'};
      }),
    ];

    if (canViewSourceStatus) {
      tasks.push(
        runItem('scheduler', '调度器检查失败', async () => {
          const health = await loadMonitoringHealth();
          const heartbeat = typeof health.scheduler.age_seconds === 'number'
            ? `心跳正常（${health.scheduler.age_seconds} 秒前）`
            : '心跳正常';
          if (health.scheduler.status === 'ok') {
            return health.overall === 'degraded'
              ? {state: 'warn', detail: `${heartbeat}，但部分链路降级`}
              : {state: 'ok', detail: heartbeat};
          }
          if (health.scheduler.status === 'stale') return {state: 'warn', detail: '心跳延迟'};
          return {state: 'warn', detail: '心跳状态未知'};
        }),
        runItem('sources', '数据源检查失败', async () => {
          const health = await loadMonitoringHealth();
          const total = health.sources.length;
          if (total === 0) return {state: 'warn', detail: '暂无启用的数据源'};
          const okCount = health.sources.filter((source) => source.state === 'ok').length;
          return okCount === total
            ? {state: 'ok', detail: `${okCount}/${total} 数据源正常`}
            : {state: 'warn', detail: `${okCount}/${total} 数据源正常`};
        }),
      );
    }

    if (canUseRiskQuery) {
      tasks.push(runItem('ai', 'AI 状态检查失败', async () => {
        const status = await api.agentStatus();
        return status.llm_configured
          ? {state: 'ok', detail: `模型已配置（${status.model}）`}
          : {state: 'warn', detail: '未配置真实模型'};
      }));
    }

    // allSettled 收敛全部检查项；与最短展示时间并行等待，保证状态变化可感知。
    await Promise.all([
      Promise.allSettled(tasks),
      new Promise((resolve) => { window.setTimeout(resolve, SELF_CHECK_MIN_DISPLAY_MS); }),
    ]);

    if (sessionExpired) return;
    writeLastSelfCheckAt(Date.now());
    setSelfCheckDone(true);
  }, []);

  // 仅在完整模式且 auth 就绪时启动一次真实自检；ref 同时防止 React StrictMode 双跑。
  useEffect(() => {
    if (!auth || selfCheckMode !== 'full' || selfCheckDone || selfCheckStartedRef.current) return;
    selfCheckStartedRef.current = true;
    void runSelfCheck(auth);
  }, [auth, selfCheckMode, selfCheckDone, runSelfCheck]);

  const handleLogin = async (username: string, password: string) => {
    setAuthError(null);
    try {
      await api.auth.login(username, password);
      // 登录成功必须走完整自检，不因 30 分钟内的旧时间戳静默跳过。
      establishAuth(await api.auth.me(), true);
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
    setSuppliers([]);
    setDataSources([]);
    setDimensions([]);
  };

  const handleCurrentUserUpdated = (updatedUser: AuthUser) => {
    setAuth((current) => current ? {...current, user: updatedUser} : current);
  };

  const p1RiskCount = useMemo(
    () => riskItems.filter((item) => item.level === 'P1').length,
    [riskItems],
  );

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
    try {
      await api.createSupplier({
        supplier_code: supplier.code,
        legal_name: supplier.legalName,
        country_code: countryCode,
        registry_no: supplier.registrationNo || null,
        registration_address: supplier.registrationAddress?.trim() || null,
        industry: supplier.category || null,
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
      const payload = buildEditPayload(editingDetail, {
        legal_name: supplier.legalName,
        country_code: countryCode,
        registry_no: supplier.registrationNo || null,
        registration_address: supplier.registrationAddress?.trim() || null,
        industry: supplier.category || null,
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

  const refreshSources = async () => {
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
  // 任务8 只读诊断：按 source_status_view 权限请求，仅在展示它的页面（总览/数据源）轮询。
  // 403 由 hook 内部隐藏，403 不进入全局错误门；401 经 handleDetailRequestError 统一登出。
  const canViewSourceStatus = permissions.includes(routePermissions.sourceStatusView);
  const monitoringHealth = useMonitoringHealth({
    enabled: canViewSourceStatus,
    active: onOverviewRoute || location.pathname === routePaths.sources,
    onRequestError: handleDetailRequestError,
  });
  const riskRouteView = <RiskRouteView riskItems={riskItems} onAskAssistant={handleAskAssistant} onCloseDetail={() => navigate(routePaths.risks)} onExportReport={(risk) => { setReportRisk(risk); setIsExportModalOpen(true); navigate(routePaths.risks); }} onSelectRisk={selectRisk} onRequestError={handleDetailRequestError} />;
  const routeViews: RouteViews = {
    overview: <OverviewView onSelectRisk={selectRisk} onViewAllRisks={() => navigate(routePaths.risks)} onRequestError={handleDetailRequestError} monitoringHealth={monitoringHealth} />,
    risks: riskRouteView,
    riskDetail: riskRouteView,
    assistant: <RiskAssistantView riskItems={riskItems} suppliers={suppliers} agentStatus={agentStatus} onSelectRisk={selectRisk} onSelectSupplier={handleSelectSupplier} pendingQuery={pendingAssistantQuery} onClearPendingQuery={() => setPendingAssistantQuery(null)} />,
    suppliers: <SuppliersView refreshToken={supplierRefreshToken} onOpenImportModal={() => setIsSupplierImportModalOpen(true)} onOpenNewSupplierModal={openNewSupplierModal} onEditSupplier={handleEditSupplier} onToggleStatus={(supplier) => void handleToggleSupplierStatus(supplier)} onAskAssistant={handleAskAssistant} onRequestError={handleDetailRequestError} role={canManageSuppliers ? 'admin' : 'viewer'} />,
    sources: <DataSourcesView dataSources={dataSources} role={canManageSources ? 'admin' : 'viewer'} onUpdateSource={handleUpdateSource} onRefreshSources={refreshSources} monitoringHealth={monitoringHealth} />,
    sourceSignals: <SourceSignalsView onRequestError={handleDetailRequestError} />,
    rules: <RuleEngineView dimensions={dimensions} onToggleDimension={handleToggleDimension} onUpdateDimension={handleUpdateDimension} role={canManageRules ? 'admin' : 'viewer'} />,
    userSettings: auth ? <UsersManagementView currentUser={auth.user} onRequestError={handleDetailRequestError} onCurrentUserUpdated={handleCurrentUserUpdated} /> : null,
  };

  const showFullSelfCheck = !authLoading && auth !== null && selfCheckMode === 'full' && !selfCheckDone;
  // 非 /overview 路由的数据加载统一用品牌加载动画呈现，取代旧的纯文字占位。
  const showSimpleSplash = auth !== null && !showFullSelfCheck && loading && !onOverviewRoute;

  if (authLoading) {
    return <SystemSplashScreen variant="simple" />;
  }
  if (!auth) return <LoginView onSubmit={handleLogin} error={authError} />;

  return (
    <div className="flex min-h-screen flex-col bg-slate-100/90 font-sans text-[#101d28] antialiased dark:bg-[#0b131e] dark:text-slate-100">
      <Sidebar
        permissions={permissions}
        onOpenSettingsModal={() => setIsSettingsModalOpen(true)}
        p1RiskCount={p1RiskCount}
      />

      <div className="lg:pl-[240px] flex-1 flex flex-col min-w-0 transition-all">
        <Header
          unreadCount={p1RiskCount}
          riskItems={riskItems}
          user={auth.user}
          onLogout={() => void handleLogout()}
        />

        <main className="mx-auto w-full max-w-[1440px] flex-1 overflow-x-clip p-4 pb-28 sm:p-6 sm:pb-24 lg:p-6">
          {error && (
            <div className="mb-4 bg-[#ffdad6] border border-[#ba1a1a] text-[#93000a] rounded-xl px-4 py-3 flex items-center justify-between gap-3 text-[13px]">
              <span>{error}</span>
              <button className="font-bold hover:underline" onClick={() => void loadData()}>重新加载</button>
            </div>
          )}
          {loading && !onOverviewRoute ? (
            <div className="min-h-[50vh] flex items-center justify-center text-[#424751]" aria-hidden="true">
              <span className="material-symbols-outlined animate-spin">progress_activity</span>
            </div>
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
                <AppRoutes permissions={permissions} views={routeViews} />
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
        {showFullSelfCheck && <SystemSplashScreen variant="full" items={selfCheckItems} />}
      </AnimatePresence>
      <AnimatePresence>
        {showSimpleSplash && <SystemSplashScreen variant="simple" />}
      </AnimatePresence>
    </div>
  );
}

export default App;

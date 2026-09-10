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
import {SystemSplashScreen} from './components/SystemSplashScreen';
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
  const [splashFinished, setSplashFinished] = useState(false);
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

  // 一旦停留在 /overview（不受全局 loading/splash 遮挡），开屏动画视为已完成，
  // 避免核心数据加载结束后切到其他路由再补放一次开屏。
  useEffect(() => { if (onOverviewRoute) setSplashFinished(true); }, [onOverviewRoute]);

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
    setSuppliers([]);
    setDataSources([]);
    setDimensions([]);
  };

  const handleCurrentUserUpdated = (updatedUser: AuthUser) => {
    setAuth((current) => current ? {...current, user: updatedUser} : current);
  };

  const completeSplash = useCallback(() => setSplashFinished(true), []);

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
  const riskRouteView = <RiskRouteView riskItems={riskItems} onAskAssistant={handleAskAssistant} onCloseDetail={() => navigate(routePaths.risks)} onExportReport={(risk) => { setReportRisk(risk); setIsExportModalOpen(true); navigate(routePaths.risks); }} onSelectRisk={selectRisk} onRequestError={handleDetailRequestError} />;
  const routeViews: RouteViews = {
    overview: <OverviewView onSelectRisk={selectRisk} onViewAllRisks={() => navigate(routePaths.risks)} onRequestError={handleDetailRequestError} />,
    risks: riskRouteView,
    riskDetail: riskRouteView,
    assistant: <RiskAssistantView riskItems={riskItems} suppliers={suppliers} agentStatus={agentStatus} onSelectRisk={selectRisk} onSelectSupplier={handleSelectSupplier} pendingQuery={pendingAssistantQuery} onClearPendingQuery={() => setPendingAssistantQuery(null)} />,
    suppliers: <SuppliersView refreshToken={supplierRefreshToken} onOpenImportModal={() => setIsSupplierImportModalOpen(true)} onOpenNewSupplierModal={openNewSupplierModal} onEditSupplier={handleEditSupplier} onToggleStatus={(supplier) => void handleToggleSupplierStatus(supplier)} onAskAssistant={handleAskAssistant} onRequestError={handleDetailRequestError} role={canManageSuppliers ? 'admin' : 'viewer'} />,
    sources: <DataSourcesView dataSources={dataSources} role={canManageSources ? 'admin' : 'viewer'} onUpdateSource={handleUpdateSource} onRefreshSources={refreshSources} />,
    sourceSignals: <SourceSignalsView onRequestError={handleDetailRequestError} />,
    rules: <RuleEngineView dimensions={dimensions} onToggleDimension={handleToggleDimension} onUpdateDimension={handleUpdateDimension} role={canManageRules ? 'admin' : 'viewer'} />,
    userSettings: auth ? <UsersManagementView currentUser={auth.user} onRequestError={handleDetailRequestError} onCurrentUserUpdated={handleCurrentUserUpdated} /> : null,
  };

  if (authLoading) {
    return <div className="flex min-h-screen items-center justify-center bg-slate-100/90 text-sm text-slate-500 dark:bg-[#0b131e]">正在验证登录状态…</div>;
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

        <main className="mx-auto w-full max-w-[1440px] flex-1 overflow-x-hidden p-4 pb-28 sm:p-6 sm:pb-24 lg:p-6">
          {error && (
            <div className="mb-4 bg-[#ffdad6] border border-[#ba1a1a] text-[#93000a] rounded-xl px-4 py-3 flex items-center justify-between gap-3 text-[13px]">
              <span>{error}</span>
              <button className="font-bold hover:underline" onClick={() => void loadData()}>重新加载</button>
            </div>
          )}
          {loading && !onOverviewRoute ? (
            <div className="min-h-[50vh] flex items-center justify-center text-[#424751]">
              <span className="material-symbols-outlined animate-spin mr-2">progress_activity</span>
              正在加载供应链风险数据…
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
        {(!splashFinished || loading) && !onOverviewRoute && <SystemSplashScreen onComplete={completeSplash} />}
      </AnimatePresence>
    </div>
  );
}

export default App;

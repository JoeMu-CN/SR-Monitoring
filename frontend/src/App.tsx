import {useCallback, useEffect, useMemo, useState} from 'react';
import {AnimatePresence, motion} from 'motion/react';
import {useLocation, useNavigate} from 'react-router-dom';
import {
  api,
  ApiError,
  mapDataSource,
  mapDimension,
  mapRiskAlert,
  mapSupplier,
  updateDimensionConfig,
  type AuthMeResponse,
  type AgentStatusRead,
  type SystemHealth,
} from './api';
import type {DataSource, MonitoringDimension, RiskItem, RiskLevel, Supplier} from './types';
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
import {OverviewView} from './components/OverviewView';
import {RiskAssistantView} from './components/RiskAssistantView';
import {RuleEngineView} from './components/RuleEngineView';
import {SuppliersView} from './components/SuppliersView';
import {riskDetailPath, routePaths, routePermissions} from './routes';

const riskRank: Record<RiskLevel, number> = {P1: 4, P2: 3, P3: 2, P4: 1};

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
  const [isNewSupplierModalOpen, setIsNewSupplierModalOpen] = useState(false);
  const [editingSupplier, setEditingSupplier] = useState<Supplier | null>(null);
  const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);
  const permissions = auth?.permissions ?? [];
  const canManageSources = permissions.includes(routePermissions.sourceManage);
  const canManageSuppliers = permissions.includes(routePermissions.supplierManage);
  const canManageRules = permissions.includes(routePermissions.ruleManage);
  const canUseRiskAssistant = permissions.includes(routePermissions.riskQueryUse);

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
      const [alertsResponse, suppliersResponse, sourcesResponse, runsResponse, dimensionResponse, healthResponse, agentResponse] = await Promise.all([
        api.alerts(), api.suppliers(), canManageSources ? api.sourcesAdmin() : api.sources(), api.collectionRuns(), api.dimensions(), api.health(), api.agentStatus(),
      ]);
      const mappedRisks = alertsResponse.items.map(mapRiskAlert);
      const strongestRisk = new Map<number, {level: RiskLevel; score: number}>();
      for (const alert of alertsResponse.items) {
        const current = strongestRisk.get(alert.supplier_id);
        if (!current || riskRank[alert.level] > riskRank[current.level]) {
          strongestRisk.set(alert.supplier_id, {level: alert.level, score: alert.score});
        }
      }
      setRiskItems(mappedRisks);
      setSuppliers(suppliersResponse.items.map((supplier) => {
        const risk = strongestRisk.get(supplier.id);
        return mapSupplier(supplier, risk?.level, risk?.score);
      }));
      setDataSources(sourcesResponse.map((source) => mapDataSource(source, runsResponse.items)));
      setDimensions(dimensionResponse.map(mapDimension));
      setHealth(healthResponse);
      setAgentStatus(agentResponse);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        setAuth(null);
        setAuthError('登录已失效，请重新登录');
        return;
      }
      setError(caught instanceof Error ? caught.message : '页面数据加载失败');
    } finally {
      setLoading(false);
    }
  }, [canManageSources]);

  useEffect(() => { if (auth) void loadData(); }, [auth, loadData]);

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

  const handleToggleSupplierStatus = async (supplierId: string) => {
    const supplier = suppliers.find((item) => item.id === supplierId);
    if (!supplier) return;
    try {
      const updated = await api.toggleSupplier(Number(supplierId), supplier.monitoringStatus === 'paused');
      setSuppliers((current) => current.map((item) => item.id === supplierId
        ? mapSupplier(updated, item.riskLevel, item.riskScore)
        : item));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '供应商监控状态更新失败');
    }
  };

  const handleAddSupplier = async (supplier: Supplier) => {
    const countryCode = /^[A-Za-z]{2}$/.test(supplier.countryRegion ?? '')
      ? String(supplier.countryRegion).toUpperCase()
      : 'CN';
    try {
      const created = await api.createSupplier({
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
      setSuppliers((current) => [mapSupplier(created), ...current]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '新增供应商失败');
      throw caught;
    }
  };

  const handleEditSupplier = (supplier: Supplier) => {
    setEditingSupplier(supplier);
    setIsNewSupplierModalOpen(true);
  };

  // NewSupplierModal 在 create/edit 模式共用的 onSave：根据当前 modal 模式分发到 create / update。
  const handleSaveSupplier = async (supplier: Supplier) => {
    if (editingSupplier) {
      // edit 分支
      const countryCode = /^[A-Za-z]{2}$/.test(supplier.countryRegion ?? '')
        ? String(supplier.countryRegion).toUpperCase()
        : 'CN';
      try {
        const updated = await api.updateSupplier(Number(editingSupplier.id), {
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
        const risk = riskItems.find((item) => item.vendorId === String(updated.id));
        const next = mapSupplier(updated, risk?.level, risk?.overallScore);
        setSuppliers((current) => current.map((item) => item.id === next.id ? next : item));
      } catch (caught) {
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
      setSuppliers((current) => current.filter((item) => item.id !== supplierId));
      setEditingSupplier(null);
      setIsNewSupplierModalOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '供应商删除失败');
      throw caught;
    }
  };

  const closeSupplierModal = () => {
    setIsNewSupplierModalOpen(false);
    setEditingSupplier(null);
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
    overview: <OverviewView riskItems={riskItems} suppliers={suppliers} onSelectRisk={selectRisk} onViewAllRisks={() => navigate(routePaths.risks)} />,
    risks: riskRouteView,
    riskDetail: riskRouteView,
    assistant: <RiskAssistantView riskItems={riskItems} suppliers={suppliers} agentStatus={agentStatus} onSelectRisk={selectRisk} onSelectSupplier={handleSelectSupplier} pendingQuery={pendingAssistantQuery} onClearPendingQuery={() => setPendingAssistantQuery(null)} />,
    suppliers: <SuppliersView suppliers={suppliers} onOpenImportModal={() => setIsNewSupplierModalOpen(true)} onEditSupplier={handleEditSupplier} onToggleStatus={(id) => void handleToggleSupplierStatus(id)} onAskAssistant={handleAskAssistant} role={canManageSuppliers ? 'admin' : 'viewer'} />,
    sources: <DataSourcesView dataSources={dataSources} role={canManageSources ? 'admin' : 'viewer'} onUpdateSource={handleUpdateSource} onRefreshSources={refreshSources} />,
    sourceSignals: <SourceSignalsView onRequestError={handleDetailRequestError} />,
    rules: <RuleEngineView dimensions={dimensions} onToggleDimension={handleToggleDimension} onUpdateDimension={handleUpdateDimension} role={canManageRules ? 'admin' : 'viewer'} />,
    userSettings: <section className="mx-auto flex min-h-[50vh] max-w-xl flex-col items-start justify-center gap-3 rounded-2xl border border-slate-200/80 bg-white/80 p-6 shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60"><h1 className="text-xl font-black tracking-tight text-slate-900 dark:text-white">用户管理</h1><p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">用户管理功能将在后续任务中提供。</p></section>,
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
          {loading ? (
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
        onSave={handleSaveSupplier} onDelete={handleDeleteSupplier} />
      <SettingsModal isOpen={isSettingsModalOpen} onClose={() => setIsSettingsModalOpen(false)} />
      <AnimatePresence>
        {(!splashFinished || loading) && <SystemSplashScreen onComplete={completeSplash} />}
      </AnimatePresence>
    </div>
  );
}

export default App;

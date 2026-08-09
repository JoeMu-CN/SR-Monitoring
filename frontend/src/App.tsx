import {useCallback, useEffect, useMemo, useState} from 'react';
import {AnimatePresence, motion} from 'motion/react';
import {
  api,
  mapDataSource,
  mapDimension,
  mapRiskAlert,
  mapSupplier,
  setApiRole,
  updateDimensionConfig,
  type AgentStatusRead,
  type SystemHealth,
} from './api';
import type {ActiveTab, DataSource, MonitoringDimension, RiskItem, RiskLevel, Supplier} from './types';
import {CurrentRisksView} from './components/CurrentRisksView';
import {DataSourcesView} from './components/DataSourcesView';
import {ExportReportModal} from './components/ExportReportModal';
import {Header} from './components/Header';
import {MobileNav} from './components/MobileNav';
import {NewSupplierModal} from './components/NewSupplierModal';
import {OverviewView} from './components/OverviewView';
import {RiskAssistantView} from './components/RiskAssistantView';
import {RiskDetailModal} from './components/RiskDetailModal';
import {RuleEngineView} from './components/RuleEngineView';
import {SettingsModal} from './components/SettingsModal';
import {Sidebar} from './components/Sidebar';
import {SuppliersView} from './components/SuppliersView';

const riskRank: Record<RiskLevel, number> = {P1: 4, P2: 3, P3: 2, P4: 1};

export function App() {
  const [activeTab, setActiveTab] = useState<ActiveTab>('overview');
  const [riskItems, setRiskItems] = useState<RiskItem[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [dimensions, setDimensions] = useState<MonitoringDimension[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentStatusRead | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedRisk, setSelectedRisk] = useState<RiskItem | null>(null);
  const [pendingAssistantQuery, setPendingAssistantQuery] = useState<string | null>(null);
  const [isExportModalOpen, setIsExportModalOpen] = useState(false);
  const [reportRisk, setReportRisk] = useState<RiskItem | null>(null);
  const [isNewSupplierModalOpen, setIsNewSupplierModalOpen] = useState(false);
  const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);
  const [consoleRole, setConsoleRole] = useState<'viewer' | 'admin'>('viewer');

  useEffect(() => { setApiRole(consoleRole); }, [consoleRole]);

  const loadData = useCallback(async () => {
    setError(null);
    try {
      const [alertsResponse, suppliersResponse, sourcesResponse, runsResponse, dimensionResponse, healthResponse, agentResponse] = await Promise.all([
        api.alerts(), api.suppliers(), api.sources(), api.collectionRuns(), api.dimensions(), api.health(), api.agentStatus(),
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
      setError(caught instanceof Error ? caught.message : '页面数据加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadData(); }, [loadData]);

  const p1RiskCount = useMemo(
    () => riskItems.filter((item) => item.level === 'P1').length,
    [riskItems],
  );

  const handleAskAssistant = (query: string) => {
    setSelectedRisk(null);
    setPendingAssistantQuery(query);
    setActiveTab('risk-assistant');
  };

  const handleSelectSupplier = (supplier: Supplier) => {
    const matchingRisk = riskItems.find(
      (risk) => risk.companyName === supplier.legalName || risk.vendorId === supplier.id,
    );
    if (matchingRisk) setSelectedRisk(matchingRisk);
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
        industry: supplier.category || null,
        raw_materials: [],
        enabled: true,
        aliases: [],
        sites: supplier.productionLocation ? [{
          site_name: supplier.productionLocation,
          country_code: countryCode,
          region: null,
          city: null,
          address: supplier.productionLocation,
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

  const handleTriggerDataSync = async () => {
    const runnable = dataSources.filter((source) => source.type !== 'external_tool' && !source.type.toLowerCase().includes('file'));
    const results = await Promise.allSettled(runnable.map((source) => api.runSource(Number(source.id))));
    if (results.some((result) => result.status === 'rejected')) {
      setError('部分数据源采集失败，请查看数据源运行状态。');
    }
    const [sourcesResponse, runsResponse] = await Promise.all([api.sources(), api.collectionRuns()]);
    setDataSources(sourcesResponse.map((source) => mapDataSource(source, runsResponse.items)));
  };

  const refreshSources = async () => {
    const [sourcesResponse, runsResponse] = await Promise.all([api.sources(), api.collectionRuns()]);
    setDataSources(sourcesResponse.map((source) => mapDataSource(source, runsResponse.items)));
  };

  const handleCreateSource = async (payload: Parameters<typeof api.createSource>[0]) => {
    await api.createSource(payload);
    await refreshSources();
  };

  const handleUpdateSource = async (id: string, payload: Partial<Parameters<typeof api.createSource>[0]>) => {
    await api.updateSource(Number(id), payload);
    await refreshSources();
  };

  const handleDeleteSource = async (id: string) => {
    await api.deleteSource(Number(id));
    await refreshSources();
  };

  return (
    <div className="min-h-screen bg-[#f7f9ff] dark:bg-[#101d28] text-[#101d28] dark:text-slate-100 flex flex-col font-sans antialiased">
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onOpenExportModal={() => { setReportRisk(null); setIsExportModalOpen(true); }}
        onOpenSettingsModal={() => setIsSettingsModalOpen(true)}
        p1RiskCount={p1RiskCount}
      />

      <div className="lg:pl-[240px] flex-1 flex flex-col min-w-0 transition-all">
        <Header
          activeTab={activeTab}
          unreadCount={p1RiskCount}
          riskItems={riskItems}
          health={health}
          agentStatus={agentStatus}
          onSearch={handleAskAssistant}
        />

        <main className="p-4 pb-24 sm:p-6 sm:pb-24 lg:p-8 max-w-[1440px] w-full mx-auto flex-1">
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
                key={activeTab}
                initial={{opacity: 0, y: 12}}
                animate={{opacity: 1, y: 0}}
                exit={{opacity: 0, y: -12}}
                transition={{duration: 0.2, ease: 'easeOut'}}
                className="w-full h-full"
              >
                {activeTab === 'overview' && (
                  <OverviewView riskItems={riskItems} suppliers={suppliers} dataSources={dataSources}
                    onSelectRisk={setSelectedRisk} onViewAllRisks={() => setActiveTab('current-risks')} />
                )}
                {activeTab === 'current-risks' && <CurrentRisksView riskItems={riskItems} onSelectRisk={setSelectedRisk} />}
                {activeTab === 'risk-assistant' && (
                  <RiskAssistantView riskItems={riskItems} suppliers={suppliers} agentStatus={agentStatus}
                    onSelectRisk={setSelectedRisk} onSelectSupplier={handleSelectSupplier}
                    pendingQuery={pendingAssistantQuery} onClearPendingQuery={() => setPendingAssistantQuery(null)} />
                )}
                {activeTab === 'suppliers' && (
                  <SuppliersView suppliers={suppliers} onOpenImportModal={() => setIsNewSupplierModalOpen(true)}
                    onSelectSupplier={handleSelectSupplier} onToggleStatus={(id) => void handleToggleSupplierStatus(id)}
                    onAskAssistant={handleAskAssistant} />
                )}
                {activeTab === 'data-sources' && (
                  <DataSourcesView dataSources={dataSources} onTriggerSync={handleTriggerDataSync}
                    role={consoleRole} onRoleChange={setConsoleRole}
                    onCreateSource={handleCreateSource} onUpdateSource={handleUpdateSource}
                    onDeleteSource={handleDeleteSource} onRefreshSources={refreshSources} />
                )}
                {activeTab === 'rules' && (
                  <RuleEngineView dimensions={dimensions} onToggleDimension={handleToggleDimension}
                    onUpdateDimension={handleUpdateDimension} role={consoleRole} onRoleChange={setConsoleRole} />
                )}
              </motion.div>
            </AnimatePresence>
          )}
        </main>
      </div>

      <MobileNav activeTab={activeTab} setActiveTab={setActiveTab} p1RiskCount={p1RiskCount} />
      <RiskDetailModal risk={selectedRisk} onClose={() => setSelectedRisk(null)}
        onExportReport={(risk) => { setReportRisk(risk); setSelectedRisk(null); setIsExportModalOpen(true); }} onAskAssistant={handleAskAssistant} />
      <ExportReportModal isOpen={isExportModalOpen} onClose={() => setIsExportModalOpen(false)} selectedRisk={reportRisk} riskItems={riskItems} />
      <NewSupplierModal isOpen={isNewSupplierModalOpen} onClose={() => setIsNewSupplierModalOpen(false)}
        onAddSupplier={handleAddSupplier} />
      <SettingsModal isOpen={isSettingsModalOpen} onClose={() => setIsSettingsModalOpen(false)} />
    </div>
  );
}

export default App;

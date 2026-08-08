import React, { useState } from 'react';
import { ActiveTab, RiskItem, Supplier, DataSource, MonitoringDimension } from './types';
import {
  mockRiskItems,
  mockSuppliers,
  mockDataSources,
  mockMonitoringDimensions,
} from './data/mockData';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { MobileNav } from './components/MobileNav';
import { OverviewView } from './components/OverviewView';
import { CurrentRisksView } from './components/CurrentRisksView';
import { RiskAssistantView } from './components/RiskAssistantView';
import { RiskDetailModal } from './components/RiskDetailModal';
import { SuppliersView } from './components/SuppliersView';
import { DataSourcesView } from './components/DataSourcesView';
import { RuleEngineView } from './components/RuleEngineView';
import { ExportReportModal } from './components/ExportReportModal';
import { NewSupplierModal } from './components/NewSupplierModal';
import { SettingsModal } from './components/SettingsModal';

export function App() {
  const [activeTab, setActiveTab] = useState<ActiveTab>('overview');
  const [isSimulatedEmpty, setIsSimulatedEmpty] = useState(false);

  // Core App State
  const [riskItems, setRiskItems] = useState<RiskItem[]>(mockRiskItems);
  const [suppliers, setSuppliers] = useState<Supplier[]>(mockSuppliers);
  const [dataSources, setDataSources] = useState<DataSource[]>(mockDataSources);
  const [dimensions, setDimensions] = useState<MonitoringDimension[]>(
    mockMonitoringDimensions
  );

  // Selected Risk Modal State
  const [selectedRisk, setSelectedRisk] = useState<RiskItem | null>(null);

  // Other Modals State
  const [isExportModalOpen, setIsExportModalOpen] = useState(false);
  const [isNewSupplierModalOpen, setIsNewSupplierModalOpen] = useState(false);
  const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);

  // Unread P1 risk alert count
  const p1RiskCount = riskItems.filter((item) => item.level === 'P1').length;

  // Handlers
  const handleSelectRisk = (risk: RiskItem) => {
    setSelectedRisk(risk);
  };

  const handleSelectSupplier = (supplier: Supplier) => {
    // Find associated risk or create placeholder risk modal view
    const matchingRisk = riskItems.find(
      (r) => r.companyName === supplier.legalName || r.vendorId === supplier.code
    );
    if (matchingRisk) {
      setSelectedRisk(matchingRisk);
    } else {
      // Create temporary risk item view for supplier
      setSelectedRisk({
        id: `risk-temp-${supplier.id}`,
        companyName: supplier.legalName,
        vendorId: supplier.code,
        location: supplier.productionLocation,
        country: supplier.countryRegion,
        level: supplier.riskLevel,
        levelName: supplier.riskLevel === 'P1' ? '严重' : supplier.riskLevel === 'P2' ? '高风险' : '正常',
        riskType: supplier.category,
        summary: `正在对 ${supplier.legalName} (${supplier.suppliedProduct}) 进行多维度监控。近期无重特大负面事件。`,
        aiConfidence: 95.0,
        updatedTime: '刚刚',
        source: 'SR 核心监管库',
        tags: [supplier.tier, supplier.category],
        status: 'valid',
        overallScore: supplier.riskScore,
      });
    }
  };

  const handleToggleSupplierStatus = (supplierId: string) => {
    setSuppliers((prev) =>
      prev.map((sup) => {
        if (sup.id === supplierId) {
          const newStatus = sup.monitoringStatus === 'paused' ? 'normal' : 'paused';
          return { ...sup, monitoringStatus: newStatus };
        }
        return sup;
      })
    );
  };

  const handleDeleteSupplier = (supplierId: string) => {
    setSuppliers((prev) => prev.filter((sup) => sup.id !== supplierId));
  };

  const handleAddSupplier = (newSup: Supplier) => {
    setSuppliers((prev) => [newSup, ...prev]);
  };

  const handleToggleDimension = (dimId: string) => {
    setDimensions((prev) =>
      prev.map((d) => (d.id === dimId ? { ...d, enabled: !d.enabled } : d))
    );
  };

  const handleUpdateDimension = (updatedDim: MonitoringDimension) => {
    setDimensions((prev) =>
      prev.map((d) => (d.id === updatedDim.id ? updatedDim : d))
    );
  };

  const handleTriggerDataSync = () => {
    setDataSources((prev) =>
      prev.map((ds) => ({
        ...ds,
        lastSyncTime: new Date().toISOString().replace('T', ' ').slice(0, 19),
        status: 'normal',
        latency: '实时 < 500ms',
      }))
    );
  };

  return (
    <div className="min-h-screen bg-[#f7f9ff] dark:bg-[#101d28] text-[#101d28] dark:text-slate-100 flex flex-col font-sans antialiased">
      {/* Desktop Sidebar Navigation */}
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onOpenExportModal={() => setIsExportModalOpen(true)}
        onOpenSettingsModal={() => setIsSettingsModalOpen(true)}
        p1RiskCount={p1RiskCount}
      />

      {/* Main Content Area */}
      <div className="lg:pl-[240px] flex-1 flex flex-col min-w-0 transition-all">
        {/* Top Header */}
        <Header
          activeTab={activeTab}
          unreadCount={p1RiskCount}
          isSimulatedEmpty={isSimulatedEmpty}
          setIsSimulatedEmpty={setIsSimulatedEmpty}
        />

        {/* View Content Frame */}
        <main className="p-4 sm:p-6 lg:p-8 max-w-[1440px] w-full mx-auto flex-1">
          {activeTab === 'overview' && (
            <OverviewView
              riskItems={riskItems}
              dataSources={dataSources}
              onSelectRisk={handleSelectRisk}
              onViewAllRisks={() => setActiveTab('current-risks')}
              isSimulatedEmpty={isSimulatedEmpty}
              setIsSimulatedEmpty={setIsSimulatedEmpty}
            />
          )}

          {activeTab === 'current-risks' && (
            <CurrentRisksView
              riskItems={isSimulatedEmpty ? [] : riskItems}
              onSelectRisk={handleSelectRisk}
            />
          )}

          {activeTab === 'risk-assistant' && (
            <RiskAssistantView
              riskItems={isSimulatedEmpty ? [] : riskItems}
              suppliers={suppliers}
              onSelectRisk={handleSelectRisk}
              onSelectSupplier={handleSelectSupplier}
            />
          )}

          {activeTab === 'suppliers' && (
            <SuppliersView
              suppliers={suppliers}
              onOpenImportModal={() => setIsNewSupplierModalOpen(true)}
              onSelectSupplier={handleSelectSupplier}
              onToggleStatus={handleToggleSupplierStatus}
              onDeleteSupplier={handleDeleteSupplier}
            />
          )}

          {activeTab === 'data-sources' && (
            <DataSourcesView
              dataSources={dataSources}
              onTriggerSync={handleTriggerDataSync}
            />
          )}

          {activeTab === 'rules' && (
            <RuleEngineView
              dimensions={dimensions}
              onToggleDimension={handleToggleDimension}
              onUpdateDimension={handleUpdateDimension}
            />
          )}
        </main>
      </div>

      {/* Mobile Navigation */}
      <MobileNav
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        p1RiskCount={p1RiskCount}
      />

      {/* Risk Detail Modal / Drawer */}
      <RiskDetailModal
        risk={selectedRisk}
        onClose={() => setSelectedRisk(null)}
        onExportReport={(risk) => {
          setSelectedRisk(null);
          setIsExportModalOpen(true);
        }}
      />

      {/* Export Report Modal */}
      <ExportReportModal
        isOpen={isExportModalOpen}
        onClose={() => setIsExportModalOpen(false)}
        selectedRisk={selectedRisk}
      />

      {/* Import / Add New Supplier Modal */}
      <NewSupplierModal
        isOpen={isNewSupplierModalOpen}
        onClose={() => setIsNewSupplierModalOpen(false)}
        onAddSupplier={handleAddSupplier}
      />

      {/* System Settings Modal */}
      <SettingsModal
        isOpen={isSettingsModalOpen}
        onClose={() => setIsSettingsModalOpen(false)}
      />
    </div>
  );
}

export default App;

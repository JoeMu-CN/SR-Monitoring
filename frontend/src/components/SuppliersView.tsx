import React, { useMemo, useState } from 'react';
import { Supplier } from '../types';

interface SuppliersViewProps {
  suppliers: Supplier[];
  onOpenImportModal: () => void;
  onEditSupplier: (supplier: Supplier) => void;
  onToggleStatus: (supplierId: string) => void;
  onAskAssistant: (query: string) => void;
  role: 'viewer' | 'admin';
}

export const SuppliersView: React.FC<SuppliersViewProps> = ({
  suppliers,
  onOpenImportModal,
  onEditSupplier,
  onToggleStatus,
  onAskAssistant,
  role,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'normal' | 'high_risk' | 'paused'>('all');

  // 按供应商编码升序展示，搜索/状态过滤不影响最终排序。
  const sortedSuppliers = useMemo(
    () => [...suppliers].sort((a, b) => a.code.localeCompare(b.code, 'zh-Hans-CN', {numeric: true, sensitivity: 'base'})),
    [suppliers],
  );

  const filteredSuppliers = sortedSuppliers.filter((sup) => {
    const matchesSearch =
      sup.legalName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      sup.code.toLowerCase().includes(searchTerm.toLowerCase()) ||
      sup.suppliedProduct.toLowerCase().includes(searchTerm.toLowerCase()) ||
      sup.registrationNo.toLowerCase().includes(searchTerm.toLowerCase());

    const matchesStatus = statusFilter === 'all' || sup.monitoringStatus === statusFilter;

    return matchesSearch && matchesStatus;
  });

  const canManage = role === 'admin';

  return (
    <div className="space-y-5 pb-20 lg:pb-8">
      {/* Top Header & Import Action */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-xl font-black text-slate-900 dark:text-white tracking-tight lg:text-2xl">
            供应商管理
          </h1>
          <p className="text-xs text-[#424751] dark:text-slate-400 mt-0.5">
            全网一级供应商主表及实时风险监控状态
          </p>
        </div>

        <button
          onClick={onOpenImportModal}
          disabled={!canManage}
          className="flex items-center gap-2 rounded-xl bg-[#185fa5] px-4 py-2 text-[13px] font-bold text-white shadow-sm transition-all hover:bg-[#004782] disabled:opacity-40"
        >
          <span className="material-symbols-outlined text-[18px]">upload_file</span>
          <span>导入供应商</span>
        </button>
      </div>

      {/* Search & Filter Bar */}
      <div className="flex flex-col items-center justify-between gap-3 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60 sm:flex-row">
        <div className="relative w-full sm:w-80">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[#727782] text-[20px]">
            search
          </span>
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="搜索供应商编码、法人主体或产品..."
            className="w-full bg-[#f7f9ff] dark:bg-slate-800 border border-[#c2c6d2] dark:border-slate-700 rounded-lg pl-9 pr-3 py-1.5 text-[13px] focus:outline-none focus:ring-2 focus:ring-[#004782]"
          />
        </div>

        <div className="flex items-center gap-2 text-[12px] font-medium w-full sm:w-auto justify-end">
          <span className="text-[#727782]">监控状态:</span>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as any)}
            className="bg-[#f7f9ff] dark:bg-slate-800 border border-[#c2c6d2] dark:border-slate-700 rounded-lg px-2.5 py-1.5 font-bold text-[#004782]"
          >
            <option value="all">全部</option>
            <option value="normal">正常监控</option>
            <option value="high_risk">高危预警</option>
            <option value="paused">暂停监控</option>
          </select>
        </div>
      </div>

      {/* Supplier List Table */}
      <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white/80 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead className="border-b border-slate-200/80 bg-slate-100/70 text-[11px] font-bold uppercase text-slate-600 dark:border-slate-800 dark:bg-slate-800/80 dark:text-slate-300">
              <tr>
                <th className="p-3.5 pl-4 font-bold">供应商编码</th>
                <th className="p-3.5 font-bold">法人主体</th>
                <th className="p-3.5 font-bold">注册号</th>
                <th className="p-3.5 font-bold">生产地点</th>
                <th className="p-3.5 font-bold">供应产品</th>
                <th className="p-3.5 font-bold text-center">监控状态</th>
                <th className="p-3.5 pr-4 text-center">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#c2c6d2]/50 text-[13px]">
              {filteredSuppliers.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-slate-400">
                    未找到相关供应商数据
                  </td>
                </tr>
              ) : (
                filteredSuppliers.map((sup) => {
                  return (
                    <tr
                      key={sup.id}
                      className="transition-colors hover:bg-[#185fa5]/5 dark:hover:bg-slate-800/60"
                    >
                      <td className="p-3.5 pl-4 font-mono font-bold text-[#101d28] dark:text-white">
                        {sup.code}
                      </td>
                      <td className="p-3.5 font-bold text-[#101d28] dark:text-white">
                        {sup.legalName}
                      </td>
                      <td className="p-3.5 font-mono text-[#424751] dark:text-slate-400 text-[12px]">
                        {sup.registrationNo}
                      </td>
                      <td className="p-3.5 text-[#424751] dark:text-slate-300">
                        {sup.productionLocation}
                      </td>
                      <td className="p-3.5 font-medium text-[#101d28] dark:text-white">
                        {sup.suppliedProduct}
                      </td>
                      <td className="p-3.5 text-center">
                        {sup.monitoringStatus === 'normal' && (
                          <span className="inline-flex items-center gap-1.5 bg-blue-50 text-[#004782] border border-blue-200 text-[11px] font-bold px-2.5 py-0.5 rounded-full">
                            <span className="w-1.5 h-1.5 rounded-full bg-[#004782]"></span>
                            正常监控
                          </span>
                        )}
                        {sup.monitoringStatus === 'high_risk' && (
                          <span className="inline-flex items-center gap-1.5 bg-red-50 text-[#C92A2A] border border-red-200 text-[11px] font-bold px-2.5 py-0.5 rounded-full">
                            <span className="w-1.5 h-1.5 rounded-full bg-[#C92A2A]"></span>
                            高危预警
                          </span>
                        )}
                        {sup.monitoringStatus === 'paused' && (
                          <span className="inline-flex items-center gap-1.5 bg-slate-100 text-slate-600 border border-slate-300 text-[11px] font-bold px-2.5 py-0.5 rounded-full">
                            <span className="w-1.5 h-1.5 rounded-full bg-slate-400"></span>
                            暂停监控
                          </span>
                        )}
                      </td>
                      <td className="p-3.5 pr-4 text-center">
                        <div className="flex items-center justify-center gap-1">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onAskAssistant(`请查询供应商【${sup.legalName}】（编码 ${sup.code}）的完整工商风险、司法记录与供应链合规评级。`);
                            }}
                            className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-600 transition-colors hover:bg-slate-100 hover:text-[#004782]"
                            title="询问风险助手"
                          >
                            <span className="material-symbols-outlined text-[18px] leading-none">smart_toy</span>
                          </button>

                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onToggleStatus(sup.id);
                            }}
                            disabled={!canManage}
                            className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-600 transition-colors hover:bg-slate-100 hover:text-[#004782] disabled:cursor-not-allowed disabled:opacity-40"
                            title={canManage ? (sup.monitoringStatus === 'paused' ? '恢复监控' : '暂停监控') : '仅管理员可操作监控启停'}
                          >
                            <span className="material-symbols-outlined text-[18px] leading-none">
                              {sup.monitoringStatus === 'paused' ? 'play_arrow' : 'pause'}
                            </span>
                          </button>

                          {canManage && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                onEditSupplier(sup);
                              }}
                              className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-600 transition-colors hover:bg-slate-100 hover:text-[#004782]"
                              title="编辑供应商信息"
                            >
                              <span className="material-symbols-outlined text-[18px] leading-none">edit</span>
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Footer */}
        <div className="p-3.5 bg-[#f7f9ff] dark:bg-slate-800/50 border-t border-[#c2c6d2] flex justify-between items-center text-[12px] text-[#424751] dark:text-slate-400">
          <div>共 {filteredSuppliers.length} 条记录</div>
          <div className="flex gap-2">
            <button className="px-3 py-1 border border-[#c2c6d2] rounded-md hover:bg-white text-slate-600 font-medium">
              上一页
            </button>
            <button className="px-3 py-1 border border-[#c2c6d2] rounded-md hover:bg-white text-slate-600 font-medium">
              下一页
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

import React, { useState } from 'react';
import { Upload, Search, Bot, Play, Pause, Trash2 } from 'lucide-react';
import { Supplier } from '../types';

interface SuppliersViewProps {
  suppliers: Supplier[];
  onOpenImportModal: () => void;
  onSelectSupplier: (supplier: Supplier) => void;
  onToggleStatus: (supplierId: string) => void;
  onDeleteSupplier: (supplierId: string) => void;
  onAskAssistant: (query: string) => void;
}

export const SuppliersView: React.FC<SuppliersViewProps> = ({
  suppliers,
  onOpenImportModal,
  onSelectSupplier,
  onToggleStatus,
  onDeleteSupplier,
  onAskAssistant,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'normal' | 'high_risk' | 'paused'>('all');

  const filteredSuppliers = suppliers.filter((sup) => {
    const matchesSearch =
      sup.legalName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      sup.code.toLowerCase().includes(searchTerm.toLowerCase()) ||
      sup.suppliedProduct.toLowerCase().includes(searchTerm.toLowerCase()) ||
      sup.registrationNo.toLowerCase().includes(searchTerm.toLowerCase());

    const matchesStatus = statusFilter === 'all' || sup.monitoringStatus === statusFilter;

    return matchesSearch && matchesStatus;
  });

  return (
    <div className="space-y-6 pb-20 lg:pb-8">
      {/* Top Header & Import Action */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-xl lg:text-2xl font-black text-slate-900 dark:text-white tracking-tight">
            供应商管理名录
          </h1>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            全网一级/二级供应商主表及实时风险监控状态
          </p>
        </div>

        <button
          onClick={onOpenImportModal}
          className="bg-[#007aff] hover:bg-[#0062cc] text-white font-bold text-[13px] px-4 py-2 rounded-xl shadow-xs transition-all flex items-center gap-2 cursor-pointer"
        >
          <Upload className="w-4 h-4" />
          <span>导入供应商</span>
        </button>
      </div>

      {/* Search & Filter Bar */}
      <div className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 flex flex-col sm:flex-row justify-between items-center gap-3 shadow-2xs">
        <div className="relative w-full sm:w-80">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="搜索供应商编码、法人主体或产品..."
            className="w-full bg-slate-100/80 dark:bg-slate-900/60 border border-slate-200/80 dark:border-slate-700/60 rounded-xl pl-9 pr-3 py-1.5 text-[13px] focus:outline-none focus:ring-2 focus:ring-[#007aff]"
          />
        </div>

        <div className="flex items-center gap-2 text-[12px] font-medium w-full sm:w-auto justify-end">
          <span className="text-slate-500">监控状态:</span>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as any)}
            className="bg-slate-100 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-2.5 py-1.5 font-bold text-[#007aff] cursor-pointer"
          >
            <option value="all">全部</option>
            <option value="normal">正常监控</option>
            <option value="high_risk">高危预警</option>
            <option value="paused">暂停监控</option>
          </select>
        </div>
      </div>

      {/* Supplier List Table */}
      <div className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl shadow-2xs overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead className="bg-slate-100/70 dark:bg-slate-800/80 text-[11px] font-bold uppercase text-slate-500 dark:text-slate-400 border-b border-slate-200/80">
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
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60 text-[13px]">
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
                      onClick={() => onSelectSupplier(sup)}
                      className="hover:bg-[#007aff]/5 dark:hover:bg-slate-700/40 transition-colors cursor-pointer"
                    >
                      <td className="p-3.5 pl-4 font-mono font-bold text-slate-900 dark:text-white">
                        {sup.code}
                      </td>
                      <td className="p-3.5 font-bold text-slate-900 dark:text-white">
                        {sup.legalName}
                      </td>
                      <td className="p-3.5 font-mono text-slate-500 dark:text-slate-400 text-[12px]">
                        {sup.registrationNo}
                      </td>
                      <td className="p-3.5 text-slate-600 dark:text-slate-300">
                        {sup.productionLocation}
                      </td>
                      <td className="p-3.5 font-medium text-slate-900 dark:text-white">
                        {sup.suppliedProduct}
                      </td>
                      <td className="p-3.5 text-center">
                        {sup.monitoringStatus === 'normal' && (
                          <span className="inline-flex items-center gap-1.5 bg-blue-50 text-[#007aff] border border-blue-200 text-[11px] font-bold px-2.5 py-0.5 rounded-full">
                            <span className="w-1.5 h-1.5 rounded-full bg-[#007aff]"></span>
                            正常监控
                          </span>
                        )}
                        {sup.monitoringStatus === 'high_risk' && (
                          <span className="inline-flex items-center gap-1.5 bg-red-50 text-[#ff3b30] border border-red-200 text-[11px] font-bold px-2.5 py-0.5 rounded-full">
                            <span className="w-1.5 h-1.5 rounded-full bg-[#ff3b30]"></span>
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
                            onClick={(e) => {
                              e.stopPropagation();
                              onAskAssistant(`请查询供应商【${sup.legalName}】（编码 ${sup.code}）的完整工商风险、司法记录与供应链合规评级。`);
                            }}
                            className="p-1.5 text-[#007aff] dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-slate-700 rounded-lg transition-colors flex items-center gap-1 text-[12px] font-bold cursor-pointer"
                            title="询问风险助手"
                          >
                            <Bot className="w-4 h-4" />
                            <span className="hidden xl:inline">问助手</span>
                          </button>

                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onToggleStatus(sup.id);
                            }}
                            className="p-1.5 text-slate-600 hover:text-[#007aff] hover:bg-slate-100 rounded-lg transition-colors cursor-pointer"
                            title={sup.monitoringStatus === 'paused' ? '恢复监控' : '暂停监控'}
                          >
                            {sup.monitoringStatus === 'paused' ? (
                              <Play className="w-4 h-4" />
                            ) : (
                              <Pause className="w-4 h-4" />
                            )}
                          </button>

                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              if (confirm(`确认要移除供应商 ${sup.legalName} 吗？`)) {
                                onDeleteSupplier(sup.id);
                              }
                            }}
                            className="p-1.5 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors cursor-pointer"
                            title="删除供应商"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
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
        <div className="p-3.5 bg-slate-50/50 dark:bg-slate-800/50 border-t border-slate-200/80 flex justify-between items-center text-[12px] text-slate-500 dark:text-slate-400">
          <div>共 {filteredSuppliers.length} 条记录</div>
          <div className="flex gap-2 font-mono">
            <button className="px-3 py-1 border border-slate-200 dark:border-slate-700 rounded-lg hover:bg-white text-slate-600 dark:text-slate-300 font-medium cursor-pointer">
              上一页
            </button>
            <button className="px-3 py-1 border border-slate-200 dark:border-slate-700 rounded-lg hover:bg-white text-slate-600 dark:text-slate-300 font-medium cursor-pointer">
              下一页
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};


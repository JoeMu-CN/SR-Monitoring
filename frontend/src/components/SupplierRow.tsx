import type {Supplier} from '../types';

const statusBadges = {
  normal: {label: '正常监控', badge: 'bg-blue-50 text-[#004782] border-blue-200', dot: 'bg-[#004782]'},
  high_risk: {label: '当前风险', badge: 'bg-red-50 text-[#C92A2A] border-red-200', dot: 'bg-[#C92A2A]'},
  paused: {label: '暂停监控', badge: 'bg-slate-100 text-slate-600 border-slate-300', dot: 'bg-slate-400'},
} as const;

interface SupplierRowProps {
  readonly supplier: Supplier;
  readonly canManage: boolean;
  readonly onAskAssistant: (query: string) => void;
  readonly onToggleStatus: (supplier: Supplier) => void;
  readonly onEditSupplier: (supplier: Supplier) => void;
}

export const SupplierRow = ({supplier, canManage, onAskAssistant, onToggleStatus, onEditSupplier}: SupplierRowProps) => {
  const status = statusBadges[supplier.monitoringStatus];
  const paused = supplier.monitoringStatus === 'paused';
  return (
    <tr className="transition-colors hover:bg-[#185fa5]/5 dark:hover:bg-slate-800/60">
      <td className="p-3.5 pl-4 font-mono font-bold text-[#101d28] dark:text-white">{supplier.code}</td>
      <td className="p-3.5 font-bold text-[#101d28] dark:text-white">{supplier.legalName}</td>
      <td className="p-3.5 font-mono text-[12px] text-[#424751] dark:text-slate-400">{supplier.registrationNo}</td>
      <td className="p-3.5 text-[#424751] dark:text-slate-300">{supplier.productionLocation}</td>
      <td className="p-3.5 font-medium text-[#101d28] dark:text-white">{supplier.suppliedProduct}</td>
      <td className="p-3.5 text-center">
        <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-bold ${status.badge}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${status.dot}`} />
          {status.label}
        </span>
      </td>
      <td className="p-3.5 pr-4 text-center">
        <div className="flex items-center justify-center gap-1">
          <button
            type="button"
            onClick={() => onAskAssistant(`请查询供应商【${supplier.legalName}】（编码 ${supplier.code}）的完整工商风险、司法记录与供应链合规评级。`)}
            className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-600 transition-colors hover:bg-slate-100 hover:text-[#004782]"
            title={`询问风险助手：${supplier.legalName}`}
            aria-label={`询问风险助手：${supplier.legalName}`}
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[18px] leading-none">smart_toy</span>
          </button>
          <button
            type="button"
            onClick={() => onToggleStatus(supplier)}
            disabled={!canManage}
            className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-600 transition-colors hover:bg-slate-100 hover:text-[#004782] disabled:cursor-not-allowed disabled:opacity-40"
            title={canManage ? (paused ? '恢复监控' : '暂停监控') : '仅管理员可操作监控启停'}
            aria-label={`${paused ? '恢复监控' : '暂停监控'}：${supplier.legalName}`}
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[18px] leading-none">{paused ? 'play_arrow' : 'pause'}</span>
          </button>
          {canManage && (
            <button
              type="button"
              onClick={() => onEditSupplier(supplier)}
              className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-600 transition-colors hover:bg-slate-100 hover:text-[#004782]"
              title={`编辑供应商：${supplier.legalName}`}
              aria-label={`编辑供应商：${supplier.legalName}`}
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[18px] leading-none">edit</span>
            </button>
          )}
        </div>
      </td>
    </tr>
  );
};

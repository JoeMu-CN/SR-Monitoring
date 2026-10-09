import {useEffect, useRef, useState} from 'react';
import {useSearchParams} from 'react-router-dom';
import {api, ApiError, mapSupplierListItem, type SupplierCollection} from '../api';
import {isSupplierStatusFilter, supplierSearchParams, type SupplierStatusFilter} from '../routes';
import type {Supplier} from '../types';
import {SupplierRow} from './SupplierRow';

const SEARCH_DEBOUNCE_MS = 300;

const statusOptions: ReadonlyArray<readonly [SupplierStatusFilter, string]> = [
  ['all', '全部'],
  ['high_risk', '当前风险'],
  ['normal', '正常监控'],
  ['paused', '暂停监控'],
];

interface SuppliersViewProps {
  readonly role: 'viewer' | 'admin';
  readonly refreshToken: number;
  readonly onOpenImportModal: () => void;
  readonly onOpenNewSupplierModal: () => void;
  readonly onEditSupplier: (supplier: Supplier) => void;
  readonly onToggleStatus: (supplier: Supplier) => void;
  readonly onAskAssistant: (query: string) => void;
  readonly onRequestError: (error: ApiError) => void;
}

export const SuppliersView = ({
  role,
  refreshToken,
  onOpenImportModal,
  onOpenNewSupplierModal,
  onEditSupplier,
  onToggleStatus,
  onAskAssistant,
  onRequestError,
}: SuppliersViewProps) => {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestSequence = useRef(0);
  const [data, setData] = useState<SupplierCollection | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [retryKey, setRetryKey] = useState(0);

  const query = (searchParams.get('q') ?? '').trim();
  const statusFilter: SupplierStatusFilter = isSupplierStatusFilter(searchParams.get('status'))
    ? (searchParams.get('status') as SupplierStatusFilter)
    : 'all';
  // 单页展示全部供应商：规范 URL 不含 page，旧书签里的 page 会被规范化替换掉，不再限制数据。
  const canonicalSearch = supplierSearchParams(query, statusFilter).toString();
  const isCanonical = searchParams.toString() === canonicalSearch;

  const [draftQuery, setDraftQuery] = useState(query);
  const canManage = role === 'admin';

  useEffect(() => {
    if (!isCanonical) setSearchParams(canonicalSearch, {replace: true});
  }, [canonicalSearch, isCanonical, setSearchParams]);

  // URL 是唯一事实来源：规范化、前进/后退和刷新都要把输入框拉回当前查询词。
  useEffect(() => { setDraftQuery(query); }, [query]);

  useEffect(() => {
    if (draftQuery === query) return;
    const timer = setTimeout(
      () => setSearchParams(supplierSearchParams(draftQuery.trim(), statusFilter), {replace: true}),
      SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [draftQuery, query, setSearchParams, statusFilter]);

  useEffect(() => {
    if (!isCanonical) return;
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    setLoading(true);
    setError(null);
    setData(null);

    void api.supplierAll(query, statusFilter)
      .then((response) => {
        if (requestSequence.current !== sequence) return;
        setData(response);
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (requestSequence.current !== sequence) return;
        const nextError = caught instanceof Error ? caught : new Error('供应商清单加载失败');
        if (nextError instanceof ApiError && (nextError.status === 401 || nextError.status === 403)) {
          onRequestError(nextError);
        }
        setError(nextError);
        setLoading(false);
      });

    return () => {
      if (requestSequence.current === sequence) requestSequence.current += 1;
    };
  }, [isCanonical, onRequestError, query, refreshToken, retryKey, statusFilter]);

  const applyStatus = (nextStatus: SupplierStatusFilter) => {
    if (nextStatus !== statusFilter) setSearchParams(supplierSearchParams(query, nextStatus));
  };

  const suppliers: Supplier[] = data ? data.items.map(mapSupplierListItem) : [];
  const lastItem = data?.items.length ?? 0;
  const total = data?.total ?? lastItem;
  const status = error instanceof ApiError ? error.status : null;

  return (
    <div className="space-y-5 pb-20 lg:pb-8">
      <div className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-xl font-black tracking-tight text-slate-900 lg:text-2xl dark:text-white">供应商管理</h1>
          <p className="mt-0.5 text-xs text-[#424751] dark:text-slate-400">全网一级供应商主表及实时风险监控状态</p>
        </div>
        <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
          <button
            type="button"
            onClick={onOpenNewSupplierModal}
            disabled={!canManage}
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-[#c2c6d2] bg-white px-4 text-[13px] font-bold text-[#007aff] hover:bg-[#eef6ff] focus:outline-none focus:ring-2 focus:ring-[#007aff] disabled:opacity-40 dark:border-slate-600 dark:bg-slate-900"
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[18px]">add_business</span>
            <span>新增供应商</span>
          </button>
          <button
            type="button"
            onClick={onOpenImportModal}
            disabled={!canManage}
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-[#007aff] px-4 text-[13px] font-bold text-white shadow-sm hover:bg-[#0062cc] focus:outline-none focus:ring-2 focus:ring-[#007aff] focus:ring-offset-2 disabled:opacity-40"
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[18px]">upload_file</span>
            <span>导入供应商</span>
          </button>
        </div>
      </div>

      <div className="flex flex-col items-center justify-between gap-3 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md sm:flex-row dark:border-slate-700/60 dark:bg-slate-800/60">
        <div className="relative w-full sm:w-80">
          <span aria-hidden="true" className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[20px] text-[#727782]">search</span>
          <input
            type="search"
            aria-label="搜索供应商"
            value={draftQuery}
            onChange={(event) => setDraftQuery(event.target.value)}
            placeholder="搜索供应商编码、法人主体、注册号或产品..."
            className="w-full rounded-lg border border-[#c2c6d2] bg-[#f7f9ff] py-1.5 pl-9 pr-3 text-[13px] focus:outline-none focus:ring-2 focus:ring-[#004782] dark:border-slate-700 dark:bg-slate-800"
          />
        </div>
        <div className="flex w-full items-center justify-end gap-2 text-[12px] font-medium sm:w-auto">
          <label className="text-[#727782]" htmlFor="supplier-status-filter">监控状态:</label>
          <select
            id="supplier-status-filter"
            value={statusFilter}
            onChange={(event) => applyStatus(event.target.value as SupplierStatusFilter)}
            className="rounded-lg border border-[#c2c6d2] bg-[#f7f9ff] px-2.5 py-1.5 font-bold text-[#004782] dark:border-slate-700 dark:bg-slate-800"
          >
            {statusOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </div>
      </div>

      <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white/80 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead className="border-b border-slate-200/80 bg-slate-100/70 text-[11px] font-bold uppercase text-slate-600 dark:border-slate-800 dark:bg-slate-800/80 dark:text-slate-300">
              <tr>
                <th className="p-3.5 pl-4 font-bold">供应商编码</th>
                <th className="p-3.5 font-bold">法人主体</th>
                <th className="p-3.5 font-bold">注册号</th>
                <th className="p-3.5 font-bold">生产地点</th>
                <th className="p-3.5 font-bold">供应产品</th>
                <th className="p-3.5 text-center font-bold">监控状态</th>
                <th className="p-3.5 pr-4 text-center">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#c2c6d2]/50 text-[13px]">
              {loading && (
                <tr><td className="p-8 text-center text-slate-500" colSpan={7}><span role="status" className="inline-flex items-center gap-2"><span className="sr-only">正在加载供应商…</span></span></td></tr>
              )}
              {!loading && error !== null && (
                <tr>
                  <td className="p-8 text-center" colSpan={7}>
                    <p className="text-sm font-bold text-slate-900 dark:text-white" role="alert">
                      {status === 403 ? '无权查看供应商名录' : '供应商清单加载失败'}
                    </p>
                    <p className="mt-1 text-[12px] text-slate-500">{error.message}</p>
                    {status !== 403 && (
                      <button
                        type="button"
                        onClick={() => setRetryKey((value) => value + 1)}
                        className="mt-3 rounded-lg bg-[#185fa5] px-4 py-1.5 text-[13px] font-bold text-white hover:bg-[#004782]"
                      >
                        重试
                      </button>
                    )}
                  </td>
                </tr>
              )}
              {!loading && error === null && suppliers.length === 0 && (
                <tr><td className="p-8 text-center text-slate-400" colSpan={7}>未找到相关供应商数据</td></tr>
              )}
              {!loading && error === null && suppliers.map((supplier) => (
                <SupplierRow
                  key={supplier.id}
                  supplier={supplier}
                  canManage={canManage}
                  onAskAssistant={onAskAssistant}
                  onToggleStatus={onToggleStatus}
                  onEditSupplier={onEditSupplier}
                />
              ))}
            </tbody>
          </table>
        </div>

        <div aria-live="polite" className="border-t border-[#c2c6d2] bg-[#f7f9ff] p-3.5 text-[12px] text-[#424751] dark:bg-slate-800/50 dark:text-slate-400">
          显示 {lastItem === 0 ? 0 : 1}-{lastItem}，共 {total.toLocaleString()} 条
        </div>
      </div>
    </div>
  );
};

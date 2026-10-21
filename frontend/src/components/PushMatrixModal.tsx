import {useEffect, useMemo, useRef, useState, type MouseEvent} from 'react';
import {AlertTriangle, BellRing, X} from 'lucide-react';
import {api, ApiError, type PushMatrixRow, type PushMatrixUpdate, type UserRole} from '../api';
import {useDialogFocus} from '../hooks/useDialogFocus';

interface PushMatrixModalProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly onSaved?: () => void;
  readonly onRequestError?: (error: ApiError) => void;
}

const roleLabels: Record<UserRole, string> = {
  viewer: '查看者', risk_analyst: '风险分析员', risk_admin: '风险管理员', platform_admin: '平台管理员',
};

type ColumnKey = 'push_p1' | 'push_p2' | 'push_p3' | 'push_monthly_report';

const columns: ReadonlyArray<readonly [ColumnKey, string]> = [
  ['push_p1', 'P1'], ['push_p2', 'P2'], ['push_p3', 'P3'], ['push_monthly_report', '月报'],
];

type PushFlags = Record<ColumnKey, boolean>;

const flagsOf = (row: PushMatrixRow): PushFlags => ({
  push_p1: row.push_p1, push_p2: row.push_p2, push_p3: row.push_p3, push_monthly_report: row.push_monthly_report,
});

/** 查看者不参与推送；没有邮箱的用户无法接收邮件，两者都不可勾选。 */
const isCheckable = (row: PushMatrixRow) => row.role !== 'viewer' && (row.email ?? '').trim() !== '';

const flagsEqual = (left: PushFlags, right: PushFlags) => columns.every(([key]) => left[key] === right[key]);

export const PushMatrixModal = ({isOpen, onClose, onSaved, onRequestError}: PushMatrixModalProps) => {
  const {dialogRef} = useDialogFocus({isOpen, disabled: false, onClose: () => requestClose()});
  const [rows, setRows] = useState<readonly PushMatrixRow[] | null>(null);
  const [draft, setDraft] = useState<Record<number, PushFlags>>({});
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const loadGenerationRef = useRef(0);
  // 回调身份不稳定时不应触发重新加载，这里用 ref 固定住。
  const requestErrorRef = useRef(onRequestError);
  requestErrorRef.current = onRequestError;

  useEffect(() => {
    if (!isOpen) return;
    const generation = ++loadGenerationRef.current;
    setLoading(true);
    setLoadError(null);
    setSaveError(null);
    setConfirmClose(false);
    setRows(null);
    setDraft({});
    void api.notifications.getPushMatrix().then((data) => {
      if (generation !== loadGenerationRef.current) return;
      setRows(data);
      setDraft(Object.fromEntries(data.map((row) => [row.user_id, flagsOf(row)])));
      setLoading(false);
    }).catch((caught: unknown) => {
      if (generation !== loadGenerationRef.current) return;
      const error = caught instanceof Error ? caught : new Error('推送方案加载失败');
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) requestErrorRef.current?.(error);
      setLoadError(error.message);
      setLoading(false);
    });
  }, [isOpen]);

  /** 发生改动的行：只提交这些行，未改动的行保持服务端原值。 */
  const changedRows = useMemo<readonly PushMatrixRow[]>(() => {
    if (!rows) return [];
    return rows.filter((row) => {
      const flags = draft[row.user_id];
      if (!flags) return false;
      return !flagsEqual(flags, flagsOf(row));
    });
  }, [rows, draft]);

  const hasChanges = changedRows.length > 0;

  function requestClose() {
    if (hasChanges) {
      setConfirmClose(true);
      return;
    }
    onClose();
  }

  const toggleCell = (row: PushMatrixRow, key: ColumnKey) => {
    setDraft((current) => ({
      ...current,
      [row.user_id]: {...(current[row.user_id] ?? flagsOf(row)), [key]: !(current[row.user_id]?.[key] ?? false)},
    }));
  };

  /** 表头全选/反选：只作用于可勾选行（跳过查看者与无邮箱用户）。 */
  const toggleColumn = (key: ColumnKey) => {
    const checkable = (rows ?? []).filter(isCheckable);
    if (checkable.length === 0) return;
    const allChecked = checkable.every((row) => draft[row.user_id]?.[key]);
    setDraft((current) => {
      const next = {...current};
      for (const row of checkable) {
        next[row.user_id] = {...(next[row.user_id] ?? flagsOf(row)), [key]: !allChecked};
      }
      return next;
    });
  };

  const columnState = (key: ColumnKey) => {
    const checkable = (rows ?? []).filter(isCheckable);
    const checkedCount = checkable.filter((row) => draft[row.user_id]?.[key]).length;
    return {total: checkable.length, checkedCount, allChecked: checkable.length > 0 && checkedCount === checkable.length};
  };

  const handleSave = async () => {
    if (!hasChanges) return;
    const items: PushMatrixUpdate[] = changedRows.map((row) => ({
      user_id: row.user_id,
      push_p1: draft[row.user_id].push_p1,
      push_p2: draft[row.user_id].push_p2,
      push_p3: draft[row.user_id].push_p3,
      push_monthly_report: draft[row.user_id].push_monthly_report,
    }));
    setSaving(true);
    setSaveError(null);
    try {
      await api.notifications.updatePushMatrix(items);
      setConfirmClose(false);
      onSaved?.();
      onClose();
    } catch (caught) {
      const error = caught instanceof Error ? caught : new Error('推送方案保存失败');
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) requestErrorRef.current?.(error);
      setSaveError(error.message);
    } finally {
      setSaving(false);
    }
  };

  const handleOverlayClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    requestClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-xs" onClick={handleOverlayClick}>
      <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="push-matrix-title" className="flex max-h-[calc(100dvh-2rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900">
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 p-5 dark:border-slate-700">
          <div className="min-w-0">
            <h2 id="push-matrix-title" className="text-lg font-bold text-[#101d28] dark:text-white">推送管理</h2>
            <p className="mt-1 text-sm text-[#424751] dark:text-slate-400">按用户设置 P1/P2/P3 实时提醒与月报的邮件推送范围。</p>
          </div>
          <button type="button" aria-label="关闭推送管理弹窗" onClick={requestClose} disabled={saving} className="flex min-h-11 min-w-11 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-[#007aff] disabled:opacity-40 dark:hover:bg-slate-800"><X aria-hidden="true" className="h-5 w-5" /></button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {loading && <div role="status" className="flex items-center justify-center p-8 text-sm text-[#007aff]">正在加载推送方案…</div>}
          {!loading && loadError && (
            <div className="rounded-xl border border-red-200 bg-[#ffdad6] p-4 text-sm text-[#93000a] dark:border-red-900 dark:bg-red-950/40 dark:text-red-100">
              <p role="alert" className="flex items-start gap-2 font-bold"><AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />推送方案加载失败</p>
              <p className="mt-1 text-xs">{loadError}</p>
            </div>
          )}
          {!loading && !loadError && rows && (
            <div className="overflow-x-auto">
              <table aria-label="推送管理矩阵" className="w-full min-w-[560px] border-collapse text-left text-sm">
                <thead className="bg-slate-100/70 text-xs font-bold text-slate-600 dark:bg-slate-800/80 dark:text-slate-300">
                  <tr>
                    <th scope="col" className="sticky left-0 z-10 bg-slate-100 p-3 dark:bg-slate-800">用户</th>
                    {columns.map(([key, label]) => {
                      const state = columnState(key);
                      return (
                        <th key={key} scope="col" className="p-3 text-center">
                          <span className="mr-1.5">{label}</span>
                          <input
                            type="checkbox"
                            aria-label={`全选 ${label}`}
                            checked={state.allChecked}
                            disabled={state.total === 0 || saving}
                            ref={(element) => { if (element) element.indeterminate = state.checkedCount > 0 && !state.allChecked; }}
                            onChange={() => toggleColumn(key)}
                            className="h-4 w-4 accent-[#007aff]"
                          />
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#c2c6d2]/50">
                  {rows.map((row) => {
                    const name = row.display_name || row.username;
                    const flags = draft[row.user_id] ?? flagsOf(row);
                    const emailMissing = (row.email ?? '').trim() === '';
                    return (
                      <tr key={row.user_id}>
                        <th scope="row" className="sticky left-0 z-10 bg-white p-3 font-normal dark:bg-slate-900">
                          <span className="flex items-center gap-1.5">
                            <span className="font-bold text-[#101d28] dark:text-white">{name}</span>
                            {row.role === 'risk_analyst' && row.supplier_count === 0 && (
                              <span title="未分配负责供应商，不会推送实时风险" aria-label="未分配负责供应商，不会推送实时风险" className="inline-flex">
                                <AlertTriangle aria-hidden="true" className="h-4 w-4 shrink-0 text-[#D97706]" />
                              </span>
                            )}
                          </span>
                          <span className="mt-0.5 block text-xs text-[#727782] dark:text-slate-400">{roleLabels[row.role]} · {row.email || '未设置邮箱'}</span>
                        </th>
                        {columns.map(([key, label]) => (
                          <td key={key} className="p-3 text-center">
                            {row.role === 'viewer' ? (
                              <span aria-label="查看者不参与推送" title="查看者不参与推送" className="text-slate-400 dark:text-slate-500">-</span>
                            ) : (
                              <input
                                type="checkbox"
                                aria-label={`${name} ${label}`}
                                checked={flags[key]}
                                disabled={emailMissing || saving}
                                title={emailMissing ? '未设置邮箱，无法开启推送' : undefined}
                                onChange={() => toggleCell(row, key)}
                                className="h-4 w-4 accent-[#007aff] disabled:cursor-not-allowed disabled:opacity-50"
                              />
                            )}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {!loading && !loadError && rows && rows.length === 0 && <p className="p-6 text-center text-sm text-[#727782] dark:text-slate-400">暂无用户可配置。</p>}
        </div>
        {confirmClose && (
          <div role="alertdialog" aria-label="未保存的更改" className="mx-5 mb-3 flex flex-col gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 sm:flex-row sm:items-center sm:justify-between dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
            <span>有未保存的推送方案更改，确认关闭？</span>
            <span className="flex gap-2">
              <button type="button" onClick={() => setConfirmClose(false)} className="min-h-11 rounded-xl border border-[#c2c6d2] px-4 text-sm font-bold text-[#424751] hover:bg-white focus:outline-none focus:ring-2 focus:ring-[#007aff] dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800">继续编辑</button>
              <button type="button" onClick={onClose} className="min-h-11 rounded-xl bg-[#C92A2A] px-4 text-sm font-bold text-white hover:bg-[#a51f1f] focus:outline-none focus:ring-2 focus:ring-[#C92A2A] focus:ring-offset-2">放弃修改并关闭</button>
            </span>
          </div>
        )}
        {saveError && <p role="alert" className="mx-5 mb-3 flex items-start gap-2 rounded-xl border border-red-200 bg-[#ffdad6] p-3 text-sm text-[#93000a] dark:border-red-900 dark:bg-red-950/40 dark:text-red-100"><AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />{saveError}</p>}
        <footer className="flex flex-col-reverse gap-3 border-t border-slate-200 p-5 sm:flex-row sm:items-center sm:justify-between dark:border-slate-700">
          <p className="flex items-center gap-1.5 text-xs text-slate-500"><BellRing aria-hidden="true" className="h-3.5 w-3.5" />仅管理员可调整推送方案</p>
          <span className="flex gap-3">
            <button type="button" onClick={requestClose} disabled={saving} className="min-h-11 rounded-xl border border-[#c2c6d2] px-5 text-sm font-bold text-[#424751] hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-[#007aff] disabled:opacity-40 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800">取消</button>
            <button type="button" onClick={() => void handleSave()} disabled={!hasChanges || saving} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-[#007aff] px-5 text-sm font-bold text-white hover:bg-[#0062cc] focus:outline-none focus:ring-2 focus:ring-[#007aff] focus:ring-offset-2 disabled:opacity-40">{saving ? '保存中…' : '保存'}</button>
          </span>
        </footer>
      </section>
    </div>
  );
};

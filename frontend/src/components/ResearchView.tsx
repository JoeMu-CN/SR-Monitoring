import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {motion} from 'motion/react';
import {Building2, CheckCircle2, Clock3, FileText, Plus, RefreshCw, Search, ShieldCheck, Trash2, XCircle} from 'lucide-react';
import {
  api,
  type ResearchReportRead,
  type ResearchSourceRead,
  type ResearchTaskEventRead,
  type ResearchTaskRead,
  type ResearchWorkerStatusRead,
  type SupplierRead,
} from '../api';
import {ResearchExecutionGraph} from './ResearchExecutionGraph';

interface ResearchViewProps { canCreate: boolean; }

const taskStatus: Record<ResearchTaskRead['status'], {label: string; className: string}> = {
  queued: {label: '等待执行', className: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'},
  running: {label: '执行中', className: 'bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300'},
  succeeded: {label: '已完成', className: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300'},
  failed: {label: '执行失败', className: 'bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-300'},
  cancelled: {label: '已取消', className: 'bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300'},
};

const formatDate = (value: string | null) => value
  ? new Intl.DateTimeFormat('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}).format(new Date(value))
  : '尚未记录';

const budgetLimit = (task: ResearchTaskRead, key: string) => {
  const value = task.budget_snapshot?.[key];
  return typeof value === 'number' || typeof value === 'string' ? String(value) : '—';
};

const sectionMeta = {
  facts: {label: '事实', tone: 'text-blue-700 dark:text-blue-300', marker: 'bg-blue-500'},
  inferences: {label: '推断', tone: 'text-amber-700 dark:text-amber-300', marker: 'bg-amber-500'},
  forecasts: {label: '预测', tone: 'text-violet-700 dark:text-violet-300', marker: 'bg-violet-500'},
} as const;

const workerStatusMeta: Record<ResearchWorkerStatusRead['status'], {label: string; className: string; detail: string}> = {
  online: {label: 'Worker 在线', className: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300', detail: '任务可以被本地 Worker 领取'},
  stale: {label: 'Worker 心跳过期', className: 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300', detail: '最近心跳已超过允许间隔，请检查 Worker 日志'},
  offline: {label: 'Worker 未启动', className: 'border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300', detail: '点击开始后任务会保持等待，需先启动本地研究 Worker'},
};

export const ResearchView: React.FC<ResearchViewProps> = ({canCreate}) => {
  const [tasks, setTasks] = useState<ResearchTaskRead[]>([]);
  const [selectedTask, setSelectedTask] = useState<ResearchTaskRead | null>(null);
  const [reports, setReports] = useState<ResearchReportRead[]>([]);
  const [sources, setSources] = useState<ResearchSourceRead[]>([]);
  const [events, setEvents] = useState<ResearchTaskEventRead[]>([]);
  const [suppliers, setSuppliers] = useState<SupplierRead[]>([]);
  const [workerStatus, setWorkerStatus] = useState<ResearchWorkerStatusRead | null>(null);
  const [selectedSupplierIds, setSelectedSupplierIds] = useState<number[]>([]);
  const [supplierSearch, setSupplierSearch] = useState('');
  const [topic, setTopic] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadingReports, setLoadingReports] = useState(false);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [deletingTaskId, setDeletingTaskId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const eventCursor = useRef(0);
  const eventTaskId = useRef<number | null>(null);

  const loadTasks = useCallback(async () => {
    setError(null);
    try {
      const response = await api.research.tasks();
      setTasks(response.items);
      setSelectedTask((current) => current ?? response.items[0] ?? null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '研究任务加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadWorkerStatus = useCallback(async () => {
    try {
      setWorkerStatus(await api.research.workerStatus());
    } catch (caught) {
      if (caught instanceof Error) setError(caught.message);
    }
  }, []);

  const loadReports = useCallback(async (taskId: number) => {
    setLoadingReports(true);
    try {
      const response = await api.research.reports(taskId);
      setReports(response.items);
    } catch (caught) {
      setReports([]);
      setError(caught instanceof Error ? caught.message : '研究草稿加载失败');
    } finally {
      setLoadingReports(false);
    }
  }, []);

  const loadSources = useCallback(async (taskId: number) => {
    try {
      const response = await api.research.sources(taskId);
      setSources(response.items);
    } catch (caught) {
      setSources([]);
      setError(caught instanceof Error ? caught.message : '研究来源加载失败');
    }
  }, []);

  const loadEvents = useCallback(async (taskId: number, reset = false, limit = 200) => {
    if (reset) {
      eventTaskId.current = taskId;
      eventCursor.current = 0;
      setEvents([]);
    }
    const cursor = eventTaskId.current === taskId ? eventCursor.current : 0;
    try {
      const response = await api.research.events(taskId, cursor, limit);
      if (eventTaskId.current !== taskId) return;
      eventCursor.current = response.next_after_id;
      if (response.items.length > 0) {
        setEvents((current) => {
          const known = new Set(current.map((event) => event.id));
          return [...current, ...response.items.filter((event) => !known.has(event.id))];
        });
      }
    } catch (caught) {
      if (eventTaskId.current === taskId) {
        setError(caught instanceof Error ? caught.message : '研究执行事件加载失败');
      }
    }
  }, []);

  const refreshSelectedTask = useCallback(async (taskId: number) => {
    try {
      const next = await api.research.task(taskId);
      setTasks((current) => current.map((task) => task.id === taskId ? next : task));
      setSelectedTask((current) => current?.id === taskId ? next : current);
      if (!['queued', 'running'].includes(next.status)) {
        // 任务可能在两次轮询之间快速结束；终态再补一次增量事件，避免图停在半程。
        void loadEvents(taskId, false, 200);
      }
      if (next.status === 'succeeded') {
        void loadReports(taskId);
        void loadSources(taskId);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '研究任务状态刷新失败');
    }
  }, [loadEvents, loadReports, loadSources]);

  useEffect(() => {
    void loadTasks();
    void loadWorkerStatus();
    void api.suppliers().then((response) => setSuppliers(response.items)).catch(() => setSuppliers([]));
    const timer = window.setInterval(() => void loadWorkerStatus(), 5000);
    return () => window.clearInterval(timer);
  }, [loadTasks, loadWorkerStatus]);
  useEffect(() => {
    if (selectedTask) {
      void loadReports(selectedTask.id);
      void loadSources(selectedTask.id);
      void loadEvents(selectedTask.id, true, ['queued', 'running'].includes(selectedTask.status) ? 1 : 200);
    } else {
      setReports([]);
      setSources([]);
      setEvents([]);
      eventTaskId.current = null;
      eventCursor.current = 0;
    }
  }, [loadEvents, loadReports, loadSources, selectedTask?.id]);
  useEffect(() => {
    if (!selectedTask || !['queued', 'running'].includes(selectedTask.status)) return undefined;
    const timer = window.setInterval(() => {
      void refreshSelectedTask(selectedTask.id);
      void loadEvents(selectedTask.id, false, 2);
      void loadSources(selectedTask.id);
    }, 900);
    return () => window.clearInterval(timer);
  }, [loadEvents, loadSources, refreshSelectedTask, selectedTask?.id, selectedTask?.status]);

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = topic.trim();
    if (!trimmed || saving || !canCreate) return;
    setSaving(true);
    setError(null);
    try {
      const created = await api.research.createTask(trimmed, selectedSupplierIds);
      setTasks((current) => [created, ...current]);
      setSelectedTask(created);
      setTopic('');
      setSelectedSupplierIds([]);
      setSupplierSearch('');
      setReports([]);
      setSources([]);
      setEvents([]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '研究任务创建失败');
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = async () => {
    if (!selectedTask || cancelling || selectedTask.cancel_requested_at || !['queued', 'running'].includes(selectedTask.status)) return;
    setCancelling(true);
    setError(null);
    try {
      const updated = await api.research.cancelTask(selectedTask.id);
      setTasks((current) => current.map((task) => task.id === updated.id ? updated : task));
      setSelectedTask(updated);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '研究任务取消失败');
    } finally {
      setCancelling(false);
    }
  };

  const handleStart = async () => {
    if (!selectedTask || starting || selectedTask.status !== 'queued' || selectedTask.execution_requested_at) return;
    setStarting(true);
    setError(null);
    try {
      const updated = await api.research.startTask(selectedTask.id);
      setTasks((current) => current.map((task) => task.id === updated.id ? updated : task));
      setSelectedTask(updated);
      void loadEvents(updated.id, false, 2);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '研究任务启动失败');
    } finally {
      setStarting(false);
    }
  };

  const handleDelete = async (task: ResearchTaskRead) => {
    if (deletingTaskId !== null) return;
    if (!window.confirm(`确定删除研究任务“${task.topic}”吗？相关来源、事件和研究草稿也会一并删除。`)) return;
    setDeletingTaskId(task.id);
    setError(null);
    try {
      await api.research.deleteTask(task.id);
      setTasks((current) => {
        const remaining = current.filter((item) => item.id !== task.id);
        if (selectedTask?.id === task.id) setSelectedTask(remaining[0] ?? null);
        return remaining;
      });
      if (selectedTask?.id === task.id) {
        setReports([]);
        setSources([]);
        setEvents([]);
        eventTaskId.current = null;
        eventCursor.current = 0;
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '研究任务删除失败');
    } finally {
      setDeletingTaskId(null);
    }
  };

  const latestReport = reports[0] ?? null;
  const filteredSuppliers = useMemo(() => {
    const query = supplierSearch.trim().toLocaleLowerCase('zh-CN');
    if (!query) return suppliers;
    return suppliers.filter((supplier) => `${supplier.legal_name} ${supplier.supplier_code}`.toLocaleLowerCase('zh-CN').includes(query));
  }, [supplierSearch, suppliers]);
  const claimCount = useMemo(() => latestReport
    ? latestReport.draft.facts.length + latestReport.draft.inferences.length + latestReport.draft.forecasts.length
    : 0, [latestReport]);

  return (
    <div className="space-y-5">
      <section className="relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-[#101d28]">
        <div className="absolute right-0 top-0 h-full w-1/3 bg-[radial-gradient(circle_at_top_right,rgba(0,122,255,0.18),transparent_68%)] dark:bg-[radial-gradient(circle_at_top_right,rgba(64,156,255,0.2),transparent_68%)]" />
        <div className="relative flex flex-col gap-5 p-5 sm:p-7 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <div className="mb-3 flex items-center gap-2 text-[10px] font-extrabold uppercase tracking-[0.2em] text-[#007aff]">
              <span className="h-1.5 w-1.5 rounded-full bg-[#007aff]" /> RESEARCH TRACK / 研究轨
            </div>
            <h1 className="text-2xl font-black tracking-tight text-[#101d28] dark:text-white sm:text-3xl">把公开信息变成可回看的研究草稿</h1>
            <p className="mt-2 max-w-xl text-[13px] leading-6 text-slate-600 dark:text-slate-300">
              先创建手动研究任务，再查看带引用的结构化草稿。当前仅供管理层和业务人员参考，不直接进入风险评分。
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2 text-[11px] font-bold">
            <span className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1.5 text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300">手动即时研究</span>
            <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1.5 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">仅供参考</span>
          </div>
        </div>
      </section>

      {workerStatus && (() => {
        const meta = workerStatusMeta[workerStatus.status];
        return <section className={`rounded-xl border px-4 py-3 ${meta.className}`} aria-live="polite">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-2.5"><span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-current" /><div><p className="text-[12px] font-extrabold">{meta.label}</p><p className="mt-0.5 text-[11px] leading-5 opacity-85">{meta.detail}</p></div></div>
            {workerStatus.status !== 'online' && <code className="rounded-lg border border-current/20 bg-white/50 px-2.5 py-1.5 text-[10px] font-semibold dark:bg-slate-950/30">.\scripts\start-research-worker.ps1 -Mode topic_source_discovery -EnableCrawl4AI</code>}
          </div>
        </section>;
      })()}

      {error && (
        <div className="flex items-center justify-between gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[13px] text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
          <span>{error}</span>
          <button type="button" onClick={() => void loadTasks()} className="font-bold hover:underline">重试</button>
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[minmax(280px,0.82fr)_minmax(0,1.65fr)]">
        <section className="flex min-h-[560px] flex-col rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-[#101d28]">
          <div className="border-b border-slate-200/80 p-4 dark:border-slate-800">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <h2 className="flex items-center gap-2 text-[15px] font-extrabold text-[#101d28] dark:text-white"><Search className="h-4 w-4 text-[#007aff]" />新建研究任务</h2>
                <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">只需填写研究主题，系统将自动发现并读取公开信源。</p>
              </div>
              <span className="font-mono text-[10px] text-slate-400">MANUAL</span>
            </div>
            <form onSubmit={handleCreate} className="space-y-2">
              <textarea
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                disabled={!canCreate || saving}
                maxLength={2000}
                rows={3}
                placeholder={canCreate ? '例如：某供应商近 30 天公开风险动态' : '当前账号无创建研究任务权限'}
                className="w-full resize-none rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-[13px] leading-5 text-[#101d28] outline-none transition focus:border-[#007aff] focus:ring-2 focus:ring-blue-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:focus:ring-blue-950"
              />
              <details className="group rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-900">
                <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-[12px] font-bold text-slate-700 marker:content-none dark:text-slate-200">
                  <span className="inline-flex min-w-0 items-center gap-2"><Building2 className="h-4 w-4 shrink-0 text-[#007aff]" /><span className="truncate">监控轨范围（可选）</span></span>
                  <span className="shrink-0 text-[10px] font-semibold text-slate-400">{selectedSupplierIds.length > 0 ? `已选 ${selectedSupplierIds.length} 家` : '未选择'}</span>
                </summary>
                <div className="border-t border-slate-200 p-2.5 dark:border-slate-700">
                  <input
                    type="search"
                    value={supplierSearch}
                    onChange={(event) => setSupplierSearch(event.target.value)}
                    placeholder="按供应商名称或编码搜索"
                    className="mb-2 min-h-11 w-full rounded-lg border border-slate-200 bg-white px-3 text-[12px] text-[#101d28] outline-none placeholder:text-slate-500 focus:border-[#007aff] focus:ring-2 focus:ring-blue-100 dark:border-slate-700 dark:bg-[#0b131e] dark:text-white dark:placeholder:text-slate-400 dark:focus:ring-blue-950"
                  />
                  <div className="max-h-44 space-y-1 overflow-y-auto" role="group" aria-label="选择研究任务供应商范围">
                    {filteredSuppliers.length === 0 ? <p className="px-2 py-4 text-center text-[11px] text-slate-500">没有匹配的供应商</p> : filteredSuppliers.map((supplier) => {
                      const checked = selectedSupplierIds.includes(supplier.id);
                      return <label key={supplier.id} className="flex min-h-11 cursor-pointer items-center gap-2.5 rounded-lg px-2 py-2 transition hover:bg-blue-50 dark:hover:bg-blue-950/30">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => setSelectedSupplierIds((current) => checked ? current.filter((id) => id !== supplier.id) : [...current, supplier.id])}
                          className="h-4 w-4 rounded border-slate-300 accent-[#007aff]"
                        />
                        <span className="min-w-0"><span className="block truncate text-[12px] font-semibold text-slate-700 dark:text-slate-200">{supplier.legal_name}</span><span className="font-mono text-[10px] text-slate-400">{supplier.supplier_code}</span></span>
                      </label>;
                    })}
                  </div>
                  <p className="mt-2 px-1 text-[10px] leading-4 text-slate-500 dark:text-slate-400">未选择时不会读取全库监控信号；选择后仅载入这些供应商的当前、已确认风险证据。</p>
                </div>
              </details>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px] text-slate-400">开始研究后调用搜索服务，并按任务预算受控读取候选页面</span>
                <button type="submit" disabled={!topic.trim() || saving || !canCreate} className="inline-flex items-center gap-1.5 rounded-lg bg-[#007aff] px-3 py-2 text-[12px] font-bold text-white shadow-sm transition hover:bg-[#0062cc] disabled:cursor-not-allowed disabled:opacity-45">
                  <Plus className="h-3.5 w-3.5" />{saving ? '创建中…' : '创建任务'}
                </button>
              </div>
            </form>
          </div>

          <div className="flex items-center justify-between px-4 pb-2 pt-4">
            <h2 className="text-[12px] font-extrabold uppercase tracking-wider text-slate-500 dark:text-slate-400">我的研究任务</h2>
            <button type="button" onClick={() => void loadTasks()} className="rounded-md p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-[#007aff] dark:hover:bg-slate-800" title="刷新任务"><RefreshCw className="h-3.5 w-3.5" /></button>
          </div>
          <div className="flex-1 space-y-1.5 overflow-y-auto px-3 pb-3">
            {loading ? <div className="p-6 text-center text-[12px] text-slate-400">正在加载任务…</div> : tasks.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-200 p-6 text-center dark:border-slate-700"><FileText className="mx-auto mb-2 h-6 w-6 text-slate-300" /><p className="text-[12px] font-semibold text-slate-500">还没有研究任务</p><p className="mt-1 text-[11px] text-slate-400">从上方创建一次手动研究。</p></div>
            ) : tasks.map((task) => {
              const meta = taskStatus[task.status];
              const active = selectedTask?.id === task.id;
              return <div key={task.id} className={`relative w-full rounded-xl border text-left transition ${active ? 'border-blue-300 bg-blue-50/70 shadow-sm dark:border-blue-800 dark:bg-blue-950/30' : 'border-transparent hover:border-slate-200 hover:bg-slate-50 dark:hover:border-slate-700 dark:hover:bg-slate-900'}`}>
                <button type="button" onClick={() => setSelectedTask(task)} className="w-full p-3 pr-12 text-left">
                  <div className="mb-1.5 flex items-start justify-between gap-2"><span className="line-clamp-2 min-w-0 text-[13px] font-bold text-[#101d28] dark:text-slate-100">{task.topic}</span><span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${meta.className}`}>{meta.label}</span></div>
                  <div className="flex items-center justify-between text-[10px] text-slate-400"><span>#{task.id} · 自动发现信源</span><span>{formatDate(task.created_at)}</span></div>
                </button>
                <button type="button" aria-label={`删除研究任务 ${task.id}`} title="删除研究任务" onClick={() => void handleDelete(task)} disabled={deletingTaskId !== null || task.status === 'running'} className="absolute right-2 top-2 rounded-md p-1 text-slate-400 transition hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-35 dark:hover:bg-red-950/30"><Trash2 className="h-3.5 w-3.5" /></button>
              </div>;
            })}
          </div>
        </section>

        <section className="min-h-[560px] rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-[#101d28]">
          {!selectedTask ? <div className="flex min-h-[560px] flex-col items-center justify-center px-6 text-center"><div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50 text-[#007aff] dark:bg-blue-950/40"><FileText className="h-7 w-7" /></div><h2 className="text-[16px] font-extrabold text-[#101d28] dark:text-white">选择一项研究任务</h2><p className="mt-2 max-w-xs text-[12px] leading-5 text-slate-500 dark:text-slate-400">任务完成后，带引用的报告草稿会显示在这里。</p></div> : (
            <div>
              <div className="flex flex-col gap-3 border-b border-slate-200/80 p-5 sm:flex-row sm:items-start sm:justify-between dark:border-slate-800"><div><div className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-[#007aff]"><span>任务 #{selectedTask.id}</span><span className="text-slate-300">/</span><span>手动研究</span>{['queued', 'running'].includes(selectedTask.status) && <span className="inline-flex items-center gap-1 text-slate-400"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[#007aff]" />动态刷新</span>}</div><h2 className="text-xl font-black text-[#101d28] dark:text-white">{selectedTask.topic}</h2><p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">创建于 {formatDate(selectedTask.created_at)} · 监控范围 {selectedTask.supplier_scope.length} 家 · 尝试 {selectedTask.attempts} 次{selectedTask.cancel_requested_at ? ' · 已请求取消' : ''}</p><div className="mt-3 flex flex-wrap gap-1.5 text-[10px] font-semibold text-slate-500 dark:text-slate-400"><span className="rounded-md bg-slate-100 px-2 py-1 dark:bg-slate-800">查询 {selectedTask.search_queries_used}/{budgetLimit(selectedTask, 'max_queries')}</span><span className="rounded-md bg-slate-100 px-2 py-1 dark:bg-slate-800">结果 {selectedTask.search_results_used}/{budgetLimit(selectedTask, 'max_results')}</span><span className="rounded-md bg-slate-100 px-2 py-1 dark:bg-slate-800">Token {(selectedTask.input_tokens_used + selectedTask.output_tokens_used).toLocaleString()}/{budgetLimit(selectedTask, 'max_input_tokens')}+{budgetLimit(selectedTask, 'max_output_tokens')}</span>{selectedTask.current_step && <span className="rounded-md bg-blue-50 px-2 py-1 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300">当前步骤：{selectedTask.current_step}</span>}</div></div><div className="flex flex-wrap items-center gap-2"><span className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${taskStatus[selectedTask.status].className}`}>{taskStatus[selectedTask.status].label}</span><span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-bold text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">草稿仅供参考</span>{selectedTask.status === 'queued' && <button type="button" onClick={() => void handleStart()} disabled={starting || Boolean(selectedTask.execution_requested_at)} className="rounded-lg bg-[#007aff] px-2.5 py-1.5 text-[11px] font-bold text-white transition hover:bg-[#0062cc] disabled:cursor-not-allowed disabled:opacity-50">{starting ? '请求中…' : selectedTask.execution_requested_at ? '等待 Worker' : '开始研究'}</button>}{['queued', 'running'].includes(selectedTask.status) && <button type="button" onClick={() => void handleCancel()} disabled={cancelling || Boolean(selectedTask.cancel_requested_at)} className="rounded-lg border border-red-200 px-2.5 py-1.5 text-[11px] font-bold text-red-700 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950/30">{cancelling || selectedTask.cancel_requested_at ? '取消中…' : '取消任务'}</button>}</div></div>
              <div className="space-y-5 p-5"><ResearchExecutionGraph task={selectedTask} events={events} sources={sources} />{sources.length > 0 && <div><div className="mb-2 flex items-center justify-between"><h3 className="text-[13px] font-extrabold text-[#101d28] dark:text-white">研究证据来源</h3><span className="text-[10px] font-bold text-slate-400">{sources.length} 个已保存</span></div><div className="space-y-2">{sources.map((source) => <a key={source.id} href={source.url} target="_blank" rel="noreferrer" className={`block rounded-xl border p-3 transition ${source.source_type === 'monitoring_signal' ? 'border-amber-200 bg-amber-50/50 hover:border-amber-400 dark:border-amber-900 dark:bg-amber-950/15' : 'border-slate-200/80 hover:border-blue-300 hover:bg-blue-50/50 dark:border-slate-800 dark:hover:border-blue-800 dark:hover:bg-blue-950/20'}`}><div className="flex items-center justify-between gap-3"><span className={`truncate text-[12px] font-bold ${source.source_type === 'monitoring_signal' ? 'text-amber-700 dark:text-amber-300' : 'text-[#007aff]'}`}>{source.title || source.url}</span><span className="shrink-0 rounded-full bg-white/70 px-2 py-0.5 text-[10px] font-bold text-slate-500 dark:bg-slate-900/70 dark:text-slate-300">{source.source_type === 'monitoring_signal' ? '监控轨' : `HTTP ${source.http_status ?? '—'}`}</span></div>{source.content_excerpt && <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-slate-600 dark:text-slate-300">{source.content_excerpt}</p>}</a>)}</div></div>}{loadingReports ? <div className="py-16 text-center text-[12px] text-slate-400">正在加载研究草稿…</div> : !latestReport ? <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-6 py-10 text-center dark:border-slate-700 dark:bg-slate-900/50"><Clock3 className="mx-auto mb-3 h-7 w-7 text-slate-300" /><h3 className="text-[14px] font-bold text-slate-600 dark:text-slate-300">{sources.length > 0 ? '证据读取完成，尚未生成研究草稿' : '研究尚未产出结果'}</h3><p className="mx-auto mt-2 max-w-sm text-[12px] leading-5 text-slate-500 dark:text-slate-400">开始任务后，系统会先载入指定供应商的监控证据，再检索并受控读取公开页面。</p></div> : <ReportDraftCard report={latestReport} claimCount={claimCount} />}</div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

const ReportDraftCard: React.FC<{report: ResearchReportRead; claimCount: number}> = ({report, claimCount}) => {
  const sections = (Object.keys(sectionMeta) as Array<keyof typeof sectionMeta>).map((key) => ({key, claims: report.draft[key], ...sectionMeta[key]}));
  return <div className="space-y-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><div className="mb-1 flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-400"><FileText className="h-3.5 w-3.5" />研究草稿 · {claimCount} 条结论</div><h3 className="text-xl font-black text-[#101d28] dark:text-white">{report.title}</h3></div><div className="flex flex-wrap items-center justify-end gap-2 text-[11px] font-bold">{report.model_version && <span className="rounded-full bg-blue-50 px-2.5 py-1 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300">远端模型 · {report.model_version}</span>}<span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-slate-600 dark:bg-slate-800 dark:text-slate-300"><Clock3 className="h-3.5 w-3.5" />待人工确认</span><span className="text-slate-400">{formatDate(report.created_at)}</span></div></div><div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-[11px] leading-5 text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" /><span>{report.draft.disclaimer}</span></div>{sections.map(({key, claims, label, tone, marker}) => claims.length > 0 && <div key={key} className="space-y-2"><h4 className={`flex items-center gap-2 text-[12px] font-extrabold ${tone}`}><span className={`h-1.5 w-1.5 rounded-full ${marker}`} />{label} <span className="font-mono text-[10px] opacity-60">{claims.length}</span></h4>{claims.map((claim) => <div key={claim.claim_id} className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3.5 dark:border-slate-800 dark:bg-slate-900/60"><div className="flex items-start justify-between gap-3"><p className="text-[13px] leading-6 text-[#101d28] dark:text-slate-200">{claim.text}</p>{claim.confidence !== null && <span className="shrink-0 font-mono text-[10px] text-slate-400">{claim.confidence}%</span>}</div><div className="mt-2 flex flex-wrap gap-1.5">{claim.citation_ids.map((citationId) => { const citation = report.draft.citations.find((item) => item.citation_id === citationId); return <span key={citationId} className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-bold ${citation?.verified ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' : 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300'}`}>{citation?.verified ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}引用 {citationId}</span>; })}</div></div>)}</div>)}<div className="border-t border-slate-200/80 pt-4 dark:border-slate-800"><h4 className="mb-2 text-[12px] font-extrabold text-slate-500 dark:text-slate-400">引用来源</h4><div className="space-y-2">{report.draft.citations.map((citation) => <a key={citation.citation_id} href={citation.url} target="_blank" rel="noreferrer" className="block rounded-xl border border-slate-200/80 p-3 transition hover:border-blue-300 hover:bg-blue-50/50 dark:border-slate-800 dark:hover:border-blue-800 dark:hover:bg-blue-950/20"><div className="flex items-center justify-between gap-3"><span className="truncate text-[12px] font-bold text-[#007aff]">{citation.url}</span><span className={`shrink-0 text-[10px] font-bold ${citation.verified ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>{citation.verified ? '已回验' : '未回验'}</span></div><p className="mt-1 line-clamp-2 text-[11px] leading-5 text-slate-500 dark:text-slate-400">“{citation.quote}”</p></a>)}</div></div></div>;
};

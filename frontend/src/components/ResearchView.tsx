import React, {useCallback, useEffect, useMemo, useState} from 'react';
import {motion} from 'motion/react';
import {CheckCircle2, Clock3, FileText, Plus, RefreshCw, Search, ShieldCheck, XCircle} from 'lucide-react';
import {api, type ResearchReportRead, type ResearchSourceRead, type ResearchTaskRead} from '../api';

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

export const ResearchView: React.FC<ResearchViewProps> = ({canCreate}) => {
  const [tasks, setTasks] = useState<ResearchTaskRead[]>([]);
  const [selectedTask, setSelectedTask] = useState<ResearchTaskRead | null>(null);
  const [reports, setReports] = useState<ResearchReportRead[]>([]);
  const [sources, setSources] = useState<ResearchSourceRead[]>([]);
  const [topic, setTopic] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadingReports, setLoadingReports] = useState(false);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  const refreshSelectedTask = useCallback(async (taskId: number) => {
    try {
      const next = await api.research.task(taskId);
      setTasks((current) => current.map((task) => task.id === taskId ? next : task));
      setSelectedTask((current) => current?.id === taskId ? next : current);
      if (next.status === 'succeeded') {
        void loadReports(taskId);
        void loadSources(taskId);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '研究任务状态刷新失败');
    }
  }, [loadReports, loadSources]);

  useEffect(() => { void loadTasks(); }, [loadTasks]);
  useEffect(() => {
    if (selectedTask) {
      void loadReports(selectedTask.id);
      void loadSources(selectedTask.id);
    } else {
      setReports([]);
      setSources([]);
    }
  }, [loadReports, loadSources, selectedTask]);
  useEffect(() => {
    if (!selectedTask || !['queued', 'running'].includes(selectedTask.status)) return undefined;
    const timer = window.setInterval(() => void refreshSelectedTask(selectedTask.id), 5000);
    return () => window.clearInterval(timer);
  }, [refreshSelectedTask, selectedTask]);

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = topic.trim();
    if (!trimmed || saving || !canCreate) return;
    setSaving(true);
    setError(null);
    try {
      const created = await api.research.createTask(trimmed);
      setTasks((current) => [created, ...current]);
      setSelectedTask(created);
      setTopic('');
      setReports([]);
      setSources([]);
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
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '研究任务启动失败');
    } finally {
      setStarting(false);
    }
  };

  const latestReport = reports[0] ?? null;
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
              return <button key={task.id} type="button" onClick={() => setSelectedTask(task)} className={`w-full rounded-xl border p-3 text-left transition ${active ? 'border-blue-300 bg-blue-50/70 shadow-sm dark:border-blue-800 dark:bg-blue-950/30' : 'border-transparent hover:border-slate-200 hover:bg-slate-50 dark:hover:border-slate-700 dark:hover:bg-slate-900'}`}>
                <div className="mb-1.5 flex items-start justify-between gap-2"><span className="line-clamp-2 text-[13px] font-bold text-[#101d28] dark:text-slate-100">{task.topic}</span><span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${meta.className}`}>{meta.label}</span></div>
                <div className="flex items-center justify-between text-[10px] text-slate-400"><span>#{task.id} · 自动发现信源</span><span>{formatDate(task.created_at)}</span></div>
              </button>;
            })}
          </div>
        </section>

        <section className="min-h-[560px] rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-[#101d28]">
          {!selectedTask ? <div className="flex min-h-[560px] flex-col items-center justify-center px-6 text-center"><div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50 text-[#007aff] dark:bg-blue-950/40"><FileText className="h-7 w-7" /></div><h2 className="text-[16px] font-extrabold text-[#101d28] dark:text-white">选择一项研究任务</h2><p className="mt-2 max-w-xs text-[12px] leading-5 text-slate-500 dark:text-slate-400">任务完成后，带引用的报告草稿会显示在这里。</p></div> : (
            <div>
              <div className="flex flex-col gap-3 border-b border-slate-200/80 p-5 sm:flex-row sm:items-start sm:justify-between dark:border-slate-800"><div><div className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-[#007aff]"><span>任务 #{selectedTask.id}</span><span className="text-slate-300">/</span><span>手动研究</span>{['queued', 'running'].includes(selectedTask.status) && <span className="inline-flex items-center gap-1 text-slate-400"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[#007aff]" />每 5 秒刷新</span>}</div><h2 className="text-xl font-black text-[#101d28] dark:text-white">{selectedTask.topic}</h2><p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">创建于 {formatDate(selectedTask.created_at)} · 尝试 {selectedTask.attempts} 次{selectedTask.cancel_requested_at ? ' · 已请求取消' : ''}</p><div className="mt-3 flex flex-wrap gap-1.5 text-[10px] font-semibold text-slate-500 dark:text-slate-400"><span className="rounded-md bg-slate-100 px-2 py-1 dark:bg-slate-800">查询 {selectedTask.search_queries_used}/{budgetLimit(selectedTask, 'max_queries')}</span><span className="rounded-md bg-slate-100 px-2 py-1 dark:bg-slate-800">结果 {selectedTask.search_results_used}/{budgetLimit(selectedTask, 'max_results')}</span><span className="rounded-md bg-slate-100 px-2 py-1 dark:bg-slate-800">Token {(selectedTask.input_tokens_used + selectedTask.output_tokens_used).toLocaleString()}/{budgetLimit(selectedTask, 'max_input_tokens')}+{budgetLimit(selectedTask, 'max_output_tokens')}</span>{selectedTask.current_step && <span className="rounded-md bg-blue-50 px-2 py-1 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300">当前步骤：{selectedTask.current_step}</span>}</div></div><div className="flex flex-wrap items-center gap-2"><span className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${taskStatus[selectedTask.status].className}`}>{taskStatus[selectedTask.status].label}</span><span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-bold text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">草稿仅供参考</span>{selectedTask.status === 'queued' && <button type="button" onClick={() => void handleStart()} disabled={starting || Boolean(selectedTask.execution_requested_at)} className="rounded-lg bg-[#007aff] px-2.5 py-1.5 text-[11px] font-bold text-white transition hover:bg-[#0062cc] disabled:cursor-not-allowed disabled:opacity-50">{starting ? '请求中…' : selectedTask.execution_requested_at ? '等待 Worker' : '开始研究'}</button>}{['queued', 'running'].includes(selectedTask.status) && <button type="button" onClick={() => void handleCancel()} disabled={cancelling || Boolean(selectedTask.cancel_requested_at)} className="rounded-lg border border-red-200 px-2.5 py-1.5 text-[11px] font-bold text-red-700 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950/30">{cancelling || selectedTask.cancel_requested_at ? '取消中…' : '取消任务'}</button>}</div></div>
              <div className="space-y-5 p-5">{sources.length > 0 && <div><div className="mb-2 flex items-center justify-between"><h3 className="text-[13px] font-extrabold text-[#101d28] dark:text-white">自动发现的公开来源</h3><span className="text-[10px] font-bold text-slate-400">{sources.length} 个已读取</span></div><div className="space-y-2">{sources.map((source) => <a key={source.id} href={source.url} target="_blank" rel="noreferrer" className="block rounded-xl border border-slate-200/80 p-3 transition hover:border-blue-300 hover:bg-blue-50/50 dark:border-slate-800 dark:hover:border-blue-800 dark:hover:bg-blue-950/20"><div className="flex items-center justify-between gap-3"><span className="truncate text-[12px] font-bold text-[#007aff]">{source.title || source.url}</span><span className="shrink-0 text-[10px] text-slate-400">HTTP {source.http_status ?? '—'}</span></div>{source.content_excerpt && <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-slate-500 dark:text-slate-400">{source.content_excerpt}</p>}</a>)}</div></div>}{loadingReports ? <div className="py-16 text-center text-[12px] text-slate-400">正在加载研究草稿…</div> : !latestReport ? <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-6 py-10 text-center dark:border-slate-700 dark:bg-slate-900/50"><Clock3 className="mx-auto mb-3 h-7 w-7 text-slate-300" /><h3 className="text-[14px] font-bold text-slate-600 dark:text-slate-300">{sources.length > 0 ? '信源读取完成，尚未生成研究草稿' : '研究尚未产出结果'}</h3><p className="mx-auto mt-2 max-w-sm text-[12px] leading-5 text-slate-400">系统会按主题自动检索并受控读取公开页面；模型报告生成仍未启用。</p></div> : <ReportDraftCard report={latestReport} claimCount={claimCount} />}</div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

const ReportDraftCard: React.FC<{report: ResearchReportRead; claimCount: number}> = ({report, claimCount}) => {
  const sections = (Object.keys(sectionMeta) as Array<keyof typeof sectionMeta>).map((key) => ({key, claims: report.draft[key], ...sectionMeta[key]}));
  return <div className="space-y-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><div className="mb-1 flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-400"><FileText className="h-3.5 w-3.5" />研究草稿 · {claimCount} 条结论</div><h3 className="text-xl font-black text-[#101d28] dark:text-white">{report.title}</h3></div><div className="flex items-center gap-2 text-[11px] font-bold"><span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-slate-600 dark:bg-slate-800 dark:text-slate-300"><Clock3 className="h-3.5 w-3.5" />待人工确认</span><span className="text-slate-400">{formatDate(report.created_at)}</span></div></div><div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-[11px] leading-5 text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" /><span>{report.draft.disclaimer}</span></div>{sections.map(({key, claims, label, tone, marker}) => claims.length > 0 && <div key={key} className="space-y-2"><h4 className={`flex items-center gap-2 text-[12px] font-extrabold ${tone}`}><span className={`h-1.5 w-1.5 rounded-full ${marker}`} />{label} <span className="font-mono text-[10px] opacity-60">{claims.length}</span></h4>{claims.map((claim) => <div key={claim.claim_id} className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3.5 dark:border-slate-800 dark:bg-slate-900/60"><div className="flex items-start justify-between gap-3"><p className="text-[13px] leading-6 text-[#101d28] dark:text-slate-200">{claim.text}</p>{claim.confidence !== null && <span className="shrink-0 font-mono text-[10px] text-slate-400">{claim.confidence}%</span>}</div><div className="mt-2 flex flex-wrap gap-1.5">{claim.citation_ids.map((citationId) => { const citation = report.draft.citations.find((item) => item.citation_id === citationId); return <span key={citationId} className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-bold ${citation?.verified ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' : 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300'}`}>{citation?.verified ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}引用 {citationId}</span>; })}</div></div>)}</div>)}<div className="border-t border-slate-200/80 pt-4 dark:border-slate-800"><h4 className="mb-2 text-[12px] font-extrabold text-slate-500 dark:text-slate-400">引用来源</h4><div className="space-y-2">{report.draft.citations.map((citation) => <a key={citation.citation_id} href={citation.url} target="_blank" rel="noreferrer" className="block rounded-xl border border-slate-200/80 p-3 transition hover:border-blue-300 hover:bg-blue-50/50 dark:border-slate-800 dark:hover:border-blue-800 dark:hover:bg-blue-950/20"><div className="flex items-center justify-between gap-3"><span className="truncate text-[12px] font-bold text-[#007aff]">{citation.url}</span><span className={`shrink-0 text-[10px] font-bold ${citation.verified ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>{citation.verified ? '已回验' : '未回验'}</span></div><p className="mt-1 line-clamp-2 text-[11px] leading-5 text-slate-500 dark:text-slate-400">“{citation.quote}”</p></a>)}</div></div></div>;
};

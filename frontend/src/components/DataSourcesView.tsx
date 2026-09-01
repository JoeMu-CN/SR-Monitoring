import React, {useState} from 'react';
import {motion} from 'motion/react';
import {AlertTriangle, X} from 'lucide-react';
import {Link} from 'react-router-dom';
import {api, type DataSourceWritePayload} from '../api';
import {sourceSignalsPath} from '../routes';
import type {DataSource} from '../types';

interface DataSourcesViewProps {
  dataSources: DataSource[];
  role: 'viewer' | 'admin';
  onUpdateSource: (id: string, payload: Partial<DataSourceWritePayload>) => Promise<void>;
  onRefreshSources: () => Promise<void>;
  // 草稿箱、新增数据源、立即全量同步已下线：平台不再开放人工接入新数据源，
  // 但保留"编辑现有数据源"（API Key / 调度周期 / 适配器等配置项）+ 启停 + 修改日志审计。
}

const adapterTemplate = {
  format: 'json',
  request: {
    method: 'GET', url: 'https://official.example/api/events', params: {}, headers: {},
    timeout_seconds: 15, max_response_bytes: 10485760,
  },
  items_path: 'data.items',
  mapping: {
    external_id: 'id', title: 'title', content: 'description',
    url: 'official_url', published_at: 'published_at',
  },
  fingerprint_fields: ['external_id', 'title', 'published_at'],
  max_items: 1000,
};

const emptyForm: DataSourceWritePayload = {
  code: '', name: '', source_type: 'official_api', credibility: 90,
  schedule: '*/30 * * * *', endpoint_url: null, auth_type: 'none',
  login_config: {}, credential_ref: null, description: null,
  adapter_config: adapterTemplate, enabled: false, signal_validity_days: null,
};

export const DataSourcesView: React.FC<DataSourcesViewProps> = ({
  dataSources, role, onUpdateSource, onRefreshSources,
}) => {
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [warningDismissed, setWarningDismissed] = useState(false);
  const [showAudit, setShowAudit] = useState(false);
  const [auditText, setAuditText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [runAllLoading, setRunAllLoading] = useState(false);
  const [runAllMsg, setRunAllMsg] = useState<{type: 'ok' | 'err'; text: string} | null>(null);

  // 编辑表单状态
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<DataSourceWritePayload>(emptyForm);
  const [adapterText, setAdapterText] = useState(JSON.stringify(adapterTemplate, null, 2));
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingStatus, setEditingStatus] = useState<DataSource['adapterStatus']>('draft');
  const [apiKeyInput, setApiKeyInput] = useState('');
  const [editingKeyHint, setEditingKeyHint] = useState<string | null>(null);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [previewText, setPreviewText] = useState('');

  const update = (key: keyof DataSourceWritePayload, value: unknown) => {
    setForm((current) => ({...current, [key]: value}));
  };

  const updateTycConfig = (key: 'daily_limit' | 'monthly_limit', value: number) => {
    setForm((current) => ({
      ...current,
      login_config: {...current.login_config, [key]: value},
    }));
  };

  const openEdit = (source: DataSource) => {
    setEditingId(source.id);
    setEditingStatus(source.adapterStatus);
    setForm({
      code: source.code, name: source.name, source_type: source.type,
      credibility: source.credibility, schedule: source.schedule,
      endpoint_url: source.endpointUrl, auth_type: source.authType,
      login_config: source.loginConfig, credential_ref: source.credentialRef,
      description: source.description, adapter_config: source.adapterConfig,
      enabled: source.enabled, signal_validity_days: source.signalValidityDays,
    });
    setAdapterText(Object.keys(source.adapterConfig).length
      ? JSON.stringify(source.adapterConfig, null, 2) : '');
    setPreviewText('');
    setApiKeyInput('');
    setEditingKeyHint(source.apiKeyHint);
    setError(null);
    setShowForm(true);
  };

  const parseAdapter = (): Record<string, unknown> | null => {
    if (!adapterText.trim()) return null;
    const parsed: unknown = JSON.parse(adapterText);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error('适配器配置必须是 JSON 对象');
    }
    return parsed as Record<string, unknown>;
  };

  const preview = async () => {
    setError(null);
    setIsPreviewing(true);
    try {
      const adapterConfig = parseAdapter();
      if (!adapterConfig) throw new Error('请先填写适配器配置');
      if (!['none', 'api_key', 'bearer'].includes(form.auth_type ?? 'none')) {
        throw new Error('声明式适配器当前仅支持无需认证、API Key 和 Bearer Token');
      }
      const result = await api.previewSource({
        source_code: form.code || 'preview-source', adapter_config: adapterConfig,
        auth_type: (form.auth_type ?? 'none') as 'none' | 'api_key' | 'bearer',
        credential_ref: form.credential_ref, login_config: form.login_config,
      });
      setPreviewText(JSON.stringify(result, null, 2));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '实时预览失败');
    } finally {
      setIsPreviewing(false);
    }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      const isExternalTool = form.source_type === 'external_tool';
      const adapterConfig = isExternalTool ? null : parseAdapter();
      const payload = {
        ...form,
        schedule: isExternalTool ? null : form.schedule,
        adapter_config: adapterConfig,
        api_key: apiKeyInput.trim() || undefined,
      };
      if (editingId) {
        const {code: _code, adapter_config: _adapterConfig, api_key: _apiKey, ...rest} = payload;
        const changes = adapterConfig ? {...rest, adapter_config: adapterConfig} : rest;
        await onUpdateSource(editingId, changes);
        await onRefreshSources();
      }
      setShowForm(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '保存数据源失败');
    }
  };

  const handleRunAll = async () => {
    if (!window.confirm('确认全量刷新所有数据源？将依次触发各信源采集（跳过 5 分钟内已成功的），可能需要 1-3 分钟。')) return;
    setRunAllLoading(true);
    setRunAllMsg(null);
    try {
      const result = await api.runAllSources();
      const failedItems = result.items.filter((item) => item.status === 'failed' || item.status === 'error');
      setRunAllMsg({
        type: 'ok',
        text: `刷新完成：成功 ${result.succeeded}，失败 ${result.failed}，跳过 ${result.skipped}` + (failedItems.length ? `（${failedItems.map((item) => item.code).join('、')}）` : ''),
      });
      await onRefreshSources();
    } catch (caught) {
      setRunAllMsg({type: 'err', text: caught instanceof Error ? caught.message : '全量刷新失败'});
    }
    setRunAllLoading(false);
  };

  const handleToggleSource = async (source: DataSource) => {
    if (role !== 'admin' || togglingId !== null) return;
    const action = source.enabled ? '停用' : '启用';
    if (!window.confirm(`确认${action}“${source.name}”？`)) return;
    setTogglingId(source.id);
    setError(null);
    try {
      await api.updateSource(Number(source.id), {enabled: !source.enabled});
      await onRefreshSources();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `数据源${action}失败`);
    } finally {
      setTogglingId(null);
    }
  };

  const loadAudit = async () => {
    if (role !== 'admin') return;
    try {
      const result = await api.sourceAuditLogs();
      setAuditText(result.items.map((item) => (
        `${new Date(item.created_at).toLocaleString()} · 数据源 ID ${item.source_id ?? '-'} · ${item.action} · ${item.actor_role} · ${JSON.stringify(item.changes)}`
      )).join('\n') || '暂无修改记录');
      setShowAudit(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '审计日志加载失败');
    }
  };

  const delayedSources = dataSources.filter((source) => source.status !== 'normal');
  const totalItems = dataSources.reduce((total, source) => total + source.itemCount, 0);
  const isExternalForm = form.source_type === 'external_tool';
  const isTycForm = isExternalForm && form.code === 'tianyancha';
  const mayEnable = isExternalForm || editingStatus === 'builtin' || editingStatus === 'published';

  return (
    <div className="space-y-5 pb-20 lg:pb-8">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
        <div>
          <h1 className="text-xl font-black text-slate-900 dark:text-white tracking-tight lg:text-2xl">数据源清单</h1>
          <p className="text-xs text-[#424751] dark:text-slate-400 mt-0.5">
            监控多维数据 API 接口连通度、网络延迟及全网累计采集记录。
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span className="text-xs text-slate-500 mr-1">当前权限：{role === 'admin' ? '风险运营管理' : '只读'}</span>
          {role === 'admin' && (
            <button
              type="button"
              onClick={() => void loadAudit()}
              className="border border-[#c2c6d2] dark:border-slate-700 bg-white dark:bg-slate-900 rounded-lg px-3 py-2 text-xs font-bold hover:bg-[#ecf4ff] dark:hover:bg-slate-800"
            >
              修改日志
            </button>
          )}
        </div>
      </div>

      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}

      {!warningDismissed && delayedSources.length > 0 && (
        <motion.div
          initial={{opacity: 0, y: -6}}
          animate={{opacity: 1, y: 0}}
          className="flex items-start justify-between gap-3 rounded-2xl border border-amber-300/80 bg-amber-500/10 p-3.5 shadow-sm dark:border-amber-800/60 dark:bg-amber-950/40"
        >
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-[#D97706]"/>
            <div>
              <h4 className="text-[13px] font-bold text-amber-900 dark:text-amber-200">数据源运行提示</h4>
              <p className="mt-0.5 text-[12px] leading-relaxed text-amber-800/90 dark:text-amber-300/80">
                {delayedSources.map((source) => `${source.name}：${source.latency}`).join('；')}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setWarningDismissed(true)}
            aria-label="关闭警告"
            className="rounded-lg p-1 text-amber-700 hover:bg-amber-500/20 dark:text-amber-400"
          >
            <X className="h-4 w-4"/>
          </button>
        </motion.div>
      )}

      <div
        className="grid grid-cols-2 gap-3 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60 md:grid-cols-4"
        aria-label="数据源概览"
      >
        <div className="space-y-0.5">
          <div className="text-[11px] text-[#727782] dark:text-slate-400 font-medium">数据源接入数</div>
          <div className="text-xl font-bold font-mono text-[#101d28] dark:text-white">
            {dataSources.length} <span className="text-xs font-normal text-slate-500">个管道</span>
          </div>
        </div>
        <div className="space-y-0.5">
          <div className="text-[11px] text-[#727782] dark:text-slate-400 font-medium">正常运行 (Normal)</div>
          <div className="text-xl font-bold font-mono text-emerald-600 dark:text-emerald-400">
            {dataSources.filter((source) => source.status === 'normal').length} <span className="text-xs font-normal text-slate-500">个</span>
          </div>
        </div>
        <div className="space-y-0.5">
          <div className="text-[11px] text-[#727782] dark:text-slate-400 font-medium">异常/延迟节点</div>
          <div className="text-xl font-bold font-mono text-[#ba1a1a] dark:text-red-400">
            {dataSources.filter((source) => source.status === 'warning' || source.status === 'error').length} <span className="text-xs font-normal text-slate-500">个</span>
          </div>
        </div>
        <div className="space-y-0.5">
          <div className="text-[11px] text-[#727782] dark:text-slate-400 font-medium">全网累计记录数</div>
          <div className="text-xl font-bold font-mono text-[#004782] dark:text-blue-400">
            {totalItems.toLocaleString()} <span className="text-xs font-normal text-slate-500">条</span>
          </div>
        </div>
      </div>

      {role === 'admin' && (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <button
            type="button"
            onClick={() => void handleRunAll()}
            disabled={runAllLoading}
            className="flex items-center justify-center gap-1.5 rounded-xl border border-[#004782] bg-white/80 px-4 py-2 text-[12px] font-bold text-[#004782] shadow-sm backdrop-blur-md transition hover:bg-blue-50 disabled:opacity-50 dark:border-blue-400 dark:bg-slate-800/60 dark:text-blue-300"
          >
            <span className={`material-symbols-outlined text-[16px] ${runAllLoading ? 'animate-spin' : ''}`}>{runAllLoading ? 'sync' : 'refresh'}</span>
            {runAllLoading ? '全量刷新中…（串行采集，请稍候）' : '全量刷新所有数据源'}
          </button>
          {runAllMsg && (
            <span role="status" className={`text-[12px] rounded-lg px-3 py-1.5 ${runAllMsg.type === 'ok' ? 'text-emerald-700 bg-emerald-50 border border-emerald-200 dark:text-emerald-300 dark:bg-emerald-950/30 dark:border-emerald-900' : 'text-red-700 bg-red-50 border border-red-200 dark:text-red-300 dark:bg-red-950/30 dark:border-red-900'}`}>
              {runAllMsg.text}
            </span>
          )}
        </div>
      )}

      <div
        className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white/80 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60"
        role="list"
        aria-label="数据源列表"
      >
        <div className="hidden border-b border-slate-200/80 bg-slate-100/70 px-5 py-3 text-[12px] font-bold text-slate-600 dark:border-slate-800 dark:bg-slate-800/80 dark:text-slate-300 md:grid md:grid-cols-12 md:gap-4">
          <div className="col-span-4">数据源名称与类型</div>
          <div className="col-span-2">连通状态 / 延迟</div>
          <div className="col-span-2">最近同步时间</div>
          <div className="col-span-2">有效记录数</div>
          <div className="col-span-2 text-right">节点与操作</div>
        </div>
        <div className="divide-y divide-[#c2c6d2]/50 dark:divide-slate-800">
          {dataSources.length === 0 && (
            <div className="p-10 text-center text-sm text-slate-500">暂无数据源配置</div>
          )}
          {dataSources.map((source) => {
            const isExternalTool = source.type === 'external_tool';
            // 状态配色映射：disabled (灰停用) / running (蓝执行) / error (红失败) /
            //               warning (橙异常) / normal (绿正常)
            const statusStyle = ({
              disabled: { row: 'bg-slate-100/40 dark:bg-slate-800/30', icon: 'bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-400', pill: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300', dot: 'bg-slate-500/60', dotCore: 'bg-slate-600', ringDur: 2, pulse: 'bg-slate-400' },
              running: { row: 'bg-blue-50/40 dark:bg-blue-950/10', icon: 'bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300', pill: 'bg-blue-100 text-blue-700 dark:bg-blue-950/80 dark:text-blue-300', dot: 'bg-blue-500/60', dotCore: 'bg-blue-600', ringDur: 1.2, pulse: 'bg-blue-400' },
              error: { row: 'bg-red-50/30 dark:bg-red-950/10', icon: 'bg-red-100 text-[#ba1a1a] dark:bg-red-950 dark:text-red-300', pill: 'bg-red-100 text-[#ba1a1a] dark:bg-red-950/80 dark:text-red-300', dot: 'bg-red-500/60', dotCore: 'bg-[#ba1a1a]', ringDur: 1.2, pulse: 'bg-red-400' },
              warning: { row: 'bg-orange-50/40 dark:bg-orange-950/10', icon: 'bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300', pill: 'bg-orange-100 text-orange-700 dark:bg-orange-950/80 dark:text-orange-300', dot: 'bg-orange-500/60', dotCore: 'bg-orange-600', ringDur: 1.6, pulse: 'bg-orange-400' },
              normal: { row: '', icon: 'bg-[#ecf4ff] text-[#004782] dark:bg-slate-800 dark:text-blue-300', pill: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300', dot: 'bg-emerald-500/60', dotCore: 'bg-emerald-600', ringDur: 2, pulse: 'bg-emerald-400' },
            } as const)[source.status];
            const canToggle = source.adapterStatus === 'builtin' || source.adapterStatus === 'published' || isExternalTool;
            const isToggling = togglingId === source.id;
            const typeLabel = isExternalTool ? '按需外部核查' : source.type === 'official_api' ? '官方 API' : source.type.replaceAll('_', ' ');
            return (
              <motion.div
                key={source.id}
                role="listitem"
                className={`p-4 transition-colors sm:px-5 hover:bg-[#185fa5]/5 dark:hover:bg-slate-800/50 ${statusStyle.row}`}
              >
                <div className="grid grid-cols-1 md:grid-cols-12 gap-3 md:gap-4 items-center">
                  <div className="col-span-12 md:col-span-4 flex items-center gap-3 min-w-0">
                    <div className={`p-2.5 rounded-lg flex items-center justify-center shrink-0 ${statusStyle.icon}`}>
                      <span className="material-symbols-outlined text-[20px]">
                        {source.type.includes('api') || source.type.includes('API') || source.type.includes('接口') ? 'api' : 'database'}
                      </span>
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 min-w-0">
                        <h3 className="font-bold text-[14px] text-[#101d28] dark:text-white truncate">{source.name}</h3>
                        <span className="shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-slate-700">
                          {typeLabel}
                        </span>
                      </div>
                      <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
                        数据源 ID: <span className="font-mono font-bold">{source.id}</span> · 编码: <span className="font-mono">{source.code}</span>
                      </p>
                    </div>
                  </div>
                  <div className="col-span-6 md:col-span-2 flex items-center gap-2">
                    <span className={`text-[11px] font-bold px-2.5 py-1 rounded-full inline-flex items-center gap-1.5 ${statusStyle.pill}`}>
                      <span className="relative flex items-center justify-center w-2 h-2">
                        <motion.span
                          className={`absolute inline-flex h-full w-full rounded-full ${statusStyle.dot}`}
                          animate={{scale: [1, 2.2, 1], opacity: [0.8, 0, 0.8]}}
                          transition={{duration: statusStyle.ringDur, repeat: Infinity, ease: 'easeInOut'}}
                        />
                        <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${statusStyle.dotCore}`} />
                      </span>
                      {source.latency}
                    </span>
                  </div>
                  <div className="col-span-6 md:col-span-2 text-[12px] font-mono text-slate-700 dark:text-slate-300">
                    <span className="md:hidden text-slate-400 text-[11px] font-sans mr-1">同步:</span>
                    {source.lastSyncTime}
                  </div>
                  <div className="col-span-6 md:col-span-2 text-[13px] font-mono font-bold text-[#004782] dark:text-blue-400">
                    <span className="md:hidden text-slate-400 text-[11px] font-sans font-normal mr-1">有效:</span>
                    <Link
                      to={sourceSignalsPath(source.id, 'valid')}
                      aria-label={`${source.name} 有效记录 ${source.validSignalCount} 条`}
                      className="rounded-sm underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
                    >
                      {source.validSignalCount.toLocaleString()}
                    </Link>{' '}
                    <span className="text-[11px] font-normal text-slate-500">条</span>
                    {source.totalSignalCount > source.validSignalCount && (
                      <Link
                        to={sourceSignalsPath(source.id, 'all')}
                        aria-label={`${source.name} 全部历史记录 ${source.totalSignalCount} 条`}
                        className="ml-1.5 rounded-sm text-[10px] font-mono font-normal text-slate-500 underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-slate-400"
                        title="累计历史存量（含已过期）"
                      >
                        （累计 {source.totalSignalCount.toLocaleString()}）
                      </Link>
                    )}
                    {source.signalValidityDays != null && (
                      <span className="ml-1 text-[10px] font-normal text-slate-400" title="信息记录有效期">
                        有效期 {source.signalValidityDays} 天
                      </span>
                    )}
                  </div>
                  <div className="col-span-6 md:col-span-2 flex flex-wrap items-center justify-end gap-2 text-right">
                    <span className="text-[11px] font-mono text-slate-500 hidden xl:inline-block">
                      {isExternalTool
                        ? (source.apiKeyConfigured ? (source.apiKeyHint ? `运行密钥 ${source.apiKeyHint}` : '运行密钥已配置') : '运行密钥未配置')
                        : source.code === 'manual-json'
                          ? '非联网数据源'
                          : !source.endpointUrl
                            ? '等待配置地址'
                            : source.accessLastHttpStatus
                              ? `HTTP ${source.accessLastHttpStatus}`
                              : '域名保护已启用'}
                    </span>
                    {canToggle && (
                      <button
                        type="button"
                        onClick={() => void handleToggleSource(source)}
                        disabled={role !== 'admin' || togglingId !== null}
                        aria-pressed={source.enabled}
                        className={`px-2.5 py-1 text-[11px] font-bold rounded-lg border disabled:opacity-40 ${source.enabled ? 'border-amber-300 text-amber-800 dark:text-amber-300' : 'border-emerald-300 text-emerald-800 dark:text-emerald-300'}`}
                      >
                        {isToggling ? '处理中...' : source.enabled ? '停用' : '启用'}
                      </button>
                    )}
                    {role === 'admin' && (
                      <button
                        type="button"
                        onClick={() => openEdit(source)}
                        className="px-2.5 py-1 text-[11px] font-bold rounded-lg border border-[#c2c6d2] dark:border-slate-700 text-[#004782] dark:text-blue-300 hover:bg-[#ecf4ff] dark:hover:bg-slate-800 transition-colors"
                        title="编辑数据源配置（API Key / 调度周期 / 适配器）"
                      >
                        编辑
                      </button>
                    )}
                  </div>
                </div>
              </motion.div>
            );
          })}
        </div>
      </div>

      {showForm && editingId && (
        <div role="dialog" aria-modal="true" className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4">
          <form onSubmit={(event) => void submit(event)} className="bg-white rounded-2xl p-6 w-full max-w-3xl space-y-4 shadow-xl max-h-[90vh] overflow-y-auto">
            <div className="flex justify-between">
              <h2 className="text-lg font-bold">编辑数据源 · ID {editingId}</h2>
              <button type="button" onClick={() => setShowForm(false)} className="text-slate-400 hover:text-slate-600">关闭</button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="text-xs font-bold">编码
                <input required disabled value={form.code} className="mt-1 w-full border rounded-lg p-2 disabled:bg-slate-100 disabled:text-slate-500" />
              </label>
              <label className="text-xs font-bold">名称
                <input required value={form.name} onChange={(event) => update('name', event.target.value)} className="mt-1 w-full border rounded-lg p-2" />
              </label>
              <label className="text-xs font-bold">类型
                <input required value={form.source_type} onChange={(event) => update('source_type', event.target.value)} className="mt-1 w-full border rounded-lg p-2" />
              </label>
              <label className="text-xs font-bold">可信度
                <input type="number" min="0" max="100" required value={form.credibility} onChange={(event) => update('credibility', Number(event.target.value))} className="mt-1 w-full border rounded-lg p-2" />
              </label>
              <label className="text-xs font-bold sm:col-span-2">官方接口 URL
                <input type="url" value={form.endpoint_url ?? ''} onChange={(event) => update('endpoint_url', event.target.value || null)} className="mt-1 w-full border rounded-lg p-2" />
              </label>
              <label className="text-xs font-bold">{isExternalForm ? '调用方式' : '调度 cron'}
                <input disabled={isExternalForm} value={isExternalForm ? '按需调用' : form.schedule ?? ''} onChange={(event) => update('schedule', event.target.value || null)} className="mt-1 w-full border rounded-lg p-2 font-mono disabled:bg-slate-100 disabled:text-slate-600" />
              </label>
              <label className="text-xs font-bold" title="信号自发生起 N 天内有效；留空=永久有效。过期后仅留库，不再计入有效记录、风险提醒按该时长失效">
                信息记录有效期（天）
                <input type="number" min="1" max="3650" placeholder="留空=永久有效" value={form.signal_validity_days ?? ''} onChange={(event) => update('signal_validity_days', event.target.value === '' ? null : Number(event.target.value))} className="mt-1 w-full border rounded-lg p-2" />
              </label>
              <label className="text-xs font-bold">认证方式
                <select value={form.auth_type} onChange={(event) => update('auth_type', event.target.value)} className="mt-1 w-full border rounded-lg p-2">
                  <option value="none">无需认证</option>
                  <option value="api_key">API Key Header</option>
                  <option value="bearer">Bearer Token</option>
                </select>
              </label>
              {!isExternalForm && (
                <label className="text-xs font-bold">凭据引用
                  <input value={form.credential_ref ?? ''} onChange={(event) => update('credential_ref', event.target.value || null)} placeholder="env:SOURCE_API_KEY" className="mt-1 w-full border rounded-lg p-2 font-mono" />
                </label>
              )}
              {!isExternalForm && (
                <label className="text-xs font-bold">API Key 请求头名
                  <input value={String(form.login_config.header_name ?? '')} onChange={(event) => update('login_config', event.target.value ? {header_name: event.target.value} : {})} placeholder="X-API-Key" className="mt-1 w-full border rounded-lg p-2" />
                </label>
              )}
              {isExternalForm && (
                <label className="text-xs font-bold sm:col-span-2">运行密钥 API Key
                  <input type="password" autoComplete="new-password" value={apiKeyInput} onChange={(event) => setApiKeyInput(event.target.value)} placeholder={editingKeyHint ? `已配置（${editingKeyHint}），留空保持不变` : '输入天眼查 API Key（tyc_ 开头）'} className="mt-1 w-full border rounded-lg p-2 font-mono" />
                </label>
              )}
              {isTycForm && (
                <label className="text-xs font-bold">每日调用上限
                  <input type="number" min="1" max="1000" value={String(form.login_config.daily_limit ?? 80)} onChange={(event) => updateTycConfig('daily_limit', Number(event.target.value))} className="mt-1 w-full border rounded-lg p-2" />
                </label>
              )}
              {isTycForm && (
                <label className="text-xs font-bold">每月调用上限
                  <input type="number" min="1" max="10000" value={String(form.login_config.monthly_limit ?? 900)} onChange={(event) => updateTycConfig('monthly_limit', Number(event.target.value))} className="mt-1 w-full border rounded-lg p-2" />
                </label>
              )}
              <label className="text-xs font-bold sm:col-span-2">说明
                <textarea value={form.description ?? ''} onChange={(event) => update('description', event.target.value || null)} className="mt-1 w-full border rounded-lg p-2" />
              </label>
              {!isExternalForm && (
                <label className="text-xs font-bold sm:col-span-2">声明式适配器 JSON
                  <textarea required={editingStatus !== 'builtin'} rows={12} value={adapterText} onChange={(event) => setAdapterText(event.target.value)} className="mt-1 w-full border rounded-lg p-3 font-mono text-xs" />
                </label>
              )}
            </div>
            {isExternalForm && (
              <p className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900">运行密钥加密保存在服务器数据库中，控制台仅显示末四位，保存后不回传明文；启用/停用由下方开关统一控制。</p>
            )}
            <label className="text-xs font-bold flex items-center gap-2">
              <input type="checkbox" disabled={!mayEnable} checked={form.enabled && mayEnable} onChange={(event) => update('enabled', event.target.checked)} />
              {isExternalForm ? '启用按需核查' : '启用正式采集（仅已发布或内置适配器可用）'}
            </label>
            {previewText && <pre className="bg-slate-50 border rounded-lg p-3 text-xs whitespace-pre-wrap max-h-56 overflow-auto">{previewText}</pre>}
            <div className="flex justify-end gap-2">
              {!isExternalForm && (
                <button type="button" onClick={() => void preview()} disabled={isPreviewing} className="border border-emerald-300 text-emerald-800 rounded-lg px-4 py-2 font-bold">
                  {isPreviewing ? '联网查询中...' : '实时联网预览'}
                </button>
              )}
              <button type="button" onClick={() => setShowForm(false)} className="border rounded-lg px-4 py-2">取消</button>
              <button type="submit" className="bg-[#004782] text-white rounded-lg px-4 py-2 font-bold">保存配置</button>
            </div>
          </form>
        </div>
      )}

      {showAudit && (
        <div role="dialog" aria-modal="true" className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl p-6 w-full max-w-3xl space-y-3 shadow-xl">
            <div className="flex justify-between">
              <h2 className="text-lg font-bold">数据源修改日志</h2>
              <button type="button" onClick={() => setShowAudit(false)}>关闭</button>
            </div>
            <pre className="bg-slate-50 rounded-lg p-4 text-xs whitespace-pre-wrap max-h-[60vh] overflow-auto">{auditText}</pre>
          </div>
        </div>
      )}
    </div>
  );
};

import React, {useState} from 'react';
import {motion} from 'motion/react';
import {api, type DataSourceWritePayload} from '../api';
import type {DataSource} from '../types';

interface DataSourcesViewProps {
  dataSources: DataSource[];
  onTriggerSync: () => Promise<void>;
  role: 'viewer' | 'admin';
  onRoleChange: (role: 'viewer' | 'admin') => void;
  onCreateSource: (payload: DataSourceWritePayload) => Promise<void>;
  onUpdateSource: (id: string, payload: Partial<DataSourceWritePayload>) => Promise<void>;
  onDeleteSource: (id: string) => Promise<void>;
  onRefreshSources: () => Promise<void>;
  onOpenSourceAgent: (draftId?: number) => void;
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
  adapter_config: adapterTemplate, enabled: false,
};

export const DataSourcesView: React.FC<DataSourcesViewProps> = ({
  dataSources, onTriggerSync, role, onRoleChange, onCreateSource, onUpdateSource,
  onDeleteSource, onRefreshSources, onOpenSourceAgent,
}) => {
  const [isSyncing, setIsSyncing] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [form, setForm] = useState<DataSourceWritePayload>(emptyForm);
  const [adapterText, setAdapterText] = useState(JSON.stringify(adapterTemplate, null, 2));
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingStatus, setEditingStatus] = useState<DataSource['adapterStatus']>('draft');
  const [showForm, setShowForm] = useState(false);
  const [showAudit, setShowAudit] = useState(false);
  const [showDraftBox, setShowDraftBox] = useState(false);
  const [draftItems, setDraftItems] = useState<Awaited<ReturnType<typeof api.sourceOnboardingDrafts>>['items']>([]);
  const [isLoadingDrafts, setIsLoadingDrafts] = useState(false);
  const [auditText, setAuditText] = useState('');
  const [previewText, setPreviewText] = useState('');
  const [error, setError] = useState<string | null>(null);

  const update = (key: keyof DataSourceWritePayload, value: unknown) => {
    setForm((current) => ({...current, [key]: value}));
  };

  const openCreate = () => {
    setEditingId(null);
    setEditingStatus('draft');
    setForm({...emptyForm, adapter_config: {...adapterTemplate}});
    setAdapterText(JSON.stringify(adapterTemplate, null, 2));
    setPreviewText('');
    setError(null);
    setShowForm(true);
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
      enabled: source.enabled,
    });
    setAdapterText(Object.keys(source.adapterConfig).length
      ? JSON.stringify(source.adapterConfig, null, 2) : '');
    setPreviewText('');
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
      };
      if (editingId) {
        const {code: _code, adapter_config: _adapterConfig, ...rest} = payload;
        const changes = adapterConfig ? {...rest, adapter_config: adapterConfig} : rest;
        await onUpdateSource(editingId, changes);
      } else {
        await onCreateSource(payload);
      }
      setShowForm(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '保存数据源失败');
    }
  };

  const publish = async (source: DataSource) => {
    if (!window.confirm(`将实时验证并发布“${source.name}”，发布后仍保持停用。确认继续？`)) return;
    setError(null);
    try {
      await api.publishSource(Number(source.id));
      await onRefreshSources();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '适配器发布失败');
      await onRefreshSources().catch(() => undefined);
    }
  };

  const handleSyncClick = async () => {
    setIsSyncing(true);
    try { await onTriggerSync(); } finally { setIsSyncing(false); }
  };

  const handleTestConnection = async (source: DataSource) => {
    if (role !== 'admin' || !source.endpointUrl || source.type === 'external_tool' || testingId || source.accessStatus !== 'ready') return;
    setTestingId(source.id);
    setError(null);
    try {
      await api.runSource(Number(source.id));
      await onRefreshSources();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '数据源连通性测试失败');
      await onRefreshSources().catch(() => undefined);
    } finally {
      setTestingId(null);
    }
  };

  const handleToggleSource = async (source: DataSource) => {
    if (role !== 'admin' || togglingId || !['builtin', 'published'].includes(source.adapterStatus)) return;
    const action = source.enabled ? '停用' : '启用';
    if (!window.confirm(`确认${action}“${source.name}”？`)) return;
    setTogglingId(source.id);
    setError(null);
    try {
      await onUpdateSource(source.id, {enabled: !source.enabled});
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `数据源${action}失败`);
      await onRefreshSources().catch(() => undefined);
    } finally {
      setTogglingId(null);
    }
  };

  const loadAudit = async () => {
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

  const loadDraftBox = async () => {
    if (role !== 'admin') return;
    setIsLoadingDrafts(true);
    setError(null);
    try {
      const result = await api.sourceOnboardingDrafts();
      setDraftItems(result.items);
      setShowDraftBox(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '草稿箱加载失败');
    } finally {
      setIsLoadingDrafts(false);
    }
  };

  const deleteDraft = async (draftId: number) => {
    if (role !== 'admin') return;
    setError(null);
    try {
      await api.deleteSourceOnboardingDraft(draftId);
      setDraftItems((current) => current.filter((item) => item.draft_id !== draftId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '草稿删除失败');
    }
  };

  const isExternalForm = form.source_type === 'external_tool';
  const mayEnable = isExternalForm || editingStatus === 'builtin' || editingStatus === 'published';

  return (
    <div className="space-y-6 pb-20 lg:pb-8">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[#101d28] dark:text-white tracking-tight">数据源与同步状态</h1>
          <p className="text-xs text-[#424751] dark:text-slate-400 mt-0.5">监控多维数据 API 接口连通度、网络延迟、已拉取监管日志及全网缓存节点状态。</p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span className="text-xs text-slate-500 mr-1">当前角色：{role === 'admin' ? '管理员' : '只读用户'}</span>
          <button onClick={() => onRoleChange(role === 'admin' ? 'viewer' : 'admin')} className="border border-[#c2c6d2] dark:border-slate-700 bg-white dark:bg-slate-900 rounded-lg px-3 py-2 text-xs font-bold hover:bg-[#ecf4ff] dark:hover:bg-slate-800">切换为{role === 'admin' ? '只读' : '管理员'}模式</button>
          <button onClick={() => void loadDraftBox()} disabled={role !== 'admin' || isLoadingDrafts} className="border border-[#c2c6d2] bg-white text-[#004782] dark:bg-slate-900 dark:text-blue-300 rounded-lg px-3 py-2 text-xs font-bold hover:bg-[#ecf4ff] dark:hover:bg-slate-800 disabled:opacity-40 flex items-center gap-1.5"><span className="material-symbols-outlined text-[16px]">inventory_2</span>{isLoadingDrafts ? '加载草稿…' : '草稿箱'}</button>
          <button onClick={() => onOpenSourceAgent()} disabled={role !== 'admin'} className="border border-[#004782] bg-[#ecf4ff] text-[#004782] dark:bg-slate-950/30 dark:text-blue-300 rounded-lg px-3 py-2 text-xs font-bold hover:bg-[#d6e4f3] dark:hover:bg-slate-800 disabled:opacity-40 flex items-center gap-1.5"><span className="material-symbols-outlined text-[16px]">add_link</span>数据源接入助手</button>
          <button onClick={() => void loadAudit()} className="border border-[#c2c6d2] dark:border-slate-700 bg-white dark:bg-slate-900 rounded-lg px-3 py-2 text-xs font-bold hover:bg-[#ecf4ff] dark:hover:bg-slate-800">修改日志</button>
          <button onClick={() => void handleSyncClick()} disabled={isSyncing || role !== 'admin'} className="bg-[#004782] hover:bg-[#185fa5] text-white font-bold text-[13px] px-4 py-2 rounded-lg shadow-2xs transition-all flex items-center gap-2 disabled:opacity-50"><span className={`material-symbols-outlined text-[18px] ${isSyncing ? 'animate-spin' : ''}`}>sync</span>{isSyncing ? '全量数据同步中...' : '立即全量数据同步'}</button>
          <button onClick={openCreate} disabled={role !== 'admin'} className="bg-emerald-700 hover:bg-emerald-800 text-white font-bold text-[13px] px-4 py-2 rounded-lg disabled:opacity-40">新增数据源</button>
        </div>
      </div>

      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}

      {showDraftBox && (
        <section className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl shadow-2xs overflow-hidden" aria-labelledby="draft-box-title">
          <div className="px-5 py-4 border-b border-[#c2c6d2] dark:border-slate-800 flex items-center justify-between gap-3">
            <div><h2 id="draft-box-title" className="font-bold text-[#101d28] dark:text-white">新增数据源草稿箱</h2><p className="text-xs text-[#727782] mt-0.5">接入中、待发布和已发布待启用的数据源统一在此查看。</p></div>
            <button type="button" onClick={() => setShowDraftBox(false)} className="text-xs font-bold text-[#004782]">收起</button>
          </div>
          {draftItems.length === 0 ? (
            <div className="p-8 text-center text-sm text-[#727782]">草稿箱为空。开始新的逐项接入后，配置会自动保存到这里。</div>
          ) : (
            <div className="divide-y divide-[#c2c6d2]/60 dark:divide-slate-800">
              {draftItems.map((item) => (
                <div key={`${item.kind}-${item.draft_id ?? item.source_id}`} className="px-5 py-4 flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0"><div className="flex items-center gap-2"><h3 className="text-sm font-bold text-[#101d28] dark:text-white truncate">{item.title}</h3><span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${item.kind === 'in_progress' ? 'border-[#c2c6d2] bg-[#ecf4ff] text-[#004782]' : item.kind === 'adapter_draft' ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-emerald-200 bg-emerald-50 text-emerald-800'}`}>{item.kind === 'in_progress' ? '接入中' : item.kind === 'adapter_draft' ? '待发布' : '待启用'}</span></div><p className="mt-1 text-xs text-[#727782]">{item.detail}</p><p className="mt-1 text-[11px] font-mono text-[#727782]">{item.draft_id ? `草稿 ID ${item.draft_id}` : `数据源 ID ${item.source_id} · ${item.source_code}`} · {new Date(item.updated_at).toLocaleString()}</p></div>
                  {item.kind === 'in_progress' && item.draft_id && <div className="flex items-center gap-2"><button type="button" onClick={() => onOpenSourceAgent(item.draft_id ?? undefined)} className="border border-[#004782] rounded-lg px-3 py-2 text-xs font-bold text-[#004782] hover:bg-[#ecf4ff]">继续配置</button><button type="button" onClick={() => void deleteDraft(item.draft_id ?? 0)} className="border border-red-200 rounded-lg px-3 py-2 text-xs font-bold text-[#ba1a1a] hover:bg-red-50">删除</button></div>}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 shadow-2xs" aria-label="数据源概览">
        <div className="space-y-0.5"><div className="text-[11px] text-[#727782] dark:text-slate-400 font-medium">数据源接入数</div><div className="text-xl font-bold font-mono text-[#101d28] dark:text-white">{dataSources.length} <span className="text-xs font-normal text-slate-500">个管道</span></div></div>
        <div className="space-y-0.5"><div className="text-[11px] text-[#727782] dark:text-slate-400 font-medium">正常运行 (Normal)</div><div className="text-xl font-bold font-mono text-emerald-600 dark:text-emerald-400">{dataSources.filter((source) => source.status === 'normal').length} <span className="text-xs font-normal text-slate-500">个</span></div></div>
        <div className="space-y-0.5"><div className="text-[11px] text-[#727782] dark:text-slate-400 font-medium">异常/延迟节点</div><div className="text-xl font-bold font-mono text-[#ba1a1a] dark:text-red-400">{dataSources.filter((source) => source.status === 'warning' || source.status === 'error').length} <span className="text-xs font-normal text-slate-500">个</span></div></div>
        <div className="space-y-0.5"><div className="text-[11px] text-[#727782] dark:text-slate-400 font-medium">全网拉取监管记录</div><div className="text-xl font-bold font-mono text-[#004782] dark:text-blue-400">{dataSources.reduce((total, source) => total + source.itemCount, 0).toLocaleString()} <span className="text-xs font-normal text-slate-500">条</span></div></div>
      </div>

      <div className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl shadow-2xs overflow-hidden" role="list" aria-label="数据源列表">
        <div className="hidden md:grid grid-cols-12 gap-4 px-5 py-3 bg-[#f7f9ff] dark:bg-slate-800/60 border-b border-[#c2c6d2] dark:border-slate-800 text-[12px] font-bold text-[#424751] dark:text-slate-300">
          <div className="col-span-4">数据源名称与类型</div><div className="col-span-2">连通状态 / 延迟</div><div className="col-span-2">最近同步时间</div><div className="col-span-2">已拉取监管记录</div><div className="col-span-2 text-right">节点与操作</div>
        </div>
        <div className="divide-y divide-[#c2c6d2]/50 dark:divide-slate-800">
          {dataSources.length === 0 && <div className="p-10 text-center text-sm text-slate-500">暂无数据源配置</div>}
          {dataSources.map((source) => {
            const isWarning = source.status === 'warning' || source.status === 'error';
            const isExternalTool = source.type === 'external_tool';
            const canPublish = !isExternalTool && (source.adapterStatus === 'draft' || source.adapterStatus === 'invalid');
            const canToggle = source.adapterStatus === 'builtin' || source.adapterStatus === 'published';
            const isTesting = testingId === source.id;
            const isToggling = togglingId === source.id;
            const typeLabel = isExternalTool ? '按需外部核查' : source.type === 'official_api' ? '官方 API' : source.type.replaceAll('_', ' ');
            return (
              <motion.div key={source.id} role="listitem" className={`p-4 sm:px-5 transition-colors hover:bg-[#f7f9ff]/70 dark:hover:bg-slate-800/50 ${isWarning ? 'bg-red-50/20 dark:bg-red-950/10' : ''}`}>
                <div className="grid grid-cols-1 md:grid-cols-12 gap-3 md:gap-4 items-center">
                  <div className="col-span-12 md:col-span-4 flex items-center gap-3 min-w-0">
                    <div className={`p-2.5 rounded-lg flex items-center justify-center shrink-0 ${isWarning ? 'bg-red-100 text-[#ba1a1a] dark:bg-red-950 dark:text-red-300' : 'bg-[#ecf4ff] text-[#004782] dark:bg-slate-800 dark:text-blue-300'}`}><span className="material-symbols-outlined text-[20px]">{source.type.includes('api') || source.type.includes('API') || source.type.includes('接口') ? 'api' : 'database'}</span></div>
                    <div className="min-w-0"><div className="flex items-center gap-2 min-w-0"><h3 className="font-bold text-[14px] text-[#101d28] dark:text-white truncate">{source.name}</h3><span className="shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-slate-700">{typeLabel}</span></div><p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">数据源 ID: <span className="font-mono font-bold">{source.id}</span> · 编码: <span className="font-mono">{source.code}</span></p></div>
                  </div>
                  <div className="col-span-6 md:col-span-2 flex items-center gap-2"><span className={`text-[11px] font-bold px-2.5 py-1 rounded-full inline-flex items-center gap-1.5 ${isWarning ? 'bg-red-100 text-[#ba1a1a] dark:bg-red-950/80 dark:text-red-300' : 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300'}`}><span className="relative flex items-center justify-center w-2 h-2"><motion.span className={`absolute inline-flex h-full w-full rounded-full ${isWarning ? 'bg-red-500/60' : 'bg-emerald-500/60'}`} animate={{scale: [1, 2.2, 1], opacity: [0.8, 0, 0.8]}} transition={{duration: isWarning ? 1.2 : 2, repeat: Infinity, ease: 'easeInOut'}} /><span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${isWarning ? 'bg-[#ba1a1a]' : 'bg-emerald-600'}`} /></span>{source.latency}</span></div>
                  <div className="col-span-6 md:col-span-2 text-[12px] font-mono text-slate-700 dark:text-slate-300"><span className="md:hidden text-slate-400 text-[11px] font-sans mr-1">同步:</span>{source.lastSyncTime}</div>
                  <div className="col-span-6 md:col-span-2 text-[13px] font-mono font-bold text-[#004782] dark:text-blue-400"><span className="md:hidden text-slate-400 text-[11px] font-sans font-normal mr-1">记录:</span>{source.itemCount.toLocaleString()} <span className="text-[11px] font-normal text-slate-500">条</span></div>
                  <div className="col-span-6 md:col-span-2 flex flex-wrap items-center justify-end gap-2 text-right"><span className="text-[11px] font-mono text-slate-500 hidden xl:inline-block">{isExternalTool ? '按需外部工具' : source.code === 'manual-json' ? '非联网数据源' : !source.endpointUrl ? '等待配置地址' : source.accessLastHttpStatus ? `HTTP ${source.accessLastHttpStatus}` : '域名保护已启用'}</span>{canToggle && <button type="button" onClick={() => void handleToggleSource(source)} disabled={role !== 'admin' || togglingId !== null} aria-pressed={source.enabled} className={`px-2.5 py-1 text-[11px] font-bold rounded-lg border disabled:opacity-40 ${source.enabled ? 'border-amber-300 text-amber-800 dark:text-amber-300' : 'border-emerald-300 text-emerald-800 dark:text-emerald-300'}`}>{isToggling ? '处理中...' : source.enabled ? '停用' : '启用'}</button>}<button onClick={() => void handleTestConnection(source)} disabled={role !== 'admin' || !source.endpointUrl || isExternalTool || isTesting || source.accessStatus !== 'ready'} className="px-2.5 py-1 text-[11px] font-bold rounded-lg border border-[#c2c6d2] dark:border-slate-700 hover:bg-[#ecf4ff] dark:hover:bg-slate-800 text-[#004782] dark:text-blue-300 transition-colors flex items-center gap-1 disabled:opacity-40" title={source.accessStatus === 'ready' ? '触发一次采集并刷新状态' : source.latency}><span className={`material-symbols-outlined text-[14px] ${isTesting ? 'animate-spin' : ''}`}>{isTesting ? 'sync' : 'network_check'}</span><span>{isTesting ? '测速中...' : '测试连通'}</span></button><button onClick={() => openEdit(source)} disabled={role !== 'admin'} className="px-2.5 py-1 text-[11px] font-bold rounded-lg border border-[#c2c6d2] dark:border-slate-700 text-[#004782] dark:text-blue-300 disabled:opacity-40">编辑</button>{canPublish && <button onClick={() => void publish(source)} disabled={role !== 'admin'} className="px-2.5 py-1 text-[11px] font-bold rounded-lg border border-emerald-300 text-emerald-800 dark:text-emerald-300 disabled:opacity-40">发布</button>}<button onClick={() => { if (role === 'admin' && window.confirm(`确认删除“${source.name}”？`)) void onDeleteSource(source.id); }} disabled={role !== 'admin'} className="px-2.5 py-1 text-[11px] font-bold rounded-lg border border-red-200 text-red-700 dark:text-red-300 disabled:opacity-40">删除</button></div>
                </div>
              </motion.div>
            );
          })}
        </div>
      </div>

      {showForm && (
        <div role="dialog" aria-modal="true" className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4">
          <form onSubmit={(event) => void submit(event)} className="bg-white rounded-2xl p-6 w-full max-w-3xl space-y-4 shadow-xl max-h-[90vh] overflow-y-auto">
            <div className="flex justify-between"><h2 className="text-lg font-bold">{editingId ? `编辑数据源 · ID ${editingId}` : '新增数据源'}</h2><button type="button" onClick={() => setShowForm(false)}>关闭</button></div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="text-xs font-bold">编码<input required disabled={Boolean(editingId)} value={form.code} onChange={(event) => update('code', event.target.value)} className="mt-1 w-full border rounded-lg p-2" /></label>
              <label className="text-xs font-bold">名称<input required value={form.name} onChange={(event) => update('name', event.target.value)} className="mt-1 w-full border rounded-lg p-2" /></label>
              <label className="text-xs font-bold">类型<input required value={form.source_type} onChange={(event) => update('source_type', event.target.value)} className="mt-1 w-full border rounded-lg p-2" /></label>
              <label className="text-xs font-bold">可信度<input type="number" min="0" max="100" required value={form.credibility} onChange={(event) => update('credibility', Number(event.target.value))} className="mt-1 w-full border rounded-lg p-2" /></label>
              <label className="text-xs font-bold sm:col-span-2">官方接口 URL<input type="url" value={form.endpoint_url ?? ''} onChange={(event) => update('endpoint_url', event.target.value || null)} className="mt-1 w-full border rounded-lg p-2" /></label>
              <label className="text-xs font-bold">{isExternalForm ? '调用方式' : '调度 cron'}<input disabled={isExternalForm} value={isExternalForm ? '按需调用' : form.schedule ?? ''} onChange={(event) => update('schedule', event.target.value || null)} className="mt-1 w-full border rounded-lg p-2 font-mono disabled:bg-slate-100 disabled:text-slate-600" /></label>
              <label className="text-xs font-bold">认证方式<select value={form.auth_type} onChange={(event) => update('auth_type', event.target.value)} className="mt-1 w-full border rounded-lg p-2"><option value="none">无需认证</option><option value="api_key">API Key Header</option><option value="bearer">Bearer Token</option></select></label>
              <label className="text-xs font-bold">凭据引用<input value={form.credential_ref ?? ''} onChange={(event) => update('credential_ref', event.target.value || null)} placeholder="env:SOURCE_API_KEY" className="mt-1 w-full border rounded-lg p-2 font-mono" /></label>
              <label className="text-xs font-bold">API Key 请求头名<input value={String(form.login_config.header_name ?? '')} onChange={(event) => update('login_config', event.target.value ? {header_name: event.target.value} : {})} placeholder="X-API-Key" className="mt-1 w-full border rounded-lg p-2" /></label>
              <label className="text-xs font-bold sm:col-span-2">说明<textarea value={form.description ?? ''} onChange={(event) => update('description', event.target.value || null)} className="mt-1 w-full border rounded-lg p-2" /></label>
              {!isExternalForm && <label className="text-xs font-bold sm:col-span-2">声明式适配器 JSON<textarea required={editingStatus !== 'builtin'} rows={15} value={adapterText} onChange={(event) => setAdapterText(event.target.value)} className="mt-1 w-full border rounded-lg p-3 font-mono text-xs" /></label>}
            </div>
            {isExternalForm && <p className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900">运行密钥仅通过服务器环境变量注入，控制台只保存凭据引用，不保存或返回明文。</p>}
            <label className="text-xs font-bold flex items-center gap-2"><input type="checkbox" disabled={!mayEnable} checked={form.enabled && mayEnable} onChange={(event) => update('enabled', event.target.checked)} />{isExternalForm ? '启用按需核查' : '启用正式采集（仅已发布或内置适配器可用）'}</label>
            {previewText && <pre className="bg-slate-50 border rounded-lg p-3 text-xs whitespace-pre-wrap max-h-56 overflow-auto">{previewText}</pre>}
            <div className="flex justify-end gap-2">{!isExternalForm && <button type="button" onClick={() => void preview()} disabled={isPreviewing} className="border border-emerald-300 text-emerald-800 rounded-lg px-4 py-2 font-bold">{isPreviewing ? '联网查询中...' : '实时联网预览'}</button>}<button type="button" onClick={() => setShowForm(false)} className="border rounded-lg px-4 py-2">取消</button><button type="submit" className="bg-[#004782] text-white rounded-lg px-4 py-2 font-bold">{isExternalForm ? '保存配置' : '保存草稿'}</button></div>
          </form>
        </div>
      )}

      {showAudit && <div role="dialog" aria-modal="true" className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4"><div className="bg-white rounded-2xl p-6 w-full max-w-3xl space-y-3 shadow-xl"><div className="flex justify-between"><h2 className="text-lg font-bold">数据源修改日志</h2><button type="button" onClick={() => setShowAudit(false)}>关闭</button></div><pre className="bg-slate-50 rounded-lg p-4 text-xs whitespace-pre-wrap max-h-[60vh] overflow-auto">{auditText}</pre></div></div>}
    </div>
  );
};

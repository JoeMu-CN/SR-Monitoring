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
  onDeleteSource, onRefreshSources,
}) => {
  const [isSyncing, setIsSyncing] = useState(false);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [form, setForm] = useState<DataSourceWritePayload>(emptyForm);
  const [adapterText, setAdapterText] = useState(JSON.stringify(adapterTemplate, null, 2));
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingStatus, setEditingStatus] = useState<DataSource['adapterStatus']>('draft');
  const [showForm, setShowForm] = useState(false);
  const [showAudit, setShowAudit] = useState(false);
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
    }
  };

  const handleSyncClick = async () => {
    setIsSyncing(true);
    try { await onTriggerSync(); } finally { setIsSyncing(false); }
  };

  const loadAudit = async () => {
    try {
      const result = await api.sourceAuditLogs();
      setAuditText(result.items.map((item) => (
        `${new Date(item.created_at).toLocaleString()} · ${item.action} · ${item.actor_role} · ${JSON.stringify(item.changes)}`
      )).join('\n') || '暂无修改记录');
      setShowAudit(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '审计日志加载失败');
    }
  };

  const isExternalForm = form.source_type === 'external_tool';
  const mayEnable = isExternalForm || editingStatus === 'builtin' || editingStatus === 'published';

  return (
    <div className="space-y-6 pb-20 lg:pb-8">
      <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold text-[#101d28] dark:text-white tracking-tight">数据源控制台</h1>
          <p className="text-xs text-[#424751] dark:text-slate-400 mt-0.5">管理员可创建声明式适配器、实时联网预览并发布；新来源发布后仍默认停用。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-500">当前角色：{role === 'admin' ? '管理员' : '只读用户'}</span>
          <button onClick={() => onRoleChange(role === 'admin' ? 'viewer' : 'admin')} className="border border-slate-300 rounded-lg px-3 py-2 text-xs font-bold">切换为{role === 'admin' ? '只读' : '管理员'}模式</button>
          <button onClick={() => void loadAudit()} className="border border-slate-300 rounded-lg px-3 py-2 text-xs font-bold">修改日志</button>
          <button onClick={() => void handleSyncClick()} disabled={isSyncing || role !== 'admin'} className="bg-[#004782] text-white font-bold text-[13px] px-4 py-2 rounded-lg disabled:opacity-50">{isSyncing ? '数据同步中...' : '立即同步'}</button>
          <button onClick={openCreate} disabled={role !== 'admin'} className="bg-emerald-700 text-white font-bold text-[13px] px-4 py-2 rounded-lg disabled:opacity-40">新增数据源</button>
        </div>
      </div>

      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}

      <div className="border border-[#c2c6d2] dark:border-slate-800 rounded-xl overflow-hidden bg-white dark:bg-slate-900" role="list" aria-label="数据源列表">
        {dataSources.map((source) => {
          const isWarning = source.status === 'warning';
          const isExternalTool = source.type === 'external_tool';
          const canPublish = !isExternalTool && (source.adapterStatus === 'draft' || source.adapterStatus === 'invalid');
          return (
            <div key={source.id} role="listitem" className={`grid grid-cols-1 md:grid-cols-[minmax(220px,1.1fr)_minmax(0,2fr)_auto] gap-4 md:gap-6 items-center p-4 md:px-5 ${isWarning ? 'bg-red-50/40 dark:bg-red-950/20' : 'bg-white dark:bg-slate-900'} ${source.id !== dataSources[0]?.id ? 'border-t border-[#c2c6d2] dark:border-slate-800' : ''}`}>
              <div className="flex justify-between md:block items-start gap-3 min-w-0">
                <div className="min-w-0"><span className="text-[11px] font-bold text-[#727782]">{isExternalTool ? '按需外部核查' : source.type}</span><h3 className="font-bold text-[16px] truncate text-[#101d28] dark:text-white">{source.name}</h3><p className="text-[11px] text-slate-500 font-mono truncate">{source.code}</p></div>
                <span className={`shrink-0 text-[11px] font-bold px-2.5 py-0.5 rounded-full flex items-center gap-1.5 ${isWarning ? 'bg-red-100 text-[#ba1a1a] dark:bg-red-950/60 dark:text-red-300' : 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300'}`}><motion.span className={`inline-flex h-1.5 w-1.5 rounded-full ${isWarning ? 'bg-red-500' : 'bg-emerald-600'}`} animate={{opacity: [1, .4, 1]}} transition={{duration: 2, repeat: Infinity}} />{source.latency}</span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-[12px] min-w-0">
                <div className="flex justify-between gap-3 text-slate-500"><span>{isExternalTool ? '调用方式:' : '适配器:'}</span><span className="font-mono font-bold text-right text-[#101d28] dark:text-slate-200">{isExternalTool ? '按需调用' : `${source.adapterStatus} v${source.adapterVersion}`}</span></div>
                <div className="flex justify-between gap-3 text-slate-500"><span>{isExternalTool ? '运行模式:' : '调度周期:'}</span><span className="font-mono font-bold text-right text-[#101d28] dark:text-slate-200">{isExternalTool ? '用户触发' : source.schedule || '未设置'}</span></div>
                <div className="flex justify-between gap-3 text-slate-500"><span>凭据引用:</span><span className="font-mono text-right truncate text-[#101d28] dark:text-slate-200">{source.credentialRef || '无需凭据'}</span></div>
                <div className="flex justify-between gap-3 text-slate-500"><span>{isExternalTool ? '最近调用:' : '最近同步:'}</span><span className="font-mono text-right truncate text-[#101d28] dark:text-slate-200">{source.lastSyncTime}</span></div>
              </div>
              <div className="flex flex-wrap md:flex-col lg:flex-row gap-2 md:justify-self-end">
                <button onClick={() => openEdit(source)} disabled={role !== 'admin'} className="flex-1 border border-slate-300 rounded-lg py-2 text-xs font-bold disabled:opacity-40">编辑配置</button>
                {canPublish && <button onClick={() => void publish(source)} disabled={role !== 'admin'} className="border border-emerald-300 text-emerald-800 rounded-lg px-3 py-2 text-xs font-bold disabled:opacity-40">实时验证并发布</button>}
                <button onClick={() => { if (role === 'admin' && window.confirm(`确认删除“${source.name}”？`)) void onDeleteSource(source.id); }} disabled={role !== 'admin'} className="border border-red-200 text-red-700 rounded-lg px-3 py-2 text-xs font-bold disabled:opacity-40">删除</button>
              </div>
            </div>
          );
        })}
      </div>

      {showForm && (
        <div role="dialog" aria-modal="true" className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4">
          <form onSubmit={(event) => void submit(event)} className="bg-white rounded-2xl p-6 w-full max-w-3xl space-y-4 shadow-xl max-h-[90vh] overflow-y-auto">
            <div className="flex justify-between"><h2 className="text-lg font-bold">{editingId ? '编辑数据源' : '新增数据源'}</h2><button type="button" onClick={() => setShowForm(false)}>关闭</button></div>
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

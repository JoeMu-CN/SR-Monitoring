import React, {useState} from 'react';
import {motion} from 'motion/react';
import {AlertTriangle, X} from 'lucide-react';
import {Link} from 'react-router-dom';
import {api, VALIDITY_MODE_LABELS, type DataSourceWritePayload, type SourceValidityPolicy, type ValidityMode} from '../api';
import {sourceSignalsPath} from '../routes';
import {describeSourceSchedule} from '../sourceSchedule';
import type {DataSource} from '../types';
import type {MonitoringHealthSnapshot} from '../useMonitoringHealth';
import {MonitoringSourceFreshness} from './MonitoringHealthBanner';

/**
 * 列表重载的最窄副作用意图：单来源刷新/核查 2xx 后，监控健康（monitoring-health）
 * 需要立即重取，由 App 递增诊断刷新版本号实现；其他刷新入口不传意图，保持原语义。
 */
export interface RefreshSourcesIntent {
  readonly refreshMonitoringHealth: true;
}

interface DataSourcesViewProps {
  dataSources: DataSource[];
  role: 'viewer' | 'admin';
  onUpdateSource: (id: string, payload: Partial<DataSourceWritePayload>) => Promise<void>;
  onRefreshSources: (intent?: RefreshSourcesIntent) => Promise<void>;
  // 任务8 只读诊断：每来源最后成功/下次预期/失败超期原因；403 或失败时为 hidden/unknown，不渲染。
  monitoringHealth: MonitoringHealthSnapshot;
  // 草稿箱、新增信息源、立即全量同步已下线：平台不再开放人工接入新信息源，
  // 但保留"编辑现有信息源"（API Key / 调度周期 / 适配器等配置项）+ 启停 + 修改日志审计。
  // 调度器实况已迁移为独立 /scheduler 页面，本页不再承载入口、弹窗或健康刷新回调。
}

const emptyForm: DataSourceWritePayload = {
  code: '', name: '', source_type: 'official_api', credibility: 90,
  schedule: '*/30 * * * *', endpoint_url: null, auth_type: 'none',
  login_config: {}, credential_ref: null, description: null,
  enabled: false, signal_validity_days: null,
};

// W1-T1：认证方式仅保留两种可编辑值；历史值（bearer/basic/oauth2/custom 等）只读展示，
// 提交时省略 auth_type 键，由后端保持原值，避免静默改写既有配置。
const AUTH_TYPE_OPTIONS: ReadonlyArray<{value: 'none' | 'api_key'; label: string}> = [
  {value: 'none', label: '无需认证'},
  {value: 'api_key', label: 'API Key Header'},
];

// —— 展示层工具（只影响页面呈现，不改变后端存储值） ——

// 来源名称展示规范化：去掉半角/全角括号及其内容，避免括号里的技术标识挤占名称位置。
const displaySourceName = (name: string): string => {
  const stripped = name.replace(/[（(][^）)]*[）)]/g, '').replace(/\s+/g, ' ').trim();
  return stripped || name;
};

// 中文业务分类：按来源编码关键词优先匹配，未知编码回退来源类型或通用兜底。
const SOURCE_CODE_CATEGORY_RULES: ReadonlyArray<readonly [readonly string[], string]> = [
  [['tianyancha'], '主体核查'],
  [['weather', 'nmc'], '天气预警'],
  [['ofac', 'uflpa', 'bis', 'sanction', 'compliance', 'un-consolidated', 'mofcom'], '制裁合规'],
  [['commodity', 'pbc', 'stats', 'fx', 'shipping'], '宏观市场'],
  // 自然灾害类（USGS 地震速报、中国地震台网 CENC）：须先于政策法规规则匹配，
  // 保证地震速报编码不会落入其他关键词组，未被 code 命中时仍回退 source_type。
  [['usgs', 'earthquake', 'cenc'], '自然灾害'],
  [['journal', 'announcement', 'notice', 'press', 'bulletin', 'customs', 'fmprc', 'wto', 'mem-', 'mee-', 'policy'], '政策法规'],
  [['manual'], '人工录入'],
];

const SOURCE_TYPE_CATEGORY: Record<string, string> = {
  official_api: '官方接口',
  external_tool: '外部核查',
  sanctions: '制裁合规',
  'export-control': '制裁合规',
  policy: '政策法规',
  incident: '突发事件',
  manual: '人工录入',
};

const sourceCategory = (source: DataSource): string => {
  const code = source.code.toLowerCase();
  for (const [keywords, label] of SOURCE_CODE_CATEGORY_RULES) {
    if (keywords.some((keyword) => code.includes(keyword))) return label;
  }
  return SOURCE_TYPE_CATEGORY[source.type] ?? '其他信源';
};

// 连通状态标签：与名称下方「采集正常/失败」的新鲜度状态区分职责，只描述连通与访问能力。
// 徽标的文案与配色必须同源：先判定连通变体，再由变体唯一决定标签与色调，
// 避免文案取自访问语义、颜色却沿用采集状态（如「连通正常」被染成采集失败的红色）。
type ConnectivityVariant =
  | 'connected' | 'cooldown' | 'busy' | 'throttled'
  | 'access_error' | 'disabled' | 'on_demand' | 'manual' | 'unconfigured';

type ConnectivityTone = 'success' | 'info' | 'warning' | 'danger' | 'neutral';

// 判定顺序与此前的标签实现保持一致：非联网来源 → 停用 → 访问执行态 → 访问错误 → 未配置 → 正常。
const sourceConnectivityVariant = (source: DataSource): ConnectivityVariant => {
  if (source.type === 'external_tool') return 'on_demand';
  if (source.code === 'manual-json') return 'manual';
  if (!source.enabled) return 'disabled';
  if (source.accessStatus === 'cooldown') return 'cooldown';
  if (source.accessStatus === 'busy') return 'busy';
  if (source.accessStatus === 'throttled') return 'throttled';
  if (source.accessLastErrorKind !== null
    || (source.accessLastHttpStatus !== null && source.accessLastHttpStatus >= 400)) return 'access_error';
  if (!source.endpointUrl) return 'unconfigured';
  return 'connected';
};

const CONNECTIVITY_META: Record<ConnectivityVariant, {label: string; tone: ConnectivityTone}> = {
  connected: {label: '连通正常', tone: 'success'},
  cooldown: {label: '访问冷却中', tone: 'warning'},
  busy: {label: '请求执行中', tone: 'info'},
  throttled: {label: '间隔保护中', tone: 'warning'},
  access_error: {label: '连通异常', tone: 'danger'},
  disabled: {label: '已停用', tone: 'neutral'},
  on_demand: {label: '核查', tone: 'neutral'},
  manual: {label: '人工录入', tone: 'neutral'},
  unconfigured: {label: '未配置', tone: 'neutral'},
};

// 五种色调直接复用采集状态色板已有的类名，不引入新的原始色值：
// 绿色=连通正常，橙色=冷却/间隔保护，蓝色=请求执行中，红色=连通异常，中性灰=停用/核查/人工/未配置。
const CONNECTIVITY_TONE_CLASSES: Record<ConnectivityTone, {
  pill: string; dot: string; dotCore: string; ringDur: number;
}> = {
  success: {pill: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300', dot: 'bg-emerald-500/60', dotCore: 'bg-emerald-600', ringDur: 2},
  info: {pill: 'bg-blue-100 text-blue-700 dark:bg-blue-950/80 dark:text-blue-300', dot: 'bg-blue-500/60', dotCore: 'bg-blue-600', ringDur: 1.2},
  warning: {pill: 'bg-orange-100 text-orange-700 dark:bg-orange-950/80 dark:text-orange-300', dot: 'bg-orange-500/60', dotCore: 'bg-orange-600', ringDur: 1.6},
  danger: {pill: 'bg-red-100 text-[#ba1a1a] dark:bg-red-950/80 dark:text-red-300', dot: 'bg-red-500/60', dotCore: 'bg-[#ba1a1a]', ringDur: 1.2},
  neutral: {pill: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300', dot: 'bg-slate-500/60', dotCore: 'bg-slate-600', ringDur: 2},
};

// 节点详情通俗说明：挂在标签悬浮提示上，面向业务用户，不直接暴露 HTTP 状态码。
const ACCESS_ERROR_HINTS: Record<string, string> = {
  access_blocked: '目标网站限制了自动访问，已进入保护冷却，稍后自动恢复',
  rate_limited: '目标网站请求过于频繁，系统已自动降速保护',
  authentication_required: '目标网站要求认证，请检查运行密钥或凭据配置',
  upstream_error: '目标网站暂时不可用，系统稍后会自动重试',
  http_error: '接口返回异常，请检查接口地址或参数配置',
  network_error: '网络连接异常，系统稍后会自动重试',
};

const sourceNodeHint = (source: DataSource): string => {
  if (source.accessStatus === 'cooldown') return `访问冷却中：${source.latency}，冷却结束后自动恢复`;
  if (source.accessStatus === 'busy') return '同一域名的请求正在执行，稍后自动继续';
  if (source.accessStatus === 'throttled') return '为保护目标网站，当前处于请求间隔保护中';
  if (source.accessLastErrorKind) {
    return ACCESS_ERROR_HINTS[source.accessLastErrorKind] ?? '最近一次采集未成功，系统稍后会自动重试';
  }
  if (source.type === 'external_tool') {
    // 天眼查的单供应商手动核查入口已收敛到供应商查询助手；自动核查为周度分片，提示不声称每日 06:00 或整批刷新。
    if (source.code === TYC_SOURCE_CODE) {
      return source.apiKeyConfigured
        ? '外部核查工具：运行密钥已配置，单个供应商风险信息请通过供应商查询助手核查；每周日、周一按分片自动核查已启用供应商'
        : '外部核查工具：运行密钥未配置，请先在编辑中配置运行密钥后再发起核查';
    }
    return source.apiKeyConfigured
      ? '外部核查工具：运行密钥已配置，发起查询，不支持页面刷新'
      : '外部核查工具：运行密钥未配置，请先在编辑中配置，不支持页面刷新';
  }
  if (source.code === 'manual-json') return '通过人工上传文件录入信号，不进行联网采集';
  if (!source.enabled) return '信息源已停用，启用后才会恢复自动采集';
  if (!source.endpointUrl) return '尚未配置接口地址，暂不可采集';
  if (source.accessLastHttpStatus !== null && source.accessLastHttpStatus >= 200 && source.accessLastHttpStatus < 300) {
    return '最近一次采集请求成功，接口连通正常';
  }
  if (source.accessLastHttpStatus !== null && source.accessLastHttpStatus >= 400) {
    return '最近一次采集请求未成功，请检查接口地址或凭据配置';
  }
  if (source.accessLastHttpStatus !== null) return '最近一次采集结果异常，系统会按计划重试';
  return '采集请求按域名保护策略执行，当前连通正常';
};

// 天眼查使用「按需核查」标签且不渲染通用刷新按钮；单供应商核查由供应商查询助手承担。
const TYC_SOURCE_CODE = 'tianyancha';

// 迁移 0050 退役来源：mofcom-entity-control（业务库 ID 4）已停用并取消调度，
// 唯一入口收敛到 mofcom-entity-detail（业务库 ID 361）；控制台只隐藏不删除其行。
const RETIRED_SOURCE_CODE = 'mofcom-entity-control';

// 单来源刷新的业务化禁用原因；null 表示当前可触发采集。
const refreshBlockedReason = (
  source: DataSource,
  role: 'viewer' | 'admin',
  refreshingId: string | null,
): string | null => {
  if (role !== 'admin') return '仅管理员可触发采集';
  if (source.type === 'external_tool' && source.code !== TYC_SOURCE_CODE) return '外部核查工具调用，不支持页面刷新';
  if (source.code === 'manual-json') return '人工录入信息源不支持刷新';
  // adapterStatus 只描述声明式适配器的发布状态；external_tool 不走适配器发布流程，
  // 真实后端返回 unconfigured，不能据此禁用行内操作。
  if (source.type !== 'external_tool' && source.adapterStatus !== 'builtin' && source.adapterStatus !== 'published') {
    return source.adapterStatus === 'draft' || source.adapterStatus === 'invalid'
      ? '适配器尚未发布，暂不可刷新'
      : '该来源尚未完成适配器配置，暂不可刷新';
  }
  if (!source.enabled) return '信息源已停用，启用后可刷新';
  if (refreshingId !== null && refreshingId !== source.id) return '正在刷新其他信息源，请稍候';
  return null;
};

export const DataSourcesView: React.FC<DataSourcesViewProps> = ({
  dataSources, role, onUpdateSource, onRefreshSources, monitoringHealth,
}) => {
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [warningDismissed, setWarningDismissed] = useState(false);
  const [showAudit, setShowAudit] = useState(false);
  const [auditText, setAuditText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [runAllLoading, setRunAllLoading] = useState(false);
  const [runAllMsg, setRunAllMsg] = useState<{type: 'ok' | 'err'; text: string} | null>(null);
  // 单来源刷新：同一时间只允许一个来源刷新，结果按来源行内反馈。
  const [refreshingId, setRefreshingId] = useState<string | null>(null);
  const [refreshMsg, setRefreshMsg] = useState<{
    sourceId: string;
    tone: 'ok' | 'err';
    text: string;
  } | null>(null);

  // 编辑表单状态
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<DataSourceWritePayload>(emptyForm);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingStatus, setEditingStatus] = useState<DataSource['adapterStatus']>('draft');
  const [apiKeyInput, setApiKeyInput] = useState('');
  const [editingKeyHint, setEditingKeyHint] = useState<string | null>(null);
  // 有效期策略编辑状态（独立于 form，避免与 signal_validity_days 冲突）
  const [validityMode, setValidityMode] = useState<ValidityMode>('fixed_days');
  const [validityFixedDays, setValidityFixedDays] = useState<number | null>(null);
  const [validityGraceDays, setValidityGraceDays] = useState<number | null>(null);
  const [validityCriticalGraceDays, setValidityCriticalGraceDays] = useState<number | null>(null);
  const [validityError, setValidityError] = useState<string | null>(null);
  // 客户端校验用 UI 状态（不进入策略 payload，后端策略 schema 不接受这些字段）
  const [autoRevokeOnMissingSnapshot, setAutoRevokeOnMissingSnapshot] = useState(false);
  const [authoritativeFullSnapshot, setAuthoritativeFullSnapshot] = useState(false);

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
      description: source.description,
      enabled: source.enabled, signal_validity_days: source.signalValidityDays,
    });
    // 有效期策略：优先取结构化策略，否则回退到旧 signal_validity_days 映射为 fixed_days
    const policy = source.validityPolicy;
    setValidityMode(policy?.mode ?? 'fixed_days');
    setValidityFixedDays(policy?.fixed_days ?? source.signalValidityDays ?? null);
    setValidityGraceDays(policy?.grace_days ?? null);
    setValidityCriticalGraceDays(policy?.critical_grace_days ?? null);
    // 冲突旧字段：signal_validity_days 与策略不一致时阻止保存，避免后端 422
    const legacyConflict = source.signalValidityDays != null && policy !== null
      && (policy.mode !== 'fixed_days' || policy.fixed_days !== source.signalValidityDays);
    setValidityError(legacyConflict ? 'signal_validity_days 与 validity_policy 配置冲突，请先修正有效期策略' : null);
    setApiKeyInput('');
    setEditingKeyHint(source.apiKeyHint);
    setError(null);
    setShowForm(true);
  };

  const buildValidityPolicy = (): SourceValidityPolicy | null => {
    const policy: SourceValidityPolicy = {mode: validityMode};
    switch (validityMode) {
      case 'fixed_days':
        if (validityFixedDays === null) return null; // 留空=永久有效
        return {...policy, fixed_days: validityFixedDays};
      case 'until_superseded':
        // 替代时失效必须有固定天数兜底；空值由 validateValidity 阻止保存。
        return {...policy, fixed_days: validityFixedDays};
      case 'event_end_plus_grace':
        return {
          ...policy,
          ...(validityGraceDays !== null ? {grace_days: validityGraceDays} : {}),
          ...(validityCriticalGraceDays !== null ? {critical_grace_days: validityCriticalGraceDays} : {}),
        };
      case 'until_revoked':
      case 'indefinite':
        return policy;
    }
  };

  const validateValidity = (): string | null => {
    if ((validityMode === 'fixed_days' || validityMode === 'until_superseded')
      && validityFixedDays !== null && (validityFixedDays < 1 || validityFixedDays > 3650)) {
      return '固定天数必须在 1 到 3650 之间';
    }
    if (validityMode === 'until_superseded' && validityFixedDays === null) {
      return '替代时失效必须提供固定天数';
    }
    if (validityMode === 'event_end_plus_grace') {
      if (validityGraceDays !== null && (validityGraceDays < 1 || validityGraceDays > 3650)) return '宽限天数必须在 1 到 3650 之间';
      if (validityCriticalGraceDays !== null && (validityCriticalGraceDays < 1 || validityCriticalGraceDays > 3650)) return '关键宽限天数必须在 1 到 3650 之间';
    }
    if (validityMode === 'until_revoked' && autoRevokeOnMissingSnapshot && !authoritativeFullSnapshot) {
      return '声明按完整快照缺失自动撤销时，必须同时声明权威完整快照（authoritative_full_snapshot）';
    }
    return null;
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setValidityError(null);
    const validityCheck = validateValidity();
    if (validityCheck) {
      setValidityError(validityCheck);
      return;
    }
    try {
      const isExternalTool = form.source_type === 'external_tool';
      const validityPolicy = buildValidityPolicy();
      // 旧 signal_validity_days 与 validity_policy 必须一致：fixed_days 模式同步天数，其余模式置空
      const signalValidityDays = validityMode === 'fixed_days' ? validityFixedDays : null;
      const payload = {
        ...form,
        schedule: isExternalTool ? null : form.schedule,
        api_key: apiKeyInput.trim() || undefined,
        signal_validity_days: signalValidityDays,
        validity_policy: validityPolicy,
      };
      if (editingId) {
        const {code: _code, auth_type: authTypeValue, ...rest} = payload;
        // 历史认证值：省略 auth_type 键，由后端保持原值；其余情况原样提交。
        const changes = isLegacyAuthType ? rest : {...rest, auth_type: authTypeValue};
        await onUpdateSource(editingId, changes);
        await onRefreshSources();
      }
      setShowForm(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '保存信息源失败');
    }
  };

  const handleRunAll = async () => {
    if (!window.confirm('确认全量刷新所有信息源？将依次触发各信源采集（跳过 5 分钟内已成功的），可能需要 1-3 分钟。')) return;
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

  // 单来源刷新：仅管理员、且仅对已启用且可拉取的普通来源可用；失败原因按行反馈。
  // 天眼查不渲染通用刷新按钮，普通拉取式来源继续走通用 /run。
  const handleRunSource = async (source: DataSource) => {
    if (role !== 'admin' || refreshingId !== null) return;
    setRefreshingId(source.id);
    setRefreshMsg(null);
    setError(null);
    let completed = false;
    try {
      const run = await api.runSource(Number(source.id));
      setRefreshMsg({sourceId: source.id, tone: 'ok', text: `刷新完成，新增 ${run.created_count} 条记录`});
      completed = true;
    } catch (caught) {
      setRefreshMsg({
        sourceId: source.id,
        tone: 'err',
        text: caught instanceof Error ? caught.message : '刷新失败，请稍后重试',
      });
    } finally {
      setRefreshingId(null);
    }
    // 采集/核查已完成后再重载列表：重载失败不得改写已成功的操作结果，只追加次级提示。
    // 成功后必须携带健康刷新意图：单源刷新改变来源新鲜度，诊断需立即重取而非等待 60 秒周期。
    if (completed) {
      try {
        await onRefreshSources({refreshMonitoringHealth: true});
      } catch (caught) {
        const reason = caught instanceof Error ? caught.message : '未知原因';
        setRefreshMsg((current) => current && current.sourceId === source.id
          ? {...current, text: `${current.text}（列表刷新失败，请重新加载：${reason}）`}
          : current);
      }
    }
  };

  const handleToggleSource = async (source: DataSource) => {
    if (role !== 'admin' || togglingId !== null) return;
    const action = source.enabled ? '停用' : '启用';
    if (!window.confirm(`确认${action}“${displaySourceName(source.name)}”？`)) return;
    setTogglingId(source.id);
    setError(null);
    try {
      await api.updateSource(Number(source.id), {enabled: !source.enabled});
      await onRefreshSources();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `信息源${action}失败`);
    } finally {
      setTogglingId(null);
    }
  };

  const loadAudit = async () => {
    if (role !== 'admin') return;
    try {
      const result = await api.sourceAuditLogs();
      setAuditText(result.items.map((item) => (
        `${new Date(item.created_at).toLocaleString()} · 信息源 ID ${item.source_id ?? '-'} · ${item.action} · ${item.actor_role} · ${JSON.stringify(item.changes)}`
      )).join('\n') || '暂无修改记录');
      setShowAudit(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '审计日志加载失败');
    }
  };

  // 调度器实况已迁移独立 /scheduler 页面（桌面 system 导航），本页不再提供打开入口。

  // 退役来源只隐藏不删除：行、运行提示、概览统计与空态判断必须基于同一可见集合，
  // 避免只过滤行导致统计数字仍把退役来源计入；filter 保留原始对象引用，可见行回调语义不变。
  const visibleSources = dataSources.filter((source) => source.code !== RETIRED_SOURCE_CODE);
  // 运行提示只针对真实异常（冷却/失败/超期等），停用不是运行异常，不再进入提示区。
  const delayedSources = visibleSources.filter((source) => source.status === 'warning' || source.status === 'error');
  // 全网累计记录数取已入库累计信号数（totalSignalCount），不是最近一次采集新增数（itemCount）。
  const totalSignals = visibleSources.reduce((total, source) => total + source.totalSignalCount, 0);
  const isExternalForm = form.source_type === 'external_tool';
  const isTycForm = isExternalForm && form.code === 'tianyancha';
  const isLegacyAuthType = !AUTH_TYPE_OPTIONS.some((option) => option.value === form.auth_type);
  const mayEnable = isExternalForm || editingStatus === 'builtin' || editingStatus === 'published';
  // 诊断就绪时按 source_id 建立新鲜度索引；hidden/unknown/loading 一律不渲染来源级健康。
  const healthBySource = monitoringHealth.status === 'ready'
    ? new Map(monitoringHealth.health.sources.map((item) => [item.source_id, item]))
    : null;

  return (
    <div className="space-y-5 pb-20 lg:pb-8">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
        <div>
          <h1 className="text-xl font-black text-slate-900 dark:text-white tracking-tight lg:text-2xl">信息源清单</h1>
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

      {error && <div role="alert" className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}

      {!warningDismissed && delayedSources.length > 0 && (
        <motion.div
          initial={{opacity: 0, y: -6}}
          animate={{opacity: 1, y: 0}}
          className="flex items-start justify-between gap-3 rounded-2xl border border-amber-300/80 bg-amber-500/10 p-3.5 shadow-sm dark:border-amber-800/60 dark:bg-amber-950/40"
        >
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-[#D97706]"/>
            <div>
              <h4 className="text-[13px] font-bold text-amber-900 dark:text-amber-200">信息源运行提示</h4>
              <p className="mt-0.5 text-[12px] leading-relaxed text-amber-800/90 dark:text-amber-300/80">
                {delayedSources.map((source) => `${displaySourceName(source.name)}：${source.latency}`).join('；')}
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
        aria-label="信息源概览"
      >
        <div className="space-y-0.5">
          <div className="text-[11px] text-[#727782] dark:text-slate-400 font-medium">信息源接入数</div>
          <div className="text-xl font-bold font-mono text-[#101d28] dark:text-white">
            {visibleSources.length} <span className="text-xs font-normal text-slate-500">个管道</span>
          </div>
        </div>
        <div className="space-y-0.5">
          <div className="text-[11px] text-[#727782] dark:text-slate-400 font-medium">正常运行 (Normal)</div>
          <div className="text-xl font-bold font-mono text-emerald-600 dark:text-emerald-400">
            {visibleSources.filter((source) => source.status === 'normal').length} <span className="text-xs font-normal text-slate-500">个</span>
          </div>
        </div>
        <div className="space-y-0.5">
          <div className="text-[11px] text-[#727782] dark:text-slate-400 font-medium">异常/延迟节点</div>
          <div className="text-xl font-bold font-mono text-[#ba1a1a] dark:text-red-400">
            {visibleSources.filter((source) => source.status === 'warning' || source.status === 'error').length} <span className="text-xs font-normal text-slate-500">个</span>
          </div>
        </div>
        <div className="space-y-0.5">
          <div className="text-[11px] text-[#727782] dark:text-slate-400 font-medium">全网累计记录数</div>
          <div className="text-xl font-bold font-mono text-[#004782] dark:text-blue-400">
            {totalSignals.toLocaleString()} <span className="text-xs font-normal text-slate-500">条</span>
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
            {runAllLoading ? '全量刷新中…（串行采集，请稍候）' : '全量刷新所有信息源'}
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
        aria-label="信息源列表"
      >
        <div className="hidden border-b border-slate-200/80 bg-slate-100/70 px-5 py-3 text-[12px] font-bold text-slate-600 dark:border-slate-800 dark:bg-slate-800/80 dark:text-slate-300 md:grid md:grid-cols-[repeat(14,minmax(0,1fr))] md:gap-4">
          <div className="col-span-3">信息源名称与类别</div>
          <div className="col-span-2">连通状态</div>
          <div className="col-span-2">调度周期</div>
          <div className="col-span-2">有效期策略</div>
          {/* 768px CJK 回归：表头曾把「累计」从中间断开。拆为两个不可拆语义短语，
              md 下有意分行，lg 起恢复同行；DOM 顺序保持可访问文本为「记录数（有效/累计）」。 */}
          <div className="col-span-2 flex flex-col lg:block">
            <span className="whitespace-nowrap">记录数</span>
            <span className="whitespace-nowrap">（有效/累计）</span>
          </div>
          <div className="col-span-3 text-right">操作</div>
        </div>
        <div className="divide-y divide-[#c2c6d2]/50 dark:divide-slate-800">
          {visibleSources.length === 0 && (
            <div className="p-10 text-center text-sm text-slate-500">暂无信息源配置</div>
          )}
          {visibleSources.map((source) => {
            const isExternalTool = source.type === 'external_tool';
            // 采集状态配色（只用于行底色与来源图标）：disabled (灰停用) / running (蓝执行) /
            //               error (红失败) / warning (橙异常) / normal (绿正常)
            const statusStyle = ({
              disabled: { row: 'bg-slate-100/40 dark:bg-slate-800/30', icon: 'bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-400' },
              running: { row: 'bg-blue-50/40 dark:bg-blue-950/10', icon: 'bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300' },
              error: { row: 'bg-red-50/30 dark:bg-red-950/10', icon: 'bg-red-100 text-[#ba1a1a] dark:bg-red-950 dark:text-red-300' },
              warning: { row: 'bg-orange-50/40 dark:bg-orange-950/10', icon: 'bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300' },
              normal: { row: '', icon: 'bg-[#ecf4ff] text-[#004782] dark:bg-slate-800 dark:text-blue-300' },
            } as const)[source.status];
            // 连通徽标配色只由访问/连通语义决定，与采集状态彻底解耦。
            const connectivity = CONNECTIVITY_META[sourceConnectivityVariant(source)];
            const connectivityStyle = CONNECTIVITY_TONE_CLASSES[connectivity.tone];
            // 天眼查显示「按需核查」；其他 external_tool 沿用默认「核查」。
            const isTycSource = source.code === TYC_SOURCE_CODE;
            const connectivityLabel = isTycSource ? '按需核查' : connectivity.label;
            const canToggle = source.adapterStatus === 'builtin' || source.adapterStatus === 'published' || isExternalTool;
            const isToggling = togglingId === source.id;
            const displayName = displaySourceName(source.name);
            const isRefreshing = refreshingId === source.id;
            const refreshBlocked = refreshBlockedReason(source, role, refreshingId);
            const runTitle = isRefreshing ? '正在触发采集，请稍候' : (refreshBlocked ?? '立即触发一次采集');
            const runLabel = isRefreshing ? '刷新中…' : '刷新';
            const scheduleDisplay = describeSourceSchedule(source);
            return (
              <motion.div
                key={source.id}
                role="listitem"
                className={`p-4 transition-colors sm:px-5 hover:bg-[#185fa5]/5 dark:hover:bg-slate-800/50 ${statusStyle.row}`}
              >
                <div className="grid grid-cols-12 gap-3 md:grid-cols-[repeat(14,minmax(0,1fr))] md:gap-4 items-center">
                  <div className="col-span-12 md:col-span-3 flex items-center gap-3 min-w-0">
                    <div data-testid={`source-icon-${source.id}`} className={`p-2.5 rounded-lg flex items-center justify-center shrink-0 ${statusStyle.icon}`}>
                      <span className="material-symbols-outlined text-[20px]">
                        {source.type.includes('api') || source.type.includes('API') || source.type.includes('接口') ? 'api' : 'database'}
                      </span>
                    </div>
                    <div className="min-w-0">
                      <div className="flex flex-nowrap items-center gap-2 min-w-0">
                        <h3 data-testid={`source-name-${source.id}`} title={displayName} className="font-bold text-[14px] text-[#101d28] dark:text-white truncate min-w-0">{displayName}</h3>
                        <span data-testid={`source-category-${source.id}`} className="shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-slate-700">
                          {sourceCategory(source)}
                        </span>
                      </div>
                      <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
                        信息源 ID: <span className="font-mono font-bold">{source.id}</span> · 编码: <span className="font-mono">{source.code}</span>
                      </p>
                      <MonitoringSourceFreshness
                        health={healthBySource?.get(Number(source.id))}
                        onDemand={isExternalTool}
                        labelOverride={isTycSource ? '按需核查' : undefined}
                      />
                    </div>
                  </div>
                  <div className="col-span-6 md:col-span-2 flex items-center gap-2">
                    <span
                      data-testid={`source-connectivity-${source.id}`}
                      title={sourceNodeHint(source)}
                      className={`text-[11px] font-bold px-2.5 py-1 rounded-full inline-flex items-center gap-1.5 cursor-help whitespace-nowrap ${connectivityStyle.pill}`}
                    >
                      <span className="relative flex items-center justify-center w-2 h-2">
                        <motion.span
                          data-testid={`source-connectivity-ring-${source.id}`}
                          className={`absolute inline-flex h-full w-full rounded-full ${connectivityStyle.dot}`}
                          animate={{scale: [1, 2.2, 1], opacity: [0.8, 0, 0.8]}}
                          transition={{duration: connectivityStyle.ringDur, repeat: Infinity, ease: 'easeInOut'}}
                        />
                        <span data-testid={`source-connectivity-dot-${source.id}`} className={`relative inline-flex rounded-full h-1.5 w-1.5 ${connectivityStyle.dotCore}`} />
                      </span>
                      {connectivityLabel}
                    </span>
                  </div>
                  <div data-testid={`source-schedule-${source.id}`} className="col-span-6 md:col-span-2 min-w-0 text-[13px]">
                    <span className="md:hidden whitespace-nowrap text-xs font-sans font-semibold text-[#424751] dark:text-slate-400 mr-0.5">调度:</span>
                    <span
                      className="whitespace-normal text-xs font-semibold text-[#424751] dark:text-slate-400"
                      title={scheduleDisplay.title}
                      aria-label={scheduleDisplay.ariaLabel}
                    >
                      {scheduleDisplay.label}
                    </span>
                  </div>
                  <div
                    data-testid={`source-validity-policy-${source.id}`}
                    className="col-span-6 md:col-span-2 flex flex-wrap items-baseline gap-x-1 text-[13px]"
                  >
                    {source.validityPolicy && (
                      <>
                        <span className="md:hidden whitespace-nowrap text-xs font-sans font-semibold text-[#424751] dark:text-slate-400 mr-0.5">有效期:</span>
                        <span className="whitespace-nowrap text-xs font-semibold text-[#424751] dark:text-slate-400">
                          {VALIDITY_MODE_LABELS[source.validityPolicy.mode] ?? source.validityPolicy.mode}
                          {source.validityPolicy.fixed_days != null ? ` ${source.validityPolicy.fixed_days} 天` : ''}
                        </span>
                      </>
                    )}
                  </div>
                  <div
                    data-testid={`source-record-count-${source.id}`}
                    className="col-span-6 md:col-span-2 flex flex-wrap items-baseline gap-x-1 text-[13px] font-mono font-bold text-[#004782] dark:text-blue-400"
                  >
                    <span className="md:hidden text-slate-400 text-[11px] font-sans font-normal mr-0.5">记录:</span>
                    <Link
                      to={sourceSignalsPath(source.id, 'valid')}
                      aria-label={`${displayName} 有效记录 ${source.validSignalCount} 条`}
                      data-testid={`source-valid-${source.id}`}
                      className="rounded-sm underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
                    >
                      {source.validSignalCount.toLocaleString()}
                    </Link>
                    <span className="font-normal text-slate-400">/</span>
                    <Link
                      to={sourceSignalsPath(source.id, 'all')}
                      aria-label={`${displayName} 全部历史记录 ${source.totalSignalCount} 条`}
                      data-testid={`source-total-${source.id}`}
                      title="累计历史存量（含已过期）"
                      className="rounded-sm underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
                    >
                      {source.totalSignalCount.toLocaleString()}
                    </Link>
                    <span className="text-[11px] font-normal text-slate-500">条</span>
                  </div>
                  <div data-testid={`source-actions-${source.id}`} className="col-span-12 md:col-span-3 flex flex-wrap items-center justify-end gap-2 text-right">
                    {/* 天眼查手动核查入口已收敛到供应商查询助手：动作区不渲染通用刷新。 */}
                    {!isTycSource && (
                      <span title={runTitle} className="inline-flex">
                        <button
                          type="button"
                          onClick={() => void handleRunSource(source)}
                          disabled={refreshBlocked !== null || isRefreshing}
                          aria-label={`刷新${displayName}`}
                          title={runTitle}
                          className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-bold rounded-lg border border-[#004782]/40 text-[#004782] hover:bg-[#ecf4ff] disabled:opacity-40 disabled:cursor-not-allowed dark:border-blue-400/40 dark:text-blue-300 dark:hover:bg-slate-800 transition-colors"
                        >
                          <span aria-hidden="true" className={`material-symbols-outlined text-[14px] ${isRefreshing ? 'animate-spin' : ''}`}>{isRefreshing ? 'sync' : 'refresh'}</span>
                          {runLabel}
                        </button>
                      </span>
                    )}
                    {role === 'admin' && (
                      <button
                        type="button"
                        onClick={() => openEdit(source)}
                        aria-label={`编辑${displayName}`}
                        className="px-2.5 py-1 text-[11px] font-bold rounded-lg border border-[#c2c6d2] dark:border-slate-700 text-[#004782] dark:text-blue-300 hover:bg-[#ecf4ff] dark:hover:bg-slate-800 transition-colors"
                        title="编辑信息源配置（API Key / 调度周期 / 适配器）"
                      >
                        编辑
                      </button>
                    )}
                    {canToggle && (
                      <button
                        type="button"
                        onClick={() => void handleToggleSource(source)}
                        disabled={role !== 'admin' || togglingId !== null}
                        aria-pressed={source.enabled}
                        aria-label={`${source.enabled ? '停用' : '启用'}${displayName}`}
                        title={source.enabled ? '停用该信息源，停止自动采集' : '启用该信息源，恢复自动采集'}
                        className={`px-2.5 py-1 text-[11px] font-bold rounded-lg border disabled:opacity-40 ${source.enabled ? 'border-amber-300 text-amber-800 dark:text-amber-300' : 'border-emerald-300 text-emerald-800 dark:text-emerald-300'}`}
                      >
                        {isToggling ? '处理中...' : source.enabled ? '停用' : '启用'}
                      </button>
                    )}
                    {refreshMsg?.sourceId === source.id && (
                      <p
                        data-testid={`source-run-msg-${source.id}`}
                        role={refreshMsg.tone === 'err' ? 'alert' : 'status'}
                        className={`w-full text-[11px] ${refreshMsg.tone === 'err' ? 'text-red-600 dark:text-red-300' : 'text-emerald-700 dark:text-emerald-300'}`}
                      >
                        {refreshMsg.text}
                      </p>
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
              <h2 className="text-lg font-bold">编辑信息源 · ID {editingId}</h2>
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
                <input required disabled value={form.source_type} className="mt-1 w-full border rounded-lg p-2 disabled:bg-slate-100 disabled:text-slate-500" />
              </label>
              <label className="text-xs font-bold">可信度
                <input type="number" min="0" max="100" required value={form.credibility} onChange={(event) => update('credibility', Number(event.target.value))} className="mt-1 w-full border rounded-lg p-2" />
              </label>
              <label className="text-xs font-bold sm:col-span-2">官方接口 URL
                <input type="url" value={form.endpoint_url ?? ''} onChange={(event) => update('endpoint_url', event.target.value || null)} className="mt-1 w-full border rounded-lg p-2" />
              </label>
              <label className="text-xs font-bold" title="分 时 日 月 周，如 */30 * * * * 表示每 30 分钟一次，北京时间">{isExternalForm ? '调用方式' : '调度 cron'}
                <input disabled={isExternalForm} value={isExternalForm ? '调用' : form.schedule ?? ''} onChange={(event) => update('schedule', event.target.value || null)} className="mt-1 w-full border rounded-lg p-2 font-mono disabled:bg-slate-100 disabled:text-slate-600" />
              </label>
              {isTycForm && (
                <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs leading-relaxed text-blue-900">
                  调度策略：人工核查通过页面按供应商调用；Scheduler 不按 cron 刷新。启用且运行密钥有效、额度充足时，每周日、周一各覆盖一个分片自动核查已启用供应商；停用时不执行批量核查。
                </div>
              )}
              <label className="text-xs font-bold sm:col-span-2" title="选择信号有效期策略模式；不同模式启用不同配置字段">
                有效期策略
                <select
                  value={validityMode}
                  onChange={(event) => {
                    setValidityMode(event.target.value as ValidityMode);
                    setValidityError(null);
                  }}
                  className="mt-1 w-full border rounded-lg p-2"
                >
                  {(Object.keys(VALIDITY_MODE_LABELS) as ValidityMode[]).map((mode) => (
                    <option key={mode} value={mode}>{VALIDITY_MODE_LABELS[mode]}</option>
                  ))}
                </select>
              </label>
              {(validityMode === 'fixed_days' || validityMode === 'until_superseded') && (
                <label
                  className="text-xs font-bold"
                  title={validityMode === 'until_superseded'
                    ? '替代时失效必须提供固定天数，作为长期未更新时的兜底有效期'
                    : '信号自发生起 N 天内有效；留空=永久有效。过期后仅留库，不再计入有效记录'}
                >
                  固定天数（天）
                  <input type="number" min="1" max="3650" placeholder={validityMode === 'until_superseded' ? '必填' : '留空=永久有效'} value={validityFixedDays ?? ''} onChange={(event) => setValidityFixedDays(event.target.value === '' ? null : Number(event.target.value))} className="mt-1 w-full border rounded-lg p-2" />
                </label>
              )}
              {validityMode === 'event_end_plus_grace' && (
                <>
                  <label className="text-xs font-bold" title="事件结束后额外宽限的天数">
                    宽限天数（天）
                    <input type="number" min="1" max="3650" placeholder="留空=不设宽限" value={validityGraceDays ?? ''} onChange={(event) => setValidityGraceDays(event.target.value === '' ? null : Number(event.target.value))} className="mt-1 w-full border rounded-lg p-2" />
                  </label>
                  <label className="text-xs font-bold" title="关键事件（如制裁、出口管制）的额外宽限天数">
                    关键宽限天数（天）
                    <input type="number" min="1" max="3650" placeholder="留空=不设关键宽限" value={validityCriticalGraceDays ?? ''} onChange={(event) => setValidityCriticalGraceDays(event.target.value === '' ? null : Number(event.target.value))} className="mt-1 w-full border rounded-lg p-2" />
                  </label>
                </>
              )}
              {validityMode === 'until_revoked' && (
                <label className="text-xs font-bold sm:col-span-2 flex items-center gap-2">
                  <input type="checkbox" checked={autoRevokeOnMissingSnapshot} onChange={(event) => setAutoRevokeOnMissingSnapshot(event.target.checked)} />
                  按完整快照缺失自动撤销（名单类信源）
                </label>
              )}
              {validityMode === 'until_revoked' && autoRevokeOnMissingSnapshot && (
                <label className="text-xs font-bold sm:col-span-2 flex items-center gap-2" title="自动撤销依赖信源声明为权威完整快照；未声明时后端不会执行自动撤销">
                  <input type="checkbox" checked={authoritativeFullSnapshot} onChange={(event) => setAuthoritativeFullSnapshot(event.target.checked)} />
                  声明权威完整快照（authoritative_full_snapshot）
                </label>
              )}
              {validityError && (
                <p role="alert" className="sm:col-span-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{validityError}</p>
              )}
              {isLegacyAuthType ? (
                <div
                  data-testid="auth-type-legacy-warning"
                  role="status"
                  className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-900 sm:col-span-2"
                >
                  当前认证方式为“{form.auth_type}”（历史值，不在可编辑选项中），保存时将保持服务器上的原值不变。
                </div>
              ) : (
                <label className="text-xs font-bold">认证方式
                  <select value={form.auth_type} onChange={(event) => update('auth_type', event.target.value)} className="mt-1 w-full border rounded-lg p-2">
                    {AUTH_TYPE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>
              )}
              {!isExternalForm && form.auth_type === 'api_key' && (
                <label className="text-xs font-bold">凭据引用
                  <input value={form.credential_ref ?? ''} onChange={(event) => update('credential_ref', event.target.value || null)} placeholder="env:SOURCE_API_KEY" className="mt-1 w-full border rounded-lg p-2 font-mono" />
                </label>
              )}
              {!isExternalForm && form.auth_type === 'api_key' && (
                <label className="text-xs font-bold">API Key 请求头名
                  <input
                    value={String(form.login_config.header_name ?? '')}
                    onChange={(event) => {
                      const value = event.target.value;
                      if (value) {
                        // 合并而非覆盖：保全迁移 marker（0054/0055 provenance）等既有 login_config 键。
                        update('login_config', {...form.login_config, header_name: value});
                      } else {
                        const {header_name: _headerName, ...restConfig} = form.login_config;
                        update('login_config', restConfig);
                      }
                    }}
                    placeholder="X-API-Key"
                    className="mt-1 w-full border rounded-lg p-2"
                  />
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
            </div>
            {isExternalForm && (
              <p className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900">运行密钥加密保存在服务器数据库中，控制台仅显示末四位，保存后不回传明文；启用/停用由下方开关统一控制。</p>
            )}
            <label className="text-xs font-bold flex items-center gap-2">
              <input type="checkbox" disabled={!mayEnable} checked={form.enabled && mayEnable} onChange={(event) => update('enabled', event.target.checked)} />
              {isExternalForm ? '启用核查' : '启用正式采集（仅已发布或内置适配器可用）'}
            </label>
            <div className="flex justify-end gap-2">
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
              <h2 className="text-lg font-bold">信息源修改日志</h2>
              <button type="button" onClick={() => setShowAudit(false)}>关闭</button>
            </div>
            <pre className="bg-slate-50 rounded-lg p-4 text-xs whitespace-pre-wrap max-h-[60vh] overflow-auto">{auditText}</pre>
          </div>
        </div>
      )}
    </div>
  );
};

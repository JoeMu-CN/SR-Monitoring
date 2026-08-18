import React, {useLayoutEffect, useMemo, useRef, useState} from 'react';
import {motion, useReducedMotion} from 'motion/react';
import {
  Activity,
  CheckCircle2,
  CircleDashed,
  Database,
  FileText,
  Globe2,
  Search,
  ShieldAlert,
  SkipForward,
  XCircle,
  type LucideIcon,
} from 'lucide-react';
import type {
  ResearchSourceRead,
  ResearchTaskEventRead,
  ResearchTaskEventStatus,
  ResearchTaskRead,
} from '../api';

interface ResearchExecutionGraphProps {
  task: ResearchTaskRead;
  events: ResearchTaskEventRead[];
  sources: ResearchSourceRead[];
}

type NodeChannel = 'task' | 'monitoring' | 'web' | 'evidence' | 'report';

interface GraphNode {
  key: string;
  label: string;
  subtitle: string;
  status: ResearchTaskEventStatus;
  channel: NodeChannel;
  icon: LucideIcon;
  event?: ResearchTaskEventRead;
  source?: ResearchSourceRead;
  sequence?: number;
}

interface GraphEdge { from: string; to: string; channel: NodeChannel; }
interface GraphPath extends GraphEdge { d: string; }

const statusMeta: Record<ResearchTaskEventStatus, {label: string; icon: LucideIcon; dot: string}> = {
  pending: {label: '等待', icon: CircleDashed, dot: 'bg-slate-400'},
  running: {label: '执行中', icon: Activity, dot: 'bg-[#007aff]'},
  succeeded: {label: '已完成', icon: CheckCircle2, dot: 'bg-[#34c759]'},
  failed: {label: '失败', icon: XCircle, dot: 'bg-[#ff3b30]'},
  skipped: {label: '已跳过', icon: SkipForward, dot: 'bg-slate-500'},
  info: {label: '信息', icon: Activity, dot: 'bg-sky-400'},
};

const channelMeta: Record<NodeChannel, {node: string; icon: string; line: string}> = {
  task: {node: 'border-blue-500/70 bg-blue-500/10', icon: 'bg-blue-500/20 text-blue-300', line: '#60a5fa'},
  monitoring: {node: 'border-amber-500/70 bg-amber-500/10', icon: 'bg-amber-500/20 text-amber-300', line: '#f59e0b'},
  web: {node: 'border-sky-500/70 bg-sky-500/10', icon: 'bg-sky-500/20 text-sky-300', line: '#38bdf8'},
  evidence: {node: 'border-emerald-500/70 bg-emerald-500/10', icon: 'bg-emerald-500/20 text-emerald-300', line: '#34d399'},
  report: {node: 'border-blue-400/70 bg-blue-400/10', icon: 'bg-blue-400/20 text-blue-200', line: '#60a5fa'},
};

const taskEventStatus = (status: ResearchTaskRead['status']): ResearchTaskEventStatus => {
  if (status === 'running') return 'running';
  if (status === 'succeeded') return 'succeeded';
  if (status === 'failed') return 'failed';
  if (status === 'cancelled') return 'skipped';
  return 'pending';
};

const detailNumber = (event: ResearchTaskEventRead | undefined, key: string) => {
  const value = event?.detail[key];
  return typeof value === 'number' ? value : null;
};

const formatEventTime = (value: string | undefined) => value
  ? new Intl.DateTimeFormat('zh-CN', {hour: '2-digit', minute: '2-digit', second: '2-digit'}).format(new Date(value))
  : '尚未记录';

export const ResearchExecutionGraph: React.FC<ResearchExecutionGraphProps> = ({task, events, sources}) => {
  const reduceMotion = useReducedMotion();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLButtonElement>());
  const [paths, setPaths] = useState<GraphPath[]>([]);
  const visibleEvents = useMemo(
    () => events.filter((event) => event.event_type !== 'monitoring_signal_filtered'),
    [events],
  );

  const {nodes, edges, monitoringNodes, webNodes, roots, evidenceNode, reportNode} = useMemo(() => {
    const latestByNode = new Map<string, ResearchTaskEventRead>();
    for (const event of visibleEvents) latestByNode.set(event.node_key, event);
    const eventSequence = new Map(visibleEvents.map((event, index) => [event.id, index]));
    const sourceById = new Map(sources.map((source) => [source.id, source]));
    const makeEventNode = (event: ResearchTaskEventRead, channel: NodeChannel, icon: LucideIcon): GraphNode => {
      const sourceId = detailNumber(event, 'research_source_id');
      const source = sourceId === null ? undefined : sourceById.get(sourceId);
      return {
        key: event.node_key,
        label: source?.title || event.label,
        subtitle: source
          ? (source.source_type === 'monitoring_signal' ? '监控轨风险信号' : '公开检索来源')
          : event.label,
        status: event.status,
        channel,
        icon,
        event,
        source,
        sequence: eventSequence.get(event.id) ?? 0,
      };
    };

    const taskNode: GraphNode = {
      key: 'task', label: `研究任务 #${task.id}`, subtitle: task.topic,
      status: taskEventStatus(task.status), channel: 'task', icon: FileText,
      event: latestByNode.get('task'),
    };
    const monitoringRoot = latestByNode.get('monitoring_context');
    const webRoot = latestByNode.get('web_search');
    const nextMonitoringNodes = [...latestByNode.values()]
      .filter((event) => event.node_key.startsWith('monitoring_signal:'))
      .map((event) => makeEventNode(event, 'monitoring', ShieldAlert));
    const nextWebNodes = [...latestByNode.values()]
      .filter((event) => event.node_key.startsWith('web_source:'))
      .sort((left, right) => (detailNumber(left, 'index') ?? 0) - (detailNumber(right, 'index') ?? 0))
      .map((event) => makeEventNode(event, 'web', Globe2));
    const nextRoots = [
      monitoringRoot ? makeEventNode(monitoringRoot, 'monitoring', ShieldAlert) : null,
      webRoot ? makeEventNode(webRoot, 'web', Search) : null,
    ].filter((node): node is GraphNode => node !== null);
    const nextEvidenceNode: GraphNode | null = sources.length > 0 ? {
      key: 'evidence_pool', label: '证据池', subtitle: `${sources.length} 条可追溯来源`,
      status: 'succeeded', channel: 'evidence', icon: Database,
    } : null;
    const reportEvent = latestByNode.get('report');
    const nextReportNode = reportEvent ? makeEventNode(reportEvent, 'report', FileText) : null;
    const nextNodes = [taskNode, ...nextRoots, ...nextMonitoringNodes, ...nextWebNodes];
    if (nextEvidenceNode) nextNodes.push(nextEvidenceNode);
    if (nextReportNode) nextNodes.push(nextReportNode);

    const nextEdges: GraphEdge[] = [];
    for (const root of nextRoots) nextEdges.push({from: taskNode.key, to: root.key, channel: root.channel});
    if (monitoringRoot) for (const node of nextMonitoringNodes) nextEdges.push({from: monitoringRoot.node_key, to: node.key, channel: 'monitoring'});
    if (webRoot) for (const node of nextWebNodes) nextEdges.push({from: webRoot.node_key, to: node.key, channel: 'web'});
    // 证据池是候选来源的汇聚节点，不再把每个候选都直接连过去，避免多根曲线交叉成线束。
    // 候选节点仍保留在检索轨迹中，证据池只从对应轨道根节点接一条汇聚线。
    if (nextEvidenceNode) {
      const evidenceRoot = webRoot ?? monitoringRoot;
      if (evidenceRoot) nextEdges.push({from: evidenceRoot.node_key, to: nextEvidenceNode.key, channel: 'evidence'});
    }
    if (nextEvidenceNode && nextReportNode) nextEdges.push({from: nextEvidenceNode.key, to: nextReportNode.key, channel: 'evidence'});
    return {
      nodes: nextNodes, edges: nextEdges, monitoringNodes: nextMonitoringNodes,
      webNodes: nextWebNodes, roots: nextRoots, evidenceNode: nextEvidenceNode,
      reportNode: nextReportNode,
    };
  }, [visibleEvents, sources, task.id, task.status, task.topic]);

  const selectedNode = nodes.find((node) => node.key === selectedKey)
    ?? nodes.find((node) => node.status === 'running')
    ?? reportNode ?? evidenceNode ?? nodes[0];

  const layoutSignature = `${nodes.map((node) => node.key).join('|')}::${edges.map((edge) => `${edge.from}>${edge.to}`).join('|')}`;
  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    const calculate = () => {
      const containerRect = container.getBoundingClientRect();
      const nextPaths = edges.flatMap((edge) => {
        const from = nodeRefs.current.get(edge.from);
        const to = nodeRefs.current.get(edge.to);
        if (!from || !to) return [];
        const fromRect = from.getBoundingClientRect();
        const toRect = to.getBoundingClientRect();
        const x1 = fromRect.right - containerRect.left;
        const y1 = fromRect.top + fromRect.height / 2 - containerRect.top;
        const x2 = toRect.left - containerRect.left;
        const y2 = toRect.top + toRect.height / 2 - containerRect.top;
        const bend = Math.max(24, (x2 - x1) * 0.48);
        return [{...edge, d: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`}];
      });
      setPaths(nextPaths);
    };
    calculate();
    const observer = new ResizeObserver(calculate);
    observer.observe(container);
    for (const node of nodeRefs.current.values()) observer.observe(node);
    return () => observer.disconnect();
  }, [edges, layoutSignature]);

  const registerNode = (key: string) => (element: HTMLButtonElement | null) => {
    if (element) nodeRefs.current.set(key, element);
    else nodeRefs.current.delete(key);
  };

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-800 bg-[#0b131e] text-slate-100 shadow-sm">
      <div className="flex flex-col gap-3 border-b border-slate-800 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-[14px] font-extrabold">研究执行图</h3>
      <p className="mt-0.5 text-[11px] text-slate-400">节点按事件到达顺序生成；仅展示系统已记录的检索、筛选、证据和报告事件</p>
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1.5 text-[10px] font-semibold text-slate-300" aria-label="执行图图例">
          <Legend color="bg-amber-400" label="监控轨证据" />
          <Legend color="bg-sky-400" label="公开检索" />
          <Legend color="bg-[#007aff]" label="执行中" />
          <Legend color="bg-[#34c759]" label="已完成" />
          <Legend color="bg-[#ff3b30]" label="失败" />
        </div>
      </div>

      <div className="grid xl:grid-cols-[minmax(0,1fr)_240px]">
        <div className="min-w-0 border-b border-slate-800 xl:border-b-0 xl:border-r">
          <div className="hidden overflow-x-auto p-4 lg:block">
            <div ref={containerRef} className="relative grid min-w-[980px] grid-cols-[140px_150px_minmax(280px,1fr)_130px_130px] items-start gap-x-8">
              <svg aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full overflow-visible">
                {paths.map((path) => (
                  <motion.path
                    key={`${path.from}-${path.to}`}
                    d={path.d}
                    fill="none"
                    stroke={channelMeta[path.channel].line}
                    strokeOpacity="0.62"
                    strokeWidth="1.5"
                    initial={reduceMotion ? false : {pathLength: 0, opacity: 0}}
                    animate={{pathLength: 1, opacity: 1}}
                    transition={{duration: 0.22, ease: 'easeOut'}}
                  />
                ))}
              </svg>

              <div className="relative z-10 col-start-1 self-start pt-2">
                <NodeCard node={nodes[0]} selected={selectedNode?.key === nodes[0].key} register={registerNode(nodes[0].key)} onSelect={setSelectedKey} />
              </div>
              <div className="relative z-10 col-start-2 flex flex-col gap-8 pt-10">
                {roots.find((node) => node.channel === 'monitoring') && <NodeCard node={roots.find((node) => node.channel === 'monitoring')!} selected={selectedNode?.key === 'monitoring_context'} register={registerNode('monitoring_context')} onSelect={setSelectedKey} />}
                {roots.find((node) => node.channel === 'web') && <NodeCard node={roots.find((node) => node.channel === 'web')!} selected={selectedNode?.key === 'web_search'} register={registerNode('web_search')} onSelect={setSelectedKey} />}
              </div>
              <div className="relative z-10 col-start-3 flex flex-col gap-7 pt-2">
                {monitoringNodes.length > 0 && <div>
                  <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-amber-300/80">监控轨信号 · {monitoringNodes.length}</p>
                  <div className="flex flex-col gap-2.5">
                    {monitoringNodes.map((node) => <motion.div key={node.key} initial={reduceMotion ? false : {opacity: 0, y: 8}} animate={{opacity: 1, y: 0}} transition={{duration: 0.22, delay: Math.min((node.sequence ?? 0) * 0.015, 0.8)}}><NodeCard node={node} compact selected={selectedNode?.key === node.key} register={registerNode(node.key)} onSelect={setSelectedKey} /></motion.div>)}
                  </div>
                </div>}
                {webNodes.length > 0 && <div>
                  <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-sky-300/80">公网候选 · {webNodes.length}</p>
                  <div className="flex flex-col gap-2.5">
                    {webNodes.map((node) => <motion.div key={node.key} initial={reduceMotion ? false : {opacity: 0, y: 8}} animate={{opacity: 1, y: 0}} transition={{duration: 0.22, delay: Math.min((node.sequence ?? 0) * 0.015, 0.8)}}><NodeCard node={node} compact selected={selectedNode?.key === node.key} register={registerNode(node.key)} onSelect={setSelectedKey} /></motion.div>)}
                  </div>
                </div>}
                {monitoringNodes.length === 0 && webNodes.length === 0 && <p className="rounded-xl border border-dashed border-slate-700 px-3 py-6 text-center text-[11px] text-slate-500">候选节点将在检索事件到达后逐步出现</p>}
              </div>
              {evidenceNode && <div className="relative z-10 col-start-4 self-start pt-2"><NodeCard node={evidenceNode} selected={selectedNode?.key === evidenceNode.key} register={registerNode(evidenceNode.key)} onSelect={setSelectedKey} /></div>}
              {reportNode && <div className="relative z-10 col-start-5 self-start pt-2"><NodeCard node={reportNode} selected={selectedNode?.key === reportNode.key} register={registerNode(reportNode.key)} onSelect={setSelectedKey} /></div>}
              {visibleEvents.length === 0 && <div className="relative z-10 col-start-2 col-span-3 flex items-center justify-center py-16 text-center text-[12px] text-slate-500"><span>此任务暂无可回放事件<br />新任务开始执行后会逐步生成节点</span></div>}
            </div>
          </div>

          <div className="space-y-2 p-3 lg:hidden">
            <p className="px-1 text-[11px] text-slate-400">窄屏使用时间线展示相同的真实执行事件</p>
            {visibleEvents.length === 0 ? <div className="rounded-xl border border-dashed border-slate-700 px-4 py-8 text-center text-[12px] text-slate-500">暂无执行事件</div> : visibleEvents.map((event) => {
              const meta = statusMeta[event.status];
              const Icon = meta.icon;
              return <button key={event.id} type="button" onClick={() => setSelectedKey(event.node_key)} className="flex min-h-11 w-full items-center gap-3 rounded-xl border border-slate-800 bg-[#0e1726] px-3 py-2.5 text-left transition hover:border-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#007aff]">
                <Icon className="h-4 w-4 shrink-0 text-slate-300" />
                <span className="min-w-0 flex-1"><span className="block truncate text-[12px] font-bold">{event.label}</span><span className="text-[10px] text-slate-500">{formatEventTime(event.occurred_at)}</span></span>
                <span className="text-[10px] font-semibold text-slate-400">{meta.label}</span>
              </button>;
            })}
          </div>
        </div>

        <NodeInspector node={selectedNode} />
      </div>
    </section>
  );
};

const Legend: React.FC<{color: string; label: string}> = ({color, label}) => (
  <span className="inline-flex items-center gap-1.5"><span className={`h-2 w-2 rounded-full ${color}`} />{label}</span>
);

const NodeCard: React.FC<{
  node: GraphNode;
  selected: boolean;
  compact?: boolean;
  register: (element: HTMLButtonElement | null) => void;
  onSelect: (key: string) => void;
}> = ({node, selected, compact = false, register, onSelect}) => {
  const Icon = node.icon;
  const status = statusMeta[node.status];
  const StatusIcon = status.icon;
  return (
    <button
      ref={register}
      type="button"
      onClick={() => onSelect(node.key)}
      aria-pressed={selected}
      className={`w-full rounded-xl border text-left shadow-sm transition duration-200 hover:-translate-y-0.5 hover:border-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#007aff] ${compact ? 'px-2.5 py-2' : 'px-3 py-3'} ${channelMeta[node.channel].node} ${selected ? 'ring-2 ring-[#007aff] ring-offset-2 ring-offset-[#0b131e]' : ''}`}
    >
      <div className="flex items-start gap-2.5">
        <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${channelMeta[node.channel].icon}`}><Icon className="h-4 w-4" /></span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center justify-between gap-1.5"><span className="truncate text-[11px] font-extrabold text-slate-100">{node.label}</span><StatusIcon className={`h-3.5 w-3.5 shrink-0 ${node.status === 'running' ? 'animate-pulse text-blue-300' : 'text-slate-300'}`} /></span>
          <span className={`mt-1 block text-slate-400 ${compact ? 'truncate text-[9px]' : 'line-clamp-2 text-[10px] leading-4'}`}>{node.subtitle}</span>
        </span>
      </div>
    </button>
  );
};

const NodeInspector: React.FC<{node: GraphNode | undefined}> = ({node}) => {
  if (!node) return <aside className="p-4 text-[12px] text-slate-500">选择节点查看详情</aside>;
  const meta = statusMeta[node.status];
  const detailEntries = Object.entries(node.event?.detail ?? {}).filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value));
  return (
    <aside className="bg-[#0e1726] p-4" aria-label="执行节点详情">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><p className="text-[10px] font-bold text-slate-500">节点详情</p><h4 className="mt-1 truncate text-[14px] font-extrabold text-white">{node.label}</h4></div>
        <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-slate-700 px-2 py-1 text-[10px] font-bold text-slate-300"><span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />{meta.label}</span>
      </div>
      <dl className="mt-4 space-y-3 text-[11px]">
        <InspectorRow label="节点类型" value={{task: '研究任务', monitoring: '监控轨证据', web: '公开检索', evidence: '证据池', report: '报告生成'}[node.channel]} />
        <InspectorRow label="记录时间" value={formatEventTime(node.event?.occurred_at)} />
        {node.source && <InspectorRow label="来源类型" value={node.source.source_type === 'monitoring_signal' ? '监控轨风险信号' : '公开网页'} />}
        {node.source && <InspectorRow label="可信度" value={node.source.credibility_tier} />}
        {detailEntries.slice(0, 10).map(([key, value]) => <InspectorRow key={key} label={detailLabel[key] ?? key} value={formatDetailValue(key, value)} />)}
      </dl>
      {node.source?.content_excerpt && <div className="mt-4 rounded-xl border border-slate-800 bg-[#0b131e] p-3"><p className="text-[10px] font-bold text-slate-500">证据摘要</p><p className="mt-1.5 line-clamp-5 text-[11px] leading-5 text-slate-300">{node.source.content_excerpt}</p></div>}
      {node.source && <a href={node.source.url} target="_blank" rel="noreferrer" className="mt-3 inline-flex min-h-11 w-full items-center justify-center rounded-lg border border-slate-700 text-[11px] font-bold text-slate-200 transition hover:border-blue-500 hover:text-blue-300">查看原始来源</a>}
    </aside>
  );
};

const detailLabel: Record<string, string> = {
  supplier_count: '供应商数量', source_count: '来源数量', candidate_count: '候选数量',
  evidence_count: '证据数量', risk_level: '风险等级', index: '候选序号',
  attempt: '执行尝试', signal_id: '风险信号 ID', alert_id: '风险提醒 ID',
  report_id: '报告 ID', duplicate_source_id: '重复来源 ID',
  error_kind: '失败分类', status_code: 'HTTP 状态', reason: '过滤原因',
  reader: '读取器', provider: '服务提供方', query: '实际查询词', model: '模型',
  provider_candidate_count: 'Provider 原始候选', entity_filtered_count: '实体过滤数量',
  input_tokens: '输入 Token', output_tokens: '输出 Token',
};

const errorKindLabel: Record<string, string> = {
  access_blocked: '访问被阻断 / WAF',
  rate_limited: '访问频率受限',
  authentication_required: '源站要求认证',
  network_error: '网络连接失败',
  upstream_error: '源站服务异常',
  crawler_unavailable: 'Crawl4AI 未配置',
  crawler_network_error: 'Crawl4AI 网络错误',
  crawler_http_error: 'Crawl4AI 返回错误',
  crawler_invalid_response: 'Crawl4AI 返回格式异常',
  crawler_empty_result: 'Crawl4AI 未返回页面',
  empty_content: '页面正文为空',
  response_too_large: '页面超过大小限制',
  redirect: '重定向不符合规则',
  redirect_limit: '重定向次数超限',
  deferred: '域名访问处于冷却或排队',
  invalid_source: '来源地址无效',
  robots: 'robots 规则拒绝',
  district_mismatch: '区级行政区不匹配',
  report_generation_failed: '远端报告生成失败',
  report_validation_failed: '报告结构或引用校验失败',
};

const formatDetailValue = (key: string, value: unknown) => {
  if (key === 'error_kind') return errorKindLabel[String(value)] ?? String(value);
  if (key === 'reason') return errorKindLabel[String(value)] ?? String(value);
  if (key === 'reader') return String(value) === 'crawl4ai' ? 'Crawl4AI 动态读取' : '原生 HTTPS 读取';
  return String(value);
};

const InspectorRow: React.FC<{label: string; value: string}> = ({label, value}) => (
  <div className="flex items-start justify-between gap-3"><dt className="text-slate-500">{label}</dt><dd className="text-right font-semibold text-slate-300">{value}</dd></div>
);

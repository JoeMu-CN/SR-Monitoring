import React, {useCallback, useEffect, useRef, useState} from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {api, type AgentStatusRead, type ToolCallRead} from '../api';
import type {RiskItem, Supplier, ChatMessage, ToolCall, ExternalCompanyCheck, TianYanChaQuota, RiskLevel} from '../types';
import {RiskAssistantStepTimeline} from './RiskAssistantStepTimeline';
import {useRiskAssistantStepTimeline} from './useRiskAssistantStepTimeline';

// 供应商卡片徽标按真实当前等级显示，等级名称与 RiskDetailView 一致；无当前等级才是「正常监控」。
const RISK_LEVEL_BADGE: Record<RiskLevel, {label: string; className: string}> = {
  P1: {label: '重大风险', className: 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300'},
  P2: {label: '高风险', className: 'bg-amber-100 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300'},
  P3: {label: '中风险', className: 'bg-blue-100 text-blue-700 dark:bg-blue-950/60 dark:text-blue-300'},
  P4: {label: '低风险', className: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'},
};

const NO_RISK_BADGE = {
  label: '正常监控',
  className: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300',
};

interface RiskAssistantViewProps {
  riskItems: RiskItem[];
  suppliers: Supplier[];
  agentStatus: AgentStatusRead | null;
  onSelectRisk: (risk: RiskItem) => void;
  onSelectSupplier: (supplier: Supplier) => void;
  pendingQuery?: string | null;
  onClearPendingQuery?: () => void;
  // 清单内供应商核查会真实写入正式告警；助手回答落地后由父级重取风险列表，
  // 避免页面停留在核查前的旧快照（以前必须刷新浏览器才更新）。
  onAlertsChanged?: () => Promise<void>;
}

// 清单内核查（verify_company）按规则引擎创建或更新正式告警，返回的 alert_ids 非空
// 才是风险列表失效的唯一信号；只读工具与未产生告警的核查都不需要重取。
const writesRiskAlerts = (calls: readonly ToolCallRead[]): boolean =>
  calls.some((call) => {
    if (call.name !== 'verify_company') return false;
    const alertIds = call.result.alert_ids;
    return Array.isArray(alertIds) && alertIds.length > 0;
  });

export const RiskAssistantView: React.FC<RiskAssistantViewProps> = ({
  riskItems,
  suppliers,
  agentStatus,
  onSelectRisk,
  onSelectSupplier,
  pendingQuery,
  onClearPendingQuery,
  onAlertsChanged,
}) => {
  const welcomeMessage = (): ChatMessage => ({
    id: 'msg-welcome',
    sender: 'assistant',
    timestamp: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
    content: `您好！我是 **SR 风险查询助手**。我可以帮助您查询当前启用的重点供应商、生产地点、供应产品，以及筛选当前有效的 P1–P4 风险提醒。天眼查网关启用后，还可发起清单外企业一次性工商核查并查询调用额度。`,
  });

  const [messages, setMessages] = useState<ChatMessage[]>(() => [welcomeMessage()]);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>({});
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [quota, setQuota] = useState<TianYanChaQuota | null>(null);

  const chatEndRef = useRef<HTMLDivElement>(null);
  const consumedPendingQueryRef = useRef<string | null>(null);
  // 运行期真实步骤时间线：轮询、合并与逐行展开全部由该控制器收口，
  // 回答返回 / 重置对话 / 组件卸载都通过 stop() 作废在途响应并清理定时器。
  const stepTimeline = useRiskAssistantStepTimeline();
  const stepRows = stepTimeline.rows;
  const stopStepsPolling = stepTimeline.stop;
  const startStepsPolling = stepTimeline.start;

  const scrollToBottom = () => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping, stepRows.length]);

  const toggleToolExpand = (msgId: string) => {
    setExpandedTools((prev) => ({ ...prev, [msgId]: !prev[msgId] }));
  };

  // Preset query shortcuts
  const presetQueries = [
    '查询当前启用的所有 P1 严重风险提醒',
    '查询深圳和苏州地区的重点供应商及供应产品',
    '核查【杭州智造科技有限公司】的工商登记信息（清单外）',
    '查询天眼查 API 今日和本月调用额度',
    '筛选供应【微电子元件】与【特种溶剂】的供应商',
  ];

  const asRecord = (value: unknown): Record<string, unknown> | null =>
    value !== null && typeof value === 'object' && !Array.isArray(value)
      ? value as Record<string, unknown>
      : null;

  const asRecords = (value: unknown): Array<Record<string, unknown>> =>
    Array.isArray(value) ? value.map(asRecord).filter((item): item is Record<string, unknown> => item !== null) : [];

  const mapQuota = (value: Record<string, unknown>): TianYanChaQuota | null => {
    const dailyLimit = Number(value.daily_limit);
    const monthlyLimit = Number(value.monthly_limit);
    if (!Number.isFinite(dailyLimit) || !Number.isFinite(monthlyLimit)) return null;
    const dailyUsed = Number(value.daily_used ?? 0);
    const monthlyUsed = Number(value.monthly_used ?? 0);
    return {
      dailyUsed,
      dailyLimit,
      monthlyUsed,
      monthlyLimit,
      lastResetTime: '北京时间每日及每月自动重置',
      status: dailyUsed >= dailyLimit || monthlyUsed >= monthlyLimit ? 'exceeded' : 'normal',
    };
  };

  // 清单内供应商的核查只读库内已采集信号（source=database），工商字段仅存在于 content 文本
  // （格式：企业：X；统一社会信用代码：Y；登记状态：Z；候选1：...），需解析文本补齐卡片。
  const parseExternalCheckContent = (value: unknown): {
    companyName?: string;
    creditCode?: string;
    regStatus?: string;
    candidateNames: string[];
  } | null => {
    if (typeof value !== 'string' || value.trim() === '') return null;
    const fields: {companyName?: string; creditCode?: string; regStatus?: string; candidateNames: string[]} = {
      candidateNames: [],
    };
    for (const segment of value.split('；')) {
      const separator = segment.indexOf('：');
      if (separator <= 0) continue;
      const key = segment.slice(0, separator).trim();
      const fieldValue = segment.slice(separator + 1).trim();
      if (fieldValue === '') continue;
      if (key === '企业') fields.companyName = fieldValue;
      else if (key === '统一社会信用代码') fields.creditCode = fieldValue;
      else if (key === '登记状态') fields.regStatus = fieldValue;
      else if (/^候选\d+$/.test(key)) fields.candidateNames.push(fieldValue);
    }
    return fields;
  };

  // 主字段回退顺序：结构化顶层 → content 文本 → 结构化候选 → 占位。
  // 候选映射里的「未披露」只是占位而非真实值，必须跳过，否则会掩盖 content 解析结果。
  const firstExternalField = (fallback: string, ...values: Array<unknown>): string => {
    for (const value of values) {
      if (typeof value === 'string' && value.trim() !== '' && value !== '未披露') return value;
    }
    return fallback;
  };

  const mapExternalCheck = (result: Record<string, unknown>): ExternalCompanyCheck | undefined => {
    if (result.status !== 'success') return undefined;
    const candidates = asRecords(result.candidates).map((candidate) => ({
      name: String(candidate.name ?? '未披露'),
      creditCode: String(candidate.credit_code ?? '未披露'),
      status: String(candidate.reg_status ?? '未披露'),
    }));
    const contentFields = parseExternalCheckContent(result.content);
    // 库内信号没有结构化候选：候选列表用 content 文本中解析出的候选名补齐。
    const resolvedCandidates = candidates.length > 0
      ? candidates
      : (contentFields?.candidateNames ?? []).map((name) => ({name, creditCode: '未披露', status: '未披露'}));
    return {
      companyName: firstExternalField('核查企业', result.company_name, contentFields?.companyName, candidates[0]?.name),
      registrationNo: firstExternalField('未披露', result.credit_code, contentFields?.creditCode, candidates[0]?.creditCode),
      operatingStatus: firstExternalField('未披露', result.reg_status, contentFields?.regStatus, candidates[0]?.status),
      candidates: resolvedCandidates,
      checkTime: new Date().toLocaleString('zh-CN'),
      source: result.source === 'database' ? '库内已采集核查' : '天眼查 MCP 实时核查',
      isExternal: true,
    };
  };

  const toolDescription: Record<string, string> = {
    query_suppliers: '检索启用中的重点供应商、地点与产品',
    query_current_alerts: '检索当前有效的 P1–P4 风险提醒',
    verify_company: '执行清单外企业一次性工商核查',
    get_budget: '查询天眼查真实调用额度',
  };

  // 运行期步骤文案与合并规则已收敛到 useRiskAssistantStepTimeline，视图不再重复维护。

  // 运行令牌：优先随机 UUID；旧环境无 crypto.randomUUID 时退化为时间戳 + 随机串，保证非空。
  const createRunToken = (): string =>
    crypto.randomUUID?.() ?? `run-${Date.now()}-${Math.random().toString(36).slice(2)}`;

  const buildResponseMessage = (answer: string, calls: ToolCallRead[]): ChatMessage => {
    const toolCalls: ToolCall[] = calls.map((call, index) => ({
      id: `tool-${Date.now()}-${index}`,
      toolName: call.name,
      description: toolDescription[call.name] ?? '执行只读查询工具',
      params: call.arguments,
      result: call.result,
      durationMs: 0,
      status: call.result.status === 'error' ? 'failed' : call.result.status === 'not_configured' || call.result.status === 'quota_exhausted' ? 'warning' : 'success',
      resultCount: typeof call.result.total === 'number' ? call.result.total : undefined,
    }));
    const alertIds = new Set(calls
      .filter((call) => call.name === 'query_current_alerts')
      .flatMap((call) => asRecords(call.result.items))
      .map((item) => String(item.alert_id)));
    const supplierIds = new Set(calls
      .filter((call) => call.name === 'query_suppliers')
      .flatMap((call) => asRecords(call.result.items))
      .map((item) => String(item.id)));
    const verifyResult = calls.find((call) => call.name === 'verify_company')?.result;
    const usageResult = calls.find((call) => call.name === 'get_budget')?.result
      ?? asRecord(verifyResult?.usage);
    const nextQuota = usageResult ? mapQuota(usageResult) : null;
    if (nextQuota) setQuota(nextQuota);
    return {
      id: `asst-${Date.now()}`,
      sender: 'assistant',
      timestamp: new Date().toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'}),
      content: answer,
      toolCalls,
      data: {
        riskCards: riskItems.filter((risk) => alertIds.has(risk.id)),
        supplierCards: suppliers.filter((supplier) => supplierIds.has(supplier.id)),
        externalCheckCard: verifyResult ? mapExternalCheck(verifyResult) : undefined,
        quotaCard: nextQuota ?? undefined,
      },
    };
  };

  const handleSend = useCallback(async (textToSend?: string) => {
    const queryText = (textToSend || input).trim();
    if (!queryText || isTyping) return;

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      sender: 'user',
      timestamp: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
      content: queryText,
    };

    setMessages((prev) => [...prev, userMsg]);
    if (!textToSend) setInput('');
    setIsTyping(true);

    // 每次发送使用新的运行令牌；start 内部会先作废上一轮并清空旧步骤，再按 700ms 增量轮询。
    const runToken = createRunToken();
    startStepsPolling(runToken);

    try {
      const response = await api.chat(queryText, sessionId, runToken);
      setSessionId(response.session_id);
      const responseMsg = buildResponseMessage(response.answer, response.tool_calls);
      setMessages((prev) => [...prev, responseMsg]);
      // 核查已写入告警：立即重取风险列表，不必等浏览器刷新。
      // 刷新失败只追加次级提示，不得把已成功的核查改写成查询失败。
      if (onAlertsChanged && writesRiskAlerts(response.tool_calls)) {
        try {
          await onAlertsChanged();
        } catch (caught) {
          const reason = caught instanceof Error ? caught.message : '未知原因';
          setMessages((prev) => [...prev, {
            id: `asst-refresh-${Date.now()}`,
            sender: 'assistant',
            timestamp: new Date().toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'}),
            content: `风险列表刷新失败：${reason}。当前风险监控页仍显示核查前的数据，请手动刷新页面。`,
          }]);
        }
      }
    } catch (caught) {
      setMessages((prev) => [...prev, {
        id: `asst-error-${Date.now()}`,
        sender: 'assistant',
        timestamp: new Date().toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'}),
        content: `查询失败：${caught instanceof Error ? caught.message : '助手服务暂时不可用'}。请检查服务状态后重试。`,
      }]);
    } finally {
      stopStepsPolling();
      setIsTyping(false);
    }
  }, [input, isTyping, onAlertsChanged, riskItems, sessionId, suppliers, startStepsPolling, stopStepsPolling]);

  useEffect(() => {
    const query = pendingQuery?.trim();
    if (!query) {
      consumedPendingQueryRef.current = null;
      return;
    }
    // 同一 pendingQuery 只消费一次，避免回调身份变化导致重复预填/重复清理。
    if (consumedPendingQueryRef.current === query) return;
    consumedPendingQueryRef.current = query;
    setInput(pendingQuery ?? '');
    onClearPendingQuery?.();
  }, [onClearPendingQuery, pendingQuery]);

  const getRiskBadgeColor = (level: string) => {
    switch (level) {
      case 'P1':
        return 'bg-red-100 text-[#C92A2A] dark:bg-red-950/60 dark:text-red-300 border-red-200 dark:border-red-900';
      case 'P2':
        return 'bg-amber-100 text-[#D97706] dark:bg-amber-950/60 dark:text-amber-300 border-amber-200 dark:border-amber-900';
      case 'P3':
        return 'bg-yellow-100 text-yellow-800 dark:bg-yellow-950/60 dark:text-yellow-300 border-yellow-200 dark:border-yellow-900';
      default:
        return 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 border-slate-200 dark:border-slate-700';
    }
  };

  return (
    <div className="flex w-full flex-col gap-5 lg:h-[calc(100vh-120px)] lg:min-h-[600px]">
      {/* MAIN COLUMN: Chat Assistant (full width) */}
      <div className="flex h-[calc(100dvh-150px)] min-h-[520px] flex-col overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-[#101d28] lg:h-full">
        {/* Assistant Top Banner */}
        <div className="bg-[#ecf4ff] dark:bg-slate-900/80 px-4 py-3 border-b border-[#c2c6d2] dark:border-slate-800 flex items-center justify-between gap-3 flex-shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-[#004782] text-white flex items-center justify-center font-bold shadow-xs">
              <span className="material-symbols-outlined text-[20px]">smart_toy</span>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="font-extrabold text-[15px] text-[#101d28] dark:text-white leading-none">
                  风险查询助手
                </h2>
                <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-900/50 text-[#004782] dark:text-blue-300 border border-blue-200 dark:border-blue-800">
                  只读查询
                </span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                stopStepsPolling();
                setIsTyping(false);
                setMessages([welcomeMessage()]);
                setSessionId(null);
                setQuota(null);
                setExpandedTools({});
              }}
              className="text-[#424751] dark:text-slate-300 hover:text-[#004782] p-1.5 rounded-lg hover:bg-slate-200 dark:hover:bg-slate-800 transition-colors text-[12px] flex items-center gap-1"
              title="清空对话记录"
            >
              <span className="material-symbols-outlined text-[18px]">refresh</span>
              <span className="hidden md:inline">重置对话</span>
            </button>
          </div>
        </div>

        {/* Chat Messages Scroll Container */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-5 bg-[#f7f9ff]/50 dark:bg-[#101d28]/30">
          <AnimatePresence initial={false}>
            {messages.map((msg) => (
              <motion.div
                key={msg.id}
                initial={{ opacity: 0, y: 14, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, scale: 0.96 }}
                transition={{ duration: 0.25, ease: 'easeOut' }}
                className={`flex flex-col ${
                  msg.sender === 'user' ? 'items-end' : 'items-start'
                } space-y-2 max-w-full`}
              >
                {/* Message Header */}
                <div className="flex items-center gap-2 px-1 text-[11px] text-[#727782] dark:text-slate-400">
                  <span className="font-semibold">
                    {msg.sender === 'user' ? '采购决策员' : 'SR 风险查询助手'}
                  </span>
                  <span>•</span>
                  <span>{msg.timestamp}</span>
                </div>

                {/* Message Bubble */}
                <div
                  className={`p-4 rounded-2xl text-[14px] leading-relaxed shadow-xs max-w-[92%] sm:max-w-[85%] ${
                    msg.sender === 'user'
                      ? 'bg-[#185fa5] text-white rounded-tr-none'
                      : 'bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 text-[#101d28] dark:text-slate-100 rounded-tl-none'
                  }`}
                >
                  {/* Formatted Text Content */}
                  <div className="whitespace-pre-wrap space-y-2">
                    {msg.content.split('\n\n').map((paragraph, pIdx) => (
                      <p key={pIdx}>
                        {paragraph.split('**').map((part, bIdx) =>
                          bIdx % 2 === 1 ? (
                            <strong key={bIdx} className="font-bold text-[#004782] dark:text-blue-300">
                              {part}
                            </strong>
                          ) : (
                            part
                          )
                        )}
                      </p>
                    ))}
                  </div>

                  {/* Collapsible Tool Call / Query Evidence Accordion */}
                  {msg.toolCalls && msg.toolCalls.length > 0 && (
                    <div className="mt-3 pt-3 border-t border-[#c2c6d2]/50 dark:border-slate-800">
                      <button
                        onClick={() => toggleToolExpand(msg.id)}
                        className="w-full flex items-center justify-between p-2 rounded-lg bg-[#ecf4ff]/80 dark:bg-slate-800/80 hover:bg-[#d6e4f3] dark:hover:bg-slate-800 transition-all text-[12px] font-medium text-[#004782] dark:text-blue-300"
                      >
                        <div className="flex items-center gap-2">
                          <span className="material-symbols-outlined text-[16px]">build_circle</span>
                          <span>查询依据与工具调用 ({msg.toolCalls.length} 项)</span>
                        </div>
                        <span className="material-symbols-outlined text-[18px]">
                          {expandedTools[msg.id] ? 'expand_less' : 'expand_more'}
                        </span>
                      </button>

                      <AnimatePresence>
                        {expandedTools[msg.id] && (
                          <motion.div
                            initial={{ height: 0, opacity: 0 }}
                            animate={{ height: 'auto', opacity: 1 }}
                            exit={{ height: 0, opacity: 0 }}
                            transition={{ duration: 0.2 }}
                            className="mt-2 space-y-2 p-2.5 rounded-lg bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 text-[11px] font-mono overflow-hidden"
                          >
                            {msg.toolCalls.map((tool) => (
                              <div
                                key={tool.id}
                                className="p-2 rounded bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 space-y-1"
                              >
                                <div className="flex justify-between items-center font-bold text-[#004782] dark:text-blue-400">
                                  <span className="flex items-center gap-1">
                                    <span className="material-symbols-outlined text-[14px]">terminal</span>
                                    {tool.toolName}
                                  </span>
                                  <span className={`text-[10px] ${tool.status === 'success' ? 'text-emerald-600 dark:text-emerald-400' : tool.status === 'warning' ? 'text-amber-600 dark:text-amber-400' : 'text-red-600 dark:text-red-400'}`}>
                                    {tool.status === 'success' ? '调用完成' : tool.status === 'warning' ? '能力受限' : '调用失败'}
                                  </span>
                                </div>
                                <div className="text-slate-600 dark:text-slate-300">
                                  描述: {tool.description}
                                </div>
                                <div className="text-slate-400 dark:text-slate-500 truncate">
                                  参数: {JSON.stringify(tool.params)}
                                </div>
                              </div>
                            ))}
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>
                  )}

                {/* Structured Cards Render Engine */}
                {msg.data && (
                  <div className="mt-4 space-y-3">
                    {/* 1. Risk Cards */}
                    {msg.data.riskCards && msg.data.riskCards.length > 0 && (
                      <div className="space-y-2">
                        <div className="text-[12px] font-bold text-[#424751] dark:text-slate-400 flex items-center gap-1.5">
                          <span className="material-symbols-outlined text-[16px] text-[#C92A2A]">
                            warning
                          </span>
                          <span>核心风险提醒卡片 ({msg.data.riskCards.length} 条)</span>
                        </div>
                        <div className="grid grid-cols-1 gap-2.5">
                          {msg.data.riskCards.map((risk) => (
                            <div
                              key={risk.id}
                              className="p-3.5 rounded-xl bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 shadow-xs hover:border-[#004782] transition-all"
                            >
                              <div className="flex items-start justify-between gap-2">
                                <div className="flex items-center gap-2">
                                  <span
                                    className={`text-[11px] font-bold px-2 py-0.5 rounded-md border ${getRiskBadgeColor(
                                      risk.level
                                    )}`}
                                  >
                                    {risk.level} {risk.levelName}
                                  </span>
                                  <h4 className="font-bold text-[14px] text-[#101d28] dark:text-white">
                                    {risk.companyName}
                                  </h4>
                                </div>
                                <span className="text-[11px] text-slate-400">{risk.updatedTime}</span>
                              </div>

                              <p className="text-[12px] text-slate-600 dark:text-slate-300 mt-2 line-clamp-2">
                                {risk.summary}
                              </p>

                              <div className="mt-3 pt-2.5 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between text-[11px]">
                                <div className="flex items-center gap-3 text-slate-500">
                                  <span>地点: {risk.location || '不详'}</span>
                                  <span>AI 置信度: {risk.aiConfidence}%</span>
                                </div>
                                <button
                                  onClick={() => onSelectRisk(risk)}
                                  className="text-[#004782] dark:text-blue-400 font-bold hover:underline flex items-center gap-0.5"
                                >
                                  <span>查看详情</span>
                                  <span className="material-symbols-outlined text-[14px]">
                                    chevron_right
                                  </span>
                                </button>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* 2. Supplier Cards */}
                    {msg.data.supplierCards && msg.data.supplierCards.length > 0 && (
                      <div className="space-y-2">
                        <div className="text-[12px] font-bold text-[#424751] dark:text-slate-400 flex items-center gap-1.5">
                          <span className="material-symbols-outlined text-[16px] text-[#004782]">
                            factory
                          </span>
                          <span>重点供应商台账 ({msg.data.supplierCards.length} 家)</span>
                        </div>
                        <div className="grid grid-cols-1 gap-2.5">
                          {msg.data.supplierCards.map((sup) => (
                            <div
                              key={sup.id}
                              className="p-3.5 rounded-xl bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 shadow-xs hover:border-[#004782] transition-all"
                            >
                              <div className="flex items-start justify-between gap-2">
                                <div>
                                  <div className="flex items-center gap-2">
                                    <span className="text-[10px] font-mono font-bold bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded text-slate-600 dark:text-slate-300">
                                      {sup.code}
                                    </span>
                                    <h4 className="font-bold text-[14px] text-[#101d28] dark:text-white">
                                      {sup.legalName}
                                    </h4>
                                  </div>
                                  <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
                                    生产地点: {sup.productionLocation} ｜ 供货: {sup.suppliedProduct}
                                  </div>
                                </div>
                                <span
                                  className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                                    (sup.riskLevel ? RISK_LEVEL_BADGE[sup.riskLevel] : NO_RISK_BADGE).className
                                  }`}
                                >
                                  {(sup.riskLevel ? RISK_LEVEL_BADGE[sup.riskLevel] : NO_RISK_BADGE).label}
                                </span>
                              </div>

                              <div className="mt-3 pt-2.5 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between text-[11px]">
                                <div className="flex items-center gap-2">
                                  <span className="bg-blue-50 text-[#004782] dark:bg-blue-950/60 dark:text-blue-300 px-2 py-0.5 rounded font-medium">
                                    {sup.tier}
                                  </span>
                                  <span className="text-slate-500">{sup.category}</span>
                                </div>
                                <button
                                  onClick={() => onSelectSupplier(sup)}
                                  className="text-[#004782] dark:text-blue-400 font-bold hover:underline flex items-center gap-0.5"
                                >
                                  <span>查看供应商档案</span>
                                  <span className="material-symbols-outlined text-[14px]">
                                    chevron_right
                                  </span>
                                </button>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* 3. External Company Verification Card (天眼查 API) */}
                    {msg.data.externalCheckCard && (
                      <div className="p-4 rounded-xl bg-slate-50 dark:bg-slate-900/90 border-2 border-blue-200 dark:border-blue-900 shadow-sm space-y-3">
                        <div className="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-2">
                          <div className="flex items-center gap-2">
                            <span className="material-symbols-outlined text-[20px] text-blue-600">
                              verified
                            </span>
                            <span className="font-extrabold text-[15px] text-[#101d28] dark:text-white">
                              {msg.data.externalCheckCard.companyName}
                            </span>
                          </div>
                          <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-blue-100 dark:bg-blue-900/60 text-[#004782] dark:text-blue-300">
                            {msg.data.externalCheckCard.source}
                          </span>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px]">
                          <div className="bg-white dark:bg-slate-800 p-2 rounded border border-slate-200 dark:border-slate-700">
                            <span className="text-slate-400 block">统一社会信用代码</span>
                            <span className="font-bold text-slate-800 dark:text-slate-100">
                              {msg.data.externalCheckCard.registrationNo}
                            </span>
                          </div>
                          <div className="bg-white dark:bg-slate-800 p-2 rounded border border-slate-200 dark:border-slate-700">
                            <span className="text-slate-400 block">经营状态</span>
                            <span className="font-bold text-emerald-600 dark:text-emerald-400">
                              {msg.data.externalCheckCard.operatingStatus}
                            </span>
                          </div>
                          <div className="bg-white dark:bg-slate-800 p-2 rounded border border-slate-200 dark:border-slate-700">
                            <span className="text-slate-400 block">核查时间</span>
                            <span className="font-bold text-slate-800 dark:text-slate-100">
                              {msg.data.externalCheckCard.checkTime}
                            </span>
                          </div>
                        </div>

                        {/* Candidate Companies */}
                        <div className="p-3 rounded-lg bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 space-y-1.5">
                          <div className="text-[11px] font-bold text-slate-600 dark:text-slate-300">
                            匹配候选企业：
                          </div>
                          <div className="space-y-1 text-[11px]">
                            {msg.data.externalCheckCard.candidates.map((candidate, index) => (
                              <div key={`${candidate.creditCode}-${index}`} className="flex items-center justify-between gap-2 px-2 py-1 bg-slate-50 dark:bg-slate-900 rounded">
                                <span className="font-medium text-slate-700 dark:text-slate-200 truncate">{candidate.name}</span>
                                <span className="text-slate-500 whitespace-nowrap">{candidate.status} · {candidate.creditCode}</span>
                              </div>
                            ))}
                          </div>
                        </div>

                        {/* Disclaimer */}
                        <div className="p-2.5 rounded-lg bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-900 text-[11px] text-amber-800 dark:text-amber-300 flex items-start gap-2">
                          <span className="material-symbols-outlined text-[16px] flex-shrink-0 mt-0.5">
                            info
                          </span>
                          <span>
                            此结果为清单外企业一次性核查快照，不自动加入内部常态监控，无对应内部供应商编码。
                          </span>
                        </div>
                      </div>
                    )}

                    {/* 4. TianYanCha Quota Card */}
                    {msg.data.quotaCard && (
                      <div className="p-4 rounded-xl bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 shadow-xs space-y-3">
                        <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-2">
                          <div className="flex items-center gap-2">
                            <span className="material-symbols-outlined text-[18px] text-[#004782]">
                              account_balance_wallet
                            </span>
                            <span className="font-bold text-[14px] text-[#101d28] dark:text-white">
                              天眼查 API 接口调用配额
                            </span>
                          </div>
                          <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
                            额度充裕
                          </span>
                        </div>

                        <div className="space-y-2.5 text-[12px]">
                          <div>
                            <div className="flex justify-between text-[#424751] dark:text-slate-400 mb-1">
                              <span>今日额度消耗</span>
                              <span className="font-mono font-bold text-[#101d28] dark:text-white">
                                {msg.data.quotaCard.dailyUsed} / {msg.data.quotaCard.dailyLimit} 次 (
                                {Math.round(
                                  (msg.data.quotaCard.dailyUsed / msg.data.quotaCard.dailyLimit) * 100
                                )}
                                %)
                              </span>
                            </div>
                            <div className="w-full h-2 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                              <div
                                className="h-full bg-[#185fa5] rounded-full transition-all duration-300"
                                style={{
                                  width: `${
                                    (msg.data.quotaCard.dailyUsed / msg.data.quotaCard.dailyLimit) *
                                    100
                                  }%`,
                                }}
                              ></div>
                            </div>
                          </div>

                          <div>
                            <div className="flex justify-between text-[#424751] dark:text-slate-400 mb-1">
                              <span>本月额度消耗</span>
                              <span className="font-mono font-bold text-[#101d28] dark:text-white">
                                {msg.data.quotaCard.monthlyUsed} / {msg.data.quotaCard.monthlyLimit} 次 (
                                {Math.round(
                                  (msg.data.quotaCard.monthlyUsed /
                                    msg.data.quotaCard.monthlyLimit) *
                                    100
                                )}
                                %)
                              </span>
                            </div>
                            <div className="w-full h-2 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                              <div
                                className="h-full bg-indigo-600 rounded-full transition-all duration-300"
                                style={{
                                  width: `${
                                    (msg.data.quotaCard.monthlyUsed /
                                      msg.data.quotaCard.monthlyLimit) *
                                    100
                                  }%`,
                                }}
                              ></div>
                            </div>
                          </div>
                        </div>

                        <div className="text-[11px] text-slate-400 pt-1 flex justify-between">
                          <span>重置时间: {msg.data.quotaCard.lastResetTime}</span>
                          <span>剩余每日额度: {msg.data.quotaCard.dailyLimit - msg.data.quotaCard.dailyUsed} 次</span>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>

          {/* 运行步骤时间线：每步一行、无行首图标；回答返回即整体撤下，不等展开队列。 */}
          {isTyping && <RiskAssistantStepTimeline rows={stepRows} />}

          <div ref={chatEndRef} />
        </div>

        {/* Chat Input Bar */}
        <div className="p-3 bg-white dark:bg-[#101d28] border-t border-[#c2c6d2] dark:border-slate-800 flex-shrink-0">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSend();
            }}
            className="flex items-center gap-2"
          >
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="请输入自然语言对话查询"
              className="flex-1 bg-[#f7f9ff] dark:bg-slate-800 border border-[#c2c6d2] dark:border-slate-700 rounded-xl px-4 py-2.5 text-[14px] text-[#101d28] dark:text-white focus:outline-none focus:ring-2 focus:ring-[#004782] transition-all"
            />

            <button
              type="submit"
              disabled={!input.trim() || isTyping}
              className="bg-[#004782] hover:bg-[#185fa5] disabled:opacity-50 text-white font-bold px-5 py-2.5 rounded-xl transition-all flex items-center gap-1.5 shadow-xs flex-shrink-0"
            >
              <span>发送</span>
              <span className="material-symbols-outlined text-[18px]">send</span>
            </button>
          </form>
        </div>
      </div>
    </div>
  );
};

import React, {useCallback, useEffect, useRef, useState} from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {api, type AgentStatusRead, type ToolCallRead} from '../api';
import type {RiskItem, Supplier, ChatMessage, ToolCall, ExternalCompanyCheck, TianYanChaQuota} from '../types';

interface RiskAssistantViewProps {
  riskItems: RiskItem[];
  suppliers: Supplier[];
  agentStatus: AgentStatusRead | null;
  onSelectRisk: (risk: RiskItem) => void;
  onSelectSupplier: (supplier: Supplier) => void;
  pendingQuery?: string | null;
  onClearPendingQuery?: () => void;
}

export const RiskAssistantView: React.FC<RiskAssistantViewProps> = ({
  riskItems,
  suppliers,
  agentStatus,
  onSelectRisk,
  onSelectSupplier,
  pendingQuery,
  onClearPendingQuery,
}) => {
  const welcomeMessage = (): ChatMessage => ({
    id: 'msg-welcome',
    sender: 'assistant',
    timestamp: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
    content: `您好！我是 **SR 风险查询助手**。我可以帮助您查询当前启用的重点供应商、生产地点、供应产品，以及筛选当前有效的 P1–P4 风险提醒。天眼查网关启用后，还可发起清单外企业一次性工商核查并查询调用额度。

💡 **只读提示**：本助手为**只读查询助手**，不具备新增/修改业务数据、更改监控状态或自动触发处置的权限。`,
    data: {
      riskCards: riskItems.filter((r) => r.level === 'P1').slice(0, 2),
    },
  });

  const [messages, setMessages] = useState<ChatMessage[]>(() => [welcomeMessage()]);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>({});
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [quota, setQuota] = useState<TianYanChaQuota | null>(null);

  const chatEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping]);

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

  const mapExternalCheck = (result: Record<string, unknown>): ExternalCompanyCheck | undefined => {
    if (result.status !== 'success') return undefined;
    const candidates = asRecords(result.candidates).map((candidate) => ({
      name: String(candidate.name ?? '未披露'),
      creditCode: String(candidate.credit_code ?? '未披露'),
      status: String(candidate.reg_status ?? '未披露'),
    }));
    return {
      companyName: String(result.company_name ?? candidates[0]?.name ?? '核查企业'),
      registrationNo: String(result.credit_code ?? candidates[0]?.creditCode ?? '未披露'),
      operatingStatus: String(result.reg_status ?? candidates[0]?.status ?? '未披露'),
      candidates,
      checkTime: new Date().toLocaleString('zh-CN'),
      source: '天眼查 MCP 实时核查',
      isExternal: true,
    };
  };

  const toolDescription: Record<string, string> = {
    query_suppliers: '检索启用中的重点供应商、地点与产品',
    query_current_alerts: '检索当前有效的 P1–P4 风险提醒',
    verify_company: '执行清单外企业一次性工商核查',
    get_budget: '查询天眼查真实调用额度',
  };

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

    try {
      const response = await api.chat(queryText, sessionId);
      setSessionId(response.session_id);
      const responseMsg = buildResponseMessage(response.answer, response.tool_calls);
      setMessages((prev) => [...prev, responseMsg]);
    } catch (caught) {
      setMessages((prev) => [...prev, {
        id: `asst-error-${Date.now()}`,
        sender: 'assistant',
        timestamp: new Date().toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'}),
        content: `查询失败：${caught instanceof Error ? caught.message : '助手服务暂时不可用'}。请检查服务状态后重试。`,
      }]);
    } finally {
      setIsTyping(false);
    }
  }, [input, isTyping, riskItems, sessionId, suppliers]);

  useEffect(() => {
    if (pendingQuery?.trim()) {
      void handleSend(pendingQuery);
      onClearPendingQuery?.();
    }
  }, [handleSend, onClearPendingQuery, pendingQuery]);

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
    <div className="flex w-full flex-col gap-5 lg:h-[calc(100vh-120px)] lg:min-h-[600px] lg:flex-row">
      {/* LEFT COLUMN: Main Chat Assistant (72% on desktop) */}
      <div className="flex h-[calc(100dvh-150px)] min-h-[520px] flex-col overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-[#101d28] lg:h-full lg:w-[72%]">
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
              <p className="text-[11px] text-[#424751] dark:text-slate-400 mt-0.5">
                支持检索重点供应商、风险提醒、生产地点、供应产品及清单外天眼查核查
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* AI Model Status Light */}
            <span className={`hidden md:inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1 rounded-lg border ${agentStatus?.llm_configured ? 'text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/40 border-emerald-200 dark:border-emerald-800' : 'text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/40 border-amber-200 dark:border-amber-800'}`}>
              <div className="relative flex items-center justify-center w-2 h-2">
                <motion.span
                  className={`absolute inline-flex h-full w-full rounded-full ${agentStatus?.llm_configured ? 'bg-emerald-500/60' : 'bg-amber-500/60'}`}
                  animate={{ scale: [1, 2.4, 1], opacity: [0.85, 0, 0.85] }}
                  transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
                />
                <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${agentStatus?.llm_configured ? 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.8)]' : 'bg-amber-500'}`} />
              </div>
              <span className="text-slate-500 dark:text-slate-400">模型状态:</span>
              {agentStatus?.llm_configured ? agentStatus.model : '未配置'}
            </span>

            {/* External Check TianYanCha Status Light */}
            <span className={`hidden sm:inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1 rounded-lg border ${agentStatus?.tyc_enabled ? 'text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950/40 border-blue-200 dark:border-blue-800' : 'text-[#424751] dark:text-slate-300 bg-slate-50 dark:bg-slate-800 border-slate-200 dark:border-slate-700'}`}>
              <div className="relative flex items-center justify-center w-2 h-2">
                <motion.span
                  className="absolute inline-flex h-full w-full rounded-full bg-blue-500/60"
                  animate={{ scale: [1, 2.4, 1], opacity: [0.85, 0, 0.85] }}
                  transition={{ duration: 2.2, repeat: Infinity, ease: 'easeInOut' }}
                />
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-blue-500 shadow-[0_0_6px_rgba(59,130,246,0.8)]" />
              </div>
              <span className="text-slate-500 dark:text-slate-400">外部核查:</span>
              {agentStatus?.tyc_enabled ? '天眼查已启用' : '未启用'}
            </span>

            <button
              onClick={() => {
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
                                    sup.monitoringStatus === 'high_risk'
                                      ? 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300'
                                      : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300'
                                  }`}
                                >
                                  {sup.monitoringStatus === 'high_risk' ? '高危预警' : '正常监控'}
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

          {/* Typing Indicator */}
          {isTyping && (
            <div className="flex items-center gap-2 text-slate-400 text-[12px] italic p-2">
              <span className="material-symbols-outlined text-[18px] animate-spin">sync</span>
              <span>助手正在执行只读查询...</span>
            </div>
          )}

          <div ref={chatEndRef} />
        </div>

        {/* Quick Presets Prompt Bar */}
        <div className="p-2.5 bg-slate-50 dark:bg-slate-900 border-t border-[#c2c6d2]/50 dark:border-slate-800 flex items-center gap-2 overflow-x-auto no-scrollbar flex-shrink-0">
          <span className="text-[11px] font-bold text-[#424751] dark:text-slate-400 whitespace-nowrap pl-2">
            快捷提问:
          </span>
          {presetQueries.map((query, idx) => (
            <button
              key={idx}
              onClick={() => handleSend(query)}
              className="px-3 py-1 bg-white dark:bg-slate-800 border border-[#c2c6d2] dark:border-slate-700 hover:border-[#004782] dark:hover:border-blue-400 hover:bg-[#ecf4ff] dark:hover:bg-slate-700 rounded-full text-[12px] text-[#101d28] dark:text-slate-200 transition-all whitespace-nowrap flex-shrink-0"
            >
              {query}
            </button>
          ))}
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
            <div className="relative flex-1">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="请输入自然语言查询，如：'查询深圳的高危供应商'，'核查【杭州智造】'..."
                className="w-full bg-[#f7f9ff] dark:bg-slate-800 border border-[#c2c6d2] dark:border-slate-700 rounded-xl px-4 py-2.5 text-[14px] text-[#101d28] dark:text-white focus:outline-none focus:ring-2 focus:ring-[#004782] transition-all pr-24"
              />
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] font-bold text-[#004782] dark:text-blue-300 bg-blue-50 dark:bg-blue-950 px-2 py-0.5 rounded border border-blue-200 dark:border-blue-800">
                只读检索
              </span>
            </div>

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

      {/* RIGHT COLUMN: Context Panel & Capabilities Scope (28% on desktop) */}
      <div className="flex h-auto flex-col gap-4 overflow-visible lg:h-full lg:w-[28%] lg:overflow-y-auto lg:pr-1">
        {/* Quick System Status Card */}
        <div className="p-4 rounded-2xl bg-white dark:bg-[#101d28] border border-[#c2c6d2] dark:border-slate-800 shadow-xs space-y-3">
          <h3 className="font-bold text-[14px] text-[#101d28] dark:text-white flex items-center gap-2">
            <span className="material-symbols-outlined text-[18px] text-[#004782]">analytics</span>
            <span>核心监控数据概览</span>
          </h3>

          <div className="grid grid-cols-2 gap-2 text-[12px]">
            <div className="p-2.5 rounded-xl bg-[#ecf4ff] dark:bg-slate-800 border border-[#c2c6d2]/50">
              <span className="text-[#424751] dark:text-slate-400 block text-[11px]">重点供应商</span>
              <span className="font-bold text-[18px] text-[#004782] dark:text-blue-300">
                {suppliers.length} 家
              </span>
            </div>
            <div className="p-2.5 rounded-xl bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900">
              <span className="text-red-700 dark:text-red-300 block text-[11px]">P1 严重风险</span>
              <span className="font-bold text-[18px] text-[#C92A2A] dark:text-red-400">
                {riskItems.filter((r) => r.level === 'P1').length} 条
              </span>
            </div>
          </div>
        </div>

        {/* TianYanCha Quota Quick Widget */}
        <div className="p-4 rounded-2xl bg-white dark:bg-[#101d28] border border-[#c2c6d2] dark:border-slate-800 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-bold text-[14px] text-[#101d28] dark:text-white flex items-center gap-2">
              <span className="material-symbols-outlined text-[18px] text-blue-600">domain</span>
              <span>天眼查接口额度</span>
            </h3>
            <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${agentStatus?.tyc_enabled ? 'text-emerald-700 bg-emerald-50' : 'text-[#424751] bg-slate-100'}`}>
              {agentStatus?.tyc_enabled ? (quota?.status === 'exceeded' ? '额度已用尽' : '已启用') : '未启用'}
            </span>
          </div>

          <div className="space-y-2 text-[12px]">
            <div>
              <div className="flex justify-between text-slate-500 text-[11px]">
                <span>今日使用</span>
                <span className="font-bold text-slate-800 dark:text-slate-200">
                  {quota ? `${quota.dailyUsed} / ${quota.dailyLimit} 次` : '查询后显示'}
                </span>
              </div>
              <div className="w-full h-1.5 bg-slate-100 dark:bg-slate-800 rounded-full mt-1 overflow-hidden">
                <div
                  className="h-full bg-[#185fa5] rounded-full"
                  style={{width: `${quota && quota.dailyLimit > 0 ? (quota.dailyUsed / quota.dailyLimit) * 100 : 0}%`}}
                ></div>
              </div>
            </div>

            <div>
              <div className="flex justify-between text-slate-500 text-[11px]">
                <span>本月使用</span>
                <span className="font-bold text-slate-800 dark:text-slate-200">
                  {quota ? `${quota.monthlyUsed} / ${quota.monthlyLimit} 次` : '查询后显示'}
                </span>
              </div>
              <div className="w-full h-1.5 bg-slate-100 dark:bg-slate-800 rounded-full mt-1 overflow-hidden">
                <div
                  className="h-full bg-indigo-500 rounded-full"
                  style={{width: `${quota && quota.monthlyLimit > 0 ? (quota.monthlyUsed / quota.monthlyLimit) * 100 : 0}%`}}
                ></div>
              </div>
            </div>
          </div>

          <button
            onClick={() => handleSend('查询天眼查 API 调用额度')}
            className="w-full py-1.5 bg-[#f7f9ff] dark:bg-slate-800 hover:bg-[#ecf4ff] border border-[#c2c6d2] dark:border-slate-700 rounded-xl text-[12px] text-[#004782] dark:text-blue-300 font-bold transition-all"
          >
            刷新额度卡片
          </button>
        </div>

        {/* Quick Action Shortcuts */}
        <div className="p-4 rounded-2xl bg-white dark:bg-[#101d28] border border-[#c2c6d2] dark:border-slate-800 shadow-xs space-y-2.5">
          <h3 className="font-bold text-[14px] text-[#101d28] dark:text-white flex items-center gap-2">
            <span className="material-symbols-outlined text-[18px] text-[#004782]">tune</span>
            <span>快捷风险指令</span>
          </h3>

          <div className="space-y-1.5 text-[12px]">
            <button
              onClick={() => handleSend('查询当前所有 P1 严重风险提醒')}
              className="w-full text-left p-2.5 rounded-xl bg-slate-50 dark:bg-slate-800/60 hover:bg-[#ecf4ff] dark:hover:bg-slate-800 transition-all font-medium text-[#101d28] dark:text-slate-200 flex items-center justify-between"
            >
              <span>查看全部 P1 极高风险</span>
              <span className="material-symbols-outlined text-[16px] text-[#C92A2A]">
                arrow_forward
              </span>
            </button>

            <button
              onClick={() => handleSend('查询供应【微电子元件】的重点供应商')}
              className="w-full text-left p-2.5 rounded-xl bg-slate-50 dark:bg-slate-800/60 hover:bg-[#ecf4ff] dark:hover:bg-slate-800 transition-all font-medium text-[#101d28] dark:text-slate-200 flex items-center justify-between"
            >
              <span>按微电子品类检索供应商</span>
              <span className="material-symbols-outlined text-[16px] text-[#004782]">
                arrow_forward
              </span>
            </button>

            <button
              onClick={() => handleSend('核查【杭州智造科技有限公司】的工商登记信息（清单外）')}
              className="w-full text-left p-2.5 rounded-xl bg-slate-50 dark:bg-slate-800/60 hover:bg-[#ecf4ff] dark:hover:bg-slate-800 transition-all font-medium text-[#101d28] dark:text-slate-200 flex items-center justify-between"
            >
              <span>清单外企业工商核查示例</span>
              <span className="material-symbols-outlined text-[16px] text-blue-600">
                arrow_forward
              </span>
            </button>
          </div>
        </div>

        {/* Capability Boundaries Notice Card */}
        <div className="p-4 rounded-2xl bg-[#ecf4ff]/70 dark:bg-slate-900 border border-blue-200 dark:border-blue-900 text-[12px] space-y-2">
          <div className="flex items-center gap-1.5 font-bold text-[#004782] dark:text-blue-300">
            <span className="material-symbols-outlined text-[18px]">verified_user</span>
            <span>能力与只读边界说明</span>
          </div>

          <div className="space-y-1.5 text-[#424751] dark:text-slate-300 leading-relaxed text-[11px]">
            <div className="flex items-start gap-1">
              <span className="text-emerald-600 font-bold">✓ 支持：</span>
              <span>检索启用供应商、查询生产地点与产品、按风险等级与城市筛选提醒、清单外天眼查核查、额度查询。</span>
            </div>
            <div className="flex items-start gap-1">
              <span className="text-red-600 font-bold">✕ 禁止：</span>
              <span>新增或删除供应商、改动监控状态、手动重置风险得分或触发一键处置。</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

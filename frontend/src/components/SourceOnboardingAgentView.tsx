import {useEffect, useRef, useState} from 'react';
import {motion} from 'motion/react';
import {api, type AgentStatusRead, type ChatResponse, type ToolCallRead} from '../api';

interface SourceOnboardingAgentViewProps {
  agentStatus: AgentStatusRead | null;
  role: 'viewer' | 'admin';
  onRoleChange: (role: 'viewer' | 'admin') => void;
  initialDraftId: number | null;
}

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  tools?: ToolCallRead[];
}

const steps = [
  {id: 'source_url', label: '官方地址'},
  {id: 'collection_goal', label: '采集目标'},
  {id: 'access_authorization', label: '授权条件'},
  {id: 'source_identity_schedule', label: '名称与周期'},
  {id: 'generate_adapter', label: '探测与预览'},
];

const toolLabels: Record<string, string> = {
  inspect_source_url: '探测数据源页面',
  preview_source_adapter: '实时联网预览',
  create_source_adapter_draft: '创建适配器草稿',
  publish_source_adapter: '发布适配器',
  run_source_now: '立即正式采集',
};

const welcomeMessage = (): Message => ({
  id: 'source-agent-welcome',
  role: 'assistant',
  content: '我会用 5 个步骤引导完成接入：一次只确认一项信息。中途关闭页面会自动保留草稿；不会保存明文账号、密码、Cookie 或 Token。',
});

export function SourceOnboardingAgentView({
  agentStatus,
  role,
  onRoleChange,
  initialDraftId,
}: SourceOnboardingAgentViewProps) {
  const [messages, setMessages] = useState<Message[]>(() => [welcomeMessage()]);
  const [input, setInput] = useState('');
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [draftId, setDraftId] = useState<number | null>(null);
  const [currentStep, setCurrentStep] = useState('source_url');
  const [isSending, setIsSending] = useState(false);
  const restoredDraftId = useRef<number | null>(null);
  const activeStepIndex = steps.findIndex((step) => step.id === currentStep);
  const completed = currentStep === 'completed';

  const applyResponse = (response: ChatResponse) => {
    setSessionId(response.session_id);
    if (response.onboarding_draft) {
      setDraftId(response.onboarding_draft.id);
      setCurrentStep(response.onboarding_draft.current_step);
    }
    setMessages((current) => [...current, {
      id: `source-assistant-${Date.now()}`,
      role: 'assistant',
      content: response.answer,
      tools: response.tool_calls,
    }]);
  };

  const send = async (question: string, targetDraftId = draftId, targetSessionId = sessionId) => {
    if (!question.trim() || isSending || role !== 'admin') return;
    const now = Date.now();
    setMessages((current) => [...current, {id: `source-user-${now}`, role: 'user', content: question}]);
    setInput('');
    setIsSending(true);
    try {
      applyResponse(await api.sourceAgentChat(question, targetSessionId, targetDraftId));
    } catch (caught) {
      setMessages((current) => [...current, {
        id: `source-error-${Date.now()}`,
        role: 'assistant',
        content: `接入失败：${caught instanceof Error ? caught.message : '数据源接入助手暂时不可用'}`,
      }]);
    } finally {
      setIsSending(false);
    }
  };

  useEffect(() => {
    if (!initialDraftId || restoredDraftId.current === initialDraftId || role !== 'admin') return;
    restoredDraftId.current = initialDraftId;
    setMessages([welcomeMessage(), {
      id: `source-resume-${initialDraftId}`,
      role: 'assistant',
      content: '已从草稿箱恢复接入配置，将继续当前步骤。',
    }]);
    void send('继续当前数据源接入', initialDraftId, null);
  // 只在外部明确选择一条草稿时恢复，避免输入过程反复触发。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialDraftId, role]);

  const start = () => {
    restoredDraftId.current = null;
    setMessages([welcomeMessage()]);
    setSessionId(null);
    setDraftId(null);
    setCurrentStep('source_url');
    void send('开始新的数据源接入', null, null);
  };

  const reset = () => {
    restoredDraftId.current = null;
    setMessages([welcomeMessage()]);
    setSessionId(null);
    setDraftId(null);
    setCurrentStep('source_url');
    setInput('');
  };

  return (
    <div className="max-w-5xl mx-auto space-y-4">
      <section className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-2xl overflow-hidden shadow-sm">
        <header className="bg-[#ecf4ff] dark:bg-slate-950/40 border-b border-[#c2c6d2] dark:border-slate-800 px-5 py-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-[#004782] text-white flex items-center justify-center">
              <span className="material-symbols-outlined">add_link</span>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="font-extrabold text-lg">数据源接入助手</h1>
                <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-white text-[#004782] border border-[#c2c6d2]">逐项配置</span>
              </div>
              <p className="text-xs text-[#424751] dark:text-slate-400">仅处理接入配置；发布和启用仍由管理员分别确认</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">模型：{agentStatus?.llm_configured ? agentStatus.model : '未配置'}</span>
            <button onClick={() => onRoleChange(role === 'admin' ? 'viewer' : 'admin')}
              className="border border-[#c2c6d2] rounded-lg px-3 py-2 text-xs font-bold text-[#004782] dark:text-blue-300">
              {role === 'admin' ? '退出管理员模式' : '进入管理员模式'}
            </button>
            <button onClick={reset} className="border border-[#c2c6d2] rounded-lg px-3 py-2 text-xs font-bold">新建接入</button>
          </div>
        </header>

        <div className="px-5 py-4 border-b border-[#c2c6d2] dark:border-slate-800 bg-white dark:bg-slate-900">
          <ol className="grid grid-cols-2 sm:grid-cols-5 gap-2" aria-label="数据源接入进度">
            {steps.map((step, index) => {
              const done = completed || (activeStepIndex >= 0 && index < activeStepIndex);
              const active = step.id === currentStep;
              return (
                <li key={step.id} className={`rounded-lg border px-3 py-2 text-xs font-bold flex items-center gap-2 ${
                  active ? 'border-[#004782] bg-[#ecf4ff] text-[#004782]' : done ? 'border-emerald-300 bg-emerald-50 text-emerald-800' : 'border-[#c2c6d2] text-[#727782]'
                }`}>
                  <span className="font-mono">{done ? '✓' : index + 1}</span><span>{step.label}</span>
                </li>
              );
            })}
          </ol>
          {draftId && <p className="mt-3 text-[11px] text-[#727782]">接入草稿 ID：<span className="font-mono font-bold">{draftId}</span> · 每次回答都会自动保存</p>}
        </div>

        {role !== 'admin' && (
          <div className="m-4 rounded-xl border border-amber-200 bg-amber-50 text-amber-900 px-4 py-3 text-sm">
            数据源接入涉及配置写入，仅管理员可以使用。进入管理员模式后才会保存草稿或发送探测请求。
          </div>
        )}

        <div className="h-[46vh] min-h-[360px] overflow-y-auto p-5 space-y-4 bg-[#f7f9ff]/70 dark:bg-slate-950/30">
          {messages.map((message) => (
            <motion.div key={message.id} initial={{opacity: 0, y: 8}} animate={{opacity: 1, y: 0}}
              transition={{duration: 0.18, ease: 'easeOut'}} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[88%] rounded-2xl px-4 py-3 text-sm whitespace-pre-wrap ${
                message.role === 'user'
                  ? 'bg-[#004782] text-white rounded-tr-sm'
                  : 'bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-tl-sm'
              }`}>
                {message.content}
                {message.tools?.map((tool, index) => (
                  <details key={`${message.id}-${tool.name}-${index}`} className="mt-3 border-t border-[#c2c6d2] pt-2 text-xs">
                    <summary className="cursor-pointer font-bold text-[#004782] dark:text-blue-300">
                      {toolLabels[tool.name] ?? tool.name}
                    </summary>
                    <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{JSON.stringify(tool.result, null, 2)}</pre>
                  </details>
                ))}
              </div>
            </motion.div>
          ))}
          {isSending && <div className="text-xs text-[#727782]">正在保存本步骤并执行受控分析…</div>}
        </div>

        <div className="border-t bg-white dark:bg-slate-900 p-4">
          {!draftId ? (
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm text-[#424751] dark:text-slate-300">准备好后开始第 1 步；草稿会出现在数据源管理页的“草稿箱”。</p>
              <button type="button" onClick={start} disabled={role !== 'admin' || isSending}
                className="shrink-0 bg-[#004782] hover:bg-[#185fa5] text-white font-bold rounded-xl px-5 py-3 disabled:opacity-40">
                开始逐项配置
              </button>
            </div>
          ) : completed ? (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
              适配器草稿已生成且保持停用。请回到数据源管理页的草稿箱发布；发布后仍需在那里手动启用。
            </div>
          ) : (
            <form onSubmit={(event) => { event.preventDefault(); void send(input); }} className="space-y-2">
              <label htmlFor="source-onboarding-answer" className="text-xs font-bold text-[#424751] dark:text-slate-300">仅回答当前步骤</label>
              <div className="flex gap-2">
                <input id="source-onboarding-answer" value={input} onChange={(event) => setInput(event.target.value)}
                  disabled={role !== 'admin' || isSending}
                  placeholder="按上方问题填写；不要输入明文凭据"
                  className="flex-1 border border-[#c2c6d2] rounded-xl px-4 py-3 bg-[#f7f9ff] dark:bg-slate-800 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-[#004782]" />
                <button type="submit" disabled={role !== 'admin' || isSending || !input.trim()}
                  className="bg-[#004782] text-white font-bold rounded-xl px-5 py-3 disabled:opacity-40">
                  保存并继续
                </button>
              </div>
              <p className="text-[11px] text-[#727782]">遇到验证码、访问受限或安全防护时，助手会停止联网并保留当前草稿，不会尝试绕过限制。</p>
            </form>
          )}
        </div>
      </section>
    </div>
  );
}

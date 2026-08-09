import {useState} from 'react';
import {motion} from 'motion/react';
import {api, type AgentStatusRead, type ToolCallRead} from '../api';

interface SourceOnboardingAgentViewProps {
  agentStatus: AgentStatusRead | null;
  role: 'viewer' | 'admin';
  onRoleChange: (role: 'viewer' | 'admin') => void;
}

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  tools?: ToolCallRead[];
}

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
  content: '我是数据源接入助手，仅负责官方 HTTPS 数据源（JSON、CSV 或静态 HTML）的探测、字段映射、实时预览和接入配置。发布与正式采集必须由管理员在当前消息中明确确认。',
});

export function SourceOnboardingAgentView({
  agentStatus,
  role,
  onRoleChange,
}: SourceOnboardingAgentViewProps) {
  const [messages, setMessages] = useState<Message[]>(() => [welcomeMessage()]);
  const [input, setInput] = useState('');
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [isSending, setIsSending] = useState(false);

  const reset = () => {
    setMessages([welcomeMessage()]);
    setSessionId(null);
    setInput('');
  };

  const send = async (preset?: string) => {
    const question = (preset ?? input).trim();
    if (!question || isSending || role !== 'admin') return;
    const now = Date.now();
    setMessages((current) => [...current, {id: `source-user-${now}`, role: 'user', content: question}]);
    if (!preset) setInput('');
    setIsSending(true);
    try {
      const response = await api.sourceAgentChat(question, sessionId);
      setSessionId(response.session_id);
      setMessages((current) => [...current, {
        id: `source-assistant-${Date.now()}`,
        role: 'assistant',
        content: response.answer,
        tools: response.tool_calls,
      }]);
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

  return (
    <div className="max-w-5xl mx-auto space-y-4">
      <section className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-2xl overflow-hidden shadow-sm">
        <header className="bg-emerald-50 dark:bg-emerald-950/30 border-b border-emerald-200 dark:border-emerald-900 px-5 py-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-emerald-700 text-white flex items-center justify-center">
              <span className="material-symbols-outlined">add_link</span>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="font-extrabold text-lg">数据源接入助手</h1>
                <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 border border-emerald-200">配置专用</span>
              </div>
              <p className="text-xs text-slate-600 dark:text-slate-400">只处理数据源接入，不查询供应商、风险提醒或规则评分</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">模型：{agentStatus?.llm_configured ? agentStatus.model : '未配置'}</span>
            <button onClick={() => onRoleChange(role === 'admin' ? 'viewer' : 'admin')}
              className="border border-emerald-300 rounded-lg px-3 py-2 text-xs font-bold text-emerald-800 dark:text-emerald-300">
              {role === 'admin' ? '退出管理员模式' : '进入管理员模式'}
            </button>
            <button onClick={reset} className="border rounded-lg px-3 py-2 text-xs font-bold">重置对话</button>
          </div>
        </header>

        {role !== 'admin' && (
          <div className="m-4 rounded-xl border border-amber-200 bg-amber-50 text-amber-900 px-4 py-3 text-sm">
            数据源接入涉及配置写入，仅管理员可以使用。进入管理员模式后才会向服务端发送接入请求。
          </div>
        )}

        <div className="h-[52vh] min-h-[420px] overflow-y-auto p-5 space-y-4 bg-slate-50/60 dark:bg-slate-950/30">
          {messages.map((message) => (
            <motion.div key={message.id} initial={{opacity: 0, y: 8}} animate={{opacity: 1, y: 0}}
              className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[88%] rounded-2xl px-4 py-3 text-sm whitespace-pre-wrap ${
                message.role === 'user'
                  ? 'bg-emerald-700 text-white rounded-tr-sm'
                  : 'bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-tl-sm'
              }`}>
                {message.content}
                {message.tools?.map((tool, index) => (
                  <details key={`${message.id}-${tool.name}-${index}`} className="mt-3 border-t pt-2 text-xs">
                    <summary className="cursor-pointer font-bold text-emerald-700 dark:text-emerald-300">
                      {toolLabels[tool.name] ?? tool.name}
                    </summary>
                    <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{JSON.stringify(tool.result, null, 2)}</pre>
                  </details>
                ))}
              </div>
            </motion.div>
          ))}
          {isSending && <div className="text-xs text-slate-500">数据源接入助手正在分析并调用受控工具…</div>}
        </div>

        <div className="border-t bg-white dark:bg-slate-900 p-4 space-y-3">
          <div className="flex flex-wrap gap-2">
            {[
              '列出接入一个官方 HTTPS JSON、CSV 或静态 HTML 数据源所需的信息。',
              '根据我提供的官方 URL 和采集目标探测页面，先实时预览并创建停用草稿，不要发布。',
              '说明当前草稿的字段映射、认证方式和安全限制。',
            ].map((preset) => (
              <button key={preset} disabled={role !== 'admin' || isSending} onClick={() => void send(preset)}
                className="border rounded-full px-3 py-1.5 text-xs disabled:opacity-40 hover:border-emerald-500">
                {preset}
              </button>
            ))}
          </div>
          <form onSubmit={(event) => { event.preventDefault(); void send(); }} className="flex gap-2">
            <input value={input} onChange={(event) => setInput(event.target.value)}
              disabled={role !== 'admin' || isSending}
              placeholder="提供官方 URL、采集目标和授权说明…"
              className="flex-1 border rounded-xl px-4 py-3 bg-slate-50 dark:bg-slate-800 disabled:opacity-50" />
            <button type="submit" disabled={role !== 'admin' || isSending || !input.trim()}
              className="bg-emerald-700 text-white font-bold rounded-xl px-5 py-3 disabled:opacity-40">
              发送
            </button>
          </form>
          <p className="text-[11px] text-slate-500">发布需明确输入“确认发布”；正式采集需明确输入“立即采集”。发布后仍需管理员在数据源页面手动启用。</p>
        </div>
      </section>
    </div>
  );
}

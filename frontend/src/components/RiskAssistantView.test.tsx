import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest';
import {api, type ChatResponse} from '../api';
import {RiskAssistantView} from './RiskAssistantView';

// 仅把 chat 网络方法替换为可控 mock，保留真实 ApiError 与其他 api 成员。
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {...actual.api, chat: vi.fn()},
  };
});

interface RenderOptions {
  readonly pendingQuery?: string | null;
}

const renderAssistant = (options: RenderOptions = {}) => {
  const onClearPendingQuery = vi.fn();
  const onSelectRisk = vi.fn();
  const onSelectSupplier = vi.fn();
  const result = render(
    <RiskAssistantView
      riskItems={[]}
      suppliers={[]}
      agentStatus={null}
      onSelectRisk={onSelectRisk}
      onSelectSupplier={onSelectSupplier}
      pendingQuery={options.pendingQuery}
      onClearPendingQuery={onClearPendingQuery}
    />,
  );
  return {onClearPendingQuery, onSelectRisk, onSelectSupplier, ...result};
};

const queryInput = () => screen.getByPlaceholderText('请输入自然语言对话查询') as HTMLInputElement;

const PRESET_QUERY = '查询当前启用的所有 P1 严重风险提醒';

// jsdom 未实现 Element#scrollIntoView；该组件挂载与消息更新时滚动到底部会调用它。
beforeAll(() => {
  Element.prototype.scrollIntoView ??= vi.fn();
});

beforeEach(() => {
  // 本组测试只观察「是否发送」与「发送了什么」，用挂起 Promise 避免响应渲染干扰计数。
  vi.mocked(api.chat).mockReturnValue(new Promise<ChatResponse>(() => {}));
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe('RiskAssistantView pendingQuery 预填与消费', () => {
  it('传入非空 pendingQuery 时输入框显示完整查询，而非保持空值', () => {
    renderAssistant({pendingQuery: PRESET_QUERY});

    expect(queryInput().value).toBe(PRESET_QUERY);
  });

  it('挂载时只预填，不自动调用 api.chat，发送必须由用户触发', () => {
    renderAssistant({pendingQuery: PRESET_QUERY});

    expect(api.chat).not.toHaveBeenCalled();
  });

  it('用户手动点击发送后，api.chat 恰好以该完整查询调用一次', async () => {
    const user = userEvent.setup();
    renderAssistant({pendingQuery: PRESET_QUERY});

    // 挂载阶段不得自动发送，否则本条会在点击前失败，而不是被挂载调用蒙混过关。
    expect(api.chat).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', {name: /发送/}));

    expect(api.chat).toHaveBeenCalledTimes(1);
    expect(api.chat).toHaveBeenCalledWith(PRESET_QUERY, null);
  });

  it('onClearPendingQuery 恰好消费一次，避免重复清理或重复发送', () => {
    const {onClearPendingQuery} = renderAssistant({pendingQuery: PRESET_QUERY});

    expect(onClearPendingQuery).toHaveBeenCalledTimes(1);
  });

  it('pendingQuery 为 null 或纯空白时不预填、不发送、不消费', () => {
    const nullCase = renderAssistant({pendingQuery: null});
    expect(queryInput().value).toBe('');
    expect(api.chat).not.toHaveBeenCalled();
    expect(nullCase.onClearPendingQuery).not.toHaveBeenCalled();
    nullCase.unmount();

    const blankCase = renderAssistant({pendingQuery: '   '});
    expect(queryInput().value).toBe('');
    expect(api.chat).not.toHaveBeenCalled();
    expect(blankCase.onClearPendingQuery).not.toHaveBeenCalled();
  });
});

// 缺陷回归：清单内供应商的 verify_company 只读库内已采集核查记录（source=database），
// 工商字段仅存在于 content 文本（企业：X；统一社会信用代码：Y；登记状态：Z）。
// 旧实现不解析 content、且候选占位「未披露」会掩盖文本回退，导致卡片显示「未披露」，
// 并把库内数据错误标注为「天眼查 MCP 实时核查」。
describe('RiskAssistantView 库内已采集核查结果映射', () => {
  const databaseCheckResponse: ChatResponse = {
    session_id: 11,
    answer: '已读取该供应商库内最新的天眼查核查记录。',
    tool_calls: [
      {
        name: 'verify_company',
        arguments: {company_name: '宁波鸿腾精密制造股份有限公司'},
        result: {
          status: 'success',
          source: 'database',
          company_name: '宁波鸿腾精密制造股份有限公司',
          content: '企业：宁波鸿腾精密制造股份有限公司；统一社会信用代码：91330200MA2XXXXXXX；登记状态：存续',
        },
      },
    ],
  };

  it('结构化字段缺失时用 content 文本填充信用代码与登记状态，来源标注为库内已采集核查', async () => {
    vi.mocked(api.chat).mockResolvedValue(databaseCheckResponse);
    renderAssistant();

    const user = userEvent.setup();
    await user.type(queryInput(), '核查宁波鸿腾精密制造股份有限公司');
    await user.click(screen.getByRole('button', {name: /发送/}));

    // content 解析出的信用代码与登记状态必须真正渲染，而不是占位「未披露」。
    expect(await screen.findByText('91330200MA2XXXXXXX')).toBeInTheDocument();
    expect(screen.getByText('存续')).toBeInTheDocument();
    expect(screen.queryAllByText('未披露')).toHaveLength(0);
    // 来源必须按真实出处标注：库内已采集核查，而不是实时 MCP。
    expect(screen.getByText('库内已采集核查')).toBeInTheDocument();
    expect(screen.queryAllByText('天眼查 MCP 实时核查')).toHaveLength(0);
  });
});

import {cleanup, render, screen, within} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import {SystemSplashScreen, type SelfCheckItem} from './SystemSplashScreen';

afterEach(cleanup);

const allStates: SelfCheckItem[] = [
  {id: 'database', label: '数据库连接', detail: 'PostgreSQL 连接正常', state: 'ok'},
  {id: 'scheduler', label: '调度器心跳', detail: '心跳正常（30 秒前）', state: 'warn'},
  {id: 'sources', label: '信息源状态', detail: '1/2 信息源正常', state: 'error'},
  {id: 'ai', label: 'AI 引擎', detail: '未配置真实模型', state: 'unavailable'},
];

describe('SystemSplashScreen full 变体', () => {
  it('根元素具备 role=status 与可访问名称，并逐项展示 label/detail 与状态语义', () => {
    render(<SystemSplashScreen variant="full" items={allStates} />);
    const splash = screen.getByRole('status', {name: '正在初始化供应商风险监控平台'});

    expect(within(splash).getByText('数据库连接')).toBeInTheDocument();
    expect(within(splash).getByText('PostgreSQL 连接正常')).toBeInTheDocument();
    expect(within(splash).getByText('调度器心跳')).toBeInTheDocument();
    expect(within(splash).getByText('心跳正常（30 秒前）')).toBeInTheDocument();
    expect(within(splash).getByText('信息源状态')).toBeInTheDocument();
    expect(within(splash).getByText('1/2 信息源正常')).toBeInTheDocument();
    expect(within(splash).getByText('AI 引擎')).toBeInTheDocument();
    expect(within(splash).getByText('未配置真实模型')).toBeInTheDocument();

    // 状态图标语义：ok=正常、warn=告警、error=错误、unavailable=不可用（pending 在下一用例验证）
    expect(within(splash).getByRole('img', {name: '正常'})).toBeInTheDocument();
    expect(within(splash).getByRole('img', {name: '告警'})).toBeInTheDocument();
    expect(within(splash).getByRole('img', {name: '错误'})).toBeInTheDocument();
    expect(within(splash).getByRole('img', {name: '不可用'})).toBeInTheDocument();
  });

  it('进度随已结束项增长：pending 视为未结束，全部结束后为 100%', () => {
    const two: SelfCheckItem[] = [
      {id: 'database', label: '数据库连接', detail: '正在检查…', state: 'pending'},
      {id: 'ai', label: 'AI 引擎', detail: '正在检查…', state: 'pending'},
    ];
    const {container, rerender} = render(<SystemSplashScreen variant="full" items={two} />);

    expect(screen.getByRole('progressbar', {name: '自检进度'})).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getAllByRole('img', {name: '检查中'})).toHaveLength(2);
    // pending 状态图标保留（语义标签「检查中」仍在），但不再自动旋转。
    expect(container.querySelectorAll('.animate-spin')).toHaveLength(0);

    rerender(<SystemSplashScreen variant="full" items={[
      {...two[0], state: 'ok', detail: 'PostgreSQL 连接正常'},
      two[1],
    ]} />);
    expect(screen.getByRole('progressbar', {name: '自检进度'})).toHaveAttribute('aria-valuenow', '50');

    rerender(<SystemSplashScreen variant="full" items={[
      {...two[0], state: 'ok', detail: 'PostgreSQL 连接正常'},
      {...two[1], state: 'unavailable', detail: '检查超时'},
    ]} />);
    expect(screen.getByRole('progressbar', {name: '自检进度'})).toHaveAttribute('aria-valuenow', '100');
    expect(screen.getByTestId('self-check-percent')).toHaveTextContent('100%');
  });

  it('不再出现旧的写死在线文案', () => {
    render(<SystemSplashScreen variant="full" items={allStates} />);
    for (const legacy of ['Gemini AI 在线', '天眼查 API 直连', '加密通信中', '节点在线']) {
      expect(screen.queryByText(legacy)).not.toBeInTheDocument();
    }
  });
});

describe('SystemSplashScreen simple 变体', () => {
  it('只展示品牌标识与标题，不再渲染加载指示行、无自检列表与自检项文案', () => {
    render(<SystemSplashScreen variant="simple" />);
    const splash = screen.getByRole('status', {name: '正在初始化供应商风险监控平台'});

    // logo 与标题保留
    expect(within(splash).getByAltText('SR Monitoring')).toBeInTheDocument();
    expect(within(splash).getByRole('heading', {name: '供应商风险智能监控平台'})).toBeInTheDocument();

    // 刷新时"一闪而逝"的旋转加载行已移除：加载图标、加载指示与文案均不得再渲染。
    expect(within(splash).queryByTestId('splash-loading-indicator')).not.toBeInTheDocument();
    expect(within(splash).queryByRole('img', {name: '加载中'})).not.toBeInTheDocument();
    expect(within(splash).queryByText('正在加载…')).not.toBeInTheDocument();
    expect(within(splash).queryByRole('progressbar')).not.toBeInTheDocument();
    expect(within(splash).queryByText('系统状态自检')).not.toBeInTheDocument();
    expect(within(splash).queryByText('数据库连接')).not.toBeInTheDocument();
  });
});

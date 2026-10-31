import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {
  EventValidityGantt,
  buildGanttLayout,
  formatGanttRange,
  type GanttMember,
} from './EventValidityGantt';

const HOUR = 3_600_000;
const T0 = Date.parse('2026-09-01T00:00:00Z');

const member = (overrides: Partial<GanttMember> = {}): GanttMember => ({
  alertId: 1,
  eventId: 101,
  summary: '港口罢工导致交付延迟',
  sourceTitle: '路透社',
  level: 'P2',
  startAt: new Date(T0).toISOString(),
  expiresAt: new Date(T0 + 24 * HOUR).toISOString(),
  status: 'current',
  ...overrides,
});

const now = new Date(T0 + 24 * HOUR);

afterEach(cleanup);

describe('buildGanttLayout 轴范围与条形定位', () => {
  it('轴取 min(开始) ~ max(截止)，两端各留 5% 余量', () => {
    const members = [
      member({alertId: 1, eventId: 101, expiresAt: new Date(T0 + 24 * HOUR).toISOString()}),
      member({
        alertId: 2,
        eventId: 102,
        startAt: new Date(T0 + 48 * HOUR).toISOString(),
        expiresAt: new Date(T0 + 74 * HOUR).toISOString(),
      }),
    ];

    const layout = buildGanttLayout(members, now, 'full');
    const span = 74 * HOUR;
    const padding = span * 0.05;

    expect(layout.axisStart?.getTime()).toBeCloseTo(T0 - padding, 0);
    expect(layout.axisEnd?.getTime()).toBeCloseTo(T0 + span + padding, 0);

    // 留白占轴两端各 5/110，数据边界稳定落在约 4.5% / 95.5%
    expect(layout.rows[0].leftPercent).toBeCloseTo(4.545, 2);
    expect(layout.rows[1].leftPercent + layout.rows[1].widthPercent).toBeCloseTo(95.455, 2);
    expect(layout.rows.every((row) => row.hasRange)).toBe(true);
  });

  it('短跨度至少各留 1 小时，单点事件也有可见宽度', () => {
    const members = [
      member({expiresAt: new Date(T0 + 6 * HOUR).toISOString()}),
      member({alertId: 3, eventId: 103, startAt: new Date(T0 + 3 * HOUR).toISOString(), expiresAt: new Date(T0 + 3 * HOUR).toISOString()}),
    ];

    const layout = buildGanttLayout(members, now, 'mini');

    expect(layout.axisStart?.getTime()).toBe(T0 - HOUR);
    expect(layout.axisEnd?.getTime()).toBe(T0 + 7 * HOUR);
    // 1 小时余量占 8 小时轴（6 小时数据 + 两端各 1 小时）的 1/8
    expect(layout.rows[0].leftPercent).toBeCloseTo(12.5, 2);
    // 开始=截止的单点事件仍保留最小宽度，可被点中
    expect(layout.rows[1].widthPercent).toBeGreaterThan(0);
  });

  it('完全没有时间字段时返回空轴且 hasRange=false', () => {
    const layout = buildGanttLayout([
      member({startAt: null, expiresAt: null}),
      member({alertId: 2, eventId: 102, startAt: null, expiresAt: null}),
    ], now, 'full');

    expect(layout.axisStart).toBeNull();
    expect(layout.axisEnd).toBeNull();
    expect(layout.rows.map((row) => row.hasRange)).toEqual([false, false]);

    render(<EventValidityGantt members={layout.rows.map((row) => row.member)} variant="full" now={now} />);
    expect(screen.getByText('暂无可展示的有效期')).toBeInTheDocument();
    expect(screen.queryAllByTestId('gantt-bar')).toHaveLength(0);
  });

  it('缺少截止时间的事件画到轴末端，缺少开始时间的事件从轴起点画起', () => {
    const layout = buildGanttLayout([
      member({startAt: new Date(T0 + 10 * HOUR).toISOString(), expiresAt: null}),
      member({alertId: 2, eventId: 102, startAt: null, expiresAt: new Date(T0 + 4 * HOUR).toISOString()}),
    ], now, 'full');

    const [openEnded, openStart] = layout.rows;
    expect(openEnded.hasRange).toBe(true);
    expect(openEnded.leftPercent + openEnded.widthPercent).toBeCloseTo(100, 1);
    expect(openStart.leftPercent).toBeCloseTo(0, 1);
    expect(openStart.widthPercent).toBeGreaterThan(0);
  });
});

describe('今天线', () => {
  const members = [member({expiresAt: new Date(T0 + 24 * HOUR).toISOString()})];

  it('now 在轴内时画红色虚线并给出今天点', () => {
    render(<EventValidityGantt members={members} variant="mini" now={new Date(T0 + 12 * HOUR)} />);

    expect(screen.getByTestId('gantt-today-line')).toBeInTheDocument();
    expect(screen.getByTestId('gantt-today-dot')).toBeInTheDocument();
    expect(screen.queryByText('今天在范围外')).not.toBeInTheDocument();
  });

  it('now 在轴外时退化为边缘提示，不画线', () => {
    render(<EventValidityGantt members={members} variant="mini" now={new Date(T0 + 240 * HOUR)} />);

    expect(screen.getByText('今天在范围外')).toBeInTheDocument();
    expect(screen.queryByTestId('gantt-today-line')).not.toBeInTheDocument();
    expect(screen.queryByTestId('gantt-today-dot')).not.toBeInTheDocument();
  });

  it('full 模式同样只在轴内画今天线', () => {
    const {rerender} = render(<EventValidityGantt members={members} variant="full" now={new Date(T0 + 12 * HOUR)} />);
    expect(screen.getByTestId('gantt-today-line')).toBeInTheDocument();
    expect(screen.queryByText('今天在范围外')).not.toBeInTheDocument();

    rerender(<EventValidityGantt members={members} variant="full" now={new Date(T0 - 48 * HOUR)} />);
    expect(screen.getByText('今天在范围外')).toBeInTheDocument();
    expect(screen.queryByTestId('gantt-today-line')).not.toBeInTheDocument();
  });
});

describe('mini / full 渲染与交互', () => {
  const members = [
    member({alertId: 1, eventId: 101, summary: '港口罢工导致交付延迟', sourceTitle: '路透社'}),
    member({
      alertId: 2,
      eventId: 102,
      summary: '仓库积水停运',
      sourceTitle: '地方应急办',
      startAt: new Date(T0 + 30 * HOUR).toISOString(),
      expiresAt: new Date(T0 + 40 * HOUR).toISOString(),
      status: 'expired',
    }),
    member({
      alertId: 3,
      eventId: 103,
      summary: '旧版通报被替代',
      sourceTitle: '气象台',
      startAt: new Date(T0 + 42 * HOUR).toISOString(),
      expiresAt: new Date(T0 + 50 * HOUR).toISOString(),
      status: 'expired',
      superseded: true,
    }),
  ];

  it('mini 每个有效成员一条 4px 细条，不渲染左栏文字标签', () => {
    render(<EventValidityGantt members={members} variant="mini" now={now} />);

    expect(screen.getAllByTestId('gantt-bar')).toHaveLength(3);
    expect(screen.queryByText('路透社')).not.toBeInTheDocument();
    expect(screen.getByRole('group', {name: '事件有效期概览，共 3 个事件'})).toBeInTheDocument();
  });

  it('full 展示左栏信源、24 字截断摘要、刻度与图例', () => {
    const longSummary = '港口罢工导致交付延迟并波及多条航线与仓储周转效率下降';
    const truncated = `${Array.from(longSummary).slice(0, 24).join('')}…`;
    render(
      <EventValidityGantt
        members={[{...members[0], summary: longSummary}]}
        variant="full"
        now={now}
      />,
    );

    expect(screen.getByRole('group', {name: '事件有效期甘特图'})).toBeInTheDocument();
    expect(screen.getByText('路透社')).toBeInTheDocument();
    const label = screen.getByText(truncated);
    expect(label).toHaveAttribute('title', longSummary);
    expect(screen.getByText('已被替代')).toBeInTheDocument();
    expect(screen.getByText('今天')).toBeInTheDocument();
    expect(screen.getAllByTestId('gantt-bar')).toHaveLength(1);
  });

  it('aria-label 同时包含摘要、信源、起止与状态，点击回调带上事件编号', async () => {
    const user = userEvent.setup();
    const onSelectEvent = vi.fn();
    render(<EventValidityGantt members={members} variant="full" now={now} onSelectEvent={onSelectEvent} />);

    const bars = screen.getAllByRole('button');
    expect(bars).toHaveLength(3);
    const label = bars[1].getAttribute('aria-label') ?? '';
    expect(label).toContain('仓库积水停运');
    expect(label).toContain('地方应急办');
    expect(label).toContain('状态：已过期');
    expect(label).toMatch(/有效期：\d{1,2}\/\d{1,2} \d{2}:\d{2} 至 \d{1,2}\/\d{1,2} \d{2}:\d{2}/);

    await user.click(bars[1]);
    expect(onSelectEvent).toHaveBeenCalledWith(102);
  });

  it('时间条可用键盘聚焦，并在悬停/聚焦时展示事件详情', async () => {
    const user = userEvent.setup();
    render(<EventValidityGantt members={members} variant="full" now={now} onSelectEvent={vi.fn()} />);

    expect(screen.queryByTestId('gantt-detail')).not.toBeInTheDocument();

    await user.tab();
    const firstBar = screen.getAllByRole('button')[0];
    expect(firstBar).toHaveFocus();
    const detail = screen.getByTestId('gantt-detail');
    expect(detail).toHaveTextContent('港口罢工导致交付延迟');
    expect(detail).toHaveTextContent('路透社');
    expect(detail).toHaveTextContent('有效');

    await user.tab();
    expect(firstBar).not.toHaveFocus();
    expect(screen.getByTestId('gantt-detail')).toHaveTextContent('仓库积水停运');
  });
});

describe('formatGanttRange', () => {
  it('两端齐全用 ~ 连接，只有一端时降级为自/至，两端都没有返回 null', () => {
    const start = new Date(T0);
    const end = new Date(T0 + 26 * HOUR);

    const both = formatGanttRange(start, end) ?? '';
    expect(both).toContain('~');
    expect(formatGanttRange(start, null)).toMatch(/^自 /);
    expect(formatGanttRange(null, end)).toMatch(/^至 /);
    expect(formatGanttRange(null, null)).toBeNull();
  });
});

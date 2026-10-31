import {cleanup, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {RiskAlertSummary} from '../api';
import type {RiskItem, RiskItemMergedEvent} from '../types';
import {CurrentRisksView} from './CurrentRisksView';

const baseItem = (overrides: Partial<RiskItem> = {}): RiskItem => ({
  id: '1',
  companyName: '示例供应商',
  level: 'P2',
  levelName: '高风险',
  riskType: '物流',
  eventType: 'logistics',
  summary: '供应链运输中断风险',
  aiConfidence: 99.8,
  updatedTime: '2026-09-01 09:00',
  source: '官方风险源',
  status: 'valid',
  validityState: 'active',
  validUntil: '2026-09-30T08:00:00Z',
  reviewDueAt: '2026-09-15T08:00:00Z',
  validityReason: {code: 'effective_signal_support', anchor_source: 'published_at', details: {}},
  validityPolicyVersion: 'v1',
  ...overrides,
});

const summaryFixture = (counts: Readonly<Record<'P1' | 'P2' | 'P3' | 'P4', number>>): RiskAlertSummary => ({
  level_counts: [
    {level: 'P1', count: counts.P1},
    {level: 'P2', count: counts.P2},
    {level: 'P3', count: counts.P3},
    {level: 'P4', count: counts.P4},
  ],
  total_current: counts.P1 + counts.P2 + counts.P3 + counts.P4,
});

/**
 * 组内事件的映射结构（RiskItemMergedEvent）：测试按真实字段构造，
 * 兼容层字段缺失时的降级另有用例覆盖。
 */
const groupedItem = (overrides: Partial<RiskItem>, merged: RiskItemMergedEvent[]): RiskItem => ({
  ...baseItem(overrides),
  mergedEvents: merged,
});

const mergedEvent = (overrides: Partial<RiskItemMergedEvent> = {}): RiskItemMergedEvent => ({
  alertId: 2,
  eventId: 102,
  summary: '同组事件：仓库积水停运',
  sourceTitle: '地方应急办',
  level: 'P3',
  startAt: '2026-09-02T00:00:00Z',
  expiresAt: '2026-09-03T00:00:00Z',
  status: 'expired',
  superseded: false,
  ...overrides,
});

/** 代表事件 + 一个已过期成员，构成「部分失效」组。 */
const partialGroup = (): RiskItem => groupedItem({id: '1', companyName: '组内事件供应商'}, [
  mergedEvent({
    alertId: 1,
    eventId: 101,
    summary: '代表事件：港口罢工',
    sourceTitle: '信源 A',
    level: 'P2',
    startAt: '2026-09-01T00:00:00Z',
    expiresAt: '2026-09-05T00:00:00Z',
    status: 'current',
  }),
  mergedEvent(),
]);

afterEach(cleanup);

describe('当前风险列表有效期状态展示', () => {
  it('以文本展示 active/expired/revoked/superseded/conflicted/legacy/irrelevant 状态而不只靠颜色', async () => {
    const user = userEvent.setup();
    const items: RiskItem[] = [
      baseItem({id: '1', validityState: 'active'}),
      baseItem({id: '2', companyName: '过期供应商', validityState: 'expired', status: 'invalid'}),
      baseItem({id: '3', companyName: '撤销供应商', validityState: 'revoked', status: 'invalid'}),
      baseItem({id: '4', companyName: '替代供应商', validityState: 'superseded', status: 'invalid'}),
      baseItem({id: '5', companyName: '冲突供应商', validityState: 'conflicted', status: 'invalid'}),
      baseItem({id: '6', companyName: '旧版供应商', validityState: 'legacy', status: 'invalid'}),
      baseItem({id: '7', companyName: '已过滤供应商', validityState: 'irrelevant', status: 'invalid'}),
    ];

    render(<CurrentRisksView riskItems={items} summary={null} onSelectRisk={vi.fn()} />);

    expect(screen.getByText('有效')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: '已失效'}));
    expect(screen.getByText('已过期')).toBeInTheDocument();
    expect(screen.getByText('已撤销')).toBeInTheDocument();
    expect(screen.getByText('已替代')).toBeInTheDocument();
    expect(screen.getByText('冲突')).toBeInTheDocument();
    expect(screen.getByText('旧版兼容')).toBeInTheDocument();
    // 历史兼容：旧 irrelevant 终态按「已过滤」展示。
    expect(screen.getByText('已过滤')).toBeInTheDocument();
  });

  it('并排展示处理状态，且生命周期为 null 时不渲染空徽标', () => {
    render(
      <CurrentRisksView
        riskItems={[baseItem({
          id: '8',
          companyName: '处理状态供应商',
          validityState: null,
          processingState: 'no_trigger',
        })]}
        summary={null}
        onSelectRisk={vi.fn()}
      />,
    );

    expect(screen.getByText('未触发（数据积累中）')).toBeInTheDocument();
    expect(screen.queryByText('有效')).not.toBeInTheDocument();
    expect(screen.queryByText('其他状态')).not.toBeInTheDocument();
    expect(screen.queryByText('undefined')).not.toBeInTheDocument();
  });

  it('展示截止时间与复核时间；无异常的「存在有效信号支撑」不再展示，且不暴露策略版本', () => {
    render(
      <CurrentRisksView
        riskItems={[baseItem({validityReason: {code: 'effective_signal_support', anchor_source: 'published_at', details: {}}})]}
        summary={null}
        onSelectRisk={vi.fn()}
      />,
    );

    expect(screen.getByText('截止')).toBeInTheDocument();
    expect(screen.getByText('复核')).toBeInTheDocument();
    expect(screen.queryByText('原因')).not.toBeInTheDocument();
    expect(screen.queryByText('存在有效信号支撑')).not.toBeInTheDocument();
    expect(screen.queryByText('effective_signal_support')).not.toBeInTheDocument();
    expect(screen.queryByText('策略版本')).not.toBeInTheDocument();
    expect(screen.queryByText('v1')).not.toBeInTheDocument();
  });

  it('未知原因码回落到中文说明，不裸露英文码', () => {
    render(
      <CurrentRisksView
        riskItems={[baseItem({
          id: '2',
          companyName: '未知原因供应商',
          validityReason: {code: 'brand_new_reason', anchor_source: 'published_at', details: {}},
        })]}
        summary={null}
        onSelectRisk={vi.fn()}
      />,
    );

    expect(screen.getByText('其他原因')).toBeInTheDocument();
    expect(screen.queryByText('brand_new_reason')).not.toBeInTheDocument();
  });

  it('未知有效期状态回落到中文说明，不裸露英文码', () => {
    render(
      <CurrentRisksView
        riskItems={[baseItem({
          id: '3',
          companyName: '未知状态供应商',
          validityState: 'brand_new_state',
          validityReason: {code: 'filtered_out', anchor_source: 'published_at', details: {}},
        })]}
        summary={null}
        onSelectRisk={vi.fn()}
      />,
    );

    expect(screen.getByText('其他状态')).toBeInTheDocument();
    expect(screen.queryByText('brand_new_state')).not.toBeInTheDocument();
    // 新增的 filtered_out 原因码同样必须有中文，不回落到英文原码
    expect(screen.getByText('已过滤')).toBeInTheDocument();
    expect(screen.queryByText('filtered_out')).not.toBeInTheDocument();
  });

  it('传入 expiredItems 时，已失效口径使用独立数据源', async () => {
    const user = userEvent.setup();
    render(
      <CurrentRisksView
        riskItems={[baseItem({id: '1', companyName: '当前供应商'})]}
        summary={null}
        expiredItems={[baseItem({id: '2', companyName: '历史供应商', status: 'invalid'})]}
        expiredTotal={1}
        onSelectRisk={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', {name: '已失效'}));
    expect(await screen.findByText('历史供应商')).toBeInTheDocument();
    expect(screen.queryByText('当前供应商')).not.toBeInTheDocument();
    expect(screen.getByText('本页显示 1 条（已加载 1 条），全库已失效共 1 条')).toBeInTheDocument();
  });

  it('当前/历史可切换：默认只显示当前有效，切换到已失效显示历史', async () => {
    const user = userEvent.setup();
    const items: RiskItem[] = [
      baseItem({id: '1', validityState: 'active'}),
      baseItem({id: '2', companyName: '过期供应商', validityState: 'expired', status: 'invalid'}),
    ];

    render(<CurrentRisksView riskItems={items} summary={null} onSelectRisk={vi.fn()} />);

    expect(screen.getByText('示例供应商')).toBeInTheDocument();
    expect(screen.queryByText('过期供应商')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: '已失效'}));
    expect(screen.getByText('过期供应商')).toBeInTheDocument();
    expect(screen.queryByText('示例供应商')).not.toBeInTheDocument();
  });
});

describe('风险类型筛选', () => {
  it('按事件类型筛选，且下拉不再罗列每条独立的信源', async () => {
    const user = userEvent.setup();
    const items: RiskItem[] = [
      baseItem({id: '1', companyName: '物流供应商', eventType: 'logistics', riskType: '物流', source: '信源 A'}),
      baseItem({id: '2', companyName: '司法供应商', eventType: 'judicial', riskType: '司法', source: '信源 B'}),
    ];

    render(<CurrentRisksView riskItems={items} summary={null} onSelectRisk={vi.fn()} />);

    const typeSelect = screen.getByRole('combobox', {name: '风险类型'});
    expect(screen.queryByRole('combobox', {name: '信源'})).not.toBeInTheDocument();
    expect(within(typeSelect).getByRole('option', {name: '物流（1）'})).toBeInTheDocument();
    expect(within(typeSelect).getByRole('option', {name: '司法（1）'})).toBeInTheDocument();

    await user.selectOptions(typeSelect, 'judicial');

    expect(screen.getByText('司法供应商')).toBeInTheDocument();
    expect(screen.queryByText('物流供应商')).not.toBeInTheDocument();
  });

  it('只有一种风险类型时不渲染类型下拉，避免无意义的单选项', () => {
    render(
      <CurrentRisksView
        riskItems={[baseItem({id: '1'}), baseItem({id: '2', companyName: '另一供应商'})]}
        summary={null}
        onSelectRisk={vi.fn()}
      />,
    );

    expect(screen.queryByRole('combobox', {name: '风险类型'})).not.toBeInTheDocument();
  });
});

describe('当前风险概览：全库计数与单页列表口径分离', () => {
  it('概览卡与总计使用全库汇总，列表文案明确标注本页/已加载口径', () => {
    // Given 已加载 2 行（其中 1 行 P4），服务端全库汇总为 94 条
    const items: RiskItem[] = [
      baseItem({id: '1', level: 'P2'}),
      baseItem({id: '2', level: 'P4', companyName: '低风险供应商'}),
    ];
    const summary = summaryFixture({P1: 0, P2: 1, P3: 0, P4: 93});

    // When
    render(<CurrentRisksView riskItems={items} summary={summary} onSelectRisk={vi.fn()} />);

    // Then 概览卡取全库口径：总计 94，P1-P4 分别为 0/1/0/93，而非本页的 0/1/0/1
    const overviewCard = screen.getByText('当前风险概览').closest('section');
    if (overviewCard === null) throw new Error('当前风险概览卡片未渲染');
    expect(within(overviewCard).getByText('共 94 条（全库当前）')).toBeInTheDocument();
    const p4Cell = within(overviewCard).getByText('P4 轻微').parentElement;
    if (p4Cell === null) throw new Error('P4 概览单元未渲染');
    expect(within(p4Cell).getByText('93')).toBeInTheDocument();
    // 页脚同时给出两个口径，不再用单页条数冒充全库总量
    expect(screen.getByText('本页显示 2 条（已加载 2 条），全库当前共 94 条')).toBeInTheDocument();
  });

  it('等级筛选下概览卡仍为全库口径，页脚反映筛选后的本页条数', async () => {
    const user = userEvent.setup();
    const items: RiskItem[] = [
      baseItem({id: '1', level: 'P2'}),
      baseItem({id: '2', level: 'P4', companyName: '低风险供应商'}),
    ];
    const summary = summaryFixture({P1: 3, P2: 7, P3: 11, P4: 93});

    render(<CurrentRisksView riskItems={items} summary={summary} onSelectRisk={vi.fn()} />);

    // When 按等级筛选只保留本页 P4 行
    await user.selectOptions(screen.getByRole('combobox'), 'P4');

    // Then 本页只剩 1 条，但概览卡总数仍是全库的 114
    expect(screen.getByText('本页显示 1 条（已加载 2 条），全库当前共 114 条')).toBeInTheDocument();
    expect(screen.getByText('共 114 条（全库当前）')).toBeInTheDocument();
  });

  it('汇总不可用时回落本页计数，并在文案中声明全库计数暂不可用', () => {
    const items: RiskItem[] = [
      baseItem({id: '1', level: 'P2'}),
      baseItem({id: '2', level: 'P4', companyName: '低风险供应商'}),
    ];

    render(<CurrentRisksView riskItems={items} summary={null} onSelectRisk={vi.fn()} />);

    expect(screen.getByText('共 2 条（本页已加载）')).toBeInTheDocument();
    expect(screen.getByText('本页显示 2 条，已加载 2 条（全库当前计数暂不可用）')).toBeInTheDocument();
  });
});

describe('组内事件有效期（按供应商视图）', () => {
  it('一级摘要给出事件数、信源数与有效期范围，并标注部分失效', () => {
    render(<CurrentRisksView riskItems={[partialGroup()]} summary={null} onSelectRisk={vi.fn()} />);

    expect(screen.getByText('2 个事件')).toBeInTheDocument();
    expect(screen.getByText('2 个信源')).toBeInTheDocument();
    // 组内最早开始 9/1 至最晚截止 9/5；断言区间分隔存在，避免把时区写死
    expect(screen.getByText(/ ~ /)).toBeInTheDocument();
    expect(screen.getByText('部分失效')).toBeInTheDocument();
    // 取消 mini 甘特：默认不展示时间条，需显式展开
    expect(screen.queryAllByTestId('gantt-bar')).toHaveLength(0);
  });

  it('点击「展开事件有效期」展示 full 甘特图，含图例与事件标签', async () => {
    const user = userEvent.setup();
    render(<CurrentRisksView riskItems={[partialGroup()]} summary={null} onSelectRisk={vi.fn()} />);

    const toggle = screen.getByRole('button', {name: '展开事件有效期'});
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('group', {name: '事件有效期甘特图'})).not.toBeInTheDocument();

    await user.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const chart = screen.getByRole('group', {name: '事件有效期甘特图'});
    expect(within(chart).getByText('已被替代')).toBeInTheDocument();
    expect(within(chart).getByText('今天')).toBeInTheDocument();
    expect(within(chart).getByText('信源 A')).toBeInTheDocument();
    expect(within(chart).getByText('地方应急办')).toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: '收起事件有效期'}));
    expect(screen.queryByRole('group', {name: '事件有效期甘特图'})).not.toBeInTheDocument();
  });

  it('点击时间条优先打开组内具体事件，该提醒不在当前页时回退到代表事件', async () => {
    const user = userEvent.setup();
    const onSelectRisk = vi.fn();
    const group = partialGroup();
    const sibling = baseItem({
      id: '2',
      companyName: '同组另一供应商',
      status: 'invalid',
      validityState: 'expired',
    });
    render(<CurrentRisksView riskItems={[group, sibling]} summary={null} onSelectRisk={onSelectRisk} />);

    await user.click(screen.getByRole('button', {name: '展开事件有效期'}));
    const bars = screen.getAllByTestId('gantt-bar');
    await user.click(bars[1]);
    expect(onSelectRisk).toHaveBeenCalledWith(sibling);

    onSelectRisk.mockClear();
    await user.click(bars[0]);
    expect(onSelectRisk).toHaveBeenCalledWith(group);
  });

  it('已失效模式下同样展示甘特图，状态按已失效组标注', async () => {
    const user = userEvent.setup();
    const expiredGroup = groupedItem(
      {id: '9', companyName: '已失效供应商', status: 'invalid', validityState: 'expired'},
      [
        mergedEvent({alertId: 9, eventId: 901, summary: '旧事件', status: 'expired', expiresAt: '2026-09-03T00:00:00Z'}),
        mergedEvent({alertId: 10, eventId: 902, status: 'expired'}),
      ],
    );
    render(<CurrentRisksView riskItems={[expiredGroup]} summary={null} onSelectRisk={vi.fn()} />);

    await user.click(screen.getByRole('button', {name: '已失效'}));

    const card = screen.getByText('已失效供应商').closest('article');
    if (card === null) throw new Error('已失效卡片未渲染');
    expect(within(card).getByText('已失效')).toBeInTheDocument();
    await user.click(within(card).getByRole('button', {name: '展开事件有效期'}));
    expect(within(card).getAllByTestId('gantt-bar')).toHaveLength(2);
    // 过期事件的条用 slate 色，不再使用品牌蓝
    expect(within(card).getAllByTestId('gantt-bar')[0].firstElementChild?.className).toContain('bg-slate-400');
  });

  it('组内事件字段缺失时用当前事件的 validUntil 画单条，不报错也不显示组摘要', () => {
    render(<CurrentRisksView riskItems={[baseItem({id: '5'})]} summary={null} onSelectRisk={vi.fn()} />);

    expect(screen.queryAllByTestId('gantt-bar')).toHaveLength(0);
    expect(screen.getByRole('button', {name: '展开事件有效期'})).toBeInTheDocument();
    expect(screen.queryByText(/个事件$/)).not.toBeInTheDocument();
    expect(screen.queryByText('部分失效')).not.toBeInTheDocument();
    // 单条不再重复 min ~ max 的组摘要，仍保留原有截止/复核信息
    expect(screen.getByText('截止')).toBeInTheDocument();
  });
});
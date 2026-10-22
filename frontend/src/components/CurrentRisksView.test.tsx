import {cleanup, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {RiskAlertSummary} from '../api';
import type {RiskItem} from '../types';
import {CurrentRisksView} from './CurrentRisksView';

const baseItem = (overrides: Partial<RiskItem> = {}): RiskItem => ({
  id: '1',
  companyName: '示例供应商',
  level: 'P2',
  levelName: '高风险',
  riskType: '运输中断',
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

  it('展示截止时间、复核时间与中文原因，且不暴露策略版本', () => {
    render(
      <CurrentRisksView
        riskItems={[baseItem({validityReason: {code: 'effective_signal_support', anchor_source: 'published_at', details: {}}})]}
        summary={null}
        onSelectRisk={vi.fn()}
      />,
    );

    expect(screen.getByText('截止')).toBeInTheDocument();
    expect(screen.getByText('复核')).toBeInTheDocument();
    expect(screen.getByText('原因')).toBeInTheDocument();
    expect(screen.getByText('存在有效信号支撑')).toBeInTheDocument();
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
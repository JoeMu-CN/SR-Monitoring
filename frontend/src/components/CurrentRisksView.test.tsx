import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
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
  validityReason: {code: 'active', anchor_source: 'published_at', details: {}},
  validityPolicyVersion: 'v1',
  ...overrides,
});

afterEach(cleanup);

describe('当前风险列表有效期状态展示', () => {
  it('以文本展示 active/expired/revoked/superseded/conflicted/legacy 状态而不只靠颜色', async () => {
    const user = userEvent.setup();
    const items: RiskItem[] = [
      baseItem({id: '1', validityState: 'active'}),
      baseItem({id: '2', companyName: '过期供应商', validityState: 'expired', status: 'invalid'}),
      baseItem({id: '3', companyName: '撤销供应商', validityState: 'revoked', status: 'invalid'}),
      baseItem({id: '4', companyName: '替代供应商', validityState: 'superseded', status: 'invalid'}),
      baseItem({id: '5', companyName: '冲突供应商', validityState: 'conflicted', status: 'invalid'}),
      baseItem({id: '6', companyName: '旧版供应商', validityState: 'legacy', status: 'invalid'}),
    ];

    render(<CurrentRisksView riskItems={items} onSelectRisk={vi.fn()} />);

    expect(screen.getByText('有效')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: '已失效'}));
    expect(screen.getByText('已过期')).toBeInTheDocument();
    expect(screen.getByText('已撤销')).toBeInTheDocument();
    expect(screen.getByText('已替代')).toBeInTheDocument();
    expect(screen.getByText('冲突')).toBeInTheDocument();
    expect(screen.getByText('旧版兼容')).toBeInTheDocument();
  });

  it('展示截止时间、复核时间、原因与策略版本', () => {
    render(
      <CurrentRisksView
        riskItems={[baseItem()]}
        onSelectRisk={vi.fn()}
      />,
    );

    expect(screen.getByText('截止')).toBeInTheDocument();
    expect(screen.getByText('复核')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('策略版本')).toBeInTheDocument();
    expect(screen.getByText('v1')).toBeInTheDocument();
  });

  it('当前/历史可切换：默认只显示当前有效，切换到已失效显示历史', async () => {
    const user = userEvent.setup();
    const items: RiskItem[] = [
      baseItem({id: '1', validityState: 'active'}),
      baseItem({id: '2', companyName: '过期供应商', validityState: 'expired', status: 'invalid'}),
    ];

    render(<CurrentRisksView riskItems={items} onSelectRisk={vi.fn()} />);

    expect(screen.getByText('示例供应商')).toBeInTheDocument();
    expect(screen.queryByText('过期供应商')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: '已失效'}));
    expect(screen.getByText('过期供应商')).toBeInTheDocument();
    expect(screen.queryByText('示例供应商')).not.toBeInTheDocument();
  });
});
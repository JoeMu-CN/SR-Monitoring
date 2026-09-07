import {cleanup, render, screen, within} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {MonitoringDimension} from '../types';
import {RuleEngineView} from './RuleEngineView';

const dimension = (overrides: Partial<MonitoringDimension> = {}): MonitoringDimension => ({
  id: 'natural',
  name: '自然环境',
  icon: 'landscape',
  enabled: true,
  ruleId: 'natural-v1',
  severityWeight: 0.5,
  relevanceWeight: 0.5,
  thresholds: {p1: 85, p2: 65, p3: 40},
  ttlHours: 336,
  contentItems: ['地震', '台风'],
  dataSources: [
    {code: 'nmc-weather', name: '中央气象台', status: 'connected', validityMode: 'fixed_days', validityPolicyVersion: 'v1'},
    {code: 'usgs-earthquake', name: 'USGS 地震', status: 'planned', validityMode: null, validityPolicyVersion: null},
  ],
  ...overrides,
});

afterEach(cleanup);

describe('规则引擎引用数据源有效期展示', () => {
  it('展示引用数据源的有效期模式与策略版本', () => {
    render(
      <RuleEngineView
        dimensions={[dimension()]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    const sourceRow = screen.getByText('中央气象台').closest('div') as HTMLElement;
    expect(within(sourceRow).getByText(/固定天数/)).toBeInTheDocument();
    expect(within(sourceRow).getByText(/策略版本 v1/)).toBeInTheDocument();
  });

  it('未配置策略的数据源不显示有效期模式', () => {
    render(
      <RuleEngineView
        dimensions={[dimension()]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    const sourceRow = screen.getByText('USGS 地震').closest('div') as HTMLElement;
    expect(within(sourceRow).queryByText(/固定天数/)).not.toBeInTheDocument();
    expect(within(sourceRow).queryByText(/策略版本/)).not.toBeInTheDocument();
  });
});
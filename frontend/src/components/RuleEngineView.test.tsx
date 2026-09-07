import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {updateDimensionConfig} from '../api';
import type {MonitoringDimension} from '../types';
import {RuleEngineView} from './RuleEngineView';

const dimension = (overrides: Partial<MonitoringDimension> = {}): MonitoringDimension => ({
  id: 'natural',
  name: '自然环境',
  icon: 'landscape',
  enabled: true,
  ruleId: 'natural-v1',
  severityScores: {critical: 35, high: 28, medium: 20, low: 10},
  associationScores: {registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12},
  thresholds: {p1: 85, p2: 65, p3: 40},
  ttlHours: 336,
  contentItems: ['地震', '台风'],
  dataSources: [
    {code: 'nmc-weather', name: '中央气象台', status: 'connected', validityMode: 'fixed_days', validityPolicyVersion: 'v1'},
    {code: 'usgs-earthquake', name: 'USGS 地震', status: 'planned', validityMode: null, validityPolicyVersion: null},
  ],
  ...overrides,
});

const geopoliticalDimension = (): MonitoringDimension => ({
  id: 'geopolitical',
  name: '地缘政治与安全',
  icon: 'public',
  enabled: true,
  ruleId: 'geopolitical-v1',
  severityScores: {critical: 35, high: 28, medium: 20, low: 10},
  associationScores: {registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12, country: 8, industry: 12},
  thresholds: {p1: 85, p2: 65, p3: 40},
  ttlHours: 336,
  contentItems: ['制裁', '出口管制'],
  dataSources: [{code: 'ofac-sdn', name: 'OFAC SDN', status: 'connected'}],
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

describe('规则引擎评分矩阵编辑', () => {
  it('渲染地缘政治维度的 8 个关联类型真实数值，只改一项后 patch 只含该项', () => {
    vi.stubGlobal('alert', vi.fn());
    const original = geopoliticalDimension();
    const onUpdateDimension = vi.fn().mockResolvedValue(undefined);
    render(
      <RuleEngineView
        dimensions={[original]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={onUpdateDimension}
        role="admin"
      />,
    );

    // 8 个关联类型（含 country/industry）都渲染出真实数值
    for (const key of ['注册号', '法人全称', '别名', '地点距离', '地点文本', '产品', '国家', '行业']) {
      expect(screen.getByText(key)).toBeInTheDocument();
    }
    expect(screen.getByDisplayValue('8')).toBeInTheDocument(); // country
    expect(screen.getAllByDisplayValue('12').length).toBeGreaterThan(0); // industry/product

    // 只改 country 从 8 → 10
    const countryInput = screen.getByDisplayValue('8');
    fireEvent.change(countryInput, {target: {value: '10'}});
    fireEvent.click(screen.getByText('保存配置'));

    expect(onUpdateDimension).toHaveBeenCalledTimes(1);
    const updated = onUpdateDimension.mock.calls[0]?.[0] as MonitoringDimension;
    expect(updated.associationScores.country).toBe(10);
    // 其余关联类型未被改动
    expect(updated.associationScores.industry).toBe(12);
    expect(updated.associationScores.registry_no).toBe(30);

    // patch 只含 country 这一项
    const patch = updateDimensionConfig(original, updated);
    expect(patch.association_scores).toEqual({country: 10});
    expect(patch).not.toHaveProperty('severity_scores');
  });

  it('提交超上限 critical=40 时前端阻止且不发请求', () => {
    const onUpdateDimension = vi.fn().mockResolvedValue(undefined);
    render(
      <RuleEngineView
        dimensions={[geopoliticalDimension()]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={onUpdateDimension}
        role="admin"
      />,
    );

    const criticalInput = screen.getByDisplayValue('35');
    fireEvent.change(criticalInput, {target: {value: '40'}});
    fireEvent.click(screen.getByText('保存配置'));

    expect(onUpdateDimension).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/超出允许范围/);
  });

  it('后端返回 422 时错误以 role=alert 可访问地呈现', async () => {
    const onUpdateDimension = vi.fn().mockRejectedValue(new Error('分值超出允许范围：严重程度 0-35'));
    render(
      <RuleEngineView
        dimensions={[geopoliticalDimension()]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={onUpdateDimension}
        role="admin"
      />,
    );

    fireEvent.click(screen.getByText('保存配置'));

    expect(await screen.findByRole('alert')).toHaveTextContent(/超出允许范围/);
  });
});
import React from 'react';
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {MemoryRouter} from 'react-router-dom';
import {api, updateDimensionConfig} from '../api';
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
    {code: 'nmc-weather', name: '中央气象台', status: 'connected', linked: true, enabled: true, adapterStatus: 'builtin', lastCollectedAt: '2026-09-01T08:00:00Z', validSignalCount: 12},
    {code: 'usgs-earthquake', name: 'USGS 地震', status: 'planned', linked: false, enabled: null, adapterStatus: null, lastCollectedAt: null, validSignalCount: null},
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
  dataSources: [{code: 'ofac-sdn', name: 'OFAC SDN', status: 'connected', linked: true, enabled: true, adapterStatus: 'builtin', lastCollectedAt: '2026-09-01T08:00:00Z', validSignalCount: 5}],
});

const renderWithRouter = (ui: React.ReactElement) => render(<MemoryRouter>{ui}</MemoryRouter>);

afterEach(cleanup);

describe('规则引擎引用数据源真实状态', () => {
  it('enabled 信源渲染真实状态且链接 href 正确', async () => {
    vi.spyOn(api, 'dimensionInputs').mockResolvedValue({
      declared_total: 2, declared_linked: 1, declared_enabled: 1,
      observed: [{code: 'nmc-weather', name: '中央气象台', signal_count: 12, latest_at: '2026-09-01T08:00:00Z'}],
      has_input: true,
    });
    renderWithRouter(
      <RuleEngineView
        dimensions={[dimension()]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    const sourceRow = screen.getByText('中央气象台').closest('div') as HTMLElement;
    expect(within(sourceRow).getByText('已启用')).toBeInTheDocument();
    expect(within(sourceRow).getByText(/12 条/)).toBeInTheDocument();
    const link = within(sourceRow).getByRole('link');
    expect(link).toHaveAttribute('href', '/sources/nmc-weather/signals?scope=valid&page=1');
  });

  it('disabled 信源不出现「已接入」字样', () => {
    const dim = dimension({
      dataSources: [
        {code: 'nmc-weather', name: '中央气象台', status: 'connected', linked: true, enabled: false, adapterStatus: 'builtin', lastCollectedAt: '2026-09-01T08:00:00Z', validSignalCount: 12},
      ],
    });
    vi.spyOn(api, 'dimensionInputs').mockResolvedValue({
      declared_total: 1, declared_linked: 1, declared_enabled: 0,
      observed: [], has_input: false,
    });
    renderWithRouter(
      <RuleEngineView
        dimensions={[dim]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    const sourceRow = screen.getByText('中央气象台').closest('div') as HTMLElement;
    expect(within(sourceRow).getByText('已停用')).toBeInTheDocument();
    expect(within(sourceRow).queryByText('已接入')).not.toBeInTheDocument();
  });

  it('linked=false 渲染「未创建」且无链接', () => {
    const dim = dimension({
      dataSources: [
        {code: 'usgs-earthquake', name: 'USGS 地震', status: 'planned', linked: false, enabled: null, adapterStatus: null, lastCollectedAt: null, validSignalCount: null},
      ],
    });
    vi.spyOn(api, 'dimensionInputs').mockResolvedValue({
      declared_total: 1, declared_linked: 0, declared_enabled: 0,
      observed: [], has_input: false,
    });
    renderWithRouter(
      <RuleEngineView
        dimensions={[dim]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    const sourceRow = screen.getByText('USGS 地震').closest('div') as HTMLElement;
    expect(within(sourceRow).getByText('未创建')).toBeInTheDocument();
    expect(within(sourceRow).queryByRole('link')).not.toBeInTheDocument();
  });

  it('has_input=false 时维度卡出现文本提示「当前无输入」', async () => {
    vi.spyOn(api, 'dimensionInputs').mockResolvedValue({
      declared_total: 1, declared_linked: 1, declared_enabled: 0,
      observed: [], has_input: false,
    });
    renderWithRouter(
      <RuleEngineView
        dimensions={[dimension()]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    expect((await screen.findAllByText('当前无输入')).length).toBeGreaterThan(0);
  });

  it('inputs 请求失败时规则配置区仍完整渲染、错误以可访问方式提示', async () => {
    vi.spyOn(api, 'dimensionInputs').mockRejectedValue(new Error('网络错误'));
    renderWithRouter(
      <RuleEngineView
        dimensions={[dimension()]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    // 规则配置区仍完整渲染
    expect(screen.getByText('自然环境 规则配置')).toBeInTheDocument();
    expect(screen.getByText('保存配置')).toBeInTheDocument();
    // 错误以 role=alert 可访问地提示
    expect(await screen.findByRole('alert')).toHaveTextContent(/输入健康度加载失败/);
  });

  it('QA happy：四种信源呈现互不相同且链接只在 linked 项出现', async () => {
    const dim = dimension({
      dataSources: [
        {code: 'nmc-weather', name: '中央气象台', status: 'connected', linked: true, enabled: true, adapterStatus: 'builtin', lastCollectedAt: '2026-09-01T08:00:00Z', validSignalCount: 12},
        {code: 'usgs-earthquake', name: 'USGS 地震', status: 'planned', linked: true, enabled: false, adapterStatus: 'builtin', lastCollectedAt: '2026-08-20T08:00:00Z', validSignalCount: 3},
        {code: 'cenc-earthquake', name: '中国地震台网', status: 'planned', linked: false, enabled: null, adapterStatus: null, lastCollectedAt: null, validSignalCount: null},
        {code: 'nhc-cdc', name: '国家卫健委', status: 'planned', linked: false, enabled: null, adapterStatus: null, lastCollectedAt: null, validSignalCount: null},
      ],
    });
    vi.spyOn(api, 'dimensionInputs').mockResolvedValue({
      declared_total: 4, declared_linked: 2, declared_enabled: 1,
      observed: [{code: 'nmc-weather', name: '中央气象台', signal_count: 12, latest_at: '2026-09-01T08:00:00Z'}],
      has_input: true,
    });
    renderWithRouter(
      <RuleEngineView
        dimensions={[dim]}
        onToggleDimension={vi.fn()}
        onUpdateDimension={vi.fn()}
        role="viewer"
      />,
    );

    // 四种呈现互不相同
    const nmcRow = screen.getByText('中央气象台').closest('div') as HTMLElement;
    const usgsRow = screen.getByText('USGS 地震').closest('div') as HTMLElement;
    const cencRow = screen.getByText('中国地震台网').closest('div') as HTMLElement;
    const nhcRow = screen.getByText('国家卫健委').closest('div') as HTMLElement;

    expect(within(nmcRow).getByText('已启用')).toBeInTheDocument();
    expect(within(usgsRow).getByText('已停用')).toBeInTheDocument();
    expect(within(cencRow).getByText('未创建')).toBeInTheDocument();
    expect(within(nhcRow).getByText('未创建')).toBeInTheDocument();

    // 链接只在 linked 项出现
    expect(within(nmcRow).getByRole('link')).toHaveAttribute('href', '/sources/nmc-weather/signals?scope=valid&page=1');
    expect(within(usgsRow).getByRole('link')).toHaveAttribute('href', '/sources/usgs-earthquake/signals?scope=valid&page=1');
    expect(within(cencRow).queryByRole('link')).not.toBeInTheDocument();
    expect(within(nhcRow).queryByRole('link')).not.toBeInTheDocument();
  });
});

describe('规则引擎评分矩阵编辑', () => {
  it('渲染地缘政治维度的 8 个关联类型真实数值，只改一项后 patch 只含该项', () => {
    vi.stubGlobal('alert', vi.fn());
    const original = geopoliticalDimension();
    const onUpdateDimension = vi.fn().mockResolvedValue(undefined);
    renderWithRouter(
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
    renderWithRouter(
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
    renderWithRouter(
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
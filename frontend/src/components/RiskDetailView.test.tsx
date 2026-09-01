import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter, Route, Routes, useNavigate} from 'react-router-dom';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {EventDetailRead, RiskAlertRead} from '../api';
import {RiskRouteView} from '../RiskRouteView';

interface MockResponse {
  readonly ok: boolean;
  readonly status: number;
  json: () => Promise<unknown>;
}

const response = (body: unknown, status = 200): MockResponse => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

const alert = (id: number, status: RiskAlertRead['status'] = 'current'): RiskAlertRead => ({
  id,
  level: 'P2',
  score: 72,
  score_detail: {severity: 30, association: 22, rule_version: 'v1'},
  status,
  supplier_id: 12,
  supplier_name: `供应商 ${id}`,
  event_id: id * 10,
  event_type: 'logistics',
  event_subtype: 'transport_disruption',
  event_summary: `事件摘要 ${id}`,
  event_start_at: '2026-08-30T08:00:00Z',
  event_end_at: null,
  confidence: 0.91,
  match_type: 'entity',
  match_reasons: ['主体名称匹配'],
  match_evidence: [{supplier_name: `供应商 ${id}`}],
  source_title: `来源 ${id}`,
  source_url: `https://example.test/source-${id}`,
  published_at: '2026-08-30T08:00:00Z',
  updated_at: '2026-08-31T08:00:00Z',
});

const event = (id: number, overrides: Partial<EventDetailRead> = {}): EventDetailRead => ({
  id,
  dedup_key: `dedup-${id}`,
  event_type: 'logistics',
  event_subtype: 'transport_disruption',
  severity: 'high',
  summary: `事件详情 ${id}`,
  start_at: '2026-08-30T08:00:00Z',
  end_at: null,
  confidence: 0.88,
  created_at: '2026-08-30T08:00:00Z',
  signals: [{signal_id: id, title: `原始信号 ${id}`, content: '信号原文内容', url: 'https://example.test/signal', published_at: '2026-08-30T08:00:00Z'}],
  entities: [{name: '关联主体', normalized_name: '关联主体有限公司', registry_no: '91310000'}],
  locations: [{name: '上海生产地点', country_code: 'CN', region: '上海', city: '上海', district: '浦东', latitude: 31.2, longitude: 121.5, radius_km: 10}],
  ...overrides,
});

const renderAt = (path: string, onRequestError = vi.fn()) => render(
  <MemoryRouter initialEntries={[path]}>
    <Routes>
      <Route path="/risks/:alertId" element={<RiskRouteView riskItems={[]} onAskAssistant={vi.fn()} onCloseDetail={vi.fn()} onExportReport={vi.fn()} onSelectRisk={vi.fn()} onRequestError={onRequestError} />} />
    </Routes>
  </MemoryRouter>,
);

const RouteSwitch = () => {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate('/risks/2')}>切换提醒</button>
      <Routes>
        <Route path="/risks/:alertId" element={<RiskRouteView riskItems={[]} onAskAssistant={vi.fn()} onCloseDetail={vi.fn()} onExportReport={vi.fn()} onSelectRisk={vi.fn()} onRequestError={vi.fn()} />} />
      </Routes>
    </>
  );
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('RiskDetailView', () => {
  it('先加载提醒再加载事件，并展示 API 返回的证据', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    expect(await screen.findByText('原始信号 70')).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(['/api/v1/risk-alerts/7', '/api/v1/events/70']);
    expect(screen.getByText(/规范名称：关联主体有限公司/)).toBeInTheDocument();
    expect(screen.getByText('上海生产地点')).toBeInTheDocument();
  });

  it.each([
    ['current', '当前有效'],
    ['expired', '已失效'],
  ] as const)('渲染 %s 提醒状态', async (status, statusLabel) => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(alert(7, status)))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    expect(await screen.findByText(statusLabel)).toBeInTheDocument();
  });

  it('事件证据为空时显示明确空态', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response(event(70, {signals: [], entities: [], locations: []})));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    expect(await screen.findByText('暂无原始信号')).toBeInTheDocument();
    expect(screen.getByText('暂无主体证据')).toBeInTheDocument();
    expect(screen.getByText('暂无地点证据')).toBeInTheDocument();
  });

  it('提醒 404 时显示可返回列表的状态', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response({detail: '风险提醒不存在'}, 404));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/404');

    expect(await screen.findByText('风险提醒不存在')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '返回风险列表'})).toBeInTheDocument();
  });

  it('提醒请求返回 401 时转交全局鉴权错误处理', async () => {
    const onRequestError = vi.fn();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response({detail: '登录已失效'}, 401)));

    renderAt('/risks/7', onRequestError);

    expect(await screen.findByText('风险提醒加载失败')).toBeInTheDocument();
    expect(onRequestError).toHaveBeenCalledWith(expect.objectContaining({status: 401}));
  });

  it('事件请求返回 403 时转交全局权限错误处理', async () => {
    const onRequestError = vi.fn();
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response({detail: '无权查看事件证据'}, 403)));

    renderAt('/risks/7', onRequestError);

    expect(await screen.findByRole('alert')).toHaveTextContent('事件详情加载失败');
    expect(onRequestError).toHaveBeenCalledWith(expect.objectContaining({status: 403}));
  });

  it('事件详情失败后只重试事件请求', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response({detail: '事件不存在'}, 404))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    expect(await screen.findByRole('alert')).toHaveTextContent('事件详情加载失败');
    await user.click(screen.getByRole('button', {name: '重试事件详情'}));
    expect(await screen.findByText('原始信号 70')).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(['/api/v1/risk-alerts/7', '/api/v1/events/70', '/api/v1/events/70']);
  });

  it('加载中显示提醒详情状态', () => {
    const fetchMock = vi.fn(() => new Promise<MockResponse>(() => undefined));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    expect(screen.getByText('正在加载风险提醒详情')).toBeInTheDocument();
  });

  it('快速切换 alertId 时忽略旧提醒响应', async () => {
    let resolveFirstAlert: ((value: MockResponse) => void) | undefined;
    const firstAlert = new Promise<MockResponse>((resolve) => {
      resolveFirstAlert = resolve;
    });
    const fetchMock = vi.fn((path: string) => {
      if (path === '/api/v1/risk-alerts/1') return firstAlert;
      if (path === '/api/v1/risk-alerts/2') return Promise.resolve(response(alert(2)));
      return Promise.resolve(response(event(20)));
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={['/risks/1']}>
        <RouteSwitch />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', {name: '切换提醒'}));
    expect(await screen.findByRole('heading', {name: '供应商 2'})).toBeInTheDocument();
    resolveFirstAlert?.(response(alert(1)));

    expect(screen.queryByText('供应商 1')).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(['/api/v1/risk-alerts/1', '/api/v1/risk-alerts/2', '/api/v1/events/20']);
  });
});

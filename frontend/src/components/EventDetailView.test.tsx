import {cleanup, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter, Route, Routes} from 'react-router-dom';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {api, ApiError, type EventDetailRead} from '../api';
import {EventDetailView} from './EventDetailView';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {...actual.api, eventDetail: vi.fn()},
  };
});

afterEach(cleanup);

const detail = (overrides: Partial<EventDetailRead> = {}): EventDetailRead => ({
  id: 6514,
  dedup_key: 'weather:zhejiang:2026-10-22',
  event_type: 'weather',
  event_subtype: null,
  severity: 'high',
  summary: '台风“蔷琵”影响浙江沿海，宁波港区作业受限',
  start_at: '2026-10-22T00:00:00Z',
  end_at: null,
  confidence: 0.92,
  created_at: '2026-10-22T01:00:00Z',
  signals: [{
    signal_id: 9001,
    title: '台风蓝色预警信号',
    content: '预警原文',
    url: null,
    published_at: '2026-10-22T00:00:00Z',
    collected_at: '2026-10-22T01:00:00Z',
    validity_state: 'active',
  }],
  entities: [],
  locations: [{name: '浙江省宁波市', country_code: 'CN', region: '浙江省', city: '宁波市', district: null, latitude: null, longitude: null, radius_km: null}],
  validity_state: 'active',
  valid_until: '2026-10-29T00:00:00Z',
  review_due_at: null,
  validity_policy_version: 'v1',
  validity_reason: {code: 'active', anchor_source: 'published_at', details: {}},
  suppliers: [
    {supplier_id: 12, supplier_name: '宁波精密部件有限公司', level: 'P2', score: 78, alert_status: 'current', expires_at: '2026-10-29T00:00:00Z', version_superseded: false, alert_id: 77},
    {supplier_id: 13, supplier_name: '浙江海运物流有限公司', level: 'P3', score: 61, alert_status: 'current', expires_at: '2026-10-30T00:00:00Z', version_superseded: true, alert_id: 78},
  ],
  ...overrides,
});

const renderDetail = (path = '/events/6514') => {
  const onBack = vi.fn();
  const onSelectAlert = vi.fn();
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/events/:eventId" element={<EventDetailView onBack={onBack} onSelectAlert={onSelectAlert} />} />
        <Route path="/risks" element={<div>风险事件列表</div>} />
      </Routes>
    </MemoryRouter>,
  );
  return {onBack, onSelectAlert};
};

describe('EventDetailView', () => {
  beforeEach(() => {
    vi.mocked(api.eventDetail).mockResolvedValue(detail());
  });

  it('展示事件摘要、信源与受影响供应商，点击供应商进入 /risks/:alertId', async () => {
    const {onSelectAlert} = renderDetail();

    expect(await screen.findByText('台风“蔷琵”影响浙江沿海，宁波港区作业受限')).toBeInTheDocument();
    expect(screen.getByText('天气')).toBeInTheDocument();
    expect(screen.getByText('浙江省宁波市')).toBeInTheDocument();
    expect(screen.getByText('台风蓝色预警信号')).toBeInTheDocument();
    expect(api.eventDetail).toHaveBeenCalledWith(6514);

    expect(screen.getByText('受影响供应商（2）')).toBeInTheDocument();
    expect(screen.getByText('宁波精密部件有限公司')).toBeInTheDocument();
    expect(screen.getByText('已被新版本替代')).toBeInTheDocument();
    const firstSupplierRow = screen.getByRole('button', {name: /宁波精密部件有限公司/});
    expect(within(firstSupplierRow).getByText('分数 78')).toBeInTheDocument();
    expect(within(firstSupplierRow).getByText('报警状态 current')).toBeInTheDocument();

    await userEvent.setup().click(firstSupplierRow);
    expect(onSelectAlert).toHaveBeenCalledWith('77');
  });

  it('后端未返回 suppliers 字段时展示空态而不是崩溃', async () => {
    vi.mocked(api.eventDetail).mockResolvedValue(detail({suppliers: undefined}));
    renderDetail();

    expect(await screen.findByText('台风“蔷琵”影响浙江沿海，宁波港区作业受限')).toBeInTheDocument();
    expect(screen.getByText('受影响供应商（0）')).toBeInTheDocument();
    expect(screen.getByText('暂无受影响供应商。')).toBeInTheDocument();
  });

  it('返回按钮回到风险事件列表', async () => {
    const {onBack} = renderDetail();
    await screen.findByText('台风“蔷琵”影响浙江沿海，宁波港区作业受限');

    await userEvent.setup().click(screen.getByRole('button', {name: /返回风险事件/}));
    expect(onBack).toHaveBeenCalled();
  });

  it('详情请求失败时显示错误态与重试入口', async () => {
    vi.mocked(api.eventDetail).mockRejectedValue(new ApiError(500, '事件服务不可用'));
    renderDetail();

    expect(await screen.findByText('事件详情不可用')).toBeInTheDocument();
    expect(screen.getByText('事件服务不可用')).toBeInTheDocument();

    vi.mocked(api.eventDetail).mockResolvedValue(detail());
    await userEvent.setup().click(screen.getByRole('button', {name: /重新加载/}));
    expect(await screen.findByText('台风“蔷琵”影响浙江沿海，宁波港区作业受限')).toBeInTheDocument();
  });
});

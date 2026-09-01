import {cleanup, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter, Route, Routes, useLocation} from 'react-router-dom';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {api, ApiError, type SourceSignalListResponse} from '../api';
import {SourceSignalsView} from './SourceSignalsView';

const emptyResponse: SourceSignalListResponse = {
  source: {id: 17, code: 'OFFICIAL-17', name: '官方风险源', signal_validity_days: 30},
  items: [],
  total: 0,
  offset: 0,
  limit: 20,
};

const populatedResponse: SourceSignalListResponse = {
  ...emptyResponse,
  items: [{
    id: 91,
    external_id: 'EXT-91',
    title: '供应链运输中断',
    content: '风险详情'.repeat(80),
    url: 'https://official.example/events/91',
    published_at: '2026-08-31T08:00:00Z',
    collected_at: '2026-09-01T08:00:00Z',
  }],
  total: 25,
  offset: 20,
};

const LocationProbe = () => {
  const location = useLocation();
  return <output aria-label="当前地址">{location.pathname}{location.search}</output>;
};

const renderView = (path: string, onRequestError = vi.fn()) => {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/sources/:sourceId/signals" element={<SourceSignalsView onRequestError={onRequestError} />} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  );
  return onRequestError;
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('数据源采集记录清单', () => {
  it('将缺失查询参数规范化为当前有效第一页', async () => {
    const request = vi.spyOn(api, 'sourceSignals').mockResolvedValue(emptyResponse);

    renderView('/sources/17/signals');

    expect(await screen.findByText('暂无当前有效记录')).toBeInTheDocument();
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/sources/17/signals?scope=valid&page=1');
    expect(request).toHaveBeenCalledWith(17, 'valid', 0);
  });

  it('按页读取全部历史并支持展开正文和返回上一页', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'sourceSignals')
      .mockResolvedValueOnce(populatedResponse)
      .mockResolvedValueOnce({...populatedResponse, offset: 0});

    renderView('/sources/17/signals?scope=all&page=2');

    expect(await screen.findByText('供应链运输中断')).toBeInTheDocument();
    expect(request).toHaveBeenNthCalledWith(1, 17, 'all', 20);
    expect(screen.getByText('第 2 页')).toBeInTheDocument();
    expect(screen.getByRole('link', {name: '查看原文'})).toHaveAttribute('href', 'https://official.example/events/91');

    await user.click(screen.getByRole('button', {name: '展开正文'}));
    expect(screen.getByRole('button', {name: '收起正文'})).toHaveAttribute('aria-expanded', 'true');

    await user.click(screen.getByRole('button', {name: '上一页'}));
    await waitFor(() => expect(request).toHaveBeenNthCalledWith(2, 17, 'all', 0));
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('scope=all&page=1');
  });

  it('切换到全部历史时重置到第一页', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'sourceSignals')
      .mockResolvedValueOnce({...emptyResponse, offset: 20})
      .mockResolvedValueOnce(emptyResponse);

    renderView('/sources/17/signals?scope=valid&page=2');
    await screen.findByText('暂无当前有效记录');
    await user.click(screen.getByRole('tab', {name: '全部历史'}));

    await waitFor(() => expect(request).toHaveBeenNthCalledWith(2, 17, 'all', 0));
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('scope=all&page=1');
  });

  it('将权限错误上报给会话边界且不显示重试按钮', async () => {
    vi.spyOn(api, 'sourceSignals').mockRejectedValue(new ApiError(403, '权限不足'));
    const onRequestError = renderView('/sources/17/signals?scope=valid&page=1');

    expect(await screen.findByRole('alert')).toHaveTextContent('无权访问采集记录');
    expect(onRequestError).toHaveBeenCalledWith(expect.objectContaining({status: 403}));
    expect(screen.queryByRole('button', {name: '重试'})).not.toBeInTheDocument();
  });

  it('普通加载错误可重试并恢复为空态', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'sourceSignals')
      .mockRejectedValueOnce(new ApiError(500, '服务暂不可用'))
      .mockResolvedValueOnce(emptyResponse);

    renderView('/sources/17/signals?scope=valid&page=1');
    await user.click(await screen.findByRole('button', {name: '重试'}));

    expect(await screen.findByText('暂无当前有效记录')).toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(2);
  });
});

import {cleanup, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter, Route, Routes, useLocation} from 'react-router-dom';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {api, ApiError, type SupplierListItem, type SupplierListResponse} from '../api';
import {SuppliersView} from './SuppliersView';

const makeItem = (index: number, overrides: Partial<SupplierListItem> = {}): SupplierListItem => ({
  id: index,
  supplier_code: `SUP-${String(index).padStart(3, '0')}`,
  legal_name: `供应商 ${String(index).padStart(3, '0')}`,
  country_code: 'CN',
  registry_no: `REG-${index}`,
  registration_address: null,
  industry: '精密件',
  raw_materials: [],
  enabled: true,
  aliases: [],
  sites: [],
  products: [{id: index, name: `产品 ${index}`, keywords: []}],
  current_risk_level: null,
  current_risk_score: null,
  ...overrides,
});

const makePage = (offset: number, count: number, total: number): SupplierListResponse => ({
  items: Array.from({length: count}, (_, index) => makeItem(offset + index + 1)),
  total,
  limit: 20,
  offset,
});

// 用 <p> 而非 <output>：<output> 的隐式 role 是 status，会与加载态断言冲突。
const LocationProbe = () => {
  const location = useLocation();
  return <p aria-label="当前地址">{location.pathname}{location.search}</p>;
};

interface RenderOptions {
  readonly role?: 'viewer' | 'admin';
  readonly refreshToken?: number;
  readonly onToggleStatus?: () => void;
  readonly onEditSupplier?: () => void;
  readonly onRequestError?: () => void;
}

const renderView = (path: string, options: RenderOptions = {}) => {
  const handlers = {
    onOpenImportModal: vi.fn(),
    onEditSupplier: options.onEditSupplier ?? vi.fn(),
    onToggleStatus: options.onToggleStatus ?? vi.fn(),
    onAskAssistant: vi.fn(),
    onRequestError: options.onRequestError ?? vi.fn(),
  };
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/suppliers"
          element={<SuppliersView role={options.role ?? 'admin'} refreshToken={options.refreshToken ?? 0} {...handlers} />}
        />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  );
  return {...handlers, rerender: view.rerender};
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('供应商服务端分页清单', () => {
  it('从 URL 恢复查询词、状态与页码并换算为服务端偏移量', async () => {
    const request = vi.spyOn(api, 'supplierPage').mockResolvedValue(makePage(20, 5, 25));

    renderView('/suppliers?q=%E9%92%A2&status=paused&page=2');

    expect(await screen.findByText('供应商 021')).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith('钢', 'paused', 20);
    expect(screen.getByText('显示 21-25，共 25 条')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '上一页'})).toBeEnabled();
    expect(screen.getByRole('button', {name: '下一页'})).toBeDisabled();
  });

  it('翻页写入 URL 并在第一页禁用上一页', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'supplierPage')
      .mockResolvedValueOnce(makePage(0, 20, 25))
      .mockResolvedValueOnce(makePage(20, 5, 25));

    renderView('/suppliers');

    expect(await screen.findByText('供应商 001')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '上一页'})).toBeDisabled();
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/suppliers');

    await user.click(screen.getByRole('button', {name: '下一页'}));

    expect(await screen.findByText('供应商 021')).toBeInTheDocument();
    expect(request).toHaveBeenNthCalledWith(2, '', 'all', 20);
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/suppliers?page=2');
  });

  it('切换监控状态时回到第一页并按状态语义发起请求', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'supplierPage')
      .mockResolvedValueOnce(makePage(20, 5, 25))
      .mockResolvedValueOnce(makePage(0, 2, 2));

    renderView('/suppliers?page=2');
    await screen.findByText('供应商 021');

    await user.selectOptions(screen.getByLabelText('监控状态:'), 'high_risk');

    await waitFor(() => expect(request).toHaveBeenNthCalledWith(2, '', 'high_risk', 0));
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/suppliers?status=high_risk');
  });

  it('搜索输入防抖后归零页码并写入查询词', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'supplierPage')
      .mockResolvedValueOnce(makePage(20, 5, 25))
      .mockResolvedValueOnce(makePage(0, 1, 1));

    renderView('/suppliers?page=2');
    await screen.findByText('供应商 021');

    await user.type(screen.getByLabelText('搜索供应商'), '功率');

    // 键入过程中只允许在防抖结束后发出一次请求，避免每个字符都打一次服务端。
    await waitFor(() => expect(request).toHaveBeenNthCalledWith(2, '功率', 'all', 0), {timeout: 2_000});
    expect(request).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/suppliers?q=%E5%8A%9F%E7%8E%87');
  });

  it('非法状态与页码被规范化为默认视图且只请求一次', async () => {
    const request = vi.spyOn(api, 'supplierPage').mockResolvedValue(makePage(0, 1, 1));

    renderView('/suppliers?status=bogus&page=0&unknown=1');

    expect(await screen.findByText('供应商 001')).toBeInTheDocument();
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/suppliers');
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith('', 'all', 0);
  });

  it('末页记录被删光后回退到仍然有数据的最后一页', async () => {
    const request = vi.spyOn(api, 'supplierPage')
      .mockResolvedValueOnce(makePage(40, 0, 25))
      .mockResolvedValueOnce(makePage(20, 5, 25));

    renderView('/suppliers?page=3');

    expect(await screen.findByText('供应商 021')).toBeInTheDocument();
    expect(request).toHaveBeenNthCalledWith(2, '', 'all', 20);
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/suppliers?page=2');
  });

  it('过期请求的响应不会覆盖当前筛选条件的数据', async () => {
    const user = userEvent.setup();
    let resolveFirst: ((value: SupplierListResponse) => void) | undefined;
    vi.spyOn(api, 'supplierPage')
      .mockImplementationOnce(() => new Promise<SupplierListResponse>((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({...makePage(0, 1, 1), items: [makeItem(9, {enabled: false})]});

    renderView('/suppliers');
    expect(await screen.findByRole('status')).toHaveTextContent('正在加载供应商…');

    await user.selectOptions(screen.getByLabelText('监控状态:'), 'paused');
    expect(await screen.findByText('供应商 009')).toBeInTheDocument();

    resolveFirst?.(makePage(0, 20, 25));

    await waitFor(() => expect(screen.getByText('显示 1-1，共 1 条')).toBeInTheDocument());
    expect(screen.queryByText('供应商 001')).not.toBeInTheDocument();
  });

  it('写操作后的刷新令牌触发当前页重查', async () => {
    const request = vi.spyOn(api, 'supplierPage').mockResolvedValue(makePage(0, 1, 1));
    const {rerender, ...handlers} = renderView('/suppliers?page=1', {refreshToken: 0});
    await screen.findByText('供应商 001');

    rerender(
      <MemoryRouter initialEntries={['/suppliers']}>
        <Routes>
          <Route path="/suppliers" element={<SuppliersView role="admin" refreshToken={1} {...handlers} />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  });

  it('只读账号看不到编辑入口且导入与启停被禁用', async () => {
    vi.spyOn(api, 'supplierPage').mockResolvedValue(makePage(0, 1, 1));

    renderView('/suppliers', {role: 'viewer'});

    expect(await screen.findByText('供应商 001')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '导入供应商'})).toBeDisabled();
    expect(screen.getByRole('button', {name: '暂停监控：供应商 001'})).toBeDisabled();
    expect(screen.queryByRole('button', {name: '编辑供应商：供应商 001'})).not.toBeInTheDocument();
  });

  it('管理员启停按钮回传当前行的供应商', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'supplierPage').mockResolvedValue({
      ...makePage(0, 1, 1),
      items: [makeItem(1, {enabled: false})],
    });
    const onToggleStatus = vi.fn();

    renderView('/suppliers', {onToggleStatus});
    await user.click(await screen.findByRole('button', {name: '恢复监控：供应商 001'}));

    expect(onToggleStatus).toHaveBeenCalledWith(expect.objectContaining({id: '1', monitoringStatus: 'paused'}));
  });

  it('把服务端当前风险等级映射为当前风险状态', async () => {
    vi.spyOn(api, 'supplierPage').mockResolvedValue({
      ...makePage(0, 1, 1),
      items: [makeItem(1, {current_risk_level: 'P3', current_risk_score: 55})],
    });

    renderView('/suppliers');

    expect(await screen.findByText('当前风险')).toBeInTheDocument();
  });

  it('权限错误上报会话边界且不提供重试', async () => {
    vi.spyOn(api, 'supplierPage').mockRejectedValue(new ApiError(403, '权限不足'));
    const onRequestError = vi.fn();

    renderView('/suppliers', {onRequestError});

    expect(await screen.findByRole('alert')).toHaveTextContent('无权查看供应商名录');
    expect(onRequestError).toHaveBeenCalledWith(expect.objectContaining({status: 403}));
    expect(screen.queryByRole('button', {name: '重试'})).not.toBeInTheDocument();
  });

  it('普通加载错误保留 URL 并可重试恢复', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'supplierPage')
      .mockRejectedValueOnce(new ApiError(500, '服务暂不可用'))
      .mockResolvedValueOnce(makePage(20, 5, 25));

    renderView('/suppliers?page=2');
    await user.click(await screen.findByRole('button', {name: '重试'}));

    expect(await screen.findByText('供应商 021')).toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/suppliers?page=2');
  });

  it('查询无结果时显示空态并保持总数为零', async () => {
    vi.spyOn(api, 'supplierPage').mockResolvedValue({items: [], total: 0, limit: 20, offset: 0});

    renderView('/suppliers?q=nothing');

    expect(await screen.findByText('未找到相关供应商数据')).toBeInTheDocument();
    expect(screen.getByText('显示 0-0，共 0 条')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '下一页'})).toBeDisabled();
  });
});

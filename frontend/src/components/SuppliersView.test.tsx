import {cleanup, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter, Route, Routes, useLocation} from 'react-router-dom';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {api, ApiError, type SupplierCollection, type SupplierListItem} from '../api';
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
  updated_at: '2026-09-01T00:00:00Z',
  aliases: [],
  sites: [],
  products: [{id: index, name: `产品 ${index}`, keywords: []}],
  current_risk_level: null,
  current_risk_score: null,
  ...overrides,
});

const makeItems = (start: number, count: number) => (
  Array.from({length: count}, (_, index) => makeItem(start + index))
);

const makeCollection = (count: number, total = count): SupplierCollection => ({
  items: makeItems(1, count),
  total,
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
    onOpenNewSupplierModal: vi.fn(),
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
  return {...handlers, container: view.container, rerender: view.rerender};
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('供应商单页全量清单', () => {
  it('从 URL 恢复查询词与监控状态并单页展示全部供应商', async () => {
    const request = vi.spyOn(api, 'supplierAll').mockResolvedValue(makeCollection(25));

    renderView('/suppliers?q=%E9%92%A2&status=paused');

    expect(await screen.findByText('供应商 021')).toBeInTheDocument();
    expect(screen.getByText('供应商 025')).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith('钢', 'paused');
    expect(screen.getByText('显示 1-25，共 25 条')).toBeInTheDocument();
  });

  it('不渲染任何翻页控件', async () => {
    vi.spyOn(api, 'supplierAll').mockResolvedValue(makeCollection(25));

    renderView('/suppliers');

    expect(await screen.findByText('供应商 025')).toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '上一页'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '下一页'})).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', {name: '供应商分页'})).not.toBeInTheDocument();
    expect(screen.queryByText(/第 \d+ 页/)).not.toBeInTheDocument();
  });

  it('旧 URL 的 page 参数被规范化移除且不限制数据', async () => {
    const request = vi.spyOn(api, 'supplierAll').mockResolvedValue(makeCollection(25));

    renderView('/suppliers?status=paused&page=3');

    expect(await screen.findByText('供应商 025')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('当前地址')).toHaveTextContent('/suppliers?status=paused'));
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith('', 'paused');
  });

  it('切换监控状态时按状态语义重新加载并写入 URL', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'supplierAll')
      .mockResolvedValueOnce(makeCollection(25))
      .mockResolvedValueOnce(makeCollection(2));

    renderView('/suppliers');
    await screen.findByText('供应商 025');

    await user.selectOptions(screen.getByLabelText('监控状态:'), 'high_risk');

    await waitFor(() => expect(request).toHaveBeenNthCalledWith(2, '', 'high_risk'));
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/suppliers?status=high_risk');
  });

  it('搜索输入防抖后写入查询词并只重新加载一次', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'supplierAll')
      .mockResolvedValueOnce(makeCollection(25))
      .mockResolvedValueOnce(makeCollection(1));

    renderView('/suppliers');
    await screen.findByText('供应商 025');

    await user.type(screen.getByLabelText('搜索供应商'), '功率');

    // 键入过程中只允许在防抖结束后发出一次请求，避免每个字符都打一次服务端。
    await waitFor(() => expect(request).toHaveBeenNthCalledWith(2, '功率', 'all'), {timeout: 2_000});
    expect(request).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/suppliers?q=%E5%8A%9F%E7%8E%87');
  });

  it('非法状态被规范化为默认视图且只请求一次', async () => {
    const request = vi.spyOn(api, 'supplierAll').mockResolvedValue(makeCollection(1));

    renderView('/suppliers?status=bogus&unknown=1');

    expect(await screen.findByText('供应商 001')).toBeInTheDocument();
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/suppliers');
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith('', 'all');
  });

  it('过期请求的响应不会覆盖当前筛选条件的数据', async () => {
    const user = userEvent.setup();
    let resolveFirst: ((value: SupplierCollection) => void) | undefined;
    vi.spyOn(api, 'supplierAll')
      .mockImplementationOnce(() => new Promise<SupplierCollection>((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({items: [makeItem(9, {enabled: false})], total: 1});

    const {container} = renderView('/suppliers');
    expect(await screen.findByRole('status')).toHaveTextContent('正在加载供应商…');
    expect(container.querySelector('.animate-spin')).toBeNull();

    await user.selectOptions(screen.getByLabelText('监控状态:'), 'paused');
    expect(await screen.findByText('供应商 009')).toBeInTheDocument();

    resolveFirst?.(makeCollection(20));

    await waitFor(() => expect(screen.getByText('显示 1-1，共 1 条')).toBeInTheDocument());
    expect(screen.queryByText('供应商 001')).not.toBeInTheDocument();
  });

  it('写操作后的刷新令牌触发重新全量加载', async () => {
    const request = vi.spyOn(api, 'supplierAll').mockResolvedValue(makeCollection(1));
    const {rerender, ...handlers} = renderView('/suppliers', {refreshToken: 0});
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

  it('只读账号看不到编辑入口且新增、导入与启停被禁用', async () => {
    vi.spyOn(api, 'supplierAll').mockResolvedValue(makeCollection(1));

    renderView('/suppliers', {role: 'viewer'});

    expect(await screen.findByText('供应商 001')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '新增供应商'})).toBeDisabled();
    expect(screen.getByRole('button', {name: '导入供应商'})).toBeDisabled();
    expect(screen.getByRole('button', {name: '暂停监控：供应商 001'})).toBeDisabled();
    expect(screen.queryByRole('button', {name: '编辑供应商：供应商 001'})).not.toBeInTheDocument();
  });

  it('管理员的单条新增与 Excel 导入使用独立入口', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'supplierAll').mockResolvedValue(makeCollection(1));
    const handlers = renderView('/suppliers');
    await screen.findByText('供应商 001');

    await user.click(screen.getByRole('button', {name: '新增供应商'}));
    await user.click(screen.getByRole('button', {name: '导入供应商'}));

    expect(handlers.onOpenNewSupplierModal).toHaveBeenCalledTimes(1);
    expect(handlers.onOpenImportModal).toHaveBeenCalledTimes(1);
  });

  it('管理员启停按钮回传当前行的供应商', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'supplierAll').mockResolvedValue({
      items: [makeItem(1, {enabled: false})],
      total: 1,
    });
    const onToggleStatus = vi.fn();

    renderView('/suppliers', {onToggleStatus});
    await user.click(await screen.findByRole('button', {name: '恢复监控：供应商 001'}));

    expect(onToggleStatus).toHaveBeenCalledWith(expect.objectContaining({id: '1', monitoringStatus: 'paused'}));
  });

  it('把服务端当前风险等级映射为当前风险状态', async () => {
    vi.spyOn(api, 'supplierAll').mockResolvedValue({
      items: [makeItem(1, {current_risk_level: 'P3', current_risk_score: 55})],
      total: 1,
    });

    renderView('/suppliers');

    expect(await screen.findByText('当前风险')).toBeInTheDocument();
  });

  it('权限错误上报会话边界且不提供重试', async () => {
    vi.spyOn(api, 'supplierAll').mockRejectedValue(new ApiError(403, '权限不足'));
    const onRequestError = vi.fn();

    renderView('/suppliers', {onRequestError});

    expect(await screen.findByRole('alert')).toHaveTextContent('无权查看供应商名录');
    expect(onRequestError).toHaveBeenCalledWith(expect.objectContaining({status: 403}));
    expect(screen.queryByRole('button', {name: '重试'})).not.toBeInTheDocument();
  });

  it('普通加载错误保留 URL 并可重试恢复', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'supplierAll')
      .mockRejectedValueOnce(new ApiError(500, '服务暂不可用'))
      .mockResolvedValueOnce(makeCollection(25));

    renderView('/suppliers?q=%E9%92%A2');
    await user.click(await screen.findByRole('button', {name: '重试'}));

    expect(await screen.findByText('供应商 025')).toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/suppliers?q=%E9%92%A2');
  });

  it('查询无结果时显示空态并保持总数为零', async () => {
    vi.spyOn(api, 'supplierAll').mockResolvedValue({items: [], total: 0});

    renderView('/suppliers?q=nothing');

    expect(await screen.findByText('未找到相关供应商数据')).toBeInTheDocument();
    expect(screen.getByText('显示 0-0，共 0 条')).toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '下一页'})).not.toBeInTheDocument();
  });
});

import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter, Route, Routes, useLocation} from 'react-router-dom';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {api, ApiError, type SourceSignalDetailRead, type SourceSignalListResponse, type ValidityAnchorSource, type ValidityReasonRead} from '../api';
import {SourceSignalsView} from './SourceSignalsView';

const PAGE_SIZE = 100;
const longContent = '风险详情'.repeat(80);
const longSummary = '重点命中：风险总览：开庭公告 3 条，法院公告 2 条，失信被执行 1 条，行政处罚 1 条，'.repeat(5);

const emptyResponse: SourceSignalListResponse = {
  source: {id: 17, code: 'OFFICIAL-17', name: '官方风险源', signal_validity_days: 30, validity_policy: {mode: 'fixed_days', fixed_days: 30}},
  items: [],
  total: 0,
  offset: 0,
  limit: PAGE_SIZE,
};

const populatedResponse: SourceSignalListResponse = {
  ...emptyResponse,
  items: [
    {
      id: 91,
      external_id: 'EXT-91',
      title: '供应链运输中断',
      content: longContent,
      url: 'https://official.example/events/91',
      published_at: '2026-08-31T08:00:00Z',
      collected_at: '2026-09-01T08:00:00Z',
      validity_profile: 'transport_disruption',
      validity_state: 'active',
      valid_from: '2026-08-31T08:00:00Z',
      valid_until: '2026-09-30T08:00:00Z',
      validity_mode: 'fixed_days',
      validity_key: null,
      lifecycle_action: 'assert',
      validity_policy_version: 'v1',
      review_due_at: '2026-09-15T08:00:00Z',
      validity_reason: {code: 'active', anchor_source: 'published_at', details: {}},
      summary: '重点命中：风险总览：开庭公告 1 条，法院公告 1 条',
    },
    {
      id: 92,
      external_id: 'EXT-92',
      title: '无摘要记录',
      content: '短正文',
      url: null,
      published_at: null,
      collected_at: '2026-09-01T09:00:00Z',
      validity_profile: null,
      validity_state: 'expired',
      valid_from: null,
      valid_until: null,
      review_due_at: null,
      validity_mode: null,
      validity_key: null,
      lifecycle_action: 'assert',
      validity_policy_version: null,
      validity_reason: {code: 'deadline_reached', anchor_source: 'collected_at', details: {}},
      summary: '',
    },
  ],
  total: 250,
  offset: PAGE_SIZE,
};

/** 天眼查是唯一带完整报告列的信息源，用于验证报告列的条件渲染。 */
const tianyanchaResponse: SourceSignalListResponse = {
  ...populatedResponse,
  source: {...emptyResponse.source, id: 18, code: 'tianyancha', name: '天眼查企业核查'},
  items: [populatedResponse.items[0]!],
  total: 1,
  offset: 0,
};

/** 预筛判定为不相关的终态记录：状态标签比「有效」更长，用于验证状态列收到最小宽度时同样不换行不溢出。 */
const irrelevantSignalResponse: SourceSignalListResponse = {
  ...tianyanchaResponse,
  items: [
    populatedResponse.items[0]!,
    {
      ...populatedResponse.items[0]!,
      id: 93,
      external_id: 'EXT-93',
      title: '预筛判定不相关的记录',
      validity_state: 'irrelevant',
      validity_reason: {code: 'filtered_out', anchor_source: 'published_at', details: {}},
    },
  ],
  total: 2,
};

const detailFixture: SourceSignalDetailRead = {
  ...populatedResponse.items[0]!,
  report: null,
  report_truncated: false,
};

// 与 SourceSignalsTable 同源的 Intl 格式化：断言时间数据流，而不是硬编码某台机器的时区字符串。
const formatDateTime = (value: string | null) => (value ? new Date(value).toLocaleString('zh-CN', {hour12: false}) : '未提供');

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

/** jsdom 不实现 Pointer Capture：临时装上可观测的桩，用于断言拖拽期间的捕获与释放。 */
const stubPointerCapture = () => {
  const descriptors = ['setPointerCapture', 'hasPointerCapture', 'releasePointerCapture']
    .map((name) => [name, Object.getOwnPropertyDescriptor(Element.prototype, name)] as const);
  const captured: number[] = [];
  const released: number[] = [];
  const define = (name: string, value: unknown) => Object.defineProperty(Element.prototype, name, {configurable: true, value});
  define('setPointerCapture', vi.fn((pointerId: number) => { captured.push(pointerId); }));
  define('hasPointerCapture', vi.fn((pointerId: number) => captured.includes(pointerId)));
  define('releasePointerCapture', vi.fn((pointerId: number) => { released.push(pointerId); }));
  return {
    captured,
    released,
    restore: () => descriptors.forEach(([name, descriptor]) => {
      if (descriptor === undefined) Reflect.deleteProperty(Element.prototype, name);
      else Object.defineProperty(Element.prototype, name, descriptor);
    }),
  };
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** 只渲染一条记录并返回「有效期原因」文案，供参数化断言复用。 */
const renderValidityReason = async (validityReason: ValidityReasonRead): Promise<string> => {
  vi.spyOn(api, 'sourceSignals').mockResolvedValue({
    ...populatedResponse,
    items: [{...populatedResponse.items[0]!, validity_reason: validityReason}],
    total: 1,
    offset: 0,
  });
  renderView('/sources/17/signals?scope=all&page=1');
  return (await screen.findByTestId('source-signal-reason-91')).textContent ?? '';
};

describe('信息源采集记录清单', () => {
  it('将缺失查询参数规范化为当前有效第一页', async () => {
    const request = vi.spyOn(api, 'sourceSignals').mockResolvedValue(emptyResponse);

    renderView('/sources/17/signals');

    expect(await screen.findByText('暂无当前有效记录')).toBeInTheDocument();
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/sources/17/signals?scope=valid&page=1');
    expect(request).toHaveBeenCalledWith(17, 'valid', 0, PAGE_SIZE);
  });

  it('每页请求 100 条，翻页偏移量为 (页码-1)×100', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'sourceSignals')
      .mockResolvedValueOnce(populatedResponse)
      .mockResolvedValue({...populatedResponse, offset: 200});

    renderView('/sources/17/signals?scope=all&page=2');
    await screen.findByText('供应链运输中断');
    expect(request).toHaveBeenNthCalledWith(1, 17, 'all', PAGE_SIZE, PAGE_SIZE);

    await user.click(screen.getByRole('button', {name: '下一页'}));

    await waitFor(() => expect(request).toHaveBeenNthCalledWith(2, 17, 'all', 200, PAGE_SIZE));
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('scope=all&page=3');
  });

  it('以语义表格呈现采集记录：四个时间字段独立成列、横向滚动容器与不换行省略均具备', async () => {
    vi.spyOn(api, 'sourceSignals').mockResolvedValue(populatedResponse);

    renderView('/sources/17/signals?scope=all&page=2');
    await screen.findByText('供应链运输中断');

    const table = screen.getByRole('table', {name: '采集记录'});
    expect(table).toHaveStyle({tableLayout: 'fixed'});
    expect(table.parentElement).toHaveClass('overflow-x-auto');
    const headers = within(table).getAllByRole('columnheader');
    expect(headers.map((header) => header.textContent)).toEqual([
      '序号', '标题', '状态', '发布时间', '采集时间', '有效期截止', '复核时间', '外部编号', '有效期原因', '摘要 / 内容', '原文',
    ]);
    headers.forEach((header) => expect(within(header).getByText(header.textContent ?? '')).toHaveClass('truncate'));
    ['发布时间', '采集时间', '有效期截止', '复核时间'].forEach((label) => {
      expect(screen.getByRole('columnheader', {name: label})).toHaveStyle({width: '170px'});
    });
    const summaryCell = screen.getByTestId('source-signal-summary-91');
    expect(within(summaryCell).getByRole('button')).toHaveClass('truncate');
  });

  it('紧凑表格只呈现既有信息：不显示策略版本', async () => {
    vi.spyOn(api, 'sourceSignals').mockResolvedValue(populatedResponse);

    renderView('/sources/17/signals?scope=all&page=2');
    await screen.findByText('供应链运输中断');

    expect(screen.queryByText('策略版本')).not.toBeInTheDocument();
    expect(screen.queryByText('v1')).not.toBeInTheDocument();
    expect(screen.queryByText('未生成')).not.toBeInTheDocument();
    expect(screen.getByTestId('source-signal-reason-91')).toHaveTextContent('有效（以发布时间为依据）');
  });

  it('按页读取全部历史：主列表只渲染后端受控摘要，不渲染完整长文', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'sourceSignals')
      .mockResolvedValueOnce(populatedResponse)
      .mockResolvedValueOnce({...populatedResponse, offset: 0});

    renderView('/sources/17/signals?scope=all&page=2');

    expect(await screen.findByText('供应链运输中断')).toBeInTheDocument();
    expect(screen.getByText('第 2 页')).toBeInTheDocument();
    expect(screen.getByRole('link', {name: '原文'})).toHaveAttribute('href', 'https://official.example/events/91');

    // 主列表只渲染后端 summary：完整 content 不出现在页面中，摘要为纯文本单行
    const summaryCell = screen.getByTestId('source-signal-summary-91');
    expect(summaryCell).toHaveTextContent('重点命中：风险总览：开庭公告 1 条，法院公告 1 条');
    expect(summaryCell.querySelectorAll('[data-testid="summary-protected-phrase"]')).toHaveLength(0);
    expect(screen.queryByText(longContent)).not.toBeInTheDocument();
    // 空摘要给稳定占位
    expect(screen.getByTestId('source-signal-summary-92')).toHaveTextContent('暂无摘要');

    // 点击「上一页」重新加载列表：偏移量回到 0，页码回到 1
    await user.click(screen.getByRole('button', {name: '上一页'}));
    await waitFor(() => expect(request).toHaveBeenNthCalledWith(2, 17, 'all', 0, PAGE_SIZE));
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('scope=all&page=1');
  });

  it('非天眼查信息源不显示报告列', async () => {
    vi.spyOn(api, 'sourceSignals').mockResolvedValue(populatedResponse);
    const detail = vi.spyOn(api, 'sourceSignalDetail').mockResolvedValue(detailFixture);

    renderView('/sources/17/signals?scope=all&page=2');
    await screen.findByText('供应链运输中断');

    const table = screen.getByRole('table', {name: '采集记录'});
    expect(within(table).queryByRole('columnheader', {name: '报告'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: /查看完整报告/})).not.toBeInTheDocument();
    expect(detail).not.toHaveBeenCalled();
  });

  it('天眼查信息源显示报告列并复用完整报告弹窗', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'sourceSignals').mockResolvedValue(tianyanchaResponse);
    vi.spyOn(api, 'sourceSignalDetail').mockResolvedValue(detailFixture);

    renderView('/sources/18/signals?scope=all&page=1');
    await screen.findByText('供应链运输中断');

    const table = screen.getByRole('table', {name: '采集记录'});
    expect(within(table).getByRole('columnheader', {name: '报告'})).toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: '查看完整报告：供应链运输中断'}));

    expect(api.sourceSignalDetail).toHaveBeenCalledWith(18, 91, expect.anything());
    expect(await screen.findByRole('dialog')).toHaveAttribute('aria-modal', 'true');
  });

  it('切换到全部历史时重置到第一页', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'sourceSignals')
      .mockResolvedValueOnce({...emptyResponse, offset: PAGE_SIZE})
      .mockResolvedValueOnce(emptyResponse);

    renderView('/sources/17/signals?scope=valid&page=2');
    await screen.findByText('暂无当前有效记录');
    await user.click(screen.getByRole('tab', {name: '全部历史'}));

    await waitFor(() => expect(request).toHaveBeenNthCalledWith(2, 17, 'all', 0, PAGE_SIZE));
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

describe('采集记录单元格完整内容浮层', () => {
  const renderWithLongSummary = async () => {
    vi.spyOn(api, 'sourceSignals').mockResolvedValue({
      ...populatedResponse,
      items: [{...populatedResponse.items[0]!, summary: longSummary}],
      total: 1,
      offset: 0,
    });
    renderView('/sources/17/signals?scope=all&page=1');
    await screen.findByText('供应链运输中断');
  };

  it('点击被省略的摘要文本在锚定浮层显示可换行的完整内容', async () => {
    const user = userEvent.setup();
    await renderWithLongSummary();

    const trigger = within(screen.getByTestId('source-signal-summary-91')).getByRole('button');
    await user.click(trigger);

    const overlay = await screen.findByRole('dialog', {name: '摘要 / 内容'});
    expect(overlay).toHaveTextContent(longSummary);
    // 浮层保留语义换行映射，长文本不会撑破浮层宽度
    expect(within(overlay).getAllByTestId('summary-protected-phrase').length).toBeGreaterThan(0);
    expect(overlay.querySelector('.whitespace-pre-wrap')).not.toBeNull();
  });

  it('Esc 关闭浮层并把焦点交还触发单元格', async () => {
    const user = userEvent.setup();
    await renderWithLongSummary();

    const trigger = within(screen.getByTestId('source-signal-summary-91')).getByRole('button');
    await user.click(trigger);
    await screen.findByRole('dialog', {name: '摘要 / 内容'});

    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it('点击浮层外部关闭浮层，点击浮层内部不关闭', async () => {
    const user = userEvent.setup();
    await renderWithLongSummary();

    const trigger = within(screen.getByTestId('source-signal-summary-91')).getByRole('button');
    await user.click(trigger);
    const overlay = await screen.findByRole('dialog', {name: '摘要 / 内容'});

    await user.click(within(overlay).getByText('重点命中：'));
    expect(screen.getByRole('dialog', {name: '摘要 / 内容'})).toBeInTheDocument();

    await user.click(document.body);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('四个时间字段各列显示各自的值：发布时间不回退采集时间，缺失一律显示未提供', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'sourceSignals').mockResolvedValue({
      ...populatedResponse,
      items: [populatedResponse.items[0]!, populatedResponse.items[1]!],
      total: 2,
      offset: 0,
    });
    renderView('/sources/17/signals?scope=all&page=1');
    await screen.findByText('供应链运输中断');

    const first = populatedResponse.items[0]!;
    const firstRow: ReadonlyArray<readonly [string, string]> = [
      ['发布时间', formatDateTime(first.published_at)],
      ['采集时间', formatDateTime(first.collected_at)],
      ['有效期截止', formatDateTime(first.valid_until)],
      ['复核时间', formatDateTime(first.review_due_at)],
    ];
    firstRow.forEach(([label, value]) => expect(screen.getAllByRole('button', {name: label})[0]).toHaveTextContent(value));

    // 第二条记录的发布时间/有效期截止/复核时间缺失：显示未提供，绝不回退成采集时间
    const second = populatedResponse.items[1]!;
    ['发布时间', '有效期截止', '复核时间'].forEach((label) => {
      expect(screen.getAllByRole('button', {name: label})[1]).toHaveTextContent('未提供');
    });
    expect(screen.getAllByRole('button', {name: '采集时间'})[1]).toHaveTextContent(formatDateTime(second.collected_at));

    // 点击某一列只展示该列自己的完整值
    await user.click(screen.getAllByRole('button', {name: '复核时间'})[0]);
    const overlay = await screen.findByRole('dialog', {name: '复核时间'});
    expect(overlay).toHaveTextContent(formatDateTime(first.review_due_at));
    expect(overlay).not.toHaveTextContent(formatDateTime(first.published_at));
    expect(overlay).not.toHaveTextContent(formatDateTime(first.valid_until));
  });

  it('状态、原文与报告单元格收到最小列宽时也不换行、不溢出', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'sourceSignals').mockResolvedValue(irrelevantSignalResponse);
    renderView('/sources/18/signals?scope=all&page=1');
    await screen.findByText('供应链运输中断');

    const singleLine = (element: HTMLElement | null) => expect(element).toHaveClass('truncate');
    singleLine(within(screen.getAllByRole('row')[1]!).getByText('有效').parentElement);
    singleLine(within(screen.getAllByRole('row')[1]!).getByRole('link', {name: '原文'}).parentElement);
    singleLine(within(screen.getAllByRole('row')[1]!).getByRole('button', {name: /查看完整报告/}).parentElement);
    // 「不相关」标签比「有效」多一个字，同样受 truncate 约束，不换行不溢出
    singleLine(within(screen.getAllByRole('row')[2]!).getByText('不相关').parentElement);

    const statusHandle = screen.getByRole('separator', {name: '调整「状态」列宽'});
    statusHandle.focus();
    for (let step = 0; step < 2; step += 1) await user.keyboard('{ArrowLeft}');

    await waitFor(() => expect(statusHandle).toHaveAttribute('aria-valuenow', '80'));
    singleLine(within(screen.getAllByRole('row')[1]!).getByText('有效').parentElement);
    singleLine(within(screen.getAllByRole('row')[2]!).getByText('不相关').parentElement);
  });
});

describe('采集记录表格列宽调整', () => {
  const renderTable = async () => {
    vi.spyOn(api, 'sourceSignals').mockResolvedValue(populatedResponse);
    renderView('/sources/17/signals?scope=all&page=1');
    await screen.findByText('供应链运输中断');
  };

  it('键盘左右键按 16px 步进调整列宽并同步表头宽度', async () => {
    const user = userEvent.setup();
    await renderTable();

    const handle = screen.getByRole('separator', {name: '调整「标题」列宽'});
    const header = screen.getByRole('columnheader', {name: '标题'});
    expect(handle).toHaveAttribute('aria-valuenow', '280');
    expect(header).toHaveStyle({width: '280px'});

    handle.focus();
    await user.keyboard('{ArrowRight}');

    await waitFor(() => expect(handle).toHaveAttribute('aria-valuenow', '296'));
    expect(header).toHaveStyle({width: '296px'});

    await user.keyboard('{ArrowLeft}{ArrowLeft}');

    await waitFor(() => expect(handle).toHaveAttribute('aria-valuenow', '264'));
    expect(header).toHaveStyle({width: '264px'});
  });

  it('列宽不会被键盘调整到 80px 以下', async () => {
    const user = userEvent.setup();
    await renderTable();

    const handle = screen.getByRole('separator', {name: '调整「标题」列宽'});

    handle.focus();
    for (let step = 0; step < 30; step += 1) await user.keyboard('{ArrowLeft}');

    await waitFor(() => expect(handle).toHaveAttribute('aria-valuenow', '80'));
  });

  it('拖拽列把手调整列宽，禁用触控滚动，结束时释放指针捕获且不弹出内容浮层', async () => {
    const pointer = stubPointerCapture();
    try {
      await renderTable();

      const handle = screen.getByRole('separator', {name: '调整「摘要 / 内容」列宽'});
      const header = screen.getByRole('columnheader', {name: '摘要 / 内容'});
      expect(header).toHaveStyle({width: '360px'});
      expect(handle).toHaveClass('touch-none');

      fireEvent.pointerDown(handle, {button: 0, pointerId: 7, clientX: 300});
      expect(pointer.captured).toContain(7);

      fireEvent.pointerMove(handle, {pointerId: 7, clientX: 360});

      await waitFor(() => expect(header).toHaveStyle({width: '420px'}));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

      fireEvent.pointerUp(handle, {pointerId: 7});

      expect(pointer.released).toContain(7);
    } finally {
      pointer.restore();
    }
  });

  it.each<{event: 'pointerCancel' | 'lostPointerCapture'; columns: string; startX: number; movedX: number; expected: string}>([
    {event: 'pointerCancel', columns: '外部编号', startX: 100, movedX: 160, expected: '280px'},
    {event: 'lostPointerCapture', columns: '有效期原因', startX: 500, movedX: 560, expected: '340px'},
  ])('指针拖拽被 $event 中断时同样结束拖拽并保留当前列宽', async ({event, columns, startX, movedX, expected}) => {
    const pointer = stubPointerCapture();
    try {
      await renderTable();

      const handle = screen.getByRole('separator', {name: `调整「${columns}」列宽`});
      const header = screen.getByRole('columnheader', {name: columns});

      fireEvent.pointerDown(handle, {button: 0, pointerId: 9, clientX: startX});
      fireEvent.pointerMove(handle, {pointerId: 9, clientX: movedX});
      fireEvent[event](handle, {pointerId: 9});

      expect(pointer.released).toContain(9);
      expect(header).toHaveStyle({width: expected});

      // 捕获已结束，后续移动不再改变列宽
      fireEvent.pointerMove(handle, {pointerId: 9, clientX: movedX + 200});
      expect(header).toHaveStyle({width: expected});
    } finally {
      pointer.restore();
    }
  });
});

describe('采集记录序号列与整行选中', () => {
  /** 表格数据行（不含表头行），每次重新查询以适配翻页后的重新挂载。 */
  const dataRows = () => within(screen.getByRole('table', {name: '采集记录'})).getAllByRole('row').slice(1);
  const serialNumbers = () => dataRows().map((row) => within(row).getAllByRole('cell')[0]!.textContent);
  const selectedRows = () => dataRows().filter((row) => row.getAttribute('data-selected') === 'true');

  it('序号按后端 offset 与行序连续编号：第一页 1 起、第二页 101 起，且不对页内记录自行排序', async () => {
    vi.spyOn(api, 'sourceSignals').mockResolvedValue(populatedResponse);

    renderView('/sources/17/signals?scope=all&page=2');
    await screen.findByText('供应链运输中断');

    expect(screen.getByRole('columnheader', {name: '序号'})).toHaveStyle({width: '80px'});
    expect(serialNumbers()).toEqual(['101', '102']);
    // 采集时间更晚的第二条记录仍排在原位：序号只跟随后端返回顺序，不在前端按时间重排。
    expect(within(dataRows()[0]!).getByRole('button', {name: '标题'})).toHaveTextContent('供应链运输中断');
  });

  it('切换记录范围后重新从 1 编号', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api, 'sourceSignals')
      .mockResolvedValueOnce(populatedResponse)
      .mockResolvedValueOnce({...populatedResponse, offset: 0});

    renderView('/sources/17/signals?scope=valid&page=2');
    await screen.findByText('供应链运输中断');
    expect(serialNumbers()).toEqual(['101', '102']);

    await user.click(screen.getByRole('tab', {name: '全部历史'}));

    await waitFor(() => expect(request).toHaveBeenNthCalledWith(2, 17, 'all', 0, PAGE_SIZE));
    expect(serialNumbers()).toEqual(['1', '2']);
  });

  it('序号列宽可用列把手按键盘步进调整', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'sourceSignals').mockResolvedValue({...populatedResponse, offset: 0});

    renderView('/sources/17/signals?scope=all&page=1');
    await screen.findByText('供应链运输中断');

    const handle = screen.getByRole('separator', {name: '调整「序号」列宽'});
    handle.focus();
    await user.keyboard('{ArrowRight}');

    await waitFor(() => expect(handle).toHaveAttribute('aria-valuenow', '96'));
    expect(screen.getByRole('columnheader', {name: '序号'})).toHaveStyle({width: '96px'});
  });

  it('点击普通单元格选中整行，点击另一行切换且恒为单选', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'sourceSignals').mockResolvedValue({...populatedResponse, offset: 0});

    renderView('/sources/17/signals?scope=all&page=1');
    await screen.findByText('供应链运输中断');

    const rows = dataRows();
    expect(selectedRows()).toHaveLength(0);
    // 未选中行是浅蓝悬浮态；选中后换成更明显的一档，并停止被悬浮色覆盖。
    expect(rows[0]).toHaveClass('hover:bg-[#185fa5]/5', 'dark:hover:bg-slate-700/40');

    await user.click(within(rows[0]!).getByText('有效'));

    expect(rows[0]).toHaveAttribute('data-selected', 'true');
    expect(rows[0]).toHaveClass('bg-blue-100/70', 'dark:bg-blue-900/50');
    expect(rows[0]).not.toHaveClass('hover:bg-[#185fa5]/5');
    expect(rows[1]).toHaveAttribute('data-selected', 'false');

    await user.click(within(rows[1]!).getByRole('button', {name: '有效期原因'}));

    expect(rows[1]).toHaveAttribute('data-selected', 'true');
    expect(rows[0]).toHaveAttribute('data-selected', 'false');
    expect(selectedRows()).toHaveLength(1);
  });

  it('点击文本浮层按钮与报告按钮同样选中该行，浮层与完整报告照常打开', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'sourceSignals').mockResolvedValue(tianyanchaResponse);
    vi.spyOn(api, 'sourceSignalDetail').mockResolvedValue(detailFixture);

    renderView('/sources/18/signals?scope=all&page=1');
    await screen.findByText('供应链运输中断');
    const row = dataRows()[0]!;

    await user.click(within(screen.getByTestId('source-signal-summary-91')).getByRole('button'));

    expect(row).toHaveAttribute('data-selected', 'true');
    expect(await screen.findByRole('dialog', {name: '摘要 / 内容'})).toHaveTextContent('重点命中');

    await user.click(screen.getByRole('button', {name: /查看完整报告/}));

    expect(row).toHaveAttribute('data-selected', 'true');
    expect(api.sourceSignalDetail).toHaveBeenCalledWith(18, 91, expect.anything());
    expect(await screen.findByRole('dialog')).toHaveAttribute('aria-modal', 'true');
  });

  it('点击原文外链选中该行，且链接仍指向真实来源', async () => {
    vi.spyOn(api, 'sourceSignals').mockResolvedValue({...populatedResponse, offset: 0});

    renderView('/sources/17/signals?scope=all&page=1');
    await screen.findByText('供应链运输中断');
    const row = dataRows()[0]!;
    const link = within(row).getByRole('link', {name: '原文'});

    fireEvent.click(link);

    expect(row).toHaveAttribute('data-selected', 'true');
    expect(link).toHaveAttribute('href', 'https://official.example/events/91');
  });

  it('翻页后清除上一页的选中状态', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'sourceSignals')
      .mockResolvedValueOnce({...populatedResponse, offset: 0})
      .mockResolvedValueOnce({...populatedResponse, offset: PAGE_SIZE});

    renderView('/sources/17/signals?scope=all&page=1');
    await screen.findByText('供应链运输中断');
    await user.click(within(dataRows()[0]!).getByText('有效'));
    expect(selectedRows()).toHaveLength(1);

    await user.click(screen.getByRole('button', {name: '下一页'}));

    await waitFor(() => expect(screen.getByLabelText('当前地址')).toHaveTextContent('scope=all&page=2'));
    expect(screen.getByText('第 2 页')).toBeInTheDocument();
    expect(selectedRows()).toHaveLength(0);
  });
});

describe('采集记录有效期原因显示', () => {
  it('策略计算结果用业务语言说明所依据的时间', async () => {
    expect(await renderValidityReason({code: 'policy_resolved', anchor_source: 'published_at', details: {}}))
      .toBe('已按有效期规则计算（以发布时间为依据）');
  });

  it.each<{anchorSource: ValidityAnchorSource; expected: string}>([
    {anchorSource: 'published_at', expected: '发布时间'},
    {anchorSource: 'collected_at', expected: '采集时间'},
    {anchorSource: 'official_valid_until', expected: '官方有效期截止时间'},
    {anchorSource: 'event_end', expected: '事件结束时间'},
  ])('时间依据 $anchorSource 显示为「以$expected为依据」', async ({anchorSource, expected}) => {
    expect(await renderValidityReason({code: 'deadline_reached', anchor_source: anchorSource, details: {}}))
      .toBe(`已到有效期截止（以${expected}为依据）`);
  });

  it('发布时间缺失的降级原因自带计算依据且不重复追加', async () => {
    expect(await renderValidityReason({code: 'anchor_fallback', anchor_source: 'collected_at', details: {}}))
      .toBe('未提供发布时间，改用采集时间计算');
  });

  it('旧版兼容记录只显示原因、不显示时间依据', async () => {
    expect(await renderValidityReason({code: 'active', anchor_source: 'legacy', details: {}})).toBe('有效');
  });

  it('预筛不相关原因显示为中文，不露出英文原因码', async () => {
    expect(await renderValidityReason({code: 'filtered_out', anchor_source: 'collected_at', details: {}}))
      .toBe('预筛不相关（以采集时间为依据）');
  });

  it('未知原因保留后端原文并照常标注时间依据', async () => {
    expect(await renderValidityReason({code: 'brand_new_reason', anchor_source: 'published_at', details: {}}))
      .toBe('brand_new_reason（以发布时间为依据）');
  });
});
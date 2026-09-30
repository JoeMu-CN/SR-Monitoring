import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useState} from 'react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {
  api,
  ApiError,
  type SourceSignalDetailRead,
  type TycRiskReport,
} from '../api';
import {SourceSignalReportModal} from './SourceSignalReportModal';

const reportFixture: TycRiskReport = {
  report_kind: 'supplier_profile',
  company_name: '宁波鸿腾精密制造有限公司',
  supplier_code: 'SUP-0001',
  credit_code: '913302127995394959',
  reg_status: '存续',
  generated_at: '2026-09-29T04:30:00Z',
  period_key: 'tyc:SUP-0001:2026-W40',
  dimensions: [
    {
      key: 'get_risk_overview', name: '风险总览', status: 'success', risk_level: '警示',
      hit: true, summary: '开庭公告 1 条，法院公告 1 条',
      evidence_refs: ['开庭公告', '法院公告'], raw_ref: 'dimensions.get_risk_overview.raw',
    },
    {
      key: 'get_judicial_case', name: '司法解析', status: 'success', risk_level: null,
      hit: false, summary: '未发现司法解析记录', evidence_refs: [],
      raw_ref: 'dimensions.get_judicial_case.raw',
    },
    {
      key: 'get_random_check', name: '双随机抽查', status: 'empty', risk_level: null,
      hit: false, summary: '未发现双随机抽查记录', evidence_refs: [], raw_ref: null,
    },
    {
      key: 'get_court_notice', name: '法院公告', status: 'error', risk_level: null,
      hit: false, summary: '查询失败：上游超时', evidence_refs: [], raw_ref: null,
    },
    {
      key: 'get_administrative_license', name: '行政许可', status: 'quota_exhausted',
      risk_level: null, hit: false, summary: '额度耗尽，未执行查询', evidence_refs: [], raw_ref: null,
    },
    {
      key: 'get_change_records', name: '历史变更记录', status: 'busy', risk_level: null,
      hit: false, summary: '查询繁忙，未执行查询', evidence_refs: [], raw_ref: null,
    },
  ],
};

const longContent = `该记录完整正文。${'细节内容。'.repeat(40)}`;

const detailWithReport: SourceSignalDetailRead = {
  id: 91,
  external_id: 'tyc-SUP-0001-2026-W40',
  title: '天眼查多维度核查：宁波鸿腾精密制造有限公司',
  content: longContent,
  url: null,
  published_at: '2026-09-29T04:30:00Z',
  collected_at: '2026-09-29T04:31:00Z',
  validity_profile: null,
  validity_state: 'active',
  valid_from: '2026-09-29T04:30:00Z',
  valid_until: null,
  review_due_at: null,
  validity_mode: 'until_superseded',
  validity_key: 'tyc:SUP-0001',
  lifecycle_action: 'assert',
  validity_policy_version: 'v1',
  validity_reason: {code: 'active', anchor_source: 'published_at', details: {}},
  summary: '重点命中：风险总览：开庭公告 1 条，法院公告 1 条',
  report: reportFixture,
  report_truncated: false,
};

const detailWithoutReport: SourceSignalDetailRead = {
  ...detailWithReport,
  id: 92,
  title: 'E2E Source Signal 01',
  external_id: 'E2E-SOURCE-SIGNAL-01',
  summary: 'E2E 普通记录摘要',
  report: null,
};

const Harness = ({onRequestError = vi.fn()}: {onRequestError?: (error: ApiError) => void}) => {
  const [activeSignal, setActiveSignal] = useState<{id: number; title: string} | null>(null);
  return (
    <div>
      <button type="button" onClick={() => setActiveSignal({id: 91, title: '天眼查多维度核查：宁波鸿腾精密制造有限公司'})}>
        打开报告
      </button>
      <button type="button" onClick={() => setActiveSignal({id: 92, title: 'E2E Source Signal 01'})}>
        打开非报告记录
      </button>
      <SourceSignalReportModal
        sourceId={17}
        activeSignal={activeSignal}
        onClose={() => setActiveSignal(null)}
        onRequestError={onRequestError}
      />
    </div>
  );
};

const openReport = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole('button', {name: '打开报告'}));
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('采集记录完整报告弹窗', () => {
  it('打开时先显示加载态，成功后按维度状态分组呈现报告与来源署名', async () => {
    let resolveDetail: (value: SourceSignalDetailRead) => void = () => undefined;
    vi.spyOn(api, 'sourceSignalDetail').mockImplementation(() => new Promise((resolve) => {
      resolveDetail = resolve;
    }));
    const user = userEvent.setup();
    render(<Harness />);

    await openReport(user);

    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(within(dialog).getByText('正在加载完整报告…')).toBeInTheDocument();
    expect(vi.mocked(api.sourceSignalDetail)).toHaveBeenCalledWith(17, 91, expect.anything());

    resolveDetail(detailWithReport);

    // 命中维度：状态文字 + 风险等级 + 证据引用
    const hitGroup = await within(dialog).findByTestId('source-signal-report-group-hit');
    expect(hitGroup).toHaveTextContent('风险总览');
    expect(hitGroup).toHaveTextContent('命中');
    expect(hitGroup).toHaveTextContent('警示');
    expect(hitGroup).toHaveTextContent('开庭公告 1 条，法院公告 1 条');
    expect(hitGroup).toHaveTextContent('开庭公告');
    // 正常（success 未命中）维度
    expect(within(dialog).getByTestId('source-signal-report-group-normal')).toHaveTextContent('司法解析');
    // 空 / 失败 / 额度耗尽 / 繁忙均以文字状态呈现，不只靠颜色
    expect(within(dialog).getByTestId('source-signal-report-group-empty')).toHaveTextContent('双随机抽查');
    const errorGroup = within(dialog).getByTestId('source-signal-report-group-error');
    expect(errorGroup).toHaveTextContent('查询失败：上游超时');
    expect(errorGroup).toHaveTextContent('失败');
    expect(within(dialog).getByTestId('source-signal-report-group-skipped')).toHaveTextContent('额度耗尽');
    expect(within(dialog).getByTestId('source-signal-report-group-skipped')).toHaveTextContent('查询繁忙');
    // 报告元信息与来源署名
    expect(dialog).toHaveTextContent('宁波鸿腾精密制造有限公司');
    expect(dialog).toHaveTextContent('913302127995394959');
    expect(within(dialog).getByTestId('source-signal-report-attribution')).toHaveTextContent('数据来源：天眼查');
  });

  it('ESC 关闭并把焦点还原到触发按钮', async () => {
    vi.spyOn(api, 'sourceSignalDetail').mockResolvedValue(detailWithReport);
    const user = userEvent.setup();
    render(<Harness />);

    await openReport(user);
    await screen.findByTestId('source-signal-report-attribution');

    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('button', {name: '打开报告'})).toHaveFocus();
  });

  it('Tab 焦点陷阱：从最后一个可聚焦元素回卷到第一个', async () => {
    // 用错误态构造两个可聚焦元素（关闭 + 重试），焦点陷阱才有回卷语义。
    vi.spyOn(api, 'sourceSignalDetail').mockRejectedValue(new ApiError(500, '报告服务暂不可用'));
    const user = userEvent.setup();
    render(<Harness />);

    await openReport(user);
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByRole('alert');

    const focusables = within(dialog).getAllByRole('button');
    const first = focusables[0]!;
    const last = focusables[focusables.length - 1]!;
    expect(first).toBe(dialog.querySelector('button'));

    last.focus();
    await user.tab();
    expect(first).toHaveFocus();

    first.focus();
    await user.tab({shift: true});
    expect(last).toHaveFocus();
  });

  it('详情端点 500 时显示错误态并可重试恢复，且始终可关闭', async () => {
    const request = vi.spyOn(api, 'sourceSignalDetail')
      .mockRejectedValueOnce(new ApiError(500, '报告服务暂不可用'))
      .mockResolvedValueOnce(detailWithReport);
    const user = userEvent.setup();
    render(<Harness />);

    await openReport(user);

    const dialog = screen.getByRole('dialog');
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('报告服务暂不可用');
    expect(within(dialog).getByRole('button', {name: '关闭完整报告'})).toBeEnabled();

    await user.click(within(dialog).getByRole('button', {name: '重试'}));

    expect(await within(dialog).findByTestId('source-signal-report-attribution')).toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it('权限错误上报会话边界，非权限错误不上报', async () => {
    const onRequestError = vi.fn();
    vi.spyOn(api, 'sourceSignalDetail').mockRejectedValueOnce(new ApiError(500, '内部错误'));
    const user = userEvent.setup();
    render(<Harness onRequestError={onRequestError} />);
    await openReport(user);
    await screen.findByRole('alert');
    expect(onRequestError).not.toHaveBeenCalled();
    await user.keyboard('{Escape}');

    vi.mocked(api.sourceSignalDetail).mockRejectedValueOnce(new ApiError(403, '权限不足'));
    await openReport(user);
    await waitFor(() => expect(onRequestError).toHaveBeenCalledWith(expect.objectContaining({status: 403})));
  });

  it('非报告记录显示 no-report 状态与完整正文，不显示天眼查署名', async () => {
    vi.spyOn(api, 'sourceSignalDetail').mockResolvedValue(detailWithoutReport);
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole('button', {name: '打开非报告记录'}));

    const dialog = screen.getByRole('dialog');
    expect(await within(dialog).findByTestId('source-signal-report-no-report')).toHaveTextContent('没有结构化多维度报告');
    expect(within(dialog).getByText(longContent)).toBeInTheDocument();
    expect(within(dialog).queryByTestId('source-signal-report-attribution')).not.toBeInTheDocument();
    expect(within(dialog).queryByTestId('source-signal-report-group-hit')).not.toBeInTheDocument();
  });

  it('报告被截断时显示截断说明', async () => {
    vi.spyOn(api, 'sourceSignalDetail').mockResolvedValue({
      ...detailWithReport,
      report: null,
      report_truncated: true,
    });
    const user = userEvent.setup();
    render(<Harness />);

    await openReport(user);

    expect(await screen.findByTestId('source-signal-report-no-report')).toHaveTextContent('原始报告过大');
  });

  it('切换激活记录时旧请求晚到的响应不得覆盖新记录的响应', async () => {
    const pending: Array<(value: SourceSignalDetailRead) => void> = [];
    vi.spyOn(api, 'sourceSignalDetail').mockImplementation(() => new Promise((resolve) => {
      pending.push(resolve);
    }));
    const user = userEvent.setup();
    render(<Harness />);

    await openReport(user);
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', {name: '打开非报告记录'}));

    // 旧请求（91）晚于新请求（92）返回：不得遮蔽新记录的 no-report 响应。
    pending[1]!(detailWithoutReport);
    expect(await screen.findByTestId('source-signal-report-no-report')).toBeInTheDocument();
    pending[0]!(detailWithReport);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.queryByTestId('source-signal-report-attribution')).not.toBeInTheDocument();
    expect(screen.getByTestId('source-signal-report-no-report')).toBeInTheDocument();
  });

  it('关闭后晚到的响应不更新弹窗状态（再次打开仍从加载态开始）', async () => {
    const pending: Array<(value: SourceSignalDetailRead) => void> = [];
    vi.spyOn(api, 'sourceSignalDetail').mockImplementation(() => new Promise((resolve) => {
      pending.push(resolve);
    }));
    const user = userEvent.setup();
    render(<Harness />);

    await openReport(user);
    await user.keyboard('{Escape}');
    pending[0]!(detailWithReport);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    await openReport(user);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('正在加载完整报告…')).toBeInTheDocument();
  });

  it('遮罩层级 z-[60] 高于移动端导航 z-50，且弹窗位于遮罩内', async () => {
    vi.spyOn(api, 'sourceSignalDetail').mockResolvedValue(detailWithReport);
    const user = userEvent.setup();
    render(<Harness />);

    await openReport(user);

    const overlay = await screen.findByTestId('source-signal-report-overlay');
    expect(overlay.className).toContain('z-[60]');
    expect(overlay.className).not.toContain('z-50');
    expect(overlay).toContainElement(screen.getByRole('dialog'));
  });

  it('维度摘要使用平衡换行并保留长标识符换行（text-balance + break-words）', async () => {
    vi.spyOn(api, 'sourceSignalDetail').mockResolvedValue(detailWithReport);
    const user = userEvent.setup();
    render(<Harness />);

    await openReport(user);
    await screen.findByTestId('source-signal-report-attribution');

    const summaries = screen.getAllByTestId('source-signal-dimension-summary');
    expect(summaries.length).toBeGreaterThan(0);
    for (const summary of summaries) {
      expect(summary.className).toContain('text-balance');
      expect(summary.className).toContain('break-words');
      expect(summary.className).toContain('whitespace-pre-wrap');
    }
  });
});

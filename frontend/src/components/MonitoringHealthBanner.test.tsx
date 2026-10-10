import {cleanup, render, screen, within} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import type {MonitoringHealthRead} from '../api';
import type {MonitoringHealthSnapshot} from '../useMonitoringHealth';
import {MonitoringHealthBanner, MonitoringSourceFreshness} from './MonitoringHealthBanner';

const baseHealth: MonitoringHealthRead = {
  as_of: '2026-09-11T06:00:00Z',
  overall: 'ok',
  scheduler: {
    status: 'ok',
    last_heartbeat_at: '2026-09-11T05:59:30Z',
    age_seconds: 30,
    interval_seconds: 60,
    stale_after_seconds: 180,
    current_work: [],
    scheduled_jobs: [],
    recent_runs: [],
  },
  processing: {
    total: 0,
    classification_failed: 0,
    backlog_over_1h: 0,
    oldest_pending_age_seconds: null,
    last_run: {status: 'succeeded', started_at: '2026-09-11T05:58:00Z', finished_at: '2026-09-11T05:58:20Z', processed: 5, filtered: 1, failed: 0},
  },
  sources: [
    {
      source_id: 17,
      code: 'OFFICIAL-17',
      name: '官方风险源',
      state: 'ok',
      reason_code: 'success_observed',
      last_success_at: '2026-09-11T05:30:00Z',
      last_attempt_at: '2026-09-11T05:30:10Z',
      next_expected_at: '2026-09-11T06:00:00Z',
    },
  ],
};

const ready = (health: MonitoringHealthRead): MonitoringHealthSnapshot => ({status: 'ready', health});

// 与组件同源的 Intl 格式化：断言时间数据流，而不是硬编码某台机器的时区字符串。
const expectedTime = (value: string) => new Date(value).toLocaleString('zh-CN', {hour12: false});

const renderBanner = (snapshot: MonitoringHealthSnapshot, hasCurrentRisks = false) => render(
  <MonitoringHealthBanner snapshot={snapshot} hasCurrentRisks={hasCurrentRisks} />,
);

afterEach(cleanup);

describe('MonitoringHealthBanner 四态文案（总览空风险）', () => {
  it('ok：显示「监控正常，截至…暂无当前风险」并暴露稳定 testid/data-state', () => {
    renderBanner(ready(baseHealth));
    const banner = screen.getByTestId('monitoring-health-banner');
    expect(banner).toHaveAttribute('data-state', 'ok');
    expect(banner.textContent).toMatch(/监控正常，截至.*暂无当前风险/);
  });

  it('unknown：显示「尚无法确认监控状态」', () => {
    renderBanner(ready({...baseHealth, overall: 'unknown'}));
    const banner = screen.getByTestId('monitoring-health-banner');
    expect(banner).toHaveAttribute('data-state', 'unknown');
    expect(banner.textContent).toContain('尚无法确认监控状态');
  });

  it('degraded：显示「部分链路异常，结果可能不完整」且以告警语义（role=alert）表达', () => {
    renderBanner(ready({...baseHealth, overall: 'degraded'}));
    const banner = screen.getByTestId('monitoring-health-banner');
    expect(banner).toHaveAttribute('data-state', 'degraded');
    expect(banner.textContent).toContain('部分链路异常，结果可能不完整');
    expect(banner).toHaveAttribute('role', 'alert');
  });

  it('inactive：显示「未开启自动监控」', () => {
    renderBanner(ready({...baseHealth, overall: 'inactive'}));
    const banner = screen.getByTestId('monitoring-health-banner');
    expect(banner).toHaveAttribute('data-state', 'inactive');
    expect(banner.textContent).toContain('未开启自动监控');
  });
});

describe('MonitoringHealthBanner 非空风险时展示数据新鲜度', () => {
  it('ok 且存在当前风险：不宣称「暂无当前风险」，改为展示数据截至时间', () => {
    renderBanner(ready(baseHealth), true);
    const banner = screen.getByTestId('monitoring-health-banner');
    expect(banner.textContent).toContain('监控正常');
    expect(banner.textContent).toContain('数据截至');
    expect(banner.textContent).not.toContain('暂无当前风险');
  });

  it('degraded 且存在当前风险：降级文案与数据截至并存，风险数据不被横幅清空语义替代', () => {
    renderBanner(ready({...baseHealth, overall: 'degraded'}), true);
    const banner = screen.getByTestId('monitoring-health-banner');
    expect(banner.textContent).toContain('部分链路异常，结果可能不完整');
    expect(banner.textContent).toContain('数据截至');
  });
});

describe('MonitoringHealthBanner 诊断不可用', () => {
  it('API 失败（snapshot=unknown）：显示无法确认，不得显示 ok 文案', () => {
    renderBanner({status: 'unknown'});
    const banner = screen.getByTestId('monitoring-health-banner');
    expect(banner).toHaveAttribute('data-state', 'unknown');
    expect(banner.textContent).toContain('尚无法确认监控状态');
    expect(banner.textContent).not.toContain('监控正常');
    expect(banner.textContent).not.toContain('数据截至');
  });

  it('403/无权限（hidden）与首载中（loading）均不渲染任何横幅', () => {
    const hidden = renderBanner({status: 'hidden'});
    expect(hidden.container.textContent).toBe('');
    hidden.unmount();

    const loading = renderBanner({status: 'loading'});
    expect(loading.container.textContent).toBe('');
  });
});

describe('MonitoringSourceFreshness 信息源页每来源新鲜度', () => {
  it('ok 来源显示最近成功与下次预期时间', () => {
    render(<MonitoringSourceFreshness health={baseHealth.sources[0]} />);
    const cell = screen.getByTestId('source-health-17');
    expect(cell.textContent).toContain('采集正常');
    expect(cell.textContent).toContain(`最近成功 ${expectedTime('2026-09-11T05:30:00Z')}`);
    expect(cell.textContent).toContain(`下次预期 ${expectedTime('2026-09-11T06:00:00Z')}`);
  });

  // 版面回归：采集状态独占一行，「最近成功」「下次预期」各占一行，且每行是
  // 「标签 + 完整时间」的单个 nowrap 组——只有行与行之间换行，时间戳自身永不拆断。
  it('采集状态单独一行，最近成功与下次预期各占一行', () => {
    render(<MonitoringSourceFreshness health={baseHealth.sources[0]} />);
    const cell = screen.getByTestId('source-health-17');

    const statusRow = cell.querySelector('.rounded-full')?.parentElement;
    expect(statusRow).not.toBeNull();
    expect(within(statusRow as HTMLElement).getByText('采集正常')).toBeInTheDocument();
    // 状态行不得混入任何时间文本。
    expect(statusRow?.textContent).not.toContain('最近成功');
    expect(statusRow?.textContent).not.toContain('下次预期');

    const lastSuccessRow = screen.getByTestId('source-health-last-success-17');
    const nextExpectedRow = screen.getByTestId('source-health-next-expected-17');
    expect(lastSuccessRow).not.toBe(nextExpectedRow);
    expect(lastSuccessRow.textContent).toBe(`最近成功 ${expectedTime('2026-09-11T05:30:00Z')}`);
    expect(nextExpectedRow.textContent).toBe(`下次预期 ${expectedTime('2026-09-11T06:00:00Z')}`);
  });

  it('每个时间行是单个 nowrap 组（标签与完整时间同行不拆），且不再使用「·」前缀', () => {
    render(<MonitoringSourceFreshness health={baseHealth.sources[0]} />);
    const cell = screen.getByTestId('source-health-17');

    for (const row of [screen.getByTestId('source-health-last-success-17'), screen.getByTestId('source-health-next-expected-17')]) {
      const nowrapTokens = Array.from(row.querySelectorAll('.whitespace-nowrap'));
      // 标签与时间必须同属一个 nowrap 组：拆成两个可换行 token 会让时间戳掉到下一行。
      expect(nowrapTokens).toHaveLength(1);
      expect(row.textContent).toBe(nowrapTokens[0]?.textContent);
    }

    // 「·」分隔符已由分行取代，不得残留为独立前缀。
    expect(cell.textContent).not.toContain('·');
    expect(cell.textContent).toContain('下次预期 ');
  });

  it('时间行容器启用 min-w-0，窄列内允许收缩而不是把 nowrap 组撑成不可收缩整体', () => {
    render(<MonitoringSourceFreshness health={baseHealth.sources[0]} />);
    const cell = screen.getByTestId('source-health-17');
    const firstRow = cell.querySelector('.min-w-0');
    expect(firstRow).not.toBeNull();
    expect(firstRow?.textContent).toContain('最近成功');
    expect(firstRow?.classList.contains('min-w-0')).toBe(true);
  });

  // 回归：单 nowrap 组契约与时间文本长度无关。跨年时间戳（比常规值多出年份位宽）
// 仍必须完整落在同一个 nowrap 组内，既不截断也不拆行。
  it('跨年长时间戳同样完整落在单个 nowrap 组内，不截断、不拆行', () => {
    render(
      <MonitoringSourceFreshness
        health={{
          ...baseHealth.sources[0],
          last_success_at: '2026-12-31T23:59:59Z',
          next_expected_at: '2027-01-01T00:00:00Z',
        }}
      />,
    );
    const lastSuccess = screen.getByTestId('source-health-last-success-17');
    const nextExpected = screen.getByTestId('source-health-next-expected-17');
    const longStamp = '2026-12-31T23:59:59Z';
    const nextStamp = '2027-01-01T00:00:00Z';

    expect(lastSuccess.textContent).toBe(`最近成功 ${expectedTime(longStamp)}`);
    expect(nextExpected.textContent).toBe(`下次预期 ${expectedTime(nextStamp)}`);
    for (const row of [lastSuccess, nextExpected]) {
      expect(row.querySelectorAll('.whitespace-nowrap')).toHaveLength(1);
      expect(row.querySelector('.whitespace-nowrap')?.textContent).toBe(row.textContent);
    }
    expect(lastSuccess.textContent).toContain(expectedTime(longStamp));
  });

  it('failed 来源显示失败标签与脱敏 reason_code', () => {
    render(
      <MonitoringSourceFreshness
        health={{...baseHealth.sources[0], state: 'failed', reason_code: 'last_attempt_failed', next_expected_at: null}}
      />,
    );
    const cell = screen.getByTestId('source-health-17');
    expect(cell.textContent).toContain('采集失败');
    expect(cell.textContent).toContain('last_attempt_failed');
  });

  it('overdue 来源显示超期标签，不得显示为正常', () => {
    render(
      <MonitoringSourceFreshness
        health={{...baseHealth.sources[0], state: 'overdue', reason_code: 'missed_expected_trigger'}}
      />,
    );
    const cell = screen.getByTestId('source-health-17');
    expect(cell.textContent).toContain('已超期');
    expect(cell.textContent).not.toContain('采集正常');
  });

  it('停用与外部核查来源以中性语义展示，不出现红色故障标签', () => {
    const disabled = render(
      <MonitoringSourceFreshness
        health={{...baseHealth.sources[0], state: 'disabled', reason_code: 'disabled', next_expected_at: null}}
      />,
    );
    expect(screen.getByTestId('source-health-17').textContent).toContain('已停用');
    expect(screen.queryByText('采集失败')).not.toBeInTheDocument();
    expect(screen.queryByText('已超期')).not.toBeInTheDocument();
    disabled.unmount();

    render(
      <MonitoringSourceFreshness
        health={{...baseHealth.sources[0], source_id: 18, state: 'on_demand', reason_code: 'on_demand', next_expected_at: null}}
      />,
    );
    expect(within(screen.getByTestId('source-health-18')).getByText('核查')).toBeInTheDocument();
    expect(screen.queryByText('采集失败')).not.toBeInTheDocument();
  });

  it('健康快照中没有该来源时不渲染任何占位', () => {
    render(<MonitoringSourceFreshness health={undefined} />);
    expect(screen.queryByTestId(/^source-health-/)).not.toBeInTheDocument();
  });

  it('on_demand 来源显示「核查 + 最近核查真实时间」，不显示下次预期与最近成功', () => {
    render(
      <MonitoringSourceFreshness
        health={{
          ...baseHealth.sources[0],
          state: 'on_demand',
          reason_code: 'on_demand',
          next_expected_at: null,
          last_success_at: null,
          last_attempt_at: '2026-09-10T03:00:00Z',
        }}
      />,
    );
    const cell = screen.getByTestId('source-health-17');
    expect(within(cell).getByText('核查')).toBeInTheDocument();
    expect(cell.textContent).toContain(`最近核查 ${expectedTime('2026-09-10T03:00:00Z')}`);
    expect(cell.textContent).not.toContain('下次预期');
    expect(cell.textContent).not.toContain('最近成功');
  });

  it('on_demand 分支同样把「最近核查」标签与时间戳放在同一个 nowrap 组内', () => {
    render(
      <MonitoringSourceFreshness
        health={{
          ...baseHealth.sources[0],
          state: 'on_demand',
          reason_code: 'on_demand',
          next_expected_at: null,
          last_success_at: null,
          last_attempt_at: '2026-09-10T03:00:00Z',
        }}
      />,
    );
    const cell = screen.getByTestId('source-health-17');
    const checkRow = screen.getByTestId('source-health-last-check-17');
    const nowrapTokens = Array.from(checkRow.querySelectorAll('.whitespace-nowrap'));
    expect(nowrapTokens).toHaveLength(1);
    expect(nowrapTokens[0]?.textContent).toBe(checkRow.textContent);
    expect(checkRow.textContent).toBe(`最近核查 ${expectedTime('2026-09-10T03:00:00Z')}`);
    // on_demand 只有「最近核查」一行时间组，不存在下次预期行。
    expect(cell.querySelector('[data-testid="source-health-next-expected-17"]')).toBeNull();
    expect(cell.querySelector('[data-testid="source-health-last-success-17"]')).toBeNull();
  });

  it('onDemand 覆盖后端 disabled：外部核查工具停用时显示核查而非已停用', () => {
    render(
      <MonitoringSourceFreshness
        onDemand
        health={{...baseHealth.sources[0], state: 'disabled', reason_code: 'disabled', next_expected_at: null}}
      />,
    );
    const cell = screen.getByTestId('source-health-17');
    expect(within(cell).getByText('核查')).toBeInTheDocument();
    expect(cell.textContent).not.toContain('已停用');
    expect(cell.textContent).not.toContain('下次预期');
  });

  it('labelOverride 仅在 on_demand 语义下覆盖标签，默认仍为「核查」且不影响其他状态', () => {
    const overridden = render(
      <MonitoringSourceFreshness
        onDemand
        labelOverride="按需核查"
        health={{...baseHealth.sources[0], state: 'disabled', reason_code: 'disabled', next_expected_at: null}}
      />,
    );
    expect(within(screen.getByTestId('source-health-17')).getByText('按需核查')).toBeInTheDocument();
    overridden.unmount();

    // 未传覆盖时保持默认「核查」；非 on_demand 状态即便传入覆盖也不改变。
    const defaultLabel = render(
      <MonitoringSourceFreshness
        onDemand
        health={{...baseHealth.sources[0], state: 'disabled', reason_code: 'disabled', next_expected_at: null}}
      />,
    );
    expect(within(screen.getByTestId('source-health-17')).getByText('核查')).toBeInTheDocument();
    defaultLabel.unmount();

    render(
      <MonitoringSourceFreshness
        labelOverride="按需核查"
        health={{...baseHealth.sources[0], state: 'disabled', reason_code: 'disabled', next_expected_at: null}}
      />,
    );
    expect(within(screen.getByTestId('source-health-17')).getByText('已停用')).toBeInTheDocument();
    expect(screen.queryByText('按需核查')).not.toBeInTheDocument();
  });

  it('外部核查来源即使后端返回周度 ok 状态，仍以中性「核查」展示且不显示下次预期', () => {
    render(
      <MonitoringSourceFreshness
        onDemand
        health={{
          ...baseHealth.sources[0],
          state: 'ok',
          reason_code: 'success_observed',
          last_success_at: '2026-09-06T22:30:00Z',
          last_attempt_at: '2026-09-06T22:30:00Z',
          next_expected_at: '2026-09-12T22:00:00Z',
        }}
      />,
    );
    const cell = screen.getByTestId('source-health-17');
    expect(within(cell).getByText('核查')).toBeInTheDocument();
    // 外部核查来源不得因后端周度 ok 就展示「采集正常」或泄露下次预期。
    expect(cell.textContent).not.toContain('采集正常');
    expect(cell.textContent).not.toContain('下次预期');
    expect(cell.textContent).toContain(`最近核查 ${expectedTime('2026-09-06T22:30:00Z')}`);
  });

  it('on_demand 且无核查记录时展示「最近核查 —」，不编造时间', () => {
    render(
      <MonitoringSourceFreshness
        onDemand
        health={{
          ...baseHealth.sources[0],
          state: 'disabled',
          reason_code: 'disabled',
          next_expected_at: null,
          last_success_at: null,
          last_attempt_at: null,
        }}
      />,
    );
    expect(screen.getByTestId('source-health-17').textContent).toContain('最近核查 —');
  });
});

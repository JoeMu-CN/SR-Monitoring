import {cleanup, render, screen} from '@testing-library/react';
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

describe('MonitoringSourceFreshness 数据源页每来源新鲜度', () => {
  it('ok 来源显示最近成功与下次预期时间', () => {
    render(<MonitoringSourceFreshness health={baseHealth.sources[0]} />);
    const cell = screen.getByTestId('source-health-17');
    expect(cell.textContent).toContain('采集正常');
    expect(cell.textContent).toContain(`最近成功 ${expectedTime('2026-09-11T05:30:00Z')}`);
    expect(cell.textContent).toContain(`下次预期 ${expectedTime('2026-09-11T06:00:00Z')}`);
  });

  it('「最近成功/下次预期 + 时间」各自为不可拆分的 nowrap 语义组，CJK 标签不与时间断行', () => {
    render(<MonitoringSourceFreshness health={baseHealth.sources[0]} />);
    const cell = screen.getByTestId('source-health-17');
    const nowrapTexts = Array.from(cell.querySelectorAll('.whitespace-nowrap')).map((el) => el.textContent ?? '');
    expect(nowrapTexts.some((text) => text.startsWith('最近成功 '))).toBe(true);
    expect(nowrapTexts.some((text) => text.startsWith('下次预期 '))).toBe(true);
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

  it('停用与按需来源以中性语义展示，不出现红色故障标签', () => {
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
    expect(screen.getByTestId('source-health-18').textContent).toContain('按需核查');
    expect(screen.queryByText('采集失败')).not.toBeInTheDocument();
  });

  it('健康快照中没有该来源时不渲染任何占位', () => {
    render(<MonitoringSourceFreshness health={undefined} />);
    expect(screen.queryByTestId(/^source-health-/)).not.toBeInTheDocument();
  });
});

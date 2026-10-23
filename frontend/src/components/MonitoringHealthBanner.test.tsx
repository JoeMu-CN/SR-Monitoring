import {cleanup, render, screen} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import type {MonitoringHealthRead, MonitoringSourceHealth} from '../api';
import type {MonitoringHealthSnapshot} from '../useMonitoringHealth';
import {
  MonitoringHealthBanner,
  MonitoringSourceFreshness,
  MonitoringSourceStateBadge,
  ProcessingHealthDetailBanner,
} from './MonitoringHealthBanner';

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

// degraded 场景统一入口：基础健康数据本身健康，逐例只注入需要成立的异常事实。
const degraded = (health: MonitoringHealthRead): MonitoringHealthSnapshot => ready({...health, overall: 'degraded'});

const source = (overrides: Partial<MonitoringSourceHealth> & {source_id: number}): MonitoringSourceHealth => ({
  ...baseHealth.sources[0],
  ...overrides,
});

const bannerText = (): string => screen.getByTestId('monitoring-health-banner').textContent ?? '';

// 与组件同源的 Intl 格式化：断言时间数据流，而不是硬编码某台机器的时区字符串。
const expectedTime = (value: string) => new Date(value).toLocaleString('zh-CN', {hour12: false});

const renderBanner = (snapshot: MonitoringHealthSnapshot, hasCurrentRisks = false) => render(
  <MonitoringHealthBanner snapshot={snapshot} hasCurrentRisks={hasCurrentRisks} />,
);

type SourceHealthProps = Parameters<typeof MonitoringSourceFreshness>[0];

// 与信息源页实际组合一致：名称块内「名称 / 徽标」两行 + 徽标下方的 ID 与时间块。
// 徽标与时间行共用同一 props，才能验证两处判定同源（onDemand 覆盖不会只在一边生效）。
const renderSourceHealth = (props: SourceHealthProps) => render(
  <>
    <MonitoringSourceStateBadge {...props} />
    <MonitoringSourceFreshness {...props} />
  </>,
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

  it('degraded：以告警语义（role=alert）只报异常大类，细节留给模块页', () => {
    renderBanner(degraded({
      ...baseHealth,
      sources: [source({source_id: 17, name: '海关总署', state: 'failed', reason_code: 'last_attempt_failed'})],
    }));
    const banner = screen.getByTestId('monitoring-health-banner');
    expect(banner).toHaveAttribute('data-state', 'degraded');
    expect(banner).toHaveAttribute('role', 'alert');
    expect(banner.textContent).toContain('当前信源采集有部分异常');
    expect(banner.textContent).not.toContain('海关总署');
    expect(banner.textContent).not.toContain('1 个采集失败');
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

  it('degraded 且存在当前风险：异常大类与数据截至并存，风险数据不被横幅清空语义替代', () => {
    renderBanner(degraded({...baseHealth, scheduler: {...baseHealth.scheduler, status: 'stale', age_seconds: 600}}), true);
    const banner = screen.getByTestId('monitoring-health-banner');
    expect(banner.textContent).toContain('当前调度器心跳异常');
    expect(banner.textContent).toContain('数据截至');
  });
});

// 总览 degraded 只报大类：每例只让一类成立，验证固定拼接顺序与「不泄露细节」。
describe('MonitoringHealthBanner degraded 异常大类', () => {
  it('调度器心跳超时或未上报：报「当前调度器心跳异常」，不含时长细节', () => {
    const stale = renderBanner(degraded({...baseHealth, scheduler: {...baseHealth.scheduler, status: 'stale', age_seconds: 600}}));
    expect(bannerText()).toBe(`当前调度器心跳异常 · 数据截至${expectedTime(baseHealth.as_of)}`);
    expect(bannerText()).not.toContain('10 分钟');
    stale.unmount();

    renderBanner(degraded({
      ...baseHealth,
      scheduler: {...baseHealth.scheduler, status: 'unknown', last_heartbeat_at: null, age_seconds: null},
    }));
    expect(bannerText()).toContain('当前调度器心跳异常');
  });

  it('信源 failed / overdue：只报「当前信源采集有部分异常」，不出现条数与来源名', () => {
    renderBanner(degraded({
      ...baseHealth,
      sources: [
        source({source_id: 17, name: '海关总署', state: 'failed', reason_code: 'last_attempt_failed'}),
        source({source_id: 18, name: '中央气象台预警', state: 'overdue', reason_code: 'missed_expected_trigger'}),
      ],
    }));
    const text = bannerText();
    expect(text).toBe(`当前信源采集有部分异常 · 数据截至${expectedTime(baseHealth.as_of)}`);
    expect(text).not.toContain('海关总署');
    expect(text).not.toContain('中央气象台预警');
    expect(text).not.toContain('1 个');
  });

  it('信号处理三类异常：只报「当前信号处理异常」，不出现积压量与分类失败条数', () => {
    renderBanner(degraded({
      ...baseHealth,
      processing: {
        ...baseHealth.processing,
        total: 1234,
        oldest_pending_age_seconds: 2 * 86400,
        classification_failed: 7,
        last_run: {...baseHealth.processing.last_run, status: 'failed', failed: 3},
      },
    }));
    const text = bannerText();
    expect(text).toContain('当前信号处理异常');
    expect(text).not.toContain('积压');
    expect(text).not.toContain('1234');
    expect(text).not.toContain('分类失败');
  });

  it('待处理未超过 1 小时且无其他异常：回退兜底大类「当前监控链路异常」', () => {
    renderBanner(degraded({
      ...baseHealth,
      processing: {...baseHealth.processing, total: 42, oldest_pending_age_seconds: 600},
    }));
    expect(bannerText()).toContain('当前监控链路异常');
    expect(bannerText()).not.toContain('积压');
  });

  it('多类合并按「调度器 → 信源 → 信号处理」顺序用「；」连接', () => {
    renderBanner(degraded({
      ...baseHealth,
      scheduler: {...baseHealth.scheduler, status: 'stale', age_seconds: 600},
      sources: [source({source_id: 17, name: '中央气象台预警', state: 'overdue', reason_code: 'missed_expected_trigger'})],
      processing: {...baseHealth.processing, total: 1234, oldest_pending_age_seconds: 2 * 86400},
    }));
    const text = bannerText();
    expect(text).toBe(
      `当前调度器心跳异常；当前信源采集有部分异常；当前信号处理异常 · 数据截至${expectedTime(baseHealth.as_of)}`,
    );
    expect(text.split('；')).toHaveLength(3);
    expect(text).not.toContain('中央气象台预警');
    expect(text).not.toContain('积压 1234 条');
  });
});

describe('ProcessingHealthDetailBanner 信号处理细节（调度器实况页）', () => {
  it('积压 / 上一轮失败 / 分类失败按顺序合并，并格式化最旧积压时长', () => {
    render(
      <ProcessingHealthDetailBanner
        snapshot={ready({
          ...baseHealth,
          overall: 'degraded',
          processing: {
            ...baseHealth.processing,
            total: 5,
            oldest_pending_age_seconds: 2 * 86400 + 3 * 3600,
            classification_failed: 7,
            last_run: {...baseHealth.processing.last_run, status: 'failed', failed: 3},
          },
        })}
      />,
    );

    const banner = screen.getByTestId('processing-health-detail-banner');
    expect(banner).toHaveAttribute('role', 'alert');
    expect(banner.textContent).toBe('信号处理异常：积压 5 条（最旧 2 天 3 小时）；上一轮处理失败；7 条分类失败待复核');
  });

  it('只有积压时也报出条数与最旧时长', () => {
    render(
      <ProcessingHealthDetailBanner
        snapshot={ready({
          ...baseHealth,
          processing: {...baseHealth.processing, total: 1234, oldest_pending_age_seconds: 2 * 86400},
        })}
      />,
    );

    expect(screen.getByTestId('processing-health-detail-banner').textContent).toBe('信号处理异常：积压 1234 条（最旧 2 天）');
  });

  it('无信号处理异常（含积压未满 1 小时）时返回 null', () => {
    render(
      <ProcessingHealthDetailBanner
        snapshot={ready({...baseHealth, processing: {...baseHealth.processing, total: 42, oldest_pending_age_seconds: 600}})}
      />,
    );
    expect(screen.queryByTestId('processing-health-detail-banner')).not.toBeInTheDocument();
  });

  it('快照非 ready 时返回 null', () => {
    for (const snapshot of [{status: 'loading'}, {status: 'hidden'}, {status: 'unknown'}] as const) {
      const view = render(<ProcessingHealthDetailBanner snapshot={snapshot} />);
      expect(screen.queryByTestId('processing-health-detail-banner')).not.toBeInTheDocument();
      view.unmount();
    }
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

describe('信息源页每来源采集状态徽标与新鲜度时间', () => {
  it('ok 来源徽标显示「采集正常」，时间块显示最近成功与下次预期', () => {
    renderSourceHealth({health: baseHealth.sources[0]});

    expect(screen.getByTestId('source-health-state-17')).toHaveTextContent('采集正常');
    const cell = screen.getByTestId('source-health-17');
    expect(cell.textContent).toContain(`最近成功 ${expectedTime('2026-09-11T05:30:00Z')}`);
    expect(cell.textContent).toContain(`下次预期 ${expectedTime('2026-09-11T06:00:00Z')}`);
    // 徽标与时间块是两个独立容器：状态已上移到名称块，不再与时间挤在同一格。
    expect(cell.contains(screen.getByTestId('source-health-state-17'))).toBe(false);
  });

  // 版面回归：徽标独占名称块下一格，盒里只有状态文案。原因与时间都不并入该盒——
  // 并入会撑高徽标盒，破坏徽标下缘与 LOGO 下缘的对齐。
  it('徽标盒只承载状态文案，原因与时间都不并入', () => {
    renderSourceHealth({health: baseHealth.sources[0]});

    const badge = screen.getByTestId('source-health-state-17');
    expect(badge.textContent).toBe('采集正常');
    expect(badge.textContent).not.toContain('最近成功');
    expect(badge.textContent).not.toContain('下次预期');
    expect(badge.textContent).not.toContain('原因');
    expect(badge).toHaveClass('rounded-full');
    // column flex 内按内容宽度贴左，不被拉成整行宽。
    expect(badge).toHaveClass('self-start');

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

    it('failed 来源徽标显示失败标签，原因码在时间块单独成行、不并入徽标盒', () => {
      renderSourceHealth({health: {...baseHealth.sources[0], state: 'failed', reason_code: 'last_attempt_failed', next_expected_at: null}});

      const badge = screen.getByTestId('source-health-state-17');
      expect(badge.textContent).toBe('采集失败');
      expect(badge.textContent).not.toContain('last_attempt_failed');
      expect(screen.getByTestId('source-health-17').textContent).toContain('last_attempt_failed');
    });

    it('overdue 来源徽标显示超期标签，不得显示为正常', () => {
      renderSourceHealth({health: {...baseHealth.sources[0], state: 'overdue', reason_code: 'missed_expected_trigger'}});

      const badge = screen.getByTestId('source-health-state-17');
      expect(badge.textContent).toBe('已超期');
      expect(badge.textContent).not.toContain('采集正常');
    });

    it('停用与外部核查来源以中性语义展示，不出现红色故障标签', () => {
      const disabled = renderSourceHealth({
        health: {...baseHealth.sources[0], state: 'disabled', reason_code: 'disabled', next_expected_at: null},
      });
      expect(screen.getByTestId('source-health-state-17').textContent).toBe('已停用');
      expect(screen.queryByText('采集失败')).not.toBeInTheDocument();
      expect(screen.queryByText('已超期')).not.toBeInTheDocument();
      disabled.unmount();

      renderSourceHealth({
        health: {...baseHealth.sources[0], source_id: 18, state: 'on_demand', reason_code: 'on_demand', next_expected_at: null},
      });
      expect(screen.getByTestId('source-health-state-18').textContent).toBe('核查');
      expect(screen.queryByText('采集失败')).not.toBeInTheDocument();
    });

    it('健康快照中没有该来源时徽标与时间块都不渲染任何占位', () => {
      renderSourceHealth({health: undefined});
      expect(screen.queryByTestId(/^source-health-/)).not.toBeInTheDocument();
    });

    it('on_demand 来源显示「核查 + 最近核查真实时间」，不显示下次预期与最近成功', () => {
      renderSourceHealth({
        health: {
          ...baseHealth.sources[0],
          state: 'on_demand',
          reason_code: 'on_demand',
          next_expected_at: null,
          last_success_at: null,
          last_attempt_at: '2026-09-10T03:00:00Z',
        },
      });

      expect(screen.getByTestId('source-health-state-17').textContent).toBe('核查');
      const cell = screen.getByTestId('source-health-17');
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
    renderSourceHealth({
      onDemand: true,
      health: {...baseHealth.sources[0], state: 'disabled', reason_code: 'disabled', next_expected_at: null},
    });

    expect(screen.getByTestId('source-health-state-17').textContent).toBe('核查');
    expect(screen.getByTestId('source-health-17').textContent).not.toContain('下次预期');
  });

  it('labelOverride 仅在 on_demand 语义下覆盖标签，默认仍为「核查」且不影响其他状态', () => {
    const overridden = renderSourceHealth({
      onDemand: true,
      labelOverride: '按需核查',
      health: {...baseHealth.sources[0], state: 'disabled', reason_code: 'disabled', next_expected_at: null},
    });
    expect(screen.getByTestId('source-health-state-17').textContent).toBe('按需核查');
    overridden.unmount();

    // 未传覆盖时保持默认「核查」；非 on_demand 状态即便传入覆盖也不改变。
    const defaultLabel = renderSourceHealth({
      onDemand: true,
      health: {...baseHealth.sources[0], state: 'disabled', reason_code: 'disabled', next_expected_at: null},
    });
    expect(screen.getByTestId('source-health-state-17').textContent).toBe('核查');
    defaultLabel.unmount();

    renderSourceHealth({
      labelOverride: '按需核查',
      health: {...baseHealth.sources[0], state: 'disabled', reason_code: 'disabled', next_expected_at: null},
    });
    expect(screen.getByTestId('source-health-state-17').textContent).toBe('已停用');
    expect(screen.queryByText('按需核查')).not.toBeInTheDocument();
  });

  it('外部核查来源即使后端返回周度 ok 状态，仍以中性「核查」展示且不显示下次预期', () => {
    renderSourceHealth({
      onDemand: true,
      health: {
        ...baseHealth.sources[0],
        state: 'ok',
        reason_code: 'success_observed',
        last_success_at: '2026-09-06T22:30:00Z',
        last_attempt_at: '2026-09-06T22:30:00Z',
        next_expected_at: '2026-09-12T22:00:00Z',
      },
    });

    const badge = screen.getByTestId('source-health-state-17');
    expect(badge.textContent).toBe('核查');
    // 外部核查来源不得因后端周度 ok 就展示「采集正常」或泄露下次预期。
    expect(badge.textContent).not.toContain('采集正常');
    const cell = screen.getByTestId('source-health-17');
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

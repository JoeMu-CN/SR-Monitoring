import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {MonitoringHealthRead} from '../api';
import type {DataSource} from '../types';
import type {MonitoringHealthSnapshot} from '../useMonitoringHealth';
import {DataSourcesView} from './DataSourcesView';

const source: DataSource = {
  id: '17',
  code: 'OFFICIAL-17',
  name: '官方风险源',
  type: 'official_api',
  credibility: 95,
  schedule: '*/30 * * * *',
  endpointUrl: 'https://official.example/events',
  authType: 'none',
  loginConfig: {},
  credentialRef: null,
  apiKeyConfigured: false,
  apiKeyHint: null,
  description: null,
  adapterConfig: {},
  adapterStatus: 'builtin',
  adapterVersion: 1,
  adapterPublishedAt: null,
  accessStatus: 'ready',
  accessCooldownUntil: null,
  accessLastHttpStatus: 200,
  accessLastErrorKind: null,
  enabled: true,
  status: 'normal',
  latency: '运行正常',
  lastSyncTime: '2026-09-01 09:00',
  itemCount: 2,
  totalSignalCount: 25,
  validSignalCount: 7,
  signalValidityDays: 30,
  validityMode: 'fixed_days',
  validityPolicy: {mode: 'fixed_days', fixed_days: 30},
  validityPolicyVersion: 'v1',
};

const monitoringHealthWith = (source: MonitoringHealthRead['sources'][number]): MonitoringHealthSnapshot => ({
  status: 'ready',
  health: {
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
    sources: [source],
  },
});

const healthySource: MonitoringHealthRead['sources'][number] = {
  source_id: 17,
  code: 'OFFICIAL-17',
  name: '官方风险源',
  state: 'ok',
  reason_code: 'success_observed',
  last_success_at: '2026-09-11T05:30:00Z',
  last_attempt_at: '2026-09-11T05:30:10Z',
  next_expected_at: '2026-09-11T06:00:00Z',
};

const readyHealth = monitoringHealthWith(healthySource);

// 与组件同源的 Intl 格式化：断言时间数据流，而不是硬编码某台机器的时区字符串。
const expectedTime = (value: string) => new Date(value).toLocaleString('zh-CN', {hour12: false});

afterEach(cleanup);

describe('数据源采集记录入口', () => {
  it('将有效数和累计数分别链接到对应范围的第一页', () => {
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[source]}
          role="viewer"
          onUpdateSource={vi.fn()}
          onRefreshSources={vi.fn()}
          monitoringHealth={readyHealth}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', {name: '官方风险源 有效记录 7 条'})).toHaveAttribute(
      'href',
      '/sources/17/signals?scope=valid&page=1',
    );
    expect(screen.getByRole('link', {name: '官方风险源 全部历史记录 25 条'})).toHaveAttribute(
      'href',
      '/sources/17/signals?scope=all&page=1',
    );
  });

  it('有效期策略标签整体 nowrap，「长期有效」等 CJK 标签不拆字换行', () => {
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[{
            ...source,
            validityMode: 'indefinite',
            validityPolicy: {mode: 'indefinite'},
            signalValidityDays: null,
          }]}
          role="viewer"
          onUpdateSource={vi.fn()}
          onRefreshSources={vi.fn()}
          monitoringHealth={readyHealth}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('长期有效')).toHaveClass('whitespace-nowrap');
  });
});

describe('数据源有效期策略表单', () => {
  const renderAdmin = (overrides: Partial<DataSource> = {}, monitoringHealth: MonitoringHealthSnapshot = readyHealth) => {
    const onUpdateSource = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[{...source, ...overrides}]}
          role="admin"
          onUpdateSource={onUpdateSource}
          onRefreshSources={vi.fn().mockResolvedValue(undefined)}
          monitoringHealth={monitoringHealth}
        />
      </MemoryRouter>,
    );
    return onUpdateSource;
  };

  const openForm = async () => {
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', {name: '编辑'}));
    return user;
  };

  it('旧 signal_validity_days 映射为 fixed_days 模式并回填天数', async () => {
    renderAdmin();
    await openForm();

    expect(screen.getByLabelText('有效期策略')).toHaveValue('fixed_days');
    expect(screen.getByLabelText('固定天数（天）')).toHaveValue(30);
  });

  it('五种模式可选且条件字段随模式切换', async () => {
    const user = await (async () => {
      renderAdmin();
      return openForm();
    })();

    const modeSelect = screen.getByLabelText('有效期策略');
    expect(modeSelect).toHaveValue('fixed_days');

    await user.selectOptions(modeSelect, 'event_end_plus_grace');
    expect(screen.getByLabelText('宽限天数（天）')).toBeInTheDocument();
    expect(screen.queryByLabelText('固定天数（天）')).not.toBeInTheDocument();

    await user.selectOptions(modeSelect, 'until_superseded');
    expect(screen.getByLabelText(/替代判定键/)).toBeInTheDocument();

    await user.selectOptions(modeSelect, 'until_revoked');
    expect(screen.getByLabelText('按完整快照缺失自动撤销（名单类信源）')).toBeInTheDocument();

    await user.selectOptions(modeSelect, 'indefinite');
    expect(screen.queryByLabelText('固定天数（天）')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('宽限天数（天）')).not.toBeInTheDocument();
  });

  it('缺 validity_key 的替代策略被客户端阻止', async () => {
    const onUpdateSource = renderAdmin();
    const user = await openForm();

    await user.selectOptions(screen.getByLabelText('有效期策略'), 'until_superseded');
    await user.click(screen.getByRole('button', {name: '保存配置'}));

    expect(screen.getByRole('alert')).toHaveTextContent('替代策略必须提供替代判定键（validity_key）');
    expect(onUpdateSource).not.toHaveBeenCalled();
  });

  it('声明按完整快照缺失自动撤销却缺 authoritative_full_snapshot 被客户端阻止', async () => {
    const onUpdateSource = renderAdmin();
    const user = await openForm();

    await user.selectOptions(screen.getByLabelText('有效期策略'), 'until_revoked');
    await user.click(screen.getByLabelText('按完整快照缺失自动撤销（名单类信源）'));
    await user.click(screen.getByRole('button', {name: '保存配置'}));

    expect(screen.getByRole('alert')).toHaveTextContent('必须同时声明权威完整快照');
    expect(onUpdateSource).not.toHaveBeenCalled();
  });

  it('非名单显式 revoke 策略不要求快照声明', async () => {
    const onUpdateSource = renderAdmin();
    const user = await openForm();

    await user.selectOptions(screen.getByLabelText('有效期策略'), 'until_revoked');
    await user.click(screen.getByRole('button', {name: '保存配置'}));

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(onUpdateSource).toHaveBeenCalledWith(
      '17',
      expect.objectContaining({validity_policy: {mode: 'until_revoked', review_required: true}}),
    );
  });

  it('冲突旧字段被客户端阻止且服务端错误以 role=alert 呈现', async () => {
    const onUpdateSource = renderAdmin({
      signalValidityDays: 30,
      validityPolicy: {mode: 'until_revoked'},
    });
    await openForm();

    expect(screen.getByRole('alert')).toHaveTextContent('signal_validity_days 与 validity_policy 配置冲突');
    expect(onUpdateSource).not.toHaveBeenCalled();
  });
});

describe('数据源健康新鲜度（任务8 只读诊断）', () => {
  it('诊断 ok：来源行显示最近成功与下次预期时间', () => {
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[source]}
          role="viewer"
          onUpdateSource={vi.fn()}
          onRefreshSources={vi.fn()}
          monitoringHealth={readyHealth}
        />
      </MemoryRouter>,
    );

    const cell = screen.getByTestId('source-health-17');
    expect(cell.textContent).toContain('采集正常');
    expect(cell.textContent).toContain(`最近成功 ${expectedTime('2026-09-11T05:30:00Z')}`);
    expect(cell.textContent).toContain(`下次预期 ${expectedTime('2026-09-11T06:00:00Z')}`);
  });

  it('failed 来源显示失败标签与脱敏原因，不显示「采集正常」', () => {
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[source]}
          role="viewer"
          onUpdateSource={vi.fn()}
          onRefreshSources={vi.fn()}
          monitoringHealth={monitoringHealthWith({
            ...healthySource,
            state: 'failed',
            reason_code: 'last_attempt_failed',
            next_expected_at: null,
          })}
        />
      </MemoryRouter>,
    );

    const cell = screen.getByTestId('source-health-17');
    expect(cell.textContent).toContain('采集失败');
    expect(cell.textContent).toContain('last_attempt_failed');
    expect(cell.textContent).not.toContain('采集正常');
  });

  it('停用与按需来源不出现红色故障标签', () => {
    const view = render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[source]}
          role="viewer"
          onUpdateSource={vi.fn()}
          onRefreshSources={vi.fn()}
          monitoringHealth={monitoringHealthWith({
            ...healthySource,
            state: 'disabled',
            reason_code: 'disabled',
            next_expected_at: null,
          })}
        />
      </MemoryRouter>,
    );

    const cell = screen.getByTestId('source-health-17');
    expect(cell.textContent).toContain('已停用');
    expect(screen.queryByText('采集失败')).not.toBeInTheDocument();
    expect(screen.queryByText('已超期')).not.toBeInTheDocument();
    view.unmount();

    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[source]}
          role="viewer"
          onUpdateSource={vi.fn()}
          onRefreshSources={vi.fn()}
          monitoringHealth={monitoringHealthWith({
            ...healthySource,
            state: 'on_demand',
            reason_code: 'on_demand',
            next_expected_at: null,
          })}
        />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('source-health-17').textContent).toContain('按需核查');
  });

  it('诊断 503（unknown/hidden）不渲染任何新鲜度占位，列表照常展示', () => {
    const view = render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[source]}
          role="viewer"
          onUpdateSource={vi.fn()}
          onRefreshSources={vi.fn()}
          monitoringHealth={{status: 'unknown'}}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', {name: '官方风险源 有效记录 7 条'})).toBeInTheDocument();
    expect(screen.queryByTestId(/^source-health-/)).not.toBeInTheDocument();
    view.unmount();

    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[source]}
          role="viewer"
          onUpdateSource={vi.fn()}
          onRefreshSources={vi.fn()}
          monitoringHealth={{status: 'hidden'}}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId(/^source-health-/)).not.toBeInTheDocument();
  });
});

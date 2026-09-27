import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {api, type MonitoringHealthRead, type TycBatchRunResult} from '../api';
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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

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

  it('有效期策略是独立列：表头与专属单元承载策略文字', () => {
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

    expect(screen.getByText('有效期策略')).toBeInTheDocument();
    expect(screen.getByTestId('source-validity-policy-17')).toHaveTextContent('固定天数 30 天');
  });

  it('无有效期策略时不渲染移动端“有效期:”悬空前缀', () => {
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[{
            ...source,
            validityPolicy: null,
            validityMode: null,
            signalValidityDays: null,
          }]}
          role="viewer"
          onUpdateSource={vi.fn()}
          onRefreshSources={vi.fn()}
          monitoringHealth={readyHealth}
        />
      </MemoryRouter>,
    );

    const policyCell = screen.getByTestId('source-validity-policy-17');
    expect(policyCell).not.toHaveTextContent('有效期:');
    expect(policyCell.textContent).toBe('');
  });

  it('记录数单元只承载有效/累计数，不再混入有效期策略文字', () => {
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

    const recordCell = screen.getByTestId('source-record-count-17');
    expect(within(recordCell).getByTestId('source-valid-17')).toHaveTextContent('7');
    expect(within(recordCell).getByTestId('source-total-17')).toHaveTextContent('25');
    expect(recordCell).not.toHaveTextContent('固定天数 30 天');
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
    await user.click(screen.getByRole('button', {name: /编辑/}));
    return user;
  };

  it('旧 signal_validity_days 映射为 fixed_days 模式并回填天数', async () => {
    renderAdmin();
    await openForm();

    expect(screen.getByLabelText('有效期策略')).toHaveValue('fixed_days');
    expect(screen.getByLabelText('固定天数（天）')).toHaveValue(30);
  });

  it('天眼查编辑表单展示按需调用与每日批量核查策略', async () => {
    renderAdmin({
      code: 'tianyancha',
      name: '天眼查企业核查',
      type: 'external_tool',
      schedule: null,
      enabled: false,
    });
    await openForm();

    expect(screen.getByText(/人工查询按需调用；Scheduler 不按 cron 刷新/)).toBeInTheDocument();
    expect(screen.getByText(/每天北京时间 06:00 批量核查已启用供应商/)).toBeInTheDocument();
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

describe('数据源列表信息架构与操作区', () => {
  const renderView = (
    dataSources: DataSource[],
    role: 'viewer' | 'admin' = 'viewer',
    monitoringHealth: MonitoringHealthSnapshot = {status: 'hidden'},
  ) => render(
    <MemoryRouter>
      <DataSourcesView
        dataSources={dataSources}
        role={role}
        onUpdateSource={vi.fn()}
        onRefreshSources={vi.fn().mockResolvedValue(undefined)}
        monitoringHealth={monitoringHealth}
      />
    </MemoryRouter>,
  );

  const tianyancha: DataSource = {
    ...source,
    id: '23',
    code: 'tianyancha',
    name: '天眼查企业核查（按需核查）',
    type: 'external_tool',
    schedule: null,
    enabled: false,
    status: 'disabled',
    latency: '已停用',
    // 真实运行时 /api/v1/sources/admin 返回 adapter_status=unconfigured：声明式适配器
    // 不适用于天眼查这类 external_tool，旧 fixture 错设为 builtin 掩盖了误禁用缺陷。
    adapterStatus: 'unconfigured',
    accessLastHttpStatus: null,
    apiKeyConfigured: true,
    apiKeyHint: 'tyc_••••1234',
    lastSyncTime: '按需调用',
  };

  it('分类标签使用中文业务类别，未识别编码回退中文兜底', () => {
    renderView([
      {...source, id: '1', code: 'tianyancha', type: 'external_tool'},
      {...source, id: '2', code: 'nmc-weather'},
      {...source, id: '3', code: 'ofac-sdn'},
      {...source, id: '4', code: 'uflpa-entity-list'},
      {...source, id: '5', code: 'bis-entity-list'},
      {...source, id: '6', code: 'commodity-futures'},
      {...source, id: '7', code: 'pbc-lpr'},
      {...source, id: '8', code: 'wto-news'},
      {...source, id: '9', code: 'unknown-code-x'},
      {...source, id: '10', code: 'usgs-earthquake-day'},
      {...source, id: '11', code: 'mem-incident-bulletin'},
    ]);

    expect(screen.getByTestId('source-category-1')).toHaveTextContent('主体核查');
    expect(screen.getByTestId('source-category-2')).toHaveTextContent('天气预警');
    expect(screen.getByTestId('source-category-3')).toHaveTextContent('制裁合规');
    expect(screen.getByTestId('source-category-4')).toHaveTextContent('制裁合规');
    expect(screen.getByTestId('source-category-5')).toHaveTextContent('制裁合规');
    expect(screen.getByTestId('source-category-6')).toHaveTextContent('宏观市场');
    expect(screen.getByTestId('source-category-7')).toHaveTextContent('宏观市场');
    expect(screen.getByTestId('source-category-8')).toHaveTextContent('政策法规');
    expect(screen.getByTestId('source-category-9')).toHaveTextContent('官方接口');
    // USGS 地震速报（source_type=official_api）须命中 code 关键词规则，不再回退“官方接口”。
    expect(screen.getByTestId('source-category-10')).toHaveTextContent('自然灾害');
    // 规则顺序回归：自然灾害规则插入后，mem- 编码仍命中政策法规规则。
    expect(screen.getByTestId('source-category-11')).toHaveTextContent('政策法规');
  });

  it('记录数列标题为“记录数（有效/累计）”，两个数字分别链接到对应范围', () => {
    renderView([{...source, validSignalCount: 7, totalSignalCount: 25}]);

    expect(
      screen.getByText((_content, element) => element?.textContent?.replace(/\s+/g, '') === '记录数（有效/累计）'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('source-valid-17')).toHaveAttribute('href', '/sources/17/signals?scope=valid&page=1');
    expect(screen.getByTestId('source-total-17')).toHaveAttribute('href', '/sources/17/signals?scope=all&page=1');
  });

  // 768px CJK 回归：表头曾把「累计」从中间断开。要求按两个语义短语组织，
  // 括号短语整体 nowrap，同时表头整体仍可作为一个短语通过文本查询。
  it('记录数列标题把「记录数」与「（有效/累计）」拆为两个语义短语，括号短语整段不拆字', () => {
    renderView([source]);

    expect(screen.getByText('记录数')).toBeInTheDocument();
    expect(screen.getByText('（有效/累计）')).toHaveClass('whitespace-nowrap');
    expect(
      screen.getByText((_content, element) => element?.textContent?.replace(/\s+/g, '') === '记录数（有效/累计）'),
    ).toBeInTheDocument();
  });

  it('有效数与累计数相等时两个数字仍分别可点击', () => {
    renderView([{...source, validSignalCount: 25, totalSignalCount: 25}]);

    expect(screen.getByTestId('source-valid-17')).toHaveTextContent('25');
    expect(screen.getByTestId('source-total-17')).toHaveTextContent('25');
  });

  it('列表不再重复展示最近同步时间，状态只保留在新鲜度标签中', () => {
    renderView([{...source, lastSyncTime: '2026-09-01 09:00', latency: '运行正常'}], 'viewer', readyHealth);

    expect(screen.queryByText('最近同步时间')).not.toBeInTheDocument();
    expect(screen.queryByText('2026-09-01 09:00')).not.toBeInTheDocument();
    expect(screen.queryByText('运行正常')).not.toBeInTheDocument();
    expect(screen.getByTestId('source-connectivity-17')).toHaveTextContent('连通正常');
    expect(screen.getByTestId('source-health-17')).toHaveTextContent('采集正常');
  });

  it('列表表头为「连通状态」，与名称下方的采集新鲜度区分职责', () => {
    renderView([{...source, enabled: true}]);

    expect(screen.getByText('连通状态')).toBeInTheDocument();
    expect(screen.queryByText('采集方式')).not.toBeInTheDocument();
  });

  it('普通已启用联网来源显示连通正常，普通停用来源显示已停用', () => {
    renderView([
      {...source, enabled: true},
      {...source, id: '18', enabled: false, status: 'disabled', latency: '已停用'},
    ]);

    expect(screen.getByTestId('source-connectivity-17')).toHaveTextContent('连通正常');
    expect(screen.getByTestId('source-connectivity-18')).toHaveTextContent('已停用');
  });

  it('访问错误显示连通异常，未配置地址显示未配置', () => {
    renderView([
      {...source, id: '17', accessLastErrorKind: 'upstream_error', accessLastHttpStatus: 503},
      {...source, id: '18', endpointUrl: null, accessLastHttpStatus: null, accessLastErrorKind: null},
    ]);

    expect(screen.getByTestId('source-connectivity-17')).toHaveTextContent('连通异常');
    expect(screen.getByTestId('source-connectivity-18')).toHaveTextContent('未配置');
  });

  it('全网累计记录数取已入库累计信号数而非最近一次新增数', () => {
    renderView([
      {...source, id: '17', itemCount: 2, totalSignalCount: 25},
      {...source, id: '18', itemCount: 40, totalSignalCount: 100},
    ]);

    const summary = screen.getByText('全网累计记录数').parentElement;
    expect(summary).toHaveTextContent('125');
    expect(summary).not.toHaveTextContent('42');
  });

  it('来源名称展示去掉括号及括号内容，编码与 ID 保持不变', () => {
    renderView([tianyancha]);

    expect(screen.getByTestId('source-name-23')).toHaveTextContent('天眼查企业核查');
    expect(screen.getByTestId('source-name-23').textContent).not.toContain('按需核查');
    expect(screen.getByText('tianyancha')).toBeInTheDocument();
  });

  // 768px CJK 回归：名称与类别挤在同一不可换行 flex 行，名称被压到 36px 全部截断。
  // 要求名称标题行可换行，且名称节点以 title 暴露完整名称以兜底截断。
  it('名称标题行可换行且名称节点以 title 暴露完整名称', () => {
    renderView([source]);

    const nameNode = screen.getByTestId('source-name-17');
    expect(nameNode).toHaveAttribute('title', '官方风险源');
    expect(nameNode.parentElement).toHaveClass('flex-wrap');
  });

  it('操作按刷新、编辑、停用/启用排列，按钮均带明确 aria-label 与 title', () => {
    renderView([{...source, enabled: true}], 'admin');

    const actions = screen.getByTestId('source-actions-17');
    const buttons = within(actions).getAllByRole('button');
    expect(buttons.map((button) => button.getAttribute('aria-label'))).toEqual([
      '刷新官方风险源',
      '编辑官方风险源',
      '停用官方风险源',
    ]);
    expect(within(actions).getByRole('button', {name: '刷新官方风险源'})).toBeEnabled();
    expect(within(actions).getByRole('button', {name: '刷新官方风险源'})).toHaveAttribute('title', '立即触发一次采集');
    expect(within(actions).getByRole('button', {name: '停用官方风险源'})).toHaveAttribute('title', '停用该数据源，停止自动采集');
  });

  it('已启用且可拉取的来源点击刷新调用 api.runSource 并反馈结果', async () => {
    const runSource = vi.spyOn(api, 'runSource').mockResolvedValue({
      id: 1,
      source_id: 17,
      started_at: '2026-09-11T06:00:00Z',
      finished_at: '2026-09-11T06:00:05Z',
      status: 'succeeded',
      fetched_count: 5,
      created_count: 3,
      duplicate_count: 2,
      error: null,
    });
    const onRefreshSources = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[source]}
          role="admin"
          onUpdateSource={vi.fn()}
          onRefreshSources={onRefreshSources}
          monitoringHealth={{status: 'hidden'}}
        />
      </MemoryRouter>,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', {name: '刷新官方风险源'}));

    expect(runSource).toHaveBeenCalledWith(17);
    expect(await screen.findByTestId('source-run-msg-17')).toHaveTextContent('刷新完成，新增 3 条记录');
    expect(onRefreshSources).toHaveBeenCalled();
  });

  it('刷新失败时按行反馈业务化错误，不触发列表刷新', async () => {
    vi.spyOn(api, 'runSource').mockRejectedValue(new Error('数据源已停用'));
    const onRefreshSources = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[source]}
          role="admin"
          onUpdateSource={vi.fn()}
          onRefreshSources={onRefreshSources}
          monitoringHealth={{status: 'hidden'}}
        />
      </MemoryRouter>,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', {name: '刷新官方风险源'}));

    expect(await screen.findByTestId('source-run-msg-17')).toHaveTextContent('数据源已停用');
    expect(onRefreshSources).not.toHaveBeenCalled();
  });

  it('仅启用中的天眼查行内核查可用，其他外部工具与人工录入来源仍禁用', async () => {
    const runSource = vi.spyOn(api, 'runSource');
    renderView([
      {...tianyancha, enabled: true, status: 'normal', latency: '按需核查可用'},
      {...source, id: '18', code: 'manual-json', name: '手工 JSON 导入', type: 'manual'},
      {...source, id: '19', code: 'other-tool', name: '其他外部核查工具', type: 'external_tool'},
    ], 'admin');

    const tycButton = screen.getByRole('button', {name: '刷新天眼查企业核查'});
    expect(tycButton).toBeEnabled();
    expect(tycButton).toHaveAttribute('title', '立即发起一次批量主体核查');

    const manualButton = screen.getByRole('button', {name: '刷新手工 JSON 导入'});
    expect(manualButton).toBeDisabled();
    expect(manualButton).toHaveAttribute('title', '人工录入数据源不支持刷新');

    const otherToolButton = screen.getByRole('button', {name: '刷新其他外部核查工具'});
    expect(otherToolButton).toBeDisabled();
    expect(otherToolButton).toHaveAttribute('title', '外部核查工具按需调用，不支持页面刷新');

    const user = userEvent.setup();
    await user.click(manualButton);
    await user.click(otherToolButton);
    expect(runSource).not.toHaveBeenCalled();
  });

  // 缺陷回归：真实 /api/v1/sources/admin 返回天眼查 source_type=external_tool、
  // adapter_status=unconfigured、enabled=true。旧逻辑在豁免外部工具后，又被通用
  // adapterStatus 检查二次禁用，刷新按钮被错误置灰（title: 该来源尚未完成适配器配置）。
  it('已启用、未配置声明式适配器的天眼查仍可刷新，普通拉取来源仍要求已发布适配器', () => {
    renderView([
      {...tianyancha, adapterStatus: 'unconfigured', enabled: true, status: 'normal', latency: '按需核查可用'},
      {...source, id: '24', code: 'official-unconfigured', name: '未配置适配器接口来源', adapterStatus: 'unconfigured'},
    ], 'admin');

    const tycButton = screen.getByRole('button', {name: '刷新天眼查企业核查'});
    expect(tycButton).toBeEnabled();
    expect(tycButton).toHaveAttribute('title', '立即发起一次批量主体核查');

    const apiButton = screen.getByRole('button', {name: '刷新未配置适配器接口来源'});
    expect(apiButton).toBeDisabled();
    expect(apiButton).toHaveAttribute('title', '该来源尚未完成适配器配置，暂不可刷新');
  });

  it('天眼查核查失败时按行反馈错误且不触发列表刷新', async () => {
    vi.spyOn(api, 'runTycBatch').mockRejectedValue(new Error('运行密钥未配置'));
    const onRefreshSources = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[{...tianyancha, enabled: true, status: 'normal', latency: '按需核查可用'}]}
          role="admin"
          onUpdateSource={vi.fn()}
          onRefreshSources={onRefreshSources}
          monitoringHealth={{status: 'hidden'}}
        />
      </MemoryRouter>,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', {name: '刷新天眼查企业核查'}));

    expect(await screen.findByTestId('source-run-msg-23')).toHaveTextContent('运行密钥未配置');
    expect(onRefreshSources).not.toHaveBeenCalled();
  });

  it('未启用的天眼查核查按钮仍禁用并说明启用后可用', () => {
    renderView([tianyancha], 'admin');

    const tycButton = screen.getByRole('button', {name: '刷新天眼查企业核查'});
    expect(tycButton).toBeDisabled();
    expect(tycButton).toHaveAttribute('title', '数据源已停用，启用后可刷新');
  });

  it('天眼查核查调用 api.runTycBatch 并展示目标/已尝试/新增/重复/空结果/失败汇总', async () => {
    const runTycBatch = vi.spyOn(api, 'runTycBatch').mockResolvedValue({
      source_id: 23,
      targeted_count: 12,
      attempted_count: 10,
      created_count: 3,
      duplicate_count: 4,
      empty_count: 2,
      failed_count: 1,
      quota_exhausted: false,
    });
    const runSource = vi.spyOn(api, 'runSource');
    const onRefreshSources = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[{...tianyancha, enabled: true, status: 'normal', latency: '按需核查可用'}]}
          role="admin"
          onUpdateSource={vi.fn()}
          onRefreshSources={onRefreshSources}
          monitoringHealth={{status: 'hidden'}}
        />
      </MemoryRouter>,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', {name: '刷新天眼查企业核查'}));

    expect(runTycBatch).toHaveBeenCalledWith(23);
    expect(runSource).not.toHaveBeenCalled();
    expect(await screen.findByTestId('source-run-msg-23')).toHaveTextContent(
      '核查完成：目标 12 家，已尝试 10 家，新增 3 条，重复 4 条，空结果 2 条，失败 1 条',
    );
    expect(onRefreshSources).toHaveBeenCalled();
  });

  it('天眼查额度耗尽时汇总追加额度耗尽信息', async () => {
    vi.spyOn(api, 'runTycBatch').mockResolvedValue({
      source_id: 23,
      targeted_count: 12,
      attempted_count: 2,
      created_count: 0,
      duplicate_count: 1,
      empty_count: 1,
      failed_count: 0,
      quota_exhausted: true,
    });
    renderView([{...tianyancha, enabled: true, status: 'normal', latency: '按需核查可用'}], 'admin');

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', {name: '刷新天眼查企业核查'}));

    expect(await screen.findByTestId('source-run-msg-23')).toHaveTextContent(
      '核查完成：目标 12 家，已尝试 2 家，新增 0 条，重复 1 条，空结果 1 条，失败 0 条；本次调用额度已耗尽',
    );
  });

  it('天眼查核查期间显示「核查中…」并锁定其他来源刷新，完成后恢复可用', async () => {
    let resolveBatch: (value: TycBatchRunResult) => void = () => undefined;
    vi.spyOn(api, 'runTycBatch').mockImplementation(() => new Promise((resolve) => {
      resolveBatch = resolve;
    }));
    renderView([
      {...tianyancha, enabled: true, status: 'normal', latency: '按需核查可用'},
      {...source, enabled: true},
    ], 'admin');

    const user = userEvent.setup();
    const tycButton = screen.getByRole('button', {name: '刷新天眼查企业核查'});
    await user.click(tycButton);

    expect(tycButton).toBeDisabled();
    expect(tycButton).toHaveTextContent('核查中…');
    expect(tycButton).toHaveAttribute('title', '正在核查，请稍候');
    const otherButton = screen.getByRole('button', {name: '刷新官方风险源'});
    expect(otherButton).toBeDisabled();
    expect(otherButton).toHaveAttribute('title', '正在刷新其他数据源，请稍候');

    resolveBatch({
      source_id: 23,
      targeted_count: 0,
      attempted_count: 0,
      created_count: 0,
      duplicate_count: 0,
      empty_count: 0,
      failed_count: 0,
      quota_exhausted: false,
    });
    expect(await screen.findByTestId('source-run-msg-23')).toBeInTheDocument();
    await waitFor(() => expect(tycButton).toBeEnabled());
    expect(otherButton).toBeEnabled();
  });

  it('未启用来源的刷新按钮为禁用态并说明启用后可用', () => {
    renderView([{...source, enabled: false, status: 'disabled', latency: '已停用'}], 'admin');

    const refreshButton = screen.getByRole('button', {name: '刷新官方风险源'});
    expect(refreshButton).toBeDisabled();
    expect(refreshButton).toHaveAttribute('title', '数据源已停用，启用后可刷新');
  });

  it('天眼查停用时展示按需核查而非已停用，最近核查为真实时间且无下次预期', () => {
    renderView([tianyancha], 'viewer', monitoringHealthWith({
      ...healthySource,
      source_id: 23,
      code: 'tianyancha',
      name: '天眼查企业核查（按需核查）',
      state: 'disabled',
      reason_code: 'disabled',
      next_expected_at: null,
      last_success_at: null,
      last_attempt_at: '2026-09-10T03:00:00Z',
    }));

    const connectivity = screen.getByTestId('source-connectivity-23');
    expect(connectivity).toHaveTextContent('按需核查');
    expect(connectivity).toHaveAttribute(
      'title',
      '外部核查工具：运行密钥已配置，支持页面手动批量核查全部启用供应商；每天北京时间 06:00 仍自动批量核查',
    );

    const health = screen.getByTestId('source-health-23');
    expect(health).toHaveTextContent('按需核查');
    expect(health).toHaveTextContent(`最近核查 ${expectedTime('2026-09-10T03:00:00Z')}`);
    expect(health).not.toHaveTextContent('下次预期');
    expect(screen.queryByText('已停用')).not.toBeInTheDocument();
  });

  it('天眼查密钥未配置时提示先配置运行密钥，不再宣称不支持页面刷新', () => {
    renderView([{...tianyancha, apiKeyConfigured: false, apiKeyHint: null}], 'viewer');

    const connectivity = screen.getByTestId('source-connectivity-23');
    expect(connectivity).toHaveAttribute(
      'title',
      '外部核查工具：运行密钥未配置，请先在编辑中配置运行密钥后再发起批量核查',
    );
    expect(connectivity.getAttribute('title')).not.toContain('不支持页面刷新');
  });

  it('其他外部核查工具的悬浮说明保持「不支持页面刷新」', () => {
    renderView([
      {...source, id: '31', code: 'other-tool', name: '其他外部核查工具', type: 'external_tool', apiKeyConfigured: true},
      {...source, id: '32', code: 'other-tool-b', name: '其他外部核查工具B', type: 'external_tool', apiKeyConfigured: false},
    ], 'viewer');

    expect(screen.getByTestId('source-connectivity-31')).toHaveAttribute(
      'title',
      '外部核查工具：运行密钥已配置，按需发起查询，不支持页面刷新',
    );
    expect(screen.getByTestId('source-connectivity-32')).toHaveAttribute(
      'title',
      '外部核查工具：运行密钥未配置，请先在编辑中配置，不支持页面刷新',
    );
  });

  it('连通标签悬浮说明使用通俗文本，不向用户暴露 HTTP 状态码', () => {
    renderView([{...source, accessLastHttpStatus: 200}], 'viewer');

    expect(screen.getByTestId('source-connectivity-17')).toHaveAttribute('title', '最近一次采集请求成功，接口连通正常');
    expect(screen.queryByText(/HTTP\s*\d+/)).not.toBeInTheDocument();
  });

  it('采集失败时悬浮说明给出通俗原因，不展示状态码', () => {
    renderView([{...source, accessLastHttpStatus: 503, accessLastErrorKind: 'upstream_error'}], 'viewer');

    expect(screen.getByTestId('source-connectivity-17')).toHaveAttribute('title', '目标网站暂时不可用，系统稍后会自动重试');
    expect(screen.queryByText(/HTTP\s*\d+/)).not.toBeInTheDocument();
  });
});

// 缺陷回归：连通标签文案取自访问语义，但徽标配色曾错误沿用采集状态映射，
// 导致「连通正常」被染成采集失败的红色（或「连通异常」被染成绿色）误导用户。
describe('数据源连通徽标配色与采集状态解耦', () => {
  const renderView = (dataSources: DataSource[], role: 'viewer' | 'admin' = 'viewer') => render(
    <MemoryRouter>
      <DataSourcesView
        dataSources={dataSources}
        role={role}
        onUpdateSource={vi.fn()}
        onRefreshSources={vi.fn().mockResolvedValue(undefined)}
        monitoringHealth={{status: 'hidden'}}
      />
    </MemoryRouter>,
  );

  const connectivityBadge = (id: string) => screen.getByTestId(`source-connectivity-${id}`);
  const connectivityDot = (id: string) => screen.getByTestId(`source-connectivity-dot-${id}`);
  const connectivityRing = (id: string) => screen.getByTestId(`source-connectivity-ring-${id}`);

  it('采集失败但访问就绪的来源显示成功绿徽标，行与图标仍保持采集状态的红色', () => {
    renderView([{
      ...source,
      status: 'error',
      latency: '采集失败',
      accessStatus: 'ready',
      accessLastHttpStatus: 200,
      accessLastErrorKind: null,
    }]);

    expect(connectivityBadge('17')).toHaveTextContent('连通正常');
    expect(connectivityBadge('17')).toHaveClass('bg-emerald-100', 'text-emerald-800');
    expect(connectivityDot('17')).toHaveClass('bg-emerald-600');
    expect(connectivityRing('17')).toHaveClass('bg-emerald-500/60');

    expect(screen.getByRole('listitem')).toHaveClass('bg-red-50/30');
    expect(screen.getByTestId('source-icon-17')).toHaveClass('bg-red-100');
  });

  it('采集正常但访问异常的来源显示警示红徽标，图标仍保持采集状态的正常色', () => {
    renderView([{
      ...source,
      status: 'normal',
      latency: '运行正常',
      accessStatus: 'ready',
      accessLastHttpStatus: 503,
      accessLastErrorKind: 'upstream_error',
    }]);

    expect(connectivityBadge('17')).toHaveTextContent('连通异常');
    expect(connectivityBadge('17')).toHaveClass('bg-red-100', 'text-[#ba1a1a]');
    expect(connectivityDot('17')).toHaveClass('bg-[#ba1a1a]');
    expect(connectivityRing('17')).toHaveClass('bg-red-500/60');

    expect(screen.getByTestId('source-icon-17')).toHaveClass('bg-[#ecf4ff]');
  });

  it('访问冷却与间隔保护使用警示橙，请求执行中使用进行中蓝', () => {
    renderView([
      {...source, id: '1', accessStatus: 'cooldown', latency: '冷却中'},
      {...source, id: '2', accessStatus: 'busy', latency: '执行中'},
      {...source, id: '3', accessStatus: 'throttled', latency: '限速中'},
    ]);

    expect(connectivityBadge('1')).toHaveTextContent('访问冷却中');
    expect(connectivityBadge('1')).toHaveClass('bg-orange-100', 'text-orange-700');
    expect(connectivityDot('1')).toHaveClass('bg-orange-600');

    expect(connectivityBadge('2')).toHaveTextContent('请求执行中');
    expect(connectivityBadge('2')).toHaveClass('bg-blue-100', 'text-blue-700');
    expect(connectivityDot('2')).toHaveClass('bg-blue-600');

    expect(connectivityBadge('3')).toHaveTextContent('间隔保护中');
    expect(connectivityBadge('3')).toHaveClass('bg-orange-100', 'text-orange-700');
    expect(connectivityDot('3')).toHaveClass('bg-orange-600');
  });

  it('停用、按需核查、人工录入与未配置来源使用中性灰徽标而非故障色', () => {
    renderView([
      {...source, id: '1', enabled: false, status: 'disabled', latency: '已停用'},
      {...source, id: '2', code: 'tianyancha', name: '天眼查企业核查', type: 'external_tool', enabled: false, status: 'disabled', latency: '已停用'},
      {...source, id: '3', code: 'manual-json', name: '手工 JSON 导入', type: 'manual', enabled: false, status: 'disabled', latency: '已停用'},
      {...source, id: '4', endpointUrl: null, accessLastHttpStatus: null, accessLastErrorKind: null},
    ]);

    expect(connectivityBadge('1')).toHaveTextContent('已停用');
    expect(connectivityBadge('2')).toHaveTextContent('按需核查');
    expect(connectivityBadge('3')).toHaveTextContent('人工录入');
    expect(connectivityBadge('4')).toHaveTextContent('未配置');

    for (const id of ['1', '2', '3', '4']) {
      expect(connectivityBadge(id)).toHaveClass('bg-slate-200', 'text-slate-700');
      expect(connectivityDot(id)).toHaveClass('bg-slate-600');
    }
    expect(connectivityBadge('1')).not.toHaveClass('bg-red-100');
    expect(connectivityBadge('1')).not.toHaveClass('bg-orange-100');
  });

  it('连通徽标为不可拆分的 nowrap 胶囊，375px 下「连通异常」等 CJK 标签不拆字换行', () => {
    renderView([{...source, accessStatus: 'ready', accessLastHttpStatus: 503, accessLastErrorKind: 'upstream_error'}]);

    expect(connectivityBadge('17')).toHaveTextContent('连通异常');
    expect(connectivityBadge('17')).toHaveClass('whitespace-nowrap');
  });
});

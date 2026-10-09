import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {api, type CollectionRunRead, type DataSourceWritePayload, type MonitoringHealthRead} from '../api';
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

const monitoringHealthWith = (
  source: MonitoringHealthRead['sources'][number],
  schedulerOverrides: Partial<MonitoringHealthRead['scheduler']> = {},
): MonitoringHealthSnapshot => ({
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
      current_work: [],
      scheduled_jobs: [],
      recent_runs: [],
      ...schedulerOverrides,
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
  vi.unstubAllGlobals();
});

describe('信息源采集记录入口', () => {
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

  it('有效期策略值及移动端前缀使用可读的次级文字样式，且不暴露内部策略版本 title', () => {
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

    const policyValue = screen.getByText('固定天数 30 天');
    expect(policyValue).toHaveClass(
      'whitespace-nowrap',
      'text-xs',
      'font-semibold',
      'text-[#424751]',
      'dark:text-slate-400',
    );
    expect(policyValue).not.toHaveAttribute('title');

    const policyPrefix = screen.getByText('有效期:');
    expect(policyPrefix).toHaveClass(
      'md:hidden',
      'whitespace-nowrap',
      'text-xs',
      'font-sans',
      'font-semibold',
      'text-[#424751]',
      'dark:text-slate-400',
      'mr-0.5',
    );
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

describe('信息源有效期策略表单', () => {
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

  it('天眼查编辑表单展示按供应商调用方式与周度分片核查策略', async () => {
    renderAdmin({
      code: 'tianyancha',
      name: '天眼查企业核查',
      type: 'external_tool',
      schedule: null,
      enabled: false,
    });
    await openForm();

    expect(screen.getByText(/人工核查通过页面按供应商调用；Scheduler 不按 cron 刷新/)).toBeInTheDocument();
    expect(screen.getByText(/每周日、周一各覆盖一个分片自动核查已启用供应商/)).toBeInTheDocument();
    expect(screen.queryByText(/每天北京时间 06:00/)).not.toBeInTheDocument();
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
    expect(screen.getByLabelText('固定天数（天）')).toBeInTheDocument();
    expect(screen.queryByLabelText(/替代判定键/)).not.toBeInTheDocument();

    await user.selectOptions(modeSelect, 'until_revoked');
    expect(screen.getByLabelText('按完整快照缺失自动撤销（名单类信源）')).toBeInTheDocument();

    await user.selectOptions(modeSelect, 'indefinite');
    expect(screen.queryByLabelText('固定天数（天）')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('宽限天数（天）')).not.toBeInTheDocument();
  });

  it('until_superseded 回填固定天数后直接保存，不再要求替代判定键', async () => {
    const onUpdateSource = renderAdmin();
    const user = await openForm();

    await user.selectOptions(screen.getByLabelText('有效期策略'), 'until_superseded');
    expect(screen.queryByLabelText(/替代判定键/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: '保存配置'}));

    await waitFor(() => expect(onUpdateSource).toHaveBeenCalledTimes(1));
    expect(onUpdateSource).toHaveBeenCalledWith(
      '17',
      expect.objectContaining({validity_policy: {mode: 'until_superseded', fixed_days: 30}}),
    );
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
      expect.objectContaining({validity_policy: {mode: 'until_revoked'}}),
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

// 计划 W1-T2：移除信源级「复核天数」「需要复核」，策略 payload 与前端类型不再含 review 键。
describe('信息源编辑表单：移除信源级复核配置（W1-T2）', () => {
  const renderAdmin = (overrides: Partial<DataSource> = {}) => {
    const onUpdateSource = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[{...source, ...overrides}]}
          role="admin"
          onUpdateSource={onUpdateSource}
          onRefreshSources={vi.fn().mockResolvedValue(undefined)}

          monitoringHealth={readyHealth}
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

  it('编辑弹窗不再渲染复核天数与需要复核', async () => {
    renderAdmin();
    await openForm();

    expect(screen.queryByLabelText('复核天数（天）')).not.toBeInTheDocument();
    expect(screen.queryByText('需要复核')).not.toBeInTheDocument();
  });

  // 失败场景：fixed_days 保存的策略对象只允许 mode + fixed_days 两个键。
  it('fixed_days 模式保存的 validity_policy 仅含 mode 与 fixed_days', async () => {
    const onUpdateSource = renderAdmin();
    const user = await openForm();

    await user.click(screen.getByRole('button', {name: '保存配置'}));
    await waitFor(() => expect(onUpdateSource).toHaveBeenCalledTimes(1));

    const payload: Partial<DataSourceWritePayload> = onUpdateSource.mock.calls[0]?.[1] ?? {};
    const policy = payload.validity_policy as unknown as Record<string, unknown>;
    expect(Object.keys(policy).sort()).toEqual(['fixed_days', 'mode']);
    expect(policy).toEqual({mode: 'fixed_days', fixed_days: 30});
  });

  it('event_end_plus_grace 模式保存的 validity_policy 不含复核键', async () => {
    const onUpdateSource = renderAdmin({
      signalValidityDays: null,
      validityPolicy: {mode: 'event_end_plus_grace', grace_days: 5},
    });
    const user = await openForm();

    await user.click(screen.getByRole('button', {name: '保存配置'}));
    await waitFor(() => expect(onUpdateSource).toHaveBeenCalledTimes(1));

    const payload: Partial<DataSourceWritePayload> = onUpdateSource.mock.calls[0]?.[1] ?? {};
    expect(payload.validity_policy).toEqual({mode: 'event_end_plus_grace', grace_days: 5});
  });
});

// 计划 W1-T3：移除声明式适配器 JSON 与 UI-only 替代判定键；until_superseded 以固定天数兜底并可保存。
describe('信息源编辑表单：移除声明式 JSON 与替代判定键（W1-T3）', () => {
  const renderAdmin = (overrides: Partial<DataSource> = {}) => {
    const onUpdateSource = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[{...source, ...overrides}]}
          role="admin"
          onUpdateSource={onUpdateSource}
          onRefreshSources={vi.fn().mockResolvedValue(undefined)}

          monitoringHealth={readyHealth}
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

  it('编辑弹窗不再渲染声明式适配器 JSON，保存 changes 不含 adapter_config', async () => {
    const onUpdateSource = renderAdmin();
    const user = await openForm();

    expect(screen.queryByText('声明式适配器 JSON')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: '保存配置'}));
    await waitFor(() => expect(onUpdateSource).toHaveBeenCalledTimes(1));

    const payload: Partial<DataSourceWritePayload> = onUpdateSource.mock.calls[0]?.[1] ?? {};
    expect('adapter_config' in payload).toBe(false);
  });

  // 失败场景：清空 until_superseded 的固定天数必须被客户端阻止保存。
  it('until_superseded 固定天数为空时提示必须提供固定天数并阻止保存', async () => {
    const onUpdateSource = renderAdmin();
    const user = await openForm();

    await user.selectOptions(screen.getByLabelText('有效期策略'), 'until_superseded');
    await user.clear(screen.getByLabelText('固定天数（天）'));
    await user.click(screen.getByRole('button', {name: '保存配置'}));

    expect(screen.getByRole('alert')).toHaveTextContent('替代时失效必须提供固定天数');
    expect(onUpdateSource).not.toHaveBeenCalled();
  });

  it('天眼查 until_superseded + fixed_days=30 可直接保存', async () => {
    const onUpdateSource = renderAdmin({
      id: '23',
      code: 'tianyancha',
      name: '天眼查企业核查',
      type: 'external_tool',
      schedule: null,
      enabled: false,
      adapterStatus: 'unconfigured',
      signalValidityDays: null,
      validityPolicy: {mode: 'until_superseded', fixed_days: 30},
    });
    const user = await openForm();

    expect(screen.getByLabelText('有效期策略')).toHaveValue('until_superseded');
    expect(screen.getByLabelText('固定天数（天）')).toHaveValue(30);

    await user.click(screen.getByRole('button', {name: '保存配置'}));
    await waitFor(() => expect(onUpdateSource).toHaveBeenCalledTimes(1));

    const payload: Partial<DataSourceWritePayload> = onUpdateSource.mock.calls[0]?.[1] ?? {};
    expect(payload.schedule).toBeNull();
    expect(payload.validity_policy).toEqual({mode: 'until_superseded', fixed_days: 30});
    expect('adapter_config' in payload).toBe(false);
  });
});

// 缺陷回归（计划 Todo 6）：编辑表单曾在构造 update payload 时解构丢弃 api_key，
// 导致信息源控制台保存运行密钥无效；留空保存则必须让后端收到"不含该键 = 保持不变"。
describe('信息源运行密钥提交契约', () => {
  const tycEditSource: DataSource = {
    ...source,
    id: '23',
    code: 'tianyancha',
    name: '天眼查企业核查',
    type: 'external_tool',
    schedule: null,
    enabled: false,
    status: 'disabled',
    latency: '已停用',
    adapterStatus: 'unconfigured',
    accessLastHttpStatus: null,
    apiKeyConfigured: true,
    apiKeyHint: 'tyc_••••1234',
  };

  const renderWithSerializedUpdate = () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 200, json: async () => ({})});
    vi.stubGlobal('fetch', fetchMock);
    const updateSourceSpy = vi.spyOn(api, 'updateSource');
    // spyOn 已把 api.updateSource 替换为 spy；这里再取一次引用以保留"可调用"的编译期类型。
    const updateSource = api.updateSource;
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[tycEditSource]}
          role="admin"
          onUpdateSource={async (id, payload) => {
            await updateSource(Number(id), payload);
          }}
          onRefreshSources={vi.fn().mockResolvedValue(undefined)}

          monitoringHealth={readyHealth}
        />
      </MemoryRouter>,
    );
    // 只认真正被 JSON.stringify 过的 PUT body：内存对象上 api_key 键始终存在（值可能为 undefined），
    // 只有序列化结果才能证明"留空 = 后端收不到该键"。
    const putBodies = () => (fetchMock.mock.calls as Array<[string, RequestInit]>)
      .filter(([path]) => path === '/api/v1/sources/23')
      .map(([, options]) => JSON.parse(String(options.body)) as Record<string, unknown>);
    return {updateSourceSpy, putBodies};
  };

  const openTycForm = async () => {
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', {name: '编辑天眼查企业核查'}));
    return user;
  };

  it('输入运行密钥保存时 api.updateSource 载荷与序列化 PUT body 均包含 api_key', async () => {
    const {updateSourceSpy, putBodies} = renderWithSerializedUpdate();
    const user = await openTycForm();

    await user.type(screen.getByLabelText(/运行密钥 API Key/), 'tyc_console_key_1234');
    await user.click(screen.getByRole('button', {name: '保存配置'}));

    await waitFor(() => expect(updateSourceSpy).toHaveBeenCalledTimes(1));
    expect(updateSourceSpy.mock.calls[0]?.[0]).toBe(23);
    expect(updateSourceSpy.mock.calls[0]?.[1]?.api_key).toBe('tyc_console_key_1234');

    expect(putBodies()).toHaveLength(1);
    expect(putBodies()[0]?.api_key).toBe('tyc_console_key_1234');
  });

  it('留空运行密钥保存时载荷值为 undefined 且序列化 PUT body 不含 api_key 键', async () => {
    const {updateSourceSpy, putBodies} = renderWithSerializedUpdate();
    const user = await openTycForm();

    await user.click(screen.getByRole('button', {name: '保存配置'}));

    await waitFor(() => expect(updateSourceSpy).toHaveBeenCalledTimes(1));
    // 禁止对内存对象断言 'api_key' in payload：该键始终存在，必须断言值为 undefined。
    expect(updateSourceSpy.mock.calls[0]?.[1]?.api_key).toBeUndefined();

    expect(putBodies()).toHaveLength(1);
    expect('api_key' in (putBodies()[0] ?? {})).toBe(false);
  });
});

// 计划 W1-T1：类型只读；认证方式仅保留无需认证/API Key Header；
// 历史认证值只读告警并在提交时省略 auth_type 键；请求头名合并 login_config 保全迁移 marker。
describe('信息源编辑表单：类型只读与认证方式精简（W1-T1）', () => {
  const renderAdmin = (overrides: Partial<DataSource> = {}) => {
    const onUpdateSource = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[{...source, ...overrides}]}
          role="admin"
          onUpdateSource={onUpdateSource}
          onRefreshSources={vi.fn().mockResolvedValue(undefined)}

          monitoringHealth={readyHealth}
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

  it('类型为只读展示且提交时原样携带 source_type', async () => {
    const onUpdateSource = renderAdmin();
    const user = await openForm();

    const typeInput = screen.getByLabelText('类型');
    expect(typeInput).toHaveValue('official_api');
    expect(typeInput).toBeDisabled();

    await user.click(screen.getByRole('button', {name: '保存配置'}));
    await waitFor(() => expect(onUpdateSource).toHaveBeenCalledTimes(1));
    expect(onUpdateSource.mock.calls[0]?.[1]?.source_type).toBe('official_api');
  });

  it('认证方式仅提供无需认证与 API Key Header，不出现 Bearer Token', async () => {
    renderAdmin();
    await openForm();

    const select = screen.getByLabelText('认证方式');
    expect(within(select).getAllByRole('option').map((option) => option.getAttribute('value'))).toEqual(['none', 'api_key']);
    expect(screen.queryByText('Bearer Token')).not.toBeInTheDocument();
  });

  it('凭据引用与请求头名仅在非外部工具的 API Key Header 认证下渲染', async () => {
    renderAdmin();
    const user = await openForm();

    expect(screen.queryByLabelText('凭据引用')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('API Key 请求头名')).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('认证方式'), 'api_key');
    expect(screen.getByLabelText('凭据引用')).toBeInTheDocument();
    expect(screen.getByLabelText('API Key 请求头名')).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('认证方式'), 'none');
    expect(screen.queryByLabelText('凭据引用')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('API Key 请求头名')).not.toBeInTheDocument();
  });

  it('天眼查（external_tool）编辑弹窗始终不显示凭据引用与请求头名', async () => {
    renderAdmin({
      id: '23',
      code: 'tianyancha',
      name: '天眼查企业核查',
      type: 'external_tool',
      authType: 'api_key',
      schedule: null,
      enabled: false,
      adapterStatus: 'unconfigured',
    });
    await openForm();

    expect(screen.getByLabelText('认证方式')).toHaveValue('api_key');
    expect(screen.queryByLabelText('凭据引用')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('API Key 请求头名')).not.toBeInTheDocument();
  });

  // 失败场景：历史认证值不得静默改写——只读告警展示当前值，提交对象省略 auth_type 键。
  it('历史认证方式以只读警告展示，保存时省略 auth_type 键', async () => {
    const onUpdateSource = renderAdmin({authType: 'bearer'});
    const user = await openForm();

    const warning = screen.getByTestId('auth-type-legacy-warning');
    expect(warning).toHaveTextContent(/bearer/);
    expect(screen.queryByLabelText('认证方式')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('凭据引用')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: '保存配置'}));
    await waitFor(() => expect(onUpdateSource).toHaveBeenCalledTimes(1));
    const payload: Partial<DataSourceWritePayload> = onUpdateSource.mock.calls[0]?.[1] ?? {};
    expect('auth_type' in payload).toBe(false);
  });

  // 失败场景（marker 保全）：请求头名 onChange 必须合并 login_config，而非整体覆盖。
  it('编辑请求头名合并 login_config 并保留 0054/0055 迁移 marker', async () => {
    const marker = {revision: '0054', policy_before: {mode: 'fixed_days', review_required: true}};
    const onUpdateSource = renderAdmin({
      authType: 'api_key',
      loginConfig: {source_validity_review_removal: marker},
    });
    const user = await openForm();

    await user.type(screen.getByLabelText('API Key 请求头名'), 'X-API-Key');
    await user.click(screen.getByRole('button', {name: '保存配置'}));
    await waitFor(() => expect(onUpdateSource).toHaveBeenCalledTimes(1));

    const payload: Partial<DataSourceWritePayload> = onUpdateSource.mock.calls[0]?.[1] ?? {};
    expect(payload.login_config).toEqual({source_validity_review_removal: marker, header_name: 'X-API-Key'});
    expect(payload.auth_type).toBe('api_key');
  });

  it('清空请求头名只删除 header_name，不抹掉其它 login_config 键', async () => {
    const marker = {revision: '0055', was_inserted: false};
    const onUpdateSource = renderAdmin({
      authType: 'api_key',
      loginConfig: {header_name: 'X-Old-Key', usgs_builtin_migration: marker},
    });
    const user = await openForm();

    await user.clear(screen.getByLabelText('API Key 请求头名'));
    await user.click(screen.getByRole('button', {name: '保存配置'}));
    await waitFor(() => expect(onUpdateSource).toHaveBeenCalledTimes(1));

    const payload: Partial<DataSourceWritePayload> = onUpdateSource.mock.calls[0]?.[1] ?? {};
    expect(payload.login_config).toEqual({usgs_builtin_migration: marker});
    expect('header_name' in (payload.login_config as Record<string, unknown>)).toBe(false);
  });
});

describe('信息源健康新鲜度（任务8 只读诊断）', () => {
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

  it('停用与外部核查来源不出现红色故障标签', () => {
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
    expect(within(screen.getByTestId('source-health-17')).getByText('核查')).toBeInTheDocument();
  });

  it('天眼查 external_tool 行后端返回周度 ok 时，也只显示中性「按需核查」而非采集正常', () => {
    const tycSource: DataSource = {
      ...source,
      id: '23',
      code: 'tianyancha',
      name: '天眼查企业核查',
      type: 'external_tool',
    };
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[tycSource]}
          role="viewer"
          onUpdateSource={vi.fn()}
          onRefreshSources={vi.fn()}

          monitoringHealth={monitoringHealthWith({
            ...healthySource,
            source_id: 23,
            code: 'tianyancha',
            name: '天眼查企业核查',
            state: 'ok',
            reason_code: 'success_observed',
            last_success_at: '2026-09-06T22:30:00Z',
            last_attempt_at: '2026-09-06T22:30:00Z',
            next_expected_at: '2026-09-12T22:00:00Z',
          })}
        />
      </MemoryRouter>,
    );

    const cell = screen.getByTestId('source-health-23');
    expect(within(cell).getByText('按需核查')).toBeInTheDocument();
    expect(cell.textContent).not.toContain('采集正常');
    expect(cell.textContent).not.toContain('下次预期');
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

describe('信息源列表信息架构与操作区', () => {
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
    name: '天眼查企业核查（核查）',
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
    lastSyncTime: '调用',
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
    expect(screen.getByTestId('source-name-23').textContent).not.toContain('（核查）');
    expect(screen.getByText('tianyancha')).toBeInTheDocument();
  });

  // 类别标签必须始终留在标题右侧同一行：标题行不换行，标题可截断，
  // 标签不参与压缩；完整名称由 title 兜底。
  it('类别标签与标题保持同一行且标题以 title 暴露完整名称', () => {
    renderView([source]);

    const nameNode = screen.getByTestId('source-name-17');
    expect(nameNode).toHaveAttribute('title', '官方风险源');
    expect(nameNode.parentElement).toHaveClass('flex-nowrap');
    expect(nameNode).toHaveClass('truncate');
    expect(nameNode).toHaveClass('min-w-0');
    expect(screen.getByTestId('source-category-17')).toHaveClass('shrink-0');
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
    expect(within(actions).getByRole('button', {name: '停用官方风险源'})).toHaveAttribute('title', '停用该信息源，停止自动采集');
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
    // 单源刷新 2xx 后必须以监控健康刷新意图重载：列表重载与 monitoring-health 再请求联动，失败路径不得携带。
    expect(onRefreshSources).toHaveBeenCalledWith({refreshMonitoringHealth: true});
  });

  it('刷新失败时按行反馈业务化错误，不触发列表刷新', async () => {
    vi.spyOn(api, 'runSource').mockRejectedValue(new Error('信息源已停用'));
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

    expect(await screen.findByTestId('source-run-msg-17')).toHaveTextContent('信息源已停用');
    expect(onRefreshSources).not.toHaveBeenCalled();
  });

  // 次级语义回归：重载列表只是采集成功后的附加动作，其失败只能追加提示，不得把已成功的结果改写为失败，
  // 也不得吞掉健康刷新意图（监控健康再请求应由采集成功触发，与列表重载是否成功无关）。
  it('单源刷新成功后列表重载失败只追加次级提示，不改变成功语义并仍携带健康刷新意图', async () => {
    vi.spyOn(api, 'runSource').mockResolvedValue({
      id: 2,
      source_id: 17,
      started_at: '2026-09-11T06:05:00Z',
      finished_at: '2026-09-11T06:05:05Z',
      status: 'succeeded',
      fetched_count: 3,
      created_count: 3,
      duplicate_count: 0,
      error: null,
    });
    const onRefreshSources = vi.fn().mockRejectedValue(new Error('网络中断'));
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

    // 采集成功语义保留：主消息仍是成功文本，重载失败仅追加次级提示且 tone 不降级为 alert。
    const msg = await screen.findByTestId('source-run-msg-17');
    expect(msg).toHaveTextContent('刷新完成，新增 3 条记录');
    expect(msg).toHaveTextContent('（列表刷新失败，请重新加载：网络中断）');
    expect(msg).toHaveAttribute('role', 'status');
    expect(onRefreshSources).toHaveBeenCalledWith({refreshMonitoringHealth: true});
  });

  // 天眼查手动核查入口已收敛到供应商查询助手：卡片不再渲染任何手动核查/通用刷新控件，
  // 也不再加载供应商数据；编辑与启停按钮保持原布局与权限规则。
  it('天眼查卡片不渲染供应商选择、手动核查与通用刷新，且不请求供应商数据', async () => {
    const suppliers = vi.spyOn(api, 'suppliers').mockResolvedValue({items: [], total: 0, limit: 100, offset: 0});
    renderView([{...tianyancha, enabled: true, status: 'normal', latency: '核查可用'}], 'admin');

    expect(screen.queryByLabelText('选择核查供应商')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '核查本供应商：天眼查企业核查'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '重试加载供应商'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '刷新天眼查企业核查'})).not.toBeInTheDocument();
    expect(screen.queryByTestId(/^source-tyc-check-/)).not.toBeInTheDocument();
    expect(screen.queryByTestId(/^source-tyc-detail-/)).not.toBeInTheDocument();

    const actions = screen.getByTestId('source-actions-23');
    expect(within(actions).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
      '编辑天眼查企业核查',
      '停用天眼查企业核查',
    ]);

    // 留出一个微任务窗口：旧入口的供应商加载 effect 若残留会在此暴露。
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(suppliers).not.toHaveBeenCalled();
  });

  it('人工录入与其他外部核查工具的行内操作仍禁用且不触发调用', async () => {
    const runSource = vi.spyOn(api, 'runSource');
    renderView([
      {...source, id: '18', code: 'manual-json', name: '手工 JSON 导入', type: 'manual'},
      {...source, id: '19', code: 'other-tool', name: '其他外部核查工具', type: 'external_tool'},
    ], 'admin');

    const manualButton = screen.getByRole('button', {name: '刷新手工 JSON 导入'});
    expect(manualButton).toBeDisabled();
    expect(manualButton).toHaveAttribute('title', '人工录入信息源不支持刷新');

    const otherToolButton = screen.getByRole('button', {name: '刷新其他外部核查工具'});
    expect(otherToolButton).toBeDisabled();
    expect(otherToolButton).toHaveAttribute('title', '外部核查工具调用，不支持页面刷新');
    // 其他 external_tool 仍显示默认「核查」，不被天眼查的「按需核查」覆盖。
    expect(within(screen.getByTestId('source-connectivity-19')).getByText('核查')).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(manualButton);
    await user.click(otherToolButton);
    expect(runSource).not.toHaveBeenCalled();
  });

  // 缺陷回归：真实 /api/v1/sources/admin 返回天眼查 source_type=external_tool、
  // adapter_status=unconfigured、enabled=true。天眼查动作区只保留编辑/启停，不受适配器状态
  // 影响；普通拉取来源仍要求已发布适配器才可刷新。
  it('已启用、未配置声明式适配器的天眼查行保留编辑与停用，普通拉取来源仍要求已发布适配器', () => {
    renderView([
      {...tianyancha, adapterStatus: 'unconfigured', enabled: true, status: 'normal', latency: '核查可用'},
      {...source, id: '24', code: 'official-unconfigured', name: '未配置适配器接口来源', adapterStatus: 'unconfigured'},
    ], 'admin');

    const actions = screen.getByTestId('source-actions-23');
    expect(within(actions).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
      '编辑天眼查企业核查',
      '停用天眼查企业核查',
    ]);

    const apiButton = screen.getByRole('button', {name: '刷新未配置适配器接口来源'});
    expect(apiButton).toBeDisabled();
    expect(apiButton).toHaveAttribute('title', '该来源尚未完成适配器配置，暂不可刷新');
  });

  it('单来源刷新期间锁定其他来源的刷新按钮，完成后恢复可用', async () => {
    let resolveRun: (value: CollectionRunRead) => void = () => undefined;
    vi.spyOn(api, 'runSource').mockImplementation(() => new Promise((resolve) => {
      resolveRun = resolve;
    }));
    renderView([
      {...source, enabled: true},
      {...source, id: '18', code: 'OFFICIAL-18', name: '第二官方源', enabled: true},
    ], 'admin');

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', {name: '刷新官方风险源'}));

    const otherButton = screen.getByRole('button', {name: '刷新第二官方源'});
    expect(otherButton).toBeDisabled();
    expect(otherButton).toHaveAttribute('title', '正在刷新其他信息源，请稍候');

    resolveRun({
      id: 8,
      source_id: 17,
      started_at: '2026-09-11T06:10:00Z',
      finished_at: '2026-09-11T06:10:05Z',
      status: 'succeeded',
      fetched_count: 4,
      created_count: 2,
      duplicate_count: 2,
      error: null,
    });
    await waitFor(() => expect(otherButton).toBeEnabled());
  });

  it('未启用来源的刷新按钮为禁用态并说明启用后可用', () => {
    renderView([{...source, enabled: false, status: 'disabled', latency: '已停用'}], 'admin');

    const refreshButton = screen.getByRole('button', {name: '刷新官方风险源'});
    expect(refreshButton).toBeDisabled();
    expect(refreshButton).toHaveAttribute('title', '信息源已停用，启用后可刷新');
  });

  it('天眼查连通胶囊与新鲜度胶囊均显示「按需核查」，最近核查为真实时间且无下次预期', () => {
    renderView([tianyancha], 'viewer', monitoringHealthWith({
      ...healthySource,
      source_id: 23,
      code: 'tianyancha',
      name: '天眼查企业核查（核查）',
      state: 'disabled',
      reason_code: 'disabled',
      next_expected_at: null,
      last_success_at: null,
      last_attempt_at: '2026-09-10T03:00:00Z',
    }));

    const connectivity = screen.getByTestId('source-connectivity-23');
    expect(connectivity).toHaveTextContent('按需核查');
    // 悬浮说明明确单供应商核查已由供应商查询助手承担，同时保留周度分片自动核查说明。
    expect(connectivity).toHaveAttribute(
      'title',
      '外部核查工具：运行密钥已配置，单个供应商风险信息请通过供应商查询助手核查；每周日、周一按分片自动核查已启用供应商',
    );

    const health = screen.getByTestId('source-health-23');
    expect(within(health).getByText('按需核查')).toBeInTheDocument();
    expect(health).toHaveTextContent(`最近核查 ${expectedTime('2026-09-10T03:00:00Z')}`);
    expect(health).not.toHaveTextContent('下次预期');
    expect(screen.queryByText('已停用')).not.toBeInTheDocument();
  });

  it('天眼查密钥未配置时提示先配置运行密钥', () => {
    renderView([{...tianyancha, apiKeyConfigured: false, apiKeyHint: null}], 'viewer');

    const connectivity = screen.getByTestId('source-connectivity-23');
    expect(connectivity).toHaveAttribute(
      'title',
      '外部核查工具：运行密钥未配置，请先在编辑中配置运行密钥后再发起核查',
    );
    expect(connectivity.getAttribute('title')).not.toContain('不支持页面刷新');
    expect(connectivity.getAttribute('title')).not.toContain('每天北京时间 06:00');
  });

  it('其他外部核查工具的悬浮说明保持「不支持页面刷新」', () => {
    renderView([
      {...source, id: '31', code: 'other-tool', name: '其他外部核查工具', type: 'external_tool', apiKeyConfigured: true},
      {...source, id: '32', code: 'other-tool-b', name: '其他外部核查工具B', type: 'external_tool', apiKeyConfigured: false},
    ], 'viewer');

    expect(screen.getByTestId('source-connectivity-31')).toHaveAttribute(
      'title',
      '外部核查工具：运行密钥已配置，发起查询，不支持页面刷新',
    );
    expect(screen.getByTestId('source-connectivity-32')).toHaveAttribute(
      'title',
      '外部核查工具：运行密钥未配置，请先在编辑中配置，不支持页面刷新',
    );
    // 标签覆盖只作用于天眼查：其他外部核查工具始终显示默认「核查」。
    expect(within(screen.getByTestId('source-connectivity-31')).getByText('核查')).toBeInTheDocument();
    expect(within(screen.getByTestId('source-connectivity-32')).getByText('核查')).toBeInTheDocument();
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
describe('信息源连通徽标配色与采集状态解耦', () => {
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

  it('停用、核查、人工录入与未配置来源使用中性灰徽标而非故障色', () => {
    renderView([
      {...source, id: '1', enabled: false, status: 'disabled', latency: '已停用'},
      {...source, id: '2', code: 'tianyancha', name: '天眼查企业核查', type: 'external_tool', enabled: false, status: 'disabled', latency: '已停用'},
      {...source, id: '3', code: 'manual-json', name: '手工 JSON 导入', type: 'manual', enabled: false, status: 'disabled', latency: '已停用'},
      {...source, id: '4', endpointUrl: null, accessLastHttpStatus: null, accessLastErrorKind: null},
    ]);

    expect(connectivityBadge('1')).toHaveTextContent('已停用');
    // 天眼查使用「按需核查」，但配色仍保持中性灰，不引入新的故障语义。
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

// 迁移 0050：商务部重复信源收敛为唯一 mofcom-entity-detail（业务库 ID 361）。
// mofcom-entity-control（业务库 ID 4）停用并取消调度，但行仍保留在库中；
// 控制台必须按同一可见集合统一排除，禁止退役来源经行、提示、概览或空态判断泄露。
describe('信息源列表退役来源可见性（迁移0050）', () => {
  const retiredControl: DataSource = {
    ...source,
    id: '4',
    code: 'mofcom-entity-control',
    name: '商务部实体控制清单',
    status: 'warning',
    latency: '等待下一轮采集',
    totalSignalCount: 40,
    validSignalCount: 0,
  };

  const entityDetail: DataSource = {
    ...source,
    id: '361',
    code: 'mofcom-entity-detail',
    name: '商务部实体名单详情',
    status: 'normal',
    latency: '运行正常',
    totalSignalCount: 25,
    validSignalCount: 7,
  };

  const renderList = (
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

  // 诊断快照中同时保留退役来源与存续来源的健康条目：
  // 断言退役来源健康不渲染，是为了证明"来源级健康随可见集合排除"，而不是快照里本就没有它。
  const healthWithRetiredPair = (): MonitoringHealthSnapshot => {
    const base = monitoringHealthWith(healthySource);
    if (base.status !== 'ready') return base;
    return {
      status: 'ready',
      health: {
        ...base.health,
        sources: [
          {...healthySource, source_id: 4, code: 'mofcom-entity-control', name: '商务部实体控制清单'},
          {...healthySource, source_id: 361, code: 'mofcom-entity-detail', name: '商务部实体名单详情'},
        ],
      },
    };
  };

  it('隐藏退役来源的行、来源级健康与运行提示，保留 mofcom-entity-detail 行可见', () => {
    renderList([retiredControl, entityDetail], 'viewer', healthWithRetiredPair());

    expect(screen.queryByTestId('source-name-4')).not.toBeInTheDocument();
    expect(screen.queryByTestId('source-valid-4')).not.toBeInTheDocument();
    expect(screen.queryByTestId('source-health-4')).not.toBeInTheDocument();
    expect(screen.queryByText('信息源运行提示')).not.toBeInTheDocument();
    expect(screen.queryByText(/商务部实体控制清单/)).not.toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(1);

    expect(screen.getByTestId('source-name-361')).toHaveTextContent('商务部实体名单详情');
    expect(screen.getByTestId('source-health-361')).toHaveTextContent('采集正常');
  });

  it('概览卡只统计可见来源，退役来源不得进入接入数、异常数与累计数', () => {
    renderList([retiredControl, entityDetail]);

    const accessCard = screen.getByText('信息源接入数').parentElement;
    expect(accessCard).toHaveTextContent('1 个管道');
    expect(accessCard).not.toHaveTextContent('2 个管道');
    const abnormalCard = screen.getByText('异常/延迟节点').parentElement;
    expect(abnormalCard).toHaveTextContent('0 个');
    expect(abnormalCard).not.toHaveTextContent('1 个');
    const totalCard = screen.getByText('全网累计记录数').parentElement;
    expect(totalCard).toHaveTextContent('25 条');
    expect(totalCard).not.toHaveTextContent('65 条');
  });

  it('仅传入退役来源时列表进入空态，概览与累计归零', () => {
    renderList([{...retiredControl, status: 'normal', latency: '运行正常'}]);

    expect(screen.getByText('暂无信息源配置')).toBeInTheDocument();
    expect(screen.queryAllByRole('listitem')).toHaveLength(0);
    expect(screen.getByText('信息源接入数').parentElement).toHaveTextContent('0 个管道');
    expect(screen.getByText('正常运行 (Normal)').parentElement).toHaveTextContent('0 个');
    expect(screen.getByText('全网累计记录数').parentElement).toHaveTextContent('0 条');
  });

  it('可见行操作仍使用原始来源对象：mofcom-entity-detail 刷新提交业务库 ID 361', async () => {
    const runSource = vi.spyOn(api, 'runSource').mockResolvedValue({
      id: 9,
      source_id: 361,
      started_at: '2026-09-29T06:00:00Z',
      finished_at: '2026-09-29T06:00:05Z',
      status: 'succeeded',
      fetched_count: 1,
      created_count: 1,
      duplicate_count: 0,
      error: null,
    });
    renderList([retiredControl, entityDetail], 'admin');

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', {name: '刷新商务部实体名单详情'}));

    expect(runSource).toHaveBeenCalledWith(361);
    expect(screen.queryByRole('button', {name: '刷新商务部实体控制清单'})).not.toBeInTheDocument();
  });
});

// 调度周期列回归：可见文本只渲染中文 label，原始 cron 仅进 title/aria-label 属性；
// 表头与行网格同步从 12 列扩展为 14 列。
describe('信息源调度周期列', () => {
  const renderView = (dataSources: DataSource[]) => render(
    <MemoryRouter>
      <DataSourcesView
        dataSources={dataSources}
        role="viewer"

        onUpdateSource={vi.fn()}
        onRefreshSources={vi.fn().mockResolvedValue(undefined)}

        monitoringHealth={{status: 'hidden'}}
      />
    </MemoryRouter>,
  );

  it('表头新增「调度周期」，表头网格为 14 列且列跨度合计 14', () => {
    const {container} = renderView([source]);

    expect(screen.getByText('调度周期')).toBeInTheDocument();

    const list = container.querySelector('[role="list"]');
    const headerRow = list?.firstElementChild;
    expect(headerRow).not.toBeNull();
    expect(headerRow?.className).toContain('md:grid-cols-[repeat(14,minmax(0,1fr))]');

    const headerCells = Array.from(headerRow?.children ?? []);
    expect(headerCells).toHaveLength(6);
    const spanSum = headerCells.reduce((sum, cell) => {
      const match = /(?:^|\s)col-span-(\d+)(?:\s|$)/.exec(cell.className);
      return sum + (match ? Number.parseInt(match[1] ?? '0', 10) : 0);
    }, 0);
    expect(spanSum).toBe(14);
  });

  it('正常来源渲染中文 label，原始 cron 只出现在 title 与 aria-label', () => {
    renderView([source]);

    const cell = screen.getByTestId('source-schedule-17');
    // 负向断言：可见文本绝不含原始 cron。
    expect(cell.textContent).not.toContain('*/30');

    const value = within(cell).getByText('每 30 分钟');
    expect(value.textContent).toBe('每 30 分钟');
    expect(value).toHaveAttribute('title', '*/30 * * * *（北京时间）');

    const accessibleName = '调度周期：每 30 分钟，原始 cron */30 * * * *（北京时间）';
    expect(value).toHaveAccessibleName(accessibleName);
    expect(screen.getByLabelText(accessibleName)).toBe(value);
  });

  it('类别分支：天眼查、其它外部工具、人工录入与未单独配置各自渲染固定标签', () => {
    renderView([
      {...source, id: '31', code: 'tianyancha', type: 'external_tool', schedule: null},
      {...source, id: '32', code: 'other-tool', type: 'external_tool', schedule: '*/5 * * * *'},
      {...source, id: '33', code: 'manual-json', type: 'manual', schedule: null},
      {...source, id: '34', code: 'official-api-unset', type: 'official_api', schedule: null},
    ]);

    expect(within(screen.getByTestId('source-schedule-31')).getByText('每周日、周一 06:00（分片）')).toBeInTheDocument();

    const externalCell = screen.getByTestId('source-schedule-32');
    expect(within(externalCell).getByText('按需调用')).toBeInTheDocument();
    expect(externalCell.textContent).not.toContain('*/5');

    expect(within(screen.getByTestId('source-schedule-33')).getByText('人工录入')).toBeInTheDocument();
    expect(within(screen.getByTestId('source-schedule-34')).getByText('未单独配置')).toBeInTheDocument();
  });

  it('值节点样式 token 齐全，移动端前缀以 md:hidden 在窄屏提示', () => {
    renderView([source]);

    const cell = screen.getByTestId('source-schedule-17');
    const value = within(cell).getByText('每 30 分钟');
    ['whitespace-normal', 'text-xs', 'font-semibold', 'text-[#424751]', 'dark:text-slate-400'].forEach((token) => {
      expect(value.className.split(/\s+/)).toContain(token);
    });
    expect(within(cell).getByText('调度:').className.split(/\s+/)).toContain('md:hidden');
  });
});

// 调度器实况已迁移为独立 /scheduler 页面：信息源页不再承载按钮、弹窗与打开时的健康刷新回调。
// 单源刷新成功后携带 {refreshMonitoringHealth: true} 意图的逻辑仍保留（见「信息源列表信息架构与操作区」用例）。
describe('信息源页不再承载调度器实况入口（已迁移独立 /scheduler 页面）', () => {
  const renderView = (monitoringHealth: MonitoringHealthSnapshot = {status: 'hidden'}) => render(
    <MemoryRouter>
      <DataSourcesView
        dataSources={[source]}
        role="viewer"
        onUpdateSource={vi.fn()}
        onRefreshSources={vi.fn().mockResolvedValue(undefined)}
        monitoringHealth={monitoringHealth}
      />
    </MemoryRouter>,
  );

  it('页头不再渲染「调度器实况」按钮，也不挂载旧弹窗遮罩', () => {
    renderView(readyHealth);

    expect(screen.queryByTestId('scheduler-activity-button')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '调度器实况'})).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-activity-overlay')).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    // 列表与来源级新鲜度不受入口移除影响，页面照常渲染。
    expect(screen.getByRole('link', {name: '官方风险源 有效记录 7 条'})).toBeInTheDocument();
    expect(screen.getByTestId('source-health-17')).toHaveTextContent('采集正常');
  });

  it('挂在信息源页也不触碰旧弹窗的会话面板测试标识', () => {
    renderView(readyHealth);

    expect(screen.queryByTestId('scheduler-console')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-activity-idle')).not.toBeInTheDocument();
    expect(screen.queryByText('调度器实况')).not.toBeInTheDocument();
  });
});

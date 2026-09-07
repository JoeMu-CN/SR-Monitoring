import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {DataSource} from '../types';
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
});

describe('数据源有效期策略表单', () => {
  const renderAdmin = (overrides: Partial<DataSource> = {}) => {
    const onUpdateSource = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <DataSourcesView
          dataSources={[{...source, ...overrides}]}
          role="admin"
          onUpdateSource={onUpdateSource}
          onRefreshSources={vi.fn().mockResolvedValue(undefined)}
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

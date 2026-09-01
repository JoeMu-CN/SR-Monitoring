import {cleanup, render, screen} from '@testing-library/react';
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

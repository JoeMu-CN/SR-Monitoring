import {describe, expect, it, vi} from 'vitest';
import {
  api,
  mapDataSource,
  mapRiskAlert,
  mapSupplier,
  mapSupplierListItem,
  type DataSourceRead,
  type RiskAlertRead,
  type SupplierListItem,
  type SupplierRead,
} from './api';

describe('API 数据映射', () => {
  it('将风险提醒映射为 Google UI 风险卡片', () => {
    const alert: RiskAlertRead = {
      id: 7,
      level: 'P2',
      score: 72,
      score_detail: {severity: 30, association: 20},
      status: 'current',
      supplier_id: 3,
      supplier_name: '测试供应商',
      event_id: 11,
      event_type: 'weather',
      event_subtype: 'flood',
      event_summary: '厂区附近发生洪水',
      event_start_at: '2026-08-08T01:00:00Z',
      event_end_at: null,
      confidence: 0.91,
      match_type: 'location',
      match_reasons: ['城市匹配'],
      match_evidence: [{city: '深圳'}],
      source_title: '气象预警',
      source_url: null,
      published_at: '2026-08-08T01:00:00Z',
      updated_at: '2026-08-08T02:00:00Z',
    };

    const result = mapRiskAlert(alert);
    expect(result).toMatchObject({id: '7', level: 'P2', companyName: '测试供应商', overallScore: 72});
    expect(result.aiConfidence).toBe(91);
  });

  it('将供应商映射为真实监控状态', () => {
    const supplier: SupplierRead = {
      id: 3,
      supplier_code: 'SUP-0003',
      legal_name: '测试供应商',
      country_code: 'CN',
      registry_no: null,
      registration_address: null,
      industry: '电子元器件',
      raw_materials: [],
      enabled: true,
      aliases: [],
      sites: [{id: 1, site_name: '深圳工厂', country_code: 'CN', region: '广东', city: '深圳', district: '南山', address: '深圳市', latitude: null, longitude: null}],
      products: [{id: 1, name: '功率器件', keywords: []}],
    };

    expect(mapSupplier(supplier, 'P1', 90)).toMatchObject({
      id: '3', code: 'SUP-0003', monitoringStatus: 'high_risk', productionLocation: '深圳 南山 深圳工厂', riskScore: 90,
    });
  });

  it('使用最新采集运行映射数据源状态', () => {
    const source: DataSourceRead = {id: 2, code: 'NEWS', name: '新闻源', source_type: 'rss', credibility: 0.8, schedule: null, enabled: true};
    const result = mapDataSource(source, [
      {id: 8, source_id: 2, started_at: '2026-08-08T01:00:00Z', finished_at: '2026-08-08T01:01:00Z', status: 'succeeded', fetched_count: 5, created_count: 2, duplicate_count: 3, error: null},
    ]);

    expect(result).toMatchObject({id: '2', status: 'normal', latency: '运行正常', itemCount: 2});
  });
});

describe('API 会话请求', () => {
  it('携带 Cookie 会话与 CSRF，并不发送浏览器伪造角色 Header', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 200, json: async () => ({detail: '已退出登录'})});
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-test'});

    await api.auth.logout();

    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(options.headers);
    expect(options.credentials).toBe('include');
    expect(headers.get('X-CSRF-Token')).toBe('csrf-test');
    expect(headers.has('X-User-Role')).toBe(false);
    expect(headers.has('X-User-Id')).toBe(false);
    vi.unstubAllGlobals();
  });
});

describe('数据源采集记录 API 请求契约', () => {
  it('只发送范围与偏移量且不发送可变 limit', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 200, json: async () => ({items: []})});
    vi.stubGlobal('fetch', fetchMock);

    await api.sourceSignals(17, 'all', 40);

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/sources/17/signals?scope=all&offset=40',
      expect.objectContaining({credentials: 'include'}),
    );
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain('limit=');
    vi.unstubAllGlobals();
  });
});

describe('供应商清单 API 请求契约', () => {
  const supplierQueries: ReadonlyArray<readonly [Parameters<typeof api.supplierPage>[1], string]> = [
    ['all', '/api/v1/suppliers?limit=20&offset=40'],
    ['normal', '/api/v1/suppliers?limit=20&offset=40&enabled=true&has_current_alert=false'],
    ['high_risk', '/api/v1/suppliers?limit=20&offset=40&enabled=true&has_current_alert=true'],
    ['paused', '/api/v1/suppliers?limit=20&offset=40&enabled=false'],
  ];

  it.each(supplierQueries)('把监控状态 %s 翻译为固定 20 条的服务端查询', async (status, expected) => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 200, json: async () => ({items: []})});
    vi.stubGlobal('fetch', fetchMock);

    await api.supplierPage('', status, 40);

    expect(fetchMock).toHaveBeenCalledWith(expected, expect.objectContaining({credentials: 'include'}));
    vi.unstubAllGlobals();
  });

  it('对查询词做百分号编码且空查询词不进入请求', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 200, json: async () => ({items: []})});
    vi.stubGlobal('fetch', fetchMock);

    await api.supplierPage('功率 & 100%', 'all', 0);
    await api.supplierPage('', 'all', 0);

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe('/api/v1/suppliers?limit=20&offset=0&q=%E5%8A%9F%E7%8E%87+%26+100%25');
    expect(String(fetchMock.mock.calls[1]?.[0])).not.toContain('q=');
    vi.unstubAllGlobals();
  });

  it('列表项按服务端当前风险判定监控状态而不再按 P1/P2 推断', () => {
    const base: SupplierListItem = {
      id: 5,
      supplier_code: 'SUP-0005',
      legal_name: '测试供应商',
      country_code: 'CN',
      registry_no: null,
      registration_address: null,
      industry: null,
      raw_materials: [],
      enabled: true,
      aliases: [],
      sites: [],
      products: [],
      current_risk_level: 'P3',
      current_risk_score: 55,
    };

    expect(mapSupplierListItem(base)).toMatchObject({monitoringStatus: 'high_risk', riskLevel: 'P3', riskScore: 55});
    expect(mapSupplierListItem({...base, current_risk_level: null, current_risk_score: null}))
      .toMatchObject({monitoringStatus: 'normal', riskLevel: undefined});
    expect(mapSupplierListItem({...base, enabled: false})).toMatchObject({monitoringStatus: 'paused'});
  });
});

describe('研究 API 请求契约', () => {
  it('使用只读请求读取任务列表、详情和报告草稿', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 200, json: async () => ({items: []})});
    vi.stubGlobal('fetch', fetchMock);

    await api.research.tasks();
    await api.research.workerStatus();
    await api.research.task(12);
    await api.research.events(12, 7);
    await api.research.sources(12);
    await api.research.reports(12);
    await api.research.deleteTask(12);

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/v1/research/tasks',
      '/api/v1/research/worker/status',
      '/api/v1/research/tasks/12',
      '/api/v1/research/tasks/12/events?after_id=7&limit=200',
      '/api/v1/research/tasks/12/sources',
      '/api/v1/research/tasks/12/reports',
      '/api/v1/research/tasks/12',
    ]);
    for (const [, options] of fetchMock.mock.calls.slice(0, 6) as Array<[string, RequestInit]>) {
      expect(options.credentials).toBe('include');
      expect(options.method ?? 'GET').toBe('GET');
    }
    vi.unstubAllGlobals();
  });

  it('创建和取消任务使用 POST、JSON 载荷与 CSRF', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 202, json: async () => ({})});
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-research'});

    await api.research.createTask('某供应商近 30 天风险动态', [3, 7]);
    await api.research.startTask(12);
    await api.research.cancelTask(12);
    await api.research.deleteTask(12);

    const [, createOptions] = fetchMock.mock.calls[0] as [string, RequestInit];
    const [, startOptions] = fetchMock.mock.calls[1] as [string, RequestInit];
    const [, cancelOptions] = fetchMock.mock.calls[2] as [string, RequestInit];
    const [, deleteOptions] = fetchMock.mock.calls[3] as [string, RequestInit];
    expect(createOptions.method).toBe('POST');
    expect(JSON.parse(String(createOptions.body))).toEqual({
      task_type: 'manual',
      topic: '某供应商近 30 天风险动态',
      supplier_scope: [3, 7],
    });
    expect(new Headers(createOptions.headers).get('X-CSRF-Token')).toBe('csrf-research');
    expect(startOptions.method).toBe('POST');
    expect(startOptions.body).toBeUndefined();
    expect(new Headers(startOptions.headers).get('X-CSRF-Token')).toBe('csrf-research');
    expect(cancelOptions.method).toBe('POST');
    expect(cancelOptions.body).toBeUndefined();
    expect(new Headers(cancelOptions.headers).get('X-CSRF-Token')).toBe('csrf-research');
    expect(deleteOptions.method).toBe('DELETE');
    expect(deleteOptions.body).toBeUndefined();
    expect(new Headers(deleteOptions.headers).get('X-CSRF-Token')).toBe('csrf-research');
    vi.unstubAllGlobals();
  });
});

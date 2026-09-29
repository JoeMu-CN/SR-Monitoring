import {describe, expect, it, vi} from 'vitest';
import {
  api,
  mapDataSource,
  mapDimension,
  mapRiskAlert,
  mapSupplier,
  mapSupplierListItem,
  updateDimensionConfig,
  type DataSourceRead,
  type DimensionRead,
  type DimensionTraceRead,
  type GlobalScoringConfigRead,
  type RiskAlertRead,
  type SandboxRequest,
  type SandboxResult,
  type SupplierListItem,
  type SupplierRead,
} from './api';
import type {MonitoringDimension} from './types';

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
      expires_at: null,
      expiry_kind: 'none',
      validity_state: 'active',
      valid_until: null,
      review_due_at: null,
      validity_policy_version: 'v1',
      validity_reason: {code: 'active', anchor_source: 'published_at', details: {}},
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
      updated_at: '2026-09-01T00:00:00Z',
      aliases: [],
      sites: [{id: 1, site_name: '深圳工厂', country_code: 'CN', region: '广东', city: '深圳', district: '南山', address: '深圳市', latitude: null, longitude: null}],
      products: [{id: 1, name: '功率器件', keywords: []}],
    };

    expect(mapSupplier(supplier, 'P1', 90)).toMatchObject({
      id: '3', code: 'SUP-0003', monitoringStatus: 'high_risk', productionLocation: '深圳 南山 深圳工厂', riskScore: 90,
    });
  });

  it('使用最新采集运行映射信息源状态', () => {
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

describe('信息源采集记录 API 请求契约', () => {
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
      updated_at: '2026-09-01T00:00:00Z',
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

describe('updateDimensionConfig 幂等性', () => {
  const dimensionRead = (overrides: Partial<DimensionRead['scoring']> = {}): DimensionRead => ({
    key: 'geopolitical',
    label: '地缘政治与安全',
    description: '',
    content_items: ['制裁'],
    data_sources: [],
    event_types: ['geopolitical'],
    match_columns: ['entity', 'country', 'industry'],
    enabled: true,
    has_override: false,
    active_alerts: 0,
    scoring: {
      rule_version: 'risk-score-v1',
      severity_scores: {critical: 35, high: 28, medium: 20, low: 10},
      association_scores: {registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12, country: 8, industry: 12},
      p1_min: 85,
      p2_min: 65,
      p3_min: 40,
      alert_expiry_days: 90,
      ...overrides,
    },
  });

  const mapped = (read: DimensionRead): MonitoringDimension => mapDimension(read);

  it('未修改任何字段时 patch 不包含 severity_scores/association_scores', () => {
    const original = mapped(dimensionRead());
    const updated = mapped(dimensionRead());
    const patch = updateDimensionConfig(original, updated);
    expect(patch).not.toHaveProperty('severity_scores');
    expect(patch).not.toHaveProperty('association_scores');
  });

  it('含 country/industry 的地缘维度未修改时 patch 不包含评分矩阵', () => {
    const original = mapped(dimensionRead());
    const updated = mapped(dimensionRead());
    const patch = updateDimensionConfig(original, updated);
    expect(patch).not.toHaveProperty('severity_scores');
    expect(patch).not.toHaveProperty('association_scores');
  });

  it('只改 critical 时 patch 仅含该键', () => {
    const original = mapped(dimensionRead());
    const updated = mapped(dimensionRead());
    updated.severityScores = {...updated.severityScores, critical: 30};
    const patch = updateDimensionConfig(original, updated);
    expect(patch.severity_scores).toEqual({critical: 30});
    expect(patch).not.toHaveProperty('association_scores');
  });

  it('1.0 → 0.5 → 1.0 的往返不再丢失原值', () => {
    const original = mapped(dimensionRead());
    // 用户把 critical 从 35 下调到 17
    const down = mapped(dimensionRead());
    down.severityScores = {...down.severityScores, critical: 17};
    const downPatch = updateDimensionConfig(original, down);
    expect(downPatch.severity_scores).toEqual({critical: 17});

    // 再上调回 35（等于原值），patch 应为空，且 country/industry 未被连带缩放
    const up = mapped(dimensionRead());
    up.severityScores = {...up.severityScores, critical: 35};
    const upPatch = updateDimensionConfig(original, up);
    expect(upPatch.severity_scores).toBeUndefined();
    expect(up.associationScores.country).toBe(8);
    expect(up.associationScores.industry).toBe(12);
  });

  it('任何输入组合下 patch 都不含 alert_expiry_days', () => {
    const original = mapped(dimensionRead());
    // 修改阈值与评分，模拟一次真实保存
    const updated = mapped(dimensionRead());
    updated.thresholds = {...updated.thresholds, p1: 90};
    updated.severityScores = {...updated.severityScores, critical: 30};
    const patch = updateDimensionConfig(original, updated);
    expect(patch).not.toHaveProperty('alert_expiry_days');
    // 即使原值与更新值在 ttl 语义上不同（已无该字段），patch 也不含该键
    expect(Object.keys(patch)).not.toContain('alert_expiry_days');
  });

  it('未修改 match_columns/event_types 时 patch 不含这两个键', () => {
    const original = mapped(dimensionRead());
    const updated = mapped(dimensionRead());
    const patch = updateDimensionConfig(original, updated);
    expect(patch).not.toHaveProperty('match_columns');
    expect(patch).not.toHaveProperty('event_types');
  });

  it('只改 match_columns 时 patch 含完整新数组且不含 event_types', () => {
    const original = mapped(dimensionRead());
    const updated = mapped(dimensionRead());
    updated.matchColumns = ['entity', 'location', 'product', 'country', 'industry'];
    const patch = updateDimensionConfig(original, updated);
    expect(patch.match_columns).toEqual(['entity', 'location', 'product', 'country', 'industry']);
    expect(patch).not.toHaveProperty('event_types');
    expect(patch).not.toHaveProperty('severity_scores');
  });

  it('只改 event_types 时 patch 含完整新数组且不含 match_columns', () => {
    const original = mapped(dimensionRead());
    const updated = mapped(dimensionRead());
    updated.eventTypes = ['geopolitical', 'trade_policy'];
    const patch = updateDimensionConfig(original, updated);
    expect(patch.event_types).toEqual(['geopolitical', 'trade_policy']);
    expect(patch).not.toHaveProperty('match_columns');
  });
});

describe('供应商删除影响 API 请求契约', () => {
  it('deletion-impact 只读请求不携带写头并返回结构化影响', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        can_delete: false,
        match_count: 3,
        alert_count: 2,
        sites_count: 1,
        products_count: 1,
        aliases_count: 1,
        blocked_reason: 'supplier_has_risk_history',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const impact = await api.supplierDeletionImpact(9);

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/suppliers/9/deletion-impact',
      expect.objectContaining({credentials: 'include'}),
    );
    expect(impact.can_delete).toBe(false);
    expect(impact.blocked_reason).toBe('supplier_has_risk_history');
    vi.unstubAllGlobals();
  });
});

describe('规则引擎全局配置与轨迹 API 请求契约', () => {
  const okJson = (payload: unknown) => ({ok: true, status: 200, json: async () => payload});

  /**
   * 完整响应夹具，类型锚定 `api.ts` 导出的真实读模型：
   * 字段缺失、改名或类型不符都会让 `npm run typecheck` 失败。
   */
  const globalConfigFixture = {
    source: 'configured',
    enabled: true,
    effective: {
      severity_scores: {critical: 40, high: 25, medium: 12, low: 4},
      association_scores: {strong: 35, medium: 20, weak: 8},
      p1_min: 85,
      p2_min: 65,
      p3_min: 40,
      alert_expiry_days: 90,
      forced_rules: [{name: 'sanctions_entity_hit', dimension_key: 'sanctions'}],
    },
    defaults: {
      severity_scores: {critical: 35, high: 20, medium: 10, low: 3},
      p1_min: 80,
      p2_min: 60,
      p3_min: 40,
    },
    shadowed_by: {severity_scores: ['geopolitical']},
    forced_rules_shadowed_by: ['geopolitical'],
    dropped_dimension_rules: [{dimension_key: 'geopolitical', name: 'policy_industry_hit'}],
  } satisfies GlobalScoringConfigRead;

  const dimensionTraceFixture = {
    available: true,
    event: {
      event_type: 'geopolitical',
      event_subtype: 'sanctions',
      severity: 'critical',
      summary: '制裁清单命中示例事件',
      confidence: 0.92,
      published_at: '2026-09-15T01:00:00Z',
      source_name: 'OFAC SDN',
    },
    routing: {key: 'geopolitical', label: '地缘政治', match_columns: ['entity', 'country']},
    match: {
      match_type: 'legal_name',
      match_reasons: ['法人全称精确匹配'],
      match_evidence: [{supplier_id: 3, decision: 'exact'}],
    },
    score: {
      total: 92,
      level: 'P1',
      detail: {severity: 40, association: 32},
      level_cap: 'P2',
      forced_rule: {name: 'sanctions_entity_hit'},
    },
    samples: [
      {
        id: 7,
        supplier_id: 3,
        supplier_name: '示例供应商',
        level: 'P1',
        event_summary: '制裁清单命中示例事件',
        updated_at: '2026-09-15T02:00:00Z',
      },
    ],
  } satisfies DimensionTraceRead;

  const sandboxFixture = {
    dimension: {key: 'geopolitical', label: '地缘政治', match_columns: ['entity', 'country']},
    candidates: [
      {
        supplier_id: 42,
        supplier_name: '示例公司',
        match_type: 'legal_name',
        association_score: 20,
        reasons: ['法人全称精确匹配'],
        score: 88,
        level: 'P1',
        score_detail: {severity: 40, association: 20},
      },
    ],
  } satisfies SandboxResult;

  it('GET /global-config 是只读请求：携带会话、不带 CSRF，并解析完整读模型', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson(globalConfigFixture));
    vi.stubGlobal('fetch', fetchMock);

    const config = await api.globalConfig.get();

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/rule-engine/global-config',
      expect.objectContaining({credentials: 'include'}),
    );
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(options.method ?? 'GET').toBe('GET');
    expect(new Headers(options.headers).has('X-CSRF-Token')).toBe(false);

    // 响应契约：返回 `{}`、字段改名或嵌套值被丢弃时，以下断言必须失败
    expect(config.source).toBe('configured');
    expect(config.enabled).toBe(true);
    expect(config.effective.severity_scores).toEqual({critical: 40, high: 25, medium: 12, low: 4});
    expect(config.effective.p1_min).toBe(85);
    expect(config.defaults.p2_min).toBe(60);
    expect(config.shadowed_by).toEqual({severity_scores: ['geopolitical']});
    expect(config.forced_rules_shadowed_by).toEqual(['geopolitical']);
    expect(config.dropped_dimension_rules).toEqual([
      {dimension_key: 'geopolitical', name: 'policy_industry_hit'},
    ]);
    vi.unstubAllGlobals();
  });

  it('PUT /global-config 只发送传入字段、带 CSRF，未确认时不追加确认参数', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson(globalConfigFixture));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-global'});

    const config = await api.globalConfig.update({severity_scores: {critical: 30}});

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe('/api/v1/rule-engine/global-config');
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(options.method).toBe('PUT');
    expect(JSON.parse(String(options.body))).toEqual({severity_scores: {critical: 30}});
    expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('csrf-global');
    expect(config.effective.alert_expiry_days).toBe(90);
    expect(config.defaults.p3_min).toBe(40);
    vi.unstubAllGlobals();
  });

  it('移除当前生效强制规则时确认标记走查询参数而不是请求体', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson(globalConfigFixture));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-global'});

    const config = await api.globalConfig.update({forced_rules: []}, true);

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      '/api/v1/rule-engine/global-config?confirm_disable_forced_rules=true',
    );
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(String(options.body)) as Record<string, unknown>;
    expect(body).toEqual({forced_rules: []});
    expect(Object.keys(body)).not.toContain('confirm_disable_forced_rules');
    expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('csrf-global');
    expect(config.forced_rules_shadowed_by).toEqual(['geopolitical']);
    vi.unstubAllGlobals();
  });

  it('DELETE /global-config 恢复默认：确认标记走查询参数、无请求体', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson(globalConfigFixture));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-global'});

    const config = await api.globalConfig.reset(true);

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      '/api/v1/rule-engine/global-config?confirm_disable_forced_rules=true',
    );
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(options.method).toBe('DELETE');
    expect(options.body).toBeUndefined();
    expect(config.source).toBe('configured');
    vi.unstubAllGlobals();
  });

  it('dimensionTrace 默认取该维度最近提醒，显式 alert_id 时附带查询参数', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson(dimensionTraceFixture));
    vi.stubGlobal('fetch', fetchMock);

    const trace = await api.dimensionTrace('natural');
    await api.dimensionTrace('natural', 17);

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/v1/rule-engine/dimensions/natural/trace',
      '/api/v1/rule-engine/dimensions/natural/trace?alert_id=17',
    ]);
    for (const [, options] of fetchMock.mock.calls as Array<[string, RequestInit]>) {
      expect(options.credentials).toBe('include');
      expect(options.method ?? 'GET').toBe('GET');
    }

    // 响应契约：available/event/routing/match/score/samples 六段必须是真实读模型
    expect(trace.available).toBe(true);
    expect(trace.event?.event_type).toBe('geopolitical');
    expect(trace.event?.source_name).toBe('OFAC SDN');
    expect(trace.routing?.key).toBe('geopolitical');
    expect(trace.routing?.match_columns).toEqual(['entity', 'country']);
    expect(trace.match?.match_type).toBe('legal_name');
    expect(trace.match?.match_reasons).toEqual(['法人全称精确匹配']);
    expect(trace.match?.match_evidence).toEqual([{supplier_id: 3, decision: 'exact'}]);
    expect(trace.score?.total).toBe(92);
    expect(trace.score?.level).toBe('P1');
    expect(trace.score?.level_cap).toBe('P2');
    expect(trace.score?.forced_rule).toEqual({name: 'sanctions_entity_hit'});
    expect(trace.samples).toEqual([
      {
        id: 7,
        supplier_id: 3,
        supplier_name: '示例供应商',
        level: 'P1',
        event_summary: '制裁清单命中示例事件',
        updated_at: '2026-09-15T02:00:00Z',
      },
    ]);
    vi.unstubAllGlobals();
  });

  it('testRuleEngine 透传未保存草稿字段，且不带草稿时保持旧载荷形状', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson(sandboxFixture));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-sandbox'});

    const legacy: SandboxRequest = {
      event_type: 'geopolitical',
      event_subtype: null,
      severity: 'critical',
      organizations: [{name: '示例公司', aliases: [], registry_no: null}],
      locations: [],
      affected_products: [],
      affected_industries: [],
      summary: '草稿预览样例事件',
      credibility: 80,
      has_published_at: true,
    };
    const legacyResult = await api.testRuleEngine(legacy);

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe('/api/v1/rule-engine/test');
    const [, legacyOptions] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(legacyOptions.method).toBe('POST');
    expect(JSON.parse(String(legacyOptions.body))).toEqual(legacy);

    // 响应契约：dimension 与候选 supplier_id/score/level 必须来自真实读模型
    expect(legacyResult.dimension).toEqual({
      key: 'geopolitical',
      label: '地缘政治',
      match_columns: ['entity', 'country'],
    });
    expect(legacyResult.candidates).toHaveLength(1);
    expect(legacyResult.candidates[0]?.supplier_id).toBe(42);
    expect(legacyResult.candidates[0]?.supplier_name).toBe('示例公司');
    expect(legacyResult.candidates[0]?.score).toBe(88);
    expect(legacyResult.candidates[0]?.level).toBe('P1');
    expect(legacyResult.candidates[0]?.reasons).toEqual(['法人全称精确匹配']);

    const draftResult = await api.testRuleEngine({
      ...legacy,
      dimension_key: 'geopolitical',
      draft_config: {match_columns: ['entity'], severity_scores: {critical: 30}},
      global_config: {severity_scores: {high: 20}},
    });

    const [, draftOptions] = fetchMock.mock.calls[1] as [string, RequestInit];
    const draftBody = JSON.parse(String(draftOptions.body)) as Record<string, unknown>;
    expect(draftBody.dimension_key).toBe('geopolitical');
    expect(draftBody.draft_config).toEqual({match_columns: ['entity'], severity_scores: {critical: 30}});
    expect(draftBody.global_config).toEqual({severity_scores: {high: 20}});
    expect(new Headers(draftOptions.headers).get('X-CSRF-Token')).toBe('csrf-sandbox');
    expect(draftResult.dimension?.key).toBe('geopolitical');
    expect(draftResult.candidates[0]?.level).toBe('P1');
    vi.unstubAllGlobals();
  });
});

import {describe, expect, it} from 'vitest';
import {
  mapDataSource,
  mapRiskAlert,
  mapSupplier,
  type DataSourceRead,
  type RiskAlertRead,
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
      industry: '电子元器件',
      raw_materials: [],
      enabled: true,
      aliases: [],
      sites: [{id: 1, site_name: '深圳工厂', country_code: 'CN', region: '广东', city: '深圳', address: '深圳市', latitude: null, longitude: null}],
      products: [{id: 1, name: '功率器件', keywords: []}],
    };

    expect(mapSupplier(supplier, 'P1', 90)).toMatchObject({
      id: '3', code: 'SUP-0003', monitoringStatus: 'high_risk', productionLocation: '深圳', riskScore: 90,
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

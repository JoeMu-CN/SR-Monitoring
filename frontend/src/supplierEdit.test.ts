import {describe, expect, it} from 'vitest';
import type {SupplierRead, SupplierUpdatePayload} from './api';
import {buildEditPayload, type EditableFields} from './supplierEdit';

/** 完整 SupplierRead，包含 2 地点、2 产品、别名、raw_materials、enabled=false */
const fullDetail: SupplierRead = {
  id: 42,
  supplier_code: 'SUP-0042',
  legal_name: '测试电子有限公司',
  country_code: 'CN',
  registry_no: '91330100MA1234567X',
  registration_address: '浙江省杭州市滨江区',
  industry: '电子元件',
  raw_materials: ['硅片', '铜线'],
  enabled: false,
  updated_at: '2026-09-01T10:00:00Z',
  aliases: [
    {id: 10, alias: '测试电子', language: 'zh'},
    {id: 11, alias: 'Test Electronics', language: 'en'},
  ],
  sites: [
    {id: 20, site_name: '杭州工厂', country_code: 'CN', region: '浙江省', city: '杭州市', district: '滨江区', address: '江陵路100号', latitude: 30.1, longitude: 120.2},
    {id: 21, site_name: '深圳工厂', country_code: 'CN', region: '广东省', city: '深圳市', district: '南山区', address: '科技园2号', latitude: 22.5, longitude: 114.0},
  ],
  products: [
    {id: 30, name: '功率半导体', keywords: ['MOSFET', 'IGBT']},
    {id: 31, name: '光学镜片', keywords: ['镜头']},
  ],
};

describe('buildEditPayload', () => {
  const editable: EditableFields = {
    legal_name: '测试电子有限公司（更名）',
    country_code: 'JP',
    registry_no: '91330100MA1234567X',
    registration_address: '浙江省杭州市滨江区',
    industry: '电子元件',
    // 仅修改第一个地点名称
    site_overrides: {site_name: '东京工厂'},
    // 仅修改第一个产品名称
    product_overrides: {name: '功率半导体（升级版）'},
  };

  it('保留 enabled=false，不被编辑 payload 覆盖为 true', () => {
    const payload = buildEditPayload(fullDetail, editable);
    expect(payload.enabled).toBe(false);
  });

  it('保留 raw_materials 原样', () => {
    const payload = buildEditPayload(fullDetail, editable);
    expect(payload.raw_materials).toEqual(['硅片', '铜线']);
  });

  it('保留全部 aliases 不丢失', () => {
    const payload = buildEditPayload(fullDetail, editable);
    expect(payload.aliases).toHaveLength(2);
    expect(payload.aliases[0]).toEqual({id: 10, alias: '测试电子', language: 'zh'});
    expect(payload.aliases[1]).toEqual({id: 11, alias: 'Test Electronics', language: 'en'});
  });

  it('保留第二个地点完整不变（含坐标和地址）', () => {
    const payload = buildEditPayload(fullDetail, editable);
    expect(payload.sites).toHaveLength(2);
    const secondSite = payload.sites[1];
    expect(secondSite).toEqual({
      id: 21,
      site_name: '深圳工厂',
      country_code: 'CN',
      region: '广东省',
      city: '深圳市',
      district: '南山区',
      address: '科技园2号',
      latitude: 22.5,
      longitude: 114.0,
    });
  });

  it('修改第一个地点名称时保留其余字段', () => {
    const payload = buildEditPayload(fullDetail, editable);
    const firstSite = payload.sites[0];
    expect(firstSite.site_name).toBe('东京工厂');
    expect(firstSite.country_code).toBe('CN');
    expect(firstSite.region).toBe('浙江省');
    expect(firstSite.city).toBe('杭州市');
    expect(firstSite.latitude).toBe(30.1);
    expect(firstSite.longitude).toBe(120.2);
  });

  it('保留第二个产品不变', () => {
    const payload = buildEditPayload(fullDetail, editable);
    expect(payload.products).toHaveLength(2);
    expect(payload.products[1]).toEqual({id: 31, name: '光学镜片', keywords: ['镜头']});
  });

  it('修改第一个产品名称时保留关键词', () => {
    const payload = buildEditPayload(fullDetail, editable);
    expect(payload.products[0].name).toBe('功率半导体（升级版）');
    expect(payload.products[0].keywords).toEqual(['MOSFET', 'IGBT']);
  });

  it('editable 中 site_overrides/product_overrides 为 undefined 时保持原始值', () => {
    const editableNoOverrides: EditableFields = {
      legal_name: '新名称',
      country_code: 'CN',
    };
    const payload = buildEditPayload(fullDetail, editableNoOverrides);
    expect(payload.sites[0].site_name).toBe('杭州工厂');
    expect(payload.products[0].name).toBe('功率半导体');
  });

  it('无地点时不应凭空创建', () => {
    const detailNoSites: SupplierRead = {...fullDetail, sites: [], products: [{id: 30, name: '功率半导体', keywords: []}]};
    const editableNoSite: EditableFields = {legal_name: '新', country_code: 'CN'};
    const payload = buildEditPayload(detailNoSites, editableNoSite);
    expect(payload.sites).toHaveLength(0);
  });

  it('无产品时不应凭空创建', () => {
    const detailNoProducts: SupplierRead = {...fullDetail, products: []};
    const editableNoProduct: EditableFields = {legal_name: '新', country_code: 'CN'};
    const payload = buildEditPayload(detailNoProducts, editableNoProduct);
    expect(payload.products).toHaveLength(0);
  });

  it('返回的 payload 不含 supplier_code 和 updated_at', () => {
    const payload = buildEditPayload(fullDetail, editable) as Record<string, unknown>;
    expect(payload.supplier_code).toBeUndefined();
    expect(payload.updated_at).toBeUndefined();
  });
});

describe('buildEditPayload — 409 冲突场景：payload 携带 expected_updated_at', () => {
  it('从 SupplierRead.updated_at 派生 expected_updated_at', () => {
    const payload = buildEditPayload(fullDetail, {
      legal_name: '新名称',
      country_code: 'CN',
    });
    expect(payload.expected_updated_at).toBe('2026-09-01T10:00:00Z');
  });
});

describe('buildEditPayload — 子记录 ID 保留（后端稳定定位）', () => {
  it('所有站点保留原始 id，后端可按 id 定位并更新', () => {
    const payload = buildEditPayload(fullDetail, {
      legal_name: '测试电子有限公司',
      country_code: 'CN',
      site_overrides: {site_name: '东京工厂'},
    });
    expect(payload.sites).toHaveLength(2);
    expect((payload.sites[0] as Record<string, unknown>).id).toBe(20);
    expect((payload.sites[1] as Record<string, unknown>).id).toBe(21);
  });

  it('所有产品保留原始 id', () => {
    const payload = buildEditPayload(fullDetail, {
      legal_name: '测试电子有限公司',
      country_code: 'CN',
      product_overrides: {name: '新功率半导体'},
    });
    expect(payload.products).toHaveLength(2);
    expect((payload.products[0] as Record<string, unknown>).id).toBe(30);
    expect((payload.products[1] as Record<string, unknown>).id).toBe(31);
  });

  it('所有别名保留原始 id', () => {
    const payload = buildEditPayload(fullDetail, {
      legal_name: '测试电子有限公司',
      country_code: 'CN',
    });
    expect(payload.aliases).toHaveLength(2);
    expect((payload.aliases[0] as Record<string, unknown>).id).toBe(10);
    expect((payload.aliases[1] as Record<string, unknown>).id).toBe(11);
  });
});

describe('buildEditPayload — null 显式清空 vs undefined 未编辑', () => {
  it('registry_no 传 null 时应清空为 null，不回退到原值', () => {
    const payload = buildEditPayload(fullDetail, {
      legal_name: '测试电子有限公司',
      country_code: 'CN',
      registry_no: null,
    });
    expect(payload.registry_no).toBeNull();
  });

  it('registry_no 传 undefined 时保留原值', () => {
    const payload = buildEditPayload(fullDetail, {
      legal_name: '测试电子有限公司',
      country_code: 'CN',
      // registry_no 未提供 → undefined
    });
    expect(payload.registry_no).toBe('91330100MA1234567X');
  });

  it('industry 传 null 时应清空为 null', () => {
    const payload = buildEditPayload(fullDetail, {
      legal_name: '测试电子有限公司',
      country_code: 'CN',
      industry: null,
    });
    expect(payload.industry).toBeNull();
  });

  it('registration_address 传 null 时应清空为 null', () => {
    const payload = buildEditPayload(fullDetail, {
      legal_name: '测试电子有限公司',
      country_code: 'CN',
      registration_address: null,
    });
    expect(payload.registration_address).toBeNull();
  });

  it('site_overrides latitude 传 null 时应清空为 null', () => {
    const payload = buildEditPayload(fullDetail, {
      legal_name: '测试电子有限公司',
      country_code: 'CN',
      site_overrides: {latitude: null, longitude: null},
    });
    expect(payload.sites[0].latitude).toBeNull();
    expect(payload.sites[0].longitude).toBeNull();
  });

  it('site_overrides latitude 未提供时保留原值', () => {
    const payload = buildEditPayload(fullDetail, {
      legal_name: '测试电子有限公司',
      country_code: 'CN',
      site_overrides: {site_name: '东京工厂'},
      // latitude/longitude 未提供
    });
    expect(payload.sites[0].latitude).toBe(30.1);
    expect(payload.sites[0].longitude).toBe(120.2);
  });
});

describe('buildEditPayload — 单产品名编辑保留 keywords', () => {
  it('仅修改产品名时，keywords 从原记录继承', () => {
    const payload = buildEditPayload(fullDetail, {
      legal_name: '测试电子有限公司',
      country_code: 'CN',
      product_overrides: {name: 'IGBT模块'},
    });
    expect(payload.products[0].name).toBe('IGBT模块');
    expect(payload.products[0].keywords).toEqual(['MOSFET', 'IGBT']);
  });
});

describe('buildEditPayload — 首条地点编辑保留 country/coordinates', () => {
  it('仅修改 site_name 时，country_code/latitude/longitude 保留原值', () => {
    const payload = buildEditPayload(fullDetail, {
      legal_name: '测试电子有限公司',
      country_code: 'CN',
      site_overrides: {site_name: '上海工厂'},
    });
    const site = payload.sites[0];
    expect(site.site_name).toBe('上海工厂');
    expect(site.country_code).toBe('CN');
    expect(site.latitude).toBe(30.1);
    expect(site.longitude).toBe(120.2);
    expect(site.region).toBe('浙江省');
    expect(site.city).toBe('杭州市');
    expect(site.district).toBe('滨江区');
    expect(site.address).toBe('江陵路100号');
  });
});

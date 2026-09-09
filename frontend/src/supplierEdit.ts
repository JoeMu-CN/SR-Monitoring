import type {SupplierRead, SupplierUpdatePayload} from './api';

export interface EditableFields {
  readonly legal_name: string;
  readonly country_code: string;
  readonly registry_no?: string | null;
  readonly registration_address?: string | null;
  readonly industry?: string | null;
  readonly site_overrides?: Partial<Pick<SupplierRead['sites'][number], 'site_name' | 'country_code' | 'region' | 'city' | 'district' | 'address' | 'latitude' | 'longitude'>>;
  readonly product_overrides?: Partial<Pick<SupplierRead['products'][number], 'name' | 'keywords'>>;
}

/**
 * 从完整 SupplierRead 详情深拷贝，结合用户编辑字段构造无损更新 payload。
 *
 * 保留：
 * - enabled（不被用户编辑覆盖）
 * - raw_materials（原文）
 * - aliases（全部原样保留）
 * - 所有 sites/products 及其未编辑字段
 * - 首条地点/产品按 site_overrides/product_overrides 受控修改
 * - 其余地点/产品原样保留
 *
 * 不含 supplier_code（PUT 不允许修改）。
 * 携带 expected_updated_at 用于并发冲突检测。
 */
export function buildEditPayload(
  detail: SupplierRead,
  editable: EditableFields,
): SupplierUpdatePayload {
  // --- 站点：修改首条（如有），其余原样；保留 id 供后端稳定定位 ---
  const sites = detail.sites.map((site, index) => {
    if (index === 0 && editable.site_overrides) {
      const o = editable.site_overrides;
      return {
        id: site.id,
        site_name: o.site_name !== undefined ? o.site_name : site.site_name,
        country_code: o.country_code !== undefined ? o.country_code : site.country_code,
        region: o.region !== undefined ? o.region : site.region,
        city: o.city !== undefined ? o.city : site.city,
        district: o.district !== undefined ? o.district : site.district,
        address: o.address !== undefined ? o.address : site.address,
        latitude: o.latitude !== undefined ? o.latitude : site.latitude,
        longitude: o.longitude !== undefined ? o.longitude : site.longitude,
      };
    }
    return {
      id: site.id,
      site_name: site.site_name,
      country_code: site.country_code,
      region: site.region,
      city: site.city,
      district: site.district,
      address: site.address,
      latitude: site.latitude,
      longitude: site.longitude,
    };
  });

  // --- 产品：修改首条（如有），其余原样；保留 id 供后端稳定定位 ---
  const products = detail.products.map((product, index) => {
    if (index === 0 && editable.product_overrides) {
      const o = editable.product_overrides;
      return {
        id: product.id,
        name: o.name !== undefined ? o.name : product.name,
        keywords: o.keywords !== undefined ? o.keywords : [...product.keywords],
      };
    }
    return {
      id: product.id,
      name: product.name,
      keywords: [...product.keywords],
    };
  });

  return {
    legal_name: editable.legal_name,
    country_code: editable.country_code,
    registry_no: editable.registry_no !== undefined ? editable.registry_no : detail.registry_no,
    registration_address: editable.registration_address !== undefined ? editable.registration_address : detail.registration_address,
    industry: editable.industry !== undefined ? editable.industry : detail.industry,
    raw_materials: [...detail.raw_materials],
    enabled: detail.enabled,
    aliases: detail.aliases.map((a) => ({id: a.id, alias: a.alias, language: a.language})),
    sites,
    products,
    expected_updated_at: detail.updated_at,
  } satisfies SupplierUpdatePayload & {expected_updated_at?: string};
}

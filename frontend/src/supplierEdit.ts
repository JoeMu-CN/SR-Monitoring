import type {SupplierRead, SupplierUpdatePayload} from './api';
import type {Supplier} from './types';

export interface EditableFields {
  readonly legal_name: string;
  readonly country_code: string;
  readonly registry_no?: string | null;
  readonly registration_address?: string | null;
  readonly industry?: string | null;
  /** 结算币种：缺省=未编辑（保留详情原值），null=显式清空，字符串=新值。 */
  readonly settlement_currency?: string | null;
  readonly site_overrides?: Partial<Pick<SupplierRead['sites'][number], 'site_name' | 'country_code' | 'region' | 'city' | 'district' | 'address' | 'latitude' | 'longitude'>>;
  readonly product_overrides?: Partial<Pick<SupplierRead['products'][number], 'name' | 'keywords'>>;
}

/** 表单提交对象：Supplier 未建模结算币种，这里以交叉类型附加该可空字段，表单与 App 共享同一载荷形状。 */
export type SupplierFormSubmission = Supplier & {readonly settlementCurrency?: string | null};

/** ISO 4217 结算币种：三位大写字母（字段可空，空值另行处理）。 */
export const SETTLEMENT_CURRENCY_PATTERN = /^[A-Z]{3}$/;

/** 结算币种中文校验提示（与后端 ISO 4217 口径一致）。 */
export const SETTLEMENT_CURRENCY_ERROR = '结算币种必须为 ISO 4217 三位大写字母，例如 USD、JPY、EUR、CNY；不需要时可直接留空。';

/** 表单输入归一化：去首尾空白 + 转大写；空串/仅空白/缺省统一归一为 null（可空字段）。 */
export function normalizeSettlementCurrencyInput(value: string | null | undefined): string | null {
  const normalized = (value ?? '').trim().toUpperCase();
  return normalized === '' ? null : normalized;
}

/** 是否为合法 ISO 4217 结算币种；空值视为合法（可空字段），非法字符不做吞没，交由本函数拦截。 */
export function isValidSettlementCurrency(value: string | null | undefined): boolean {
  const normalized = normalizeSettlementCurrencyInput(value);
  return normalized === null || SETTLEMENT_CURRENCY_PATTERN.test(normalized);
}

/**
 * 从完整 SupplierRead 详情深拷贝，结合用户编辑字段构造无损更新 payload。
 *
 * 保留：
 * - enabled（不被用户编辑覆盖）
 * - raw_materials（原文）
 * - aliases（全部原样保留）
 * - settlement_currency（未编辑时保留详情原值）
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
    settlement_currency: editable.settlement_currency !== undefined ? editable.settlement_currency : detail.settlement_currency ?? null,
    raw_materials: [...detail.raw_materials],
    enabled: detail.enabled,
    aliases: detail.aliases.map((a) => ({id: a.id, alias: a.alias, language: a.language})),
    sites,
    products,
    expected_updated_at: detail.updated_at,
  } satisfies SupplierUpdatePayload & {expected_updated_at?: string};
}

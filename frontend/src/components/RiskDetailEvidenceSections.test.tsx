import React from 'react';
import {cleanup, render, screen, within} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import type {EventDetailRead, RiskAlertRead} from '../api';
import {RiskDetailEvidenceSections} from './RiskDetailEvidenceSections';

/**
 * 风险证据展示的中文标签补齐（todo 19）。
 *
 * 覆盖：
 * - `EVENT_TYPE_LABELS`：market → 市场行情、exchange_rate → 汇率波动（仅 event_subtype 为空时生效）
 * - `EVENT_SUBTYPE_LABELS`：commodity_price_move / industry_market_move / fx_volatility
 * - `MATCH_TYPE_LABELS`：currency → 结算币种（含 `+` 组合）
 * - `EVIDENCE_KEY_LABELS`：currency → 币种
 * - `EVIDENCE_VALUE_LABELS`：object_type=currency → 结算币种
 * - 对抗：未收录的枚举一律回退原文，绝不臆造中文
 */

afterEach(cleanup);

const alert = (overrides: Partial<RiskAlertRead> = {}): RiskAlertRead => ({
  id: 1,
  level: 'P2',
  score: 72,
  score_detail: {severity: 30, association: 22, rule_version: 'v1'},
  status: 'current',
  supplier_id: 12,
  supplier_name: '示例供应商',
  event_id: 10,
  event_type: 'market',
  event_subtype: null,
  event_summary: '事件摘要',
  event_start_at: '2026-08-30T08:00:00Z',
  event_end_at: null,
  confidence: 0.91,
  match_type: 'currency',
  match_reasons: ['结算币种匹配'],
  match_evidence: [],
  source_title: '示例来源',
  source_url: null,
  published_at: '2026-08-30T08:00:00Z',
  updated_at: '2026-08-31T08:00:00Z',
  expires_at: null,
  expiry_kind: 'none',
  validity_state: 'active',
  valid_until: null,
  review_due_at: null,
  validity_policy_version: 'v1',
  validity_reason: {code: 'active', anchor_source: 'published_at', details: {}},
  ...overrides,
});

const event = (overrides: Partial<EventDetailRead> = {}): EventDetailRead => ({
  id: 10,
  dedup_key: 'dedup-10',
  event_type: 'market',
  event_subtype: null,
  severity: 'high',
  summary: '事件正文',
  start_at: '2026-08-30T08:00:00Z',
  end_at: null,
  confidence: 0.88,
  created_at: '2026-08-30T08:00:00Z',
  signals: [],
  entities: [],
  locations: [],
  validity_state: 'active',
  valid_until: null,
  review_due_at: null,
  validity_policy_version: 'v1',
  validity_reason: {code: 'active', anchor_source: 'published_at', details: {}},
  ...overrides,
});

const renderSections = (overrides: {alert?: Partial<RiskAlertRead>; event?: Partial<EventDetailRead>} = {}) =>
  render(<RiskDetailEvidenceSections alert={alert(overrides.alert)} event={event(overrides.event)} />);

/** 事件类型字段所在 dd 的文本（「事件类型」是 dt，紧随其后的 dd 才是值）。 */
const eventCategoryText = (): string => {
  const dt = screen.getByText('事件类型');
  return (dt.parentElement?.querySelector('dd') as HTMLElement).textContent ?? '';
};

describe('RiskDetailEvidenceSections 行情事件类型标签（todo 19）', () => {
  it('event_type=market 且细类为空时显示「市场行情」', () => {
    renderSections({event: {event_type: 'market', event_subtype: null}});

    expect(eventCategoryText()).toBe('市场行情');
  });

  it('event_type=exchange_rate 且细类为空时显示「汇率波动」', () => {
    renderSections({event: {event_type: 'exchange_rate', event_subtype: null}});

    expect(eventCategoryText()).toBe('汇率波动');
  });

  it.each([
    ['commodity_price_move', '大宗商品价格异动'],
    ['industry_market_move', '行业行情异动'],
    ['fx_volatility', '汇率波动'],
  ] as const)('细类 %s 显示「%s」（细类优先于类型）', (subtype, label) => {
    renderSections({event: {event_type: 'market', event_subtype: subtype}});

    expect(eventCategoryText()).toBe(label);
  });

  it('未收录的事件类型/细类回退原始枚举值（对抗：不臆造中文）', () => {
    renderSections({event: {event_type: 'market_exchange', event_subtype: null}});
    expect(eventCategoryText()).toBe('market_exchange');

    cleanup();
    renderSections({event: {event_type: 'market', event_subtype: 'rate_shock'}});
    expect(eventCategoryText()).toBe('rate_shock');
  });
});

describe('RiskDetailEvidenceSections 币种关联标签（todo 19）', () => {
  it('match_type=currency 显示「结算币种」', () => {
    renderSections({alert: {match_type: 'currency'}});

    const matchType = screen.getByText('匹配类型').parentElement as HTMLElement;
    expect(within(matchType).getByText('结算币种')).toBeInTheDocument();
  });

  it('组合 match_type=currency+country 逐段映射为「结算币种 + 国别」', () => {
    renderSections({alert: {match_type: 'currency+country'}});

    const matchType = screen.getByText('匹配类型').parentElement as HTMLElement;
    expect(within(matchType).getByText('结算币种 + 国别')).toBeInTheDocument();
  });

  it('匹配证据键 currency 显示「币种」，object_type=currency 显示「结算币种」', () => {
    renderSections({
      alert: {
        match_evidence: [{object_type: 'currency', supplier_id: 1, currency: 'USD'}],
      },
    });

    expect(screen.getByText('币种：')).toBeInTheDocument();
    expect(screen.getByText('对象类型：')).toBeInTheDocument();
    // 同一段证据里的 object_type 取值 currency 映射为结算币种
    expect(screen.getByText('对象类型：').parentElement as HTMLElement).toHaveTextContent('结算币种');
  });

  it('未收录的匹配类型/证据键与取值回退原文（对抗：不臆造中文）', () => {
    renderSections({
      alert: {
        match_type: 'currency_rate',
        match_evidence: [{object_type: 'currency_rate', currency_code: 'USD'}],
      },
    });

    const matchType = screen.getByText('匹配类型').parentElement as HTMLElement;
    expect(within(matchType).getByText('currency_rate')).toBeInTheDocument();
    expect(screen.getByText('currency_code：')).toBeInTheDocument();
    expect(screen.getByText('对象类型：').parentElement as HTMLElement).toHaveTextContent('currency_rate');
  });
});

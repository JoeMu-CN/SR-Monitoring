import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App.vue'

function mockFetch(payload: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
    }),
  )
}

describe('App', () => {
  let wrapper: VueWrapper | undefined

  beforeEach(() => {
    window.history.replaceState(null, '', '#/overview')
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.unstubAllGlobals()
  })

  it('默认显示风险总览页与顶部导航', async () => {
    mockFetch({
      level_counts: [
        { level: 'P1', count: 1 },
        { level: 'P2', count: 0 },
        { level: 'P3', count: 0 },
        { level: 'P4', count: 0 },
      ],
      total_current: 1,
      today_new: 1,
      type_distribution: [{ event_type: 'weather', count: 1 }],
      recent_alerts: [
        {
          id: 1,
          level: 'P1',
          score: 100,
          status: 'current',
          supplier_name: '测试供应商有限公司',
          event_type: 'compliance',
          event_summary: '制裁名单命中',
          confidence: 0.95,
          match_type: 'registry_no',
          match_reasons: [],
          match_evidence: [],
          source_title: '公告',
          source_url: null,
          published_at: null,
          updated_at: '2026-08-08T08:00:00Z',
        },
      ],
      sources: [],
    })
    wrapper = mount(App)
    await flushPromises()

    expect(wrapper.text()).toContain('供应风险监控')
    expect(wrapper.text()).toContain('总览')
    expect(wrapper.text()).toContain('风险总览')
    expect(wrapper.text()).toContain('测试供应商有限公司')
    expect(wrapper.text()).toContain('P1')
  })

  it('导航到当前风险页', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((path: string) =>
        Promise.resolve({
          ok: true,
          json: async () =>
            path.startsWith('/api/v1/risk-alerts')
              ? { items: [], total: 0, limit: 50, offset: 0 }
              : {
                  level_counts: [],
                  total_current: 0,
                  today_new: 0,
                  type_distribution: [],
                  recent_alerts: [],
                  sources: [],
                },
        }),
      ),
    )
    wrapper = mount(App)
    await flushPromises()

    const navLink = wrapper.findAll('nav a').find((link) => link.text() === '当前风险')
    expect(navLink).toBeDefined()
    await navLink!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('当前风险')
  })

  it('无数据时总览给出空状态', async () => {
    mockFetch({
      level_counts: [
        { level: 'P1', count: 0 },
        { level: 'P2', count: 0 },
        { level: 'P3', count: 0 },
        { level: 'P4', count: 0 },
      ],
      total_current: 0,
      today_new: 0,
      type_distribution: [],
      recent_alerts: [],
      sources: [],
    })
    wrapper = mount(App)
    await flushPromises()

    expect(wrapper.text()).toContain('暂无当前风险提醒')
  })
})

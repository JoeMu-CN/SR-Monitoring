import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './App.vue'

describe('App', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('显示当前风险提醒', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          total: 1,
          items: [
            {
              id: 1,
              level: 'P2',
              score: 73,
              supplier_name: '测试供应商有限公司',
              event_type: 'weather',
              event_summary: '台风影响生产和物流',
              confidence: 0.9,
              match_reasons: ['法人全称精确匹配'],
              source_title: '台风公告',
              source_url: 'https://example.com/risk',
              published_at: '2026-08-11T08:00:00+08:00',
              updated_at: '2026-08-11T08:01:00+08:00',
            },
          ],
        }),
      }),
    )
    const wrapper = mount(App)
    await flushPromises()

    expect(wrapper.text()).toContain('当前供应风险')
    expect(wrapper.text()).toContain('测试供应商有限公司')
    expect(wrapper.text()).toContain('P2')
    expect(wrapper.text()).toContain('73 分')
  })

  it('无提醒时给出下一步指引', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ total: 0, items: [] }) }),
    )
    const wrapper = mount(App)
    await flushPromises()

    expect(wrapper.text()).toContain('暂无当前风险提醒')
    expect(wrapper.text()).toContain('前往接口文档测试链路')
  })
})

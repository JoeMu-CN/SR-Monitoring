import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import App from './App.vue'

describe('App', () => {
  it('显示本地 MVP 就绪状态', () => {
    const wrapper = mount(App)

    expect(wrapper.text()).toContain('供应商风险监控平台')
    expect(wrapper.text()).toContain('本地 MVP 基础服务已就绪')
    expect(wrapper.text()).toContain('仅限 localhost')
  })
})

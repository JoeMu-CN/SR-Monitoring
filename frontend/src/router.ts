// 轻量 hash 路由：支持 /overview、/alerts、/alerts/:id、/suppliers、/sources
import { computed, reactive } from 'vue'

export interface Route {
  name: string
  params: Record<string, string>
}

function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, '')
  const [path, query] = raw.split('?')
  const parts = path.split('/').filter(Boolean)
  const params: Record<string, string> = {}
  if (query) {
    for (const pair of query.split('&')) {
      const [key, value] = pair.split('=')
      if (key) params[key] = decodeURIComponent(value ?? '')
    }
  }
  if (parts[0] === 'alerts' && parts[1]) {
    return { name: 'alert-detail', params: { id: parts[1], ...params } }
  }
  const name = parts[0] || 'overview'
  return {
    name: ['overview', 'alerts', 'suppliers', 'sources', 'rules'].includes(name)
      ? name
      : 'overview',
    params,
  }
}

const route = reactive<Route>(parseHash(window.location.hash))

function applyRoute(next: Route) {
  route.name = next.name
  route.params = { ...next.params }
}

window.addEventListener('hashchange', () => {
  applyRoute(parseHash(window.location.hash))
})

export function navigate(name: string, params: Record<string, string> = {}) {
  const parts = [name]
  if (name === 'alert-detail' && params.id) parts.push(params.id)
  const query = Object.entries(params)
    .filter(([key]) => key !== 'id')
    .map(([key, value]) => `${key}=${encodeURIComponent(value)}`)
    .join('&')
  const suffix = query ? `?${query}` : ''
  window.location.hash = `#/${parts.join('/')}${suffix}`
}

export const currentRoute = computed(() => route.name)
export const currentParams = computed(() => route.params)

// 供组件内响应式使用的路由状态（watch 不可直接用于 reactive 深层，简单转发）
export function useRoute() {
  return { route, navigate }
}

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

interface RiskAlert {
  id: number
  level: 'P1' | 'P2' | 'P3' | 'P4'
  score: number
  supplier_name: string
  event_type: string
  event_summary: string
  confidence: number
  match_reasons: string[]
  source_title: string
  source_url: string | null
  published_at: string | null
  updated_at: string
}

interface RiskAlertResponse {
  items: RiskAlert[]
  total: number
}

const alerts = ref<RiskAlert[]>([])
const total = ref(0)
const loading = ref(true)
const error = ref('')
const refreshedAt = ref<Date | null>(null)
const levels = ['P1', 'P2', 'P3', 'P4'] as const
const levelCounts = computed(() =>
  Object.fromEntries(
    levels.map((level) => [level, alerts.value.filter((item) => item.level === level).length]),
  ),
)

const eventLabels: Record<string, string> = {
  weather: '天气',
  geological: '地质灾害',
  logistics: '物流',
  trade_policy: '贸易政策',
  geopolitical: '地缘政治',
  corporate: '企业经营',
  judicial: '司法',
  compliance: '合规',
  other: '其他',
}

async function loadAlerts() {
  loading.value = true
  error.value = ''
  try {
    const response = await globalThis.fetch('/api/v1/risk-alerts?status=current&limit=50')
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const payload = (await response.json()) as RiskAlertResponse
    alerts.value = payload.items
    total.value = payload.total
    refreshedAt.value = new Date()
  } catch {
    error.value = '当前风险加载失败，请确认本地服务正常后重试。'
  } finally {
    loading.value = false
  }
}

function formatDate(value: string | null) {
  if (!value) return '时间未披露'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

onMounted(loadAlerts)
</script>

<template>
  <main class="app-shell">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">SR</span>
        <div>
          <strong>供应风险监控</strong>
          <span>localhost · MVP</span>
        </div>
      </div>
      <nav aria-label="页面操作">
        <a href="/api/docs" target="_blank" rel="noreferrer">接口文档</a>
        <button type="button" :disabled="loading" @click="loadAlerts">
          {{ loading ? '刷新中' : '刷新风险' }}
        </button>
      </nav>
    </header>

    <section class="command-deck" aria-labelledby="page-title">
      <div class="headline">
        <p class="eyebrow">CURRENT EXPOSURE / 当前态势</p>
        <h1 id="page-title">当前供应风险</h1>
        <p>只呈现已关联到重点供应商的当前风险，不包含历史趋势与处置流程。</p>
      </div>
      <div class="total-readout" aria-label="当前风险总数">
        <span>{{ total }}</span>
        <small>条当前提醒</small>
      </div>
    </section>

    <section class="risk-scale" aria-label="本页风险等级分布">
      <div v-for="level in levels" :key="level" :class="['scale-step', level.toLowerCase()]">
        <span>{{ level }}</span>
        <strong>{{ levelCounts[level] }}</strong>
      </div>
      <p>本页按高风险优先</p>
    </section>

    <section class="content" aria-live="polite">
      <div class="section-heading">
        <div>
          <p class="eyebrow">ALERT QUEUE</p>
          <h2>风险提醒</h2>
        </div>
        <span v-if="refreshedAt">更新于 {{ formatDate(refreshedAt.toISOString()) }}</span>
      </div>

      <p v-if="error" class="message error">{{ error }}</p>
      <div v-else-if="loading" class="message">正在读取当前风险…</div>
      <div v-else-if="alerts.length === 0" class="empty-state">
        <span aria-hidden="true">○</span>
        <h3>暂无当前风险提醒</h3>
        <p>导入风险信号并完成 AI 解析与事件处理后，提醒会显示在这里。</p>
        <a href="/api/docs" target="_blank" rel="noreferrer">前往接口文档测试链路</a>
      </div>
      <div v-else class="alert-list">
        <article
          v-for="alert in alerts"
          :key="alert.id"
          :class="['alert-card', alert.level.toLowerCase()]"
        >
          <div class="alert-rail">
            <strong>{{ alert.level }}</strong>
            <span>{{ alert.score }} 分</span>
          </div>
          <div class="alert-body">
            <div class="alert-meta">
              <span>{{ eventLabels[alert.event_type] ?? alert.event_type }}</span>
              <time :datetime="alert.published_at ?? undefined">
                {{ formatDate(alert.published_at) }}
              </time>
            </div>
            <h3>{{ alert.supplier_name }}</h3>
            <p class="event-summary">{{ alert.event_summary }}</p>
            <dl>
              <div>
                <dt>关联依据</dt>
                <dd>{{ alert.match_reasons.join('；') }}</dd>
              </div>
              <div>
                <dt>AI 置信度</dt>
                <dd>{{ Math.round(alert.confidence * 100) }}%</dd>
              </div>
            </dl>
            <a v-if="alert.source_url" :href="alert.source_url" target="_blank" rel="noreferrer">
              查看来源：{{ alert.source_title }}
            </a>
            <span v-else class="source-label">来源：{{ alert.source_title }}</span>
          </div>
        </article>
      </div>
    </section>
  </main>
</template>

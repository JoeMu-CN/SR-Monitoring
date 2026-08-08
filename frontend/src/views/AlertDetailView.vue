<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  api,
  eventLabels,
  eventSubtypeLabels,
  formatDateTime,
  type EventDetail,
  type RiskAlert,
} from '../api'
import { currentParams } from '../router'

const alert = ref<RiskAlert | null>(null)
const event = ref<EventDetail | null>(null)
const loading = ref(true)
const error = ref('')

const alertId = computed(() => Number(currentParams.value.id ?? 0))

const scoreRows = computed(() => {
  if (!alert.value) return []
  const detail = alert.value.score_detail
  const entries = Object.entries(detail).filter(
    ([key]) => !['rule_version'].includes(key) && typeof detail[key] !== 'object',
  )
  return entries.map(([label, value]) => ({ label, value: String(value) }))
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    alert.value = await api.alertDetail(alertId.value)
    event.value = await api.eventDetail(alert.value.event_id)
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(alertId, () => void load())
</script>

<template>
  <div>
    <a class="back-link" href="#/alerts">← 返回当前风险</a>

    <div v-if="error" class="message error">{{ error }}</div>
    <div v-else-if="loading" class="message">正在加载…</div>
    <template v-else-if="alert">
      <div class="page-heading">
        <h1>
          <span :class="['alert-badge', alert.level.toLowerCase()]">{{ alert.level }}</span>
          {{ alert.supplier_name }}
        </h1>
        <p>{{ alert.event_summary }}</p>
      </div>

      <div class="main-grid">
        <div style="display: flex; flex-direction: column; gap: 20px">
          <div class="card">
            <div class="card-header"><h3>事件信息</h3></div>
            <div class="card-body">
              <div class="detail-grid">
                <div class="detail-item">
                  <div class="label">风险等级</div>
                  <div class="value">{{ alert.level }}（{{ alert.score }} 分）</div>
                </div>
                <div class="detail-item">
                  <div class="label">事件类型</div>
                  <div class="value">
                    {{ eventLabels[alert.event_type] ?? alert.event_type }}
                    <span v-if="alert.event_subtype">
                      / {{ eventSubtypeLabels[alert.event_subtype] ?? alert.event_subtype }}
                    </span>
                  </div>
                </div>
                <div class="detail-item">
                  <div class="label">事件时间</div>
                  <div class="value">
                    {{ formatDateTime(alert.event_start_at) }} 至 {{ formatDateTime(alert.event_end_at) }}
                  </div>
                </div>
                <div class="detail-item">
                  <div class="label">AI 置信度</div>
                  <div class="value">{{ Math.round(alert.confidence * 100) }}%</div>
                </div>
                <div class="detail-item">
                  <div class="label">匹配类型</div>
                  <div class="value">{{ alert.match_type }}</div>
                </div>
                <div class="detail-item">
                  <div class="label">提醒状态</div>
                  <div class="value">{{ alert.status === 'current' ? '当前有效' : '已失效' }}</div>
                </div>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-header"><h3>匹配理由</h3></div>
            <div class="card-body">
              <div class="tag-row">
                <span v-for="reason in alert.match_reasons" :key="reason" class="tag">{{ reason }}</span>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-header"><h3>匹配证据</h3></div>
            <div class="card-body">
              <div class="evidence-list">
                <div v-for="(evidence, index) in alert.match_evidence" :key="index" class="evidence-item">
                  {{ JSON.stringify(evidence) }}
                </div>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-header"><h3>原始来源</h3></div>
            <div class="card-body">
              <p class="alert-summary">{{ alert.source_title }}</p>
              <a v-if="alert.source_url" :href="alert.source_url" target="_blank" rel="noreferrer">
                打开原始来源 →
              </a>
              <span v-else class="tag">无原文链接</span>
            </div>
          </div>
        </div>

        <div style="display: flex; flex-direction: column; gap: 20px">
          <div class="card">
            <div class="card-header"><h3>评分明细</h3></div>
            <div class="card-body">
              <div class="score-detail-grid">
                <div v-for="row in scoreRows" :key="row.label" class="score-item">
                  <span class="label">{{ row.label }}</span>
                  <span>{{ row.value }}</span>
                </div>
              </div>
            </div>
          </div>

          <div v-if="event" class="card">
            <div class="card-header"><h3>事件证据（{{ event.signals.length }} 条信号）</h3></div>
            <div class="card-body">
              <div v-if="event.signals.length === 0" class="message">无关联信号</div>
              <div v-else class="evidence-list">
                <div v-for="signal in event.signals" :key="signal.signal_id" class="evidence-item">
                  <div class="alert-supplier">{{ signal.title }}</div>
                  <p class="alert-summary">{{ signal.content }}</p>
                  <a v-if="signal.url" :href="signal.url" target="_blank" rel="noreferrer">原文 →</a>
                </div>
              </div>
            </div>
          </div>

          <div v-if="event" class="card">
            <div class="card-header"><h3>涉及主体与地点</h3></div>
            <div class="card-body">
              <div class="tag-row">
                <span v-for="entity in event.entities" :key="String(entity.name)" class="tag">
                  {{ entity.name }}<template v-if="entity.registry_no">（{{ entity.registry_no }}）</template>
                </span>
                <span v-for="location in event.locations" :key="String(location.name)" class="tag">
                  📍 {{ location.name }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

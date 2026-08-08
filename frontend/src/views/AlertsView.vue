<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api, eventLabels, formatTime, type RiskAlert } from '../api'
import { navigate } from '../router'

const alerts = ref<RiskAlert[]>([])
const total = ref(0)
const loading = ref(true)
const error = ref('')
const levelFilter = ref('')
const statusFilter = ref('current')
const offset = ref(0)
const limit = 50

async function load() {
  loading.value = true
  error.value = ''
  try {
    const payload = await api.alerts({
      status: statusFilter.value,
      level: levelFilter.value || undefined,
      limit,
      offset: offset.value,
    })
    alerts.value = payload.items
    total.value = payload.total
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function resetAndLoad() {
  offset.value = 0
  void load()
}

function prevPage() {
  if (offset.value > 0) {
    offset.value -= limit
    void load()
  }
}

function nextPage() {
  if (offset.value + limit < total.value) {
    offset.value += limit
    void load()
  }
}

function openAlert(alert: RiskAlert) {
  navigate('alert-detail', { id: String(alert.id) })
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-heading">
      <h1>当前风险</h1>
      <p>按等级筛选当前有效提醒；也可查看已失效提醒。</p>
    </div>

    <div class="toolbar">
      <select v-model="levelFilter" @change="resetAndLoad">
        <option value="">全部等级</option>
        <option value="P1">P1 重大风险</option>
        <option value="P2">P2 高风险</option>
        <option value="P3">P3 中风险</option>
        <option value="P4">P4 低风险</option>
      </select>
      <select v-model="statusFilter" @change="resetAndLoad">
        <option value="current">当前有效</option>
        <option value="expired">已失效</option>
      </select>
      <button class="primary" :disabled="loading" @click="resetAndLoad">
        {{ loading ? '加载中' : '刷新' }}
      </button>
    </div>

    <div v-if="error" class="message error">{{ error }}</div>
    <div v-else-if="loading" class="message">正在加载…</div>
    <div v-else-if="alerts.length === 0" class="message">暂无提醒，导入风险信号并完成处理后显示在这里。</div>
    <div v-else class="alert-list">
      <div
        v-for="alert in alerts"
        :key="alert.id"
        :class="['alert-card', alert.level.toLowerCase()]"
        role="button"
        tabindex="0"
        @click="openAlert(alert)"
        @keydown.enter="openAlert(alert)"
      >
        <div class="alert-top">
          <span :class="['alert-badge', alert.level.toLowerCase()]">{{ alert.level }}</span>
          <span class="alert-type">{{ eventLabels[alert.event_type] ?? alert.event_type }}</span>
          <span class="alert-time">{{ formatTime(alert.published_at) }}</span>
        </div>
        <div class="alert-supplier">{{ alert.supplier_name }}</div>
        <div class="alert-summary">{{ alert.event_summary }}</div>
        <div class="alert-footer">
          <span>评分 <span class="score">{{ alert.score }}</span></span>
          <span>AI 置信度 {{ Math.round(alert.confidence * 100) }}%</span>
          <span>匹配 {{ alert.match_type }}</span>
          <span v-if="alert.source_url">来源：{{ alert.source_title }}</span>
        </div>
      </div>
    </div>

    <div v-if="total > limit" class="pagination">
      <button :disabled="offset === 0" @click="prevPage">上一页</button>
      <span>第 {{ offset / limit + 1 }} 页 · 共 {{ total }} 条</span>
      <button :disabled="offset + limit >= total" @click="nextPage">下一页</button>
    </div>
  </div>
</template>

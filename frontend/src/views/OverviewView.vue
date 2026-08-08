<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  api,
  eventLabels,
  formatTime,
  type DashboardSummary,
} from '../api'
import { navigate } from '../router'

const summary = ref<DashboardSummary | null>(null)
const loading = ref(true)
const error = ref('')

const levelMeta: Record<string, { label: string; cls: string }> = {
  P1: { label: '重大风险', cls: 'p1' },
  P2: { label: '高风险', cls: 'p2' },
  P3: { label: '中风险', cls: 'p3' },
  P4: { label: '低风险', cls: 'p4' },
}

const maxTypeCount = computed(() =>
  Math.max(1, ...(summary.value?.type_distribution.map((item) => item.count) ?? [1])),
)

function sourceStatus(source: { last_run_status: string | null }) {
  if (!source.last_run_status) return { cls: 'err', text: '未运行' }
  if (source.last_run_status === 'succeeded') return { cls: 'ok', text: '正常' }
  if (source.last_run_status === 'failed') return { cls: 'err', text: '失败' }
  return { cls: 'warn', text: '运行中' }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    summary.value = await api.dashboard()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function openAlert(alert: { id: number }) {
  navigate('alert-detail', { id: String(alert.id) })
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-heading">
      <h1>风险总览</h1>
      <p>只呈现已关联到重点供应商的当前风险，不包含历史趋势与处置流程。</p>
    </div>

    <div v-if="error" class="message error">{{ error }}</div>
    <div v-else-if="loading" class="message">正在加载总览…</div>
    <template v-else-if="summary">
      <div class="summary-row">
        <div v-for="level in ['P1', 'P2', 'P3', 'P4']" :key="level" :class="['summary-card', levelMeta[level].cls]">
          <div class="card-label">{{ level }} · {{ levelMeta[level].label }}</div>
          <div class="card-value">
            {{ summary.level_counts.find((item) => item.level === level)?.count ?? 0 }}
          </div>
          <div class="card-footer">共 {{ summary.total_current }} 条当前提醒</div>
        </div>
      </div>

      <div class="main-grid">
        <div class="card">
          <div class="card-header">
            <h3>最近风险提醒</h3>
            <a class="card-action" href="#/alerts">查看全部 {{ summary.total_current }} 条 →</a>
          </div>
          <div class="card-body">
            <div v-if="summary.recent_alerts.length === 0" class="message">暂无当前风险提醒</div>
            <div v-else class="alert-list">
              <div
                v-for="alert in summary.recent_alerts"
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
                  <span>{{ alert.match_type }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div style="display: flex; flex-direction: column; gap: 20px">
          <div class="card">
            <div class="card-header"><h3>类型分布</h3></div>
            <div class="card-body">
              <div v-if="summary.type_distribution.length === 0" class="message">暂无数据</div>
              <div v-else class="chart-placeholder">
                <div v-for="item in summary.type_distribution" :key="item.event_type" class="chart-group">
                  <span class="chart-value">{{ item.count }}</span>
                  <div class="chart-bars">
                    <div
                      class="chart-bar"
                      :style="{
                        height: `${Math.max(10, (item.count / maxTypeCount) * 120)}px`,
                        background: 'var(--accent)',
                      }"
                    ></div>
                  </div>
                  <span class="chart-label">{{ eventLabels[item.event_type] ?? item.event_type }}</span>
                </div>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-header"><h3>数据源状态</h3><a class="card-action" href="#/sources">管理 →</a></div>
            <div class="card-body">
              <div class="source-grid">
                <div v-for="source in summary.sources" :key="source.id" class="source-card">
                  <div :class="['source-icon', sourceStatus(source).cls]">
                    {{ sourceStatus(source).cls === 'ok' ? '✓' : '!' }}
                  </div>
                  <div class="source-info">
                    <div class="source-name">{{ source.name }}</div>
                    <div class="source-detail">
                      {{ source.last_run_at ? `最后运行 ${formatTime(source.last_run_at)}` : '尚未运行' }}
                    </div>
                  </div>
                  <span :class="['source-status', sourceStatus(source).cls]">{{ sourceStatus(source).text }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

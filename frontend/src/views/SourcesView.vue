<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api, formatDateTime, type CollectionRun, type DataSourceItem } from '../api'

const sources = ref<DataSourceItem[]>([])
const runs = ref<CollectionRun[]>([])
const loading = ref(true)
const error = ref('')
const runningSourceId = ref<number | null>(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    sources.value = await api.sources()
    const runPayload = await api.collectionRuns(undefined, 15)
    runs.value = runPayload.items
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function latestRun(sourceId: number): CollectionRun | undefined {
  return runs.value.find((run) => run.source_id === sourceId)
}

function statusBadge(run: CollectionRun | undefined) {
  if (!run) return { cls: 'err', text: '未运行' }
  if (run.status === 'succeeded') return { cls: 'ok', text: '正常' }
  if (run.status === 'failed') return { cls: 'err', text: '失败' }
  return { cls: 'warn', text: '运行中' }
}

async function runSource(source: DataSourceItem) {
  if (!source.enabled || runningSourceId.value !== null) return
  runningSourceId.value = source.id
  error.value = ''
  try {
    await api.runSource(source.id)
    await load()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '采集失败'
  } finally {
    runningSourceId.value = null
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-heading">
      <h1>数据源</h1>
      <p>外部风险数据源、采集运行记录与手动触发。</p>
    </div>

    <div v-if="error" class="message error">{{ error }}</div>
    <div v-else-if="loading" class="message">正在加载…</div>
    <template v-else>
      <div class="card">
        <div class="card-header"><h3>数据源列表</h3></div>
        <div class="card-body">
          <div class="source-grid">
            <div v-for="source in sources" :key="source.id" class="source-card">
              <div :class="['source-icon', statusBadge(latestRun(source.id)).cls]">
                {{ statusBadge(latestRun(source.id)).cls === 'ok' ? '✓' : '!' }}
              </div>
              <div class="source-info">
                <div class="source-name">{{ source.name }}</div>
                <div class="source-detail">
                  {{ source.code }} · 可信度 {{ source.credibility }}
                  <template v-if="source.schedule"> · {{ source.schedule }}</template>
                </div>
              </div>
              <span :class="['source-status', statusBadge(latestRun(source.id)).cls]">
                {{ statusBadge(latestRun(source.id)).text }}
              </span>
              <button
                v-if="source.source_type !== 'manual'"
                class="primary"
                :disabled="!source.enabled || runningSourceId !== null"
                @click="runSource(source)"
              >
                {{ runningSourceId === source.id ? '采集中…' : '手动采集' }}
              </button>
              <span v-else class="tag">文件导入</span>
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3>最近采集记录</h3></div>
        <div class="card-body table-wrap">
          <table>
            <thead>
              <tr>
                <th>运行 ID</th>
                <th>数据源</th>
                <th>开始时间</th>
                <th>状态</th>
                <th>抓取 / 新建 / 重复</th>
                <th>错误</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="run in runs" :key="run.id">
                <td>{{ run.id }}</td>
                <td>{{ sources.find((source) => source.id === run.source_id)?.name ?? run.source_id }}</td>
                <td>{{ formatDateTime(run.started_at) }}</td>
                <td>
                  <span :class="['source-status', statusBadge(run).cls]">{{ statusBadge(run).text }}</span>
                </td>
                <td>{{ run.fetched_count }} / {{ run.created_count }} / {{ run.duplicate_count }}</td>
                <td>{{ run.error ?? '—' }}</td>
              </tr>
              <tr v-if="runs.length === 0">
                <td colspan="6" class="message">暂无采集记录</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

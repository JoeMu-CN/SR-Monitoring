<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api, type Supplier } from '../api'

const suppliers = ref<Supplier[]>([])
const total = ref(0)
const loading = ref(true)
const error = ref('')
const keyword = ref('')
const offset = ref(0)
const limit = 50

async function load() {
  loading.value = true
  error.value = ''
  try {
    const payload = await api.suppliers({
      keyword: keyword.value || undefined,
      limit,
      offset: offset.value,
    })
    suppliers.value = payload.items
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

async function toggleEnabled(supplier: Supplier) {
  try {
    const updated = await api.toggleSupplier(supplier.id, !supplier.enabled)
    supplier.enabled = updated.enabled
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '操作失败'
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-heading">
      <h1>供应商</h1>
      <p>重点供应商清单、生产地点与供应产品。</p>
    </div>

    <div class="toolbar">
      <input v-model="keyword" placeholder="搜索名称 / 编码" @keydown.enter="resetAndLoad" />
      <button class="primary" :disabled="loading" @click="resetAndLoad">搜索</button>
      <a href="/api/v1/suppliers/import-template" target="_blank" rel="noreferrer">下载导入模板</a>
    </div>

    <div v-if="error" class="message error">{{ error }}</div>
    <div v-else-if="loading" class="message">正在加载…</div>
    <div v-else-if="suppliers.length === 0" class="message">暂无供应商，请先导入 Excel 模板。</div>
    <div v-else class="card table-wrap">
      <table>
        <thead>
          <tr>
            <th>编码</th>
            <th>法人主体</th>
            <th>国家</th>
            <th>注册编号</th>
            <th>生产地点</th>
            <th>供应产品</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="supplier in suppliers" :key="supplier.id">
            <td>{{ supplier.supplier_code }}</td>
            <td>{{ supplier.legal_name }}</td>
            <td>{{ supplier.country_code }}</td>
            <td>{{ supplier.registry_no ?? '—' }}</td>
            <td>
              <div class="tag-row">
                <span v-for="site in supplier.sites" :key="site.site_name" class="tag">
                  {{ site.site_name }}<template v-if="site.city">（{{ site.city }}）</template>
                </span>
                <span v-if="supplier.sites.length === 0" class="tag">未登记</span>
              </div>
            </td>
            <td>
              <div class="tag-row">
                <span v-for="product in supplier.products" :key="product.name" class="tag">
                  {{ product.name }}
                </span>
                <span v-if="supplier.products.length === 0" class="tag">未登记</span>
              </div>
            </td>
            <td>
              <span :class="['source-status', supplier.enabled ? 'ok' : 'err']">
                {{ supplier.enabled ? '监控中' : '已暂停' }}
              </span>
            </td>
            <td>
              <button @click="toggleEnabled(supplier)">
                {{ supplier.enabled ? '暂停' : '启用' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="total > limit" class="pagination">
      <button :disabled="offset === 0" @click="prevPage">上一页</button>
      <span>第 {{ offset / limit + 1 }} 页 · 共 {{ total }} 条</span>
      <button :disabled="offset + limit >= total" @click="nextPage">下一页</button>
    </div>
  </div>
</template>

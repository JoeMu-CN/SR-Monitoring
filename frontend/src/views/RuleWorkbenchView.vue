<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  api,
  type MatchColumnOption,
  type RuleEngineDimension,
  type SandboxResult,
} from '../api'

const dimensions = ref<RuleEngineDimension[]>([])
const matchColumns = ref<MatchColumnOption[]>([])
const eventTypeOptions = ref<MatchColumnOption[]>([])
const eventSubtypeOptions = ref<MatchColumnOption[]>([])
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const selectedKey = ref<string | null>(null)

const columnLabels: Record<string, string> = {
  entity: '主体',
  location: '地点',
  product: '产品',
  country: '国家/区域',
  industry: '行业/原材料',
}
const severityLabels: Record<string, string> = {
  critical: '重大 critical',
  high: '高 high',
  medium: '中 medium',
  low: '低 low',
}
const levelLabels: Record<string, string> = {
  P1: 'P1',
  P2: 'P2',
  P3: 'P3',
  P4: 'P4',
}

const selected = computed(() =>
  dimensions.value.find((item) => item.key === selectedKey.value) ?? null,
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    dimensions.value = await api.ruleEngine.dimensions()
    const options = await api.ruleEngine.matchColumns()
    matchColumns.value = options.match_columns.map((value) => ({
      value,
      label: columnLabels[value] ?? value,
    }))
    eventTypeOptions.value = options.event_types
    eventSubtypeOptions.value = options.event_subtypes
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function toggle(item: RuleEngineDimension) {
  try {
    const updated = await api.ruleEngine.toggle(item.key, !item.enabled)
    const index = dimensions.value.findIndex((entry) => entry.key === updated.key)
    if (index >= 0) dimensions.value[index] = updated
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '启停失败'
  }
}

// ── 规则编辑（本地副本） ────────────────────────────────────────────

const editing = reactive<{
  match_columns: string[]
  severity_scores: Record<string, number>
  association_scores: Record<string, number>
  p1_min: number
  p2_min: number
  p3_min: number
  alert_expiry_days: number
}>({
  match_columns: [],
  severity_scores: {},
  association_scores: {},
  p1_min: 85,
  p2_min: 65,
  p3_min: 40,
  alert_expiry_days: 90,
})

function selectDimension(key: string) {
  selectedKey.value = key
  const item = dimensions.value.find((entry) => entry.key === key)
  if (!item) return
  editing.match_columns = [...item.match_columns]
  editing.severity_scores = { ...item.scoring.severity_scores }
  editing.association_scores = { ...item.scoring.association_scores }
  editing.p1_min = item.scoring.p1_min
  editing.p2_min = item.scoring.p2_min
  editing.p3_min = item.scoring.p3_min
  editing.alert_expiry_days = item.scoring.alert_expiry_days
}

function toggleColumn(value: string) {
  const index = editing.match_columns.indexOf(value)
  if (index >= 0) editing.match_columns.splice(index, 1)
  else editing.match_columns.push(value)
}

async function save() {
  if (!selected.value) return
  saving.value = true
  error.value = ''
  try {
    const updated = await api.ruleEngine.update(selected.value.key, {
      config: {
        match_columns: editing.match_columns,
        severity_scores: editing.severity_scores,
        association_scores: editing.association_scores,
        p1_min: Number(editing.p1_min),
        p2_min: Number(editing.p2_min),
        p3_min: Number(editing.p3_min),
        alert_expiry_days: Number(editing.alert_expiry_days),
      },
    })
    const index = dimensions.value.findIndex((entry) => entry.key === updated.key)
    if (index >= 0) dimensions.value[index] = updated
    selectDimension(updated.key)
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '保存失败'
  } finally {
    saving.value = false
  }
}

function resetEditing() {
  if (selected.value) selectDimension(selected.value.key)
}

// ── 沙箱测试 ────────────────────────────────────────────────────────

const sandbox = reactive({
  event_type: 'compliance',
  event_subtype: '',
  severity: 'high',
  summary: '测试事件',
  organizations: '',
  locations: '',
  affected_products: '',
  affected_industries: '',
})
const sandboxResult = ref<SandboxResult | null>(null)
const sandboxError = ref('')
const testing = ref(false)

function parseLines(raw: string): string[] {
  return raw
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

async function runTest() {
  sandboxError.value = ''
  sandboxResult.value = null
  testing.value = true
  try {
    const organizations = parseLines(sandbox.organizations).map((line) => {
      const [name, registry_no = ''] = line.split(',').map((part) => part.trim())
      return { name, registry_no: registry_no || null, aliases: [] }
    })
    const locations = parseLines(sandbox.locations).map((line) => {
      const [name, country_code = 'CN', city = ''] = line
        .split(',')
        .map((part) => part.trim())
      return {
        name,
        country_code: country_code || 'CN',
        region: null,
        city: city || null,
        latitude: null,
        longitude: null,
        radius_km: null,
      }
    })
    sandboxResult.value = await api.ruleEngine.test({
      event_type: sandbox.event_type,
      event_subtype: sandbox.event_subtype || null,
      severity: sandbox.severity,
      summary: sandbox.summary,
      organizations,
      locations,
      affected_products: sandbox.affected_products
        .split(',')
        .map((part) => part.trim())
        .filter(Boolean),
      affected_industries: sandbox.affected_industries
        .split(',')
        .map((part) => part.trim())
        .filter(Boolean),
    })
  } catch (exc) {
    sandboxError.value = exc instanceof Error ? exc.message : '沙箱测试失败'
  } finally {
    testing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-heading">
      <h1>规则引擎</h1>
      <p>监控维度插件管理与规则测试：启停维度、调整匹配柱与评分参数、沙箱验证效果。</p>
    </div>

    <div v-if="error" class="message error">{{ error }}</div>
    <div v-else-if="loading" class="message">正在加载…</div>
    <div v-else class="two-col">
      <!-- 左栏：维度列表 -->
      <div class="card">
        <div class="card-header"><h3>监控维度</h3></div>
        <div class="card-body">
          <div
            v-for="item in dimensions"
            :key="item.key"
            :class="['wb-dim', { active: selectedKey === item.key }]"
            role="button"
            tabindex="0"
            @click="selectDimension(item.key)"
            @keydown.enter="selectDimension(item.key)"
          >
            <div class="wb-dim-head">
              <span class="wb-dim-label">{{ item.label }}</span>
              <label class="wb-switch" @click.stop>
                <input
                  type="checkbox"
                  :checked="item.enabled"
                  @change="toggle(item)"
                />
                <span class="wb-switch-slider"></span>
              </label>
            </div>
            <div class="wb-dim-meta">
              <code>{{ item.key }}</code>
              <span v-if="item.has_override" class="tag">已定制</span>
              <span :class="['tag', item.enabled ? 'tag-ok' : '']">
                {{ item.enabled ? '启用' : '停用' }}
              </span>
            </div>
            <div class="wb-dim-desc">{{ item.description }}</div>
            <div class="wb-dim-meta">
              事件类型：{{ item.event_types.length ? item.event_types.join(' / ') : '（未指派）' }}
            </div>
            <div class="wb-dim-meta">
              当前提醒 <strong>{{ item.active_alerts }}</strong> · 匹配柱
              {{ item.match_columns.map((col) => columnLabels[col] ?? col).join('、') }}
            </div>
          </div>
        </div>
      </div>

      <!-- 右栏：规则编辑 + 沙箱 -->
      <div v-if="selected">
        <div class="card">
          <div class="card-header">
            <h3>{{ selected.label }} · 规则编辑</h3>
            <span class="tag">{{ selected.enabled ? '启用中' : '已停用' }}</span>
          </div>
          <div class="card-body">
            <div class="wb-section">匹配柱（该维度用哪些维度关联供应商）</div>
            <div class="wb-chips">
              <button
                v-for="option in matchColumns"
                :key="option.value"
                :class="['wb-chip', { on: editing.match_columns.includes(option.value) }]"
                @click="toggleColumn(option.value)"
              >
                {{ option.label }}
              </button>
            </div>

            <div class="wb-section">严重性分值</div>
            <div class="wb-grid">
              <label v-for="(label, key) in severityLabels" :key="key" class="wb-field">
                <span>{{ label }}</span>
                <input v-model.number="editing.severity_scores[key]" type="number" min="0" max="35" />
              </label>
            </div>

            <div class="wb-section">匹配柱关联分值</div>
            <div class="wb-grid">
              <label v-for="(label, key) in columnLabels" :key="key" class="wb-field">
                <span>{{ label }}</span>
                <input v-model.number="editing.association_scores[key]" type="number" min="0" max="30" />
              </label>
            </div>

            <div class="wb-section">等级阈值与有效期</div>
            <div class="wb-grid">
              <label class="wb-field">
                <span>P1 起点分</span>
                <input v-model.number="editing.p1_min" type="number" min="0" max="100" />
              </label>
              <label class="wb-field">
                <span>P2 起点分</span>
                <input v-model.number="editing.p2_min" type="number" min="0" max="100" />
              </label>
              <label class="wb-field">
                <span>P3 起点分</span>
                <input v-model.number="editing.p3_min" type="number" min="0" max="100" />
              </label>
              <label class="wb-field">
                <span>提醒有效期（天）</span>
                <input v-model.number="editing.alert_expiry_days" type="number" min="1" max="365" />
              </label>
            </div>

            <div class="wb-section">强制规则（只读，维度声明）</div>
            <div v-if="selected.scoring.forced_rules.length" class="wb-rules">
              <div v-for="rule in selected.scoring.forced_rules" :key="rule.name" class="wb-rule">
                <span :class="['alert-badge', rule.forced_level]">{{ rule.forced_level }}</span>
                <div>
                  <div class="wb-rule-name">{{ rule.name }}</div>
                  <div class="wb-rule-desc">{{ rule.description }}</div>
                </div>
              </div>
            </div>
            <div v-else class="wb-muted">无强制规则</div>

            <div class="wb-actions">
              <button class="ghost" @click="resetEditing">重置</button>
              <button class="primary" :disabled="saving" @click="save">
                {{ saving ? '保存中…' : '保存规则' }}
              </button>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-header"><h3>沙箱测试</h3></div>
          <div class="card-body">
            <div class="wb-grid">
              <label class="wb-field">
                <span>事件类型</span>
                <select v-model="sandbox.event_type">
                  <option v-for="option in eventTypeOptions" :key="option.value" :value="option.value">
                    {{ option.label }}
                  </option>
                </select>
              </label>
              <label class="wb-field">
                <span>严重性</span>
                <select v-model="sandbox.severity">
                  <option v-for="(label, key) in severityLabels" :key="key" :value="key">
                    {{ label }}
                  </option>
                </select>
              </label>
              <label class="wb-field">
                <span>事件细类（强制规则依据）</span>
                <select v-model="sandbox.event_subtype">
                  <option value="">未识别</option>
                  <option
                    v-for="option in eventSubtypeOptions"
                    :key="option.value"
                    :value="option.value"
                  >
                    {{ option.label }}
                  </option>
                </select>
              </label>
            </div>
            <label class="wb-field">
              <span>事件摘要</span>
              <input v-model="sandbox.summary" type="text" />
            </label>
            <label class="wb-field">
              <span>涉及主体（每行一个：名称,注册号）</span>
              <textarea v-model="sandbox.organizations" rows="2"></textarea>
            </label>
            <label class="wb-field">
              <span>涉及地点（每行一个：名称,国家代码,城市）</span>
              <textarea v-model="sandbox.locations" rows="2"></textarea>
            </label>
            <label class="wb-field">
              <span>受影响产品（逗号分隔）</span>
              <input v-model="sandbox.affected_products" type="text" />
            </label>
            <label class="wb-field">
              <span>受影响行业/原材料（逗号分隔）</span>
              <input v-model="sandbox.affected_industries" type="text" />
            </label>
            <div class="wb-actions">
              <button class="primary" :disabled="testing" @click="runTest">
                {{ testing ? '评估中…' : '运行评估（不落库）' }}
              </button>
            </div>

            <div v-if="sandboxError" class="message error">{{ sandboxError }}</div>
            <div v-else-if="sandboxResult">
              <div v-if="sandboxResult.dimension" class="wb-section">
                分派维度：{{ sandboxResult.dimension.label }}
                （{{ sandboxResult.dimension.match_columns.map((col) => columnLabels[col] ?? col).join('、') }}）
              </div>
              <div v-else class="message">{{ sandboxResult.message }}</div>
              <div class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>供应商</th>
                      <th>等级</th>
                      <th>分数</th>
                      <th>匹配方式</th>
                      <th>关联分</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="candidate in sandboxResult.candidates" :key="candidate.supplier_id">
                      <td>{{ candidate.supplier_name }}</td>
                      <td><span :class="['alert-badge', candidate.level]">{{ levelLabels[candidate.level] ?? candidate.level }}</span></td>
                      <td>{{ candidate.score }}</td>
                      <td>{{ candidate.match_type }}</td>
                      <td>{{ candidate.association_score }}</td>
                    </tr>
                    <tr v-if="sandboxResult.candidates.length === 0">
                      <td colspan="5" class="message">无匹配供应商</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-else class="card">
        <div class="card-body message">从左侧选择一个维度查看与编辑规则。</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wb-dim {
  border: 1px solid var(--border, #e2e5e9);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
  cursor: pointer;
}
.wb-dim.active {
  border-color: #185fa5;
  background: #f2f7fc;
}
.wb-dim-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.wb-dim-label {
  font-weight: 500;
}
.wb-dim-meta {
  font-size: 12px;
  color: #666;
  margin-top: 4px;
}
.wb-dim-desc {
  font-size: 12px;
  color: #888;
  margin-top: 6px;
}
.wb-switch {
  position: relative;
  display: inline-block;
  width: 34px;
  height: 18px;
}
.wb-switch input {
  opacity: 0;
  width: 0;
  height: 0;
}
.wb-switch-slider {
  position: absolute;
  cursor: pointer;
  inset: 0;
  background: #ccc;
  border-radius: 18px;
  transition: 0.2s;
}
.wb-switch-slider::before {
  content: '';
  position: absolute;
  height: 14px;
  width: 14px;
  left: 2px;
  top: 2px;
  background: #fff;
  border-radius: 50%;
  transition: 0.2s;
}
.wb-switch input:checked + .wb-switch-slider {
  background: #185fa5;
}
.wb-switch input:checked + .wb-switch-slider::before {
  transform: translateX(16px);
}
.wb-section {
  font-size: 13px;
  font-weight: 500;
  margin: 14px 0 8px;
}
.wb-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.wb-chip {
  border: 1px solid #d5d9dd;
  background: #fff;
  border-radius: 14px;
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
}
.wb-chip.on {
  background: #185fa5;
  border-color: #185fa5;
  color: #fff;
}
.wb-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 8px;
}
.wb-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: #555;
}
.wb-field input,
.wb-field select,
.wb-field textarea {
  padding: 6px 8px;
  border: 1px solid #d5d9dd;
  border-radius: 6px;
  font-size: 13px;
  font-family: inherit;
}
.wb-rules {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.wb-rule {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  border: 1px solid #eceef0;
  border-radius: 6px;
  padding: 6px 8px;
}
.wb-rule-name {
  font-size: 12px;
  font-weight: 500;
}
.wb-rule-desc {
  font-size: 12px;
  color: #777;
}
.wb-muted {
  font-size: 12px;
  color: #999;
}
.wb-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 14px;
}
</style>

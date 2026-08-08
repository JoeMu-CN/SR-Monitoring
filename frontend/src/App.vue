<script setup lang="ts">
import { computed } from 'vue'

import { currentRoute, navigate } from './router'
import AlertsView from './views/AlertsView.vue'
import AlertDetailView from './views/AlertDetailView.vue'
import OverviewView from './views/OverviewView.vue'
import SourcesView from './views/SourcesView.vue'
import SuppliersView from './views/SuppliersView.vue'
import RuleWorkbenchView from './views/RuleWorkbenchView.vue'

const navItems = [
  { name: 'overview', label: '总览' },
  { name: 'alerts', label: '当前风险' },
  { name: 'suppliers', label: '供应商' },
  { name: 'sources', label: '数据源' },
  { name: 'rules', label: '规则引擎' },
]

const activeName = computed(() => currentRoute.value)

function go(name: string) {
  navigate(name)
}

function goHome() {
  navigate('overview')
}
</script>

<template>
  <header class="app-header">
    <div class="header-logo" role="button" tabindex="0" @click="goHome" @keydown.enter="goHome">
      <div class="logo-icon">SR</div>
      <div class="logo-text">供应风险监控</div>
    </div>
    <nav class="header-nav" aria-label="页面导航">
      <a
        v-for="item in navItems"
        :key="item.name"
        :class="{ active: activeName === item.name || (item.name === 'alerts' && activeName === 'alert-detail') }"
        :href="`#/${item.name}`"
        @click.prevent="go(item.name)"
      >
        {{ item.label }}
      </a>
    </nav>
    <div class="header-right">
      <a href="/api/docs" target="_blank" rel="noreferrer">接口文档</a>
    </div>
  </header>

  <main class="container">
    <OverviewView v-if="activeName === 'overview'" />
    <AlertsView v-else-if="activeName === 'alerts'" />
    <AlertDetailView v-else-if="activeName === 'alert-detail'" />
    <SuppliersView v-else-if="activeName === 'suppliers'" />
    <SourcesView v-else-if="activeName === 'sources'" />
    <RuleWorkbenchView v-else-if="activeName === 'rules'" />
  </main>
</template>

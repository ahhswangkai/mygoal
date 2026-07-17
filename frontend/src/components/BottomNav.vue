<template>
  <nav class="main-bottom-nav" aria-label="主导航">
    <router-link
      v-for="item in items"
      :key="item.name"
      :to="item.to"
      class="main-nav-item"
      :class="{ active: route.name === item.name }"
    >
      <span class="main-nav-icon" aria-hidden="true">{{ item.icon }}</span>
      <span>{{ item.label }}</span>
    </router-link>
  </nav>
</template>

<script setup>
import { useRoute } from 'vue-router'

const route = useRoute()
const items = [
  { name: 'home', to: '/home', label: '首页', icon: '⚽' },
  { name: 'calculator', to: '/calculator', label: '计算器', icon: '▦' },
  { name: 'bets', to: '/bets', label: '投注记录', icon: '▤' },
  { name: 'results', to: '/results', label: '赛果', icon: '✓' },
  { name: 'stats', to: '/stats', label: '统计', icon: '⌁' }
]
</script>

<style scoped>
.main-bottom-nav {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
</style>

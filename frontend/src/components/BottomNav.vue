<template>
  <nav class="main-bottom-nav" aria-label="主导航">
    <router-link
      v-for="item in items"
      :key="item.name"
      :to="item.to"
      class="main-nav-item"
      :class="{ active: activeTab === item.name }"
    >
      <span class="main-nav-icon" aria-hidden="true">{{ item.icon }}</span>
      <span>{{ item.label }}</span>
    </router-link>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const activeTab = computed(() => route.meta.navTab || route.name)
const items = [
  { name: 'home', to: '/home', label: '首页', icon: '⚽' },
  { name: 'recommendations', to: '/recommendations', label: '推荐', icon: '★' },
  { name: 'calculator', to: '/calculator', label: '计算器', icon: '▦' },
  { name: 'results', to: '/results', label: '赛果', icon: '✓' },
  { name: 'mine', to: '/mine', label: '我的', icon: '◉' }
]
</script>

<style scoped>
.main-bottom-nav {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
</style>

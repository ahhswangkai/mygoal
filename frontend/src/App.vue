<template>
  <router-view v-slot="{ Component, route: viewRoute }">
    <KeepAlive>
      <component
        :is="Component"
        v-if="viewRoute.meta.keepAlive"
        :key="viewRoute.name"
      />
    </KeepAlive>
    <component
      :is="Component"
      v-if="!viewRoute.meta.keepAlive"
      :key="viewRoute.fullPath"
    />
  </router-view>
  <BottomNav v-if="showBottomNav" />
  <AuthModal />
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import AuthModal from './components/AuthModal.vue'
import BottomNav from './components/BottomNav.vue'
import { loadCurrentUser } from './auth'

const route = useRoute()
const showBottomNav = computed(() => !!route.meta.mainTab)

onMounted(() => loadCurrentUser())
</script>

<style>
/* 全局样式在 style.css 中 */
</style>

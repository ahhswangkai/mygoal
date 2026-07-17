<template>
  <button type="button" class="header-account-btn" @click="openAuth()">
    <span v-if="authState.user" class="header-account-avatar">{{ initial }}</span>
    <span class="header-account-text">{{ authState.user ? authState.user.display_name : '登录' }}</span>
  </button>
</template>

<script setup>
import { computed } from 'vue'
import { authState, openAuth } from '../auth'

const initial = computed(() => (authState.user?.display_name || authState.user?.username || '我').slice(0, 1))
</script>

<template>
  <div class="app-container primary-page mine-page">
    <header class="top-header">
      <span class="header-side-spacer" aria-hidden="true"></span>
      <span class="header-title">我的</span>
      <AccountButton />
    </header>

    <main class="mine-content">
      <section class="mine-account-card">
        <div class="mine-avatar" aria-hidden="true">{{ accountInitial }}</div>
        <div class="mine-account-info">
          <strong>{{ accountName }}</strong>
          <span>{{ accountHint }}</span>
        </div>
        <button type="button" @click="openAuth()">
          {{ authState.user ? '账号设置' : '登录 / 注册' }}
        </button>
      </section>

      <section class="mine-services">
        <h2>我的服务</h2>

        <router-link to="/bets" class="mine-service-card">
          <span class="mine-service-icon mine-service-icon--records" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M7 3.75h10A2.25 2.25 0 0 1 19.25 6v12A2.25 2.25 0 0 1 17 20.25H7A2.25 2.25 0 0 1 4.75 18V6A2.25 2.25 0 0 1 7 3.75Z" />
              <path d="M8 8h8M8 12h8M8 16h5" />
            </svg>
          </span>
          <span class="mine-service-copy">
            <strong>投注记录</strong>
            <small>查看投注方案、结算结果与盈亏明细</small>
          </span>
          <span class="mine-service-arrow" aria-hidden="true">›</span>
        </router-link>

        <router-link to="/stats" class="mine-service-card">
          <span class="mine-service-icon mine-service-icon--stats" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M5 20V10M12 20V4M19 20v-7" />
              <path d="M3.5 20.25h17" />
            </svg>
          </span>
          <span class="mine-service-copy">
            <strong>个人统计</strong>
            <small>查看月度投入、盈利率与玩法分布</small>
          </span>
          <span class="mine-service-arrow" aria-hidden="true">›</span>
        </router-link>
      </section>

      <p class="mine-privacy-note">
        <span aria-hidden="true">✓</span>
        登录后，投注记录和统计数据仅当前账号可见
      </p>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import AccountButton from '../components/AccountButton.vue'
import { authState, loadCurrentUser, openAuth } from '../auth'

const accountName = computed(() => {
  if (!authState.initialized) return '正在加载…'
  return authState.user?.display_name || '登录后查看个人数据'
})

const accountHint = computed(() => (
  authState.user
    ? `账号：${authState.user.username}`
    : '投注记录与统计数据支持账号同步'
))

const accountInitial = computed(() => (
  authState.user?.display_name || authState.user?.username || '我'
).slice(0, 1))

onMounted(() => loadCurrentUser())
</script>

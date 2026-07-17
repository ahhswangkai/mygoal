<template>
  <div class="app-container primary-page user-stats-page">
    <header class="top-header">
      <span class="header-side-spacer"></span>
      <span class="header-title">个人统计</span>
      <AccountButton />
    </header>

    <main class="user-stats-content">
      <section v-if="authState.initialized && !authState.user" class="account-gate">
        <div class="account-gate-icon">⌁</div>
        <h2>登录后查看个人统计</h2>
        <p>统计数据基于当前账号保存的投注方案生成。</p>
        <button type="button" @click="openAuth('login')">登录 / 注册</button>
      </section>

      <template v-else-if="authState.user">
        <section class="stats-month-filter">
          <div>
            <strong>统计月份</strong>
            <span>按北京时间归属月份</span>
          </div>
          <select v-model="selectedMonth" :disabled="loading" aria-label="统计月份" @change="fetchStats">
            <option v-for="month in monthOptions" :key="month" :value="month">
              {{ formatMonth(month) }}
            </option>
          </select>
        </section>

        <section class="stats-hero">
          <p>{{ authState.user.display_name }} · {{ formatMonth(selectedMonth) }}净盈亏</p>
          <strong>{{ signedMoney(stats.net_profit) }}<small> 元</small></strong>
          <span>
            已结算 {{ stats.settled_bets || 0 }} 单 · 待结算 {{ stats.pending_bets || 0 }} 单 ·
            本月投入 {{ money(stats.total_stake) }} 元
          </span>
        </section>

        <section class="personal-stat-grid">
          <article>
            <span>本月实际返还</span>
            <strong>{{ money(stats.total_return) }}</strong>
          </article>
          <article>
            <span>盈利方案</span>
            <strong>{{ stats.won_bets || 0 }} 单</strong>
          </article>
          <article>
            <span>盈利率</span>
            <strong>{{ Number(stats.win_rate || 0).toFixed(1) }}%</strong>
          </article>
          <article>
            <span>待结算</span>
            <strong>{{ stats.pending_bets || 0 }} 单</strong>
          </article>
        </section>

        <section class="stats-panel">
          <header><h2>{{ formatMonth(selectedMonth) }}每日投入与盈亏</h2><span>{{ stats.daily?.length || 0 }}天有记录</span></header>
          <div v-if="!stats.daily?.length" class="stats-panel-empty">暂无可统计的投注记录</div>
          <div v-else class="daily-chart">
            <div v-for="day in stats.daily" :key="day.date" class="daily-chart-item">
              <div class="daily-bar-shell"><i :style="{ height: barHeight(day.stake) + '%' }"></i></div>
              <span>{{ day.date.slice(5) }}</span>
              <em>投 {{ money(day.stake) }}</em>
              <strong :class="profitClass(day.profit)">{{ signedMoney(day.profit) }}</strong>
            </div>
          </div>
        </section>

        <section class="stats-panel">
          <header><h2>过关方式</h2></header>
          <div v-if="!stats.pass_distribution?.length" class="stats-panel-empty">暂无数据</div>
          <div v-else class="distribution-list">
            <div v-for="item in stats.pass_distribution" :key="item.label">
              <span>{{ item.label }}</span>
              <i><b :style="{ width: distributionWidth(item.count, stats.pass_distribution) + '%' }"></b></i>
              <strong>{{ item.count }}</strong>
            </div>
          </div>
        </section>

        <section class="stats-panel">
          <header><h2>玩法分布</h2></header>
          <div v-if="!stats.pool_distribution?.length" class="stats-panel-empty">暂无数据</div>
          <div v-else class="pool-chips">
            <span v-for="item in stats.pool_distribution" :key="item.label">
              {{ item.label }} <strong>{{ item.count }}</strong>
            </span>
          </div>
        </section>
      </template>

      <div v-else class="page-loading">正在加载统计数据…</div>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import AccountButton from '../components/AccountButton.vue'
import { apiRequest, authState, loadCurrentUser, openAuth } from '../auth'

const loading = ref(false)
const now = new Date()
const currentMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
const selectedMonth = ref(currentMonth)
const availableMonths = ref([currentMonth])
const stats = reactive({
  total_bets: 0,
  total_stake: 0,
  potential_bonus: 0,
  average_stake: 0,
  total_notes: 0,
  total_return: 0,
  net_profit: 0,
  pending_bets: 0,
  settled_bets: 0,
  won_bets: 0,
  lost_bets: 0,
  draw_bets: 0,
  win_rate: 0,
  daily: [],
  pass_distribution: [],
  pool_distribution: []
})

const monthOptions = computed(() => [...new Set([
  selectedMonth.value,
  currentMonth,
  ...availableMonths.value
])].filter(Boolean).sort().reverse())
const money = (value) => Number(value || 0).toFixed(2)
const formatMonth = (value) => {
  const [year, month] = String(value || '').split('-')
  return year && month ? `${year}年${Number(month)}月` : '本月'
}
const signedMoney = (value) => {
  const number = Number(value || 0)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}`
}
const profitClass = (value) => Number(value || 0) > 0 ? 'profit-positive' : Number(value || 0) < 0 ? 'profit-negative' : 'profit-zero'
const barHeight = (value) => {
  const max = Math.max(...(stats.daily || []).map(day => Number(day.stake || 0)), 1)
  return Math.max(8, Math.round(Number(value || 0) / max * 100))
}
const distributionWidth = (value, list) => {
  const max = Math.max(...(list || []).map(item => Number(item.count || 0)), 1)
  return Math.max(8, Math.round(Number(value || 0) / max * 100))
}

const fetchStats = async () => {
  if (!authState.user) return
  loading.value = true
  try {
    const result = await apiRequest(`/api/user/bet-stats?month=${encodeURIComponent(selectedMonth.value)}`)
    Object.assign(stats, result.data || {})
    availableMonths.value = result.data?.available_months || [selectedMonth.value]
  } catch (error) {
    if (error.status === 401) openAuth('login')
  } finally {
    loading.value = false
  }
}

watch(() => authState.user?.id, () => {
  selectedMonth.value = currentMonth
  if (authState.user) fetchStats()
})

onMounted(async () => {
  await loadCurrentUser()
  if (authState.user) fetchStats()
})
</script>

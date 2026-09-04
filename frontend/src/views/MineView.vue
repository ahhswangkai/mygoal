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

        <router-link to="/drafts" class="mine-service-card">
          <span class="mine-service-icon mine-service-icon--drafts" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M6.5 3.75h8.75L19 7.5V20.25H6.5A1.75 1.75 0 0 1 4.75 18.5v-13A1.75 1.75 0 0 1 6.5 3.75Z" />
              <path d="M14.75 3.75V8h4.25M8 12h8M8 16h5" />
            </svg>
          </span>
          <span class="mine-service-copy">
            <strong>今日草稿箱</strong>
            <small>观察计算器中选好的当天比赛与玩法</small>
          </span>
          <span class="mine-service-arrow" aria-hidden="true">›</span>
        </router-link>

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

      <section v-if="wecom.canManage" class="mine-wecom-card">
        <header>
          <div>
            <strong>企业微信通知</strong>
            <span>全日研判、赛后复盘与比赛提醒</span>
          </div>
          <em :class="{ configured: wecom.configured }">
            {{ wecom.configured ? '已连接' : '未配置' }}
          </em>
        </header>

        <div class="mine-wecom-types">
          <span :class="{ enabled: wecom.dailyAi }">全日研判</span>
          <span :class="{ enabled: wecom.aiReview }">赛后复盘</span>
          <span :class="{ enabled: wecom.liveAlerts }">比赛提醒</span>
        </div>

        <p v-if="!wecom.configured">
          请先在服务器 .env 中配置 WECOM_WEBHOOK_URL，密钥不会返回浏览器。
        </p>
        <p v-else-if="wecom.message" :class="{ error: wecom.error }">
          {{ wecom.message }}
        </p>
        <button
          type="button"
          :disabled="!wecom.configured || wecom.testing"
          @click="sendWeComTest"
        >
          {{ wecom.testing ? '正在发送…' : '发送测试消息' }}
        </button>
      </section>

      <p class="mine-privacy-note">
        <span aria-hidden="true">✓</span>
        登录后，投注记录和统计数据仅当前账号可见
      </p>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive } from 'vue'
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

const wecom = reactive({
  canManage: false,
  configured: false,
  dailyAi: false,
  aiReview: false,
  liveAlerts: false,
  testing: false,
  message: '',
  error: false
})

const loadWeComStatus = async () => {
  try {
    const response = await fetch('/api/wecom/status')
    const payload = await response.json()
    if (!response.ok || !payload.success) return
    const data = payload.data || {}
    wecom.canManage = Boolean(data.can_manage)
    wecom.configured = Boolean(data.configured && data.enabled)
    wecom.dailyAi = Boolean(data.daily_ai)
    wecom.aiReview = Boolean(data.ai_review)
    wecom.liveAlerts = Boolean(data.live_alerts)
  } catch (_) {
    // Notification status is auxiliary and must not block the Mine page.
  }
}

const sendWeComTest = async () => {
  if (!wecom.configured || wecom.testing) return
  wecom.testing = true
  wecom.message = ''
  wecom.error = false
  try {
    const response = await fetch('/api/wecom/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    })
    const payload = await response.json()
    wecom.message = payload.message || (
      response.ok ? '测试消息已发送' : '测试消息发送失败'
    )
    wecom.error = !response.ok || !payload.success
  } catch (_) {
    wecom.message = '测试消息发送失败，请检查网络'
    wecom.error = true
  } finally {
    wecom.testing = false
  }
}

onMounted(async () => {
  await loadCurrentUser()
  if (authState.user) await loadWeComStatus()
})
</script>

<style scoped>
.mine-wecom-card {
  display: grid;
  gap: 13px;
  margin-top: 14px;
  padding: 16px;
  background: #fff;
  border: 1px solid #f0e8e9;
  border-radius: 12px;
  box-shadow: 0 4px 14px rgb(31 38 53 / 5%);
}
.mine-wecom-card header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.mine-wecom-card header div {
  display: grid;
  gap: 4px;
}
.mine-wecom-card header strong {
  color: #303038;
  font-size: 15px;
}
.mine-wecom-card header span,
.mine-wecom-card p {
  color: #999;
  font-size: 10px;
  line-height: 1.55;
}
.mine-wecom-card header em {
  padding: 4px 8px;
  color: #999;
  font-size: 9px;
  font-style: normal;
  white-space: nowrap;
  background: #f0f0f2;
  border-radius: 10px;
}
.mine-wecom-card header em.configured {
  color: #14855d;
  background: #e8f7f1;
}
.mine-wecom-types {
  display: flex;
  gap: 7px;
}
.mine-wecom-types span {
  padding: 6px 9px;
  color: #aaa;
  font-size: 9px;
  background: #f4f4f6;
  border-radius: 12px;
}
.mine-wecom-types span.enabled {
  color: #b65e69;
  background: #fae9ec;
}
.mine-wecom-card p {
  margin: 0;
  padding: 9px 10px;
  background: #fafafa;
  border-radius: 7px;
}
.mine-wecom-card p.error {
  color: #d34a55;
  background: #fff1f2;
}
.mine-wecom-card > button {
  height: 37px;
  color: #fff;
  font-size: 12px;
  background: #ff3151;
  border: 0;
  border-radius: 19px;
}
.mine-wecom-card > button:disabled {
  color: #aaa;
  background: #eeeef1;
}
</style>

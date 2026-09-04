<template>
  <div class="app-container primary-page draft-box-page">
    <header class="top-header">
      <span class="header-side-spacer" aria-hidden="true"></span>
      <span class="header-title">草稿箱</span>
      <AccountButton />
    </header>

    <main class="draft-content">
      <section v-if="authState.initialized && !authState.user" class="account-gate">
        <div class="account-gate-icon">☆</div>
        <h2>登录后查看草稿箱</h2>
        <p>当天观察方案按账号保存，其他用户无法查看。</p>
        <button type="button" @click="openAuth('login')">登录 / 注册</button>
      </section>

      <template v-else-if="authState.user">
        <section class="draft-day-card">
          <div>
            <span>今日观察</span>
            <strong>{{ matchDate || '加载中…' }}</strong>
          </div>
          <em>{{ drafts.length }}个草稿</em>
          <p>草稿不会按日期清空；任意一场比赛开赛后，相关方案会自动清理。</p>
        </section>

        <div class="draft-toolbar">
          <div>
            <h2>观察方案</h2>
            <p>载入计算器时会使用最新赔率</p>
          </div>
          <button type="button" :disabled="loading" @click="fetchDrafts">
            {{ loading ? '刷新中…' : '刷新' }}
          </button>
        </div>

        <div v-if="loading && drafts.length === 0" class="page-loading">正在加载草稿…</div>

        <section v-else-if="drafts.length === 0" class="draft-empty">
          <div class="draft-empty-icon">☆</div>
          <strong>今天还没有观察方案</strong>
          <p>在计算器选好玩法后，点击底部“草稿”即可加入。</p>
          <router-link to="/calculator">去计算器选号</router-link>
        </section>

        <section v-else class="draft-list">
          <article v-for="draft in drafts" :key="draft.id" class="draft-card">
            <header>
              <div>
                <strong>{{ draftTitle(draft) }}</strong>
                <time>更新于 {{ formatTime(draft.updated_at) }}</time>
              </div>
              <button type="button" aria-label="删除草稿" @click="removeDraft(draft)">×</button>
            </header>

            <div class="draft-matches">
              <section v-for="group in groupedItems(draft)" :key="group.matchId">
                <div class="draft-match-name">
                  <span>{{ group.items[0].match_num || '比赛' }}</span>
                  <strong>{{ group.items[0].home_team }} <i>VS</i> {{ group.items[0].away_team }}</strong>
                </div>
                <div class="draft-picks">
                  <span v-for="item in group.items" :key="item.pool + item.opt">
                    {{ pickText(item) }} <b>@{{ oddsText(item.odd) }}</b>
                  </span>
                </div>
              </section>
            </div>

            <footer>
              <span>加入时赔率仅作观察记录</span>
              <button type="button" @click="loadDraft(draft)">载入计算器</button>
            </footer>
          </article>
        </section>
      </template>

      <div v-else class="page-loading">正在确认登录状态…</div>
    </main>

    <div v-if="notice" class="draft-notice">{{ notice }}</div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AccountButton from '../components/AccountButton.vue'
import { apiRequest, authState, loadCurrentUser, openAuth } from '../auth'

const CALCULATOR_DRAFT_LOAD_KEY = 'mygoal-calculator-draft-load-v1'
const router = useRouter()
const drafts = ref([])
const matchDate = ref('')
const loading = ref(false)
const notice = ref('')
let noticeTimer = null
let refreshTimer = null

const showNotice = message => {
  notice.value = message
  if (noticeTimer) window.clearTimeout(noticeTimer)
  noticeTimer = window.setTimeout(() => { notice.value = '' }, 2600)
}

const formatTime = value => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace('T', ' ')
  return date.toLocaleString('zh-CN', {
    hour12: false,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

const passText = draft => (draft.pass_counts || [])
  .map(value => Number(value) === 1 ? '单关' : `${value}关`)
  .join('、')

const draftTitle = draft => (
  `${draft.match_count || 0}场 · ${passText(draft) || '未选过关'} · ${draft.multiplier || 1}倍`
)

const groupedItems = draft => {
  const groups = new Map()
  ;(draft.selected_items || []).forEach(item => {
    const matchId = String(item.match_id || '')
    if (!groups.has(matchId)) groups.set(matchId, { matchId, items: [] })
    groups.get(matchId).items.push(item)
  })
  return [...groups.values()]
}

const poolName = pool => ({
  had: '胜平负',
  hhad: '让球',
  score: '比分',
  goals: '进球数',
  hafu: '半全场'
}[pool] || pool)

const pickText = item => {
  const label = String(item.opt || item.label || '').replace(/球$/, '')
  if (item.pool === 'hhad') {
    const handicap = Number(item.handicap)
    const line = Number.isFinite(handicap) && handicap !== 0
      ? `(${handicap > 0 ? '+' : ''}${handicap})`
      : ''
    return `${poolName(item.pool)}${item.label || label}${line}`
  }
  if (item.pool === 'had') return item.label || label
  return `${poolName(item.pool)} ${label}`
}

const oddsText = value => {
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(2) : '-'
}

const fetchDrafts = async () => {
  if (!authState.user || loading.value) return
  loading.value = true
  try {
    const result = await apiRequest('/api/user/drafts')
    drafts.value = result.data || []
    matchDate.value = result.match_date || ''
  } catch (error) {
    if (error.status === 401) openAuth('login')
    else showNotice(error.message || '草稿加载失败')
  } finally {
    loading.value = false
  }
}

const removeDraft = async draft => {
  if (!window.confirm('确定删除这个观察草稿吗？')) return
  try {
    await apiRequest(`/api/user/drafts/${draft.id}`, { method: 'DELETE' })
    drafts.value = drafts.value.filter(item => item.id !== draft.id)
    showNotice('草稿已删除')
  } catch (error) {
    showNotice(error.message || '删除失败')
  }
}

const loadDraft = draft => {
  try {
    window.sessionStorage.setItem(CALCULATOR_DRAFT_LOAD_KEY, JSON.stringify({
      match_date: draft.match_date,
      selected_items: draft.selected_items,
      pass_counts: draft.pass_counts,
      multiplier: draft.multiplier
    }))
  } catch {
    showNotice('当前浏览器无法载入草稿')
    return
  }
  router.push('/calculator')
}

watch(() => authState.user?.id, () => {
  drafts.value = []
  matchDate.value = ''
  if (authState.user) fetchDrafts()
})

onMounted(async () => {
  await loadCurrentUser()
  if (authState.user) fetchDrafts()
  refreshTimer = window.setInterval(fetchDrafts, 60 * 1000)
})

onBeforeUnmount(() => {
  if (noticeTimer) window.clearTimeout(noticeTimer)
  if (refreshTimer) window.clearInterval(refreshTimer)
})
</script>

<style scoped>
.draft-content {
  width: 100%;
  max-width: 600px;
  margin: 0 auto;
  padding: 14px 12px 28px;
}

.draft-day-card {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 5px 12px;
  padding: 16px;
  color: #7f4c14;
  background: linear-gradient(135deg, #fff8e9, #fff3d5);
  border: 1px solid #f3d8a7;
  border-radius: 13px;
}

.draft-day-card > div {
  display: grid;
  gap: 3px;
}

.draft-day-card span,
.draft-day-card p {
  color: #a57c4e;
  font-size: 11px;
}

.draft-day-card strong {
  font-size: 18px;
}

.draft-day-card em {
  align-self: center;
  padding: 5px 10px;
  font-size: 12px;
  font-style: normal;
  background: rgb(255 255 255 / 72%);
  border-radius: 14px;
}

.draft-day-card p {
  grid-column: 1 / -1;
  margin: 3px 0 0;
}

.draft-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 20px 2px 10px;
}

.draft-toolbar h2 {
  margin: 0;
  font-size: 16px;
}

.draft-toolbar p {
  margin: 4px 0 0;
  color: #999;
  font-size: 11px;
}

.draft-toolbar button {
  padding: 7px 13px;
  color: #e5414d;
  background: #fff;
  border: 1px solid #f0b8bd;
  border-radius: 16px;
}

.draft-empty {
  display: grid;
  justify-items: center;
  gap: 8px;
  padding: 48px 20px;
  color: #777;
  text-align: center;
  background: #fff;
  border-radius: 13px;
}

.draft-empty-icon {
  color: #e9a344;
  font-size: 38px;
}

.draft-empty p {
  color: #aaa;
  font-size: 12px;
}

.draft-empty a {
  margin-top: 5px;
  padding: 8px 18px;
  color: #fff;
  text-decoration: none;
  background: #ef3f4c;
  border-radius: 18px;
}

.draft-list {
  display: grid;
  gap: 12px;
}

.draft-card {
  overflow: hidden;
  background: #fff;
  border: 1px solid #efe6e7;
  border-radius: 13px;
  box-shadow: 0 4px 14px rgb(31 38 53 / 5%);
}

.draft-card > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 14px 14px 11px;
  border-bottom: 1px solid #f4eeee;
}

.draft-card > header > div {
  display: grid;
  gap: 4px;
}

.draft-card > header strong {
  font-size: 15px;
}

.draft-card time {
  color: #aaa;
  font-size: 10px;
}

.draft-card > header button {
  width: 28px;
  height: 28px;
  color: #aaa;
  font-size: 22px;
  background: transparent;
  border: 0;
}

.draft-matches > section {
  padding: 12px 14px;
}

.draft-matches > section + section {
  border-top: 1px dashed #eee;
}

.draft-match-name {
  display: flex;
  align-items: center;
  gap: 9px;
}

.draft-match-name > span {
  flex: 0 0 auto;
  padding: 3px 7px;
  color: #e94451;
  font-size: 11px;
  background: #fff0f2;
  border-radius: 5px;
}

.draft-match-name strong {
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.draft-match-name i {
  margin: 0 3px;
  color: #bbb;
  font-size: 10px;
  font-style: normal;
  font-weight: 400;
}

.draft-picks {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 9px;
}

.draft-picks span {
  padding: 5px 8px;
  color: #d93649;
  font-size: 12px;
  background: #fff5f6;
  border: 1px solid #f7d7da;
  border-radius: 7px;
}

.draft-picks b {
  font-weight: 500;
}

.draft-card > footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 14px;
  background: #fafafa;
}

.draft-card > footer span {
  color: #aaa;
  font-size: 10px;
}

.draft-card > footer button {
  flex: 0 0 auto;
  padding: 7px 13px;
  color: #fff;
  font-size: 12px;
  background: #ef3f4c;
  border: 0;
  border-radius: 16px;
}

.draft-notice {
  position: fixed;
  z-index: 900;
  bottom: 88px;
  left: 50%;
  padding: 9px 15px;
  color: #fff;
  font-size: 12px;
  white-space: nowrap;
  background: rgb(38 38 38 / 88%);
  border-radius: 18px;
  transform: translateX(-50%);
}
</style>

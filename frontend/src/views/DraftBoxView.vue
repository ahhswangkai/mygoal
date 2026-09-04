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
              <section
                v-for="group in groupedItems(draft)"
                :key="group.matchId"
                :class="{ expanded: isMatchExpanded(draft, group) }"
              >
                <button
                  type="button"
                  class="draft-match-toggle"
                  :aria-expanded="isMatchExpanded(draft, group)"
                  @click="toggleMatch(draft, group)"
                >
                  <span class="draft-match-name">
                    <span>{{ group.items[0].match_num || '比赛' }}</span>
                    <strong>{{ group.items[0].home_team }} <i>VS</i> {{ group.items[0].away_team }}</strong>
                    <i class="draft-expand-arrow" aria-hidden="true">⌄</i>
                  </span>
                  <span class="draft-picks">
                    <span v-for="item in group.items" :key="item.pool + item.opt">
                      {{ pickText(item) }} <b>@{{ oddsText(item.odd) }}</b>
                    </span>
                  </span>
                </button>

                <div v-if="isMatchExpanded(draft, group)" class="draft-match-analysis">
                  <div v-if="isInsightLoading(group)" class="draft-insight-state">
                    正在加载本场研判…
                  </div>
                  <div v-else-if="insightError(group)" class="draft-insight-state error">
                    {{ insightError(group) }}
                  </div>
                  <template v-else-if="matchInsight(group)">
                    <div class="draft-insight-head">
                      <div>
                        <span>AI 全日研判</span>
                        <strong>{{ insightPrimary(group) }}</strong>
                      </div>
                      <time>{{ formatInsightTime(matchInsight(group)?.generated_at) }}</time>
                    </div>

                    <p class="draft-insight-verdict">{{ insightVerdict(group) }}</p>

                    <div
                      v-if="insightOddsRows(group).length"
                      class="draft-insight-odds"
                    >
                      <p v-for="row in insightOddsRows(group)" :key="row.label">
                        <span>{{ row.label }}</span>
                        <b>{{ row.value }}</b>
                      </p>
                    </div>

                    <div class="draft-insight-metrics">
                      <p><span>FAE概率</span><b>{{ insightMetric(group, 'probability', '--') }}{{ insightMetric(group, 'probability', null) != null ? '%' : '' }}</b></p>
                      <p><span>价值指数</span><b>{{ insightMetric(group, 'value', '--') }}{{ insightMetric(group, 'value', null) != null ? '分' : '' }}</b></p>
                      <p><span>盘口可信</span><b>{{ insightMetric(group, 'confidence', '--') }}{{ insightMetric(group, 'confidence', null) != null ? '分' : '' }}</b></p>
                      <p><span>投注分</span><b>{{ insightMetric(group, 'bet', '--') }}{{ insightMetric(group, 'bet', null) != null ? '分' : '' }}</b></p>
                    </div>

                    <div v-if="insightMarketRows(group).length" class="draft-insight-markets">
                      <p v-for="row in insightMarketRows(group)" :key="row.label">
                        <span>{{ row.label }}</span>
                        <b>{{ row.value }}</b>
                      </p>
                    </div>

                    <div v-if="insightReasons(group).length" class="draft-insight-list reasons">
                      <strong>核心依据</strong>
                      <p v-for="reason in insightReasons(group)" :key="reason">✓ {{ reason }}</p>
                    </div>
                    <div v-if="insightRisks(group).length" class="draft-insight-list risks">
                      <strong>风险</strong>
                      <p v-for="risk in insightRisks(group)" :key="risk">⚠ {{ risk }}</p>
                    </div>

                    <footer class="draft-insight-foot">
                      <span>参考比分</span>
                      <b>{{ insightScores(group) }}</b>
                      <button type="button" @click="openMatchDetail(group)">比赛详情</button>
                    </footer>
                  </template>
                  <div v-else class="draft-insight-state">
                    <span>当前比赛暂无全日研判</span>
                    <button type="button" @click="openMatchDetail(group)">查看比赛详情</button>
                  </div>
                </div>
              </section>
            </div>

            <footer>
              <span>加入时赔率仅作观察记录</span>
              <button type="button" @click="editDraft(draft)">修改</button>
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
const expandedMatches = ref(new Set())
const insightLoading = ref(new Set())
const insights = ref({})
const insightErrors = ref({})
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

const groupDetailId = group => String(
  group?.items?.[0]?.detail_match_id || group?.matchId || ''
)

const matchPanelKey = (draft, group) => `${draft.id}:${group.matchId}`
const isMatchExpanded = (draft, group) => expandedMatches.value.has(matchPanelKey(draft, group))
const matchInsight = group => insights.value[groupDetailId(group)] || null
const isInsightLoading = group => insightLoading.value.has(groupDetailId(group))
const insightError = group => insightErrors.value[groupDetailId(group)] || ''

const setLoadingInsight = (detailId, active) => {
  const next = new Set(insightLoading.value)
  if (active) next.add(detailId)
  else next.delete(detailId)
  insightLoading.value = next
}

const fetchMatchInsight = async group => {
  const detailId = groupDetailId(group)
  if (!detailId || insights.value[detailId] !== undefined || isInsightLoading(group)) return
  setLoadingInsight(detailId, true)
  insightErrors.value = { ...insightErrors.value, [detailId]: '' }
  const date = String(group?.items?.[0]?.date || '').slice(0, 10)
  try {
    const response = await fetch(
      `/api/fae/daily-ai/match/${encodeURIComponent(detailId)}${date ? `?date=${encodeURIComponent(date)}` : ''}`
    )
    const payload = await response.json().catch(() => ({}))
    if (!response.ok || !payload.success) throw new Error(payload.message || '研判加载失败')
    insights.value = { ...insights.value, [detailId]: payload.data || null }
  } catch (error) {
    insightErrors.value = {
      ...insightErrors.value,
      [detailId]: error.message || '研判加载失败'
    }
  } finally {
    setLoadingInsight(detailId, false)
  }
}

const toggleMatch = (draft, group) => {
  const key = matchPanelKey(draft, group)
  const next = new Set(expandedMatches.value)
  if (next.has(key)) next.delete(key)
  else {
    next.add(key)
    fetchMatchInsight(group)
  }
  expandedMatches.value = next
}

const insightAnalysis = group => matchInsight(group)?.analysis || {}
const insightPrimary = group => {
  const analysis = insightAnalysis(group)
  return analysis.single_play || analysis.primary_play || analysis.predicted_result || '方向观察'
}
const insightVerdict = group => {
  const analysis = insightAnalysis(group)
  return analysis.verdict || analysis.summary || '本场已完成研判，详细依据请进入比赛详情查看。'
}
const insightMetric = (group, key, fallback = '--') => {
  const analysis = insightAnalysis(group)
  const values = {
    probability: analysis.prediction_probability,
    value: analysis.value_score,
    confidence: analysis.market_confidence?.score,
    bet: analysis.bet_score
  }
  return values[key] ?? fallback
}
const arrayTriplet = values => Array.isArray(values)
  ? values.map(value => value ?? '--').join(' / ')
  : '--'
const insightOddsRows = group => {
  const snapshot = matchInsight(group)?.input_snapshot || {}
  return [
    { label: '欧赔', value: arrayTriplet(snapshot.euro?.current) },
    { label: '亚盘', value: arrayTriplet(snapshot.asian?.current) },
    { label: `竞彩${snapshot.sporttery_handicap?.value ?? ''}`, value: arrayTriplet(snapshot.sporttery_handicap?.current) },
    { label: '大小球', value: arrayTriplet(snapshot.total?.current) }
  ].filter(row => row.value !== '--')
}
const insightMarketRows = group => {
  const markets = insightAnalysis(group).market_analysis || {}
  const labels = {
    euro: '欧赔方向',
    asian: '亚盘升深',
    sporttery: '竞彩让球',
    total: '大小球',
    consistency: '市场一致性'
  }
  return Object.entries(labels)
    .filter(([key]) => markets[key])
    .map(([key, label]) => ({ label, value: markets[key] }))
}
const insightReasons = group => (insightAnalysis(group).evidence || []).slice(0, 4)
const insightRisks = group => (insightAnalysis(group).risks || []).slice(0, 4)
const insightScores = group => (
  insightAnalysis(group).score_candidates?.join('　') || '暂无'
)
const formatInsightTime = value => value ? `研判于 ${formatTime(value)}` : '研判时间未知'

const openMatchDetail = group => {
  const detailId = groupDetailId(group)
  if (!detailId) return
  router.push({
    name: 'match-detail',
    params: { id: detailId },
    query: { from: 'drafts' }
  })
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

const editDraft = draft => {
  try {
    window.sessionStorage.setItem(CALCULATOR_DRAFT_LOAD_KEY, JSON.stringify({
      draft_id: draft.id,
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
  expandedMatches.value = new Set()
  insights.value = {}
  insightErrors.value = {}
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

.draft-matches > section.expanded {
  background: linear-gradient(180deg, #fffafb, #fff);
}

.draft-match-toggle {
  display: block;
  width: 100%;
  padding: 12px 14px;
  color: inherit;
  text-align: left;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.draft-match-toggle:active {
  background: #fff8f9;
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
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.draft-expand-arrow {
  flex: 0 0 auto;
  color: #aaa;
  font-size: 17px;
  font-style: normal;
  line-height: 1;
  transition: transform .2s ease;
}

.draft-matches > section.expanded .draft-expand-arrow {
  transform: rotate(180deg);
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

.draft-match-analysis {
  padding: 12px 14px 14px;
  background: #fcfcfd;
  border-top: 1px solid #f2e8ea;
}

.draft-insight-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 68px;
  color: #999;
  font-size: 12px;
}

.draft-insight-state.error {
  color: #dc4858;
}

.draft-insight-state button,
.draft-insight-foot button {
  padding: 5px 9px;
  color: #666;
  font-size: 11px;
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 12px;
}

.draft-insight-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.draft-insight-head > div {
  display: flex;
  align-items: center;
  gap: 7px;
}

.draft-insight-head span {
  padding: 3px 6px;
  color: #fff;
  font-size: 9px;
  background: #303446;
  border-radius: 5px;
}

.draft-insight-head strong {
  color: #e53955;
  font-size: 14px;
}

.draft-insight-head time {
  color: #aaa;
  font-size: 9px;
}

.draft-insight-verdict {
  margin: 10px 0;
  color: #555;
  font-size: 12px;
  line-height: 1.65;
}

.draft-insight-odds,
.draft-insight-metrics,
.draft-insight-markets {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}

.draft-insight-odds {
  padding: 8px;
  background: #f5f5f7;
  border-radius: 8px;
}

.draft-insight-odds p,
.draft-insight-metrics p,
.draft-insight-markets p {
  min-width: 0;
  margin: 0;
}

.draft-insight-odds span,
.draft-insight-odds b {
  display: block;
}

.draft-insight-odds span {
  color: #999;
  font-size: 9px;
}

.draft-insight-odds b {
  margin-top: 3px;
  overflow: hidden;
  color: #444;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.draft-insight-metrics {
  margin-top: 8px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.draft-insight-metrics p {
  padding: 7px 3px;
  text-align: center;
  background: #fff;
  border: 1px solid #f1e3e6;
  border-radius: 7px;
}

.draft-insight-metrics span,
.draft-insight-metrics b {
  display: block;
}

.draft-insight-metrics span {
  color: #aaa;
  font-size: 8px;
}

.draft-insight-metrics b {
  margin-top: 3px;
  color: #444;
  font-size: 11px;
}

.draft-insight-markets {
  margin-top: 8px;
}

.draft-insight-markets p {
  padding: 7px;
  background: #fff;
  border: 1px solid #f0e8ea;
  border-radius: 7px;
}

.draft-insight-markets p:last-child:nth-child(odd) {
  grid-column: 1 / -1;
}

.draft-insight-markets span,
.draft-insight-markets b {
  display: block;
}

.draft-insight-markets span {
  color: #e53955;
  font-size: 10px;
  font-weight: 700;
}

.draft-insight-markets b {
  margin-top: 4px;
  color: #666;
  font-size: 10px;
  font-weight: 400;
  line-height: 1.5;
}

.draft-insight-list {
  margin-top: 9px;
}

.draft-insight-list > strong {
  color: #444;
  font-size: 11px;
}

.draft-insight-list p {
  margin: 4px 0 0;
  color: #287b60;
  font-size: 10px;
  line-height: 1.5;
}

.draft-insight-list.risks p {
  color: #a66b2c;
}

.draft-insight-foot {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 10px;
  padding-top: 9px;
  border-top: 1px dashed #e7e7e7;
}

.draft-insight-foot span {
  color: #999;
  font-size: 10px;
}

.draft-insight-foot b {
  flex: 1;
  color: #e53955;
  font-size: 12px;
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

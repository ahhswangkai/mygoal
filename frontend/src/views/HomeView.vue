<template>
  <div class="app-container primary-page home-page">
    <header class="top-header">
      <span class="header-side-spacer" aria-hidden="true"></span>
      <span class="header-title">⚽ 足彩分析</span>
      <AccountButton />
    </header>

    <nav class="date-filter" aria-label="比赛日期筛选">
      <button
        v-for="day in dateOptions"
        :key="day.value"
        type="button"
        class="date-filter-item"
        :class="{ active: selectedDate === day.value }"
        :aria-pressed="selectedDate === day.value"
        @click="selectDate(day.value)"
      >
        <span class="date-weekday">
          {{ day.weekday }}
          <span v-if="day.isToday" class="today-badge">今</span>
        </span>
        <span class="date-label">{{ day.label }}</span>
      </button>
    </nav>

    <nav v-if="!loading && !error && typeOptions.length" class="type-filter" aria-label="比赛类型筛选">
      <button
        v-for="type in typeOptions"
        :key="type.value"
        type="button"
        class="type-filter-chip"
        :class="{ active: selectedType === type.value }"
        :aria-pressed="selectedType === type.value"
        @click="selectType(type.value)"
      >
        {{ type.label }}
      </button>
    </nav>

    <!-- 比赛列表 -->
    <div class="match-list">
      <div v-if="loading" class="loading">加载中...</div>
      <div v-else-if="error" class="empty">
        {{ error }}
        <button type="button" class="page-btn" @click="fetchMatches">重试</button>
      </div>
      <div v-else-if="filteredMatches.length === 0" class="empty">暂无比赛数据</div>
      <div v-else>
        <div
          v-for="m in matches"
          :key="m.match_id"
          class="match-card"
          @click="goToDetail(m.match_id)"
        >
          <div class="match-row match-summary-row">
            <div class="match-league">
              <span class="league-name">{{ m.league }}</span>
              <span class="match-number">{{ m.match_number || '—' }}</span>
              <time class="match-time">
                <span class="match-time-icon" aria-hidden="true">◷</span>
                {{ formatTime(m.match_time) }}
              </time>
            </div>
            <div class="match-status" :class="statusClass(m.status)">
              <span class="match-status-dot" aria-hidden="true"></span>
              {{ statusText(m.status) }}
            </div>
          </div>

          <div class="match-teams-row">
            <div class="team home">
              <span v-if="m.home_rank" class="team-rank">{{ m.home_rank }}</span>
              <span class="team-name">{{ m.home_team }}</span>
            </div>
            <div class="score" :class="{ 'score-live': isLive(m.status), 'score-finished': isFinished(m.status) }">
              <span v-if="isLive(m.status) || m.status === '2' || m.status === 2" class="score-num">
                {{ m.home_score }} - {{ m.away_score }}
              </span>
              <span v-else class="vs-text">VS</span>
              <span v-if="isLive(m.status)" class="live-score-label">实时</span>
            </div>
            <div class="team away">
              <span class="team-name">{{ m.away_team }}</span>
              <span v-if="m.away_rank" class="team-rank">{{ m.away_rank }}</span>
            </div>
          </div>

          <!-- 赔率区域：每个盘口独立成块，即时盘与初盘分两行 -->
          <div class="home-odds-markets" v-if="hasOdds(m)">
            <section class="home-market" v-if="m.euro_current_win">
              <div class="home-market-head">
                <span>竞彩胜平负</span>
                <span class="home-market-hint">即时 / 初盘</span>
              </div>
              <div class="home-market-grid">
                <div class="home-market-item" v-for="item in [
                  { label: '胜', current: m.euro_current_win, initial: m.euro_initial_win },
                  { label: '平', current: m.euro_current_draw, initial: m.euro_initial_draw },
                  { label: '负', current: m.euro_current_lose, initial: m.euro_initial_lose }
                ]" :key="item.label">
                  <span class="home-market-option">{{ item.label }}</span>
                  <span class="home-market-current">
                    <i class="home-market-arrow" :class="arrowClass(item.current, item.initial)">{{ arrow(item.current, item.initial) }}</i>
                    {{ item.current }}
                  </span>
                  <span class="home-market-initial">初 {{ item.initial || '-' }}</span>
                </div>
              </div>
            </section>

            <section class="home-market" v-if="m.hi_current_home_odds || m.hi_initial_home_odds || m.hi_current_draw_odds || m.hi_initial_draw_odds || m.hi_current_away_odds || m.hi_initial_away_odds">
              <div class="home-market-head">
                <span>
                  竞彩让球
                  <b v-if="displayHandicap(m) !== ''" class="home-market-handicap">
                    {{ displayHandicap(m) }}
                  </b>
                </span>
                <span class="home-market-hint">即时 / 初盘</span>
              </div>
              <div class="home-market-grid">
                <div class="home-market-item" v-for="item in [
                  { label: '主胜', current: m.hi_current_home_odds, initial: m.hi_initial_home_odds },
                  { label: '平', current: m.hi_current_draw_odds, initial: m.hi_initial_draw_odds },
                  { label: '客胜', current: m.hi_current_away_odds, initial: m.hi_initial_away_odds }
                ]" :key="item.label">
                  <span class="home-market-option">{{ item.label }}</span>
                  <span class="home-market-current">
                    <i class="home-market-arrow" :class="arrowClass(item.current, item.initial)">{{ arrow(item.current, item.initial) }}</i>
                    {{ item.current || '-' }}
                  </span>
                  <span class="home-market-initial">初 {{ item.initial || '-' }}</span>
                </div>
              </div>
            </section>

            <section class="home-market" v-if="m.asian_current_home_odds">
              <div class="home-market-head">
                <span>亚盘</span>
                <span class="home-market-hint">即时 / 初盘</span>
              </div>
              <div class="home-market-grid">
                <div class="home-market-item home-market-item--line">
                  <span class="home-market-option">盘口</span>
                  <span class="home-market-current">
                    <i
                      v-if="handicapArrow(m.asian_current_handicap, m.asian_initial_handicap)"
                      class="home-market-arrow"
                      :class="handicapArrowClass(m.asian_current_handicap, m.asian_initial_handicap)"
                    >{{ handicapArrow(m.asian_current_handicap, m.asian_initial_handicap) }}</i>
                    {{ cleanHandicap(m.asian_current_handicap) || '-' }}
                  </span>
                  <span class="home-market-initial">初 {{ cleanHandicap(m.asian_initial_handicap) || '-' }}</span>
                </div>
                <div class="home-market-item">
                  <span class="home-market-option">主</span>
                  <span class="home-market-current">
                    <i class="home-market-arrow" :class="arrowClass(m.asian_current_home_odds, m.asian_initial_home_odds)">{{ arrow(m.asian_current_home_odds, m.asian_initial_home_odds) }}</i>
                    {{ m.asian_current_home_odds }}
                  </span>
                  <span class="home-market-initial">初 {{ m.asian_initial_home_odds || '-' }}</span>
                </div>
                <div class="home-market-item">
                  <span class="home-market-option">客</span>
                  <span class="home-market-current">
                    <i class="home-market-arrow" :class="arrowClass(m.asian_current_away_odds, m.asian_initial_away_odds)">{{ arrow(m.asian_current_away_odds, m.asian_initial_away_odds) }}</i>
                    {{ m.asian_current_away_odds }}
                  </span>
                  <span class="home-market-initial">初 {{ m.asian_initial_away_odds || '-' }}</span>
                </div>
              </div>
            </section>

            <section class="home-market" v-if="m.ou_current_over_odds">
              <div class="home-market-head">
                <span>大小球</span>
                <span class="home-market-hint">即时 / 初盘</span>
              </div>
              <div class="home-market-grid">
                <div class="home-market-item">
                  <span class="home-market-option">盘口</span>
                  <span class="home-market-current">{{ m.ou_current_total || '-' }}</span>
                  <span class="home-market-initial">初 {{ m.ou_initial_total || '-' }}</span>
                </div>
                <div class="home-market-item">
                  <span class="home-market-option">大</span>
                  <span class="home-market-current">
                    <i class="home-market-arrow" :class="arrowClass(m.ou_current_over_odds, m.ou_initial_over_odds)">{{ arrow(m.ou_current_over_odds, m.ou_initial_over_odds) }}</i>
                    {{ m.ou_current_over_odds }}
                  </span>
                  <span class="home-market-initial">初 {{ m.ou_initial_over_odds || '-' }}</span>
                </div>
                <div class="home-market-item">
                  <span class="home-market-option">小</span>
                  <span class="home-market-current">
                    <i class="home-market-arrow" :class="arrowClass(m.ou_current_under_odds, m.ou_initial_under_odds)">{{ arrow(m.ou_current_under_odds, m.ou_initial_under_odds) }}</i>
                    {{ m.ou_current_under_odds }}
                  </span>
                  <span class="home-market-initial">初 {{ m.ou_initial_under_odds || '-' }}</span>
                </div>
              </div>
            </section>
          </div>

          <!-- AI 预测 -->
          <div class="ai-prediction" v-if="getPrediction(m.match_id)">
            <span class="ai-tag">AI</span>
            <span class="pred-text">{{ getPrediction(m.match_id).win_prediction }}</span>
            <span class="pred-conf">置信度 {{ getPrediction(m.match_id).win_confidence }}%</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 分页 -->
    <div class="pagination" v-if="totalPages > 1">
      <button class="page-btn" :disabled="filters.page <= 1" @click="changePage(filters.page - 1)">上一页</button>
      <span class="page-info">{{ filters.page }} / {{ totalPages }}</span>
      <button class="page-btn" :disabled="filters.page >= totalPages" @click="changePage(filters.page + 1)">下一页</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, onActivated, onDeactivated } from 'vue'
import { useRouter } from 'vue-router'
import AccountButton from '../components/AccountButton.vue'

const router = useRouter()

const allMatches = ref([])
const predictions = ref([])
const loading = ref(false)
const error = ref('')
const LIVE_SCORE_INTERVAL = 30 * 1000
let liveScoreTimer = null
let liveScoreController = null
let liveScoreRequestPending = false
let matchesController = null
let realtimeActive = false

const filters = ref({
  page: 1,
  page_size: 30
})

const ALL_TYPES = ''
const selectedType = ref(ALL_TYPES)
const typeOptions = computed(() => {
  const counts = new Map()
  allMatches.value.forEach(match => {
    const league = String(match.league || '').trim()
    if (league) counts.set(league, (counts.get(league) || 0) + 1)
  })

  const leagues = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh-CN'))
    .map(([league]) => ({ value: league, label: league }))

  return [{ value: ALL_TYPES, label: '全部' }, ...leagues]
})
const filteredMatches = computed(() => {
  if (selectedType.value === ALL_TYPES) return allMatches.value
  return allMatches.value.filter(match => match.league === selectedType.value)
})
const totalPages = computed(() => Math.max(1, Math.ceil(filteredMatches.value.length / filters.value.page_size)))
const matches = computed(() => {
  const start = (filters.value.page - 1) * filters.value.page_size
  return filteredMatches.value.slice(start, start + filters.value.page_size)
})
const formatDateParam = (date) => {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const today = new Date()
const todayParam = formatDateParam(today)
const selectedDate = ref(todayParam)
const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
const dateOptions = Array.from({ length: 7 }, (_, index) => {
  const date = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  date.setDate(date.getDate() + index - 3)
  return {
    value: formatDateParam(date),
    weekday: weekdays[date.getDay()],
    label: `${String(date.getMonth() + 1).padStart(2, '0')}/${String(date.getDate()).padStart(2, '0')}`,
    isToday: index === 3
  }
})

const matchTimeTimestamp = (value) => {
  if (!value) return Number.POSITIVE_INFINITY
  const timestamp = new Date(String(value).trim().replace(' ', 'T')).getTime()
  return Number.isNaN(timestamp) ? Number.POSITIVE_INFINITY : timestamp
}

const fetchMatches = async () => {
  matchesController?.abort()
  const controller = new AbortController()
  matchesController = controller
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({
      date: selectedDate.value,
      page: '1',
      page_size: '200'
    })
    const response = await fetch(`/api/matches?${params}`, { signal: controller.signal })
    if (!response.ok) throw new Error('比赛接口请求失败')

    const payload = await response.json()
    if (!payload.success) throw new Error('比赛接口返回失败')

    allMatches.value = (payload.data || []).sort((a, b) => (
      matchTimeTimestamp(a.match_time) - matchTimeTimestamp(b.match_time)
      || String(a.match_number || '').localeCompare(String(b.match_number || ''), 'zh-CN')
    ))
    filters.value.page = 1
  } catch (e) {
    if (e.name === 'AbortError') return
    console.error(e)
    allMatches.value = []
    filters.value.page = 1
    error.value = '比赛加载失败，请稍后重试'
  } finally {
    if (matchesController === controller) {
      loading.value = false
      matchesController = null
    }
  }
}

const selectDate = (date) => {
  if (selectedDate.value === date) return
  selectedDate.value = date
  selectedType.value = ALL_TYPES
  allMatches.value = []
  filters.value.page = 1
  fetchMatches()
}

const selectType = (type) => {
  selectedType.value = type
  filters.value.page = 1
}

const fetchPredictions = async () => {
  try {
    const resp = await fetch('/api/predictions?limit=200')
    const data = await resp.json()
    if (data.success) {
      predictions.value = data.data || []
    }
  } catch (e) {
    console.error(e)
  }
}

const fetchLiveScores = async () => {
  if (document.visibilityState === 'hidden' || liveScoreRequestPending) return

  liveScoreRequestPending = true
  liveScoreController = new AbortController()
  try {
    const params = new URLSearchParams({
      status: '1',
      page: '1',
      page_size: '200'
    })
    const response = await fetch(`/api/matches?${params}`, {
      signal: liveScoreController.signal
    })
    if (!response.ok) throw new Error('实时比分接口请求失败')

    const payload = await response.json()
    if (!payload.success) throw new Error('实时比分接口返回失败')

    const liveMatches = new Map(
      (payload.data || []).map(match => [String(match.match_id), match])
    )
    allMatches.value = allMatches.value.map(match => {
      const liveMatch = liveMatches.get(String(match.match_id))
      if (!liveMatch) return match
      return {
        ...match,
        home_score: liveMatch.home_score,
        away_score: liveMatch.away_score
      }
    })
  } catch (e) {
    if (e.name !== 'AbortError') console.error(e)
  } finally {
    liveScoreRequestPending = false
    liveScoreController = null
  }
}

const startLiveScorePolling = () => {
  if (liveScoreTimer || document.visibilityState === 'hidden') return
  fetchLiveScores()
  liveScoreTimer = window.setInterval(fetchLiveScores, LIVE_SCORE_INTERVAL)
}

const stopLiveScorePolling = () => {
  if (liveScoreTimer) {
    window.clearInterval(liveScoreTimer)
    liveScoreTimer = null
  }
  liveScoreController?.abort()
}

const handleVisibilityChange = () => {
  if (document.visibilityState === 'hidden') {
    stopLiveScorePolling()
  } else {
    startLiveScorePolling()
  }
}

const activateRealtime = () => {
  if (realtimeActive) return
  realtimeActive = true
  document.addEventListener('visibilitychange', handleVisibilityChange)
  startLiveScorePolling()
}

const deactivateRealtime = () => {
  if (!realtimeActive) return
  realtimeActive = false
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  stopLiveScorePolling()
}

const getPrediction = (matchId) => {
  return predictions.value.find(p => String(p.match_id) === String(matchId))
}

const hasOdds = (m) => {
  const hasHandicapOdds = m.hi_current_home_odds || m.hi_initial_home_odds ||
    m.hi_current_draw_odds || m.hi_initial_draw_odds ||
    m.hi_current_away_odds || m.hi_initial_away_odds
  return m.euro_current_win || m.asian_current_home_odds || m.ou_current_over_odds || hasHandicapOdds
}

const displayHandicap = (match) => {
  const raw = match.hi_handicap_value ?? match.handicap
  if (raw === null || raw === undefined || raw === '') return ''
  const value = Number(raw)
  if (Number.isNaN(value)) return String(raw)
  return value > 0 ? `+${value}` : String(value)
}

const arrow = (curr, init) => {
  const c = parseFloat(curr)
  const i = parseFloat(init)
  if (isNaN(c) || isNaN(i)) return ''
  if (c > i) return '↑'
  if (c < i) return '↓'
  return ''
}

const arrowClass = (curr, init) => {
  const c = parseFloat(curr)
  const i = parseFloat(init)
  if (isNaN(c) || isNaN(i)) return ''
  if (c > i) return 'up'
  if (c < i) return 'down'
  return ''
}

const cleanHandicap = (value) => String(value || '')
  .replace(/\s+/g, '')
  .replace(/(?:[↑↓]|升|降)+$/g, '')

const handicapValue = (value) => {
  const text = cleanHandicap(value)
  const receiving = text.startsWith('受')
  const key = text.replace(/^受/, '')
  const values = {
    '平手': 0,
    '平/半': 0.25,
    '平手/半球': 0.25,
    '半球': 0.5,
    '半/一': 0.75,
    '半球/一球': 0.75,
    '一球': 1,
    '一/球半': 1.25,
    '一球/球半': 1.25,
    '球半': 1.5,
    '球半/两球': 1.75,
    '两球': 2,
    '两球/两球半': 2.25,
    '两球半': 2.5
  }
  if (values[key] === undefined) return null
  return receiving ? -values[key] : values[key]
}

const handicapDirection = (current, initial) => {
  const raw = String(current || '').trim()
  if (/(?:↓|降)$/.test(raw)) return 'down'
  if (/(?:↑|升)$/.test(raw)) return 'up'
  const currentValue = handicapValue(current)
  const initialValue = handicapValue(initial)
  if (currentValue === null || initialValue === null || currentValue === initialValue) return ''
  return currentValue > initialValue ? 'up' : 'down'
}

const handicapArrow = (current, initial) => {
  const direction = handicapDirection(current, initial)
  return direction === 'up' ? '↑' : direction === 'down' ? '↓' : ''
}

const handicapArrowClass = (current, initial) => handicapDirection(current, initial)

const statusText = (s) => {
  if (s === '0' || s === 0) return '未开始'
  if (s === '1' || s === 1) return '进行中'
  if (s === '2' || s === 2) return '已完场'
  return '未知'
}

const isLive = (status) => status === '1' || status === 1
const isFinished = (status) => status === '2' || status === 2

const statusClass = (s) => {
  if (s === '0' || s === 0) return 'status-pending'
  if (s === '1' || s === 1) return 'status-live'
  if (s === '2' || s === 2) return 'status-finished'
  return ''
}

const formatTime = (t) => {
  if (!t) return ''
  return t.slice(5) // MM-DD HH:MM
}

const changePage = (p) => {
  filters.value.page = p
}

const goToDetail = (id) => {
  router.push(`/match/${id}`)
}

onMounted(() => {
  fetchMatches()
  fetchPredictions()
})

onActivated(activateRealtime)
onDeactivated(deactivateRealtime)

onUnmounted(() => {
  deactivateRealtime()
  matchesController?.abort()
})
</script>

<style scoped>
.fae-parlay-board {
  margin: 0 12px 12px;
  overflow: hidden;
  background: #fff;
  border: 1px solid #eadde0;
  border-radius: 14px;
  box-shadow: 0 5px 18px rgb(57 31 37 / 7%);
}

.fae-parlay-board > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 48px;
  padding: 0 13px;
  background: linear-gradient(110deg, #fff7f9, #fff);
  border-bottom: 1px solid #f4e6e9;
}

.fae-parlay-board > header > div {
  display: flex;
  align-items: center;
  gap: 7px;
}

.fae-parlay-board > header strong {
  color: #333;
  font-size: 15px;
}

.fae-parlay-board > header small {
  color: #e53955;
  font-size: 11px;
  font-weight: 700;
}

.fae-parlay-intro {
  margin: 0;
  padding: 10px 13px;
  color: #777;
  font-size: 11px;
  line-height: 1.55;
}

.fae-all-picks {
  margin: 0 11px 11px;
  border: 1px solid #f0e5e7;
  border-radius: 10px;
}

.fae-all-picks summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px;
  color: #444;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  list-style: none;
}

.fae-all-picks summary::-webkit-details-marker {
  display: none;
}

.fae-all-picks summary b {
  color: #e53955;
  font-size: 11px;
}

.fae-all-picks-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
  padding: 0 8px 8px;
}

.fae-all-picks-grid button {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 6px;
  padding: 8px;
  text-align: left;
  background: #fcfafb;
  border: 1px solid #f2e8ea;
  border-radius: 8px;
}

.fae-all-picks-grid button > span {
  grid-column: 1 / 3;
  overflow: hidden;
  color: #777;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fae-all-picks-grid button > span b {
  margin-right: 4px;
  color: #444;
}

.fae-all-picks-grid button > strong {
  color: #e53955;
  font-size: 12px;
}

.fae-all-picks-grid button > em {
  justify-self: end;
  color: #555;
  font-size: 10px;
  font-style: normal;
}

.fae-all-picks-grid button > small {
  grid-column: 1 / 3;
  color: #aaa;
  font-size: 9px;
}

.fae-combo-groups {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 9px;
  padding: 0 11px 11px;
}

.fae-combo-group {
  min-width: 0;
}

.fae-combo-group > h3 {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 0 0 7px;
  color: #333;
  font-size: 13px;
}

.fae-combo-group > h3 small {
  color: #aaa;
  font-size: 9px;
  font-weight: 400;
}

.fae-combo-group article {
  margin-bottom: 8px;
  overflow: hidden;
  background: linear-gradient(145deg, #fff, #fff9fa);
  border: 1px solid #f0dfe2;
  border-radius: 9px;
}

.fae-combo-group article > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 8px;
  border-bottom: 1px solid #f4e9eb;
}

.fae-combo-group article > header span {
  display: flex;
  align-items: center;
  color: #555;
  font-size: 11px;
  font-weight: 600;
}

.fae-combo-group article > header i {
  width: 17px;
  height: 17px;
  margin-right: 5px;
  color: #fff;
  font-size: 9px;
  font-style: normal;
  line-height: 17px;
  text-align: center;
  background: #e53955;
  border-radius: 50%;
}

.fae-combo-group article > header b {
  color: #e53955;
  font-size: 11px;
}

.fae-combo-group article > button {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  text-align: left;
  background: none;
  border: 0;
  border-bottom: 1px dashed #f0e5e7;
}

.fae-combo-group article > button span {
  overflow: hidden;
  color: #777;
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fae-combo-group article > button strong {
  color: #e53955;
  font-size: 10px;
  white-space: nowrap;
}

.fae-combo-group article > button em {
  color: #555;
  font-size: 9px;
  font-style: normal;
  white-space: nowrap;
}

.fae-combo-group article > footer {
  display: flex;
  justify-content: space-between;
  padding: 7px 8px;
  color: #888;
  font-size: 9px;
}

.fae-combo-group article > footer b {
  color: #333;
}

.fae-parlay-disclaimer {
  margin: 0;
  padding: 0 12px 11px;
  color: #aaa;
  font-size: 9px;
  line-height: 1.55;
}

@media (max-width: 390px) {
  .fae-all-picks-grid,
  .fae-combo-groups {
    grid-template-columns: 1fr;
  }
}

.fae-daily-board {
  margin: 0 12px 12px;
  overflow: hidden;
  background: linear-gradient(145deg, #fff 0%, #fff8f9 100%);
  border: 1px solid #f1d9dd;
  border-radius: 14px;
  box-shadow: 0 5px 18px rgb(57 31 37 / 7%);
}

.fae-daily-board > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 46px;
  padding: 0 13px;
  border-bottom: 1px solid #f5e8ea;
}

.fae-daily-board > header > div {
  display: flex;
  align-items: center;
  gap: 7px;
}

.fae-daily-board > header strong {
  color: #333;
  font-size: 15px;
}

.fae-daily-board > header small {
  color: #aaa;
  font-size: 10px;
}

.fae-board-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 22px;
  padding: 0 7px;
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .4px;
  background: #2d3142;
  border-radius: 5px;
}

.fae-board-groups {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  padding: 11px;
}

.fae-board-group {
  min-width: 0;
  padding: 9px;
  background: #fff;
  border: 1px solid #f2e6e8;
  border-radius: 10px;
}

.fae-board-group h3 {
  margin: 0 0 7px;
  color: #e53955;
  font-size: 13px;
}

.fae-board-group button {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto;
  gap: 0 6px;
  width: 100%;
  padding: 7px 0;
  text-align: left;
  background: none;
  border: 0;
  border-top: 1px solid #f5f0f1;
}

.fae-board-group button > i {
  grid-row: 1 / 3;
  align-self: center;
  width: 18px;
  height: 18px;
  color: #fff;
  font-size: 10px;
  font-style: normal;
  line-height: 18px;
  text-align: center;
  background: #e53955;
  border-radius: 50%;
}

.fae-board-group button > span {
  grid-column: 2;
  min-width: 0;
  overflow: hidden;
  color: #777;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fae-board-group button > span b {
  margin-right: 4px;
  color: #444;
}

.fae-board-group button > .fae-board-stars {
  grid-column: 2;
  color: #ff9c25;
  font-size: 9px;
  font-style: normal;
  letter-spacing: -1px;
}

.fae-board-group button > .fae-board-odds {
  grid-column: 3;
  grid-row: 1;
  color: #777;
  font-size: 9px;
  font-style: normal;
  letter-spacing: 0;
  white-space: nowrap;
}

.fae-board-group button > strong {
  grid-column: 3;
  grid-row: 2;
  justify-self: end;
  color: #e53955;
  font-size: 10px;
}

.fae-danger-board {
  margin: 0 11px 11px;
  padding: 9px 10px;
  background: #fff7e8;
  border-radius: 9px;
}

.fae-danger-board h3 {
  margin: 0 0 5px;
  color: #9a6b13;
  font-size: 12px;
}

.fae-danger-board button {
  display: flex;
  justify-content: space-between;
  width: 100%;
  padding: 5px 0;
  color: #805e21;
  font-size: 10px;
  text-align: left;
  background: none;
  border: 0;
}

.fae-danger-board button strong {
  flex: 0 0 auto;
  margin-left: 8px;
  color: #d47b22;
}

@media (max-width: 390px) {
  .fae-board-groups {
    grid-template-columns: 1fr;
  }
}

.date-filter {
  display: flex;
  gap: 8px;
  margin: 10px 0;
  padding: 0 12px 4px;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scroll-snap-type: x proximity;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}

.date-filter::-webkit-scrollbar {
  display: none;
}

.date-filter-item {
  flex: 0 0 68px;
  min-height: 52px;
  padding: 6px 5px;
  border: 1px solid #f1d6da;
  border-radius: 10px;
  background: #fff;
  color: #666;
  font: inherit;
  line-height: 1.25;
  scroll-snap-align: center;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.date-filter-item.active {
  border-color: #e53955;
  background: #e53955;
  color: #fff;
  box-shadow: 0 3px 8px rgb(229 57 85 / 20%);
}

.date-weekday,
.date-label {
  display: block;
}

.date-weekday {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}

.date-label {
  margin-top: 3px;
  font-size: 12px;
}

.today-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  margin-left: 2px;
  border-radius: 50%;
  background: #e53955;
  color: #fff;
  font-size: 9px;
  vertical-align: 1px;
}

.date-filter-item.active .today-badge {
  background: #fff;
  color: #e53955;
}

.type-filter {
  display: flex;
  gap: 8px;
  margin: -2px 0 10px;
  padding: 0 12px 2px;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}

.type-filter::-webkit-scrollbar {
  display: none;
}

.type-filter-chip {
  flex: 0 0 auto;
  min-height: 30px;
  padding: 5px 13px;
  border: 1px solid #f1d6da;
  border-radius: 16px;
  background: #fff;
  color: #666;
  font: inherit;
  font-size: 13px;
  line-height: 1.2;
  white-space: nowrap;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.type-filter-chip.active {
  border-color: #e53955;
  background: #e53955;
  color: #fff;
  box-shadow: 0 2px 6px rgb(229 57 85 / 18%);
}

.match-summary-row {
  position: relative;
  min-height: 46px;
  margin-bottom: 2px;
  padding: 8px 10px 8px 14px;
  overflow: hidden;
  background:
    radial-gradient(circle at 92% -25%, rgb(229 57 85 / 10%), transparent 45%),
    linear-gradient(110deg, #fff9fa 0%, #fbfcff 58%, #f6f8fc 100%);
  border: 1px solid #edf0f4;
  border-radius: 10px;
}

.match-summary-row::before {
  position: absolute;
  top: 10px;
  bottom: 10px;
  left: 0;
  width: 3px;
  content: '';
  background: linear-gradient(#ff5a68, #e53955);
  border-radius: 0 3px 3px 0;
}

.match-summary-row .match-league {
  display: flex;
  flex: 1;
  flex-direction: row;
  align-items: center;
  min-width: 0;
  gap: 7px;
}

.match-summary-row .league-name {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  min-height: 26px;
  padding: 4px 8px;
  color: #168fd5;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  background: #eaf7ff;
  border: 1px solid #d9effd;
  border-radius: 7px;
}

.match-number {
  flex: 0 0 auto;
  margin-left: 0;
  color: #666;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

.match-summary-row .match-time {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  width: auto;
  margin: 0;
  padding: 4px 7px;
  color: #555;
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
  background: rgb(255 255 255 / 78%);
  border: 1px solid #e9ebef;
  border-radius: 7px;
  box-shadow: 0 1px 2px rgb(27 39 51 / 4%);
}

.match-time-icon {
  margin-right: 3px;
  color: #e53955;
  font-size: 13px;
}

.match-summary-row .match-status {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  min-width: 60px;
  margin-left: 8px;
  padding: 6px 9px;
  color: #777;
  font-size: 11px;
  line-height: 1;
  text-align: center;
  font-weight: 600;
  background: #fff;
  border: 1px solid #e4e6ea;
  border-radius: 14px;
  box-shadow: 0 2px 6px rgb(31 42 55 / 6%);
}

.match-status-dot {
  width: 6px;
  height: 6px;
  margin-right: 5px;
  background: currentColor;
  border-radius: 50%;
}

.match-summary-row .match-status.status-pending {
  color: #85888d;
  background: #fff;
}

.match-summary-row .match-status.status-live {
  color: #e53955;
  background: #fff5f6;
  border-color: #ffd9de;
}

.match-summary-row .match-status.status-finished {
  color: #18a161;
  background: #f1fbf6;
  border-color: #d8f2e4;
}

.match-teams-row {
  padding: 13px 0 14px;
}

.team-name {
  font-size: 17px;
  font-weight: 600;
  line-height: 1.35;
}

.team-rank {
  display: inline-flex;
  flex: 0 0 20px;
  align-items: center;
  justify-content: center;
  height: 20px;
  border-radius: 50%;
  background: #f0f1f3;
  color: #777;
  font-size: 10px;
  font-weight: 600;
}

.team-handicap {
  padding: 2px 5px;
  border-radius: 5px;
  background: #fff0f2;
  color: #e53955;
  font-weight: 600;
}

.score {
  min-width: 64px;
  padding: 0 10px;
  text-align: center;
}

.score-num {
  font-size: 23px;
  line-height: 1;
  color: #333;
}

.score-finished .score-num {
  color: #333;
}

.vs-text {
  color: #a4a8ad;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 1px;
}

.score-live .score-num {
  color: #e53935;
  font-size: 24px;
  font-weight: 700;
}

.live-score-label {
  display: block;
  margin-top: 2px;
  color: #e53935;
  font-size: 11px;
  font-weight: 600;
}

@media (max-width: 360px) {
  .match-summary-row {
    padding-right: 8px;
    padding-left: 12px;
  }

  .match-summary-row .match-league {
    gap: 5px;
  }

  .match-summary-row .league-name {
    padding-right: 6px;
    padding-left: 6px;
  }

  .match-summary-row .match-time {
    padding-right: 5px;
    padding-left: 5px;
  }

  .match-summary-row .match-status {
    min-width: 54px;
    margin-left: 5px;
    padding-right: 7px;
    padding-left: 7px;
  }
}
</style>

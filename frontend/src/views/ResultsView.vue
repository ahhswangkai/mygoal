<template>
  <div class="app-container primary-page results-page">
    <header class="top-header">
      <span class="header-side-spacer"></span>
      <span class="header-title">赛果</span>
      <AccountButton />
    </header>

    <main class="results-content">
      <nav class="results-view-tabs" aria-label="赛果内容">
        <button
          type="button"
          :class="{ active: activeTab === 'results' }"
          :aria-current="activeTab === 'results' ? 'page' : undefined"
          @click="activeTab = 'results'"
        >赛果</button>
        <button
          type="button"
          :class="{ active: activeTab === 'profiles' }"
          :aria-current="activeTab === 'profiles' ? 'page' : undefined"
          @click="activeTab = 'profiles'"
        >联赛画像</button>
      </nav>

      <template v-if="activeTab === 'results'">
        <section class="results-filters" aria-label="赛果筛选">
          <div class="filter-field league-filter">
            <span class="filter-label">联赛</span>
            <button type="button" class="league-filter-trigger" @click="openLeagueFilter">
              <span>{{ leagueFilterLabel }}</span>
              <i></i>
            </button>
          </div>
          <div class="filter-field time-filter">
            <span class="filter-label">时间范围</span>
            <div class="time-segments" role="group" aria-label="时间范围">
              <button
                v-for="range in [{ value: 'all', label: '全部' }, { value: '7', label: '近 7 天' }, { value: '30', label: '近 30 天' }]"
                :key="range.value"
                type="button"
                :class="{ active: timeRange === range.value }"
                :aria-pressed="timeRange === range.value"
                @click="timeRange = range.value"
              >
                {{ range.label }}
              </button>
            </div>
          </div>
        </section>

        <div v-if="loading" class="results-state">正在加载赛果…</div>
        <div v-else-if="error" class="results-state results-error">
          <span>{{ error }}</span>
          <button type="button" @click="fetchMatches">重试</button>
        </div>
        <div v-else-if="filteredMatches.length === 0" class="results-state">暂无完赛数据</div>

        <section v-else class="results-list">
          <article
            v-for="match in filteredMatches"
            :key="match.match_id"
            class="result-card"
            role="link"
            tabindex="0"
            :aria-label="`查看${match.home_team}对${match.away_team}比赛详情`"
            @click="goToDetail(match.match_id)"
            @keydown.enter="goToDetail(match.match_id)"
            @keydown.space.prevent="goToDetail(match.match_id)"
          >
            <header class="result-meta">
              <strong>{{ match.league || '未知联赛' }}</strong>
              <span class="match-number">{{ match.match_number || '—' }}</span>
              <time>{{ formatTime(match.match_time) }}</time>
            </header>
            <div class="result-score">
              <span>{{ match.home_team }}</span>
              <strong>{{ match.home_score }}<i>:</i>{{ match.away_score }}</strong>
              <span>{{ match.away_team }}</span>
            </div>
            <div class="result-markets">
              <div>
                <span>让球结果</span>
                <strong :class="{ hit: hiResult(match) }">{{ hiResultLabel(match) }}</strong>
                <small v-if="hiHandicapValue(match) !== null">盘口 {{ signed(hiHandicapValue(match)) }}</small>
              </div>
              <div>
                <span>欧赔结果</span>
                <strong :class="{ hit: euroResult(match) }">{{ label(euroResult(match), { win: '主胜', draw: '平', lose: '客胜' }) }}</strong>
                <small>{{ hitOdds(match, 'euro') }}</small>
              </div>
              <div>
                <span>亚盘结果</span>
                <strong :class="{ hit: asianResult(match) }">{{ asianResultLabel(match) }}</strong>
                <small>{{ asianHandicapLabel(match) }}</small>
              </div>
              <div>
                <span>大小球结果</span>
                <strong :class="{ hit: ouResult(match) }">{{ ouResultLabel(match) }}</strong>
                <small>{{ ouTotalLabel(match) }}</small>
              </div>
            </div>
          </article>

          <div class="results-load-more">
            <button v-if="loadMoreError" type="button" @click="loadMore">加载失败，点击重试</button>
            <small v-else-if="loadingMore">加载中...</small>
            <small v-else-if="!hasMore">已加载全部</small>
          </div>
          <div ref="loadMoreSentinel" class="load-more-sentinel" aria-hidden="true"></div>
        </section>
      </template>

      <template v-else>
        <section class="profile-toolbar">
          <div>
            <strong>联赛历史画像</strong>
            <span>时间衰减统计 · 仅使用今天以前的完赛数据</span>
          </div>
          <button type="button" class="profile-filter-button" @click="openLeagueFilter">
            {{ leagueFilterLabel }}
          </button>
        </section>

        <div v-if="profilesLoading" class="results-state">正在计算联赛画像…</div>
        <div v-else-if="profilesError" class="results-state results-error">
          <span>{{ profilesError }}</span>
          <button type="button" @click="fetchLeagueProfiles">重试</button>
        </div>
        <div v-else-if="leagueProfileItems.length === 0" class="results-state">
          暂无联赛历史画像
        </div>
        <section v-else class="league-profiles-list">
          <article
            v-for="item in leagueProfileItems"
            :key="item.league"
            class="league-profile-card"
          >
            <header>
              <div>
                <strong>{{ item.league }}</strong>
                <span>统计截至 {{ item.profile.before_date }} 之前</span>
              </div>
              <em :class="{ eligible: item.profile.eligible_for_adjustment }">
                {{ item.profile.eligible_for_adjustment ? `可信度 ${item.profile.confidence}` : '样本不足' }}
              </em>
            </header>
            <div class="league-profile-grid">
              <div>
                <span>历史样本</span>
                <strong>{{ item.profile.sample_size }}</strong>
                <small>有效 {{ item.profile.effective_sample_size }}</small>
              </div>
              <div>
                <span>主胜 / 平 / 客胜</span>
                <strong>
                  {{ percent(item.profile.baseline?.home_win_rate) }} /
                  {{ percent(item.profile.baseline?.draw_rate) }} /
                  {{ percent(item.profile.baseline?.away_win_rate) }}
                </strong>
              </div>
              <div>
                <span>场均进球</span>
                <strong>{{ decimal(item.profile.baseline?.avg_total_goals) }}</strong>
                <small>双方进球 {{ percent(item.profile.baseline?.both_teams_score_rate) }}</small>
              </div>
              <div>
                <span>一球分差</span>
                <strong>{{ percent(item.profile.baseline?.one_goal_margin_rate) }}</strong>
              </div>
              <div>
                <span>竞彩让平</span>
                <strong>{{ percent(item.profile.sporttery_handicap?.let_draw_rate) }}</strong>
                <small>{{ item.profile.sporttery_handicap?.sample || 0 }}场有让球盘</small>
              </div>
              <div>
                <span>大球 / 小球</span>
                <strong>
                  {{ percent(item.profile.total_market?.over_rate) }} /
                  {{ percent(item.profile.total_market?.under_rate) }}
                </strong>
              </div>
            </div>
            <ul class="league-profile-signals">
              <li
                v-for="signal in item.profile.hidden_signals || []"
                :key="signal"
              >{{ signal }}</li>
            </ul>
            <p>历史条件频率仅作辅助，不代表单场真实概率。</p>
          </article>
        </section>
      </template>
    </main>

    <div v-if="showLeagueFilter" class="league-filter-overlay" @click.self="cancelLeagueFilter">
      <section class="league-filter-modal" role="dialog" aria-modal="true" aria-label="联赛筛选">
        <header class="league-filter-modal-head">
          <div>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3 5h18l-7 8v5.2l-4 2V13L3 5z" />
            </svg>
            <strong>竞彩</strong>
          </div>
          <span>共{{ totalAvailableMatches }}场比赛，已选{{ draftLeagues.length }}个联赛</span>
        </header>

        <div v-if="leagues.length" class="league-option-grid">
          <button
            v-for="league in leagues"
            :key="league"
            type="button"
            :class="{ selected: draftLeagues.includes(league) }"
            @click="toggleDraftLeague(league)"
          >{{ league }}</button>
        </div>
        <div v-else class="league-options-empty">当前范围暂无可筛选联赛</div>

        <div class="league-quick-actions">
          <button type="button" :disabled="!leagues.length" @click="selectAllLeagues">全选</button>
          <button type="button" :disabled="!leagues.length" @click="invertLeagues">反选</button>
          <button type="button" :disabled="!majorLeagues.length" @click="selectMajorLeagues">五大联赛</button>
        </div>

        <footer>
          <button type="button" @click="cancelLeagueFilter">取消</button>
          <button type="button" @click="applyLeagueFilter">筛好了</button>
        </footer>
      </section>
    </div>
  </div>
</template>

<script setup>
import axios from 'axios'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AccountButton from '../components/AccountButton.vue'

const router = useRouter()
const activeTab = ref('results')
const matches = ref([])
const loading = ref(false)
const loadingMore = ref(false)
const loadMoreError = ref(false)
const error = ref('')
const page = ref(1)
const hasMore = ref(true)
const selectedLeagues = ref([])
const draftLeagues = ref([])
const showLeagueFilter = ref(false)
const timeRange = ref('all')
const pageSize = 20
const availableLeagues = ref([])
const totalAvailableMatches = ref(0)
const loadMoreSentinel = ref(null)
const leagueProfileItems = ref([])
const profilesLoading = ref(false)
const profilesError = ref('')
const loadedProfileFilter = ref(null)
let loadMoreObserver = null

const leagues = computed(() => availableLeagues.value)
const filteredMatches = computed(() => matches.value)
const majorLeagues = computed(() => {
  const names = new Set(['英超', '西甲', '德甲', '意甲', '法甲'])
  return leagues.value.filter(league => names.has(league))
})
const leagueFilterLabel = computed(() => {
  if (!selectedLeagues.value.length) return '全部联赛'
  if (selectedLeagues.value.length === 1) return selectedLeagues.value[0]
  return `已选${selectedLeagues.value.length}个联赛`
})

const goToDetail = matchId => {
  if (!matchId) return
  router.push(`/match/${matchId}`)
}

const fetchLeagueProfiles = async () => {
  const filterKey = selectedLeagues.value.slice().sort().join('|') || '*'
  profilesLoading.value = true
  profilesError.value = ''
  try {
    const response = await axios.get('/api/fae/league-profiles', {
      params: {
        before_date: formatDateParam(new Date()),
        leagues: selectedLeagues.value.length
          ? selectedLeagues.value.join(',')
          : undefined
      }
    })
    leagueProfileItems.value = response.data?.data?.items || []
    loadedProfileFilter.value = filterKey
  } catch (profileError) {
    leagueProfileItems.value = []
    profilesError.value = profileError.response?.data?.message || '联赛画像加载失败'
  } finally {
    profilesLoading.value = false
  }
}

const percent = value => {
  const number = Number(value)
  return Number.isFinite(number) ? `${number.toFixed(1)}%` : '-'
}
const decimal = value => {
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(2) : '-'
}

const openLeagueFilter = () => {
  draftLeagues.value = [...selectedLeagues.value]
  showLeagueFilter.value = true
}
const cancelLeagueFilter = () => {
  draftLeagues.value = []
  showLeagueFilter.value = false
}
const toggleDraftLeague = league => {
  draftLeagues.value = draftLeagues.value.includes(league)
    ? draftLeagues.value.filter(item => item !== league)
    : [...draftLeagues.value, league]
}
const selectAllLeagues = () => {
  draftLeagues.value = [...leagues.value]
}
const invertLeagues = () => {
  const selected = new Set(draftLeagues.value)
  draftLeagues.value = leagues.value.filter(league => !selected.has(league))
}
const selectMajorLeagues = () => {
  draftLeagues.value = [...majorLeagues.value]
}
const applyLeagueFilter = () => {
  selectedLeagues.value = [...draftLeagues.value]
  showLeagueFilter.value = false
}

const formatDateParam = date => {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}
const resultDateRange = () => {
  const end = new Date()
  const start = new Date(end)
  if (timeRange.value === 'all') {
    start.setFullYear(1970, 0, 1)
  } else {
    start.setDate(start.getDate() - Number(timeRange.value))
  }
  return { start_date: formatDateParam(start), end_date: formatDateParam(end) }
}

const score = match => ({ home: Number(match.home_score), away: Number(match.away_score) })
const hasScore = match => match.home_score !== null && match.home_score !== undefined && match.home_score !== '' &&
  match.away_score !== null && match.away_score !== undefined && match.away_score !== '' &&
  Number.isFinite(score(match).home) && Number.isFinite(score(match).away)
const euroResult = match => {
  if (!hasScore(match)) return null
  const { home, away } = score(match)
  return home > away ? 'win' : home < away ? 'lose' : 'draw'
}

const handicapMap = {
  '平手': 0, '平/半': 0.25, '平手/半球': 0.25, '半球': 0.5,
  '半/一': 0.75, '半球/一球': 0.75, '一球': 1, '一/球半': 1.25,
  '一球/球半': 1.25, '球半': 1.5, '球半/两': 1.75,
  '球半/两球': 1.75, '两球': 2, '两/两球半': 2.25, '两球半': 2.5
}
const parseHandicap = value => {
  if (value === null || value === undefined || value === '') return null
  const text = String(value).trim().replace(/(?:[↑↓]|升|降)+$/, '')
  const clean = text.replace('受', '')
  let parsed = handicapMap[clean]
  if (parsed === undefined) {
    const found = clean.match(/-?\d+(?:\.\d+)?/)
    parsed = found ? Number(found[0]) : null
  }
  if (parsed === null) return null
  // 中文亚盘默认表示主队让球；“受”表示主队受让。
  if (handicapMap[clean] !== undefined) {
    return text.includes('受') ? Math.abs(parsed) : -Math.abs(parsed)
  }
  return text.includes('受') ? Math.abs(parsed) : parsed
}
const asianResult = match => {
  if (!hasScore(match)) return null
  const handicap = parseHandicap(match.asian_current_handicap || match.asian_initial_handicap)
  if (handicap === null) return null
  const adjusted = score(match).home + handicap - score(match).away
  return adjusted > 0 ? 'home' : adjusted < 0 ? 'away' : 'push'
}
const asianResultLabel = match => {
  if (!hasScore(match)) return '比分未录入'
  if (parseHandicap(match.asian_current_handicap || match.asian_initial_handicap) === null) return '盘口未录入'
  return { home: '主队赢盘', away: '客队赢盘', push: '走盘' }[asianResult(match)]
}
const asianHandicapLabel = match => {
  const value = match.asian_current_handicap || match.asian_initial_handicap
  return value ? `盘口 ${String(value).replace(/(?:[↑↓]|升|降)+$/, '')}` : '盘口未录入'
}
const parseHiHandicap = value => {
  if (value === null || value === undefined || value === '') return null
  if (typeof value === 'number') return Number.isFinite(value) ? value : null

  const text = String(value).trim().replace(/[−－]/g, '-')
  if (!text) return null

  // Mongo stores the crawler's source text (normally "-1", "0" or "+1").
  // Also tolerate split handicaps such as "+0/0.5" by using their midpoint.
  const isReceiving = text.includes('受')
  const numericText = text.replace('受', '')
  const parts = numericText.split('/').map(part => part.trim()).filter(Boolean)
  if (parts.length > 2) return null

  const first = Number(parts[0])
  if (!Number.isFinite(first)) return null
  if (parts.length === 1) return isReceiving ? Math.abs(first) : first

  let second = Number(parts[1])
  if (!Number.isFinite(second)) return null
  // In shorthand "-0.5/1", the first sign applies to both halves.
  if (/^-/.test(parts[0]) && !/^[+-]/.test(parts[1])) second = -Math.abs(second)
  if (isReceiving) return (Math.abs(first) + Math.abs(second)) / 2
  return (first + second) / 2
}
const hiHandicapValue = match => {
  const value = match.hi_handicap_value ?? match.handicap
  if (value === null || value === undefined) return null
  return typeof value === 'string' && value.trim() === '' ? null : value
}
const hiResult = match => {
  if (!hasScore(match)) return null
  const handicap = parseHiHandicap(hiHandicapValue(match))
  if (handicap === null) return null
  const adjusted = score(match).home + handicap - score(match).away
  return adjusted > 0 ? 'win' : adjusted < 0 ? 'lose' : 'draw'
}
const hiResultLabel = match => {
  if (!hasScore(match)) return '比分未录入'
  if (hiHandicapValue(match) === null) {
    return '盘口未录入'
  }
  const result = hiResult(match)
  return result ? { win: '让球胜', draw: '让球平', lose: '让球负' }[result] : '盘口格式异常'
}
const parseTotal = value => {
  if (value === null || value === undefined || value === '') return null
  const text = String(value).trim().replace(/[↑↓升降]/g, '')
  const parts = text.split('/').map(part => Number(part.trim()))
  if (!parts.length || parts.some(part => !Number.isFinite(part))) return null
  return parts.reduce((sum, part) => sum + part, 0) / parts.length
}
const ouResult = match => {
  if (!hasScore(match)) return null
  const total = parseTotal(match.ou_current_total || match.ou_initial_total)
  if (total === null) return null
  const actual = score(match).home + score(match).away
  return actual > total ? 'over' : actual < total ? 'under' : 'push'
}
const ouResultLabel = match => {
  if (!hasScore(match)) return '比分未录入'
  const total = parseTotal(match.ou_current_total || match.ou_initial_total)
  if (total === null) return '盘口未录入'
  return { over: '大球', under: '小球', push: '走盘' }[ouResult(match)]
}
const ouTotalLabel = match => {
  const total = match.ou_current_total || match.ou_initial_total
  if (!total) return '盘口未录入'
  const direction = /(?:↑|升)$/.test(String(total)) ? ' ↑' : /(?:↓|降)$/.test(String(total)) ? ' ↓' : ''
  return `盘口 ${String(total).replace(/[↑↓升降]/g, '')}${direction}`
}

const label = (value, labels) => value ? labels[value] : '-'
const signed = value => {
  if (value === null || value === undefined || value === '') return '-'
  const text = String(value).trim()
  const parsed = parseHiHandicap(value)
  return parsed !== null && parsed > 0 && !text.startsWith('+') ? `+${text}` : text
}
const hitOdds = (match, market) => {
  if (market !== 'euro') return ''
  const key = { win: 'win', draw: 'draw', lose: 'lose' }[euroResult(match)]
  return key ? `赔率 ${match[`euro_current_${key}`] || match[`euro_initial_${key}`] || '-'}` : '暂无赔率'
}
const formatTime = value => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace('T', ' ')
  return date.toLocaleString('zh-CN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
const fetchMatches = async ({ append = false } = {}) => {
  if (append) loadingMore.value = true
  else loading.value = true
  if (append) loadMoreError.value = false
  error.value = ''
  try {
    const response = await axios.get('/api/matches', {
      params: {
        status: 2,
        page: page.value,
        page_size: pageSize,
        league: selectedLeagues.value.length ? selectedLeagues.value.join(',') : undefined,
        ...resultDateRange()
      }
    })
    const newMatches = response.data?.data || []
    matches.value = append ? [...matches.value, ...newMatches] : newMatches
    availableLeagues.value = (response.data?.leagues || []).filter(Boolean)
    totalAvailableMatches.value = Number(response.data?.available_total || 0)
    const totalPages = Number(response.data?.total_pages)
    hasMore.value = Number.isFinite(totalPages)
      ? page.value < totalPages
      : newMatches.length >= pageSize
    return true
  } catch (fetchError) {
    console.error(fetchError)
    if (!append) {
      matches.value = []
      error.value = '赛果加载失败，请稍后重试'
    } else {
      loadMoreError.value = true
    }
    return false
  } finally {
    if (append) loadingMore.value = false
    else loading.value = false
  }
}
const loadMore = async () => {
  if (loadingMore.value || loading.value || !hasMore.value) return
  page.value += 1
  const loaded = await fetchMatches({ append: true })
  if (!loaded) {
    page.value -= 1
    return
  }
  await nextTick()
  if (loadMoreSentinel.value && hasMore.value) {
    loadMoreObserver?.unobserve(loadMoreSentinel.value)
    observeLoadMoreSentinel()
  }
}

const observeLoadMoreSentinel = () => {
  if (!loadMoreObserver || !loadMoreSentinel.value || !hasMore.value) return
  loadMoreObserver.observe(loadMoreSentinel.value)
}

const createLoadMoreObserver = () => {
  loadMoreObserver?.disconnect()
  loadMoreObserver = new IntersectionObserver(entries => {
    if (entries.some(entry => entry.isIntersecting) && hasMore.value && !loadingMore.value && !loading.value && !loadMoreError.value) {
      loadMore()
    }
  }, {
    rootMargin: '0px 0px 100px 0px',
    threshold: 0.1
  })
  observeLoadMoreSentinel()
}

watch(loadMoreSentinel, (sentinel, previousSentinel) => {
  if (previousSentinel) loadMoreObserver?.unobserve(previousSentinel)
  if (sentinel) observeLoadMoreSentinel()
})

watch(hasMore, value => {
  if (value) observeLoadMoreSentinel()
  else loadMoreObserver?.disconnect()
})

watch([timeRange, () => selectedLeagues.value.join('|')], () => {
  page.value = 1
  matches.value = []
  hasMore.value = true
  loadMoreError.value = false
  fetchMatches()
  if (activeTab.value === 'profiles') fetchLeagueProfiles()
  nextTick(createLoadMoreObserver)
})

watch(activeTab, value => {
  if (value !== 'profiles') return
  const filterKey = selectedLeagues.value.slice().sort().join('|') || '*'
  if (loadedProfileFilter.value !== filterKey) fetchLeagueProfiles()
})

onMounted(() => {
  createLoadMoreObserver()
  fetchMatches()
})

onUnmounted(() => {
  loadMoreObserver?.disconnect()
  loadMoreObserver = null
})
</script>

<style scoped>
.results-content { padding: 12px; }
.results-view-tabs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; margin-bottom: 12px; padding: 4px; background: #e9e9ec; border-radius: 11px; }
.results-view-tabs button { height: 42px; color: #777780; font: inherit; font-size: 14px; font-weight: 600; background: transparent; border: 0; border-radius: 8px; cursor: pointer; }
.results-view-tabs button.active { color: #f33b48; background: #fff; box-shadow: 0 2px 7px rgb(24 24 30 / 10%); }
.results-view-tabs button:focus-visible { outline: 2px solid rgb(243 59 72 / 45%); outline-offset: -2px; }
.results-filters { display: grid; grid-template-columns: minmax(120px, .8fr) minmax(210px, 1.2fr); align-items: end; gap: 16px; margin-bottom: 12px; padding: 14px; background: #fff; border: 1px solid #f0f0f2; border-radius: 12px; box-shadow: 0 3px 14px rgb(30 35 50 / 5%); }
.filter-field { display: grid; min-width: 0; gap: 7px; }
.filter-label { color: #777; font-size: 12px; font-weight: 500; line-height: 1; }
.league-filter-trigger { display: flex; width: 100%; min-height: 40px; align-items: center; justify-content: space-between; gap: 8px; padding: 9px 13px 9px 12px; color: #25252a; background: #f8f8fa; border: 1px solid #dedee3; border-radius: 9px; font: inherit; font-size: 14px; cursor: pointer; }
.league-filter-trigger span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.league-filter-trigger i { width: 7px; height: 7px; flex: 0 0 auto; border-right: 1.5px solid #8c8c94; border-bottom: 1.5px solid #8c8c94; transform: translateY(-2px) rotate(45deg); }
.league-filter-trigger:focus-visible { border-color: #f33b48; outline: 3px solid rgb(243 59 72 / 12%); }
.time-segments { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); min-height: 40px; padding: 3px; background: #f1f1f4; border: 1px solid #e5e5e9; border-radius: 10px; }
.time-segments button { min-width: 0; padding: 7px 8px; color: #777780; background: transparent; border: 0; border-radius: 7px; font: inherit; font-size: 13px; white-space: nowrap; cursor: pointer; transition: color .18s ease, background-color .18s ease, box-shadow .18s ease; }
.time-segments button:hover { color: #33333a; }
.time-segments button.active { color: #f33b48; background: #fff; box-shadow: 0 1px 4px rgb(24 24 30 / 12%); font-weight: 600; }
.time-segments button:focus-visible { outline: 2px solid rgb(243 59 72 / 45%); outline-offset: -2px; }
@media (max-width: 430px) {
  .results-filters { grid-template-columns: minmax(105px, .72fr) minmax(190px, 1.28fr); gap: 10px; padding: 12px; }
  .league-filter-trigger { padding-inline: 10px; font-size: 12px; }
  .time-segments button { padding-inline: 5px; font-size: 12px; }
}
.league-filter-overlay { position: fixed; inset: 0; z-index: 950; display: flex; align-items: center; justify-content: center; padding: 18px 14px; background: rgb(0 0 0 / 45%); }
.league-filter-modal { display: flex; width: min(100%, 560px); height: min(76vh, 650px); min-height: 430px; flex-direction: column; background: #fff; border-radius: 11px; box-shadow: 0 16px 48px rgb(0 0 0 / 24%); overflow: hidden; }
.league-filter-modal-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 19px 22px 14px; }
.league-filter-modal-head div { display: flex; align-items: center; gap: 10px; color: #a9b5c0; }
.league-filter-modal-head svg { width: 23px; height: 23px; fill: currentColor; }
.league-filter-modal-head strong { font-size: 15px; font-weight: 500; }
.league-filter-modal-head > span { color: #a9b5c0; font-size: 12px; text-align: right; }
.league-option-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px 18px; padding: 20px 24px; overflow-y: auto; }
.league-option-grid button { min-width: 0; height: 46px; overflow: hidden; color: #222b34; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; background: #edf2f6; border: 1px solid transparent; border-radius: 7px; }
.league-option-grid button.selected { color: #ee3445; background: #fff1f3; border-color: #f26b77; }
.league-options-empty { display: flex; flex: 1; align-items: center; justify-content: center; color: #aaa; font-size: 13px; }
.league-quick-actions { display: flex; gap: 11px; margin-top: auto; padding: 14px 24px 20px; }
.league-quick-actions button { min-width: 64px; height: 32px; padding: 0 13px; color: #9ba6b0; font-size: 12px; background: #fff; border: 1px solid #e2e5e8; border-radius: 17px; }
.league-quick-actions button:disabled { opacity: .45; }
.league-filter-modal footer { display: grid; grid-template-columns: 1fr 1fr; border-top: 1px solid #e7e7e9; }
.league-filter-modal footer button { height: 66px; color: #242a31; font-size: 17px; background: #fff; border: 0; }
.league-filter-modal footer button + button { border-left: 1px solid #e7e7e9; }
.league-filter-modal footer button:last-child { color: #ee3445; font-weight: 500; }
@media (max-width: 430px) {
  .league-filter-overlay { padding: 14px 12px; }
  .league-filter-modal { height: 72vh; min-height: 410px; }
  .league-filter-modal-head { padding: 17px 17px 12px; }
  .league-filter-modal-head > span { font-size: 11px; }
  .league-option-grid { gap: 10px 12px; padding: 18px 16px; }
  .league-option-grid button { height: 43px; font-size: 13px; }
  .league-quick-actions { padding: 12px 16px 16px; }
  .league-filter-modal footer button { height: 58px; font-size: 16px; }
}
.results-list { display: grid; gap: 10px; }
.result-card { padding: 13px; background: #fff; border-radius: 10px; box-shadow: 0 1px 4px rgb(0 0 0 / 5%); cursor: pointer; transition: transform .16s ease, box-shadow .16s ease; -webkit-tap-highlight-color: transparent; }
.result-card:hover { box-shadow: 0 4px 14px rgb(0 0 0 / 9%); transform: translateY(-1px); }
.result-card:active { box-shadow: 0 1px 4px rgb(0 0 0 / 5%); transform: scale(.995); }
.result-card:focus-visible { outline: 2px solid rgb(243 59 72 / 65%); outline-offset: 2px; }
.result-meta { display: flex; justify-content: space-between; gap: 12px; color: #999; font-size: 12px; }
.result-meta strong { color: #f33b48; }
.result-meta time { margin-left: auto; }
.match-number {
  margin-left: 6px;
  color: #999;
  font-size: 12px;
  font-weight: 400;
}
.result-score { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 10px; padding: 16px 2px; text-align: center; }
.result-score span { color: #222; font-size: 15px; font-weight: 600; }
.result-score strong { color: #222; font-size: 23px; white-space: nowrap; }
.result-score i { padding: 0 5px; color: #aaa; font-style: normal; }
.result-markets { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
.result-markets div { display: grid; gap: 3px; min-width: 0; padding: 9px; background: #f7f7f8; border-radius: 7px; }
.result-markets span, .result-markets small { overflow: hidden; color: #999; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.result-markets strong { color: #555; font-size: 13px; }
.result-markets strong.hit { color: #f33b48; }
.profile-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; padding: 14px; background: linear-gradient(135deg, #fff 0%, #fff8f9 100%); border: 1px solid #f1e3e6; border-radius: 12px; box-shadow: 0 3px 14px rgb(30 35 50 / 5%); }
.profile-toolbar > div { display: grid; min-width: 0; gap: 4px; }
.profile-toolbar strong { color: #2d2d32; font-size: 15px; }
.profile-toolbar span { color: #999; font-size: 10px; line-height: 1.4; }
.profile-filter-button { max-width: 42%; padding: 8px 12px; overflow: hidden; color: #f33b48; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; background: #fff; border: 1px solid #f2aab1; border-radius: 18px; cursor: pointer; }
.league-profiles-list { display: grid; gap: 10px; }
.league-profile-card { padding: 13px; background: #fff; border: 1px solid #f0e8e9; border-radius: 10px; box-shadow: 0 2px 8px rgb(30 35 50 / 5%); }
.league-profile-card > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 11px; }
.league-profile-card > header div { display: grid; gap: 3px; }
.league-profile-card > header strong { color: #2f2f34; font-size: 15px; }
.league-profile-card > header span { color: #aaa; font-size: 10px; }
.league-profile-card > header em { padding: 4px 8px; color: #999; font-size: 9px; font-style: normal; white-space: nowrap; background: #eee; border-radius: 10px; }
.league-profile-card > header em.eligible { color: #b85e69; background: #fae8eb; }
.league-profile-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
.league-profile-grid > div { display: grid; min-width: 0; gap: 2px; padding: 8px; background: #fff; border: 1px solid #f0e8e9; border-radius: 6px; }
.league-profile-grid span, .league-profile-grid small { overflow: hidden; color: #999; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.league-profile-grid strong { color: #3f3f43; font-size: 11px; }
.league-profile-signals { display: grid; gap: 4px; margin: 9px 0 0; padding: 0; list-style: none; }
.league-profile-signals li { position: relative; padding-left: 12px; color: #815961; font-size: 10px; line-height: 1.45; }
.league-profile-signals li::before { position: absolute; top: 6px; left: 2px; width: 4px; height: 4px; content: ""; background: #df6d79; border-radius: 50%; }
.league-profile-card > p { margin: 8px 0 0; color: #aaa; font-size: 9px; line-height: 1.4; }
.results-state { display: flex; min-height: 180px; align-items: center; justify-content: center; gap: 12px; color: #999; }
.results-error { flex-direction: column; color: #d44; }
.results-state button { padding: 8px 15px; border: 0; border-radius: 7px; background: #f33b48; color: #fff; }
.results-load-more { display: flex; justify-content: center; padding: 10px 0 4px; }
.results-load-more button { min-width: 132px; padding: 10px 24px; border: 0; border-radius: 999px; background: #f33b48; color: #fff; font-size: 14px; box-shadow: 0 3px 10px rgb(243 59 72 / 20%); cursor: pointer; }
.results-load-more button:disabled { background: #ccc; box-shadow: none; cursor: not-allowed; }
.results-load-more small { color: #aaa; font-size: 12px; }
.load-more-sentinel { height: 1px; visibility: hidden; }
</style>

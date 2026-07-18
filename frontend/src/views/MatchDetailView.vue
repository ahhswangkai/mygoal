<template>
  <main class="live-detail">
    <header class="detail-headerbar">
      <router-link to="/home" aria-label="返回">‹</router-link>
      <h1>比赛详情</h1>
      <button :class="{ rotating: loading }" aria-label="刷新" @click="fetchAll">↻</button>
    </header>

    <div v-if="loading && !match" class="page-state">比赛数据加载中…</div>
    <div v-else-if="error || !match" class="page-state error">
      <p>{{ error || '比赛不存在' }}</p>
      <button @click="fetchAll">重新加载</button>
    </div>

    <template v-else>
      <section class="match-panel">
        <div class="match-meta">{{ match.league }} · {{ match.match_number || match.round_id || '足球' }} · {{ match.match_time }}</div>
        <div class="match-main">
          <div class="team-side">
            <span class="team-logo home">{{ initial(match.home_team) }}</span>
            <strong>{{ match.home_team }}</strong>
            <small v-if="match.home_rank">排名 {{ match.home_rank }}</small>
          </div>
          <div class="score-side">
            <strong v-if="hasScore">{{ match.home_score }} - {{ match.away_score }}</strong>
            <strong v-else>VS</strong>
            <span>{{ match.status_text || (hasScore ? '完场' : '未开始') }}</span>
          </div>
          <div class="team-side">
            <span class="team-logo away">{{ initial(match.away_team) }}</span>
            <strong>{{ match.away_team }}</strong>
            <small v-if="match.away_rank">排名 {{ match.away_rank }}</small>
          </div>
        </div>
      </section>

      <nav class="main-tabs">
        <button :class="{ active: activeTab === 'status' }" @click="activeTab = 'status'">赛况</button>
        <button :class="{ active: activeTab === 'analysis' }" @click="activeTab = 'analysis'">分析</button>
        <button :class="{ active: activeTab === 'odds' }" @click="activeTab = 'odds'">赔率</button>
      </nav>

      <div class="tab-page">
        <template v-if="activeTab === 'analysis'">
          <DataSection title="AI 比赛前瞻">
            <div class="ai-analysis-card">
              <template v-if="aiContent">
                <div class="ai-analysis-head">
                  <span class="ai-label">Skills AI</span>
                  <strong>{{ aiContent.result_tendency || '数据不足' }}</strong>
                  <b>{{ aiContent.confidence ?? 0 }}%</b>
                </div>
                <p class="ai-summary">{{ aiContent.summary }}</p>
                <div class="ai-markets">
                  <div><span>亚盘倾向</span><strong>{{ aiContent.asian_tendency || '数据不足' }}</strong></div>
                  <div><span>大小球</span><strong>{{ aiContent.over_under_tendency || '数据不足' }}</strong></div>
                  <div><span>参考比分</span><strong>{{ (aiContent.score_candidates || []).join('、') || '数据不足' }}</strong></div>
                </div>
                <div v-if="aiContent.evidence?.length" class="ai-reasons">
                  <h3>核心依据</h3>
                  <p v-for="(item, index) in aiContent.evidence" :key="`e-${index}`"><i>{{ index + 1 }}</i>{{ item }}</p>
                </div>
                <div v-if="aiContent.risks?.length" class="ai-risks">
                  <h3>风险提示</h3>
                  <p v-for="(item, index) in aiContent.risks" :key="`r-${index}`">• {{ item }}</p>
                </div>
                <div v-if="aiSkillNames.length" class="ai-skills">
                  <span v-for="skill in aiSkillNames" :key="skill">{{ skill }}</span>
                </div>
                <div class="ai-analysis-foot">
                  <small>{{ aiAnalysis.model || '火山方舟' }} · {{ formatGeneratedAt(aiAnalysis.generated_at) }}</small>
                  <button :disabled="aiGenerating || !aiConfigured" @click="generateAiAnalysis(true)">
                    {{ aiGenerating ? '分析中…' : '重新分析' }}
                  </button>
                </div>
                <p class="ai-disclaimer">{{ aiContent.disclaimer }}</p>
              </template>
              <template v-else>
                <div class="ai-empty">
                  <p>{{ aiConfigured ? '加载比赛数据与相关 Skills，生成结构化赛前分析。' : '服务器尚未配置火山方舟 API Key。' }}</p>
                  <button :disabled="aiGenerating || !aiConfigured" @click="generateAiAnalysis(false)">
                    {{ aiGenerating ? '正在加载 Skills…' : '生成 AI 分析' }}
                  </button>
                </div>
              </template>
              <p v-if="aiError" class="ai-error">{{ aiError }}</p>
            </div>
          </DataSection>

          <DataSection title="近期战绩">
            <div class="sub-tabs">
              <button :class="{ active: recentTeam === 'home' }" @click="recentTeam = 'home'">{{ match.home_team }}</button>
              <button :class="{ active: recentTeam === 'away' }" @click="recentTeam = 'away'">{{ match.away_team }}</button>
            </div>
            <div v-if="currentRecent.length" class="match-table">
              <div class="table-head"><span>日期/赛事</span><span>主队</span><span>比分</span><span>客队</span><span>赛果</span></div>
              <div v-for="game in currentRecent.slice(0, 6)" :key="game.matchId" class="table-row">
                <span><b>{{ shortDate(game.matchDate) }}</b><small>{{ game.tournamentShortName }}</small></span>
                <span :class="{ focus: isCurrentTeam(game.homeTeamShortName) }">{{ game.homeTeamShortName }}</span>
                <strong>{{ game.fullCourtGoal || '-' }}</strong>
                <span :class="{ focus: isCurrentTeam(game.awayTeamShortName) }">{{ game.awayTeamShortName }}</span>
                <em :class="resultClass(game)">{{ resultText(game) }}</em>
              </div>
            </div>
            <EmptyData v-else />
          </DataSection>

          <DataSection title="历史交锋">
            <div v-if="history.length" class="match-table">
              <div class="table-head"><span>日期/赛事</span><span>主队</span><span>比分</span><span>客队</span><span>赛果</span></div>
              <div v-for="game in history.slice(0, 6)" :key="game.matchId" class="table-row">
                <span><b>{{ shortDate(game.matchDate) }}</b><small>{{ game.tournamentShortName }}</small></span>
                <span>{{ game.homeTeamShortName }}</span>
                <strong>{{ game.fullCourtGoal || '-' }}</strong>
                <span>{{ game.awayTeamShortName }}</span>
                <em :class="resultClass(game)">{{ resultText(game) }}</em>
              </div>
            </div>
            <EmptyData v-else />
          </DataSection>

          <DataSection title="联赛积分排名">
            <div class="ranking-compare">
              <div><strong>{{ match.home_rank || '--' }}</strong><span>{{ match.home_team }}</span><small>当前排名</small></div>
              <i></i>
              <div><strong>{{ match.away_rank || '--' }}</strong><span>{{ match.away_team }}</span><small>当前排名</small></div>
            </div>
          </DataSection>

          <DataSection title="未来赛程">
            <div v-if="futureRows.length" class="future-grid">
              <div v-for="(team, index) in futureRows" :key="index">
                <h3>{{ index === 0 ? match.home_team : match.away_team }}</h3>
                <p v-for="game in team.slice(0, 3)" :key="game.matchId">
                  <span>{{ shortDate(game.matchDateTime || game.matchDate) }}</span>
                  <b>{{ game.homeTeamShortName }} vs {{ game.awayTeamShortName }}</b>
                </p>
              </div>
            </div>
            <EmptyData v-else />
          </DataSection>
        </template>

        <template v-else-if="activeTab === 'odds'">
          <DataSection v-for="market in markets" :key="market.key" :title="market.label">
            <div class="odds-table">
              <div class="odds-head"><span></span><span v-for="item in market.items" :key="item.label">{{ item.label }}</span></div>
              <div><b>初盘</b><span v-for="item in market.items" :key="item.label">{{ clean(item.initial) }}</span></div>
              <div><b>即时</b><span v-for="item in market.items" :key="item.label">{{ clean(item.current) }} <i :class="trend(item)">{{ trendIcon(item) }}</i></span></div>
            </div>
          </DataSection>
        </template>

        <template v-else>
          <DataSection title="比赛信息">
            <div class="info-list">
              <p><span>比赛时间</span><b>{{ match.match_time || '-' }}</b></p>
              <p><span>比赛轮次</span><b>{{ match.round || match.round_id || '-' }}</b></p>
              <p><span>当前状态</span><b>{{ match.status_text || '-' }}</b></p>
              <p><span>竞彩编号</span><b>{{ match.match_number || '-' }}</b></p>
            </div>
          </DataSection>
          <DataSection title="AI 预测">
            <div v-if="prediction" class="prediction">
              <div><span>胜平负</span><strong>{{ prediction.win_prediction || '--' }}</strong><small>{{ prediction.win_confidence || '--' }}% 置信</small></div>
              <div><span>亚盘</span><strong>{{ prediction.asian_prediction || '--' }}</strong><small>{{ prediction.asian_confidence || '--' }}% 置信</small></div>
              <div><span>大小球</span><strong>{{ prediction.ou_prediction || '--' }}</strong><small>{{ prediction.ou_confidence || '--' }}% 置信</small></div>
            </div>
            <EmptyData v-else />
          </DataSection>
        </template>
      </div>
    </template>
  </main>
</template>

<script setup>
import { computed, defineComponent, h, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { openAuth } from '../auth'

const DataSection = defineComponent({
  props: { title: String },
  setup(props, { slots }) {
    return () => h('section', { class: 'data-section' }, [
      h('header', [h('i'), h('h2', props.title)]),
      h('div', { class: 'section-body' }, slots.default?.())
    ])
  }
})
const EmptyData = defineComponent({ setup: () => () => h('div', { class: 'empty-data' }, '暂无数据') })

const route = useRoute()
const match = ref(null)
const prediction = ref(null)
const aiAnalysis = ref(null)
const aiConfigured = ref(false)
const aiGenerating = ref(false)
const aiError = ref('')
const preview = ref({})
const loading = ref(false)
const error = ref('')
const activeTab = ref('analysis')
const recentTeam = ref('home')
const aiContent = computed(() => aiAnalysis.value?.analysis || null)
const aiSkillNames = computed(() => {
  const selected = aiAnalysis.value?.selected_skills
  if (Array.isArray(selected)) {
    return selected.map(item => typeof item === 'string' ? item : item?.name).filter(Boolean)
  }
  return aiContent.value?.skills || []
})

const hasScore = computed(() => match.value?.home_score !== '' && match.value?.home_score != null && match.value?.away_score !== '' && match.value?.away_score != null)
const normalizeFixture = game => ({
  ...game,
  matchId: game.matchId || game.match_id,
  matchDate: game.matchDate || game.date,
  tournamentShortName: game.tournamentShortName || game.league,
  homeTeamShortName: game.homeTeamShortName || game.home_team,
  awayTeamShortName: game.awayTeamShortName || game.away_team,
  fullCourtGoal: game.fullCourtGoal || game.score,
})
const recent = computed(() => preview.value?.recent || {})
const currentRecent = computed(() =>
  (recent.value?.[recentTeam.value]?.matchList || recent.value?.[recentTeam.value] || []).map(normalizeFixture)
)
const history = computed(() =>
  (preview.value?.history?.matchList || preview.value?.history || []).map(normalizeFixture)
)
const futureRows = computed(() => {
  const data = preview.value?.future || {}
  return [data.home?.matchList || data.home || [], data.away?.matchList || data.away || []]
    .filter(item => Array.isArray(item) && item.length)
    .map(list => list.map(game => ({
      ...normalizeFixture(game),
      matchDateTime: game.matchDateTime || game.date
    })))
})
const markets = computed(() => {
  const m = match.value || {}
  return [
    { key: 'euro', label: '欧赔', show: m.euro_current_win, items: [
      { label: '主胜', initial: m.euro_initial_win, current: m.euro_current_win },
      { label: '平局', initial: m.euro_initial_draw, current: m.euro_current_draw },
      { label: '客胜', initial: m.euro_initial_lose, current: m.euro_current_lose }] },
    { key: 'asian', label: '亚盘', show: m.asian_current_home_odds, items: [
      { label: '主水', initial: m.asian_initial_home_odds, current: m.asian_current_home_odds },
      { label: '盘口', initial: m.asian_initial_handicap, current: m.asian_current_handicap },
      { label: '客水', initial: m.asian_initial_away_odds, current: m.asian_current_away_odds }] },
    { key: 'ou', label: '大小球', show: m.ou_current_over_odds, items: [
      { label: '大球', initial: m.ou_initial_over_odds, current: m.ou_current_over_odds },
      { label: '盘口', initial: m.ou_initial_total, current: m.ou_current_total },
      { label: '小球', initial: m.ou_initial_under_odds, current: m.ou_current_under_odds }] }
  ].filter(item => item.show)
})

async function fetchAll() {
  loading.value = true
  error.value = ''
  try {
    const id = route.params.id
    const [matchRes, predRes, previewRes, aiRes] = await Promise.all([
      fetch(`/api/match/${id}`),
      fetch('/api/predictions?limit=200'),
      fetch(`/api/match/${id}/500-analysis`),
      fetch(`/api/match/${id}/ai-analysis`)
    ])
    const matchData = await matchRes.json()
    if (!matchRes.ok || !matchData.success) throw new Error(matchData.message || '比赛加载失败')
    match.value = matchData.data
    if (predRes.ok) {
      const data = await predRes.json()
      prediction.value = (data.data || []).find(item => String(item.match_id) === String(id)) || null
    }
    if (previewRes.ok) {
      const data = await previewRes.json()
      preview.value = data.data || {}
    }
    if (aiRes.ok) {
      const data = await aiRes.json()
      aiAnalysis.value = data.data || null
      aiConfigured.value = !!data.configured
    }
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function generateAiAnalysis(force = false) {
  aiGenerating.value = true
  aiError.value = ''
  try {
    const response = await fetch(`/api/match/${route.params.id}/ai-analysis`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ force })
    })
    const data = await response.json().catch(() => ({}))
    if (response.status === 401) {
      openAuth('login')
      throw new Error('登录后才能生成 AI 分析')
    }
    if (!response.ok || !data.success) {
      throw new Error(data.message || 'AI 分析生成失败')
    }
    aiAnalysis.value = data.data
  } catch (e) {
    aiError.value = e.message || 'AI 分析生成失败'
  } finally {
    aiGenerating.value = false
  }
}

const initial = name => String(name || '队').slice(0, 1)
const shortDate = date => String(date || '').slice(5).replace('-', '/')
const formatGeneratedAt = value => {
  if (!value) return '时间未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
}
const clean = value => value == null || value === '' ? '-' : String(value).replace(/[↑↓升降]+$/, '')
const numeric = value => Number(String(value ?? '').replace(/[^\d.+-]/g, ''))
const trendIcon = item => Number.isFinite(numeric(item.current)) && Number.isFinite(numeric(item.initial)) ? numeric(item.current) > numeric(item.initial) ? '↑' : numeric(item.current) < numeric(item.initial) ? '↓' : '' : ''
const trend = item => trendIcon(item) === '↑' ? 'up' : trendIcon(item) === '↓' ? 'down' : ''
const isCurrentTeam = name => String(name || '').includes(String(recentTeam.value === 'home' ? match.value?.home_team : match.value?.away_team).slice(0, 2))
const resultText = game => game.result || (game.teamMatchResult === 'draw' || game.winningTeam === 'draw' ? '平' : game.teamMatchResult === 'home' || game.winningTeam === 'home' ? '胜' : '负')
const resultClass = game => ({ '胜': 'win', '平': 'draw', '负': 'loss' }[resultText(game)])

watch(() => route.params.id, fetchAll)
onMounted(fetchAll)
</script>

<style scoped>
.live-detail{min-height:100vh;background:#f3f3f3;color:#333;padding-bottom:30px}.detail-headerbar{height:46px;display:grid;grid-template-columns:42px 1fr 42px;align-items:center;background:#fff;border-bottom:1px solid #e8e8e8;position:sticky;top:0;z-index:30}.detail-headerbar h1{text-align:center;font-size:17px;font-weight:500}.detail-headerbar a,.detail-headerbar button{border:0;background:none;color:#555;font-size:30px;text-align:center}.detail-headerbar button{font-size:22px}.rotating{animation:rotate 1s linear infinite}@keyframes rotate{to{transform:rotate(360deg)}}.match-panel{background:#fff;padding:10px 12px 16px}.match-meta{text-align:center;color:#999;font-size:11px}.match-main{display:grid;grid-template-columns:1fr 86px 1fr;align-items:center;margin-top:14px}.team-side{display:flex;align-items:center;flex-direction:column;min-width:0}.team-side strong{margin-top:7px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px}.team-side small{margin-top:3px;color:#aaa;font-size:10px}.team-logo{width:46px;height:46px;display:grid;place-items:center;border-radius:50%;color:#fff;font-weight:700;border:3px solid #eef2f6}.team-logo.home{background:#5083b9}.team-logo.away{background:#d28a45}.score-side{text-align:center}.score-side strong{display:block;font-size:26px;color:#222}.score-side span{display:inline-block;margin-top:5px;padding:2px 8px;border-radius:10px;background:#f3f3f3;color:#999;font-size:10px}.main-tabs{height:45px;display:grid;grid-template-columns:repeat(3,1fr);background:#fff;border-top:1px solid #eee;border-bottom:1px solid #ddd;position:sticky;top:46px;z-index:20}.main-tabs button{position:relative;border:0;background:none;color:#666;font-size:14px}.main-tabs button.active{color:#e64b3c;font-weight:600}.main-tabs button.active:after{content:"";position:absolute;bottom:0;left:34%;right:34%;height:2px;background:#e64b3c}.tab-page{padding-top:8px}.data-section{background:#fff;margin-bottom:8px}.data-section>header{height:42px;display:flex;align-items:center;border-bottom:1px solid #eee;padding:0 12px}.data-section>header i{width:3px;height:15px;background:#e64b3c;margin-right:7px}.data-section h2{font-size:14px;font-weight:600}.section-body{padding-bottom:10px}.ai-analysis-card{padding:12px}.ai-analysis-head{display:flex;align-items:center;gap:8px}.ai-analysis-head .ai-label{padding:3px 7px;border-radius:4px;background:#2d3142;color:#fff;font-size:10px}.ai-analysis-head strong{flex:1;color:#e64b3c;font-size:18px}.ai-analysis-head b{font-size:20px;color:#e64b3c}.ai-summary{margin-top:12px;line-height:1.7;font-size:13px;color:#555}.ai-markets{display:grid;grid-template-columns:repeat(3,1fr);margin-top:12px;padding:10px 0;border:1px solid #f1eeee;border-radius:7px;background:#fffafa;text-align:center}.ai-markets div+div{border-left:1px solid #eee}.ai-markets span,.ai-markets strong{display:block}.ai-markets span{color:#999;font-size:10px}.ai-markets strong{margin-top:5px;font-size:12px;color:#444}.ai-reasons,.ai-risks{margin-top:14px}.ai-reasons h3,.ai-risks h3{margin-bottom:7px;font-size:12px}.ai-reasons p,.ai-risks p{display:flex;align-items:flex-start;gap:7px;margin-top:6px;line-height:1.5;font-size:12px;color:#555}.ai-reasons i{flex:0 0 18px;height:18px;display:grid;place-items:center;border-radius:50%;background:#fbe5e3;color:#e64b3c;font-style:normal;font-size:10px}.ai-risks{padding:9px;border-radius:6px;background:#fff7e8}.ai-risks p{color:#8c651d}.ai-skills{display:flex;flex-wrap:wrap;gap:5px;margin-top:12px}.ai-skills span{padding:3px 6px;border-radius:3px;background:#f2f3f5;color:#777;font-size:9px}.ai-analysis-foot{display:flex;justify-content:space-between;align-items:center;margin-top:14px}.ai-analysis-foot small{color:#aaa;font-size:9px}.ai-analysis-foot button,.ai-empty button{border:0;border-radius:16px;padding:7px 13px;background:#e64b3c;color:#fff;font-size:11px}.ai-analysis-foot button:disabled,.ai-empty button:disabled{background:#ccc}.ai-disclaimer{margin-top:10px;color:#aaa;font-size:9px;text-align:center}.ai-empty{text-align:center;padding:20px 0}.ai-empty p{margin-bottom:12px;color:#888;font-size:12px}.ai-error{margin-top:9px;color:#e64b3c;text-align:center;font-size:11px}.sub-tabs{display:grid;grid-template-columns:1fr 1fr;border-bottom:1px solid #eee}.sub-tabs button{height:38px;border:0;background:#fafafa;color:#777;font-size:12px}.sub-tabs button.active{background:#fff;color:#e64b3c;border-bottom:2px solid #e64b3c}.match-table{font-size:10px}.table-head,.table-row{display:grid;grid-template-columns:68px 1fr 44px 1fr 32px;align-items:center;text-align:center;min-height:39px;border-bottom:1px solid #f0f0f0}.table-head{min-height:30px;background:#fafafa;color:#aaa}.table-row>span:first-child b,.table-row>span:first-child small{display:block;font-weight:400}.table-row>span:first-child small{color:#aaa;margin-top:2px}.table-row strong{color:#e64b3c}.table-row .focus{color:#e64b3c}.table-row em{width:20px;height:20px;display:grid;place-items:center;margin:auto;border-radius:2px;color:#fff;font-style:normal}.table-row em.win{background:#e64b3c}.table-row em.draw{background:#aaa}.table-row em.loss{background:#4c86bd}.ranking-compare{display:grid;grid-template-columns:1fr 1px 1fr;align-items:center;text-align:center;padding:20px}.ranking-compare i{height:48px;background:#eee}.ranking-compare strong,.ranking-compare span,.ranking-compare small{display:block}.ranking-compare strong{font-size:28px;color:#e64b3c}.ranking-compare span{margin-top:4px;font-size:13px}.ranking-compare small{margin-top:3px;color:#aaa}.future-grid{display:grid;grid-template-columns:1fr 1fr}.future-grid>div{padding:10px}.future-grid>div+div{border-left:1px solid #eee}.future-grid h3{text-align:center;font-size:12px;margin-bottom:7px}.future-grid p{display:flex;gap:6px;padding:6px 0;border-top:1px solid #f2f2f2;font-size:10px}.future-grid p span{color:#999}.future-grid p b{font-weight:400}.odds-table{margin:10px;border:1px solid #eee}.odds-table>div{display:grid;grid-template-columns:55px repeat(3,1fr);align-items:center;min-height:38px;text-align:center;border-top:1px solid #eee;font-size:12px}.odds-table>div:first-child{border:0;background:#fafafa;color:#999}.odds-table b{font-weight:400;color:#888}.odds-table i{font-style:normal}.odds-table i.up{color:#e64b3c}.odds-table i.down{color:#139862}.info-list{padding:5px 12px}.info-list p{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #f2f2f2;font-size:12px}.info-list span{color:#999}.prediction{display:grid;grid-template-columns:repeat(3,1fr);padding:15px 0;text-align:center}.prediction div+div{border-left:1px solid #eee}.prediction span,.prediction strong,.prediction small{display:block}.prediction span,.prediction small{color:#999;font-size:10px}.prediction strong{margin:6px 0;color:#e64b3c;font-size:16px}.empty-data,.page-state{text-align:center;color:#aaa;padding:30px}.page-state{min-height:60vh;display:grid;place-items:center}.page-state.error button{border:0;background:#e64b3c;color:#fff;border-radius:20px;padding:8px 18px}@media(min-width:620px){.live-detail{max-width:600px;margin:auto;box-shadow:0 0 20px rgba(0,0,0,.08)}}
</style>

<template>
  <main class="live-detail">
    <header class="detail-headerbar">
      <button type="button" class="detail-back" aria-label="返回" @click="goBack">‹</button>
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
          <DataSection v-if="dailyAiContent" title="AI 全日研判 · 本场">
            <div class="daily-detail-card">
              <div class="daily-detail-head">
                <span><i>AI</i>火山全日研判</span>
                <small>最新研判 {{ formatGeneratedAt(dailyAiAnalysis.generated_at) }}</small>
              </div>
              <div class="daily-detail-result">
                <div class="daily-detail-picks">
                  <strong v-if="dailyAiPrimaryRadar" class="daily-radar-main">
                    <small>{{ radarTierLabel(dailyAiPrimaryRadar.tier) }}</small>{{ dailyAiPrimaryRadar.selection }}
                  </strong>
                  <strong v-else-if="dailyAiContent.no_bet" class="daily-no-bet"><small>结论</small>不下注</strong>
                  <span v-if="dailyAiPrimaryRadar"><small>总盘结论</small>{{ dailyAiContent.decision || '观望' }}</span>
                  <span><small>赛果预测</small>{{ dailyAiContent.predicted_result || '观望' }}</span>
                  <strong><small>主选</small>{{ dailyAiContent.primary_play || '观望' }}</strong>
                  <span v-if="dailyAiContent.secondary_play && dailyAiContent.secondary_play !== '观望'">
                    <small>防选</small>{{ dailyAiContent.secondary_play }}
                  </span>
                  <span v-if="dailyAiContent.handicap_play && dailyAiContent.handicap_play !== '观望'">
                    <small>让球</small>{{ dailyAiContent.handicap_play }}
                  </span>
                </div>
                <b>{{ dailyAiPrimaryRadar ? starText(dailyAiPrimaryRadar.rating) : dailyAiContent.no_bet ? '方向观察' : (dailyAiContent.star_text || starText(dailyAiContent.rating)) }}</b>
              </div>
              <div
                v-if="dailyAiContent.consistency_guard?.triggered"
                class="daily-detail-guard"
              >
                <strong>一致性护栏已生效</strong>
                <span>
                  AI 原选 {{ dailyAiContent.model_primary_play }}，
                  正式推荐按 {{ dailyAiContent.primary_play }} 记录与复盘
                </span>
              </div>
              <p class="daily-detail-verdict">{{ dailyAiContent.verdict }}</p>
              <div v-if="dailyAiRadarRows.length" class="daily-radar-detail">
                <header>
                  <strong>平 / 让平雷达</strong>
                  <span>独立精确进球差扫描</span>
                </header>
                <p
                  v-for="radar in dailyAiRadarRows"
                  :key="radar.model_key"
                  :class="radar.tier"
                >
                  <span>
                    <b>{{ radar.selection }}</b>
                    <i>{{ radarTierLabel(radar.tier) }}</i>
                    <strong>{{ starText(radar.rating) }}</strong>
                  </span>
                  <small>
                    {{ radar.definition }} · 概率 {{ percentText(radar.probability) }} ·
                    赔率 {{ radar.odds ?? '--' }} · 价值 {{ signedMetric(radar.odds_value) }}%
                  </small>
                  <em>{{ radar.reason }}</em>
                </p>
              </div>
              <div class="daily-detail-odds">
                <p><span>欧赔</span><b>{{ triplet(dailyAiSnapshot.euro?.current) }}</b></p>
                <p><span>亚盘</span><b>{{ triplet(dailyAiSnapshot.asian?.current) }}</b></p>
                <p>
                  <span>竞彩 {{ signedHandicap(dailyAiSnapshot.sporttery_handicap?.value) }}</span>
                  <b>{{ triplet(dailyAiSnapshot.sporttery_handicap?.current) }}</b>
                </p>
                <p><span>大小球</span><b>{{ totalTriplet(dailyAiSnapshot.total?.current) }}</b></p>
              </div>
              <div class="daily-detail-values">
                <p><span>FAE概率</span><b>{{ dailyAiContent.prediction_probability ?? '--' }}%</b></p>
                <p><span>市场概率</span><b>{{ dailyAiContent.market_implied_probability ?? '--' }}%</b></p>
                <p><span>价值指数</span><b>{{ dailyAiContent.value_score ?? '--' }}分</b></p>
                <p><span>盘口可信</span><b>{{ dailyAiContent.market_confidence?.score ?? '--' }}分</b></p>
                <p><span>投注分</span><b>{{ dailyAiContent.bet_score ?? '--' }}分</b></p>
                <p>
                  <span>{{ dailyAiPrimaryRadar ? '总盘结论' : '投注结论' }}</span>
                  <b :class="{ danger: dailyAiContent.no_bet }">{{ dailyAiContent.decision || '观望' }}</b>
                </p>
              </div>
              <HistoricalGoalMarginCard
                :model="dailyAiSnapshot.historical_goal_margin_model"
                :calibration="dailyAiContent.historical_calibration"
              />
              <div class="daily-detail-markets">
                <p v-for="item in dailyAiMarkets" :key="item.key">
                  <span>{{ item.label }}</span>
                  <b>{{ dailyAiContent.market_analysis?.[item.key] || '输入数据不足' }}</b>
                </p>
              </div>
              <div v-if="dailyAiContent.evidence?.length" class="ai-reasons">
                <h3><i aria-hidden="true"></i>核心依据</h3>
                <p v-for="(item, index) in dailyAiContent.evidence" :key="`daily-e-${index}`">
                  <i>{{ index + 1 }}</i><span>{{ item }}</span>
                </p>
              </div>
              <div v-if="dailyAiContent.risks?.length" class="ai-risks">
                <h3><i aria-hidden="true">!</i>风险提示</h3>
                <p v-for="(item, index) in dailyAiContent.risks" :key="`daily-r-${index}`">
                  <span aria-hidden="true">•</span>{{ item }}
                </p>
              </div>
              <div class="daily-detail-foot">
                <span>参考比分</span>
                <b>{{ dailyAiContent.score_candidates?.join('　') || '暂无' }}</b>
              </div>
            </div>
          </DataSection>

          <DataSection :title="dailyAiContent ? 'FAE 核心分析' : 'AI 比赛前瞻'">
            <div class="ai-analysis-card">
              <template v-if="aiContent">
                <div class="ai-analysis-hero">
                  <div class="ai-analysis-head">
                    <span class="ai-label"><i aria-hidden="true">{{ isFaeAnalysis ? 'FAE' : 'AI' }}</i>{{ aiEngineLabel }}</span>
                    <span class="ai-confidence" :class="{ 'fae-danger-score': faeRisk.dangerous }">
                      <b>{{ faeRecommendation.bet_score ?? faeRecommendation.score ?? aiContent.confidence ?? 0 }}</b>{{ isFaeAnalysis ? ' 投注分' : '% 置信度' }}
                    </span>
                  </div>
                  <strong class="ai-result">{{ faeRecommendation.primary || aiContent.result_tendency || '数据不足' }}</strong>
                  <div v-if="isFaeAnalysis" class="fae-recommendation-meta">
                    <span v-if="faeRecommendation.no_bet" class="risk-高">不下注</span>
                    <span class="fae-stars">{{ faeRecommendation.star_text || starText(faeRecommendation.stars) }}</span>
                    <span>{{ faeRecommendation.market || '综合推荐' }}</span>
                    <span>价值 {{ faeRecommendation.value_score ?? '--' }}分</span>
                    <span>盘口可信 {{ faeRecommendation.market_confidence?.score ?? '--' }}分</span>
                    <span :class="`risk-${faeRisk.level || '低'}`">{{ faeRisk.level || '低' }}风险</span>
                  </div>
                  <p class="ai-summary">{{ aiContent.summary }}</p>
                </div>
                <div v-if="faeMarketTypes.length" class="fae-market-types">
                  <span v-for="item in faeMarketTypes" :key="item.code" :title="item.reason">
                    <b>{{ item.code }}</b>{{ item.name }}
                  </span>
                </div>
                <div v-if="faeDimensionScores.length" class="fae-score-panel">
                  <div class="fae-panel-title"><span>FAE 多维评分</span><b>{{ aiContent.overall_score ?? '--' }}分</b></div>
                  <div class="fae-score-grid">
                    <div v-for="item in faeDimensionScores" :key="item.key" class="fae-score-card" :class="{ missing: item.data_status === 'missing' }">
                      <div><span>{{ item.label }}</span><b>{{ item.score }}</b></div>
                      <em>{{ item.star_text || starText(item.stars) }}</em>
                      <small>{{ item.tendency }}</small>
                    </div>
                  </div>
                </div>
                <div v-if="isFaeAnalysis && faeProbabilities.home_win != null" class="fae-probability-panel">
                  <div class="fae-panel-title">
                    <span>概率参考</span>
                    <small>{{ aiContent.probability_basis?.label || 'FAE估算（未校准）' }}</small>
                  </div>
                  <div class="fae-probability-row" v-for="item in faeOutcomeRows" :key="item.key">
                    <span>{{ item.label }}</span>
                    <i><b :style="{ width: `${item.value}%` }"></b></i>
                    <strong>{{ item.value }}%</strong>
                  </div>
                  <div class="fae-margin-probabilities">
                    <span>赢一球 <b>{{ faeProbabilities.home_win_by_one ?? '--' }}%</b></span>
                    <span>赢两球+ <b>{{ faeProbabilities.home_win_by_two_plus ?? '--' }}%</b></span>
                    <span v-if="faeHandicapSummary">竞彩{{ faeProbabilities.sporttery_handicap > 0 ? '+' : '' }}{{ faeProbabilities.sporttery_handicap }} {{ faeHandicapSummary.label }} <b>{{ faeHandicapSummary.value }}%</b></span>
                    <span v-if="faeTotalSummary">{{ faeProbabilities.total_line }}球 {{ faeTotalSummary.label }} <b>{{ faeTotalSummary.value }}%</b></span>
                  </div>
                  <p class="fae-probability-note">
                    {{ aiContent.probability_basis?.note || '概率仅用于市场比较，不等同于真实胜率。' }}
                  </p>
                </div>
                <div class="ai-markets">
                  <div class="ai-market-item">
                    <span><i aria-hidden="true">亚</i>亚盘倾向</span>
                    <strong>{{ aiContent.asian_tendency || '数据不足' }}</strong>
                  </div>
                  <div class="ai-market-item">
                    <span><i aria-hidden="true">球</i>大小球</span>
                    <strong>{{ aiContent.over_under_tendency || '数据不足' }}</strong>
                  </div>
                  <div class="ai-market-item ai-score-item">
                    <span><i aria-hidden="true">分</i>参考比分</span>
                    <strong>
                      <em v-for="score in (aiContent.score_candidates || [])" :key="score">{{ score }}</em>
                      <template v-if="!aiContent.score_candidates?.length">数据不足</template>
                    </strong>
                  </div>
                </div>
                <div v-if="aiContent.evidence?.length" class="ai-reasons">
                  <h3><i aria-hidden="true"></i>核心依据</h3>
                  <p v-for="(item, index) in aiContent.evidence" :key="`e-${index}`"><i>{{ index + 1 }}</i><span>{{ item }}</span></p>
                </div>
                <div v-if="aiContent.risks?.length" class="ai-risks">
                  <h3><i aria-hidden="true">!</i>风险提示</h3>
                  <p v-for="(item, index) in aiContent.risks" :key="`r-${index}`"><span aria-hidden="true">•</span>{{ item }}</p>
                </div>
                <div v-if="aiModuleNames.length" class="ai-skills">
                  <span v-for="module in aiModuleNames" :key="module">{{ module }}</span>
                </div>
                <div class="ai-analysis-foot">
                  <small>{{ aiFooterLabel }} · {{ formatGeneratedAt(aiAnalysis.generated_at) }}</small>
                  <button :disabled="aiGenerating || !aiConfigured" @click="generateAiAnalysis(true)">
                    {{ aiGenerating ? 'FAE 运行中…' : '重新运行' }}
                  </button>
                </div>
                <p class="ai-disclaimer">{{ aiContent.disclaimer }}</p>
              </template>
              <template v-else>
                <div class="ai-empty">
                  <p>{{ aiConfigured ? '加载盘口与基本面数据，运行 Football AI Engine。' : 'FAE 暂不可用。' }}</p>
                  <button :disabled="aiGenerating || !aiConfigured" @click="generateAiAnalysis(false)">
                    {{ aiGenerating ? 'FAE 运行中…' : '运行 FAE 分析' }}
                  </button>
                </div>
              </template>
              <p v-if="aiError" class="ai-error">{{ aiError }}</p>
            </div>
          </DataSection>

          <DataSection v-if="faeReview" title="FAE 赛后复盘">
            <div class="fae-review-card">
              <div class="fae-review-result">
                <div><span>赛果</span><strong>{{ faeReview.result?.score || '--' }}</strong></div>
                <i></i>
                <div><span>预测</span><strong>{{ faeReview.prediction?.primary || '--' }}</strong></div>
                <em :class="{ hit: faeReview.prediction?.correct === true, miss: faeReview.prediction?.correct === false }">
                  {{ faeReview.prediction?.correct === true ? '✓' : faeReview.prediction?.correct === false ? '×' : faeReview.prediction?.result === 'push' ? '走' : '--' }}
                </em>
              </div>
              <div v-if="faeReview.diagnosis?.why_wrong?.length" class="fae-review-list wrong">
                <h3>为什么错</h3>
                <p v-for="item in faeReview.diagnosis.why_wrong" :key="item">× <span>{{ item }}</span></p>
              </div>
              <div v-if="faeReview.diagnosis?.what_worked?.length" class="fae-review-list worked">
                <h3>哪些判断有效</h3>
                <p v-for="item in faeReview.diagnosis.what_worked" :key="item">✓ <span>{{ item }}</span></p>
              </div>
              <div v-if="faeReview.learning_adjustments?.length" class="fae-learning-updates">
                <h3>AI 学习更新</h3>
                <p v-for="item in faeReview.learning_adjustments" :key="item.rule_id">
                  <span>{{ item.rule_id }}</span>
                  <b>{{ item.previous_weight }} → {{ item.new_weight }}</b>
                </p>
              </div>
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
            <div v-else-if="previewLoading" class="empty-data">近期数据加载中…</div>
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
            <div v-else-if="previewLoading" class="empty-data">历史交锋加载中…</div>
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
            <div v-else-if="previewLoading" class="empty-data">未来赛程加载中…</div>
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
import { useRoute, useRouter } from 'vue-router'
import HistoricalGoalMarginCard from '../components/HistoricalGoalMarginCard.vue'
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
const router = useRouter()
const match = ref(null)
const prediction = ref(null)
const aiAnalysis = ref(null)
const dailyAiAnalysis = ref(null)
const aiConfigured = ref(false)
const aiGenerating = ref(false)
const aiError = ref('')
const faeReview = ref(null)
const preview = ref({})
const previewLoading = ref(false)
const loading = ref(false)
const error = ref('')
const activeTab = ref('analysis')
const recentTeam = ref('home')
let fetchVersion = 0
const aiContent = computed(() => aiAnalysis.value?.analysis || null)
const dailyAiContent = computed(() => dailyAiAnalysis.value?.analysis || null)
const dailyAiSnapshot = computed(() => dailyAiAnalysis.value?.input_snapshot || {})
const dailyAiRadarRows = computed(() => {
  const radar = dailyAiContent.value?.draw_radar || {}
  return ['handicap_draw', 'ordinary_draw']
    .map(key => radar[key])
    .filter(item => item && item.tier && item.tier !== 'exclude')
    .sort((left, right) => radarTierWeight(right.tier) - radarTierWeight(left.tier))
})
const dailyAiPrimaryRadar = computed(() => (
  dailyAiRadarRows.value.find(item => item.tier === 'core') || null
))
const dailyAiMarkets = [
  { key: 'euro', label: '欧赔方向' },
  { key: 'asian', label: '亚盘升深' },
  { key: 'sporttery', label: '竞彩让球' },
  { key: 'total', label: '大小球' },
  { key: 'consistency', label: '市场一致性' }
]
const isFaeAnalysis = computed(() => !!(aiAnalysis.value?.engine || aiContent.value?.engine_code === 'FAE'))
const faeRecommendation = computed(() => aiContent.value?.recommendation || {})
const faeRisk = computed(() => aiContent.value?.risk || {})
const faeProbabilities = computed(() => aiContent.value?.probabilities || {})
const faeMarketTypes = computed(() => aiContent.value?.market_types || [])
const faeDimensionScores = computed(() => {
  const scores = aiContent.value?.dimension_scores
  if (!scores || typeof scores !== 'object') return []
  const order = ['handicap', 'euro', 'over_under', 'sporttery', 'motivation', 'injuries', 'history', 'form']
  return order.map(key => scores[key]).filter(Boolean)
})
const faeOutcomeRows = computed(() => [
  { key: 'home', label: '主胜', value: faeProbabilities.value.home_win ?? 0 },
  { key: 'draw', label: '平局', value: faeProbabilities.value.draw ?? 0 },
  { key: 'away', label: '客胜', value: faeProbabilities.value.away_win ?? 0 },
])
const faeHandicapSummary = computed(() => {
  const values = faeProbabilities.value.hhad
  if (!values) return null
  const labels = { win: '让胜', draw: '让平', lose: '让负' }
  const key = Object.keys(labels).sort((a, b) => (values[b] ?? 0) - (values[a] ?? 0))[0]
  return key ? { label: labels[key], value: values[key] ?? 0 } : null
})
const faeTotalSummary = computed(() => {
  const values = faeProbabilities.value.over_under
  if (!values) return null
  const key = (values.over ?? 0) >= (values.under ?? 0) ? 'over' : 'under'
  return { label: key === 'over' ? '大球' : '小球', value: values[key] ?? 0 }
})
const aiEngineLabel = computed(() => {
  if (!isFaeAnalysis.value) return 'Skills 智能分析'
  const engine = aiAnalysis.value?.engine || {}
  return `${engine.code || 'FAE'} v${engine.version || aiContent.value?.engine_version || '2.0'}`
})
const aiFooterLabel = computed(() => {
  if (!isFaeAnalysis.value) return aiAnalysis.value?.model || '火山方舟'
  return aiAnalysis.value?.model ? `FAE Core + ${aiAnalysis.value.model}` : 'FAE Core'
})
const aiModuleNames = computed(() => {
  if (isFaeAnalysis.value) {
    const labels = {
      'data-layer': '数据层',
      'market-classifier': '盘口分类',
      'scoring-engine': '八维评分',
      'probability-engine': '概率模型',
      'recommendation-engine': '推荐引擎',
      'risk-control': '风险控制',
      'review-learning': '复盘学习',
      'version-control': '版本管理'
    }
    return (aiContent.value?.modules || []).map(item => labels[item] || item)
  }
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
  const version = ++fetchVersion
  loading.value = true
  error.value = ''
  match.value = null
  prediction.value = null
  aiAnalysis.value = null
  dailyAiAnalysis.value = null
  preview.value = {}
  faeReview.value = null
  aiConfigured.value = false
  try {
    const id = String(route.params.id)
    const matchRes = await fetch(`/api/match/${id}`)
    const matchData = await matchRes.json()
    if (!matchRes.ok || !matchData.success) throw new Error(matchData.message || '比赛加载失败')
    if (version !== fetchVersion) return
    match.value = matchData.data
    loading.value = false
    void fetchLocalDetails(id, version)
    void fetchRemotePreview(id, version)
  } catch (e) {
    if (version !== fetchVersion) return
    error.value = e.message || '加载失败'
  } finally {
    if (version === fetchVersion) loading.value = false
  }
}

async function fetchJson(url) {
  const response = await fetch(url)
  const data = await response.json().catch(() => ({}))
  return { response, data }
}

async function fetchLocalDetails(id, version) {
  const results = await Promise.allSettled([
    fetchJson(`/api/predictions?match_id=${encodeURIComponent(id)}&limit=1`),
    fetchJson(`/api/match/${id}/ai-analysis`),
    fetchJson(`/api/fae/daily-ai/match/${id}`),
    fetchJson(`/api/match/${id}/fae-review`)
  ])
  if (version !== fetchVersion) return
  const fulfilled = index => (
    results[index].status === 'fulfilled' ? results[index].value : null
  )
  const pred = fulfilled(0)
  const core = fulfilled(1)
  const daily = fulfilled(2)
  const review = fulfilled(3)
  if (pred?.response.ok) {
    prediction.value = (pred.data.data || [])[0] || null
  }
  if (core?.response.ok) {
    aiAnalysis.value = core.data.data || null
    aiConfigured.value = !!core.data.configured
  }
  if (daily?.response.ok) {
    dailyAiAnalysis.value = daily.data.data || null
  }
  if (review?.response.ok) {
    faeReview.value = review.data.data || null
  }
}

async function fetchRemotePreview(id, version) {
  previewLoading.value = true
  try {
    const { response, data } = await fetchJson(`/api/match/${id}/500-analysis`)
    if (version === fetchVersion && response.ok) {
      preview.value = data.data || {}
    }
  } catch {
    // 远程资料失败不影响比赛、赔率和本地 AI 数据展示。
  } finally {
    if (version === fetchVersion) previewLoading.value = false
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
      body: JSON.stringify({ force, narrative: true })
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
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
      })
}
const starText = value => {
  const rating = Math.max(0, Math.min(5, Number(value) || 0))
  const stars = Math.floor(rating)
  const text = '★'.repeat(stars) + '☆'.repeat(5 - stars)
  return Number.isInteger(rating) ? text : `${text} · ${rating}星`
}
const radarTierWeight = tier => ({ core: 2, watch: 1 }[tier] || 0)
const radarTierLabel = tier => ({ core: '核心', watch: '观察' }[tier] || '观察')
const percentText = value => {
  const number = Number(value)
  return Number.isFinite(number) ? `${Number.isInteger(number) ? number : number.toFixed(1)}%` : '--'
}
const signedMetric = value => {
  const number = Number(value || 0)
  return `${number > 0 ? '+' : ''}${number}`
}
const triplet = values => Array.isArray(values)
  ? values.map(value => value ?? '--').join(' / ')
  : '--'
const totalTriplet = values => {
  if (!Array.isArray(values)) return '--'
  const normalized = [...values]
  if (normalized.length > 1) normalized[1] = formatTotalLine(normalized[1])
  return triplet(normalized)
}
const formatTotalLine = value => {
  const raw = String(value ?? '').replace(/[↑↓升降]/g, '').trim()
  if (!raw) return value ?? '--'
  const slashParts = raw.split('/').map(item => Number(item.trim()))
  if (slashParts.length > 1 && slashParts.every(Number.isFinite)) {
    return Number((slashParts.reduce((sum, item) => sum + item, 0) / slashParts.length).toFixed(2))
  }
  const lowHigh = raw.match(/^([1-4])([1-4]\.5)$/)
  if (lowHigh) return Number(((Number(lowHigh[1]) + Number(lowHigh[2])) / 2).toFixed(2))
  const highLow = raw.match(/^([1-4]\.5)([1-4])$/)
  if (highLow) return Number(((Number(highLow[1]) + Number(highLow[2])) / 2).toFixed(2))
  return value ?? '--'
}
const signedHandicap = value => {
  const number = Number(value)
  if (!Number.isFinite(number)) return '让球'
  return number > 0 ? `+${number}` : String(number)
}
const clean = value => value == null || value === '' ? '-' : String(value).replace(/[↑↓升降]+$/, '')
const numeric = value => Number(String(value ?? '').replace(/[^\d.+-]/g, ''))
const trendIcon = item => Number.isFinite(numeric(item.current)) && Number.isFinite(numeric(item.initial)) ? numeric(item.current) > numeric(item.initial) ? '↑' : numeric(item.current) < numeric(item.initial) ? '↓' : '' : ''
const trend = item => trendIcon(item) === '↑' ? 'up' : trendIcon(item) === '↓' ? 'down' : ''
const isCurrentTeam = name => String(name || '').includes(String(recentTeam.value === 'home' ? match.value?.home_team : match.value?.away_team).slice(0, 2))
const resultText = game => game.result || (game.teamMatchResult === 'draw' || game.winningTeam === 'draw' ? '平' : game.teamMatchResult === 'home' || game.winningTeam === 'home' ? '胜' : '负')
const resultClass = game => ({ '胜': 'win', '平': 'draw', '负': 'loss' }[resultText(game)])
const goBack = () => {
  if (window.history.state?.back) {
    router.back()
    return
  }
  router.replace(
    route.query.from === 'recommendations'
      ? { name: 'recommendations' }
      : { name: 'home' }
  )
}

watch(() => route.params.id, fetchAll)
onMounted(fetchAll)
</script>

<style scoped>
.live-detail{min-height:100vh;background:#f3f3f3;color:#333;padding-bottom:30px}.detail-headerbar{height:46px;display:grid;grid-template-columns:42px 1fr 42px;align-items:center;background:#fff;border-bottom:1px solid #e8e8e8;position:sticky;top:0;z-index:30}.detail-headerbar h1{text-align:center;font-size:17px;font-weight:500}.detail-headerbar button{border:0;background:none;color:#555;font-size:22px;text-align:center}.detail-headerbar button.detail-back{font-size:30px}.rotating{animation:rotate 1s linear infinite}@keyframes rotate{to{transform:rotate(360deg)}}.match-panel{background:#fff;padding:10px 12px 16px}.match-meta{text-align:center;color:#999;font-size:11px}.match-main{display:grid;grid-template-columns:1fr 86px 1fr;align-items:center;margin-top:14px}.team-side{display:flex;align-items:center;flex-direction:column;min-width:0}.team-side strong{margin-top:7px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px}.team-side small{margin-top:3px;color:#aaa;font-size:10px}.team-logo{width:46px;height:46px;display:grid;place-items:center;border-radius:50%;color:#fff;font-weight:700;border:3px solid #eef2f6}.team-logo.home{background:#5083b9}.team-logo.away{background:#d28a45}.score-side{text-align:center}.score-side strong{display:block;font-size:26px;color:#222}.score-side span{display:inline-block;margin-top:5px;padding:2px 8px;border-radius:10px;background:#f3f3f3;color:#999;font-size:10px}.main-tabs{height:45px;display:grid;grid-template-columns:repeat(3,1fr);background:#fff;border-top:1px solid #eee;border-bottom:1px solid #ddd;position:sticky;top:46px;z-index:20}.main-tabs button{position:relative;border:0;background:none;color:#666;font-size:14px}.main-tabs button.active{color:#e64b3c;font-weight:600}.main-tabs button.active:after{content:"";position:absolute;bottom:0;left:34%;right:34%;height:2px;background:#e64b3c}.tab-page{padding-top:8px}.data-section{background:#fff;margin-bottom:8px}.data-section>header{height:42px;display:flex;align-items:center;border-bottom:1px solid #eee;padding:0 12px}.data-section>header i{width:3px;height:15px;background:#e64b3c;margin-right:7px}.data-section h2{font-size:14px;font-weight:600}.section-body{padding-bottom:10px}.ai-analysis-card{padding:12px}.ai-analysis-head{display:flex;align-items:center;gap:8px}.ai-analysis-head .ai-label{padding:3px 7px;border-radius:4px;background:#2d3142;color:#fff;font-size:10px}.ai-analysis-head strong{flex:1;color:#e64b3c;font-size:18px}.ai-analysis-head b{font-size:20px;color:#e64b3c}.ai-summary{margin-top:12px;line-height:1.7;font-size:13px;color:#555}.ai-markets{display:grid;grid-template-columns:repeat(3,1fr);margin-top:12px;padding:10px 0;border:1px solid #f1eeee;border-radius:7px;background:#fffafa;text-align:center}.ai-markets div+div{border-left:1px solid #eee}.ai-markets span,.ai-markets strong{display:block}.ai-markets span{color:#999;font-size:10px}.ai-markets strong{margin-top:5px;font-size:12px;color:#444}.ai-reasons,.ai-risks{margin-top:14px}.ai-reasons h3,.ai-risks h3{margin-bottom:7px;font-size:12px}.ai-reasons p,.ai-risks p{display:flex;align-items:flex-start;gap:7px;margin-top:6px;line-height:1.5;font-size:12px;color:#555}.ai-reasons i{flex:0 0 18px;height:18px;display:grid;place-items:center;border-radius:50%;background:#fbe5e3;color:#e64b3c;font-style:normal;font-size:10px}.ai-risks{padding:9px;border-radius:6px;background:#fff7e8}.ai-risks p{color:#8c651d}.ai-skills{display:flex;flex-wrap:wrap;gap:5px;margin-top:12px}.ai-skills span{padding:3px 6px;border-radius:3px;background:#f2f3f5;color:#777;font-size:9px}.ai-analysis-foot{display:flex;justify-content:space-between;align-items:center;margin-top:14px}.ai-analysis-foot small{color:#aaa;font-size:9px}.ai-analysis-foot button,.ai-empty button{border:0;border-radius:16px;padding:7px 13px;background:#e64b3c;color:#fff;font-size:11px}.ai-analysis-foot button:disabled,.ai-empty button:disabled{background:#ccc}.ai-disclaimer{margin-top:10px;color:#aaa;font-size:9px;text-align:center}.ai-empty{text-align:center;padding:20px 0}.ai-empty p{margin-bottom:12px;color:#888;font-size:12px}.ai-error{margin-top:9px;color:#e64b3c;text-align:center;font-size:11px}.sub-tabs{display:grid;grid-template-columns:1fr 1fr;border-bottom:1px solid #eee}.sub-tabs button{height:38px;border:0;background:#fafafa;color:#777;font-size:12px}.sub-tabs button.active{background:#fff;color:#e64b3c;border-bottom:2px solid #e64b3c}.match-table{font-size:10px}.table-head,.table-row{display:grid;grid-template-columns:68px 1fr 44px 1fr 32px;align-items:center;text-align:center;min-height:39px;border-bottom:1px solid #f0f0f0}.table-head{min-height:30px;background:#fafafa;color:#aaa}.table-row>span:first-child b,.table-row>span:first-child small{display:block;font-weight:400}.table-row>span:first-child small{color:#aaa;margin-top:2px}.table-row strong{color:#e64b3c}.table-row .focus{color:#e64b3c}.table-row em{width:20px;height:20px;display:grid;place-items:center;margin:auto;border-radius:2px;color:#fff;font-style:normal}.table-row em.win{background:#e64b3c}.table-row em.draw{background:#aaa}.table-row em.loss{background:#4c86bd}.ranking-compare{display:grid;grid-template-columns:1fr 1px 1fr;align-items:center;text-align:center;padding:20px}.ranking-compare i{height:48px;background:#eee}.ranking-compare strong,.ranking-compare span,.ranking-compare small{display:block}.ranking-compare strong{font-size:28px;color:#e64b3c}.ranking-compare span{margin-top:4px;font-size:13px}.ranking-compare small{margin-top:3px;color:#aaa}.future-grid{display:grid;grid-template-columns:1fr 1fr}.future-grid>div{padding:10px}.future-grid>div+div{border-left:1px solid #eee}.future-grid h3{text-align:center;font-size:12px;margin-bottom:7px}.future-grid p{display:flex;gap:6px;padding:6px 0;border-top:1px solid #f2f2f2;font-size:10px}.future-grid p span{color:#999}.future-grid p b{font-weight:400}.odds-table{margin:10px;border:1px solid #eee}.odds-table>div{display:grid;grid-template-columns:55px repeat(3,1fr);align-items:center;min-height:38px;text-align:center;border-top:1px solid #eee;font-size:12px}.odds-table>div:first-child{border:0;background:#fafafa;color:#999}.odds-table b{font-weight:400;color:#888}.odds-table i{font-style:normal}.odds-table i.up{color:#e64b3c}.odds-table i.down{color:#139862}.info-list{padding:5px 12px}.info-list p{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #f2f2f2;font-size:12px}.info-list span{color:#999}.prediction{display:grid;grid-template-columns:repeat(3,1fr);padding:15px 0;text-align:center}.prediction div+div{border-left:1px solid #eee}.prediction span,.prediction strong,.prediction small{display:block}.prediction span,.prediction small{color:#999;font-size:10px}.prediction strong{margin:6px 0;color:#e64b3c;font-size:16px}.empty-data,.page-state{text-align:center;color:#aaa;padding:30px}.page-state{min-height:60vh;display:grid;place-items:center}.page-state.error button{border:0;background:#e64b3c;color:#fff;border-radius:20px;padding:8px 18px}@media(min-width:620px){.live-detail{max-width:600px;margin:auto;box-shadow:0 0 20px rgba(0,0,0,.08)}}

/* 与首页、计算器统一的详情页视觉 */
.live-detail {
  --detail-accent: #f33b48;
  --detail-accent-soft: #fff1f2;
  min-height: 100vh;
  padding-bottom: 32px;
  overflow-x: hidden;
  color: #2d3035;
  background: #f4f5f7;
}

.detail-headerbar {
  top: 0;
  height: 50px;
  grid-template-columns: 48px 1fr 48px;
  color: #fff;
  background: linear-gradient(90deg, #ff4b47 0%, #ff3333 100%);
  border-bottom: 0;
  box-shadow: 0 2px 8px rgb(210 31 45 / 16%);
}

.detail-headerbar h1 {
  font-size: 18px;
  font-weight: 500;
}

.detail-headerbar a,
.detail-headerbar button {
  width: 48px;
  height: 50px;
  color: #fff;
  font-size: 34px;
  line-height: 48px;
  text-decoration: none;
}

.detail-headerbar button {
  font-size: 24px;
  cursor: pointer;
}

.match-panel {
  margin: 12px 12px 0;
  padding: 13px 12px 17px;
  background: #fff;
  border-radius: 11px;
  box-shadow: 0 3px 12px rgb(30 38 50 / 5%);
}

.match-meta {
  color: #9a9ea5;
  font-size: 11px;
}

.match-main {
  margin-top: 16px;
}

.team-side strong {
  margin-top: 8px;
  color: #292c31;
  font-size: 14px;
}

.team-side small {
  color: #a7abb1;
}

.team-logo {
  width: 50px;
  height: 50px;
  border: 3px solid #fff;
  box-shadow: 0 0 0 3px #f0f2f5, 0 4px 10px rgb(30 40 55 / 9%);
}

.team-logo.home {
  background: linear-gradient(135deg, #5aa4d8, #377fb8);
}

.team-logo.away {
  background: linear-gradient(135deg, #f4a24c, #df7e22);
}

.score-side strong {
  color: #272a30;
  font-size: 27px;
  letter-spacing: -0.5px;
}

.score-side span {
  margin-top: 7px;
  padding: 3px 10px;
  color: #92969d;
  background: #f1f2f4;
  border-radius: 12px;
}

.main-tabs {
  top: 58px;
  z-index: 20;
  height: 44px;
  margin: 10px 12px 0;
  padding: 4px;
  background: #e9eaec;
  border: 0;
  border-radius: 10px;
  box-shadow: 0 2px 7px rgb(0 0 0 / 4%);
}

.main-tabs button {
  color: #747981;
  background: transparent;
  border-radius: 7px;
}

.main-tabs button.active {
  color: var(--detail-accent);
  background: #fff;
  box-shadow: 0 2px 7px rgb(0 0 0 / 7%);
}

.main-tabs button.active::after {
  display: none;
}

.tab-page {
  padding: 10px 12px 24px;
}

.data-section {
  margin-bottom: 10px;
  overflow: hidden;
  background: #fff;
  border: 1px solid #f0f0f2;
  border-radius: 11px;
  box-shadow: 0 3px 12px rgb(30 38 50 / 4%);
}

.live-detail :deep(.data-section > header) {
  height: 44px;
  padding: 0 14px;
  border-bottom: 1px solid #f0f1f3;
}

.live-detail :deep(.data-section > header > i) {
  width: 3px;
  height: 16px;
  margin-right: 8px;
  background: var(--detail-accent);
  border-radius: 0 3px 3px 0;
}

.live-detail :deep(.data-section > header h2) {
  margin: 0;
  color: #30343a;
  font-size: 15px;
  font-weight: 600;
}

.live-detail :deep(.data-section > .section-body) {
  padding-bottom: 0;
}

.ai-analysis-card {
  padding: 12px;
}

.daily-detail-card {
  padding: 14px;
}

.daily-detail-head,
.daily-detail-result,
.daily-detail-foot {
  display: flex;
  align-items: center;
}

.daily-detail-head {
  justify-content: space-between;
  color: #777d85;
  font-size: 13px;
}

.daily-detail-head > span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.daily-detail-head i {
  display: inline-grid;
  width: 28px;
  height: 24px;
  place-items: center;
  color: #fff;
  font-size: 10px;
  font-style: normal;
  font-weight: 700;
  background: linear-gradient(135deg, #ff5962, #ee2e42);
  border-radius: 7px;
}

.daily-detail-head small {
  color: #a1a5ab;
  font-size: 11px;
}

.daily-detail-result {
  justify-content: space-between;
  margin-top: 13px;
}

.daily-detail-result strong {
  color: var(--detail-accent);
  font-size: 22px;
}

.daily-detail-picks {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 13px;
}

.daily-detail-picks .daily-no-bet {
  color: #fff;
  padding: 4px 7px;
  font-size: 14px;
  background: #515866;
  border-radius: 6px;
}

.daily-detail-picks .daily-radar-main {
  color: #fff;
  padding: 4px 8px;
  font-size: 15px;
  background: linear-gradient(135deg, #ff5962, #ee2e42);
  border-radius: 6px;
}

.daily-detail-picks .daily-radar-main small {
  color: #ffe5e9;
}

.daily-detail-picks .daily-no-bet small {
  color: #d9dce1;
}

.daily-detail-picks strong,
.daily-detail-picks span {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
}

.daily-detail-picks span {
  color: #777d85;
  font-size: 14px;
  font-weight: 600;
}

.daily-detail-picks small {
  color: #a1a5ab;
  font-size: 10px;
  font-weight: 500;
}

.fae-probability-note {
  margin: 9px 0 0;
  color: #999;
  font-size: 10px;
  line-height: 1.55;
}

.daily-detail-result b {
  color: #ef9b20;
  font-size: 15px;
  letter-spacing: 1px;
}

.daily-detail-guard {
  display: flex;
  gap: 8px;
  margin-top: 10px;
  padding: 9px 10px;
  color: #865c25;
  font-size: 12px;
  line-height: 1.55;
  background: #fff7e8;
  border: 1px solid #f0dfbd;
  border-radius: 8px;
}

.daily-detail-guard strong {
  flex: 0 0 auto;
  color: #c36d14;
}

.daily-detail-verdict {
  margin-top: 11px;
  color: #565c64;
  font-size: 14px;
  line-height: 1.85;
  text-align: justify;
}

.daily-radar-detail {
  margin-top: 11px;
  padding: 10px;
  background: #fff8f9;
  border: 1px solid #f1e1e5;
  border-radius: 8px;
}

.daily-radar-detail header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.daily-radar-detail header strong {
  color: var(--detail-accent);
  font-size: 13px;
}

.daily-radar-detail header span {
  color: #a0a5ad;
  font-size: 10px;
}

.daily-radar-detail p {
  margin: 0;
  padding: 8px 0;
  border-top: 1px dashed #ead8dd;
}

.daily-radar-detail p:first-of-type {
  border-top: 0;
}

.daily-radar-detail p > span {
  display: flex;
  align-items: center;
  gap: 7px;
}

.daily-radar-detail p b {
  color: #333941;
  font-size: 14px;
}

.daily-radar-detail p i {
  padding: 2px 5px;
  color: #8c641e;
  font-size: 10px;
  font-style: normal;
  background: #fff4d8;
  border-radius: 4px;
}

.daily-radar-detail p.core i {
  color: #fff;
  background: var(--detail-accent);
}

.daily-radar-detail p strong {
  margin-left: auto;
  color: var(--detail-accent);
  font-size: 13px;
}

.daily-radar-detail small,
.daily-radar-detail em {
  display: block;
  margin-top: 5px;
  color: #777d85;
  font-size: 11px;
  line-height: 1.55;
  font-style: normal;
}

.daily-radar-detail em {
  color: #5f666f;
}

.daily-detail-odds {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
  margin-top: 12px;
  padding: 9px;
  background: #f7f8fa;
  border-radius: 8px;
}

.daily-detail-odds p,
.daily-detail-markets p {
  min-width: 0;
  margin: 0;
}

.daily-detail-odds span,
.daily-detail-odds b {
  display: block;
}

.daily-detail-odds span {
  color: #969ba3;
  font-size: 11px;
}

.daily-detail-odds b {
  margin-top: 3px;
  overflow: hidden;
  color: #454a51;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.daily-detail-values {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 7px;
  margin-top: 9px;
}

.daily-detail-values p {
  margin: 0;
  padding: 8px 5px;
  text-align: center;
  background: #fff8f9;
  border: 1px solid #f1e4e7;
  border-radius: 8px;
}

.daily-detail-values span,
.daily-detail-values b {
  display: block;
}

.daily-detail-values span {
  color: #999ea5;
  font-size: 10px;
}

.daily-detail-values b {
  margin-top: 3px;
  color: #444a52;
  font-size: 13px;
}

.daily-detail-values b.danger {
  color: #e53955;
}

.daily-detail-markets {
  display: grid;
  gap: 7px;
  margin-top: 10px;
}

.daily-detail-markets p {
  padding: 10px;
  background: #fff;
  border: 1px solid #eceef1;
  border-radius: 8px;
}

.daily-detail-markets span,
.daily-detail-markets b {
  display: block;
}

.daily-detail-markets span {
  color: var(--detail-accent);
  font-size: 12px;
  font-weight: 600;
}

.daily-detail-markets b {
  margin-top: 4px;
  color: #626870;
  font-size: 13px;
  font-weight: 400;
  line-height: 1.7;
}

.daily-detail-foot {
  gap: 8px;
  margin-top: 13px;
  padding-top: 11px;
  border-top: 1px dashed #e5e7ea;
}

.daily-detail-foot span {
  color: #969ba3;
  font-size: 12px;
}

.daily-detail-foot b {
  flex: 1;
  color: var(--detail-accent);
  font-size: 14px;
}

.daily-detail-foot small {
  color: #a5a9af;
  font-size: 10px;
}

.ai-analysis-hero {
  padding: 14px;
  background:
    radial-gradient(circle at 100% 0, rgb(243 59 72 / 9%), transparent 42%),
    linear-gradient(145deg, #fff8f8, #fff);
  border: 1px solid #f8dfe2;
  border-radius: 10px;
}

.ai-analysis-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.ai-analysis-head .ai-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  color: #747982;
  font-size: 11px;
  font-weight: 500;
  background: transparent;
}

.ai-analysis-head .ai-label i {
  display: inline-grid;
  min-width: 28px;
  height: 24px;
  padding: 0 5px;
  place-items: center;
  color: #fff;
  font-size: 9px;
  font-style: normal;
  font-weight: 700;
  background: linear-gradient(135deg, #ff5962, #ee2e42);
  border-radius: 7px;
  box-shadow: 0 4px 10px rgb(238 46 66 / 17%);
}

.ai-confidence {
  padding: 4px 8px;
  color: #dc3442;
  font-size: 10px;
  background: #fff;
  border: 1px solid #f6d6d9;
  border-radius: 12px;
}

.ai-confidence b {
  margin-right: 1px;
  color: var(--detail-accent);
  font-size: 15px;
  font-weight: 700;
}

.ai-confidence.fae-danger-score {
  color: #a96713;
  background: #fff8eb;
  border-color: #f0d6a6;
}

.fae-recommendation-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.fae-recommendation-meta > span {
  padding: 3px 7px;
  color: #737880;
  font-size: 10px;
  background: #fff;
  border: 1px solid #eee2e3;
  border-radius: 5px;
}

.fae-recommendation-meta .fae-stars {
  color: #ef9b20;
  letter-spacing: 1px;
}

.fae-recommendation-meta .risk-中,
.fae-recommendation-meta .risk-高 {
  color: #aa6e12;
  background: #fff8e9;
  border-color: #f4ddb1;
}

.fae-recommendation-meta .risk-高 {
  color: #d73543;
  background: #fff1f2;
  border-color: #f1c4c8;
}

.ai-result {
  display: block;
  margin-top: 12px;
  color: #e93443;
  font-size: 19px;
  line-height: 1.45;
}

.ai-summary {
  margin-top: 8px;
  color: #5c6169;
  font-size: 13px;
  line-height: 1.85;
  text-align: justify;
}

.fae-market-types {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.fae-market-types span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 8px 5px 5px;
  color: #6d727a;
  font-size: 10px;
  background: #f5f6f8;
  border: 1px solid #eceef1;
  border-radius: 6px;
}

.fae-market-types b {
  display: inline-grid;
  width: 20px;
  height: 20px;
  place-items: center;
  color: #fff;
  font-size: 10px;
  background: linear-gradient(135deg, #ff5962, #ee2e42);
  border-radius: 5px;
}

.fae-score-panel,
.fae-probability-panel {
  margin-top: 10px;
  padding: 11px;
  background: #f7f8fa;
  border: 1px solid #eef0f2;
  border-radius: 9px;
}

.fae-panel-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 9px;
  color: #444950;
  font-size: 12px;
  font-weight: 600;
}

.fae-panel-title b {
  color: var(--detail-accent);
  font-size: 15px;
}

.fae-panel-title small {
  color: #a0a4aa;
  font-size: 9px;
  font-weight: 400;
}

.fae-score-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
}

.fae-score-card {
  min-width: 0;
  padding: 8px;
  background: #fff;
  border: 1px solid #ebedf0;
  border-radius: 7px;
}

.fae-score-card.missing {
  background: #fafafa;
}

.fae-score-card > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.fae-score-card span {
  color: #555a62;
  font-size: 11px;
  font-weight: 600;
}

.fae-score-card b {
  color: var(--detail-accent);
  font-size: 13px;
}

.fae-score-card em {
  display: block;
  margin-top: 4px;
  color: #efa126;
  font-size: 10px;
  font-style: normal;
  letter-spacing: .5px;
}

.fae-score-card small {
  display: block;
  margin-top: 4px;
  overflow: hidden;
  color: #999da4;
  font-size: 9px;
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fae-score-card.missing b,
.fae-score-card.missing em {
  color: #afb2b7;
}

.fae-probability-row {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) 34px;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  color: #696e76;
  font-size: 10px;
}

.fae-probability-row > i {
  height: 7px;
  overflow: hidden;
  background: #e7e9ed;
  border-radius: 4px;
}

.fae-probability-row > i b {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #ff6972, #ef3546);
  border-radius: 4px;
}

.fae-probability-row strong {
  color: #454a51;
  font-size: 10px;
  text-align: right;
}

.fae-margin-probabilities {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
  margin-top: 11px;
  padding-top: 9px;
  border-top: 1px dashed #dfe2e6;
}

.fae-margin-probabilities span {
  min-width: 0;
  padding: 6px 7px;
  color: #878c93;
  font-size: 9px;
  background: #fff;
  border-radius: 5px;
}

.fae-margin-probabilities b {
  float: right;
  color: #e83a49;
}

.ai-markets {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
  margin-top: 10px;
  padding: 0;
  text-align: left;
  background: transparent;
  border: 0;
}

.ai-markets div + div {
  border-left: 0;
}

.ai-market-item {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  align-items: start;
  gap: 8px;
  padding: 10px;
  background: #f7f8fa;
  border: 1px solid #f0f1f3;
  border-radius: 8px;
}

.ai-markets .ai-market-item > span {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #898e96;
  font-size: 11px;
  line-height: 22px;
}

.ai-market-item > span i {
  display: inline-grid;
  width: 22px;
  height: 22px;
  place-items: center;
  color: var(--detail-accent);
  font-size: 10px;
  font-style: normal;
  background: var(--detail-accent-soft);
  border-radius: 6px;
}

.ai-markets .ai-market-item > strong {
  margin-top: 1px;
  color: #3d4249;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.65;
}

.ai-score-item strong {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.ai-score-item em {
  min-width: 44px;
  padding: 2px 8px;
  color: #e83b49;
  font-style: normal;
  text-align: center;
  background: #fff;
  border: 1px solid #f3d7da;
  border-radius: 5px;
}

.ai-reasons,
.ai-risks {
  margin-top: 16px;
}

.ai-reasons h3,
.ai-risks h3 {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 10px;
  color: #333840;
  font-size: 14px;
}

.ai-reasons h3 > i {
  display: block;
  flex: 0 0 3px;
  width: 3px;
  height: 14px;
  background: var(--detail-accent);
  border-radius: 2px;
}

.ai-reasons p {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  gap: 9px;
  margin-top: 9px;
  color: #5e636b;
  font-size: 12px;
  line-height: 1.7;
}

.ai-reasons p > i {
  width: 22px;
  height: 22px;
  background: var(--detail-accent-soft);
  color: var(--detail-accent);
  font-size: 10px;
}

.ai-reasons p > span {
  min-width: 0;
}

.ai-risks {
  padding: 12px;
  background: #fff8eb;
  border: 1px solid #f8e8c5;
  border-radius: 8px;
}

.ai-risks h3 {
  margin-bottom: 7px;
  color: #8b641b;
}

.ai-risks h3 > i {
  display: inline-grid;
  width: 18px;
  height: 18px;
  place-items: center;
  color: #fff;
  font-size: 11px;
  font-style: normal;
  background: #d99a26;
  border-radius: 50%;
}

.ai-risks p {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr);
  gap: 5px;
  margin-top: 6px;
  color: #87672c;
  font-size: 12px;
  line-height: 1.65;
}

.ai-skills {
  gap: 6px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed #e8e9eb;
}

.ai-skills span {
  padding: 4px 7px;
  color: #777d85;
  font-size: 9px;
  background: #f2f3f5;
  border-radius: 4px;
}

.ai-analysis-foot {
  gap: 10px;
  margin-top: 14px;
}

.ai-analysis-foot small {
  min-width: 0;
  color: #a1a5ab;
  font-size: 9px;
  line-height: 1.5;
}

.ai-analysis-foot button,
.ai-empty button {
  flex: 0 0 auto;
  padding: 7px 13px;
  color: var(--detail-accent);
  background: #fff;
  border: 1px solid #f0aeb4;
  border-radius: 7px;
  cursor: pointer;
}

.ai-analysis-foot button:disabled,
.ai-empty button:disabled {
  color: #aaa;
  background: #f2f3f5;
  border-color: #e4e5e7;
}

.ai-disclaimer {
  margin-top: 10px;
  color: #a7abb1;
  font-size: 9px;
  line-height: 1.5;
}

.fae-review-card {
  padding: 12px;
}

.fae-review-result {
  display: grid;
  grid-template-columns: 1fr 1px 1fr 44px;
  align-items: center;
  gap: 10px;
  padding: 12px;
  background: #f7f8fa;
  border-radius: 8px;
}

.fae-review-result > div {
  text-align: center;
}

.fae-review-result span,
.fae-review-result strong {
  display: block;
}

.fae-review-result span {
  color: #999da4;
  font-size: 10px;
}

.fae-review-result strong {
  margin-top: 4px;
  color: #343940;
  font-size: 16px;
}

.fae-review-result > i {
  height: 35px;
  background: #e1e3e6;
}

.fae-review-result > em {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  color: #fff;
  font-size: 21px;
  font-style: normal;
  font-weight: 700;
  background: #9ca1a8;
  border-radius: 50%;
}

.fae-review-result > em.hit {
  background: #20a36a;
}

.fae-review-result > em.miss {
  background: #ef3a49;
}

.fae-review-list,
.fae-learning-updates {
  margin-top: 13px;
  padding: 11px;
  border-radius: 8px;
}

.fae-review-list.wrong {
  background: #fff4f5;
}

.fae-review-list.worked {
  background: #f1faf5;
}

.fae-review-list h3,
.fae-learning-updates h3 {
  margin-bottom: 7px;
  color: #454a51;
  font-size: 12px;
}

.fae-review-list p {
  display: grid;
  grid-template-columns: 12px minmax(0, 1fr);
  gap: 5px;
  margin-top: 5px;
  color: #d9414d;
  font-size: 11px;
  line-height: 1.55;
}

.fae-review-list.worked p {
  color: #18875a;
}

.fae-review-list p span {
  color: #646971;
}

.fae-learning-updates {
  background: #f5f6f8;
}

.fae-learning-updates p {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 0;
  color: #777c84;
  font-size: 10px;
  border-top: 1px solid #e7e9ec;
}

.fae-learning-updates b {
  color: #e83a49;
}

.ai-empty {
  padding: 26px 14px;
}

.sub-tabs {
  padding: 4px;
  background: #f1f2f4;
  border-bottom: 0;
}

.sub-tabs button {
  background: transparent;
  border-radius: 6px;
}

.sub-tabs button.active {
  background: #fff;
  border-bottom: 0;
  box-shadow: 0 1px 4px rgb(0 0 0 / 7%);
}

.table-head {
  background: #f7f8fa;
}

.odds-table {
  overflow: hidden;
  border-radius: 8px;
}

@media (min-width: 480px) {
  .ai-markets {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .ai-market-item {
    grid-template-columns: 1fr;
  }

  .ai-score-item {
    grid-column: 1 / -1;
    grid-template-columns: 88px minmax(0, 1fr);
  }

  .fae-score-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  .fae-margin-probabilities {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (min-width: 620px) {
  .live-detail {
    max-width: 600px;
    margin: auto;
    box-shadow: 0 0 24px rgb(0 0 0 / 8%);
  }
}
</style>

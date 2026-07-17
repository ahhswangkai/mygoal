<template>
  <div class="app-container calculator-page">
    <header class="top-header">
      <span class="header-side-spacer" aria-hidden="true"></span>
      <span class="header-title">足球计算器</span>
      <AccountButton />
    </header>

    <div class="play-tabs">
      <div
        v-for="(tab, idx) in tabs"
        :key="idx"
        class="play-tab"
        :class="{ active: currentTab === idx }"
        @click="currentTab = idx"
      >
        <template v-if="typeof tab === 'string'">{{ tab }}</template>
        <template v-else>
          <span class="tab-line">{{ tab[0] }}</span>
          <span class="tab-line">{{ tab[1] }}</span>
        </template>
      </div>
    </div>

    <div class="match-list">
      <div v-for="(group, gIdx) in groupedMatches" :key="gIdx">
        <div class="date-header">
          <div class="date-info">
            <template v-if="currentTab === 0 || currentTab === 1 || currentTab === 2 || currentTab === 3 || currentTab === 4">
              <span class="date-title">{{ formatGroupTitle(group) }}</span>
            </template>
            <template v-else>
              <span>{{ group.dateText }}</span>
              <span class="match-count">共 {{ group.matches.length }} 场比赛</span>
            </template>
          </div>
          <div class="hide-btn" :class="{ 'hide-btn-active': group.hidden }" @click="toggleDateGroup(group)">
            <span class="hide-dot"></span>
            <span>{{ group.hidden ? '显示' : '隐藏' }}</span>
          </div>
        </div>

        <div v-if="!group.hidden">
          <div
            v-for="match in group.matches"
            :key="match.id"
            class="match-card"
            :class="{
              'had-match-card': currentTab === 0,
              'score-match-card': currentTab === 1,
              'goals-match-card': currentTab === 2,
              'hafu-match-card': currentTab === 3,
              'mixed-match-card': currentTab === 4
            }"
          >
            <template v-if="currentTab === 0 || currentTab === 4">
              <div class="match-left">
                <div class="match-num">{{ match.num }}</div>
                <div class="match-time">{{ match.shortDate }} {{ match.shortTime }}</div>
              </div>
            </template>

            <div class="match-right">
              <div class="match-teams" @click="currentTab !== 4 && toggleMatch(match.id)">
                <template v-if="currentTab !== 0 && currentTab !== 4">
                  <div class="match-left-inline">
                    <span class="match-num">{{ match.num }}</span>
                    <span class="match-time">{{ match.shortDate }} {{ match.shortTime }}</span>
                  </div>
                </template>
                <div class="league-name">{{ match.league }}</div>
                <div class="teams-row">
                  <span class="home-team">{{ match.homeTeam }}</span>
                  <span class="vs-text">VS</span>
                  <span class="away-team">{{ match.awayTeam }}</span>
                </div>
                <div v-if="currentTab !== 0 && currentTab !== 4" class="collapse-arrow" :class="{ collapsed: isCollapsed(match.id) }">▼</div>
              </div>

              <div v-if="currentTab === 4 || !isCollapsed(match.id)" class="match-odds-area">
                <template v-if="currentTab === 0">
                  <div class="odds-row had-row">
                    <div class="goal-line">
                      <span class="single-tag" v-if="match.hadSingle">单</span>
                      <span class="line-text">[0]</span>
                    </div>
                    <div
                      v-for="(odd, opt) in getOdds(match.had)"
                      :key="'had-'+opt"
                      class="mix-btn"
                      :class="{ selected: isSel(match.id, 'had', opt) }"
                      @click="toggleOdds(match, 'had', opt, odd, labelHad(opt))"
                    >
                      <div class="mix-text">{{ labelHad(opt) }}</div>
                      <div class="mix-odds">{{ odd }}</div>
                      <span class="mix-arrow" :class="flagClass(match.had, opt)">{{ flagArrow(match.had, opt) }}</span>
                    </div>
                  </div>
                  <div class="odds-row had-row">
                    <div class="goal-line">
                      <span class="single-tag" v-if="match.hhadSingle">单</span>
                      <span class="line-text">[{{ formatHandicap(match.handicap) }}]</span>
                    </div>
                    <div
                      v-for="(odd, opt) in getOdds(match.hhad)"
                      :key="'hhad-'+opt"
                      class="mix-btn"
                      :class="{ selected: isSel(match.id, 'hhad', opt) }"
                      @click="toggleOdds(match, 'hhad', opt, odd, labelHhad(opt))"
                    >
                      <div class="mix-text">{{ labelHhad(opt) }}</div>
                      <div class="mix-odds">{{ odd }}</div>
                      <span class="mix-arrow" :class="flagClass(match.hhad, opt)">{{ flagArrow(match.hhad, opt) }}</span>
                    </div>
                  </div>
                </template>

                <template v-else-if="currentTab === 4">
                  <div class="mixed-base-odds">
                    <div class="odds-row mixed-base-row">
                      <div class="goal-line">
                        <span class="single-tag" v-if="match.hadSingle">单</span>
                        <span class="line-text">[0]</span>
                      </div>
                      <div
                        v-for="(odd, opt) in getOdds(match.had)"
                        :key="'mix-had-'+opt"
                        class="mix-btn"
                        :class="{ selected: isSel(match.id, 'had', opt) }"
                        @click="toggleOdds(match, 'had', opt, odd, labelHad(opt))"
                      >
                        <div class="mix-text">{{ labelHad(opt) }}</div>
                        <div class="mix-odds">{{ odd }}</div>
                        <span class="mix-arrow" :class="flagClass(match.had, opt)">{{ flagArrow(match.had, opt) }}</span>
                      </div>
                    </div>
                    <div class="odds-row mixed-base-row">
                      <div class="goal-line">
                        <span class="single-tag" v-if="match.hhadSingle">单</span>
                        <span class="line-text">[{{ formatHandicap(match.handicap) }}]</span>
                      </div>
                      <div
                        v-for="(odd, opt) in getOdds(match.hhad)"
                        :key="'mix-hhad-'+opt"
                        class="mix-btn"
                        :class="{ selected: isSel(match.id, 'hhad', opt) }"
                        @click="toggleOdds(match, 'hhad', opt, odd, labelHhad(opt))"
                      >
                        <div class="mix-text">{{ labelHhad(opt) }}</div>
                        <div class="mix-odds">{{ odd }}</div>
                        <span class="mix-arrow" :class="flagClass(match.hhad, opt)">{{ flagArrow(match.hhad, opt) }}</span>
                      </div>
                    </div>
                  </div>

                  <button
                    type="button"
                    class="mixed-more-toggle"
                    :aria-expanded="isMixedExpanded(match.id)"
                    @click="toggleMixedMore(match.id)"
                  >
                    <span>{{ isMixedExpanded(match.id) ? '收起' : '更多游戏' }}</span>
                    <span class="mixed-more-arrow" :class="{ expanded: isMixedExpanded(match.id) }">▼</span>
                  </button>

                  <div v-if="isMixedExpanded(match.id)" class="mixed-extra-games">
                    <section class="mixed-extra-section">
                      <h3 class="mixed-extra-title">半全场胜平负</h3>
                      <div class="mixed-hafu-grid">
                        <div
                          v-for="hafu in hafuOptions"
                          :key="'mix-hafu-'+hafu"
                          class="mixed-option-btn"
                          :class="{ selected: isSel(match.id, 'hafu', hafu), disabled: !match.hafu[hafu] }"
                          @click="match.hafu[hafu] && toggleOdds(match, 'hafu', hafu, match.hafu[hafu], hafu)"
                        >
                          <div class="mixed-option-text">{{ hafu }}</div>
                          <div class="mixed-option-odds">{{ match.hafu[hafu] }}</div>
                        </div>
                      </div>
                    </section>

                    <section class="mixed-extra-section">
                      <h3 class="mixed-extra-title">总进球</h3>
                      <div class="mixed-goals-grid">
                      <div
                        v-for="goal in goalNums"
                        :key="'mix-goal-'+goal"
                          class="mixed-option-btn"
                        :class="{ selected: isSel(match.id, 'goals', goal), disabled: !match.goals[goal] }"
                        @click="match.goals[goal] && toggleOdds(match, 'goals', goal, match.goals[goal], goal + '球')"
                      >
                          <div class="mixed-option-text">{{ goal }}</div>
                          <div class="mixed-option-odds">{{ match.goals[goal] || '-' }}</div>
                      </div>
                    </div>
                    </section>

                    <section class="mixed-extra-section mixed-score-section">
                      <h3 class="mixed-extra-title">比分</h3>
                      <div class="mixed-score-board">
                        <div class="mixed-score-grid">
                          <div
                            v-for="(odd, score) in homeScores(match.score)"
                            :key="'mix-score-h-'+score"
                            class="mixed-option-btn"
                            :class="{ selected: isSel(match.id, 'score', score) }"
                            @click="toggleOdds(match, 'score', score, odd, score)"
                          >
                            <div class="mixed-option-text">{{ score }}</div>
                            <div class="mixed-option-odds">{{ odd }}</div>
                          </div>
                        </div>
                        <div class="mixed-score-grid">
                          <div
                            v-for="(odd, score) in drawScores(match.score)"
                            :key="'mix-score-d-'+score"
                            class="mixed-option-btn"
                            :class="{ selected: isSel(match.id, 'score', score) }"
                            @click="toggleOdds(match, 'score', score, odd, score)"
                          >
                            <div class="mixed-option-text">{{ score }}</div>
                            <div class="mixed-option-odds">{{ odd }}</div>
                          </div>
                        </div>
                        <div class="mixed-score-grid">
                          <div
                            v-for="(odd, score) in awayScores(match.score)"
                            :key="'mix-score-a-'+score"
                            class="mixed-option-btn"
                            :class="{ selected: isSel(match.id, 'score', score) }"
                            @click="toggleOdds(match, 'score', score, odd, score)"
                          >
                            <div class="mixed-option-text">{{ score }}</div>
                            <div class="mixed-option-odds">{{ odd }}</div>
                          </div>
                        </div>
                      </div>
                    </section>
                  </div>
                </template>

                <template v-else-if="currentTab === 1">
                  <div class="score-board">
                    <div class="score-grid score-grid--home">
                      <div
                        v-for="(odd, score) in homeScores(match.score)"
                        :key="'score-h-'+score"
                        class="score-btn"
                        :class="{ selected: isSel(match.id, 'score', score), 'score-other': score === '胜其他' }"
                        @click="toggleOdds(match, 'score', score, odd, score)"
                      >
                        <div class="score-text">{{ score }}</div>
                        <div class="score-odds">{{ odd }}</div>
                      </div>
                    </div>
                    <div class="score-grid score-grid--draw">
                      <div
                        v-for="(odd, score) in drawScores(match.score)"
                        :key="'score-d-'+score"
                        class="score-btn"
                        :class="{ selected: isSel(match.id, 'score', score), 'score-other': score === '平其他' }"
                        @click="toggleOdds(match, 'score', score, odd, score)"
                      >
                        <div class="score-text">{{ score }}</div>
                        <div class="score-odds">{{ odd }}</div>
                      </div>
                    </div>
                    <div class="score-grid score-grid--away">
                      <div
                        v-for="(odd, score) in awayScores(match.score)"
                        :key="'score-a-'+score"
                        class="score-btn"
                        :class="{ selected: isSel(match.id, 'score', score), 'score-other': score === '负其他' }"
                        @click="toggleOdds(match, 'score', score, odd, score)"
                      >
                        <div class="score-text">{{ score }}</div>
                        <div class="score-odds">{{ odd }}</div>
                      </div>
                    </div>
                  </div>
                </template>

                <template v-else-if="currentTab === 2">
                  <div class="goals-grid">
                    <div
                      v-for="goal in goalNums"
                      :key="'goal-'+goal"
                      class="goal-btn"
                      :class="{ selected: isSel(match.id, 'goals', goal), disabled: !match.goals[goal] }"
                      @click="match.goals[goal] && toggleOdds(match, 'goals', goal, match.goals[goal], goal + '球')"
                    >
                      <div class="goal-text">{{ goal }}</div>
                      <div class="goal-odds">{{ match.goals[goal] }}</div>
                    </div>
                  </div>
                </template>

                <template v-else-if="currentTab === 3">
                  <div class="hafu-grid">
                    <div
                      v-for="hafu in hafuOptions"
                      :key="'hafu-'+hafu"
                      class="hafu-btn"
                      :class="{ selected: isSel(match.id, 'hafu', hafu), disabled: !match.hafu[hafu] }"
                      @click="match.hafu[hafu] && toggleOdds(match, 'hafu', hafu, match.hafu[hafu], hafu)"
                    >
                      <div class="hafu-text">{{ hafu }}</div>
                      <div class="hafu-odds">{{ match.hafu[hafu] }}</div>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="bottom-padding"></div>

    <div class="bottom-bar">
      <div class="bar-row">
        <div class="badge-column">
          <div class="badge-with-label">
            <div class="selected-badge">{{ selectedItems.length }}</div>
            <div class="selected-label">已选</div>
          </div>
          <div class="multiplier-btn" @click="showMultiplierSelect = true">{{ multiplier }}倍</div>
        </div>
        <div class="bar-middle">
          <div class="pass-select" :class="{ active: showPassSelect }" @click="showPassSelect = !showPassSelect">
            {{ passCounts.length > 0 ? passCounts.map(passLabel).join(' ') : '过关选择' }}
          </div>
          <div class="bet-info">
            <span class="label">投注金额：</span>
            <span class="highlight">{{ totalBet }} 元</span>
          </div>
          <div class="bonus-info">
            <span class="label">理论最高奖金：</span>
            <span class="highlight">{{ maxBonus }} 元</span>
          </div>
        </div>
        <div class="bar-right">
          <button class="clear-btn" @click="clearAll">
            <span class="clear-icon">🗑</span>
            <span>清空</span>
          </button>
          <button class="view-btn" @click="openViewModal">查看方案</button>
        </div>
      </div>
    </div>

    <!-- 过关选择弹窗 -->
    <div class="modal-overlay pass-overlay" v-show="showPassSelect" @click.self="showPassSelect = false">
      <div class="pass-modal">
        <div class="pass-modal-title">过关选择</div>
        <div class="pass-grid">
          <div
            v-for="n in passOptions"
            :key="n"
            class="pass-item"
            :class="{ active: passCounts.includes(n), disabled: n > maxPassCount }"
            @click="selectPass(n)"
          >
            {{ passLabel(n) }}
          </div>
        </div>
        <div class="pass-modal-cancel" @click="showPassSelect = false">确定</div>
      </div>
    </div>

    <!-- 倍数弹窗 -->
    <div class="modal-overlay" v-show="showMultiplierSelect" @click.self="showMultiplierSelect = false">
      <div class="multiplier-modal">
        <div class="multiplier-header">
          <div class="multiplier-display">
            <span class="multiplier-value">{{ tempMultiplier }}</span>
            <span class="multiplier-unit">倍</span>
          </div>
        </div>
        <div class="multiplier-keypad">
          <div class="keypad-row">
            <button class="key-btn" @click="appendDigit(1)">1</button>
            <button class="key-btn" @click="appendDigit(2)">2</button>
            <button class="key-btn" @click="appendDigit(3)">3</button>
          </div>
          <div class="keypad-row">
            <button class="key-btn" @click="appendDigit(4)">4</button>
            <button class="key-btn" @click="appendDigit(5)">5</button>
            <button class="key-btn" @click="appendDigit(6)">6</button>
          </div>
          <div class="keypad-row">
            <button class="key-btn" @click="appendDigit(7)">7</button>
            <button class="key-btn" @click="appendDigit(8)">8</button>
            <button class="key-btn" @click="appendDigit(9)">9</button>
          </div>
          <div class="keypad-row">
            <button class="key-btn key-action" @click="deleteDigit">删除</button>
            <button class="key-btn" @click="appendDigit(0)">0</button>
            <button class="key-btn key-confirm" @click="confirmMultiplier">确认</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 查看方案弹窗 -->
    <div class="modal-overlay" v-show="showViewModal" @click.self="showViewModal = false">
      <div class="view-modal">
        <div class="view-modal-header">
          <div class="view-modal-title">投注方案</div>
          <div class="view-modal-close" @click="showViewModal = false">×</div>
        </div>

        <div class="view-modal-body">
          <div class="view-empty" v-if="selectedItems.length === 0">
            <div class="empty-icon">🎫</div>
            <div class="empty-text">还未选择任何投注项</div>
            <div class="empty-hint">点击比赛赔率开始选号</div>
          </div>

          <div v-else>
            <div class="view-summary">
              <div class="summary-item">
                <div class="summary-label">投注项</div>
                <div class="summary-value">{{ selectedItems.length }} 项</div>
              </div>
              <div class="summary-item">
                <div class="summary-label">比赛</div>
                <div class="summary-value">{{ selectedMatchCount }} 场</div>
              </div>
              <div class="summary-item">
                <div class="summary-label">过关</div>
                <div class="summary-value">{{ passCounts.map(passSummaryLabel).join(' + ') }}</div>
              </div>
              <div class="summary-item">
                <div class="summary-label">倍数</div>
                <div class="summary-value">{{ multiplier }} 倍</div>
              </div>
            </div>

            <div class="view-list">
              <div class="view-list-title">投注明细</div>
              <div
                v-for="(group, gIdx) in groupedSelected"
                :key="gIdx"
                class="view-match-group"
              >
                <div class="view-match-header">
                  <span class="view-match-num">{{ gIdx + 1 }}</span>
                  <span class="view-match-name">{{ getMatchName(group.matchId) }}</span>
                </div>
                <div class="view-match-picks">
                  <div
                    v-for="(item, idx) in group.items"
                    :key="idx"
                    class="view-pick-tag"
                  >
                    <span class="pick-pool">{{ poolName(item.pool) }}</span>
                    <span class="pick-opt">{{ item.label }}</span>
                    <span class="pick-odds">{{ formatNum(item.odd) }}</span>
                  </div>
                </div>
              </div>
            </div>

            <div class="view-stats">
              <div class="stat-row">
                <span class="stat-label">总赔率</span>
                <span class="stat-value">{{ totalOdds }}</span>
              </div>
              <div class="stat-row">
                <span class="stat-label">投注金额</span>
                <span class="stat-value highlight">{{ totalBet }} 元</span>
              </div>
              <div class="stat-row bonus-row">
                <span class="stat-label">理论最高奖金</span>
                <span class="stat-value highlight">{{ maxBonus }} 元</span>
              </div>
            </div>
          </div>
        </div>

        <div class="view-modal-footer">
          <button class="footer-btn cancel-btn" @click="showViewModal = false">关闭</button>
          <button class="footer-btn confirm-btn" :disabled="savingBet" @click="confirmBet">
            {{ savingBet ? '保存中…' : '确认投注' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 同一比赛切换玩法确认弹窗 -->
    <div
      v-if="pendingPlayConflict"
      class="modal-overlay play-conflict-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="play-conflict-title"
      @click.self="cancelPlayReplacement"
    >
      <div class="play-conflict-dialog">
        <div id="play-conflict-title" class="play-conflict-title">玩法冲突</div>
        <div class="play-conflict-message">
          该比赛已添加
          <strong>{{ poolName(pendingPlayConflict.existingPool) }}</strong>，是否替换为
          <strong>{{ poolName(pendingPlayConflict.pool) }}</strong>？
        </div>
        <div class="play-conflict-hint">替换后，该比赛原玩法下的全部选择将被移除。</div>
        <div class="play-conflict-actions">
          <button type="button" class="play-conflict-cancel" @click="cancelPlayReplacement">取消</button>
          <button type="button" class="play-conflict-replace" @click="confirmPlayReplacement">替换</button>
        </div>
      </div>
    </div>

    <div v-if="saveNotice" class="calculator-save-notice">{{ saveNotice }}</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import AccountButton from '../components/AccountButton.vue'
import { apiRequest, authState, openAuth } from '../auth'

const currentTab = ref(0)
const matches = ref([])
const selectedItems = ref([])
const hiddenGroups = ref({})
const collapsedMatches = ref({})
const expandedScoreMatchId = ref(null)
const expandedGoalsMatchId = ref(null)
const expandedHafuMatchId = ref(null)
const expandedMixedMatchIds = ref([])
const showPassSelect = ref(false)
const passCounts = ref([])
const showMultiplierSelect = ref(false)
const tempMultiplier = ref(1)
const multiplier = ref(1)
const showViewModal = ref(false)
const savingBet = ref(false)
const saveNotice = ref('')
const pendingPlayConflict = ref(null)

let hasInputtedMultiplier = false
let hasManuallySelectedPass = false
let saveNoticeTimer = null

const showSaveNotice = (message) => {
  saveNotice.value = message
  if (saveNoticeTimer) window.clearTimeout(saveNoticeTimer)
  saveNoticeTimer = window.setTimeout(() => {
    saveNotice.value = ''
  }, 2600)
}

watch(showMultiplierSelect, (val) => {
  if (val) {
    tempMultiplier.value = multiplier.value
    hasInputtedMultiplier = false
  }
})

const MAX_MATCHES = 8

const maxPassCount = computed(() => {
  const uniqueMatchIds = new Set(selectedItems.value.map(i => i.matchId)).size
  return Math.min(8, uniqueMatchIds)
})

const passOptions = [1, 2, 3, 4, 5, 6, 7, 8]
const passLabel = (n) => n === 1 ? '单关' : `${n}关`
const passSummaryLabel = (n) => n === 1 ? '单关' : `${n}串1`

const tabs = [['胜平负', '让球胜平负'], '比分', '总进球数', '半全场', '混合过关']
const goalNums = ['0','1','2','3','4','5','6','7+']
const hafu1 = ['胜胜','胜平','胜负']
const hafu2 = ['平胜','平平','平负']
const hafu3 = ['负胜','负平','负负']
const hafuOptions = [...hafu1, ...hafu2, ...hafu3]

const groupedMatches = computed(() => {
  const groups = {}
  matches.value.forEach(m => {
    const date = m.date
    if (!groups[date]) {
      groups[date] = { date, dateText: m.dateText, matches: [], hidden: !!hiddenGroups.value[date] }
    }
    groups[date].matches.push(m)
  })
  return Object.values(groups)
})

const formatGroupTitle = (group) => {
  const date = group.date || ''
  const weekday = group.matches[0]?.num?.slice(0, 2) || ''
  const dateNo = date.replace(/-/g, '').slice(2)
  return `${weekday} ${date} 共${group.matches.length}场比赛（比赛编号日期${dateNo}）`
}

const selectedMatchCount = computed(() => new Set(selectedItems.value.map(i => i.matchId)).size)

watch(selectedMatchCount, (count) => {
  if (!hasManuallySelectedPass) {
    passCounts.value = count > 0 ? [count] : []
  }
})

const groupedSelected = computed(() => {
  const map = {}
  selectedItems.value.forEach(item => {
    if (!map[item.matchId]) map[item.matchId] = { matchId: item.matchId, items: [] }
    map[item.matchId].items.push(item)
  })
  return Object.values(map)
})

const matchOptionsCount = computed(() => groupedSelected.value.map(g => g.items.length))

const calculatePassNotes = (matchOptions, k) => {
  const n = matchOptions.length
  if (k < 1 || k > n) return 0
  const combos = []
  const combine = (start, combo) => {
    if (combo.length === k) { combos.push([...combo]); return }
    for (let i = start; i < n; i++) { combo.push(i); combine(i + 1, combo); combo.pop() }
  }
  combine(0, [])
  let total = 0
  combos.forEach(combo => {
    let notes = 1
    combo.forEach(idx => { notes *= matchOptions[idx] })
    total += notes
  })
  return total
}

const totalNotes = computed(() => {
  const opts = matchOptionsCount.value
  if (opts.length === 0) return 0
  let total = 0
  passCounts.value.forEach(pc => { total += calculatePassNotes(opts, pc) })
  return total
})

const totalBet = computed(() => totalNotes.value * 2 * multiplier.value)

const totalOdds = computed(() => {
  let odds = 1
  selectedItems.value.forEach(i => { odds *= i.odd })
  return odds.toFixed(2)
})

const maxBonus = computed(() => (totalOdds.value * totalNotes.value * 2 * multiplier.value).toFixed(2))

const fmt = (v) => {
  const n = parseFloat(v)
  return isNaN(n) ? v : n.toFixed(2)
}

const formatNum = (v) => {
  const n = parseFloat(v)
  return isNaN(n) ? v : n.toFixed(2)
}

const formatOdds = (obj) => {
  if (!obj) return obj
  const r = {}
  for (const k in obj) {
    // 升降标志和让球线属于元数据，不能格式化成小数字符串。
    if (k.endsWith('Flag') || k === 'goalLine') r[k] = obj[k]
    else r[k] = fmt(obj[k])
  }
  return r
}

const fetchMatches = async () => {
  try {
    const resp = await fetch('/api/calc/matches')
    const data = await resp.json()
    if (data.success) {
      matches.value = data.data.map(m => {
        m.shortTime = (m.time || '00:00').split(':').slice(0, 2).join(':')
        m.shortDate = m.date ? m.date.slice(5) : ''
        m.had = formatOdds(m.had)
        m.hhad = formatOdds(m.hhad)
        m.score = formatOdds(m.score)
        m.goals = formatOdds(m.goals)
        m.hafu = formatOdds(m.hafu)
        return m
      })
      if (!expandedScoreMatchId.value && matches.value.length > 0) {
        expandedScoreMatchId.value = matches.value[0].id
      }
      if (!expandedGoalsMatchId.value && matches.value.length > 0) {
        expandedGoalsMatchId.value = matches.value[0].id
      }
      if (!expandedHafuMatchId.value && matches.value.length > 0) {
        expandedHafuMatchId.value = matches.value[0].id
      }
    }
  } catch (e) { console.error(e) }
}

const isCollapsed = (matchId) => {
  if (currentTab.value === 0) return false
  if (currentTab.value === 1) return expandedScoreMatchId.value !== matchId
  if (currentTab.value === 2) return expandedGoalsMatchId.value !== matchId
  if (currentTab.value === 3) return expandedHafuMatchId.value !== matchId
  if (Object.prototype.hasOwnProperty.call(collapsedMatches.value, matchId)) {
    return !!collapsedMatches.value[matchId]
  }
  return !!collapsedMatches.value[matchId]
}
const toggleMatch = (matchId) => {
  if (currentTab.value === 0 || currentTab.value === 4) return

  // 比分页使用手风琴模式：展开一场时自动收起其他比赛。
  if (currentTab.value === 1) {
    expandedScoreMatchId.value = expandedScoreMatchId.value === matchId ? null : matchId
    return
  }

  // 总进球页使用单场手风琴模式。
  if (currentTab.value === 2) {
    expandedGoalsMatchId.value = expandedGoalsMatchId.value === matchId ? null : matchId
    return
  }

  // 半全场与比分页保持一致：同一时间最多展开一场。
  if (currentTab.value === 3) {
    expandedHafuMatchId.value = expandedHafuMatchId.value === matchId ? null : matchId
    return
  }

  collapsedMatches.value[matchId] = !isCollapsed(matchId)
}

const isMixedExpanded = (matchId) => expandedMixedMatchIds.value.includes(matchId)

const toggleMixedMore = (matchId) => {
  expandedMixedMatchIds.value = isMixedExpanded(matchId)
    ? expandedMixedMatchIds.value.filter(id => id !== matchId)
    : [...expandedMixedMatchIds.value, matchId]
}

const toggleDateGroup = (group) => {
  hiddenGroups.value[group.date] = !hiddenGroups.value[group.date]
}

const isSel = (matchId, pool, opt) => selectedItems.value.some(i => i.matchId === matchId && i.pool === pool && i.opt === opt)
const labelHad = (opt) => ({ win: '胜', draw: '平', lose: '负' }[opt] || opt)
const labelHhad = (opt) => ({ win: '胜', draw: '平', lose: '负' }[opt] || opt)

const formatHandicap = (h) => {
  if (h === undefined || h === null || h === 0) return 0
  const value = Number(h)
  if (!Number.isFinite(value) || value === 0) return 0
  return value > 0 ? `+${value}` : `${value}`
}

const flagArrow = (pool, opt) => {
  if (!pool) return ''
  const flag = pool[opt + 'Flag']
  if (flag === 1) return '↑'
  if (flag === 2) return '↓'
  return ''
}

const flagClass = (pool, opt) => {
  if (!pool) return ''
  const flag = pool[opt + 'Flag']
  if (flag === 1) return 'up'
  if (flag === 2) return 'down'
  return ''
}

const getOdds = (pool) => {
  if (!pool) return {}
  return { win: pool.win, draw: pool.draw, lose: pool.lose }
}

const homeScores = (s) => {
  const r = {}
  const list = ['1:0','2:0','2:1','3:0','3:1','3:2','4:0','4:1','4:2','5:0','5:1','5:2']
  list.forEach(x => { if(s[x]) r[x] = s[x] })
  if(s['胜其他']) r['胜其他'] = s['胜其他']
  return r
}

const drawScores = (s) => {
  const r = {}
  const list = ['0:0','1:1','2:2','3:3']
  list.forEach(x => { if(s[x]) r[x] = s[x] })
  if(s['平其他']) r['平其他'] = s['平其他']
  return r
}

const awayScores = (s) => {
  const r = {}
  const list = ['0:1','0:2','1:2','0:3','1:3','2:3','0:4','1:4','2:4','0:5','1:5','2:5']
  list.forEach(x => { if(s[x]) r[x] = s[x] })
  if(s['负其他']) r['负其他'] = s['负其他']
  return r
}

const toggleOdds = (match, pool, opt, odd, label) => {
  const idx = selectedItems.value.findIndex(i => i.matchId === match.id && i.pool === pool && i.opt === opt)
  if (idx >= 0) {
    selectedItems.value.splice(idx, 1)
  } else {
    const conflictingItem = selectedItems.value.find(i => i.matchId === match.id && i.pool !== pool)
    if (conflictingItem) {
      pendingPlayConflict.value = { match, pool, opt, odd, label, existingPool: conflictingItem.pool }
      return
    }

    const currentMatchIds = new Set(selectedItems.value.map(i => i.matchId))
    if (!currentMatchIds.has(match.id) && currentMatchIds.size >= MAX_MATCHES) {
      alert(`最多只能选择 ${MAX_MATCHES} 场比赛`)
      return
    }
    selectedItems.value.push({ matchId: match.id, pool, opt, odd, label })
    showSaveNotice('已添加')
  }
}

const cancelPlayReplacement = () => {
  pendingPlayConflict.value = null
}

const confirmPlayReplacement = () => {
  const pending = pendingPlayConflict.value
  if (!pending) return

  selectedItems.value = selectedItems.value.filter(item => item.matchId !== pending.match.id)
  selectedItems.value.push({
    matchId: pending.match.id,
    pool: pending.pool,
    opt: pending.opt,
    odd: pending.odd,
    label: pending.label
  })
  pendingPlayConflict.value = null
  showSaveNotice('已替换为新玩法')
}

const clearAll = () => {
  selectedItems.value = []
  passCounts.value = []
  hasManuallySelectedPass = false
}

const selectPass = (n) => {
  if (n <= maxPassCount.value) {
    hasManuallySelectedPass = true
    const idx = passCounts.value.indexOf(n)
    if (idx >= 0) passCounts.value.splice(idx, 1)
    else passCounts.value.push(n)
    passCounts.value.sort((a, b) => a - b)
    if (passCounts.value.length === 0) passCounts.value = [n]
  }
}

const confirmMultiplier = () => {
  if (tempMultiplier.value >= 1) {
    multiplier.value = tempMultiplier.value
    showMultiplierSelect.value = false
  }
}

const appendDigit = (digit) => {
  if (!hasInputtedMultiplier && tempMultiplier.value === 1 && digit !== 0) {
    tempMultiplier.value = digit
    hasInputtedMultiplier = true
    return
  }
  const current = String(tempMultiplier.value || 0)
  const newVal = current + String(digit)
  const n = parseInt(newVal)
  if (n >= 1 && n <= 9999) {
    tempMultiplier.value = n
    hasInputtedMultiplier = true
  }
}

const deleteDigit = () => {
  const current = String(tempMultiplier.value || 0)
  if (current.length <= 1) {
    tempMultiplier.value = 1
    hasInputtedMultiplier = false
  } else {
    const newVal = current.slice(0, -1)
    const n = parseInt(newVal) || 1
    tempMultiplier.value = n
    if (n === 1) hasInputtedMultiplier = false
  }
}

const poolName = (pool) => {
  const map = { 'had': '胜平负', 'hhad': '让球胜平负', 'score': '比分', 'goals': '总进球', 'hafu': '半全场' }
  return map[pool] || pool
}

const getMatchName = (matchId) => {
  const m = matches.value.find(x => x.id === matchId)
  if (!m) return matchId
  return `${m.num} ${m.homeTeam} VS ${m.awayTeam}`
}

const validatePassSelection = () => {
  const matchCount = selectedMatchCount.value
  const currentPassCount = Math.max(0, ...passCounts.value)

  if (matchCount === 0 || (currentPassCount >= 2 && matchCount < 2)) {
    showSaveNotice('请至少选择两场比赛')
    return false
  }
  if (currentPassCount > matchCount) {
    showSaveNotice('关数超过比赛数，请调整')
    return false
  }
  return true
}

const openViewModal = () => {
  if (!validatePassSelection()) return
  showViewModal.value = true
}

const confirmBet = async () => {
  if (!validatePassSelection()) return
  if (selectedItems.value.length === 0) {
    alert('请先选择投注项')
    return
  }
  if (passCounts.value.length === 0) {
    alert('请选择过关方式')
    return
  }
  if (!authState.user) {
    openAuth('login')
    return
  }

  savingBet.value = true
  try {
    const items = selectedItems.value.map(item => {
      const match = matches.value.find(entry => entry.id === item.matchId)
      return {
        ...item,
        match: match ? {
          num: match.num,
          league: match.league,
          homeTeam: match.homeTeam,
          awayTeam: match.awayTeam,
          date: match.date,
          time: match.time,
          handicap: match.handicap
        } : {}
      }
    })
    const result = await apiRequest('/api/user/bets', {
      method: 'POST',
      body: JSON.stringify({
        selected_items: items,
        pass_counts: passCounts.value,
        multiplier: multiplier.value
      })
    })
    const saved = result.data
    showSaveNotice(`方案已保存，投注金额 ${Number(saved.stake).toFixed(2)} 元`)
    showViewModal.value = false
    clearAll()
  } catch (error) {
    if (error.status === 401) openAuth('login')
    else showSaveNotice(error.message || '保存失败，请稍后重试')
  } finally {
    savingBet.value = false
  }
}

onMounted(() => { fetchMatches() })
</script>

<style scoped>
.play-conflict-overlay {
  z-index: 1200;
  align-items: center;
  padding: 24px;
}

.play-conflict-dialog {
  width: min(100%, 360px);
  overflow: hidden;
  border-top: 4px solid #e53935;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 16px 48px rgb(0 0 0 / 24%);
  text-align: center;
}

.play-conflict-title {
  padding: 22px 20px 8px;
  color: #d32f2f;
  font-size: 19px;
  font-weight: 700;
}

.play-conflict-message {
  padding: 4px 22px;
  color: #333;
  font-size: 15px;
  line-height: 1.7;
}

.play-conflict-message strong {
  color: #d32f2f;
}

.play-conflict-hint {
  padding: 8px 22px 20px;
  color: #888;
  font-size: 12px;
  line-height: 1.5;
}

.play-conflict-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  border-top: 1px solid #eee;
}

.play-conflict-actions button {
  min-height: 50px;
  border: 0;
  background: #fff;
  font-size: 16px;
}

.play-conflict-cancel {
  border-right: 1px solid #eee !important;
  color: #666;
}

.play-conflict-replace {
  color: #d32f2f;
  font-weight: 700;
}
</style>

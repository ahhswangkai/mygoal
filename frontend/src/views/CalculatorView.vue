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

    <div v-if="calculatorDataMessage" class="calculator-data-message" :class="{ error: calculatorDataError }">
      {{ calculatorDataMessage }}
      <button v-if="calculatorDataError" type="button" @click="fetchMatches">重试</button>
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
                      <span class="line-text">[{{ poolLine(match.had, 0) }}]</span>
                    </div>
                    <div
                      v-for="(odd, opt) in getOdds(match.had)"
                      :key="'had-'+opt"
                      class="mix-btn"
                      :class="{
                        selected: isSel(match.id, 'had', opt),
                        unavailable: !isOddsAvailable(odd)
                      }"
                      :aria-disabled="!isOddsAvailable(odd)"
                      @click="toggleOdds(match, 'had', opt, odd, labelHad(opt))"
                    >
                      <div class="mix-text">{{ labelHad(opt) }}</div>
                      <div class="mix-odds">{{ displayOdds(odd) }}</div>
                      <span class="mix-arrow" :class="flagClass(match.had, opt)">{{ flagArrow(match.had, opt) }}</span>
                    </div>
                  </div>
                  <div class="odds-row had-row">
                    <div class="goal-line">
                      <span class="single-tag" v-if="match.hhadSingle">单</span>
                      <span class="line-text">[{{ poolLine(match.hhad, formatHandicap(match.handicap)) }}]</span>
                    </div>
                    <div
                      v-for="(odd, opt) in getOdds(match.hhad)"
                      :key="'hhad-'+opt"
                      class="mix-btn"
                      :class="{
                        selected: isSel(match.id, 'hhad', opt),
                        unavailable: !isOddsAvailable(odd)
                      }"
                      :aria-disabled="!isOddsAvailable(odd)"
                      @click="toggleOdds(match, 'hhad', opt, odd, labelHhad(opt))"
                    >
                      <div class="mix-text">{{ labelHhad(opt) }}</div>
                      <div class="mix-odds">{{ displayOdds(odd) }}</div>
                      <span class="mix-arrow" :class="flagClass(match.hhad, opt)">{{ flagArrow(match.hhad, opt) }}</span>
                    </div>
                  </div>
                </template>

                <template v-else-if="currentTab === 4">
                  <div class="mixed-base-odds">
                    <div class="odds-row mixed-base-row">
                      <div class="goal-line">
                        <span class="single-tag" v-if="match.hadSingle">单</span>
                        <span class="line-text">[{{ poolLine(match.had, 0) }}]</span>
                      </div>
                      <div
                        v-for="(odd, opt) in getOdds(match.had)"
                        :key="'mix-had-'+opt"
                        class="mix-btn"
                        :class="{
                          selected: isSel(match.id, 'had', opt),
                          unavailable: !isOddsAvailable(odd)
                        }"
                        :aria-disabled="!isOddsAvailable(odd)"
                        @click="toggleOdds(match, 'had', opt, odd, labelHad(opt))"
                      >
                        <div class="mix-text">{{ labelHad(opt) }}</div>
                        <div class="mix-odds">{{ displayOdds(odd) }}</div>
                        <span class="mix-arrow" :class="flagClass(match.had, opt)">{{ flagArrow(match.had, opt) }}</span>
                      </div>
                    </div>
                    <div class="odds-row mixed-base-row">
                      <div class="goal-line">
                        <span class="single-tag" v-if="match.hhadSingle">单</span>
                        <span class="line-text">[{{ poolLine(match.hhad, formatHandicap(match.handicap)) }}]</span>
                      </div>
                      <div
                        v-for="(odd, opt) in getOdds(match.hhad)"
                        :key="'mix-hhad-'+opt"
                        class="mix-btn"
                        :class="{
                          selected: isSel(match.id, 'hhad', opt),
                          unavailable: !isOddsAvailable(odd)
                        }"
                        :aria-disabled="!isOddsAvailable(odd)"
                        @click="toggleOdds(match, 'hhad', opt, odd, labelHhad(opt))"
                      >
                        <div class="mix-text">{{ labelHhad(opt) }}</div>
                        <div class="mix-odds">{{ displayOdds(odd) }}</div>
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
          <div class="bar-right-tools">
            <button class="clear-btn" @click="clearAll">
              <span class="clear-icon">🗑</span>
              <span>清空</span>
            </button>
            <button
              class="draft-btn"
              :disabled="savingDraft || selectedItems.length === 0"
              @click="saveDraft"
            >
              <span aria-hidden="true">{{ editingDraftId ? '✎' : '☆' }}</span>
              <span>{{ savingDraft ? '保存中' : editingDraftId ? '保存修改' : '草稿' }}</span>
            </button>
          </div>
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
    <div
      class="modal-overlay bet-plan-overlay"
      v-show="showViewModal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="bet-plan-title"
      @click.self="showViewModal = false"
    >
      <div class="view-modal">
        <div ref="shareCardRef" class="bet-share-card" data-bet-share-card>
          <div class="view-modal-header">
            <div id="bet-plan-title" class="view-modal-title">
              过关方式：<strong>{{ passCounts.map(passLabel).join('、') }}</strong>
            </div>
            <button
              type="button"
              class="view-modal-close"
              aria-label="关闭投注方案"
              data-html2canvas-ignore="true"
              @click="showViewModal = false"
            >×</button>
          </div>

          <div class="view-modal-body">
            <div class="view-empty" v-if="selectedItems.length === 0">
              <div class="empty-icon">🎫</div>
              <div class="empty-text">还未选择任何投注项</div>
              <div class="empty-hint">点击比赛赔率开始选号</div>
            </div>

            <div v-else>
              <div class="view-list">
                <div
                  v-for="(group, gIdx) in groupedSelected"
                  :key="gIdx"
                  class="view-match-group"
                >
                  <div class="view-match-header">
                    <span class="view-match-name">{{ getMatchName(group.matchId) }}</span>
                  </div>
                  <div class="view-match-picks">
                    <span
                      v-for="(item, idx) in group.items"
                      :key="idx"
                      class="view-pick-tag"
                    >
                      {{ viewPickLabel(item) }}({{ formatNum(item.odd) }})
                    </span>
                  </div>
                </div>
              </div>

              <div class="view-stats">
                <div class="stat-row">
                  <span class="stat-label">倍数</span>
                  <span class="stat-value">{{ multiplier }}倍</span>
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

          <div class="bet-share-signature">
            <strong>MYGOAL</strong>
            <span>足球投注方案 · 仅供个人参考，请理性投注</span>
          </div>
        </div>

        <div class="plan-image-save-tip">
          点击“保存到相册”后，在系统面板选择“存储图像”
        </div>
        <div class="view-modal-footer">
          <button class="footer-btn cancel-btn" @click="showViewModal = false">关闭</button>
          <button
            class="footer-btn share-btn"
            :disabled="preparingPlanImage || sharingPlanImage || !planImageBlob"
            @click="savePlanImage"
          >
            {{ preparingPlanImage ? '生成中…' : sharingPlanImage ? '保存中…' : '保存到相册' }}
          </button>
          <button class="footer-btn confirm-btn" :disabled="savingBet" @click="confirmBet">
            {{ savingBet ? '保存中…' : '确认投注' }}
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="showPlanImagePreview"
      class="plan-image-preview-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="保存投注方案图片"
      @click.self="closePlanImagePreview"
    >
      <section class="plan-image-preview">
        <header>
          <div>
            <strong>保存到相册</strong>
            <p>长按下面图片，选择“存储到照片”</p>
          </div>
          <button type="button" aria-label="关闭图片预览" @click="closePlanImagePreview">×</button>
        </header>
        <div>
          <img :src="planImagePreviewUrl" alt="投注方案分享图片">
        </div>
      </section>
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
import { nextTick, ref, computed, onMounted, watch } from 'vue'
import AccountButton from '../components/AccountButton.vue'
import { apiRequest, authState, openAuth } from '../auth'
import { calculateMaxBonus } from '../utils/betMath'
import { oddsTrend, oddsTrendArrow } from '../utils/oddsTrend'
import { normalizeSportteryCalculatorPayload } from '../utils/sportteryCalculator'

const currentTab = ref(0)
const matches = ref([])
const calculatorDataMessage = ref('正在加载比赛数据…')
const calculatorDataError = ref(false)
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
const savingDraft = ref(false)
const editingDraftId = ref('')
const preparingPlanImage = ref(false)
const sharingPlanImage = ref(false)
const planImageBlob = ref(null)
const planImagePreviewUrl = ref('')
const showPlanImagePreview = ref(false)
const shareCardRef = ref(null)
const saveNotice = ref('')
const pendingPlayConflict = ref(null)

let hasInputtedMultiplier = false
let hasManuallySelectedPass = false
let saveNoticeTimer = null
let planImageToken = 0

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

const maxBonus = computed(() => calculateMaxBonus(
  selectedItems.value,
  passCounts.value,
  multiplier.value
).toFixed(2))

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

const SPORTTERY_CALCULATOR_URL = 'https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry?poolCode=had%2Chhad%2Ccrs%2Cttg%2Chafu'
const CALCULATOR_CACHE_KEY = 'mygoal-calculator-matches-v1'
const CALCULATOR_DRAFT_LOAD_KEY = 'mygoal-calculator-draft-load-v1'

const prepareMatches = sourceMatches => sourceMatches.map(source => {
  const match = { ...source }
  match.shortTime = (match.time || '00:00').split(':').slice(0, 2).join(':')
  match.shortDate = match.date ? match.date.slice(5) : ''
  match.had = formatOdds(match.had)
  match.hhad = formatOdds(match.hhad)
  match.score = formatOdds(match.score)
  match.goals = formatOdds(match.goals)
  match.hafu = formatOdds(match.hafu)
  return match
})

const readCachedMatches = () => {
  try {
    const cached = JSON.parse(window.localStorage.getItem(CALCULATOR_CACHE_KEY) || 'null')
    return Array.isArray(cached?.matches) && cached.matches.length > 0 ? cached : null
  } catch {
    return null
  }
}

const cacheMatches = sourceMatches => {
  try {
    window.localStorage.setItem(CALCULATOR_CACHE_KEY, JSON.stringify({
      updatedAt: new Date().toISOString(),
      matches: sourceMatches
    }))
  } catch {
    // Safari private mode or a full storage quota must not break the calculator.
  }
}

const fetchOfficialMatches = async () => {
  const response = await fetch(SPORTTERY_CALCULATOR_URL, {
    credentials: 'omit',
    cache: 'no-store'
  })
  if (!response.ok) throw new Error(`体彩接口返回 ${response.status}`)
  return normalizeSportteryCalculatorPayload(await response.json())
}

const fetchBackendMatches = async () => {
  const response = await fetch('/api/calc/matches')
  const payload = await response.json().catch(() => ({}))
  if (!response.ok || !payload.success || !Array.isArray(payload.data)) {
    throw new Error(payload.message || `服务器接口返回 ${response.status}`)
  }
  return payload.data
}

const fetchMatches = async () => {
  calculatorDataMessage.value = '正在加载比赛数据…'
  calculatorDataError.value = false
  try {
    let sourceMatches
    try {
      sourceMatches = await fetchOfficialMatches()
    } catch (officialError) {
      console.warn('体彩浏览器直连失败，回退服务器接口', officialError)
      sourceMatches = await fetchBackendMatches()
    }

    if (!sourceMatches.length) throw new Error('当前没有可售比赛')
    cacheMatches(sourceMatches)
    matches.value = prepareMatches(sourceMatches)
    restoreDraftSelection()
    calculatorDataMessage.value = ''

    if (!expandedScoreMatchId.value && matches.value.length > 0) {
      expandedScoreMatchId.value = matches.value[0].id
    }
    if (!expandedGoalsMatchId.value && matches.value.length > 0) {
      expandedGoalsMatchId.value = matches.value[0].id
    }
    if (!expandedHafuMatchId.value && matches.value.length > 0) {
      expandedHafuMatchId.value = matches.value[0].id
    }
  } catch (error) {
    console.error(error)
    const cached = readCachedMatches()
    if (cached) {
      matches.value = prepareMatches(cached.matches)
      restoreDraftSelection()
      calculatorDataMessage.value = `官方接口暂时不可用，当前显示缓存数据（${new Date(cached.updatedAt).toLocaleString('zh-CN', { hour12: false })}）`
      return
    }
    matches.value = []
    calculatorDataError.value = true
    calculatorDataMessage.value = '比赛数据加载失败，请稍后重试'
  }
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

const isOddsAvailable = odd => {
  const value = Number(odd)
  return Number.isFinite(value) && value > 0
}

const selectedItemOdds = (match, item) => {
  const pool = item.pool === 'goals'
    ? match?.goals
    : item.pool === 'score'
      ? match?.score
      : item.pool === 'hafu'
        ? match?.hafu
        : match?.[item.pool]
  return pool?.[item.opt]
}

const restoreDraftSelection = () => {
  let stored
  try {
    stored = JSON.parse(window.sessionStorage.getItem(CALCULATOR_DRAFT_LOAD_KEY) || 'null')
    if (stored) window.sessionStorage.removeItem(CALCULATOR_DRAFT_LOAD_KEY)
  } catch {
    window.sessionStorage.removeItem(CALCULATOR_DRAFT_LOAD_KEY)
    return
  }
  if (!stored || !Array.isArray(stored.selected_items)) return

  const restored = []
  stored.selected_items.forEach(item => {
    const matchId = String(item.match_id || item.matchId || '')
    const match = matches.value.find(entry => String(entry.id) === matchId)
    const odd = selectedItemOdds(match, item)
    if (!match || !isOddsAvailable(odd)) return
    restored.push({
      // Preserve the live match id type so the calculator's strict selection
      // checks keep working for sources that return numeric ids.
      matchId: match.id,
      pool: item.pool,
      opt: item.opt,
      odd,
      label: item.label || item.opt
    })
  })
  if (restored.length === 0) {
    showSaveNotice('草稿场次已停售或赔率不可用')
    return
  }

  selectedItems.value = restored
  const restoredMatchCount = new Set(restored.map(item => item.matchId)).size
  const restoredPasses = (stored.pass_counts || [])
    .map(Number)
    .filter(value => Number.isInteger(value) && value >= 1 && value <= restoredMatchCount)
  passCounts.value = restoredPasses.length ? [...new Set(restoredPasses)].sort((a, b) => a - b) : [restoredMatchCount]
  multiplier.value = Math.max(1, Math.min(9999, Number(stored.multiplier) || 1))
  hasManuallySelectedPass = restoredPasses.length > 0
  editingDraftId.value = String(stored.draft_id || '')
  showSaveNotice(
    editingDraftId.value
      ? `正在修改草稿，已载入${restoredMatchCount}场比赛`
      : `已载入草稿，${restoredMatchCount}场比赛`
  )
}

const isPoolAvailable = pool => (
  ['win', 'draw', 'lose'].some(opt => isOddsAvailable(pool?.[opt]))
)

const poolLine = (pool, line) => isPoolAvailable(pool) ? line : '未'
const displayOdds = odd => isOddsAvailable(odd) ? odd : '-'

const flagArrow = (pool, opt) => {
  if (!pool) return ''
  return oddsTrendArrow(pool[opt + 'Flag'])
}

const flagClass = (pool, opt) => {
  if (!pool) return ''
  return oddsTrend(pool[opt + 'Flag'])
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
  if (!isOddsAvailable(odd)) return
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

const viewPickLabel = (item) => {
  if (item.pool === 'hhad') return `让球${item.label}`
  if (item.pool === 'had') return item.label
  if (item.pool === 'score' || item.pool === 'goals') {
    return String(item.opt || item.label || '').replace(/球$/, '')
  }
  if (item.pool === 'hafu') return item.opt || item.label
  return `${poolName(item.pool)}${item.label}`
}

const getMatchName = (matchId) => {
  const m = matches.value.find(x => x.id === matchId)
  if (!m) return matchId
  return `${m.num} ${m.homeTeam} VS ${m.awayTeam}`
}

const selectedItemsPayload = () => selectedItems.value.map(item => {
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

const saveDraft = async () => {
  if (savingDraft.value || selectedItems.value.length === 0) return
  if (!authState.user) {
    openAuth('login')
    return
  }

  const draftPassCounts = passCounts.value.length
    ? passCounts.value
    : [Math.max(1, selectedMatchCount.value)]
  savingDraft.value = true
  try {
    const isEditing = Boolean(editingDraftId.value)
    const endpoint = isEditing
      ? `/api/user/drafts/${encodeURIComponent(editingDraftId.value)}`
      : '/api/user/drafts'
    const result = await apiRequest(endpoint, {
      method: isEditing ? 'PUT' : 'POST',
      body: JSON.stringify({
        selected_items: selectedItemsPayload(),
        pass_counts: draftPassCounts,
        multiplier: multiplier.value
      })
    })
    const duplicate = Boolean(result.data?.deduplicated)
    if (isEditing) {
      showSaveNotice(duplicate ? '修改已保存，并合并相同草稿' : '草稿修改已保存')
    } else {
      showSaveNotice(duplicate ? '该方案已在今日草稿箱' : '已加入今日草稿箱')
    }
  } catch (error) {
    if (error.status === 401) openAuth('login')
    else showSaveNotice(error.message || '草稿保存失败')
  } finally {
    savingDraft.value = false
  }
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

const imageFileName = () => {
  const now = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  const stamp = [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
    '-',
    pad(now.getHours()),
    pad(now.getMinutes())
  ].join('')
  return `mygoal-投注方案-${stamp}.png`
}

const closePlanImagePreview = () => {
  showPlanImagePreview.value = false
  if (planImagePreviewUrl.value) {
    URL.revokeObjectURL(planImagePreviewUrl.value)
    planImagePreviewUrl.value = ''
  }
}

const openPlanImagePreview = () => {
  if (!planImageBlob.value) return
  closePlanImagePreview()
  planImagePreviewUrl.value = URL.createObjectURL(planImageBlob.value)
  showPlanImagePreview.value = true
}

const preparePlanImage = async (token) => {
  preparingPlanImage.value = true
  let renderHost = null
  try {
    await nextTick()
    if (token !== planImageToken || !shareCardRef.value) return

    const { default: html2canvas } = await import('html2canvas')
    const clonedCard = shareCardRef.value.cloneNode(true)
    clonedCard.classList.add('bet-share-card--export')

    renderHost = document.createElement('div')
    renderHost.className = 'bet-share-render-host'
    renderHost.appendChild(clonedCard)
    document.body.appendChild(renderHost)

    if (document.fonts?.ready) await document.fonts.ready
    const canvas = await html2canvas(clonedCard, {
      backgroundColor: '#ffffff',
      scale: 2,
      useCORS: true,
      logging: false
    })
    const blob = await new Promise((resolve, reject) => {
      canvas.toBlob(
        (result) => result ? resolve(result) : reject(new Error('图片生成失败')),
        'image/png'
      )
    })
    if (token === planImageToken) planImageBlob.value = blob
  } catch (error) {
    if (token === planImageToken) {
      showSaveNotice(error?.message || '生成图片失败，请稍后重试')
    }
  } finally {
    renderHost?.remove()
    if (token === planImageToken) preparingPlanImage.value = false
  }
}

const savePlanImage = () => {
  if (!planImageBlob.value || sharingPlanImage.value) return

  const fileName = imageFileName()
  const imageFile = typeof File === 'function'
    ? new File([planImageBlob.value], fileName, { type: 'image/png' })
    : null
  const canShareImage = !!(
    imageFile &&
    typeof navigator.share === 'function' &&
    typeof navigator.canShare === 'function' &&
    navigator.canShare({ files: [imageFile] })
  )

  if (!canShareImage) {
    openPlanImagePreview()
    return
  }

  sharingPlanImage.value = true
  navigator.share({
    files: [imageFile],
    title: '足球投注方案'
  }).then(() => {
    showSaveNotice('图片已保存或分享')
  }).catch(error => {
    if (error?.name !== 'AbortError') openPlanImagePreview()
  }).finally(() => {
    sharingPlanImage.value = false
  })
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
    const result = await apiRequest('/api/user/bets', {
      method: 'POST',
      body: JSON.stringify({
        selected_items: selectedItemsPayload(),
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

watch(showViewModal, visible => {
  planImageToken += 1
  planImageBlob.value = null
  preparingPlanImage.value = false
  sharingPlanImage.value = false
  closePlanImagePreview()
  if (visible) preparePlanImage(planImageToken)
})

onMounted(() => { fetchMatches() })
</script>

<style scoped>
.calculator-data-message {
  display: flex;
  min-height: 40px;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin: 10px 14px 0;
  padding: 9px 12px;
  color: #9a6b19;
  background: #fff8e8;
  border: 1px solid #f3dfae;
  border-radius: 9px;
  font-size: 12px;
  line-height: 1.5;
  text-align: center;
}

.calculator-data-message.error {
  color: #d9363e;
  background: #fff1f2;
  border-color: #ffc9cd;
}

.calculator-data-message button {
  flex: 0 0 auto;
  padding: 5px 12px;
  color: #fff;
  background: #f33b48;
  border: 0;
  border-radius: 999px;
  font-size: 12px;
}

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

.calculator-page .mix-btn.unavailable {
  cursor: default;
  pointer-events: none;
  background: #fafafa;
  border-color: #e2e2e2;
}

.calculator-page .mix-btn.unavailable .mix-odds {
  color: #b8b8b8;
}

.calculator-page .mix-btn.unavailable .mix-arrow {
  display: none;
}
</style>

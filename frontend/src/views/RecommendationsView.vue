<template>
  <div class="app-container primary-page recommendations-page">
    <header class="top-header">
      <span class="header-side-spacer" aria-hidden="true"></span>
      <span class="header-title">FAE 推荐</span>
      <AccountButton />
    </header>

    <nav class="recommendation-dates" aria-label="推荐日期">
      <button
        v-for="day in dateOptions"
        :key="day.value"
        type="button"
        :class="{ active: selectedDate === day.value }"
        @click="selectDate(day.value)"
      >
        <span>{{ day.weekday }}<i v-if="day.isToday">今</i></span>
        <small>{{ day.label }}</small>
      </button>
    </nav>

    <main class="recommendations-content">
      <section class="recommendation-hero">
        <div>
          <span class="fae-badge">FAE</span>
          <div>
            <strong>Football AI Engine</strong>
            <small>v{{ faeDailyAi?.engine_version || faeSkills.engine_version || '2.0.0' }}</small>
          </div>
        </div>
        <button type="button" :class="{ rotating: loading }" @click="fetchData">↻</button>
      </section>

      <nav class="recommendation-tabs">
        <button :class="{ active: activeSection === 'dailyAi' }" @click="selectSection('dailyAi')">
          AI研判
        </button>
        <button :class="{ active: activeSection === 'review' }" @click="selectSection('review')">
          赛后复盘
        </button>
        <button :class="{ active: activeSection === 'skills' }" @click="selectSection('skills')">
          Skill 迭代
        </button>
      </nav>

      <div v-if="loading && !hasData" class="recommendation-state">正在生成当天推荐…</div>
      <div v-else-if="error && !hasData" class="recommendation-state error">
        <span>{{ error }}</span>
        <button type="button" @click="fetchData">重新加载</button>
      </div>

      <template v-else-if="activeSection === 'dailyAi'">
        <section v-if="faeDailyAi" class="daily-ai-panel">
          <header class="panel-heading daily-ai-heading">
            <div>
              <strong>火山全日 AI 研判</strong>
              <small>
                共 {{ faeDailyAi.match_count || 0 }} 场 ·
                <template v-if="faeDailyAi.retained_match_count">
                  本轮研判 {{ faeDailyAi.analyzed_match_count || 0 }} 场，
                  保留 {{ faeDailyAi.retained_match_count }} 场赛前结论 ·
                </template>
                <template v-else>一次性横向比较 ·</template>
                研判时间 {{ formatAiTime(faeDailyAi.generated_at) }}
              </small>
            </div>
            <button
              v-if="dailyAiCanManage"
              type="button"
              :disabled="dailyAiBusy"
              @click="runDailyAi(true)"
            >
              {{ dailyAiBusy ? '研判中…' : '重新研判' }}
            </button>
          </header>

          <div class="daily-ai-summary">
            <span class="daily-ai-kicker">今日核心结论</span>
            <p>{{ displayDailyText(faeDailyAi.daily_summary?.core_conclusion || '暂无总览结论') }}</p>
            <div v-if="faeDailyAi.daily_summary?.warnings?.length" class="daily-ai-warnings">
              <span v-for="warning in faeDailyAi.daily_summary.warnings" :key="warning">
                ⚠ {{ displayDailyText(warning) }}
              </span>
            </div>
            <div
              v-if="faeDailyAi.review_memory?.review_days"
              class="daily-review-memory"
            >
              <strong>已加载复盘记忆</strong>
              <span>
                {{ faeDailyAi.review_memory.review_days }} 天历史 ·
                {{ faeDailyAi.review_memory.observation_count || 0 }} 个观察日 ·
                {{ faeDailyAi.review_memory.validated_pattern_count || 0 }} 条已验证模式
              </span>
              <small>
                来源 {{ faeDailyAi.review_memory.source_dates?.join('、') }}；
                单日结论只作风险提醒，不直接决定今天推荐
              </small>
            </div>
            <div v-if="historicalModelCount" class="goal-margin-loaded">
              <strong>已加载历史进球差模型</strong>
              <span>
                覆盖 {{ historicalModelCount }} 场 ·
                {{ historicalCalibrationCount }} 场实际校准
              </span>
            </div>
            <div v-if="supervisedShadow.model_id" class="goal-margin-loaded supervised-shadow-loaded">
              <strong>历史监督模型 · 影子验证</strong>
              <span>
                {{ supervisedShadow.sample_count || 0 }} 场训练样本 ·
                截止 {{ supervisedShadow.training_end_date || '--' }}
              </span>
              <small>未通过发布门禁前不覆盖正式推荐</small>
            </div>
          </div>

          <section v-if="drawRadarGroups.length" class="draw-radar-panel">
            <header>
              <div>
                <strong>平 / 让平概率排行榜</strong>
                <small>只展示最关键候选，完整依据进详情页看</small>
              </div>
              <span>核心 / 小试</span>
            </header>
            <div class="draw-radar-groups">
              <article v-for="group in drawRadarGroups" :key="group.key">
                <div class="draw-radar-title">
                  <strong>{{ group.title }}</strong>
                  <small>Top {{ group.items.length }}</small>
                </div>
                <button
                  v-for="(item, index) in group.items"
                  :key="`${group.key}-${item.match_id}`"
                  type="button"
                  @click="goToDetail(item.match_id)"
                >
                  <i class="draw-radar-rank">{{ index + 1 }}</i>
                  <span class="draw-radar-match">
                    <b>{{ dailyMatch(item.match_id).match_number }}</b>
                    <span>
                      {{ dailyMatch(item.match_id).home_team }}
                      vs
                      {{ dailyMatch(item.match_id).away_team }}
                    </span>
                    <small>{{ shortRadarReason(item) }}</small>
                  </span>
                  <span class="draw-radar-decision">
                    <i :class="item.tier">{{ radarTierLabel(item.tier) }}</i>
                    <b>{{ starText(item.rating) }}</b>
                  </span>
                  <span class="draw-radar-metrics">
                    <i>概率 {{ radarPercent(item.probability) }}</i>
                    <i v-if="supervisedProbability(item.match_id, group.key) !== null" class="supervised-probability">
                      影子 {{ radarPercent(supervisedProbability(item.match_id, group.key)) }}
                    </i>
                    <i>赔率 {{ item.odds ?? '--' }}</i>
                    <i :class="metricClass(item.odds_value)">
                      价值 {{ signedMetric(item.odds_value) }}%
                    </i>
                  </span>
                </button>
              </article>
            </div>
          </section>

          <section v-if="showModelPanels && upsetWarningItems.length" class="draw-radar-panel upset-warning-panel">
            <header>
              <div>
                <strong>爆冷预警榜</strong>
                <small>先识别热门不稳，再决定是否防平/让平</small>
              </div>
              <span>80+ 重点防冷</span>
            </header>
            <div class="draw-radar-groups upset-warning-list">
              <article>
                <button
                  v-for="(item, index) in upsetWarningItems"
                  :key="`upset-${item.match_id}`"
                  type="button"
                  @click="goToDetail(item.match_id)"
                >
                  <i class="draw-radar-rank">{{ index + 1 }}</i>
                  <span class="draw-radar-match">
                    <b>{{ dailyMatch(item.match_id).match_number }}</b>
                    <span>
                      {{ dailyMatch(item.match_id).home_team }}
                      vs
                      {{ dailyMatch(item.match_id).away_team }}
                    </span>
                    <small>
                      热门 {{ item.favorite_team || '--' }} ·
                      防 {{ item.suggested_defenses?.join(' / ') || '--' }}
                    </small>
                  </span>
                  <span class="draw-radar-decision league-index-value">
                    <i :class="upsetLevelClass(item.score)">爆冷</i>
                    <b>{{ item.score ?? '--' }}分</b>
                  </span>
                  <span class="draw-radar-metrics">
                    <i v-for="label in item.factor_labels || []" :key="label">
                      {{ label }}
                    </i>
                  </span>
                  <small class="draw-radar-reason">{{ item.reason || item.level }}</small>
                </button>
              </article>
            </div>
            <p>{{ faeDailyAi.daily_summary?.upset_warning?.policy }}</p>
          </section>

          <section v-if="showModelPanels && oddsBandGroups.length" class="draw-radar-panel odds-band-panel">
            <header>
              <div>
                <strong>赔率区间指标</strong>
                <small>热门过热、下盘爆冷、让平价值</small>
              </div>
              <span>区间扫描 · 不直接投注</span>
            </header>
            <div class="draw-radar-groups">
              <article v-for="group in oddsBandGroups" :key="group.key">
                <div class="draw-radar-title">
                  <strong>{{ group.title }}</strong>
                  <small>Top {{ group.items.length }}</small>
                </div>
                <button
                  v-for="(item, index) in group.items"
                  :key="`${group.key}-${item.match_id}`"
                  type="button"
                  @click="goToDetail(item.match_id)"
                >
                  <i class="draw-radar-rank">{{ index + 1 }}</i>
                  <span class="draw-radar-match">
                    <b>{{ dailyMatch(item.match_id).match_number }}</b>
                    <span>
                      {{ dailyMatch(item.match_id).home_team }}
                      vs
                      {{ dailyMatch(item.match_id).away_team }}
                    </span>
                    <small>
                      热门 {{ item.favorite_team || '--' }}
                      <template v-if="item.favorite_odds">
                        · {{ item.favorite_odds }}
                      </template>
                      <template v-if="item.favorite_band">
                        · {{ item.favorite_band }}
                      </template>
                    </small>
                  </span>
                  <span class="draw-radar-decision league-index-value">
                    <i :class="leagueIndexClass(item.index)">{{ item.level || '指数' }}</i>
                    <b>{{ item.index ?? '--' }}分</b>
                  </span>
                  <span class="draw-radar-metrics">
                    <i>平赔 {{ item.draw_odds ?? '--' }}</i>
                    <i>让平 {{ item.handicap_draw_odds ?? '--' }}</i>
                    <i v-if="item.suggested_focus?.length">
                      {{ item.suggested_focus.join(' / ') }}
                    </i>
                  </span>
                  <small class="draw-radar-reason">{{ item.reason }}</small>
                </button>
              </article>
            </div>
            <p>{{ faeDailyAi.daily_summary?.odds_band_indicators?.policy }}</p>
          </section>

          <section v-if="showModelPanels && leagueModelGroups.length" class="draw-radar-panel league-model-panel">
            <header>
              <div>
                <strong>联赛模板指数榜</strong>
                <small>平局、让平、大小球、冷门四个筛选指数</small>
              </div>
              <span>只作筛选 · 不直接投注</span>
            </header>
            <div class="draw-radar-groups">
              <article v-for="group in leagueModelGroups" :key="group.key">
                <div class="draw-radar-title">
                  <strong>{{ group.title }}</strong>
                  <small>Top {{ group.items.length }}</small>
                </div>
                <button
                  v-for="(item, index) in group.items"
                  :key="`${group.key}-${item.match_id}`"
                  type="button"
                  @click="goToDetail(item.match_id)"
                >
                  <i class="draw-radar-rank">{{ index + 1 }}</i>
                  <span class="draw-radar-match">
                    <b>{{ dailyMatch(item.match_id).match_number }}</b>
                    <span>
                      {{ dailyMatch(item.match_id).home_team }}
                      vs
                      {{ dailyMatch(item.match_id).away_team }}
                    </span>
                    <small>
                      {{ item.league_label || item.league }} · {{ item.selection }}
                      <template v-if="item.score_templates?.length">
                        · {{ item.score_templates.join(' / ') }}
                      </template>
                    </small>
                  </span>
                  <span class="draw-radar-decision league-index-value">
                    <i :class="leagueIndexClass(item.index)">指数</i>
                    <b>{{ item.index ?? '--' }}分</b>
                  </span>
                  <span class="draw-radar-metrics">
                    <i>{{ item.style }}</i>
                    <i v-if="item.conditions?.length">{{ item.conditions[0] }}</i>
                  </span>
                  <small class="draw-radar-reason">{{ item.reason }}</small>
                </button>
              </article>
            </div>
            <p>{{ faeDailyAi.daily_summary?.league_model_rankings?.policy }}</p>
          </section>

          <div v-if="dailyPoolGroups.length" class="daily-pools">
            <section
              v-for="group in dailyPoolGroups"
              :key="group.key"
              :class="{ 'two-option-pool': group.key === 'two_option_core' }"
            >
              <h2>{{ group.title }}</h2>
              <button
                v-for="item in group.items"
                :key="`${group.key}-${item.match_id}`"
                type="button"
                @click="goToDetail(item.match_id)"
              >
                <span>
                  <b>{{ dailyMatch(item.match_id).match_number }}</b>
                  {{ dailyMatch(item.match_id).home_team }} vs {{ dailyMatch(item.match_id).away_team }}
                </span>
                <span class="daily-pool-meta">
                  <i v-if="item.role">{{ item.role }}</i>
                  <strong v-if="group.key === 'two_option_core'">{{ item.selection_text }}</strong>
                  <strong v-else-if="item.rating">{{ starText(item.rating) }}</strong>
                </span>
                <small>{{ displayDailyText(item.reason) }}</small>
              </button>
            </section>
          </div>

          <section
            v-if="visibleTwoOptionCombinations.length"
            class="daily-ai-combos two-option-combos"
          >
            <h2>双选 × 概率锚点</h2>
            <article
              v-for="(combo, index) in visibleTwoOptionCombinations"
              :key="`two-option-combo-${index}`"
            >
              <header>
                <b>{{ combo.play }}</b>
                <span>最低中奖路径 {{ combo.minimum_path_odds }} 倍</span>
              </header>
              <button type="button" @click="goToDetail(combo.double_pick.match_id)">
                <span>
                  {{ dailyMatch(combo.double_pick.match_id).match_number }}
                  {{ dailyMatch(combo.double_pick.match_id).home_team }} vs
                  {{ dailyMatch(combo.double_pick.match_id).away_team }}
                </span>
                <strong>{{ combo.double_pick.selection_text }}</strong>
              </button>
              <button type="button" @click="goToDetail(combo.anchor_pick.match_id)">
                <span>
                  {{ dailyMatch(combo.anchor_pick.match_id).match_number }}
                  {{ dailyMatch(combo.anchor_pick.match_id).home_team }} vs
                  {{ dailyMatch(combo.anchor_pick.match_id).away_team }}
                </span>
                <strong>
                  {{ combo.anchor_pick.selection }} @{{ combo.anchor_pick.odds }}
                </strong>
              </button>
              <small class="two-option-combo-note">两条路径各 1 注，不代表保证盈利</small>
            </article>
          </section>

          <section
            v-if="visibleDailyCombinations.length"
            class="daily-ai-combos"
          >
            <h2>AI 推荐 2 / 3 关</h2>
            <article
              v-for="(combo, index) in visibleDailyCombinations"
              :key="`daily-combo-${index}`"
            >
              <header><b>{{ combo.play }}</b><span>{{ displayDailyText(combo.reason) }}</span></header>
              <button
                v-for="pick in combo.picks"
                :key="`${index}-${pick.match_id}`"
                type="button"
                @click="goToDetail(pick.match_id)"
              >
                <span>
                  {{ dailyMatch(pick.match_id).match_number }}
                  {{ dailyMatch(pick.match_id).home_team }} vs {{ dailyMatch(pick.match_id).away_team }}
                </span>
                <strong>{{ pick.selection }}</strong>
              </button>
            </article>
          </section>
          <section v-else class="daily-ai-combos daily-ai-combos-empty">
            <h2>AI 推荐 2 / 3 关</h2>
            <p>今日没有同时达到门槛的平局与让平单选候选，不强行凑单选组合；双选请看上方“双选核心”。</p>
          </section>

          <section v-if="shouldShowDailyMatchList && visibleDailyMatches.length" class="daily-match-section">
            <header class="daily-match-section-title">
              <div>
                <h2>{{ hasOfficialDailyRecommendations ? '比赛推荐' : '逐场观察列表' }}</h2>
                <small>
                  {{ hasOfficialDailyRecommendations
                    ? '每场保留主选、次选和风险原因，未过正式门槛的场次只标记为观察级。'
                    : '今天没有达到正式门槛的推荐，以下比赛保留研判结论和风险原因。'
                  }}
                </small>
              </div>
              <span>{{ visibleDailyMatches.length }} 场</span>
            </header>
            <details
              v-for="item in visibleDailyMatches"
              :key="item.match_id"
              class="daily-match-card"
              @toggle="toggleDailyMatch($event, item.match_id)"
            >
              <summary>
                <span class="daily-match-info">
                  <b>{{ item.match_number }}</b>
                  <span>{{ item.home_team }}<i>VS</i>{{ item.away_team }}</span>
                  <small>{{ item.league }} · {{ formatMatchTime(item.match_time) }}</small>
                </span>
                <span class="daily-selection-pair">
                  <span class="daily-choice-stack">
                    <span class="daily-pick-choice primary">
                      <i>主选</i>
                      <em>
                        <b>{{ dailyDisplayPrimary(item) }}</b>
                        <small v-if="dailyDisplayOdds(item, dailyDisplayPrimary(item))">
                          @{{ dailyDisplayOdds(item, dailyDisplayPrimary(item)) }}
                        </small>
                      </em>
                    </span>
                    <span class="daily-pick-choice secondary">
                      <i>次选</i>
                      <em>
                        <b>{{ dailyDisplaySecondary(item) }}</b>
                        <small v-if="dailyDisplayOdds(item, dailyDisplaySecondary(item))">
                          @{{ dailyDisplayOdds(item, dailyDisplaySecondary(item)) }}
                        </small>
                      </em>
                    </span>
                  </span>
                  <strong>
                    {{ item.analysis?.star_text || starText(item.analysis?.rating) }}
                    <i v-if="item.analysis?.two_option_recommendation?.actionable">双选</i>
                    <i v-else-if="item.analysis?.no_bet">观察级</i>
                  </strong>
                  <span class="daily-pick-notes">
                    <i>倾向 {{ item.analysis?.predicted_result || '观望' }}</i>
                    <i v-if="item.analysis?.handicap_play && item.analysis.handicap_play !== '观望'">
                      让球 {{ item.analysis.handicap_play }}
                    </i>
                  </span>
                </span>
              </summary>
              <div v-if="expandedDailyMatches.has(String(item.match_id))" class="daily-match-body">
                <div
                  v-if="item.analysis?.consistency_guard?.triggered"
                  class="daily-guardrail-note"
                >
                  <strong>一致性护栏已生效</strong>
                  <span>
                    AI 原选 {{ item.analysis.model_primary_play }}，
                    正式推荐按 {{ item.analysis.primary_play }} 记录与复盘
                  </span>
                </div>
                <p class="daily-match-verdict">{{ item.analysis?.verdict }}</p>
                <div class="daily-odds-snapshot">
                  <p><span>欧赔</span><b>{{ triplet(item.input_snapshot?.euro?.current) }}</b></p>
                  <p><span>亚盘</span><b>{{ triplet(item.input_snapshot?.asian?.current) }}</b></p>
                  <p>
                    <span>竞彩 {{ signedHandicap(item.input_snapshot?.sporttery_handicap?.value) }}</span>
                    <b>{{ triplet(item.input_snapshot?.sporttery_handicap?.current) }}</b>
                  </p>
                  <p><span>大小球</span><b>{{ totalTriplet(item.input_snapshot?.total?.current) }}</b></p>
                </div>
                <div class="daily-value-grid">
                  <p><span>FAE概率</span><b>{{ item.analysis?.prediction_probability ?? '--' }}%</b></p>
                  <p><span>市场概率</span><b>{{ item.analysis?.market_implied_probability ?? '--' }}%</b></p>
                  <p><span>价值指数</span><b>{{ item.analysis?.value_score ?? '--' }}分</b></p>
                  <p><span>盘口可信</span><b>{{ item.analysis?.market_confidence?.score ?? '--' }}分</b></p>
                  <p><span>投注分</span><b>{{ item.analysis?.bet_score ?? '--' }}分</b></p>
                  <p>
                    <span>策略</span>
                    <b :class="{ 'no-bet-text': item.analysis?.no_bet && !item.analysis?.two_option_recommendation?.actionable }">
                      {{ item.analysis?.two_option_recommendation?.actionable
                        ? '双选可考虑'
                        : item.analysis?.no_bet
                          ? '观察降级'
                          : (item.analysis?.decision || '可考虑')
                      }}
                    </b>
                  </p>
                </div>
                <div
                  v-if="item.analysis?.two_option_recommendation?.actionable"
                  class="daily-guardrail-note"
                >
                  <strong>高覆盖双选</strong>
                  <span>
                    {{ item.analysis.two_option_recommendation.selection_text }} ·
                    覆盖分 {{ item.analysis.two_option_recommendation.coverage_score }} ·
                    {{ item.analysis.two_option_recommendation.reason }}
                  </span>
                </div>
                <HistoricalGoalMarginCard
                  :model="item.input_snapshot?.historical_goal_margin_model"
                  :calibration="item.analysis?.historical_calibration"
                />
                <div v-if="matchRadarRows(item).length" class="match-draw-radar">
                  <strong>本场平 / 让平雷达</strong>
                  <p v-for="radar in matchRadarRows(item)" :key="radar.model_key">
                    <span>
                      <b>{{ radar.selection }}</b>
                      <i :class="radar.tier">{{ radarTierLabel(radar.tier) }}</i>
                    </span>
                    <em>{{ radar.reason }}</em>
                  </p>
                </div>
                <div class="daily-market-grid">
                  <p v-for="market in dailyMarkets" :key="market.key">
                    <span>{{ market.label }}</span>
                    <b>{{ item.analysis?.market_analysis?.[market.key] || '输入不足' }}</b>
                  </p>
                </div>
                <div v-if="item.analysis?.evidence?.length" class="daily-reason-list">
                  <strong>核心依据</strong>
                  <p v-for="reason in item.analysis.evidence" :key="reason">✓ {{ reason }}</p>
                </div>
                <div v-if="item.analysis?.risks?.length" class="daily-reason-list risks">
                  <strong>风险</strong>
                  <p v-for="risk in item.analysis.risks" :key="risk">⚠ {{ risk }}</p>
                </div>
                <footer>
                  <span>参考比分</span>
                  <b>{{ item.analysis?.score_candidates?.join('　') || '暂无' }}</b>
                  <button type="button" @click="goToDetail(item.match_id)">比赛详情</button>
                </footer>
              </div>
            </details>
          </section>

          <p class="daily-ai-meta">
            研判时间 {{ formatAiTime(faeDailyAi.generated_at) }} ·
            平/让平策略 {{ drawPolicyText(faeDailyAi.draw_selection_policy || 'conservative') }} ·
            每场结果已独立写入数据库 · 不包含隐藏思维链
          </p>
        </section>

        <div v-else class="recommendation-state daily-ai-empty">
          <strong>当天还没有火山全日研判</strong>
          <p v-if="!dailyAiConfigured">请先在服务器配置 ARK_API_KEY。</p>
          <p v-else>系统会在每日定时时间自动运行，也可由管理账号立即生成。</p>
          <button
            v-if="dailyAiCanManage && dailyAiConfigured"
            type="button"
            :disabled="dailyAiBusy"
            @click="runDailyAi(false)"
          >
            {{ dailyAiBusy ? '正在分析全部比赛…' : '运行今日研判' }}
          </button>
        </div>
        <p v-if="dailyAiMessage" class="skill-action-message">{{ dailyAiMessage }}</p>
        <p v-if="dailyAiError" class="skill-action-message error">{{ dailyAiError }}</p>
      </template>

      <template v-else-if="activeSection === 'review'">
        <div v-if="reviewLoading && !faeReview" class="recommendation-state">
          正在加载赛后复盘…
        </div>
        <section v-else class="review-panel">
          <header class="panel-heading review-panel-heading">
            <div>
              <strong>AI 研判主复盘</strong>
              <small>确定性结算 + 火山方舟深度诊断，AI 建议不直接修改正式权重</small>
            </div>
            <div class="review-heading-actions">
              <span v-if="faeReview">{{ faeReview.completed ? '已完成' : `待定${faeReview.pending_matches}场` }}</span>
              <span v-else>等待赛果</span>
              <button
                v-if="dailyAiCanManage && faeReview"
                type="button"
                :disabled="reviewAiBusy"
                @click="runAiReview(true)"
              >
                {{ reviewAiBusy ? '复盘中…' : '重新AI复盘' }}
              </button>
            </div>
          </header>
          <p v-if="reviewAiMessage" class="review-ai-message">{{ reviewAiMessage }}</p>
          <p v-if="reviewAiError" class="review-ai-message error">{{ reviewAiError }}</p>

          <div class="review-stats-grid">
            <article>
              <span>累计单场</span>
              <strong>{{ faeStats.singles?.hit_rate || 0 }}%</strong>
              <small>{{ faeStats.singles?.hits || 0 }}/{{ faeStats.singles?.settled || 0 }} 命中</small>
            </article>
            <article>
              <span>累计单场ROI</span>
              <strong :class="metricClass(faeStats.singles?.roi)">{{ signedMetric(faeStats.singles?.roi) }}%</strong>
              <small>按每场1单位模拟</small>
            </article>
            <article>
              <span>累计让球参考</span>
              <strong>{{ faeStats.handicap?.hit_rate || 0 }}%</strong>
              <small>{{ faeStats.handicap?.hits || 0 }}/{{ faeStats.handicap?.settled || 0 }} 命中</small>
            </article>
            <article>
              <span>让球参考ROI</span>
              <strong :class="metricClass(faeStats.handicap?.roi)">{{ signedMetric(faeStats.handicap?.roi) }}%</strong>
              <small>让胜 / 让平 / 让负独立结算</small>
            </article>
            <article>
              <span>双选覆盖</span>
              <strong>{{ faeStats.two_option?.overall?.hit_rate || 0 }}%</strong>
              <small>{{ faeStats.two_option?.overall?.hits || 0 }}/{{ faeStats.two_option?.overall?.settled || 0 }} 覆盖</small>
            </article>
            <article>
              <span>让球双选</span>
              <strong>{{ faeStats.two_option?.handicap?.hit_rate || 0 }}%</strong>
              <small>{{ faeStats.two_option?.handicap?.hits || 0 }}/{{ faeStats.two_option?.handicap?.settled || 0 }} 覆盖</small>
            </article>
            <article>
              <span>2串1命中</span>
              <strong>{{ faeStats.by_play?.['2串1']?.hit_rate || 0 }}%</strong>
              <small>{{ faeStats.by_play?.['2串1']?.hits || 0 }}/{{ faeStats.by_play?.['2串1']?.settled || 0 }}</small>
            </article>
            <article>
              <span>3串1命中</span>
              <strong>{{ faeStats.by_play?.['3串1']?.hit_rate || 0 }}%</strong>
              <small>{{ faeStats.by_play?.['3串1']?.hits || 0 }}/{{ faeStats.by_play?.['3串1']?.settled || 0 }}</small>
            </article>
            <article>
              <span>平局雷达</span>
              <strong>{{ faeStats.draw_radar?.ordinary_draw?.hit_rate || 0 }}%</strong>
              <small>
                {{ faeStats.draw_radar?.ordinary_draw?.hits || 0 }}/{{ faeStats.draw_radar?.ordinary_draw?.settled || 0 }}
                核心与观察独立结算
              </small>
            </article>
            <article>
              <span>让平雷达</span>
              <strong>{{ faeStats.draw_radar?.handicap_draw?.hit_rate || 0 }}%</strong>
              <small>
                {{ faeStats.draw_radar?.handicap_draw?.hits || 0 }}/{{ faeStats.draw_radar?.handicap_draw?.settled || 0 }}
                核心与观察独立结算
              </small>
            </article>
          </div>

          <section v-if="faeBacktest" class="shadow-backtest-card">
            <header>
              <div>
                <strong>版本影子回测</strong>
                <small>
                  不可变赛前快照 · {{ faeBacktest.source_dates?.length || 0 }} 个比赛日
                </small>
              </div>
              <button
                v-if="dailyAiCanManage"
                type="button"
                :disabled="backtestBusy"
                @click="refreshBacktest"
              >{{ backtestBusy ? '回测中…' : '刷新' }}</button>
            </header>
            <div class="shadow-version-grid">
              <article>
                <span>基线 v{{ faeBacktest.baseline_version }}</span>
                <strong>{{ faeBacktest.baseline?.hits || 0 }}/{{ faeBacktest.baseline?.settled || 0 }}</strong>
                <small>
                  ROI
                  <b :class="metricClass(faeBacktest.baseline?.roi)">
                    {{ signedMetric(faeBacktest.baseline?.roi) }}%
                  </b>
                  · 回撤 {{ faeBacktest.baseline?.max_drawdown_units || 0 }}
                </small>
              </article>
              <article class="candidate">
                <span>候选 v{{ faeBacktest.candidate_version }}</span>
                <strong>{{ faeBacktest.candidate?.hits || 0 }}/{{ faeBacktest.candidate?.settled || 0 }}</strong>
                <small>
                  ROI
                  <b :class="metricClass(faeBacktest.candidate?.roi)">
                    {{ signedMetric(faeBacktest.candidate?.roi) }}%
                  </b>
                  · 回撤 {{ faeBacktest.candidate?.max_drawdown_units || 0 }}
                </small>
              </article>
            </div>
            <footer>
              <span :class="faeBacktest.release_guard?.status">
                {{ faeBacktest.release_guard?.can_promote ? '达到候选门槛' : '继续影子观察' }}
              </span>
              <p>{{ faeBacktest.release_guard?.reasons?.slice(0, 2).join('；') }}</p>
              <small>
                候选较基线：推荐 {{ signedMetric(faeBacktest.comparison?.recommendation_delta) }} 场，
                命中率 {{ signedMetric(faeBacktest.comparison?.hit_rate_delta) }}%，
                ROI {{ signedMetric(faeBacktest.comparison?.roi_delta) }}%
              </small>
              <small>
                样本外验证从 {{ faeBacktest.validation_start_date }} 起：
                {{ faeBacktest.validation?.candidate?.settled || 0 }}/{{ faeBacktest.release_guard?.minimum_settled || 30 }} 场
              </small>
            </footer>
            <p v-if="backtestError" class="review-ai-message error">{{ backtestError }}</p>
          </section>

          <div class="strategy-review-grid">
            <article v-for="selection in ['平局', '让平']" :key="selection">
              <header>
                <strong>{{ selection }}</strong>
                <span>权重 {{ strategyWeight(selection).weight }}</span>
              </header>
              <div>
                <p><span>命中率</span><b>{{ strategyStats(selection).hit_rate || 0 }}%</b></p>
                <p><span>ROI</span><b :class="metricClass(strategyStats(selection).roi)">{{ signedMetric(strategyStats(selection).roi) }}%</b></p>
                <p><span>样本</span><b>{{ strategyStats(selection).settled || 0 }}</b></p>
              </div>
              <small>{{ weightActionLabel(strategyWeight(selection).action) }}</small>
            </article>
          </div>

          <div
            v-if="faeReview?.summary?.guardrail_conflicts"
            class="review-guardrail-summary"
          >
            <strong>模型一致性审计</strong>
            <span>
              当天 {{ faeReview.summary.guardrail_conflicts }} 场触发强冲突护栏；
              正式结算使用护栏后的选择，原始 AI 选择完整保留。
            </span>
          </div>

          <section v-if="faeReview?.ai_deep_review" class="ai-deep-review">
            <header>
              <div>
                <span class="fae-badge">AI</span>
                <div>
                  <strong>火山方舟深度复盘</strong>
                  <small>
                    已复盘 {{ faeReview.ai_deep_review.coverage?.reviewed_matches || faeReview.ai_deep_review.coverage?.settled_matches || 0 }}
                    / {{ faeReview.ai_deep_review.coverage?.total_matches || 0 }} 场 ·
                    观察降级 {{ faeReview.ai_deep_review.coverage?.no_bet_matches || 0 }} 场 ·
                    让球参考 {{ faeReview.ai_deep_review.coverage?.settled_handicap_references || 0 }} 项 ·
                    {{ faeReview.ai_deep_review.model }}
                  </small>
                </div>
              </div>
              <span>{{ faeReview.ai_deep_review.coverage?.review_completed ? '终版' : '阶段版' }}</span>
            </header>

            <p class="ai-review-conclusion">
              {{ displayReviewText(faeReview.ai_deep_review.summary?.conclusion) }}
            </p>

            <div class="ai-review-points">
              <article>
                <strong>做对了什么</strong>
                <p
                  v-for="item in faeReview.ai_deep_review.summary?.what_worked || []"
                  :key="`worked-${item}`"
                >✓ {{ displayReviewText(item) }}</p>
                <small v-if="!faeReview.ai_deep_review.summary?.what_worked?.length">暂无足够样本</small>
              </article>
              <article>
                <strong>需要修正</strong>
                <p
                  v-for="item in faeReview.ai_deep_review.summary?.what_failed || []"
                  :key="`failed-${item}`"
                >× {{ displayReviewText(item) }}</p>
                <small v-if="!faeReview.ai_deep_review.summary?.what_failed?.length">暂无明确错误模式</small>
              </article>
            </div>

            <div class="ai-market-lessons">
              <article
                v-for="(text, key) in faeReview.ai_deep_review.market_lessons || {}"
                :key="key"
              >
                <strong>{{ aiScopeLabel(key) }}</strong>
                <p>{{ displayReviewText(text) }}</p>
              </article>
            </div>

            <section
              v-if="faeReview.ai_deep_review.learning_candidates?.length"
              class="ai-learning-candidates"
            >
              <h2>AI 调权候选 <small>只记录，需历史样本验证后才能发布</small></h2>
              <article
                v-for="(candidate, index) in faeReview.ai_deep_review.learning_candidates"
                :key="`${candidate.scope}-${candidate.target}-${index}`"
              >
                <header>
                  <strong>{{ aiScopeLabel(candidate.scope) }} · {{ candidate.target }}</strong>
                  <span :class="candidate.action">
                    {{ aiActionLabel(candidate.action, candidate.delta) }}
                  </span>
                </header>
                <p>{{ displayReviewText(candidate.reason) }}</p>
                <small>
                  置信度 {{ aiConfidenceLabel(candidate.confidence) }} ·
                  至少 {{ candidate.minimum_samples }} 个历史样本后验证
                </small>
              </article>
            </section>

            <section class="ai-match-diagnoses">
              <h2>逐场 AI 诊断</h2>
              <details
                v-for="item in faeReview.ai_deep_review.matches || []"
                :key="`ai-review-${item.match_id}`"
              >
                <summary>
                  <span>
                    <b>{{ item.match_number }}</b>
                    {{ item.home_team }} vs {{ item.away_team }}
                  </span>
                  <em>
                    {{ item.selection_text || '观望' }}
                    · {{ item.result_score }}
                  </em>
                  <i :class="aiVerdictClass(item.verdict)">{{ displayAiVerdict(item.verdict) }}</i>
                </summary>
                <small v-if="item.handicap_selection_text" class="ai-handicap-verdict">
                  竞彩参考 {{ item.handicap_selection_text }} · {{ item.handicap_verdict }}
                </small>
                <small v-if="item.two_option_verdict" class="ai-handicap-verdict">
                  双选覆盖 · {{ item.two_option_verdict }}
                </small>
                <p>{{ displayReviewText(item.diagnosis) }}</p>
                <ul v-if="item.correct_signals?.length">
                  <li v-for="signal in item.correct_signals" :key="`correct-${signal}`">
                    <b>有效</b>{{ signal }}
                  </li>
                </ul>
                <ul v-if="item.missed_signals?.length">
                  <li v-for="signal in item.missed_signals" :key="`missed-${signal}`">
                    <b class="missed">遗漏</b>{{ signal }}
                  </li>
                </ul>
                <footer v-if="item.counterfactual">
                  <strong>下次修正</strong>{{ item.counterfactual }}
                </footer>
              </details>
            </section>

            <small class="ai-review-governance">
              {{ faeReview.ai_deep_review.governance?.note }}
            </small>
          </section>
          <div
            v-else-if="faeReview && reviewableMatchCount"
            class="ai-review-waiting"
          >
            <strong>确定性结算已完成</strong>
            <span>
              {{ faeReview.ai_deep_review_error || faeReview.ai_deep_review_unavailable || 'AI 深度复盘将在下一轮自动任务中生成' }}
            </span>
          </div>

          <template v-if="faeReview">
            <section class="daily-review-block">
              <h2>
                <span>全量逐场复盘</span>
                <small>
                  主选 {{ faeReview.summary?.singles?.hits || 0 }}/{{ faeReview.summary?.singles?.settled || 0 }}
                  · 观察降级 {{ reviewNoBetCount }}
                </small>
              </h2>
              <button
                v-for="item in faeReview.match_results"
                :key="item.match_id"
                type="button"
                @click="goToDetail(item.match_id)"
              >
                <span class="review-match-info">
                  <b>{{ item.match_number }}</b>{{ item.home_team }} vs {{ item.away_team }}
                </span>
                <span class="review-pick-info">
                  <strong>
                    {{ item.selection_text || item.selection }}
                  </strong>
                  <i :class="item.status">{{ reviewStatusLabel(item.status, item.no_bet) }}</i>
                  <small v-if="item.guardrail_triggered" class="guarded-pick">
                    AI原选{{ item.model_selection }}
                  </small>
                </span>
                <span class="review-result-info">
                  <em>
                    {{ item.result_score || '待赛' }}
                    <small v-if="item.odds">@{{ item.odds }}</small>
                  </em>
                  <small v-if="isSettledStatus(item.status)">{{ signedMetric(item.profit) }}单位</small>
                </span>
              </button>
            </section>

            <section v-if="twoOptionReviewRows.length" class="daily-review-block">
              <h2>
                <span>双选覆盖复盘</span>
                <small>
                  总体 {{ faeReview.summary?.two_option?.overall?.hits || 0 }}/{{ faeReview.summary?.two_option?.overall?.settled || 0 }}
                  · 让球 {{ faeReview.summary?.two_option?.handicap?.hits || 0 }}/{{ faeReview.summary?.two_option?.handicap?.settled || 0 }}
                </small>
              </h2>
              <button
                v-for="item in twoOptionReviewRows"
                :key="`two-option-${item.match_id}`"
                type="button"
                @click="goToDetail(item.match_id)"
              >
                <span class="review-match-info">
                  <b>{{ item.match_number }}</b>{{ item.home_team }} vs {{ item.away_team }}
                </span>
                <span class="review-pick-info">
                  <strong>{{ item.selection_text || item.selection }}</strong>
                  <i :class="item.status">
                    {{ item.status === 'hit' ? `✓ 覆盖${item.hit_selection_text ? ` ${item.hit_selection_text}` : ''}` : reviewStatusLabel(item.status) }}
                  </i>
                </span>
                <span class="review-result-info">
                  <em>{{ item.result_score || '待赛' }}</em>
                  <small>
                    {{ twoOptionHitOdds(item) ? `@${twoOptionHitOdds(item)}` : (item.market || '双选') }}
                  </small>
                </span>
              </button>
            </section>

            <section v-if="faeReview.handicap_results?.length" class="daily-review-block">
              <h2>
                <span>竞彩让球参考结果</span>
                <small>{{ faeReview.summary?.handicap?.hits || 0 }}/{{ faeReview.summary?.handicap?.settled || 0 }}</small>
              </h2>
              <button
                v-for="item in faeReview.handicap_results"
                :key="`handicap-${item.match_id}`"
                type="button"
                @click="goToDetail(item.match_id)"
              >
                <span class="review-match-info">
                  <b>{{ item.match_number }}</b>{{ item.home_team }} vs {{ item.away_team }}
                </span>
                <span class="review-pick-info">
                  <strong>{{ item.selection_text || item.selection }}</strong>
                  <i :class="item.status">{{ reviewStatusLabel(item.status) }}</i>
                </span>
                <span class="review-result-info">
                  <em>
                    {{ item.result_score || '待赛' }}
                    <small v-if="item.odds">@{{ item.odds }}</small>
                  </em>
                  <small v-if="isSettledStatus(item.status)">{{ signedMetric(item.profit) }}单位</small>
                </span>
              </button>
            </section>

            <section class="daily-review-block combo-review-block">
              <h2><span>当天组合结果</span><small>2串1 / 3串1</small></h2>
              <article v-for="item in faeReview.combo_results" :key="item.key">
                <header>
                  <strong>{{ item.play }}</strong>
                  <i :class="item.status">{{ reviewStatusLabel(item.status) }}</i>
                </header>
                <p v-for="pick in item.picks" :key="`${item.key}-${pick.match_id}`">
                  <span>{{ pick.match_number }} {{ pick.selection_text || pick.selection }}</span>
                  <b>@{{ pick.odds }}</b>
                </p>
                <footer>
                  <span>组合赔率 {{ item.combined_odds || '--' }}</span>
                  <b v-if="isSettledStatus(item.status)">{{ signedMetric(item.profit) }}单位</b>
                </footer>
              </article>
              <p v-if="!faeReview.combo_results?.length" class="combo-review-empty">
                当天没有达到推荐门槛的组合，因此没有组合结算记录。
              </p>
            </section>
          </template>
          <div v-else class="review-pending">
            <strong>尚未生成 AI 主复盘</strong>
            <p>仅使用全场均未开赛时保存的 AI 研判；系统每15分钟自动结算单场和2、3关组合。</p>
          </div>
        </section>
      </template>

      <template v-else>
        <div v-if="skillsLoading && !skillsLoaded" class="recommendation-state">
          正在加载 Skill 版本…
        </div>
        <section v-else class="skill-center-panel">
          <header class="panel-heading skill-center-heading">
            <div>
              <strong>FAE Skill 版本中心</strong>
              <small>复盘生成候选 · 历史验证 · 手动发布 · 一键回滚</small>
            </div>
            <span>{{ faeSkills.active?.length || 0 }} 个线上 Skill</span>
          </header>

          <div class="skill-center-actions">
            <div>
              <strong>安全发布模式</strong>
              <small>
                每个 Skill 至少 {{ faeSkills.minimum_samples || 10 }} 个总样本，
                发布后再积累 {{ faeSkills.minimum_new_samples || 10 }} 个新样本
              </small>
            </div>
            <button
              type="button"
              :disabled="skillBusy || !faeSkills.can_manage"
              @click="generateSkillCandidates"
            >
              {{ skillBusy ? '评估中…' : '扫描复盘' }}
            </button>
          </div>

          <p v-if="!faeSkills.can_manage" class="skill-permission-note">
            当前账号可查看迭代记录；发布操作仅对 FAE 管理账号开放。
          </p>
          <p v-if="skillMessage" class="skill-action-message">{{ skillMessage }}</p>
          <p v-if="skillError" class="skill-action-message error">{{ skillError }}</p>

          <section class="skill-candidate-section">
            <h2>
              <span>待发布候选</span>
              <b>{{ faeSkills.candidates?.length || 0 }}</b>
            </h2>
            <div v-if="faeSkills.candidates?.length" class="skill-candidate-list">
              <article v-for="candidate in faeSkills.candidates" :key="candidate.candidate_id">
                <header>
                  <div>
                    <strong>{{ candidate.label }}</strong>
                    <span>v{{ candidate.parent_version }} → v{{ candidate.proposed_version }}</span>
                  </div>
                  <em>验证通过</em>
                </header>
                <div class="skill-change-list">
                  <p v-for="change in candidate.changes" :key="change.parameter">
                    <span>{{ skillChangeName(change) }}</span>
                    <b>{{ change.previous }} → {{ change.proposed }}</b>
                    <small>{{ change.reason }}</small>
                  </p>
                </div>
                <div class="skill-evaluation">
                  <span>历史重放样本 <b>{{ candidate.evaluation?.sample_count || 0 }}</b></span>
                  <span>效用提升 <b>+{{ formatImprovement(candidate.evaluation?.improvement) }}</b></span>
                </div>
                <footer>
                  <small>{{ candidate.evaluation?.limitations }}</small>
                  <button
                    type="button"
                    :disabled="skillBusy || !faeSkills.can_manage"
                    @click="openSkillConfirmation('promote', candidate)"
                  >发布 v{{ candidate.proposed_version }}</button>
                </footer>
              </article>
            </div>
            <div v-else class="skill-empty">
              暂无通过验证的候选版本，系统会继续累计赛后样本。
            </div>
          </section>

          <section class="active-skill-section">
            <h2>线上 Skill</h2>
            <div class="active-skill-grid">
              <article v-for="skill in faeSkills.active" :key="skill.skill_id">
                <header>
                  <span>{{ skill.label }}</span>
                  <b>v{{ skill.version }}</b>
                </header>
                <p>{{ skill.description }}</p>
                <div class="skill-parameter-chips">
                  <span v-for="item in skillParameters(skill)" :key="item.key">
                    {{ item.label }} <b>{{ item.value }}</b>
                  </span>
                </div>
                <footer>
                  <small>{{ formatSkillTime(skill.activated_at) }} 发布</small>
                  <button
                    v-if="skill.can_rollback"
                    type="button"
                    :disabled="skillBusy || !faeSkills.can_manage"
                    @click="openSkillConfirmation('rollback', skill)"
                  >回滚</button>
                </footer>
              </article>
            </div>
          </section>

          <section v-if="faeSkills.deployments?.length" class="skill-history-section">
            <h2>最近发布记录</h2>
            <p v-for="item in faeSkills.deployments.slice(0, 8)" :key="item.deployment_id">
              <span>{{ item.label }}</span>
              <b>{{ item.action === 'rollback' ? '回滚' : '发布' }}至 v{{ item.version }}</b>
              <small>{{ formatSkillTime(item.deployed_at) }}</small>
            </p>
          </section>
        </section>
      </template>

      <p v-if="error && hasData" class="inline-error">{{ error }}</p>
    </main>

    <div
      v-if="skillConfirmation"
      class="skill-confirm-overlay"
      role="presentation"
      @click.self="closeSkillConfirmation"
    >
      <section
        class="skill-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="skill-confirm-title"
      >
        <header>
          <span class="fae-badge">FAE</span>
          <div>
            <strong id="skill-confirm-title">
              {{ skillConfirmation.type === 'promote' ? '发布 Skill' : '回滚 Skill' }}
            </strong>
            <small>{{ skillConfirmation.item.label }}</small>
          </div>
        </header>
        <div class="skill-confirm-version">
          <template v-if="skillConfirmation.type === 'promote'">
            <span>v{{ skillConfirmation.item.parent_version }}</span>
            <i>→</i>
            <b>v{{ skillConfirmation.item.proposed_version }}</b>
          </template>
          <template v-else>
            <span>当前 v{{ skillConfirmation.item.version }}</span>
            <i>→</i>
            <b>上一版本</b>
          </template>
        </div>
        <p v-if="skillConfirmation.type === 'promote'">
          发布后，后续新生成的研判会立即使用这组参数；已经保存的历史研判不会被改写。
        </p>
        <p v-else>
          回滚后，后续新生成的研判将恢复使用上一版本参数。
        </p>
        <p v-if="skillError" class="skill-confirm-error">{{ skillError }}</p>
        <footer>
          <button type="button" :disabled="skillBusy" @click="closeSkillConfirmation">
            取消
          </button>
          <button
            type="button"
            class="primary"
            :disabled="skillBusy"
            @click="confirmSkillAction"
          >
            <template v-if="skillBusy">
              {{ skillConfirmation.type === 'promote' ? '发布中…' : '回滚中…' }}
            </template>
            <template v-else>
              {{ skillConfirmation.type === 'promote' ? '确认发布' : '确认回滚' }}
            </template>
          </button>
        </footer>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AccountButton from '../components/AccountButton.vue'
import HistoricalGoalMarginCard from '../components/HistoricalGoalMarginCard.vue'
import { openAuth } from '../auth'

const router = useRouter()
const loading = ref(false)
const error = ref('')
const activeSection = ref('dailyAi')
const faeDailyAi = ref(null)
const dailyAiConfigured = ref(false)
const dailyAiCanManage = ref(false)
const dailyAiBusy = ref(false)
const dailyAiMessage = ref('')
const dailyAiError = ref('')
const expandedDailyMatches = ref(new Set())
const faeReview = ref(null)
const reviewLoading = ref(false)
const reviewLoadedDate = ref('')
const reviewAiBusy = ref(false)
const reviewAiMessage = ref('')
const reviewAiError = ref('')
const faeStats = ref({})
const faeBacktest = ref(null)
const backtestBusy = ref(false)
const backtestError = ref('')
const faeSkills = ref({ active: [], candidates: [], deployments: [] })
const skillsLoading = ref(false)
const skillsLoaded = ref(false)
const skillBusy = ref(false)
const skillMessage = ref('')
const skillError = ref('')
const skillConfirmation = ref(null)
let requestController = null
let reviewRequestController = null
let skillsRequestController = null

const formatDateParam = date => {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const today = new Date()
const selectedDate = ref(formatDateParam(today))
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

const showModelPanels = false
const supervisedShadow = computed(() => (
  faeDailyAi.value?.daily_summary?.supervised_shadow || {}
))
const dailyPoolLabels = {
  two_option_core: '双选核心',
  draw: '平局精选',
  handicap_draw: '让平精选'
}
const dailyPoolGroups = computed(() => {
  const source = faeDailyAi.value?.daily_summary?.pools || {}
  const pools = Object.fromEntries(
    Object.entries(source).map(([key, items]) => [
      key,
      [...(items || [])].filter(item => (
        isVisibleDailyMatch(dailyMatch(item.match_id))
      ))
    ])
  )
  return Object.entries(dailyPoolLabels)
    .map(([key, title]) => ({ key, title, items: pools[key] || [] }))
    .filter(group => group.items.length)
})
const hasOfficialDailyRecommendations = computed(() => (
  dailyPoolGroups.value.length > 0 ||
  visibleDailyCombinations.value.length > 0 ||
  visibleDailyMatches.value.some(item => item.analysis?.two_option_recommendation?.actionable)
))
const shouldShowDailyMatchList = computed(() => (
  visibleDailyMatches.value.length > 0
))
const dailyMatchMap = computed(() => Object.fromEntries(
  (faeDailyAi.value?.matches || []).map(item => [String(item.match_id), item])
))
const drawRadarGroups = computed(() => {
  const radar = faeDailyAi.value?.daily_summary?.draw_radar || {}
  return [
    {
      key: 'ordinary_draw',
      title: '最可能平局',
      excluded: radar.excluded_count?.ordinary_draw || 0,
      items: radar.ordinary_draw || []
    },
    {
      key: 'handicap_draw',
      title: '最可能让平',
      excluded: radar.excluded_count?.handicap_draw || 0,
      items: radar.handicap_draw || []
    }
  ].map(group => ({
    ...group,
    items: group.items.filter(item => (
      isVisibleDailyMatch(dailyMatch(item.match_id))
    )).sort((left, right) => (
      Number(right.probability || 0) - Number(left.probability || 0)
    )).slice(0, 3)
  })).filter(group => group.items.length)
})

function supervisedProbability(matchId, key) {
  const rows = supervisedShadow.value?.[key] || []
  const item = rows.find(row => String(row.match_id) === String(matchId))
  const value = Number(item?.ranking_probability)
  return Number.isFinite(value) ? value : null
}
const leagueModelGroups = computed(() => {
  const source = faeDailyAi.value?.daily_summary?.league_model_rankings || {}
  return [
    { key: 'handicap_draw', title: '让平指数', items: source.handicap_draw || [] },
    { key: 'draw', title: '平局指数', items: source.draw || [] },
    { key: 'total', title: '大小球指数', items: source.total || [] },
    { key: 'upset', title: '冷门指数', items: source.upset || [] }
  ].map(group => ({
    ...group,
    items: group.items.filter(item => (
      isVisibleDailyMatch(dailyMatch(item.match_id))
    )).sort((left, right) => (
      Number(right.index || 0) - Number(left.index || 0)
    ))
  })).filter(group => group.items.length)
})
const upsetWarningItems = computed(() => (
  faeDailyAi.value?.daily_summary?.upset_warning?.items || []
).filter(item => (
  isVisibleDailyMatch(dailyMatch(item.match_id))
)).sort((left, right) => (
  Number(right.score || 0) - Number(left.score || 0)
)))
const oddsBandGroups = computed(() => {
  const source = faeDailyAi.value?.daily_summary?.odds_band_indicators || {}
  return [
    { key: 'favorite_heat', title: '热门过热指数', items: source.favorite_heat || [] },
    { key: 'underdog_upset', title: '下盘爆冷指数', items: source.underdog_upset || [] },
    { key: 'handicap_draw_value', title: '让平价值指数', items: source.handicap_draw_value || [] }
  ].map(group => ({
    ...group,
    items: group.items.filter(item => (
      isVisibleDailyMatch(dailyMatch(item.match_id))
    )).sort((left, right) => (
      Number(right.index || 0) - Number(left.index || 0)
    ))
  })).filter(group => group.items.length)
})
const historicalModelCount = computed(() => (
  faeDailyAi.value?.matches || []
).filter(item => item.input_snapshot?.historical_goal_margin_model?.version).length)
const historicalCalibrationCount = computed(() => (
  faeDailyAi.value?.matches || []
).filter(item => item.analysis?.historical_calibration?.applied).length)
const visibleDailyMatches = computed(() => (
  faeDailyAi.value?.matches || []
).filter(isVisibleDailyMatch))
const visibleDailyCombinations = computed(() => (
  faeDailyAi.value?.daily_summary?.recommended_combinations || []
).filter(combo => (
  (combo.picks || []).length > 0
  && (combo.picks || []).every(pick => (
    isVisibleDailyMatch(dailyMatch(pick.match_id))
  ))
)))
const visibleTwoOptionCombinations = computed(() => (
  faeDailyAi.value?.daily_summary?.two_option_combinations || []
).filter(combo => (
  combo.double_pick?.match_id
  && combo.anchor_pick?.match_id
  && isVisibleDailyMatch(dailyMatch(combo.double_pick.match_id))
  && isVisibleDailyMatch(dailyMatch(combo.anchor_pick.match_id))
)).slice(0, 3))
const reviewNoBetCount = computed(() => (
  faeReview.value?.match_results || []
).filter(item => item.no_bet).length)
const twoOptionReviewRows = computed(() => {
  const byMatch = new Map()
  for (const item of faeReview.value?.two_option_results || []) {
    const matchId = String(item.match_id || '')
    if (!matchId) continue
    const existing = byMatch.get(matchId)
    if (!existing || twoOptionRowRank(item) > twoOptionRowRank(existing)) {
      byMatch.set(matchId, item)
    }
  }
  return Array.from(byMatch.values())
})
const reviewableMatchCount = computed(() => (
  faeReview.value?.match_results || []
).filter(item => (
  ['hit', 'miss', 'push'].includes(item.status)
  || (item.status === 'skipped' && item.result_score)
)).length)
const dailyMarkets = [
  { key: 'euro', label: '欧赔方向' },
  { key: 'asian', label: '亚盘升深' },
  { key: 'sporttery', label: '竞彩让球' },
  { key: 'total', label: '大小球' },
  { key: 'consistency', label: '市场一致性' }
]
const hasData = computed(() =>
  faeDailyAi.value
  || faeReview.value
  || faeSkills.value.active?.length
)

async function fetchData() {
  requestController?.abort()
  requestController = new AbortController()
  const controller = requestController
  loading.value = true
  error.value = ''
  try {
    const date = encodeURIComponent(selectedDate.value)
    const dailyAiResponse = await fetch(`/api/fae/daily-ai?date=${date}&compact=1`, {
      signal: controller.signal,
      credentials: 'same-origin'
    })
    const dailyAiPayload = await dailyAiResponse.json()
    faeDailyAi.value = dailyAiResponse.ok && dailyAiPayload.success
      ? dailyAiPayload.data
      : null
    dailyAiConfigured.value = Boolean(
      dailyAiPayload.configured
    )
    dailyAiCanManage.value = Boolean(dailyAiPayload.can_manage)
    if (activeSection.value === 'review') void loadReviewData(true)
    if (activeSection.value === 'skills') void loadSkillsData(true)
  } catch (e) {
    if (e.name === 'AbortError') return
    error.value = e.message || '推荐加载失败，请稍后重试'
  } finally {
    if (requestController === controller) {
      loading.value = false
      requestController = null
    }
  }
}

async function loadReviewData(force = false) {
  const dateValue = selectedDate.value
  if (!force && reviewLoadedDate.value === dateValue) return
  reviewRequestController?.abort()
  reviewRequestController = new AbortController()
  const controller = reviewRequestController
  reviewLoading.value = true
  reviewAiError.value = ''
  try {
    const date = encodeURIComponent(dateValue)
    const [reviewResponse, statsResponse, backtestResponse] = await Promise.all([
      fetch(`/api/fae/daily-ai/review?date=${date}`, { signal: controller.signal }),
      fetch('/api/fae/daily-ai/review/stats', { signal: controller.signal }),
      fetch('/api/fae/backtest?days=28', {
        signal: controller.signal,
        credentials: 'same-origin'
      })
    ])
    const [reviewPayload, statsPayload, backtestPayload] = await Promise.all([
      reviewResponse.json(),
      statsResponse.json(),
      backtestResponse.json().catch(() => ({}))
    ])
    if (selectedDate.value !== dateValue) return
    faeReview.value = reviewResponse.ok && reviewPayload.success
      ? reviewPayload.data
      : null
    faeStats.value = statsResponse.ok && statsPayload.success
      ? (statsPayload.data || {})
      : {}
    faeBacktest.value = backtestResponse.ok && backtestPayload.success
      ? backtestPayload.data
      : null
    reviewLoadedDate.value = dateValue
    dailyAiConfigured.value = Boolean(
      dailyAiConfigured.value || reviewPayload.ai_review_configured
    )
  } catch (e) {
    if (e.name === 'AbortError') return
    reviewAiError.value = e.message || '赛后复盘加载失败'
  } finally {
    if (reviewRequestController === controller) {
      reviewLoading.value = false
      reviewRequestController = null
    }
  }
}

async function refreshBacktest() {
  backtestBusy.value = true
  backtestError.value = ''
  try {
    const response = await fetch('/api/fae/backtest', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ days: 28 })
    })
    const payload = await response.json().catch(() => ({}))
    if (response.status === 401) {
      openAuth('login')
      throw new Error('请先登录管理账号')
    }
    if (!response.ok || !payload.success) {
      throw new Error(payload.message || '影子回测失败')
    }
    faeBacktest.value = payload.data || null
  } catch (e) {
    backtestError.value = e.message || '影子回测失败'
  } finally {
    backtestBusy.value = false
  }
}

async function loadSkillsData(force = false) {
  if (!force && skillsLoaded.value) return
  skillsRequestController?.abort()
  skillsRequestController = new AbortController()
  const controller = skillsRequestController
  skillsLoading.value = true
  skillError.value = ''
  try {
    const response = await fetch('/api/fae/skills', { signal: controller.signal })
    const payload = await response.json()
    faeSkills.value = response.ok && payload.success
      ? (payload.data || { active: [], candidates: [], deployments: [] })
      : { active: [], candidates: [], deployments: [] }
    skillsLoaded.value = true
  } catch (e) {
    if (e.name === 'AbortError') return
    skillError.value = e.message || 'Skill 版本加载失败'
  } finally {
    if (skillsRequestController === controller) {
      skillsLoading.value = false
      skillsRequestController = null
    }
  }
}

function selectSection(section) {
  activeSection.value = section
  if (section === 'review') void loadReviewData()
  if (section === 'skills') void loadSkillsData()
}

function selectDate(date) {
  if (selectedDate.value === date) return
  selectedDate.value = date
  faeReview.value = null
  reviewLoadedDate.value = ''
  faeDailyAi.value = null
  expandedDailyMatches.value = new Set()
  dailyAiMessage.value = ''
  dailyAiError.value = ''
  reviewAiMessage.value = ''
  reviewAiError.value = ''
  fetchData()
}

function toggleDailyMatch(event, matchId) {
  const next = new Set(expandedDailyMatches.value)
  const key = String(matchId || '')
  if (event.currentTarget?.open) next.add(key)
  else next.delete(key)
  expandedDailyMatches.value = next
}

function goToDetail(matchId) {
  router.push({
    name: 'match-detail',
    params: { id: matchId },
    query: { from: 'recommendations' }
  })
}

function starText(stars) {
  const value = Math.max(0, Math.min(5, Number(stars) || 0))
  const count = Math.floor(value)
  const text = `${'★'.repeat(count)}${'☆'.repeat(5 - count)}`
  return Number.isInteger(value) ? text : `${text} · ${value}星`
}

function dailyMatch(matchId) {
  return dailyMatchMap.value[String(matchId)] || {}
}

function isVisibleDailyMatch(item) {
  const status = item?.current_status
  if (status === null || status === undefined || status === '') return true
  return Number(status) === 0
}

const DAILY_RESULT_PLAY_LABELS = new Set(['主胜', '平局', '客胜', '让胜', '让平', '让负'])

function normalizeDailyPlay(value) {
  const text = String(value || '').trim()
  if (!text || text === '观望' || text === '不下注' || text === '观察') return ''
  return text
}

function isDailyResultPlay(value) {
  return DAILY_RESULT_PLAY_LABELS.has(normalizeDailyPlay(value))
}

function dailyCandidateScores(item) {
  const scores = item?.input_snapshot?.fae_core?.recommendation?.category_scores || []
  return [...scores]
    .filter(score => isDailyResultPlay(score?.label))
    .sort((left, right) => {
      const leftNoBet = left?.no_bet ? 1 : 0
      const rightNoBet = right?.no_bet ? 1 : 0
      if (leftNoBet !== rightNoBet) return leftNoBet - rightNoBet
      return Number(right?.bet_score || 0) - Number(left?.bet_score || 0)
    })
}

function dailyDisplayPlays(item) {
  const analysis = item?.analysis || {}
  const candidates = [
    analysis.primary_play,
    analysis.secondary_play,
    analysis.handicap_play,
    analysis.predicted_result,
    ...dailyCandidateScores(item).map(score => score.label)
  ]
    .map(normalizeDailyPlay)
    .filter(isDailyResultPlay)

  const unique = []
  for (const candidate of candidates) {
    if (!unique.includes(candidate)) unique.push(candidate)
  }
  return unique
}

function dailyDisplayPrimary(item) {
  return dailyDisplayPlays(item)[0] || '观望'
}

function dailyDisplaySecondary(item) {
  return dailyDisplayPlays(item).find(play => play !== dailyDisplayPrimary(item)) || '观望'
}

function dailyDisplayOdds(item, play) {
  const label = normalizeDailyPlay(play)
  if (!label || label === '观望') return ''
  const score = dailyCandidateScores(item).find(candidate => candidate?.label === label)
  return formatPickOdds(score?.odds)
}

function formatPickOdds(value) {
  const number = Number(value)
  if (!Number.isFinite(number) || number <= 0) return ''
  return number.toFixed(2).replace(/\.?0+$/, '')
}

function radarTierLabel(tier) {
  return tier === 'core' ? '核心' : tier === 'watch' ? '观察' : '排除'
}

function radarPercent(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '--'
  return `${Number.isInteger(parsed) ? parsed : parsed.toFixed(1)}%`
}

function shortRadarReason(item) {
  const role = item?.role_signals?.find(Boolean)
  if (role) return role
  const text = displayDailyText(item?.reason || item?.definition || '')
  const parts = text
    .replace(/达到独立核心门槛。?/g, '')
    .replace(/仅列观察，不进入组合。?/g, '')
    .split(/[；。]/)
    .map(value => value.trim())
    .filter(Boolean)
  return parts[0] || item?.definition || '点击查看完整依据'
}

function matchRadarRows(item) {
  const radar = item?.analysis?.draw_radar || {}
  return ['ordinary_draw', 'handicap_draw']
    .map(key => radar[key])
    .filter(row => row && row.tier !== 'exclude')
}

function displayDailyText(value) {
  let text = String(value || '')
  const matches = Object.values(dailyMatchMap.value)
    .filter(item => item?.match_id && item?.match_number)
    .sort((left, right) => String(right.match_id).length - String(left.match_id).length)
  for (const item of matches) {
    text = text.split(String(item.match_id)).join(String(item.match_number))
  }
  return displayReviewText(text)
}

function displayReviewText(value) {
  return String(value || '')
    .replace(/不下注过保守/g, '观察过保守')
    .replace(/不下注合理/g, '风控有效')
    .replace(/不下注/g, '观察降级')
}

function displayAiVerdict(value) {
  if (value === '不下注合理') return '风控有效'
  if (value === '不下注过保守') return '观察过保守'
  return value || '观望复盘'
}

function triplet(values) {
  if (!Array.isArray(values)) return '--'
  return values.map(value => value ?? '--').join(' / ')
}

function totalTriplet(values) {
  if (!Array.isArray(values)) return '--'
  const normalized = [...values]
  if (normalized.length > 1) normalized[1] = formatTotalLine(normalized[1])
  return triplet(normalized)
}

function formatTotalLine(value) {
  const raw = String(value ?? '').replace(/[↑↓升降]/g, '').trim()
  if (!raw) return value ?? '--'
  const slashParts = raw.split('/').map(item => Number(item.trim()))
  if (slashParts.length > 1 && slashParts.every(Number.isFinite)) {
    return Number((slashParts.reduce((sum, item) => sum + item, 0) / slashParts.length).toFixed(2))
  }
  const lowHigh = raw.match(/^([1-4])([1-4]\.5)$/)
  if (lowHigh) {
    return Number(((Number(lowHigh[1]) + Number(lowHigh[2])) / 2).toFixed(2))
  }
  const highLow = raw.match(/^([1-4]\.5)([1-4])$/)
  if (highLow) {
    return Number(((Number(highLow[1]) + Number(highLow[2])) / 2).toFixed(2))
  }
  return value ?? '--'
}

function signedHandicap(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '让球'
  return number > 0 ? `+${number}` : String(number)
}

function formatMatchTime(value) {
  const text = String(value || '')
  return text.length >= 5 ? text.slice(-5) : text
}

function drawPolicyText(policy) {
  const normalized = String(policy || 'conservative')
  const labels = {
    conservative: '保守',
    balanced: '平衡',
    aggressive: '激进'
  }
  return labels[normalized] || labels.conservative
}

function formatAiTime(value) {
  if (!value) return '未知时间'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

async function runDailyAi(force) {
  dailyAiBusy.value = true
  dailyAiMessage.value = ''
  dailyAiError.value = ''
  try {
    const response = await fetch('/api/fae/daily-ai', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        date: selectedDate.value,
        force,
        compact: true,
        push_wecom: true
      })
    })
    const payload = await response.json().catch(() => ({}))
    if (response.status === 401) {
      openAuth('login')
      throw new Error('请先登录管理账号')
    }
    if (!response.ok || !payload.success) {
      throw new Error(payload.message || '全日研判运行失败')
    }
    faeDailyAi.value = payload.data || null
    faeReview.value = null
    reviewLoadedDate.value = ''
    dailyAiMessage.value = payload.message || '全日研判已完成'
  } catch (e) {
    dailyAiError.value = e.message || '全日研判运行失败'
  } finally {
    dailyAiBusy.value = false
  }
}

async function runAiReview(forceAi) {
  reviewAiBusy.value = true
  reviewAiMessage.value = ''
  reviewAiError.value = ''
  try {
    const response = await fetch('/api/fae/daily-ai/review', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        date: selectedDate.value,
        force_ai: Boolean(forceAi)
      })
    })
    const payload = await response.json().catch(() => ({}))
    if (response.status === 401) {
      openAuth('login')
      throw new Error('请先登录管理账号')
    }
    if (!response.ok || !payload.success) {
      throw new Error(payload.message || 'AI 深度复盘运行失败')
    }
    faeReview.value = payload.data || null
    reviewLoadedDate.value = selectedDate.value
    reviewAiMessage.value = payload.message || 'AI 深度复盘已完成'
  } catch (e) {
    reviewAiError.value = e.message || 'AI 深度复盘运行失败'
  } finally {
    reviewAiBusy.value = false
  }
}

function signedMetric(value) {
  const number = Number(value || 0)
  return `${number > 0 ? '+' : ''}${number}`
}

function metricClass(value) {
  const number = Number(value || 0)
  return number > 0 ? 'positive' : number < 0 ? 'negative' : ''
}

function leagueIndexClass(value) {
  const number = Number(value || 0)
  if (number >= 75) return 'core'
  if (number >= 65) return 'watch'
  return ''
}

function upsetLevelClass(value) {
  const number = Number(value || 0)
  if (number >= 80) return 'core'
  if (number >= 60) return 'watch'
  return ''
}

function strategyWeight(selection) {
  return faeStats.value?.strategy_weights?.[selection] || {
    weight: 1,
    action: 'hold'
  }
}

function strategyStats(selection) {
  if (selection === '让平') {
    return faeStats.value?.handicap_by_selection?.[selection] || {}
  }
  return faeStats.value?.by_selection?.[selection] || {}
}

function weightActionLabel(action) {
  if (action === 'increase') return '近期表现有效，等待 Skill 候选发布'
  if (action === 'decrease') return '近期表现偏低，等待 Skill 候选发布'
  return '样本积累中，线上权重保持不变'
}

function reviewStatusLabel(status, noBet = false) {
  if (status === 'hit') return '✓ 命中'
  if (status === 'miss') return '× 未中'
  if (status === 'push') return '走盘'
  if (status === 'skipped') return noBet ? '观察降级' : '观望'
  if (status === 'ungraded') return '未结算'
  return '待赛'
}

function twoOptionHitOdds(item) {
  if (item?.hit_odds != null && item.hit_odds !== '') return item.hit_odds
  const hit = (item?.selection_results || []).find(row => row?.status === 'hit')
  return hit?.odds ?? ''
}

function twoOptionRowRank(item) {
  const statusRank = {
    hit: 40,
    push: 30,
    pending: 20,
    miss: 10,
    ungraded: 0,
    skipped: 0
  }[item?.status] ?? 0
  const marketRank = item?.result_type === 'two_option_handicap' ? 2 : 1
  const oddsRank = twoOptionHitOdds(item) ? 1 : 0
  return statusRank + marketRank + oddsRank
}

function aiScopeLabel(scope) {
  return {
    euro: '欧赔',
    asian: '亚盘',
    sporttery: '竞彩让球',
    total: '大小球',
    consistency: '市场一致性',
    risk: '风险控制',
    guardrail: '一致性护栏',
    combination: '组合构建'
  }[scope] || scope
}

function aiActionLabel(action, delta) {
  if (action === 'increase') return `建议升权 +${Math.abs(Number(delta) || 0)}`
  if (action === 'decrease') return `建议降权 -${Math.abs(Number(delta) || 0)}`
  return '保持观察'
}

function aiConfidenceLabel(value) {
  return { low: '低', medium: '中', high: '高' }[value] || '低'
}

function aiVerdictClass(value) {
  if (value === '判断有效' || value === '不下注合理') return 'good'
  if (
    value === '命中但过程有风险'
    || value === '走盘'
    || value === '不下注过保守'
    || value === '观望复盘'
  ) return 'warning'
  return 'bad'
}

function isSettledStatus(status) {
  return ['hit', 'miss', 'push'].includes(status)
}

async function fetchSkills() {
  const response = await fetch('/api/fae/skills', { credentials: 'same-origin' })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok || !payload.success) {
    throw new Error(payload.message || 'Skill 版本加载失败')
  }
  faeSkills.value = payload.data || { active: [], candidates: [], deployments: [] }
}

async function runSkillAction(url, body, successMessage) {
  skillBusy.value = true
  skillError.value = ''
  skillMessage.value = ''
  try {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    })
    const payload = await response.json().catch(() => ({}))
    if (response.status === 401) {
      openAuth('login')
      throw new Error('请先登录管理账号')
    }
    if (!response.ok || !payload.success) {
      throw new Error(payload.message || 'Skill 操作失败')
    }
    await fetchSkills()
    skillMessage.value = payload.message || successMessage
    return true
  } catch (e) {
    skillError.value = e.message || 'Skill 操作失败'
    return false
  } finally {
    skillBusy.value = false
  }
}

function generateSkillCandidates() {
  return runSkillAction(
    '/api/fae/skills/candidates',
    {},
    '复盘样本扫描完成'
  )
}

function openSkillConfirmation(type, item) {
  if (skillBusy.value || !faeSkills.value.can_manage) return
  skillError.value = ''
  skillMessage.value = ''
  skillConfirmation.value = { type, item }
}

function closeSkillConfirmation() {
  if (skillBusy.value) return
  skillConfirmation.value = null
  skillError.value = ''
}

async function confirmSkillAction() {
  const confirmation = skillConfirmation.value
  if (!confirmation || skillBusy.value) return
  const { type, item } = confirmation
  const success = type === 'promote'
    ? await runSkillAction(
      `/api/fae/skills/${item.skill_id}/promote`,
      { candidate_id: item.candidate_id },
      `${item.label} 已发布`
    )
    : await runSkillAction(
      `/api/fae/skills/${item.skill_id}/rollback`,
      {},
      `${item.label} 已回滚`
    )
  if (success) skillConfirmation.value = null
}

function skillChangeName(change) {
  const labels = {
    'euro-home-support': '主胜欧赔支持',
    'euro-away-support': '客胜欧赔支持',
    'euro-draw-support': '平局欧赔支持',
    'market-consensus-home': '主队市场共识',
    'market-consensus-away': '客队市场共识',
    'asian-line-home': '亚盘主队方向',
    'asian-line-away': '亚盘客队方向',
    'asian-home-water': '主队水位',
    'asian-away-water': '客队水位',
    'total-over': '大球信号',
    'total-under': '小球信号',
    'recent-form-home': '主队近期状态',
    'recent-form-away': '客队近期状态',
    'history-home': '主队交锋',
    'history-away': '客队交锋',
    'hot-overheat': '热门过热',
    'handicap-drop': '退盘风险',
    'deep-high-water': '深盘高水',
    'cup-variance': '杯赛波动',
    'data-quality': '数据质量'
  }
  return change.selection || labels[change.rule_id] || change.rule_id || change.parameter
}

function skillParameters(skill) {
  const parameters = skill.parameters || {}
  const values = parameters.rule_weights || parameters.strategy_weights || {}
  return Object.entries(values).map(([key, value]) => ({
    key,
    label: skillChangeName({ rule_id: key, selection: parameters.strategy_weights ? key : '' }),
    value
  }))
}

function formatImprovement(value) {
  return Number(value || 0).toFixed(3)
}

function formatSkillTime(value) {
  if (!value) return '初始'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? String(value).slice(0, 10)
    : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

onMounted(fetchData)
onBeforeUnmount(() => {
  requestController?.abort()
  reviewRequestController?.abort()
  skillsRequestController?.abort()
})
</script>

<style scoped>
.recommendations-page {
  min-height: 100vh;
  padding-bottom: 86px;
  background: #f5f6f8;
}

.recommendations-page .header-title {
  font-size: 16px;
}

.recommendation-dates {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  overflow-x: auto;
  background: #fff;
  scrollbar-width: none;
}

.recommendation-dates::-webkit-scrollbar {
  display: none;
}

.recommendation-dates button {
  flex: 0 0 68px;
  min-height: 50px;
  color: #666;
  background: #fff;
  border: 1px solid #efd8dc;
  border-radius: 10px;
}

.recommendation-dates button.active {
  color: #fff;
  background: #e53955;
  border-color: #e53955;
  box-shadow: 0 3px 8px rgb(229 57 85 / 20%);
}

.recommendation-dates span,
.recommendation-dates small {
  display: block;
}

.recommendation-dates span {
  font-size: 13px;
  font-weight: 600;
}

.recommendation-dates small {
  margin-top: 3px;
  font-size: 12px;
}

.recommendation-dates i {
  display: inline-grid;
  width: 14px;
  height: 14px;
  margin-left: 2px;
  place-items: center;
  color: #e53955;
  font-size: 11px;
  font-style: normal;
  background: #fff;
  border-radius: 50%;
}

.recommendations-content {
  padding: 10px 12px 0;
}

.recommendation-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 13px;
  background: linear-gradient(120deg, #fff8fa, #fff);
  border: 1px solid #eedde0;
  border-radius: 13px;
}

.recommendation-hero > div {
  display: flex;
  align-items: center;
  gap: 9px;
}

.recommendation-hero strong,
.recommendation-hero small {
  display: block;
}

.recommendation-hero strong {
  color: #333;
  font-size: 15px;
}

.recommendation-hero small {
  margin-top: 2px;
  color: #999;
  font-size: 11px;
}

.fae-badge {
  padding: 5px 8px;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  background: #2d3142;
  border-radius: 5px;
}

.recommendation-hero > button {
  width: 30px;
  height: 30px;
  color: #777;
  font-size: 22px;
  background: #fff;
  border: 1px solid #eee;
  border-radius: 50%;
}

.rotating {
  animation: recommendation-rotate 1s linear infinite;
}

@keyframes recommendation-rotate {
  to { transform: rotate(360deg); }
}

.recommendation-tabs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  margin: 10px 0;
  padding: 4px;
  background: #e9eaed;
  border-radius: 10px;
}

.recommendation-tabs button {
  min-height: 36px;
  color: #777;
  font-size: 13px;
  font-weight: 600;
  background: transparent;
  border: 0;
  border-radius: 8px;
}

.recommendation-tabs button.active {
  color: #e53955;
  background: #fff;
  box-shadow: 0 2px 6px rgb(30 36 44 / 8%);
}

.review-panel,
.skill-center-panel,
.daily-ai-panel {
  overflow: hidden;
  background: #fff;
  border: 1px solid #eadde0;
  border-radius: 13px;
  box-shadow: 0 5px 18px rgb(57 31 37 / 6%);
}

.daily-ai-heading > button,
.daily-ai-empty > button {
  padding: 7px 10px;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  background: #e53955;
  border: 0;
  border-radius: 15px;
}

.daily-ai-heading > button:disabled,
.daily-ai-empty > button:disabled {
  opacity: .55;
}

.daily-ai-summary {
  margin: 10px;
  padding: 12px;
  background: linear-gradient(135deg, #fff5f7, #fff);
  border: 1px solid #f2dfe3;
  border-radius: 10px;
}

.daily-ai-kicker {
  color: #e53955;
  font-size: 12px;
  font-weight: 700;
}

.daily-ai-summary > p {
  margin: 7px 0 0;
  color: #4d4d52;
  font-size: 12px;
  line-height: 1.65;
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}

.daily-ai-warnings {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 9px;
}

.daily-ai-warnings span {
  padding: 4px 6px;
  color: #9b6328;
  font-size: 11px;
  line-height: 1.35;
  background: #fff7e9;
  border-radius: 5px;
}

.daily-review-memory {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 3px 7px;
  margin-top: 9px;
  padding: 7px 8px;
  color: #68626f;
  font-size: 10px;
  line-height: 1.45;
  background: #f4f1f8;
  border-radius: 7px;
}

.daily-review-memory strong {
  color: #6a4f82;
  font-size: 10px;
}

.daily-review-memory span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.daily-review-memory small {
  grid-column: 1 / 3;
  color: #9992a1;
  font-size: 9px;
}

.goal-margin-loaded {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 9px;
  padding: 7px 8px;
  color: #4c665d;
  font-size: 10px;
  line-height: 1.35;
  background: #edf8f3;
  border-radius: 7px;
}

.goal-margin-loaded strong {
  color: #237157;
  font-size: 10px;
}

.goal-margin-loaded span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.goal-margin-loaded small {
  grid-column: 1 / 3;
  color: #7e958d;
  font-size: 9px;
}

.supervised-shadow-loaded {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  color: #65577d;
  background: #f5f0fb;
}

.supervised-shadow-loaded strong {
  color: #6d4f91;
}

.supervised-shadow-loaded small {
  color: #978ba7;
}

.draw-radar-panel {
  margin: 0 10px 10px;
  overflow: hidden;
  background: linear-gradient(145deg, #fffafb, #fff);
  border: 1px solid #f0dfe3;
  border-radius: 10px;
}

.draw-radar-panel > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 10px;
  border-bottom: 1px solid #f2e7e9;
}

.draw-radar-panel > header strong,
.draw-radar-panel > header small {
  display: block;
}

.draw-radar-panel > header strong {
  color: #30343b;
  font-size: 13px;
}

.draw-radar-panel > header small,
.draw-radar-panel > header > span {
  margin-top: 2px;
  color: #9992a1;
  font-size: 9px;
}

.draw-radar-panel > header > span {
  flex: 0 0 auto;
  color: #b9792e;
}

.draw-radar-groups {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  padding: 9px;
}

.draw-radar-groups article {
  min-width: 0;
  padding: 0 8px;
  background: #fafafa;
  border: 1px solid #eee7e9;
  border-radius: 8px;
}

.upset-warning-list article {
  grid-column: 1 / -1;
}

.draw-radar-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0 5px;
}

.draw-radar-title strong {
  color: #e53955;
  font-size: 12px;
}

.draw-radar-title small {
  color: #aaa;
  font-size: 9px;
}

.draw-radar-groups button {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr) auto;
  gap: 4px 7px;
  width: 100%;
  padding: 7px 0;
  text-align: left;
  background: none;
  border: 0;
  border-top: 1px dashed #e9dfe2;
}

.draw-radar-rank {
  display: inline-grid;
  width: 20px;
  height: 20px;
  place-items: center;
  color: #e53955;
  font-size: 10px;
  font-style: normal;
  font-weight: 700;
  background: #fff1f4;
  border: 1px solid #f4d8de;
  border-radius: 50%;
}

.draw-radar-match {
  min-width: 0;
}

.draw-radar-match > b,
.draw-radar-match > span,
.draw-radar-match > small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.draw-radar-match > b {
  color: #333;
  font-size: 11px;
}

.draw-radar-match > span {
  margin-top: 2px;
  color: #555;
  font-size: 10px;
}

.draw-radar-match > small {
  margin-top: 3px;
  color: #a19aa0;
  font-size: 9px;
}

.draw-radar-decision {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
}

.draw-radar-decision i,
.match-draw-radar i {
  padding: 2px 5px;
  color: #9d6a20;
  font-size: 9px;
  font-style: normal;
  background: #fff4db;
  border-radius: 5px;
}

.draw-radar-decision i.core,
.match-draw-radar i.core {
  color: #fff;
  background: #e53955;
}

.league-index-value i.watch {
  color: #c67812;
  background: #fff0d7;
}

.draw-radar-decision b {
  color: #e53955;
  font-size: 10px;
  white-space: nowrap;
}

.league-model-panel .draw-radar-decision b {
  color: #30343b;
}

.draw-radar-metrics {
  display: flex;
  grid-column: 2 / 4;
  flex-wrap: wrap;
  gap: 3px 7px;
}

.draw-radar-metrics i {
  color: #777;
  font-size: 9px;
  font-style: normal;
}

.draw-radar-metrics i.positive {
  color: #19966c;
}

.draw-radar-metrics i.negative {
  color: #e53955;
}

.draw-radar-metrics i.supervised-probability {
  color: #76529a;
}

.draw-radar-reason {
  grid-column: 2 / 4;
  color: #8f878d;
  font-size: 9px;
  line-height: 1.45;
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.draw-radar-panel > p {
  margin: 0;
  padding: 0 10px 9px;
  color: #a09aa0;
  font-size: 9px;
  line-height: 1.4;
}

.match-draw-radar {
  margin-top: 10px;
  padding: 9px;
  background: #fff9fa;
  border: 1px solid #f0dfe3;
  border-radius: 8px;
}

.match-draw-radar > strong {
  color: #343841;
  font-size: 12px;
}

.match-draw-radar p {
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr);
  gap: 7px;
  margin: 7px 0 0;
  padding-top: 7px;
  border-top: 1px dashed #ecdfe2;
}

.match-draw-radar p > span {
  display: flex;
  align-items: center;
  gap: 4px;
}

.match-draw-radar p b {
  color: #e53955;
  font-size: 11px;
}

.match-draw-radar p em {
  color: #777;
  font-size: 10px;
  font-style: normal;
  line-height: 1.5;
}

.daily-pools {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  padding: 0 10px 10px;
}

.daily-pools section {
  min-width: 0;
  padding: 9px;
  background: #fafafa;
  border: 1px solid #eee7e9;
  border-radius: 9px;
}

.daily-pools section.two-option-pool {
  grid-column: 1 / -1;
  background: linear-gradient(135deg, #fff7f8, #fff);
  border-color: #f4cbd2;
}

.daily-pools section.two-option-pool h2 {
  color: #dc3150;
}

.daily-pools h2,
.daily-ai-combos > h2,
.daily-match-section > h2 {
  margin: 0 0 7px;
  color: #333;
  font-size: 12px;
}

.daily-pools button {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 3px 6px;
  width: 100%;
  padding: 7px 0;
  text-align: left;
  background: none;
  border: 0;
  border-top: 1px dashed #eae2e4;
}

.daily-pools button:first-of-type {
  border-top: 0;
}

.daily-pools button span {
  overflow: hidden;
  color: #555;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.daily-pools button span b {
  margin-right: 4px;
  color: #333;
}

.daily-pools button strong {
  color: #e53955;
  font-size: 12px;
  white-space: nowrap;
}

.daily-pool-meta {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
}

.daily-pool-meta i {
  padding: 1px 4px;
  color: #a16b22;
  font-size: 9px;
  font-style: normal;
  background: #fff6e7;
  border-radius: 4px;
}

.daily-pools button small {
  grid-column: 1 / 3;
  color: #999;
  font-size: 11px;
  line-height: 1.45;
}

.daily-ai-combos,
.daily-match-section {
  margin: 0 10px 10px;
}

.daily-match-section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.daily-match-section-title > div {
  min-width: 0;
}

.daily-match-section-title h2 {
  margin: 0;
  color: #30343b;
  font-size: 14px;
}

.daily-match-section-title small {
  display: block;
  margin-top: 3px;
  color: #969aa1;
  font-size: 10px;
  line-height: 1.35;
}

.daily-match-section-title span {
  flex: 0 0 auto;
  padding: 3px 8px;
  color: #8c9098;
  font-size: 10px;
  background: #f1f2f5;
  border-radius: 10px;
}

.daily-ai-combos article {
  margin-bottom: 7px;
  overflow: hidden;
  border: 1px solid #efdee2;
  border-radius: 9px;
}

.daily-ai-combos-empty {
  padding: 9px;
  color: #969aa1;
  background: #fff;
  border: 1px solid #eee4e6;
  border-radius: 9px;
}

.daily-ai-combos-empty h2 {
  margin: 0 0 5px;
  color: #555;
  font-size: 13px;
}

.daily-ai-combos-empty p,
.combo-review-empty {
  margin: 0;
  color: #999;
  font-size: 11px;
  line-height: 1.55;
}

.combo-review-empty {
  padding: 16px 10px;
  text-align: center;
}

.daily-ai-combos article > header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  background: #fff8f9;
}

.daily-ai-combos header b {
  color: #e53955;
  font-size: 13px;
  white-space: nowrap;
}

.daily-ai-combos header span {
  color: #999;
  font-size: 11px;
  line-height: 1.35;
}

.daily-ai-combos article > button {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 7px;
  width: 100%;
  padding: 7px 9px;
  text-align: left;
  background: #fff;
  border: 0;
  border-top: 1px dashed #f0e6e8;
}

.daily-ai-combos article > button span {
  overflow: hidden;
  color: #666;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.daily-ai-combos article > button strong {
  color: #e53955;
  font-size: 13px;
}

.two-option-combos > h2 {
  margin: 0 0 7px;
  color: #30343b;
  font-size: 14px;
}

.two-option-combo-note {
  display: block;
  padding: 5px 9px 7px;
  color: #aaa;
  font-size: 10px;
  background: #fff;
}

.daily-match-card {
  margin-bottom: 9px;
  overflow: hidden;
  background: #fff;
  border: 1px solid #ece7e9;
  border-radius: 12px;
  box-shadow: 0 3px 12px rgb(28 34 43 / 4%);
}

.daily-match-card > summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(104px, auto);
  gap: 10px;
  align-items: center;
  min-height: 82px;
  padding: 11px 12px;
  cursor: pointer;
  list-style: none;
}

.daily-match-card > summary::-webkit-details-marker {
  display: none;
}

.daily-match-info {
  min-width: 0;
}

.daily-match-info > b {
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  padding: 0 7px;
  color: #e53955;
  font-size: 11px;
  background: #fff1f4;
  border-radius: 6px;
}

.daily-match-info > span {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  margin-top: 7px;
  overflow: hidden;
  color: #30343b;
  font-size: 13px;
  font-weight: 650;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.daily-match-info > span i {
  color: #b4b6bc;
  font-size: 9px;
  font-style: normal;
  font-weight: 500;
}

.daily-match-card summary small {
  display: block;
  margin-top: 5px;
  color: #a2a5ac;
  font-size: 10px;
}

.daily-selection-pair {
  display: grid;
  gap: 5px;
  justify-items: end;
  min-width: 120px;
}

.daily-choice-stack {
  display: grid;
  gap: 5px;
  justify-items: end;
}

.daily-pick-choice {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 5px;
  min-width: 82px;
}

.daily-pick-choice > i {
  width: 28px;
  color: #a3a6ad;
  font-size: 9px;
  font-style: normal;
  text-align: right;
}

.daily-pick-choice em {
  display: inline-flex;
  align-items: baseline;
  justify-content: center;
  gap: 3px;
  min-width: 58px;
  padding: 4px 8px;
  color: #fff;
  font-size: 12px;
  font-style: normal;
  font-weight: 750;
  text-align: center;
  background: #e53955;
  border-radius: 7px;
}

.daily-pick-choice em b {
  color: inherit;
  font-size: inherit;
  font-weight: inherit;
}

.daily-pick-choice em small {
  color: rgb(255 255 255 / 82%);
  font-size: 9px;
  font-weight: 650;
}

.daily-pick-choice.secondary em {
  color: #e53955;
  background: #fff1f4;
  border: 1px solid #ffd6df;
}

.daily-pick-choice.secondary em small {
  color: #e9778a;
}

.daily-selection-pair > strong {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 5px;
  color: #e53955;
  font-size: 11px;
  letter-spacing: .4px;
  white-space: nowrap;
}

.daily-selection-pair > strong i {
  padding: 1px 5px;
  color: #9a6a16;
  font-size: 9px;
  font-style: normal;
  font-weight: 650;
  letter-spacing: 0;
  background: #fff6df;
  border-radius: 999px;
}

.daily-pick-notes {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 2px 6px;
}

.daily-pick-notes i {
  color: #8a8f96;
  font-size: 9px;
  font-style: normal;
  white-space: nowrap;
}

.daily-match-card[open] > summary {
  background: linear-gradient(135deg, #fffafb, #fff);
  border-bottom: 1px solid #f1e6e8;
}

.daily-match-body {
  padding: 11px;
  background: #fcfcfd;
}

.daily-guardrail-note {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 9px;
  padding: 7px 8px;
  color: #8c5a1f;
  font-size: 11px;
  line-height: 1.4;
  background: #fff6e6;
  border: 1px solid #f2dfbd;
  border-radius: 7px;
}

.daily-guardrail-note strong {
  flex: 0 0 auto;
  color: #c56d13;
}

.daily-match-verdict {
  margin: 0 0 9px;
  color: #555;
  font-size: 13px;
  line-height: 1.65;
}

.daily-odds-snapshot,
.daily-market-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 5px;
}

.daily-odds-snapshot {
  margin-bottom: 9px;
  padding: 7px;
  background: #f7f7f8;
  border-radius: 7px;
}

.daily-odds-snapshot p,
.daily-market-grid p {
  min-width: 0;
  margin: 0;
}

.daily-odds-snapshot span,
.daily-odds-snapshot b {
  display: block;
}

.daily-odds-snapshot span {
  color: #999;
  font-size: 10px;
}

.daily-odds-snapshot b {
  margin-top: 2px;
  overflow: hidden;
  color: #444;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.daily-market-grid p {
  padding: 7px;
  background: #fff;
  border: 1px solid #f0e8ea;
  border-radius: 7px;
}

.daily-market-grid p:last-child {
  grid-column: 1 / 3;
}

.daily-market-grid span,
.daily-market-grid b {
  display: block;
}

.daily-market-grid span {
  color: #e53955;
  font-size: 11px;
  font-weight: 700;
}

.daily-market-grid b {
  margin-top: 4px;
  color: #666;
  font-size: 11px;
  font-weight: 400;
  line-height: 1.5;
}

.daily-reason-list {
  margin-top: 9px;
}

.daily-reason-list > strong {
  color: #444;
  font-size: 12px;
}

.daily-reason-list p {
  margin: 4px 0 0;
  color: #287b60;
  font-size: 11px;
  line-height: 1.45;
}

.daily-reason-list.risks p {
  color: #a66b2c;
}

.daily-match-body > footer {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 9px;
  padding-top: 8px;
  border-top: 1px dashed #eee;
}

.daily-match-body > footer span {
  color: #999;
  font-size: 11px;
}

.daily-match-body > footer b {
  flex: 1;
  color: #e53955;
  font-size: 13px;
}

.daily-match-body > footer button {
  padding: 4px 7px;
  color: #666;
  font-size: 11px;
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 10px;
}

.daily-ai-meta {
  margin: 0;
  padding: 0 10px 10px;
  color: #aaa;
  font-size: 11px;
  text-align: center;
}

.daily-ai-empty {
  align-content: center;
  gap: 8px;
  padding: 20px;
}

.daily-ai-empty strong {
  color: #555;
  font-size: 16px;
}

.daily-ai-empty p {
  margin: 0;
  color: #999;
  font-size: 13px;
  text-align: center;
}

.panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 13px;
  border-bottom: 1px solid #f3e7e9;
}

.panel-heading strong,
.panel-heading small {
  display: block;
}

.panel-heading strong {
  color: #333;
  font-size: 15px;
}

.panel-heading small {
  margin-top: 3px;
  color: #999;
  font-size: 10px;
}

.panel-heading > span {
  color: #e53955;
  font-size: 14px;
  font-weight: 700;
}

.daily-value-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 5px;
  margin-bottom: 9px;
}

.daily-value-grid p {
  margin: 0;
  padding: 7px 5px;
  text-align: center;
  background: #fff8f9;
  border: 1px solid #f3e6e9;
  border-radius: 7px;
}

.daily-value-grid span,
.daily-value-grid b {
  display: block;
}

.daily-value-grid span {
  margin-bottom: 3px;
  color: #999;
  font-size: 10px;
}

.daily-value-grid b {
  color: #444;
  font-size: 12px;
}

.daily-value-grid .no-bet-text {
  color: #e53955;
}

.review-heading-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
}

.review-panel-heading {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
}

.review-panel-heading > div:first-child {
  min-width: 0;
}

.review-heading-actions > span {
  min-width: 44px;
  padding: 5px 7px;
  color: #8b8589;
  font-size: 10px;
  line-height: 1;
  text-align: center;
  white-space: nowrap;
  background: #f4f2f3;
  border-radius: 9px;
}

.review-heading-actions > button {
  min-width: 82px;
  min-height: 34px;
  padding: 6px 10px;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.2;
  white-space: nowrap;
  background: #e53955;
  border: 0;
  border-radius: 17px;
}

.review-heading-actions > button:disabled {
  opacity: .55;
}

.review-ai-message {
  margin: 8px 10px 0;
  padding: 7px 9px;
  color: #16805f;
  font-size: 12px;
  background: #effaf6;
  border-radius: 7px;
}

.review-ai-message.error {
  color: #c3364c;
  background: #fff2f4;
}

.ai-deep-review {
  margin: 0 10px 10px;
  overflow: hidden;
  background: linear-gradient(150deg, #fff8fa 0, #fff 42%);
  border: 1px solid #efdce1;
  border-radius: 10px;
}

.ai-deep-review > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px;
  border-bottom: 1px solid #f4e8eb;
}

.ai-deep-review > header > div {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ai-deep-review > header > div > div {
  display: grid;
  gap: 2px;
}

.ai-deep-review > header strong {
  color: #333;
  font-size: 14px;
}

.ai-deep-review > header small {
  color: #999;
  font-size: 10px;
}

.ai-deep-review > header > span {
  padding: 3px 7px;
  color: #b12c45;
  font-size: 10px;
  background: #ffe9ee;
  border-radius: 9px;
}

.ai-review-conclusion {
  margin: 0;
  padding: 11px 10px;
  color: #555;
  font-size: 13px;
  line-height: 1.7;
}

.ai-review-points {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  padding: 0 10px 10px;
}

.ai-review-points article {
  padding: 9px;
  background: rgb(255 255 255 / 78%);
  border: 1px solid #f1e6e8;
  border-radius: 8px;
}

.ai-review-points strong {
  color: #444;
  font-size: 12px;
}

.ai-review-points p {
  margin: 5px 0 0;
  color: #666;
  font-size: 11px;
  line-height: 1.55;
}

.ai-review-points article:first-child p {
  color: #257a62;
}

.ai-review-points article:last-child p {
  color: #a84a59;
}

.ai-review-points small {
  display: block;
  margin-top: 5px;
  color: #aaa;
  font-size: 10px;
}

.ai-market-lessons {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 6px;
  padding: 0 10px 10px;
}

.ai-market-lessons article {
  padding: 8px;
  background: #faf8f9;
  border-radius: 7px;
}

.ai-market-lessons strong {
  color: #e53955;
  font-size: 11px;
}

.ai-market-lessons p {
  margin: 4px 0 0;
  color: #777;
  font-size: 10px;
  line-height: 1.5;
}

.ai-learning-candidates,
.ai-match-diagnoses {
  margin: 0 10px 10px;
  overflow: hidden;
  background: #fff;
  border: 1px solid #f0e5e7;
  border-radius: 8px;
}

.ai-learning-candidates > h2,
.ai-match-diagnoses > h2 {
  margin: 0;
  padding: 8px 9px;
  color: #444;
  font-size: 12px;
  background: #faf8f9;
}

.ai-learning-candidates > h2 small {
  margin-left: 5px;
  color: #aaa;
  font-size: 9px;
  font-weight: 400;
}

.ai-learning-candidates > article {
  padding: 8px 9px;
  border-top: 1px solid #f5eff0;
}

.ai-learning-candidates article > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.ai-learning-candidates article > header strong {
  color: #555;
  font-size: 11px;
}

.ai-learning-candidates article > header span {
  flex: 0 0 auto;
  padding: 2px 5px;
  color: #777;
  font-size: 9px;
  background: #f1f1f3;
  border-radius: 7px;
}

.ai-learning-candidates article > header span.increase {
  color: #15795c;
  background: #eaf8f3;
}

.ai-learning-candidates article > header span.decrease {
  color: #c23850;
  background: #fff0f3;
}

.ai-learning-candidates article > p {
  margin: 5px 0;
  color: #777;
  font-size: 10px;
  line-height: 1.5;
}

.ai-learning-candidates article > small {
  color: #aaa;
  font-size: 9px;
}

.ai-match-diagnoses details {
  border-top: 1px solid #f5eff0;
}

.ai-match-diagnoses summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 5px;
  align-items: center;
  padding: 8px 9px;
  cursor: pointer;
  list-style: none;
}

.ai-match-diagnoses summary::-webkit-details-marker {
  display: none;
}

.ai-match-diagnoses summary > span {
  overflow: hidden;
  color: #666;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ai-match-diagnoses summary > span b {
  margin-right: 4px;
  color: #444;
}

.ai-match-diagnoses summary > em {
  color: #777;
  font-size: 10px;
  font-style: normal;
}

.ai-match-diagnoses summary > i {
  padding: 2px 5px;
  font-size: 9px;
  font-style: normal;
  border-radius: 7px;
}

.ai-match-diagnoses summary > i.good {
  color: #15795c;
  background: #eaf8f3;
}

.ai-match-diagnoses summary > i.warning {
  color: #a66a16;
  background: #fff6e6;
}

.ai-match-diagnoses summary > i.bad {
  color: #c23850;
  background: #fff0f3;
}

.ai-match-diagnoses .ai-handicap-verdict {
  display: block;
  margin: 0 9px 5px;
  color: #a66a16;
  font-size: 10px;
}

.ai-match-diagnoses details > p {
  margin: 0;
  padding: 2px 9px 8px;
  color: #666;
  font-size: 11px;
  line-height: 1.65;
}

.ai-match-diagnoses ul {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0 9px 7px;
  list-style: none;
}

.ai-match-diagnoses li {
  color: #777;
  font-size: 10px;
  line-height: 1.5;
}

.ai-match-diagnoses li b {
  margin-right: 5px;
  color: #16805f;
}

.ai-match-diagnoses li b.missed {
  color: #c23850;
}

.ai-match-diagnoses footer {
  margin: 0 9px 8px;
  padding: 7px 8px;
  color: #777;
  font-size: 10px;
  line-height: 1.5;
  background: #faf8f9;
  border-radius: 6px;
}

.ai-match-diagnoses footer strong {
  margin-right: 5px;
  color: #e53955;
}

.ai-review-governance {
  display: block;
  padding: 0 10px 10px;
  color: #aaa;
  font-size: 9px;
  line-height: 1.5;
}

.ai-review-waiting {
  display: grid;
  gap: 4px;
  margin: 0 10px 10px;
  padding: 9px 10px;
  background: #faf8f9;
  border-radius: 8px;
}

.ai-review-waiting strong {
  color: #555;
  font-size: 12px;
}

.ai-review-waiting span {
  color: #999;
  font-size: 10px;
}

.review-stats-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  grid-auto-rows: 1fr;
  gap: 7px;
  padding: 9px;
}

.review-stats-grid article {
  display: grid;
  grid-template-rows: 16px 24px minmax(24px, auto);
  min-height: 82px;
  padding: 9px 7px;
  align-content: center;
  text-align: center;
  background: linear-gradient(150deg, #fff, #fcfafb);
  border: 1px solid #eee6e8;
  border-radius: 10px;
}

.review-stats-grid span,
.review-stats-grid strong,
.review-stats-grid small {
  display: block;
}

.review-stats-grid span {
  color: #8c878a;
  font-size: 10px;
  line-height: 16px;
}

.review-stats-grid strong {
  margin-top: 2px;
  color: #333;
  font-size: 18px;
  line-height: 22px;
}

.review-stats-grid small {
  margin-top: 2px;
  color: #aaa4a7;
  font-size: 9px;
  line-height: 1.35;
}

.positive {
  color: #15956f !important;
}

.negative {
  color: #e53955 !important;
}

.shadow-backtest-card {
  margin: 0 9px 9px;
  overflow: hidden;
  background: #fff;
  border: 1px solid #e9e3e5;
  border-radius: 10px;
}

.shadow-backtest-card > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 10px;
  background: #faf8f9;
}

.shadow-backtest-card > header strong,
.shadow-backtest-card > header small {
  display: block;
}

.shadow-backtest-card > header strong {
  color: #414044;
  font-size: 13px;
}

.shadow-backtest-card > header small {
  margin-top: 2px;
  color: #999;
  font-size: 9px;
}

.shadow-backtest-card > header button {
  padding: 4px 8px;
  color: #e53955;
  font-size: 10px;
  background: #fff;
  border: 1px solid #e8cbd1;
  border-radius: 9px;
}

.shadow-version-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
  padding: 9px;
}

.shadow-version-grid article {
  padding: 8px;
  text-align: center;
  background: #f7f7f8;
  border-radius: 8px;
}

.shadow-version-grid article.candidate {
  background: #fff5f7;
}

.shadow-version-grid span,
.shadow-version-grid strong,
.shadow-version-grid small {
  display: block;
}

.shadow-version-grid span {
  color: #888;
  font-size: 9px;
}

.shadow-version-grid strong {
  margin: 3px 0;
  color: #333;
  font-size: 18px;
}

.shadow-version-grid small {
  color: #999;
  font-size: 9px;
}

.shadow-version-grid b {
  color: #555;
  font-weight: 600;
}

.shadow-backtest-card > footer {
  padding: 0 9px 9px;
}

.shadow-backtest-card > footer > span {
  display: inline-block;
  padding: 2px 6px;
  color: #9b6a24;
  font-size: 9px;
  background: #fff4df;
  border-radius: 7px;
}

.shadow-backtest-card > footer > span.eligible {
  color: #177d61;
  background: #eaf8f3;
}

.shadow-backtest-card > footer p {
  margin: 5px 0 2px;
  color: #777;
  font-size: 10px;
  line-height: 1.45;
}

.shadow-backtest-card > footer small {
  display: block;
  margin-top: 2px;
  color: #aaa;
  font-size: 9px;
  line-height: 1.4;
}

.strategy-review-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
  padding: 0 9px 9px;
}

.strategy-review-grid > article {
  padding: 9px;
  background: linear-gradient(145deg, #fff, #fdf9fa);
  border: 1px solid #eee2e5;
  border-radius: 10px;
}

.strategy-review-grid header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.strategy-review-grid header strong {
  color: #e53955;
  font-size: 14px;
}

.strategy-review-grid header span {
  padding: 3px 6px;
  color: #6f6b6d;
  font-size: 10px;
  background: #f3f3f5;
  border-radius: 8px;
}

.strategy-review-grid > article > div {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  margin-top: 8px;
}

.strategy-review-grid p {
  margin: 0;
  text-align: center;
  border-left: 1px solid #f0e5e7;
}

.strategy-review-grid p:first-child {
  border-left: 0;
}

.strategy-review-grid p span,
.strategy-review-grid p b {
  display: block;
}

.strategy-review-grid p span {
  color: #999;
  font-size: 9px;
  white-space: nowrap;
}

.strategy-review-grid p b {
  margin-top: 3px;
  color: #444;
  font-size: 13px;
}

.strategy-review-grid > article > small {
  display: block;
  min-height: 28px;
  margin-top: 7px;
  color: #aaa;
  font-size: 9px;
  line-height: 1.4;
  text-align: center;
}

.review-guardrail-summary {
  display: flex;
  gap: 8px;
  margin: 0 10px 10px;
  padding: 9px 10px;
  color: #87591f;
  font-size: 11px;
  line-height: 1.5;
  background: #fff7e8;
  border: 1px solid #f0dfbd;
  border-radius: 9px;
}

.review-guardrail-summary strong {
  flex: 0 0 auto;
  color: #c36d14;
}

.daily-review-block {
  margin: 0 10px 10px;
  overflow: hidden;
  border: 1px solid #f0e5e7;
  border-radius: 9px;
}

.daily-review-block > h2 {
  display: flex;
  justify-content: space-between;
  margin: 0;
  padding: 9px 10px;
  color: #444;
  font-size: 14px;
  background: #faf8f9;
}

.daily-review-block > h2 small {
  color: #e53955;
  font-size: 12px;
}

.daily-review-block > button {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(98px, 34%) 46px;
  gap: 6px;
  min-height: 72px;
  width: 100%;
  padding: 9px 10px;
  align-items: center;
  text-align: left;
  background: #fff;
  border: 0;
  border-top: 1px solid #f5eff0;
}

.review-match-info {
  overflow: hidden;
  color: #777;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.review-match-info b {
  margin-right: 4px;
  color: #444;
}

.review-pick-info,
.review-result-info {
  display: grid;
  min-width: 0;
  gap: 3px;
  align-content: center;
  justify-items: end;
  text-align: right;
}

.review-pick-info > strong {
  color: #e53955;
  font-size: 13px;
  line-height: 1.3;
  max-width: 100%;
  white-space: normal;
  word-break: break-all;
}

.review-result-info > em {
  color: #555;
  font-size: 12px;
  font-style: normal;
  line-height: 1.3;
  white-space: nowrap;
}

.review-result-info > em small {
  display: block;
  margin-top: 2px;
  color: #aaa;
  font-size: 10px;
}

.review-pick-info > i {
  color: #999;
  font-size: 12px;
  font-style: normal;
  line-height: 1.3;
  white-space: nowrap;
}

.review-pick-info > i.hit,
.combo-review-block i.hit {
  color: #15956f;
}

.review-pick-info > i.miss,
.combo-review-block i.miss {
  color: #e53955;
}

.review-pick-info > i.push,
.combo-review-block i.push {
  color: #b2771b;
}

.review-result-info > small {
  color: #777;
  font-size: 10px;
  line-height: 1.3;
  white-space: nowrap;
}

.review-pick-info > small.guarded-pick {
  color: #b2771b;
  font-size: 9px;
  line-height: 1.25;
  white-space: nowrap;
}

.combo-review-block > article {
  padding: 8px 10px;
  border-top: 1px solid #f5eff0;
}

.combo-review-block article > header,
.combo-review-block article > footer,
.combo-review-block article > p {
  display: flex;
  justify-content: space-between;
}

.combo-review-block article > header strong {
  color: #444;
  font-size: 13px;
}

.combo-review-block article > header i {
  color: #999;
  font-size: 12px;
  font-style: normal;
}

.combo-review-block article > p {
  margin: 5px 0 0;
  color: #777;
  font-size: 12px;
}

.combo-review-block article > footer {
  margin-top: 7px;
  padding-top: 6px;
  color: #999;
  font-size: 12px;
  border-top: 1px dashed #f0e5e7;
}

.review-pending {
  margin: 0 9px 9px;
  padding: 20px 12px;
  text-align: center;
  background: linear-gradient(145deg, #fafafa, #fff);
  border: 1px dashed #ebe4e6;
  border-radius: 10px;
}

.review-pending strong {
  color: #555;
  font-size: 13px;
}

.review-pending p {
  max-width: 310px;
  margin: 5px auto 0;
  color: #aaa;
  font-size: 10px;
  line-height: 1.55;
}

.skill-center-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 10px;
  padding: 11px;
  background: linear-gradient(135deg, #fff8fa, #fff);
  border: 1px solid #f0dfe2;
  border-radius: 9px;
}

.skill-center-actions > div {
  display: grid;
  gap: 4px;
}

.skill-center-actions strong {
  color: #333;
  font-size: 15px;
}

.skill-center-actions small {
  color: #999;
  font-size: 12px;
  line-height: 1.5;
}

.skill-center-actions button,
.skill-candidate-list footer button,
.active-skill-grid footer button {
  flex: 0 0 auto;
  padding: 7px 10px;
  color: #fff;
  font-size: 13px;
  background: #e53955;
  border: 0;
  border-radius: 16px;
}

.skill-center-actions button:disabled,
.skill-candidate-list footer button:disabled,
.active-skill-grid footer button:disabled {
  color: #aaa;
  background: #ececef;
}

.skill-permission-note,
.skill-action-message {
  margin: 0 10px 10px;
  padding: 8px 10px;
  color: #876828;
  font-size: 12px;
  line-height: 1.5;
  background: #fff8e9;
  border-radius: 7px;
}

.skill-action-message {
  color: #167c61;
  background: #effaf6;
}

.skill-action-message.error {
  color: #e53955;
  background: #fff1f3;
}

.skill-candidate-section,
.active-skill-section,
.skill-history-section {
  padding: 0 10px 10px;
}

.skill-candidate-section > h2,
.active-skill-section > h2,
.skill-history-section > h2 {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 12px 1px 8px;
  color: #333;
  font-size: 15px;
}

.skill-candidate-section > h2 b {
  display: grid;
  width: 18px;
  height: 18px;
  place-items: center;
  color: #fff;
  font-size: 12px;
  background: #e53955;
  border-radius: 50%;
}

.skill-candidate-list {
  display: grid;
  gap: 8px;
}

.skill-candidate-list > article {
  padding: 11px;
  border: 1px solid #efdce0;
  border-radius: 10px;
  background: #fffafb;
}

.skill-candidate-list article > header,
.skill-candidate-list article > footer,
.active-skill-grid article > header,
.active-skill-grid article > footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.skill-candidate-list header > div {
  display: grid;
  gap: 3px;
}

.skill-candidate-list header strong {
  color: #333;
  font-size: 16px;
}

.skill-candidate-list header span {
  color: #999;
  font-size: 12px;
}

.skill-candidate-list header em {
  padding: 3px 6px;
  color: #158467;
  font-size: 11px;
  font-style: normal;
  background: #eaf8f3;
  border-radius: 8px;
}

.skill-change-list {
  margin-top: 9px;
  overflow: hidden;
  border: 1px solid #f0e5e7;
  border-radius: 7px;
}

.skill-change-list p {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 3px 8px;
  margin: 0;
  padding: 7px 8px;
  background: #fff;
  border-top: 1px solid #f4edef;
}

.skill-change-list p:first-child {
  border-top: 0;
}

.skill-change-list span {
  color: #555;
  font-size: 13px;
}

.skill-change-list b {
  color: #e53955;
  font-size: 13px;
}

.skill-change-list small {
  grid-column: 1 / 3;
  color: #aaa;
  font-size: 11px;
}

.skill-evaluation {
  display: flex;
  gap: 15px;
  margin: 8px 0;
  color: #888;
  font-size: 12px;
}

.skill-evaluation b {
  color: #168566;
}

.skill-candidate-list article > footer {
  align-items: flex-end;
}

.skill-candidate-list footer small {
  max-width: 70%;
  color: #aaa;
  font-size: 11px;
  line-height: 1.45;
}

.skill-empty {
  padding: 25px 12px;
  color: #aaa;
  font-size: 13px;
  text-align: center;
  background: #fafafa;
  border-radius: 9px;
}

.active-skill-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.active-skill-grid > article {
  min-width: 0;
  padding: 10px;
  border: 1px solid #eee4e6;
  border-radius: 9px;
}

.active-skill-grid header span {
  overflow: hidden;
  color: #333;
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.active-skill-grid header b {
  color: #e53955;
  font-size: 12px;
  white-space: nowrap;
}

.active-skill-grid article > p {
  min-height: 29px;
  margin: 7px 0;
  color: #999;
  font-size: 11px;
  line-height: 1.5;
}

.skill-parameter-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.skill-parameter-chips span {
  padding: 3px 5px;
  color: #777;
  font-size: 10px;
  background: #f5f5f7;
  border-radius: 4px;
}

.skill-parameter-chips b {
  color: #444;
}

.active-skill-grid article > footer {
  margin-top: 9px;
  padding-top: 7px;
  border-top: 1px dashed #eee;
}

.active-skill-grid footer small {
  color: #aaa;
  font-size: 11px;
}

.active-skill-grid footer button {
  padding: 4px 8px;
  color: #e53955;
  background: #fff;
  border: 1px solid #efb9c1;
}

.skill-history-section > p {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 8px;
  margin: 0;
  padding: 7px 1px;
  color: #777;
  font-size: 12px;
  border-top: 1px solid #f2edef;
}

.skill-history-section > p b {
  color: #555;
}

.skill-history-section > p small {
  color: #aaa;
}

.skill-confirm-overlay {
  position: fixed;
  z-index: 2000;
  inset: 0;
  display: grid;
  padding: 20px;
  place-items: center;
  background: rgb(22 24 31 / 52%);
  backdrop-filter: blur(2px);
}

.skill-confirm-dialog {
  width: min(100%, 360px);
  padding: 18px;
  background: #fff;
  border: 1px solid #f0dfe3;
  border-radius: 16px;
  box-shadow: 0 18px 55px rgb(31 24 27 / 24%);
}

.skill-confirm-dialog > header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.skill-confirm-dialog > header > div {
  display: grid;
  gap: 2px;
}

.skill-confirm-dialog > header strong {
  color: #2f3036;
  font-size: 18px;
}

.skill-confirm-dialog > header small {
  color: #999;
  font-size: 12px;
}

.skill-confirm-version {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin: 16px 0 12px;
  padding: 12px;
  color: #999;
  background: #fff7f9;
  border: 1px solid #f4dfe4;
  border-radius: 10px;
}

.skill-confirm-version i {
  color: #c8aeb4;
  font-style: normal;
}

.skill-confirm-version b {
  color: #e53955;
}

.skill-confirm-dialog > p {
  margin: 0;
  color: #777;
  font-size: 13px;
  line-height: 1.65;
}

.skill-confirm-dialog > .skill-confirm-error {
  margin-top: 10px;
  padding: 8px 10px;
  color: #d72d49;
  background: #fff1f3;
  border-radius: 7px;
}

.skill-confirm-dialog > footer {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-top: 18px;
}

.skill-confirm-dialog > footer button {
  min-height: 42px;
  color: #666;
  font-size: 14px;
  background: #f3f3f5;
  border: 0;
  border-radius: 9px;
}

.skill-confirm-dialog > footer button.primary {
  color: #fff;
  background: linear-gradient(135deg, #ef3654, #ff174a);
}

.skill-confirm-dialog > footer button:disabled {
  opacity: 0.65;
}

.recommendation-state {
  display: grid;
  min-height: 220px;
  place-items: center;
  color: #999;
  font-size: 15px;
  background: #fff;
  border-radius: 12px;
}

.recommendation-state.error {
  align-content: center;
  gap: 10px;
}

.recommendation-state.error button {
  padding: 7px 14px;
  color: #fff;
  background: #e53955;
  border: 0;
  border-radius: 16px;
}

.inline-error {
  margin: 8px 0 0;
  color: #e53955;
  font-size: 13px;
  text-align: center;
}

@media (max-width: 560px) {
  .review-stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .review-stats-grid article {
    min-height: 76px;
  }

  .ai-market-lessons {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .ai-match-diagnoses summary {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .ai-match-diagnoses summary > em {
    display: none;
  }

  .active-skill-grid {
    grid-template-columns: 1fr;
  }

  .daily-pools {
    grid-template-columns: 1fr;
  }

  .draw-radar-groups {
    grid-template-columns: 1fr;
  }
}
</style>

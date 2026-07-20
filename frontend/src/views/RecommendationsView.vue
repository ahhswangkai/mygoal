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
            <small>v{{ faeRankings.engine_version || faeParlays.engine_version || '2.0.0' }}</small>
          </div>
        </div>
        <button type="button" :class="{ rotating: loading }" @click="fetchData">↻</button>
      </section>

      <nav class="recommendation-tabs">
        <button :class="{ active: activeSection === 'dailyAi' }" @click="activeSection = 'dailyAi'">
          AI研判
        </button>
        <button :class="{ active: activeSection === 'rankings' }" @click="activeSection = 'rankings'">
          每日推荐榜
        </button>
        <button :class="{ active: activeSection === 'review' }" @click="activeSection = 'review'">
          赛后复盘
        </button>
        <button :class="{ active: activeSection === 'skills' }" @click="activeSection = 'skills'">
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
                {{ faeDailyAi.match_count || 0 }} 场一次性横向比较 ·
                {{ faeDailyAi.model || 'ark-code-latest' }}
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
          </div>

          <div v-if="dailyPoolGroups.length" class="daily-pools">
            <section v-for="group in dailyPoolGroups" :key="group.key">
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
                  <strong v-if="item.rating">{{ starText(item.rating) }}</strong>
                </span>
                <small>{{ displayDailyText(item.reason) }}</small>
              </button>
            </section>
          </div>

          <section
            v-if="faeDailyAi.daily_summary?.recommended_combinations?.length"
            class="daily-ai-combos"
          >
            <h2>AI 推荐 2 / 3 关</h2>
            <article
              v-for="(combo, index) in faeDailyAi.daily_summary.recommended_combinations"
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
            <p>今日没有同时达到门槛的平局与让平候选，不强行凑组合。</p>
          </section>

          <section class="daily-match-section">
            <h2>逐场五维分析</h2>
            <details
              v-for="item in faeDailyAi.matches || []"
              :key="item.match_id"
              class="daily-match-card"
            >
              <summary>
                <span>
                  <b>{{ item.match_number }}</b>
                  {{ item.home_team }} vs {{ item.away_team }}
                  <small>{{ item.league }} · {{ formatMatchTime(item.match_time) }}</small>
                </span>
                <span class="daily-selection-pair">
                  <em v-if="item.analysis?.no_bet" class="no-bet-badge">不下注</em>
                  <i>预测 {{ item.analysis?.predicted_result || '观望' }}</i>
                  <em>主 {{ item.analysis?.primary_play || '观望' }}</em>
                  <i v-if="item.analysis?.secondary_play && item.analysis.secondary_play !== '观望'">
                    防 {{ item.analysis.secondary_play }}
                  </i>
                  <i v-if="item.analysis?.handicap_play && item.analysis.handicap_play !== '观望'">
                    让球 {{ item.analysis.handicap_play }}
                  </i>
                </span>
                <strong>{{ item.analysis?.no_bet ? '观察' : (item.analysis?.star_text || starText(item.analysis?.rating)) }}</strong>
              </summary>
              <div class="daily-match-body">
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
                  <p><span>大小球</span><b>{{ triplet(item.input_snapshot?.total?.current) }}</b></p>
                </div>
                <div class="daily-value-grid">
                  <p><span>FAE概率</span><b>{{ item.analysis?.prediction_probability ?? '--' }}%</b></p>
                  <p><span>市场概率</span><b>{{ item.analysis?.market_implied_probability ?? '--' }}%</b></p>
                  <p><span>价值指数</span><b>{{ item.analysis?.value_score ?? '--' }}分</b></p>
                  <p><span>盘口可信</span><b>{{ item.analysis?.market_confidence?.score ?? '--' }}分</b></p>
                  <p><span>投注分</span><b>{{ item.analysis?.bet_score ?? '--' }}分</b></p>
                  <p><span>结论</span><b :class="{ 'no-bet-text': item.analysis?.no_bet }">{{ item.analysis?.decision || '观望' }}</b></p>
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
            {{ formatAiTime(faeDailyAi.generated_at) }} 生成 ·
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

      <template v-else-if="activeSection === 'rankings'">
        <section v-if="rankingGroups.length" class="ranking-panel">
          <header class="panel-heading">
            <div>
              <strong>每日推荐榜</strong>
              <small>各玩法独立评分，展示前三名</small>
            </div>
            <span>{{ faeRankings.count || 0 }} 场</span>
          </header>
          <div class="ranking-grid">
            <section v-for="group in rankingGroups" :key="group.name">
              <h2>{{ group.name }}</h2>
              <button
                v-for="(item, index) in group.items"
                :key="item.match_id"
                type="button"
                @click="goToDetail(item.match_id)"
              >
                <i>{{ index + 1 }}</i>
                <span><b>{{ item.match_number }}</b>{{ item.home_team }} vs {{ item.away_team }}</span>
                <em>{{ item.odds_source || '即时' }} {{ item.odds || '--' }}</em>
                <small>价值 {{ item.value_score ?? '--' }} · {{ item.star_text || starText(item.stars) }}</small>
                <strong>{{ item.bet_score ?? item.score }}分</strong>
              </button>
            </section>
          </div>
          <div v-if="dangerous.length" class="danger-panel">
            <h2>⚠ 不下注 / 危险盘口</h2>
            <button v-for="item in dangerous.slice(0, 5)" :key="item.match_id" type="button" @click="goToDetail(item.match_id)">
              <span>{{ item.match_number }} {{ item.home_team }} vs {{ item.away_team }}</span>
              <b>{{ item.no_bet ? '不下注' : `${item.risk?.level || ''}风险` }} · 投注{{ item.bet_score ?? item.score }}分</b>
            </button>
          </div>
        </section>
        <div v-else class="recommendation-state">当天暂无 FAE 推荐榜数据</div>

        <section v-if="faeParlays.match_recommendations?.length" class="parlay-panel">
          <header class="panel-heading">
            <div>
              <strong>平 / 让平组合</strong>
              <small>每场只选一个方向，同组不重复比赛</small>
            </div>
            <span>{{ faeParlays.match_count }} 场</span>
          </header>

          <details class="all-picks" open>
            <summary>
              <span>当天全部单场方向</span>
              <b>{{ faeParlays.match_count }} 场</b>
            </summary>
            <div class="all-picks-grid">
              <button
                v-for="item in faeParlays.match_recommendations"
                :key="item.match_id"
                type="button"
                @click="goToDetail(item.match_id)"
              >
                <span><b>{{ item.match_number }}</b>{{ item.home_team }} vs {{ item.away_team }}</span>
                <strong>{{ item.selection_text }}</strong>
                <em>{{ item.odds_source || '即时' }} {{ item.odds || '--' }}</em>
                <small>模型 {{ item.probability }}% · {{ item.score }}分</small>
              </button>
            </div>
          </details>

          <div class="combo-groups">
            <section v-for="group in comboGroups" :key="group.key" class="combo-group">
              <h2><span>{{ group.title }}</span><small>优选 {{ group.items.length }} 组</small></h2>
              <article v-for="(combo, index) in group.items" :key="`${group.key}-${index}`">
                <header>
                  <span><i>{{ index + 1 }}</i>{{ combo.play }}</span>
                  <b>{{ combo.combo_score }}分</b>
                </header>
                <button
                  v-for="pick in combo.picks"
                  :key="`${pick.match_id}-${pick.selection}`"
                  type="button"
                  @click="goToDetail(pick.match_id)"
                >
                  <span>{{ pick.match_number }} {{ pick.home_team }} vs {{ pick.away_team }}</span>
                  <strong>{{ pick.selection_text }}</strong>
                  <em>@{{ pick.odds || '--' }}</em>
                </button>
                <footer>
                  <span>组合赔率 <b>{{ combo.combined_odds || '--' }}</b></span>
                  <span>模型命中 <b>{{ combo.model_hit_probability }}%</b></span>
                </footer>
              </article>
            </section>
          </div>
          <p class="recommendation-disclaimer">{{ faeParlays.disclaimer }}</p>
        </section>
        <div v-else class="recommendation-state">当天暂无可组合的平/让平分析</div>
      </template>

      <template v-else-if="activeSection === 'review'">
        <section class="review-panel">
          <header class="panel-heading">
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
              <span>2串1命中</span>
              <strong>{{ faeStats.by_play?.['2串1']?.hit_rate || 0 }}%</strong>
              <small>{{ faeStats.by_play?.['2串1']?.hits || 0 }}/{{ faeStats.by_play?.['2串1']?.settled || 0 }}</small>
            </article>
            <article>
              <span>3串1命中</span>
              <strong>{{ faeStats.by_play?.['3串1']?.hit_rate || 0 }}%</strong>
              <small>{{ faeStats.by_play?.['3串1']?.hits || 0 }}/{{ faeStats.by_play?.['3串1']?.settled || 0 }}</small>
            </article>
          </div>

          <div class="strategy-review-grid">
            <article v-for="selection in ['平局', '让平']" :key="selection">
              <header>
                <strong>{{ selection }}</strong>
                <span>权重 {{ strategyWeight(selection).weight }}</span>
              </header>
              <div>
                <p><span>命中率</span><b>{{ faeStats.by_selection?.[selection]?.hit_rate || 0 }}%</b></p>
                <p><span>ROI</span><b :class="metricClass(faeStats.by_selection?.[selection]?.roi)">{{ signedMetric(faeStats.by_selection?.[selection]?.roi) }}%</b></p>
                <p><span>样本</span><b>{{ faeStats.by_selection?.[selection]?.settled || 0 }}</b></p>
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
                    已审计 {{ faeReview.ai_deep_review.coverage?.settled_matches || 0 }}
                    / {{ faeReview.ai_deep_review.coverage?.total_matches || 0 }} 场 ·
                    {{ faeReview.ai_deep_review.model }}
                  </small>
                </div>
              </div>
              <span>{{ faeReview.ai_deep_review.coverage?.review_completed ? '终版' : '阶段版' }}</span>
            </header>

            <p class="ai-review-conclusion">
              {{ faeReview.ai_deep_review.summary?.conclusion }}
            </p>

            <div class="ai-review-points">
              <article>
                <strong>做对了什么</strong>
                <p
                  v-for="item in faeReview.ai_deep_review.summary?.what_worked || []"
                  :key="`worked-${item}`"
                >✓ {{ item }}</p>
                <small v-if="!faeReview.ai_deep_review.summary?.what_worked?.length">暂无足够样本</small>
              </article>
              <article>
                <strong>需要修正</strong>
                <p
                  v-for="item in faeReview.ai_deep_review.summary?.what_failed || []"
                  :key="`failed-${item}`"
                >× {{ item }}</p>
                <small v-if="!faeReview.ai_deep_review.summary?.what_failed?.length">暂无明确错误模式</small>
              </article>
            </div>

            <div class="ai-market-lessons">
              <article
                v-for="(text, key) in faeReview.ai_deep_review.market_lessons || {}"
                :key="key"
              >
                <strong>{{ aiScopeLabel(key) }}</strong>
                <p>{{ text }}</p>
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
                <p>{{ candidate.reason }}</p>
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
                  <em>{{ item.selection_text }} · {{ item.result_score }}</em>
                  <i :class="aiVerdictClass(item.verdict)">{{ item.verdict }}</i>
                </summary>
                <p>{{ item.diagnosis }}</p>
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
            v-else-if="faeReview && faeReview.summary?.singles?.settled"
            class="ai-review-waiting"
          >
            <strong>确定性结算已完成</strong>
            <span>
              {{ faeReview.ai_deep_review_error || faeReview.ai_deep_review_unavailable || 'AI 深度复盘将在下一轮自动任务中生成' }}
            </span>
          </div>

          <template v-if="faeReview">
            <section class="daily-review-block">
              <h2><span>AI 当天逐场结果</span><small>{{ faeReview.summary?.singles?.hits || 0 }}/{{ faeReview.summary?.singles?.settled || 0 }}</small></h2>
              <button
                v-for="item in faeReview.match_results"
                :key="item.match_id"
                type="button"
                @click="goToDetail(item.match_id)"
              >
                <span><b>{{ item.match_number }}</b>{{ item.home_team }} vs {{ item.away_team }}</span>
                <strong>{{ item.selection_text || item.selection }}</strong>
                <em>
                  {{ item.result_score || '待赛' }}
                  <small v-if="item.odds">@{{ item.odds }}</small>
                </em>
                <i :class="item.status">{{ reviewStatusLabel(item.status) }}</i>
                <small v-if="item.guardrail_triggered" class="guarded-pick">
                  AI原选{{ item.model_selection }}
                </small>
                <small v-if="isSettledStatus(item.status)">{{ signedMetric(item.profit) }}单位</small>
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
        <section class="skill-center-panel">
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
                    @click="promoteSkill(candidate)"
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
                    @click="rollbackSkill(skill)"
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
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AccountButton from '../components/AccountButton.vue'
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
const faeRankings = ref({})
const faeParlays = ref({})
const faeReview = ref(null)
const reviewAiBusy = ref(false)
const reviewAiMessage = ref('')
const reviewAiError = ref('')
const faeStats = ref({})
const faeSkills = ref({ active: [], candidates: [], deployments: [] })
const skillBusy = ref(false)
const skillMessage = ref('')
const skillError = ref('')
let requestController = null

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

const rankingOrder = ['让平', '让胜', '平局', '主胜', '客胜', '让负']
const rankingGroups = computed(() => {
  const groups = faeRankings.value?.groups || {}
  return rankingOrder
    .filter(name => Array.isArray(groups[name]) && groups[name].length)
    .map(name => ({ name, items: groups[name].slice(0, 3) }))
})
const dangerous = computed(() => faeRankings.value?.dangerous || [])
const comboGroups = computed(() => [
  { key: 'two', title: '2关方案（2串1）', items: faeParlays.value?.two_leg || [] },
  { key: 'three', title: '3关方案（3串1）', items: faeParlays.value?.three_leg || [] }
].filter(group => group.items.length))
const dailyPoolLabels = {
  handicap_draw: '重点让平',
  handicap_lose: '重点让负',
  draw: '普通平局',
  away_small_win: '客队小胜',
  avoid: '建议避开'
}
const dailyPoolGroups = computed(() => {
  const source = faeDailyAi.value?.daily_summary?.pools || {}
  const pools = Object.fromEntries(
    Object.entries(source).map(([key, items]) => [key, [...(items || [])]])
  )
  pools.handicap_lose ||= []
  pools.away_small_win = (pools.away_small_win || []).filter(item => {
    const match = dailyMatch(item.match_id)
    const analysis = match.analysis || {}
    const hhad = match.input_snapshot?.fae_core?.probabilities?.hhad || {}
    const letLoseIsTop = Number(hhad.lose || 0) >= Math.max(
      Number(hhad.win || 0),
      Number(hhad.draw || 0)
    )
    const isLetLose = analysis.primary_play === '让负'
      || String(item.reason || '').includes('让负')
      || letLoseIsTop
    if (isLetLose) pools.handicap_lose.push(item)
    return !isLetLose
  })
  return Object.entries(dailyPoolLabels)
    .map(([key, title]) => ({ key, title, items: pools[key] || [] }))
    .filter(group => group.items.length)
})
const dailyMatchMap = computed(() => Object.fromEntries(
  (faeDailyAi.value?.matches || []).map(item => [String(item.match_id), item])
))
const dailyMarkets = [
  { key: 'euro', label: '欧赔方向' },
  { key: 'asian', label: '亚盘升深' },
  { key: 'sporttery', label: '竞彩让球' },
  { key: 'total', label: '大小球' },
  { key: 'consistency', label: '市场一致性' }
]
const hasData = computed(() =>
  faeDailyAi.value
  || rankingGroups.value.length
  || faeParlays.value?.match_recommendations?.length
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
    const [rankingResponse, parlayResponse, reviewResponse, statsResponse, skillsResponse, dailyAiResponse] = await Promise.all([
      fetch(`/api/fae/rankings?date=${date}`, { signal: controller.signal }),
      fetch(`/api/fae/draw-parlays?date=${date}`, { signal: controller.signal }),
      fetch(`/api/fae/daily-ai/review?date=${date}`, { signal: controller.signal }),
      fetch('/api/fae/daily-ai/review/stats', { signal: controller.signal }),
      fetch('/api/fae/skills', { signal: controller.signal }),
      fetch(`/api/fae/daily-ai?date=${date}`, {
        signal: controller.signal,
        credentials: 'same-origin'
      })
    ])
    const [rankingPayload, parlayPayload, reviewPayload, statsPayload, skillsPayload, dailyAiPayload] = await Promise.all([
      rankingResponse.json(),
      parlayResponse.json(),
      reviewResponse.json(),
      statsResponse.json(),
      skillsResponse.json(),
      dailyAiResponse.json()
    ])
    if (!rankingResponse.ok || !rankingPayload.success) {
      throw new Error(rankingPayload.message || '推荐榜加载失败')
    }
    if (!parlayResponse.ok || !parlayPayload.success) {
      throw new Error(parlayPayload.message || '组合推荐加载失败')
    }
    faeRankings.value = rankingPayload.data || {}
    faeParlays.value = parlayPayload.data || {}
    faeReview.value = reviewResponse.ok && reviewPayload.success ? reviewPayload.data : null
    faeStats.value = statsResponse.ok && statsPayload.success ? (statsPayload.data || {}) : {}
    faeSkills.value = skillsResponse.ok && skillsPayload.success
      ? (skillsPayload.data || { active: [], candidates: [], deployments: [] })
      : { active: [], candidates: [], deployments: [] }
    faeDailyAi.value = dailyAiResponse.ok && dailyAiPayload.success
      ? dailyAiPayload.data
      : null
    dailyAiConfigured.value = Boolean(
      dailyAiPayload.configured || reviewPayload.ai_review_configured
    )
    dailyAiCanManage.value = Boolean(dailyAiPayload.can_manage)
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

function selectDate(date) {
  if (selectedDate.value === date) return
  selectedDate.value = date
  faeRankings.value = {}
  faeParlays.value = {}
  faeReview.value = null
  faeDailyAi.value = null
  dailyAiMessage.value = ''
  dailyAiError.value = ''
  reviewAiMessage.value = ''
  reviewAiError.value = ''
  fetchData()
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

function displayDailyText(value) {
  let text = String(value || '')
  const matches = Object.values(dailyMatchMap.value)
    .filter(item => item?.match_id && item?.match_number)
    .sort((left, right) => String(right.match_id).length - String(left.match_id).length)
  for (const item of matches) {
    text = text.split(String(item.match_id)).join(String(item.match_number))
  }
  return text
}

function triplet(values) {
  if (!Array.isArray(values)) return '--'
  return values.map(value => value ?? '--').join(' / ')
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

function formatAiTime(value) {
  if (!value) return '未知时间'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', {
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

function strategyWeight(selection) {
  return faeStats.value?.strategy_weights?.[selection] || {
    weight: 1,
    action: 'hold'
  }
}

function weightActionLabel(action) {
  if (action === 'increase') return '近期表现有效，等待 Skill 候选发布'
  if (action === 'decrease') return '近期表现偏低，等待 Skill 候选发布'
  return '样本积累中，线上权重保持不变'
}

function reviewStatusLabel(status) {
  if (status === 'hit') return '✓ 命中'
  if (status === 'miss') return '× 未中'
  if (status === 'push') return '走盘'
  if (status === 'skipped') return '观望'
  if (status === 'ungraded') return '未结算'
  return '待赛'
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
  if (value === '判断有效') return 'good'
  if (value === '命中但过程有风险' || value === '走盘') return 'warning'
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
  } catch (e) {
    skillError.value = e.message || 'Skill 操作失败'
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

function promoteSkill(candidate) {
  if (!window.confirm(
    `确定发布 ${candidate.label} v${candidate.proposed_version} 吗？新比赛会立即使用这组参数。`
  )) return
  return runSkillAction(
    `/api/fae/skills/${candidate.skill_id}/promote`,
    { candidate_id: candidate.candidate_id },
    `${candidate.label} 已发布`
  )
}

function rollbackSkill(skill) {
  if (!window.confirm(
    `确定回滚 ${skill.label} v${skill.version} 吗？`
  )) return
  return runSkillAction(
    `/api/fae/skills/${skill.skill_id}/rollback`,
    {},
    `${skill.label} 已回滚`
  )
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
onBeforeUnmount(() => requestController?.abort())
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
  grid-template-columns: repeat(4, 1fr);
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

.parlay-panel,
.ranking-panel,
.review-panel,
.skill-center-panel,
.daily-ai-panel {
  overflow: hidden;
  background: #fff;
  border: 1px solid #eadde0;
  border-radius: 13px;
  box-shadow: 0 5px 18px rgb(57 31 37 / 6%);
}

.ranking-panel + .parlay-panel {
  margin-top: 12px;
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
  white-space: pre-line;
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

.daily-match-card {
  margin-bottom: 7px;
  overflow: hidden;
  border: 1px solid #eee4e6;
  border-radius: 9px;
}

.daily-match-card > summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 7px;
  align-items: center;
  padding: 9px;
  cursor: pointer;
  list-style: none;
}

.daily-match-card > summary::-webkit-details-marker {
  display: none;
}

.daily-match-card summary > span {
  overflow: hidden;
  color: #444;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.daily-match-card summary > span b {
  margin-right: 4px;
}

.daily-match-card summary small {
  display: block;
  margin-top: 3px;
  color: #aaa;
  font-size: 10px;
}

.daily-match-card summary em {
  padding: 4px 6px;
  color: #fff;
  font-size: 12px;
  font-style: normal;
  font-weight: 700;
  background: #e53955;
  border-radius: 5px;
}

.daily-match-card summary em.no-bet-badge {
  background: #515866;
}

.daily-selection-pair {
  display: grid;
  gap: 2px;
  justify-items: end;
}

.daily-selection-pair i {
  color: #8a8f96;
  font-size: 9px;
  font-style: normal;
  white-space: nowrap;
}

.daily-match-card summary > strong {
  color: #e53955;
  font-size: 11px;
}

.daily-match-card[open] > summary {
  background: #fff8f9;
  border-bottom: 1px solid #f1e6e8;
}

.daily-match-body {
  padding: 10px;
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

.all-picks {
  margin: 10px;
  border: 1px solid #f0e5e7;
  border-radius: 10px;
}

.all-picks summary {
  display: flex;
  justify-content: space-between;
  padding: 10px;
  color: #444;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  list-style: none;
}

.all-picks summary::-webkit-details-marker {
  display: none;
}

.all-picks summary b {
  color: #e53955;
  font-size: 13px;
}

.all-picks-grid,
.combo-groups,
.ranking-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.all-picks-grid {
  padding: 0 8px 8px;
}

.all-picks-grid button {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 6px;
  padding: 8px;
  text-align: left;
  background: #fcfafb;
  border: 1px solid #f2e8ea;
  border-radius: 8px;
}

.all-picks-grid button > span {
  grid-column: 1 / 3;
  overflow: hidden;
  color: #777;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.all-picks-grid button > span b {
  margin-right: 4px;
  color: #444;
}

.all-picks-grid button > strong {
  color: #e53955;
  font-size: 15px;
}

.all-picks-grid button > em {
  color: #555;
  font-size: 13px;
  font-style: normal;
}

.all-picks-grid button > small {
  grid-column: 1 / 3;
  color: #aaa;
  font-size: 12px;
}

.combo-groups {
  padding: 0 10px 10px;
}

.combo-group {
  min-width: 0;
}

.combo-group > h2,
.ranking-grid section > h2 {
  display: flex;
  justify-content: space-between;
  margin: 0 0 7px;
  color: #333;
  font-size: 15px;
}

.combo-group > h2 small {
  color: #aaa;
  font-size: 12px;
  font-weight: 400;
}

.combo-group article {
  margin-bottom: 8px;
  overflow: hidden;
  background: linear-gradient(145deg, #fff, #fff9fa);
  border: 1px solid #f0dfe2;
  border-radius: 9px;
}

.combo-group article > header {
  display: flex;
  justify-content: space-between;
  padding: 7px 8px;
  border-bottom: 1px solid #f4e9eb;
}

.combo-group article > header span {
  color: #555;
  font-size: 13px;
  font-weight: 600;
}

.combo-group article > header i {
  display: inline-block;
  width: 17px;
  height: 17px;
  margin-right: 5px;
  color: #fff;
  font-size: 12px;
  font-style: normal;
  line-height: 17px;
  text-align: center;
  background: #e53955;
  border-radius: 50%;
}

.combo-group article > header b {
  color: #e53955;
  font-size: 13px;
}

.combo-group article > button {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 5px;
  width: 100%;
  padding: 6px 8px;
  text-align: left;
  background: none;
  border: 0;
  border-bottom: 1px dashed #f0e5e7;
}

.combo-group article > button span {
  overflow: hidden;
  color: #777;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.combo-group article > button strong {
  color: #e53955;
  font-size: 13px;
  white-space: nowrap;
}

.combo-group article > button em {
  color: #555;
  font-size: 12px;
  font-style: normal;
  white-space: nowrap;
}

.combo-group article > footer {
  display: flex;
  justify-content: space-between;
  padding: 7px 8px;
  color: #888;
  font-size: 12px;
}

.combo-group article > footer b {
  color: #333;
}

.recommendation-disclaimer {
  margin: 0;
  padding: 0 11px 11px;
  color: #aaa;
  font-size: 12px;
  line-height: 1.55;
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

.ranking-grid {
  padding: 10px;
}

.ranking-grid > section {
  min-width: 0;
  padding: 9px;
  border: 1px solid #f2e6e8;
  border-radius: 9px;
}

.ranking-grid section > h2 {
  color: #e53955;
}

.ranking-grid section > button {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto;
  gap: 2px 6px;
  width: 100%;
  padding: 7px 0;
  text-align: left;
  background: none;
  border: 0;
  border-top: 1px solid #f5f0f1;
}

.ranking-grid button > i {
  grid-row: 1 / 3;
  align-self: center;
  width: 18px;
  height: 18px;
  color: #fff;
  font-size: 12px;
  font-style: normal;
  line-height: 18px;
  text-align: center;
  background: #e53955;
  border-radius: 50%;
}

.ranking-grid button > span {
  overflow: hidden;
  color: #777;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ranking-grid button > span b {
  margin-right: 4px;
  color: #444;
}

.ranking-grid button > em {
  color: #777;
  font-size: 12px;
  font-style: normal;
}

.ranking-grid button > small {
  grid-column: 2;
  color: #ff9c25;
  font-size: 12px;
  letter-spacing: -1px;
}

.ranking-grid button > strong {
  grid-column: 3;
  color: #e53955;
  font-size: 12px;
}

.danger-panel {
  margin: 0 10px 10px;
  padding: 9px;
  background: #fff7e8;
  border-radius: 9px;
}

.danger-panel h2 {
  margin: 0 0 5px;
  color: #9a6b13;
  font-size: 14px;
}

.danger-panel button {
  display: flex;
  justify-content: space-between;
  width: 100%;
  padding: 5px 0;
  color: #805e21;
  font-size: 12px;
  text-align: left;
  background: none;
  border: 0;
}

.danger-panel button b {
  color: #d47b22;
}

.review-heading-actions {
  display: flex;
  align-items: center;
  gap: 7px;
}

.review-heading-actions > span {
  padding: 3px 7px;
  color: #8b8589;
  font-size: 10px;
  background: #f4f2f3;
  border-radius: 9px;
}

.review-heading-actions > button {
  padding: 6px 9px;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  background: #e53955;
  border: 0;
  border-radius: 14px;
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
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 7px;
  padding: 9px;
}

.review-stats-grid article {
  display: grid;
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
}

.review-stats-grid strong {
  margin-top: 4px;
  color: #333;
  font-size: 18px;
  line-height: 1.1;
}

.review-stats-grid small {
  margin-top: 4px;
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
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 3px 7px;
  width: 100%;
  padding: 8px 10px;
  text-align: left;
  background: #fff;
  border: 0;
  border-top: 1px solid #f5eff0;
}

.daily-review-block > button > span {
  overflow: hidden;
  color: #777;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.daily-review-block > button > span b {
  margin-right: 4px;
  color: #444;
}

.daily-review-block > button > strong {
  color: #e53955;
  font-size: 13px;
}

.daily-review-block > button > em {
  color: #555;
  font-size: 12px;
  font-style: normal;
}

.daily-review-block > button > em small {
  display: block;
  margin-top: 2px;
  color: #aaa;
  font-size: 10px;
}

.daily-review-block > button > i {
  grid-column: 2;
  color: #999;
  font-size: 12px;
  font-style: normal;
}

.daily-review-block > button > i.hit,
.combo-review-block i.hit {
  color: #15956f;
}

.daily-review-block > button > i.miss,
.combo-review-block i.miss {
  color: #e53955;
}

.daily-review-block > button > i.push,
.combo-review-block i.push {
  color: #b2771b;
}

.daily-review-block > button > small {
  grid-column: 3;
  color: #777;
  font-size: 12px;
}

.daily-review-block > button > small.guarded-pick {
  color: #b2771b;
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
  .all-picks-grid,
  .combo-groups,
  .ranking-grid {
    grid-template-columns: 1fr;
  }

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
}
</style>

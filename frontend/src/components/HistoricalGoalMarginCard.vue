<template>
  <section v-if="model?.version" class="goal-margin-card">
    <header>
      <div>
        <strong>历史进球差模型</strong>
        <small>相似完赛盘口 · 时间衰减</small>
      </div>
      <span>进球差 v1</span>
    </header>

    <div class="goal-margin-grid">
      <article v-for="item in rows" :key="item.key">
        <div class="goal-margin-title">
          <div>
            <b>{{ item.label }}</b>
            <small>{{ item.definition }}</small>
          </div>
          <em :class="{ eligible: item.metric?.eligible_for_adjustment }">
            {{ item.metric?.signal || '样本不足' }}
          </em>
        </div>
        <div class="goal-margin-values">
          <p><span>历史频率</span><b>{{ percent(item.metric?.historical_probability) }}</b></p>
          <p><span>市场基线</span><b>{{ percent(item.metric?.market_probability) }}</b></p>
          <p><span>校准概率</span><b>{{ percent(item.metric?.blended_probability) }}</b></p>
          <p><span>赔率价值</span><b :class="valueClass(item.metric?.value_edge)">{{ signedPercent(item.metric?.value_edge) }}</b></p>
        </div>
        <footer>
          <span>有效样本 {{ number(item.metric?.effective_sample) }}</span>
          <span>{{ item.metric?.confidence || '样本不足' }}置信</span>
          <span v-if="item.metric?.odds">赔率 {{ number(item.metric.odds) }}</span>
        </footer>
      </article>
    </div>

    <div v-if="calibration?.applied" class="goal-margin-calibration">
      <b>本场已应用历史校准</b>
      <span>
        FAE {{ percent(calibration.core_probability) }} →
        {{ percent(calibration.calibrated_probability) }}，
        权重 {{ percent(Number(calibration.weight || 0) * 100) }}
      </span>
    </div>
    <div v-else class="goal-margin-observation">
      本场模型已参与平局/让平候选比较，未改动最终主选概率。
    </div>

    <p class="goal-margin-note">
      条件历史频率不是必然赛果；样本不足时只展示，不参与评分。
    </p>
  </section>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  model: { type: Object, default: () => ({}) },
  calibration: { type: Object, default: () => ({}) }
})

const marginDefinition = value => {
  const difference = Number(value)
  if (!Number.isFinite(difference)) return '让球数无法映射'
  if (difference === 0) return '净胜球差恰好为 0'
  const goals = Math.abs(difference)
  return difference > 0
    ? `主队恰好赢 ${goals} 球`
    : `客队恰好赢 ${goals} 球`
}

const rows = computed(() => [
  {
    key: 'ordinary',
    label: '普通平局',
    definition: '净胜球差恰好为 0',
    metric: props.model?.ordinary_draw || {}
  },
  {
    key: 'handicap',
    label: '竞彩让平',
    definition: marginDefinition(props.model?.handicap_draw?.target_goal_difference),
    metric: props.model?.handicap_draw || {}
  }
])

function number(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '--'
  return Number.isInteger(parsed) ? String(parsed) : parsed.toFixed(1)
}

function percent(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${number(parsed)}%` : '--'
}

function signedPercent(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '--'
  return `${parsed > 0 ? '+' : ''}${number(parsed)}%`
}

function valueClass(value) {
  const parsed = Number(value)
  return {
    positive: Number.isFinite(parsed) && parsed > 0,
    negative: Number.isFinite(parsed) && parsed < 0
  }
}
</script>

<style scoped>
.goal-margin-card {
  margin-top: 10px;
  padding: 10px;
  background: linear-gradient(145deg, #fffafb, #fff);
  border: 1px solid #f0dfe3;
  border-radius: 9px;
}

.goal-margin-card > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.goal-margin-card > header strong,
.goal-margin-card > header small {
  display: block;
}

.goal-margin-card > header strong {
  color: #343841;
  font-size: 13px;
}

.goal-margin-card > header small {
  margin-top: 2px;
  color: #a0a3aa;
  font-size: 9px;
}

.goal-margin-card > header > span {
  flex: 0 0 auto;
  padding: 3px 7px;
  color: #e53955;
  font-size: 9px;
  background: #fff0f3;
  border-radius: 9px;
}

.goal-margin-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
  margin-top: 9px;
}

.goal-margin-grid article {
  min-width: 0;
  padding: 8px;
  background: #fff;
  border: 1px solid #f1e7e9;
  border-radius: 8px;
}

.goal-margin-title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 5px;
}

.goal-margin-title b,
.goal-margin-title small {
  display: block;
}

.goal-margin-title b {
  color: #e53955;
  font-size: 12px;
}

.goal-margin-title small {
  margin-top: 2px;
  color: #999da4;
  font-size: 9px;
  line-height: 1.3;
}

.goal-margin-title em {
  flex: 0 0 auto;
  max-width: 72px;
  padding: 2px 5px;
  overflow: hidden;
  color: #9b7a3c;
  font-size: 8px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: #fff7df;
  border-radius: 5px;
}

.goal-margin-title em.eligible {
  color: #287b60;
  background: #edf8f3;
}

.goal-margin-values {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px;
  margin-top: 7px;
}

.goal-margin-values p {
  margin: 0;
  padding: 5px 3px;
  text-align: center;
  background: #fafafa;
  border-radius: 5px;
}

.goal-margin-values span,
.goal-margin-values b {
  display: block;
}

.goal-margin-values span {
  color: #a0a3a9;
  font-size: 8px;
}

.goal-margin-values b {
  margin-top: 2px;
  color: #444a52;
  font-size: 11px;
}

.goal-margin-values b.positive {
  color: #13865f;
}

.goal-margin-values b.negative {
  color: #d95a68;
}

.goal-margin-grid footer {
  display: flex;
  flex-wrap: wrap;
  gap: 3px 7px;
  margin-top: 6px;
  color: #969aa1;
  font-size: 8px;
}

.goal-margin-calibration,
.goal-margin-observation {
  margin-top: 8px;
  padding: 7px 8px;
  font-size: 10px;
  line-height: 1.45;
  border-radius: 6px;
}

.goal-margin-calibration {
  color: #2a755e;
  background: #edf8f3;
}

.goal-margin-calibration b,
.goal-margin-calibration span {
  display: block;
}

.goal-margin-calibration span {
  margin-top: 2px;
}

.goal-margin-observation {
  color: #777d85;
  background: #f5f6f8;
}

.goal-margin-note {
  margin: 7px 0 0;
  color: #a3a6ac;
  font-size: 8px;
  line-height: 1.4;
}

@media (max-width: 370px) {
  .goal-margin-grid {
    grid-template-columns: 1fr;
  }
}
</style>

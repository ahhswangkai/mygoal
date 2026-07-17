<template>
  <div class="score-grid">
    <!-- 主胜比分 -->
    <div class="score-section">
      <div class="score-section-title">🎯 主胜比分</div>
      <div class="score-items">
        <div
          v-for="s in homeWinScores"
          :key="s"
          class="score-btn"
          :class="{ selected: isSelected('score', s) }"
          @click="select('score', s, match.score[s], s)"
        >
          <div class="score-text">{{ s }}</div>
          <div class="score-odds">{{ match.score[s] }}</div>
        </div>
      </div>
    </div>
    <!-- 平比分 -->
    <div class="score-section">
      <div class="score-section-title">🤝 平局比分</div>
      <div class="score-items">
        <div
          v-for="s in drawScores"
          :key="s"
          class="score-btn"
          :class="{ selected: isSelected('score', s) }"
          @click="select('score', s, match.score[s], s)"
        >
          <div class="score-text">{{ s }}</div>
          <div class="score-odds">{{ match.score[s] }}</div>
        </div>
      </div>
    </div>
    <!-- 客胜比分 -->
    <div class="score-section">
      <div class="score-section-title">🎯 客胜比分</div>
      <div class="score-items">
        <div
          v-for="s in awayWinScores"
          :key="s"
          class="score-btn"
          :class="{ selected: isSelected('score', s) }"
          @click="select('score', s, match.score[s], s)"
        >
          <div class="score-text">{{ s }}</div>
          <div class="score-odds">{{ match.score[s] }}</div>
        </div>
      </div>
    </div>
    <!-- 其他 -->
    <div class="score-section">
      <div class="score-section-title">📋 其他</div>
      <div class="score-items other">
        <div
          class="score-btn"
          :class="{ selected: isSelected('score', '胜其他') }"
          @click="select('score', '胜其他', match.score['胜其他'], '胜其他')"
        >
          <div class="score-text">胜其他</div>
          <div class="score-odds">{{ match.score['胜其他'] }}</div>
        </div>
        <div
          class="score-btn"
          :class="{ selected: isSelected('score', '平其他') }"
          @click="select('score', '平其他', match.score['平其他'], '平其他')"
        >
          <div class="score-text">平其他</div>
          <div class="score-odds">{{ match.score['平其他'] }}</div>
        </div>
        <div
          class="score-btn"
          :class="{ selected: isSelected('score', '负其他') }"
          @click="select('score', '负其他', match.score['负其他'], '负其他')"
        >
          <div class="score-text">负其他</div>
          <div class="score-odds">{{ match.score['负其他'] }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  match: Object,
  selected: Array
})

const emit = defineEmits(['select'])

const homeWinScores = ['1:0','2:0','2:1','3:0','3:1','3:2','4:0','4:1','4:2','5:0','5:1','5:2']
const drawScores = ['0:0','1:1','2:2','3:3']
const awayWinScores = ['0:1','0:2','1:2','0:3','1:3','2:3','0:4','1:4','2:4','0:5','1:5','2:5']

const isSelected = (pool, opt) => {
  return props.selected.some(s =>
    s.matchId === props.match.id && s.pool === pool && s.opt === opt
  )
}

const select = (pool, opt, odds, label) => {
  emit('select', {
    id: `${props.match.id}_${pool}_${opt}`,
    matchId: props.match.id,
    pool: pool,
    opt: opt,
    odds: odds,
    label: label,
    matchName: `${props.match.homeTeam} VS ${props.match.awayTeam}`
  })
}
</script>

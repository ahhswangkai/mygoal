<template>
  <div class="goals-grid">
    <div class="goals-items">
      <div
        v-for="(odds, goal) in match.goals"
        :key="goal"
        class="goal-btn"
        :class="{ selected: isSelected('goals', goal) }"
        @click="select('goals', goal, odds, goal + '球')"
      >
        <div class="goal-text">{{ goal }}</div>
        <div class="goal-odds">{{ odds }}</div>
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

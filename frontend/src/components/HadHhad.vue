<template>
  <div class="had-hhad-grid">
    <!-- 胜平负 -->
    <div class="had-row">
      <div
        class="odds-btn"
        :class="{ selected: isSelected('had', 'win') }"
        @click="select('had', 'win', match.had.win, '胜')"
      >
        <div class="odds-val">{{ match.had.win }}</div>
        <div class="odds-label">胜</div>
      </div>
      <div
        class="odds-btn"
        :class="{ selected: isSelected('had', 'draw') }"
        @click="select('had', 'draw', match.had.draw, '平')"
      >
        <div class="odds-val">{{ match.had.draw }}</div>
        <div class="odds-label">平</div>
      </div>
      <div
        class="odds-btn"
        :class="{ selected: isSelected('had', 'lose') }"
        @click="select('had', 'lose', match.had.lose, '负')"
      >
        <div class="odds-val">{{ match.had.lose }}</div>
        <div class="odds-label">负</div>
      </div>
    </div>
    <!-- 让球胜平负 -->
    <div class="hhad-row">
      <div
        class="odds-btn"
        :class="{ selected: isSelected('hhad', 'win') }"
        @click="select('hhad', 'win', match.hhad.win, '让胜')"
      >
        <span class="had-tag">{{ handicapText }}</span>
        <div class="odds-val">{{ match.hhad.win }}</div>
        <div class="odds-label">让胜</div>
      </div>
      <div
        class="odds-btn"
        :class="{ selected: isSelected('hhad', 'draw') }"
        @click="select('hhad', 'draw', match.hhad.draw, '让平')"
      >
        <span class="had-tag">&nbsp;</span>
        <div class="odds-val">{{ match.hhad.draw }}</div>
        <div class="odds-label">让平</div>
      </div>
      <div
        class="odds-btn"
        :class="{ selected: isSelected('hhad', 'lose') }"
        @click="select('hhad', 'lose', match.hhad.lose, '让负')"
      >
        <span class="had-tag">&nbsp;</span>
        <div class="odds-val">{{ match.hhad.lose }}</div>
        <div class="odds-label">让负</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  match: Object,
  selected: Array
})

const emit = defineEmits(['select'])

const handicapText = computed(() => {
  const h = props.match.handicap
  if (h > 0) return `+${h}`
  return `${h}`
})

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

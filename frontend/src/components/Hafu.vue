<template>
  <div class="hafu-grid">
    <div class="hafu-items">
      <div
        v-for="(odds, key) in hafuList"
        :key="key"
        class="hafu-btn"
        :class="{ selected: isSelected('hafu', key) }"
        @click="select('hafu', key, match.hafu[key], hafuMap[key])"
      >
        <div class="hafu-text">{{ hafuMap[key] }}</div>
        <div class="hafu-odds">{{ match.hafu[key] }}</div>
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

const hafuMap = {
  '胜胜': '胜-胜',
  '胜平': '胜-平',
  '胜负': '胜-负',
  '平胜': '平-胜',
  '平平': '平-平',
  '平负': '平-负',
  '负胜': '负-胜',
  '负平': '负-平',
  '负负': '负-负'
}

const hafuList = ['胜胜','胜平','胜负','平胜','平平','平负','负胜','负平','负负']

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

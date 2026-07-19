const roundMoney = value => Math.round((Number(value) + Number.EPSILON) * 100) / 100

const selectedMaxOdds = selectedItems => {
  const groups = new Map()
  ;(selectedItems || []).forEach(item => {
    const matchId = String(item?.matchId ?? item?.match_id ?? '')
    const odds = Number(item?.odd)
    if (!matchId || !Number.isFinite(odds) || odds <= 0) return
    groups.set(matchId, Math.max(groups.get(matchId) || 0, odds))
  })
  return [...groups.values()]
}

const selectedOptionCounts = selectedItems => {
  const groups = new Map()
  ;(selectedItems || []).forEach(item => {
    const matchId = String(item?.matchId ?? item?.match_id ?? '')
    if (!matchId) return
    groups.set(matchId, (groups.get(matchId) || 0) + 1)
  })
  return [...groups.values()]
}

const combinationNotes = (counts, size, start = 0, picked = 0, product = 1) => {
  if (picked === size) return product
  let total = 0
  const remaining = size - picked
  for (let index = start; index <= counts.length - remaining; index += 1) {
    total += combinationNotes(
      counts,
      size,
      index + 1,
      picked + 1,
      product * counts[index]
    )
  }
  return total
}

export const calculatePassNotes = (selectedItems, passSize) => {
  const counts = selectedOptionCounts(selectedItems)
  const size = Number(passSize)
  if (!Number.isInteger(size) || size < 1 || size > counts.length) return 0
  return combinationNotes(counts, size)
}

const combinationPayout = (odds, size, multiplier, start = 0, picked = 0, product = 1) => {
  if (picked === size) return roundMoney(product * 2 * multiplier)
  let total = 0
  const remaining = size - picked
  for (let index = start; index <= odds.length - remaining; index += 1) {
    total += combinationPayout(
      odds,
      size,
      multiplier,
      index + 1,
      picked + 1,
      product * odds[index]
    )
  }
  return total
}

export const calculateMaxBonus = (selectedItems, passCounts, multiplier = 1) => {
  const odds = selectedMaxOdds(selectedItems)
  const multiple = Math.max(1, Number.parseInt(multiplier, 10) || 1)
  const passes = [...new Set((passCounts || []).map(Number))]
    .filter(size => Number.isInteger(size) && size >= 1 && size <= odds.length)
    .sort((a, b) => a - b)

  const total = passes.reduce(
    (sum, size) => sum + combinationPayout(odds, size, multiple),
    0
  )
  return roundMoney(total)
}

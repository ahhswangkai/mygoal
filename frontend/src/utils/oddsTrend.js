export const oddsTrend = (flag) => {
  const value = Number(flag)
  if (value === 1) return 'up'
  // 体彩官方接口使用 -1 表示下降；兼容旧接口曾使用的 2。
  if (value === -1 || value === 2) return 'down'
  return ''
}

export const oddsTrendArrow = (flag) => {
  const trend = oddsTrend(flag)
  if (trend === 'up') return '↑'
  if (trend === 'down') return '↓'
  return ''
}

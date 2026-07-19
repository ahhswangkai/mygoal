const SCORE_KEYS = {
  s01s00: '1:0', s02s00: '2:0', s02s01: '2:1',
  s03s00: '3:0', s03s01: '3:1', s03s02: '3:2',
  s04s00: '4:0', s04s01: '4:1', s04s02: '4:2',
  s05s00: '5:0', s05s01: '5:1', s05s02: '5:2',
  s00s00: '0:0', s01s01: '1:1', s02s02: '2:2', s03s03: '3:3',
  s00s01: '0:1', s00s02: '0:2', s01s02: '1:2',
  s00s03: '0:3', s01s03: '1:3', s02s03: '2:3',
  s00s04: '0:4', s01s04: '1:4', s02s04: '2:4',
  s00s05: '0:5', s01s05: '1:5', s02s05: '2:5',
  s1sh: '胜其他', s1sd: '平其他', s1sa: '负其他'
}

const GOAL_KEYS = {
  s0: '0', s1: '1', s2: '2', s3: '3',
  s4: '4', s5: '5', s6: '6', s7: '7+'
}

const HAFU_KEYS = {
  hh: '胜胜', hd: '胜平', ha: '胜负',
  dh: '平胜', dd: '平平', da: '平负',
  ah: '负胜', ad: '负平', aa: '负负'
}

const number = value => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

const flag = value => {
  const parsed = Number.parseInt(value, 10)
  return Number.isFinite(parsed) ? parsed : 0
}

const parseThreeWay = (pool, includeGoalLine = false) => ({
  win: number(pool?.h),
  draw: number(pool?.d),
  lose: number(pool?.a),
  winFlag: flag(pool?.hf),
  drawFlag: flag(pool?.df),
  loseFlag: flag(pool?.af),
  ...(includeGoalLine ? { goalLine: pool?.goalLine || '' } : {})
})

const parseMappedOdds = (pool, keyMap) => Object.entries(keyMap).reduce(
  (result, [sourceKey, displayKey]) => {
    const odds = number(pool?.[sourceKey])
    if (odds > 0) result[displayKey] = odds
    return result
  },
  {}
)

export const normalizeSportteryCalculatorPayload = payload => {
  if (!payload?.success) {
    throw new Error(payload?.errorMessage || '体彩接口返回失败')
  }

  const groups = payload?.value?.matchInfoList
  if (!Array.isArray(groups)) throw new Error('体彩比赛数据格式异常')

  return groups.flatMap(group => {
    const businessDate = String(group?.businessDate || '')
    const subMatches = Array.isArray(group?.subMatchList) ? group.subMatchList : []

    return subMatches.map((match, index) => {
      const pools = Array.isArray(match?.poolList) ? match.poolList : []
      const poolSingles = Object.fromEntries(
        pools.map(pool => [
          String(pool?.poolCode || '').toUpperCase(),
          Number(pool?.bettingSingle || 0)
        ])
      )

      return {
        id: String(match?.matchId ?? `${businessDate}-${index}`),
        num: match?.matchNumStr || `比赛${index + 1}`,
        league: match?.leagueAbbName || match?.leagueAllName || match?.leagueName || '',
        date: businessDate,
        dateText: businessDate,
        time: match?.matchTime || '',
        homeTeam: match?.homeTeamAbbName || match?.homeTeamAllName || '',
        awayTeam: match?.awayTeamAbbName || match?.awayTeamAllName || '',
        handicap: number(match?.hhad?.goalLineValue),
        had: parseThreeWay(match?.had),
        hhad: parseThreeWay(match?.hhad, true),
        score: parseMappedOdds(match?.crs, SCORE_KEYS),
        goals: parseMappedOdds(match?.ttg, GOAL_KEYS),
        hafu: parseMappedOdds(match?.hafu, HAFU_KEYS),
        hadSingle: poolSingles.HAD || 0,
        hhadSingle: poolSingles.HHAD || 0
      }
    })
  })
}

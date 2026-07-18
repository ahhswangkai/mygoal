---
name: schedule-fatigue
description: 根据未来赛程间隔识别潜在轮换与体能压力
version: 1.0.0
priority: 70
requires_any:
  - analysis.future.home
  - analysis.future.away
---
# 赛程规则

1. 只根据输入中明确给出的比赛间隔讨论赛程压力。
2. 赛程密集只能作为风险，不得擅自断言球队一定轮换。
3. 双方赛程都密集时，应比较相对差异而非只描述一方。
4. 不得编造伤病、停赛或预计首发。

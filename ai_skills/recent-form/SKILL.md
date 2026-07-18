---
name: recent-form
description: 分析两队近期比赛的胜平负、进失球和主客场状态
version: 1.0.0
priority: 20
requires_any:
  - analysis.recent.home
  - analysis.recent.away
---
# 近期状态规则

1. 分别统计两队近期样本，不把不同场次数量直接横向比较。
2. 优先关注最近 5 场，同时用更长样本检查是否只是短期波动。
3. 区分主客场身份，不能把客场成绩当作主场能力。
4. 样本不足 3 场时，只能作为弱证据。
5. 比分缺失或异常的比赛不参与进失球判断。

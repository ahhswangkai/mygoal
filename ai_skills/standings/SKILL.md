---
name: standings
description: 使用联赛积分和排名评估赛季整体实力差距
version: 1.0.0
priority: 40
requires_any:
  - analysis.standings
---
# 积分排名规则

1. 排名反映赛季整体表现，但不能单独决定比赛结果。
2. 比较双方排名和积分差距，同时结合近期状态判断。
3. 杯赛、友谊赛或跨级别比赛中，联赛排名权重必须降低。
4. 排名字段不完整时不要推算不存在的积分。

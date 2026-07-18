---
name: head-to-head
description: 分析双方历史交锋，同时控制年代、主客场和样本偏差
version: 1.0.0
priority: 30
requires_any:
  - analysis.history
---
# 历史交锋规则

1. 历史交锋只作为辅助证据，不能覆盖近期状态和当前盘口。
2. 优先使用最近交锋，过久数据必须降低权重。
3. 注意本场主客身份是否与历史比赛一致。
4. 少于 3 场时不得形成强结论。

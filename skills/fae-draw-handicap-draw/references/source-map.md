# 源码映射

当规则文档和代码出现差异时，以以下源码为准。

## 核心研判

- `football_ai/daily_analysis.py`
  - 顶部常量：策略档位、正式池阈值、展示数量、亚盘风险 ID。
  - `DRAW_SELECTION_POLICIES`：保守、均衡、激进三档候选门槛。
  - `_league_specific_draw_signal`：普通平局联赛窄模型。
  - `_handicap_draw_path_signal`：竞彩让球三项与亚盘的一球差路径。
  - `_sporttery_draw_price_signal`：让胜/受让负与让平的价格比较及有限概率修正。
  - `_league_specific_handicap_draw_signal`：让平联赛窄模型。
  - `_draw_odds_band_signal`：均势平、冷平、让平赔率区间及硬否决。
  - `_draw_radar_candidate`：雷达概率、价值与综合分计算。
  - `_draw_radar_hard_veto_reasons`：禁止正式推荐的确定性原因。
  - `_radar_official_level`：正式核心/小试第二道门禁。
  - `_radar_rank_score`：正式池排序。
  - `attach_draw_radar_summary`：榜单互斥、排序与 Top 3 展示。

## 历史赔率规则

- `football_ai/market_rules.py`
  - `evaluate_historical_market_rules`：固定窗口赔率与联赛低权重修正。
  - `HISTORICAL_MARKET_RULES_WINDOW`：回放样本窗口。
  - `ORDINARY_DRAW_LEAGUE_PRIORS`、`HANDICAP_DRAW_LEAGUE_PRIORS`：联赛先验。

## 历史进球差与监督影子模型

- `football_ai/league_profile.py`：相似历史比赛和亚盘风险画像。
- `football_ai/supervised.py`：普通平/让平监督概率；默认仍是 shadow，不直接覆盖正式门禁。

## 复盘与学习

- `football_ai/daily_review.py`：全量比赛、雷达和双选的确定性结算。
- `football_ai/ai_review.py`：Ark 赛后深度诊断；确定性统计优先于大模型文字。
- `football_ai/review_memory.py`：只加载目标日期之前的持续复盘记忆。
- `football_ai/skills.py`、`football_ai/backtest.py`：候选规则、样本门禁和发布验证。

## API 与持久化

- `web_app.py`
  - `GET /api/matches`：本地 Skill 默认且唯一的线上原始输入。
  - `GET /api/fae/review-memory`：目标日期之前的紧凑持续复盘记忆。
  - `GET/POST /api/fae/daily-ai`
  - `GET /api/fae/daily-ai/match/<match_id>`
  - `GET/POST /api/fae/daily-ai/review`
- `database/mongodb.py`（如目录结构调整，用 `rg "fae_daily_ai"` 定位）：研判 run、逐场快照和复盘存储。

## 建议核对命令

```bash
rg -n "DRAW_SELECTION_POLICIES|RADAR_OFFICIAL|HANDICAP_DRAW_FORMAL" football_ai/daily_analysis.py
rg -n "def _league_specific_draw_signal|def _draw_odds_band_signal|def _radar_official_level" football_ai/daily_analysis.py
rg -n "HISTORICAL_MARKET_RULES|evaluate_historical_market_rules" football_ai/market_rules.py
rg -n "daily-ai" web_app.py football_ai/README.md
```

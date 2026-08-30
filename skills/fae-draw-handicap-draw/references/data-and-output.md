# 数据字段与核验输出

## 本地规则模式的线上输入

请求：

```text
GET /api/matches?date=YYYY-MM-DD&page=1&page_size=200
```

只读取比赛身份、状态和以下原始盘口字段：

- 欧赔：`euro_initial_win/draw/lose`、`euro_current_win/draw/lose`
- 亚盘：`asian_initial_home_odds/handicap/away_odds`、`asian_current_home_odds/handicap/away_odds`
- 竞彩让球：`hi_handicap_value`（缺失时使用 `handicap`）、`hi_initial_*_odds`、`hi_current_*_odds`
- 大小球：`ou_initial_over_odds/total/under_odds`、`ou_current_over_odds/total/under_odds`
- 身份：`match_id`、`match_number`、`owner_date`、`league`、`match_time`、`status`、主客队

另外读取日期隔离的持续复盘记忆：

```text
GET /api/fae/review-memory?date=YYYY-MM-DD
```

该接口只返回目标日期之前的紧凑记忆，不返回当天研判。接口未部署时，本地脚本读取历史 `/api/fae/daily-ai/review` 并在本地构建同结构记忆。

本地脚本不得读取以下线上研判字段：

- `/api/fae/daily-ai` 的 `analysis`、`daily_summary`、`draw_radar`、`pools`
- Ark 文本、AI 概率、AI 星级、AI 推荐和复盘摘要
- 线上已经计算好的赔率价值、雷达分或 tier

本地执行：

```bash
.venv/bin/python skills/fae-draw-handicap-draw/scripts/local_select.py --date 2026-08-30
.venv/bin/python skills/fae-draw-handicap-draw/scripts/local_select.py --date 2026-08-30 --json
```

本地输出先记录：`mode=local-deterministic-rules`、`uses_daily_ai=false`、`uses_ark=false`、`uses_review_memory=true`、`engine_version`、`policy`、`generated_at`、`match_count`。

记忆输出记录：`memory_hash`、`before_date`、`source_dates`、`review_days`、`observation_count`、`validated_pattern_count`、`local_source`。比赛候选记录实际匹配的验证模式与概率/雷达调整。

## 本地生成的比赛级字段

雷达候选应核验：

- `selection`、`definition`、`target_goal_difference`
- `tier`、`formal_eligible`、`official_veto_reasons`
- `probability`、`market_probability`、`historical_probability`
- `odds`、`odds_value`、`score`、`rating`
- `effective_sample`、`confidence`
- `draw_odds_band_signal.kind/note/sample/hit_rate/roi`
- `risk_pattern_ids`、`reason`

脚本本地生成并核验：

- `ordinary_draw`、`handicap_draw`
- `local_status`：正式核心、小试、雷达核心（未过正式池）、观察、排除
- `probability`、`market_probability`、`historical_probability`
- `odds`、`odds_value`、`score`、`rating`
- `draw_odds_band_signal`、`risk_pattern_ids`、`official_veto_reasons`
- `markets`：用于审计的本次原始盘口快照

脚本直接调用项目中的确定性 `FootballAIEngine`、`build_daily_match_input`、`apply_draw_radar` 与 `_radar_official_level`。规则表仍来自本地代码，线上只提供原始数据。

## 与线上研判核对（非默认）

请求：

```text
GET /api/fae/daily-ai/match/<match_id>?date=YYYY-MM-DD
```

只有用户明确要求核对时才读取该接口。比较时将“本地规则结果”和“线上研判结果”分栏展示，不能用线上结果修改本地结果。

## 赛后复盘

请求：

```text
GET /api/fae/daily-ai/review?date=YYYY-MM-DD
```

服务端按该日期最新 `run_id` 读取复盘。检查：

- `data.completed`、`pending_matches`
- `data.match_results`
- `data.draw_radar_results`
- `data.summary.draw_radar.ordinary_draw`
- `data.summary.draw_radar.handicap_draw`
- `data.ai_deep_review`

结算普通平局看 90 分钟比分是否相等；结算让平必须使用预测快照中的让球数。复盘展示应使用 `match_number`，内部 `match_id` 只用于关联。

## 输出状态映射

| 数据状态 | 对用户文案 |
|---|---|
| `radar_official_level=core` | 正式核心 |
| `radar_official_level=small` | 小试 |
| `tier=core` 但没有正式 level | 雷达核心、正式门禁未通过；按观察展示 |
| `tier=watch` | 观察 |
| `tier=exclude` | 排除 |
| `formal_eligible=false` | 必须附具体否决原因 |

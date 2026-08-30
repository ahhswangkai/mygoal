# 本地持续复盘记忆

## 数据边界

- 目标日期为 `D` 时，只允许 `owner_date < D` 且深度复盘状态为 `completed` 的记录进入记忆。
- 默认读取最近 7 个已复盘比赛日；接口不可用时扫描前 21 个自然日，并在本地调用 `build_review_memory` 压缩。
- 优先请求 `GET /api/fae/review-memory?date=D`。接口尚未部署时回退到 `GET /api/fae/daily-ai/review?date=历史日期`，只读取复盘事实，不读取当天研判作为预测输入。
- 本地快照默认保存到 `.local/fae-draw-handicap-draw/review-memory/`。联网成功时刷新；联网失败时使用相同目标日期的缓存。

## 两级记忆

### 近期观察

最近 3 个复盘日的结论、失败原因、风险形态和市场教训属于 `unvalidated-observation`：

- 只展示为核验提醒。
- 不改变概率、雷达分、tier 或正式资格。
- 单日 0% 或 100% 命中率不得转成禁选或必选规则。

### 跨日验证模式

相同 `scope + action + target` 至少在 2 个比赛日重复、累计至少 10 场独立证据后，才进入 `historically-validated-memory`：

- 只有与当前候选风险概念实际匹配时才应用。
- 单场概率总校正限制为 `-1.5pp~+1.5pp`，雷达总校正限制为 `-3~+3` 分。
- 校正后重新计算赔率价值并重跑硬否决；记忆不能覆盖数据异常、负价值和正式规则 allow-list。
- 每个候选必须输出 `review_memory.matched_validated_patterns` 与具体调整，便于审计。

## 命令

正常联网并持久化记忆：

```bash
.venv/bin/python skills/fae-draw-handicap-draw/scripts/local_select.py --date YYYY-MM-DD
```

完全离线复算：

```bash
.venv/bin/python skills/fae-draw-handicap-draw/scripts/local_select.py \
  --date YYYY-MM-DD \
  --input raw-matches.json \
  --memory-input review-memory.json
```

仅做无记忆对照实验：

```bash
.venv/bin/python skills/fae-draw-handicap-draw/scripts/local_select.py \
  --date YYYY-MM-DD --no-memory
```

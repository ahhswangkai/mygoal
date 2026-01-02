# 比赛预测与复盘系统使用说明

## 📊 系统概述

这是一个智能足球比赛预测与复盘系统，能够：

1. **自动预测**未开始的比赛结果（胜负、亚盘、大小球）
2. **定时任务**每天定时执行预测和复盘
3. **自动复盘**比赛结束后对比预测与实际结果
4. **准确率统计**生成预测准确率报告

---

## 🚀 快速开始

### 1. 启动定时任务调度器

```bash
cd /Users/kai/workspace/AI/football-crawler

# 启动预测与复盘定时任务
python3 prediction_scheduler.py
```

**任务时间表：**
- 每天 **08:00** - 预测当天比赛
- 每天 **14:00** - 更新预测（即时赔率）
- 每天 **22:00** - 临盘预测
- 每天 **03:00** - 复盘已完场比赛
- 每天 **10:00** - 复盘凌晨结束的比赛

---

### 2. 手动预测单场比赛

```bash
# 方法1: 使用Web API
curl http://127.0.0.1:5002/api/predict/1215875

# 方法2: 使用Python脚本
python3 -c "
from prediction_engine import PredictionEngine
from db_storage import MongoDBStorage

storage = MongoDBStorage()
engine = PredictionEngine()

match = storage.get_match_by_id('1215875')
prediction = engine.predict_match(match)

print(f'预测结果: {prediction[\"win_prediction\"]}')
print(f'置信度: {prediction[\"win_confidence\"]:.1f}%')

storage.save_prediction(prediction)
"
```

---

### 3. 手动复盘单场比赛

```bash
# 方法1: 使用Web API
curl http://127.0.0.1:5002/api/review/1215875

# 方法2: 使用Python脚本
python3 -c "
from prediction_review import PredictionReviewer

reviewer = PredictionReviewer()
result = reviewer.review_match('1215875')

if result:
    print(f'准确度: {result[\"accuracy\"]:.1f}%')
    print(f'胜负预测: {\"✅\" if result[\"win_correct\"] else \"❌\"}')
    print(f'亚盘预测: {\"✅\" if result[\"asian_correct\"] else \"❌\"}')
    print(f'大小球预测: {\"✅\" if result[\"ou_correct\"] else \"❌\"}')
"
```

---

### 4. 批量复盘所有完场比赛

```bash
python3 -c "
from prediction_review import PredictionReviewer

reviewer = PredictionReviewer()
results = reviewer.review_all_finished_matches()

print(f'复盘了 {len(results)} 场比赛')
"
```

---

### 5. 查看预测准确率报告

```bash
# 方法1: 使用Web API
curl http://127.0.0.1:5002/api/review/summary?days=7

# 方法2: 使用Python脚本
python3 -c "
from prediction_review import PredictionReviewer

reviewer = PredictionReviewer()
summary = reviewer.generate_summary_report(days=7)

if summary:
    print(f'最近7天统计:')
    print(f'  总场次: {summary[\"total_matches\"]}')
    print(f'  胜负准确率: {summary[\"win_accuracy\"]:.1f}%')
    print(f'  亚盘准确率: {summary[\"asian_accuracy\"]:.1f}%')
    print(f'  大小球准确率: {summary[\"ou_accuracy\"]:.1f}%')
    print(f'  总体准确度: {summary[\"avg_accuracy\"]:.1f}%')
"
```

---

## 📡 Web API 接口

启动Web服务后，可通过以下API访问：

```bash
# 启动Web服务
python3 web_app.py
```

### API 列表

#### 1. 获取预测列表
```bash
GET /api/predictions?is_reviewed=false&limit=50
```

**参数：**
- `is_reviewed`: `true`/`false` - 是否已复盘
- `limit`: 返回数量（默认50）

**返回示例：**
```json
{
  "success": true,
  "data": [
    {
      "match_id": "1215875",
      "home_team": "毕尔巴鄂",
      "away_team": "皇马",
      "win_prediction": "away",
      "win_confidence": 75.0,
      "asian_prediction": "away",
      "ou_prediction": "over",
      "predicted_home_score": 0,
      "predicted_away_score": 2
    }
  ],
  "count": 1
}
```

#### 2. 预测指定比赛
```bash
GET /api/predict/<match_id>
```

#### 3. 复盘指定比赛
```bash
GET /api/review/<match_id>
```

#### 4. 获取复盘汇总报告
```bash
GET /api/review/summary?days=7
```

---

## 💾 数据库结构

### predictions 集合

存储预测结果：

```javascript
{
  "match_id": "1215875",
  "league": "西甲",
  "match_time": "12-04 02:00",
  "home_team": "毕尔巴鄂",
  "away_team": "皇马",
  
  // 胜负预测
  "win_prediction": "away",  // 'home', 'draw', 'away'
  "win_confidence": 75.0,
  
  // 亚盘预测
  "asian_prediction": "away",  // 'home', 'away'
  "asian_handicap": "受半球",
  "asian_confidence": 70.0,
  
  // 大小球预测
  "ou_prediction": "over",  // 'over', 'under'
  "ou_total": 2.5,
  "ou_confidence": 60.0,
  
  // 比分预测
  "predicted_home_score": 0,
  "predicted_away_score": 2,
  
  // 预测时间
  "predict_date": "2025-12-02T10:00:00",
  "is_reviewed": false,
  
  // 复盘结果（完场后更新）
  "actual_home_score": 0,
  "actual_away_score": 2,
  "win_correct": true,
  "asian_correct": true,
  "ou_correct": false,
  "accuracy": 75.0,
  "review_date": "2025-12-05T10:00:00"
}
```

---

## 🧠 预测逻辑说明

### 1. 胜负预测
- 基于欧赔分析（赔率越低，庄家越看好）
- 结合球队近期状态（胜率、进球数）
- 置信度：50%-90%

### 2. 亚盘预测
- 基于亚盘水位（高水支持对手，低水支持本队）
- 分析盘口变动趋势
- 置信度：50%-70%

### 3. 大小球预测
- 基于球队大小球走势（最近10场大球率）
- 结合赔率水位变化
- 置信度：50%-75%

### 4. 比分预测
- 基于球队场均进球数
- 根据胜负预测调整
- 仅作参考

---

## 📈 准确率统计

系统会自动统计以下指标：

- **胜负准确率**：预测胜负是否正确
- **亚盘准确率**：预测让球盘是否正确
- **大小球准确率**：预测大小球是否正确
- **比分准确率**：预测比分是否完全正确
- **总体准确度**：4项预测的平均准确率

---

## ⚠️ 注意事项

1. **赔率数据依赖**：预测需要有赔率数据，确保先爬取赔率
2. **复盘时机**：只能复盘已完场（status=2）的比赛
3. **置信度参考**：置信度仅供参考，不代表绝对准确率
4. **定时任务**：建议后台运行 `prediction_scheduler.py`
5. **数据备份**：定期备份MongoDB数据库

---

## 🔧 自定义配置

### 修改定时任务时间

编辑 `prediction_scheduler.py`：

```python
# 修改预测时间
scheduler.add_job(
    daily_prediction_task,
    CronTrigger(hour=9, minute=0),  # 改为9:00执行
    ...
)
```

### 调整预测逻辑

编辑 `prediction_engine.py` 中的预测函数：

```python
def _predict_winner(self, home_form, away_form, euro_win, euro_draw, euro_lose):
    # 自定义胜负预测逻辑
    ...
```

---

## 📊 使用示例

### 场景1：每天自动预测和复盘

```bash
# 后台启动定时任务
nohup python3 prediction_scheduler.py > logs/prediction.log 2>&1 &

# 查看日志
tail -f logs/prediction.log
```

### 场景2：查看今天的预测

```bash
curl http://127.0.0.1:5002/api/predictions?is_reviewed=false&limit=20
```

### 场景3：查看最近7天准确率

```bash
curl http://127.0.0.1:5002/api/review/summary?days=7
```

---

## 🐛 故障排除

### 问题1：预测失败
- 检查比赛是否有赔率数据
- 确认球队历史数据是否充足

### 问题2：复盘失败
- 确认比赛是否已完场（status=2）
- 检查比分数据是否完整

### 问题3：定时任务未执行
- 检查进程是否运行：`ps aux | grep prediction_scheduler`
- 查看日志文件是否有错误信息

---

## 📞 支持

如有问题，请检查：
1. MongoDB是否正常运行
2. 爬虫数据是否完整
3. 日志文件错误信息

祝预测准确率节节高升！🎯

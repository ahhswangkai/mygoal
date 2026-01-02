# 前端预测展示说明

## 📺 前端展示功能

已在前端添加AI预测数据展示，包括：

### 1️⃣ 首页表格 - AI预测列

**位置**：首页比赛列表表格中新增"AI预测"列

**显示内容**：
- 未完场比赛：显示预测结果 + 置信度（如：`主胜 85%`）
- 已完场比赛：显示准确度 + 图标（如：`✅ 75%` 或 `❌ 50%`）

**交互功能**：
- 点击预测单元格：弹窗显示详细预测信息
- 弹窗包含：胜负、亚盘、大小球、比分预测
- 如已复盘，显示实际结果和准确度对比

**样式说明**：
- 绿色（✅）：准确度 ≥ 75%
- 橙色（⚠️）：准确度 50-75%
- 红色（❌）：准确度 < 50%
- 蓝色：未完场的预测

---

### 2️⃣ 比赛详情页 - AI预测卡片

**位置**：比赛详情页，赔率变动上方

**显示内容**：
1. **胜负预测卡**
   - 预测结果（主胜/平局/客胜）
   - 置信度
   - 复盘结果（完场后显示）

2. **亚盘预测卡**
   - 预测结果（主队/客队）
   - 盘口
   - 置信度
   - 复盘结果

3. **大小球预测卡**
   - 预测结果（大球/小球）
   - 盘口
   - 置信度
   - 复盘结果

4. **比分预测卡**
   - 预测比分
   - 实际比分（完场后）
   - 是否完全匹配

5. **复盘统计**（仅完场比赛）
   - 总体准确度
   - 命中项目数

**交互功能**：
- 如比赛未预测，显示"生成预测"按钮
- 点击按钮自动调用预测引擎生成预测

---

## 🚀 使用方法

### 查看预测

1. **访问首页**
```
http://127.0.0.1:5002/
```

2. **查看AI预测列**
   - 表格最右侧"AI预测"列
   - 绿色✅表示预测准确，红色❌表示预测错误
   - 蓝色表示未完场的预测

3. **点击预测查看详情**
   - 点击任意预测单元格
   - 弹窗显示完整预测信息

4. **进入详情页**
   - 点击"详情"按钮
   - 查看完整预测和复盘数据

---

### 生成预测

**方法1：命令行批量预测**
```bash
cd /Users/kai/workspace/AI/football-crawler
python3 predict.py predict
```

**方法2：详情页单独预测**
1. 访问比赛详情页
2. 如显示"未预测"，点击"🤖 生成预测"按钮
3. 等待预测完成，页面自动刷新

**方法3：API接口**
```bash
# 预测单场
curl http://127.0.0.1:5002/api/predict/1215875

# 批量预测所有未开始的比赛
python3 predict.py predict
```

---

## 📊 数据说明

### 预测字段

```javascript
{
  "match_id": "1215875",
  "win_prediction": "away",      // 胜负预测: home/draw/away
  "win_confidence": 75.0,        // 置信度 (%)
  "asian_prediction": "away",    // 亚盘预测: home/away
  "asian_handicap": "受半球",    // 亚盘盘口
  "asian_confidence": 70.0,
  "ou_prediction": "over",       // 大小球预测: over/under
  "ou_total": 2.5,              // 大小球盘口
  "ou_confidence": 60.0,
  "predicted_home_score": 0,    // 预测比分
  "predicted_away_score": 2
}
```

### 复盘字段（完场后）

```javascript
{
  "actual_home_score": 0,       // 实际比分
  "actual_away_score": 2,
  "win_correct": true,          // 胜负是否正确
  "asian_correct": true,        // 亚盘是否正确
  "ou_correct": false,          // 大小球是否正确
  "score_correct": true,        // 比分是否完全正确
  "accuracy": 75.0,             // 总体准确度
  "is_reviewed": true           // 是否已复盘
}
```

---

## 🎨 样式定制

### CSS类名

```css
/* 预测单元格 */
.prediction-cell {
    cursor: pointer;
    transition: background 0.2s;
}

/* 预测准确 (≥75%) */
.pred-success {
    color: #48bb78;
    font-weight: 600;
}

/* 预测一般 (50-75%) */
.pred-warning {
    color: #f6ad55;
    font-weight: 600;
}

/* 预测错误 (<50%) */
.pred-fail {
    color: #fc8181;
    font-weight: 600;
}

/* 未完场预测 */
.pred-pending {
    color: #667eea;
    font-weight: 500;
}
```

---

## 🔧 自定义配置

### 修改预测显示格式

编辑 `templates/index.html` 中的 `getPredictionText` 函数：

```javascript
function getPredictionText(pred, status) {
    // 自定义显示逻辑
    ...
}
```

### 修改弹窗内容

编辑 `showPredictionDetail` 函数：

```javascript
function showPredictionDetail(pred, match) {
    // 自定义弹窗HTML
    ...
}
```

---

## 📱 移动端适配

前端已自动适配移动端：
- 表格自动滚动
- 弹窗自适应屏幕宽度
- 触摸友好的交互

---

## 🐛 常见问题

### 1. 预测列显示"—"
**原因**：该比赛尚未预测
**解决**：运行 `python3 predict.py predict` 批量预测

### 2. 点击预测无反应
**原因**：未加载到预测数据
**解决**：检查浏览器控制台错误，确认API `/api/predictions` 正常

### 3. 详情页无法生成预测
**原因**：比赛缺少赔率数据
**解决**：先爬取赔率数据，再生成预测

### 4. 复盘数据未显示
**原因**：比赛未完场或未执行复盘
**解决**：
```bash
# 手动复盘
python3 predict.py review
```

---

## 🎯 最佳实践

1. **每天定时预测**
   - 运行定时任务：`python3 prediction_scheduler.py`
   - 自动预测未开始的比赛

2. **每天定时复盘**
   - 定时任务会自动复盘完场比赛
   - 也可手动运行：`python3 predict.py review`

3. **查看汇总报告**
   ```bash
   python3 predict.py summary --days 7
   ```

4. **刷新页面查看最新数据**
   - 点击"🔄 刷新数据"按钮
   - 预测数据自动异步加载

---

## 📞 技术支持

如有问题，请检查：
1. MongoDB是否运行
2. Web服务是否启动（`python3 web_app.py`）
3. 预测数据是否存在（`python3 predict.py summary`）
4. 浏览器控制台是否有错误

祝预测准确率节节高升！🎉

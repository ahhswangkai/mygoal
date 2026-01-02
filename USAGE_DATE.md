# 按日期爬取比赛数据使用指南

## 功能说明

现在支持通过日期参数爬取指定日期的比赛数据，并且会自动处理：
- **未开始的比赛**：爬取赔率数据（欧赔、亚盘、大小球的即时盘和初盘）
- **已结束的比赛**：保存最终比分数据
- **进行中的比赛**：记录当前状态

## 使用方法

### 1. 爬取当天比赛（默认）
```bash
python3 main.py
```

### 2. 爬取指定日期的比赛 ⭐
```bash
# 爬取2025年11月30日的比赛
python3 main.py --date 2025-11-30

# 爬取并导出为Excel格式
python3 main.py --date 2025-11-30 --format excel

# 爬取并导出为JSON格式
python3 main.py --date 2025-12-01 --format json
```

### 3. 完整参数说明
```bash
python3 main.py --site 500wan --date 2025-11-30 --format csv
```

**参数说明**：
- `--site`: 目标网站（默认：500wan）
  - `500wan`: 500彩票网
  - `zgzcw`: 中国足彩网
  - `okooo`: 澳客网
- `--date`: 指定日期（格式：YYYY-MM-DD，如：2025-11-30）
  - 不指定则爬取当天
- `--format`: 保存格式（默认：csv）
  - `csv`: CSV文件
  - `json`: JSON文件
  - `excel`: Excel文件

## 输出文件

### 文件类型

1. **所有比赛数据** - `matches_YYYYMMDD_HHMMSS.csv`
   - 包含当天所有比赛（未开始、进行中、已结束）
   - 已结束的比赛包含最终比分

2. **未开始比赛数据** - `matches_YYYYMMDD_HHMMSS.csv`
   - 只包含未开始的比赛

3. **已结束比赛数据** - `matches_YYYYMMDD_HHMMSS.csv`
   - 只包含已结束的比赛
   - ⭐ **包含最终比分数据**

4. **未开始比赛+赔率** - `combined_YYYYMMDD_HHMMSS.csv`
   - 未开始比赛的完整数据
   - 包含欧赔、亚盘、大小球的即时盘和初盘

### 数据字段

#### 基础比赛信息（9个字段）
- `match_id`: 比赛ID
- `round_id`: 场次编号
- `league`: 联赛名称
- `round`: 轮次
- `match_time`: 比赛时间
- `status`: 比赛状态（未开始/进行中/已完场）
- `home_team`: 主队名称
- `score`: **比分**（已结束比赛会显示最终比分，如：2-1）
- `away_team`: 客队名称

#### 赔率数据（18个字段）⭐
**欧洲赔率**：
- `euro_current_win/euro_current_draw/euro_current_lose`: 即时盘
- `euro_initial_win/euro_initial_draw/euro_initial_lose`: 初盘

**亚洲盘口**：
- `asian_current_home_odds/asian_current_handicap/asian_current_away_odds`: 即时盘
- `asian_initial_home_odds/asian_initial_handicap/asian_initial_away_odds`: 初盘

**大小球**：
- `ou_current_over_odds/ou_current_total/ou_current_under_odds`: 即时盘
- `ou_initial_over_odds/ou_initial_total/ou_initial_under_odds`: 初盘

## 使用场景

### 场景1：分析历史比赛结果
```bash
# 爬取昨天的比赛，获取最终比分
python3 main.py --date 2025-11-29
```
**用途**：
- 获取已结束比赛的最终比分
- 分析历史数据
- 验证预测模型准确率

### 场景2：预测今天的比赛
```bash
# 爬取今天未开始的比赛和赔率
python3 main.py --date 2025-11-30
```
**用途**：
- 获取今天未开始比赛的赔率
- 进行赛前分析和预测

### 场景3：批量收集数据
```bash
# 收集最近一周的数据
for date in 2025-11-24 2025-11-25 2025-11-26 2025-11-27 2025-11-28 2025-11-29 2025-11-30; do
    python3 main.py --date $date
    sleep 5
done
```
**用途**：
- 建立历史数据库
- 训练机器学习模型

## 数据示例

### 已结束比赛（带最终比分）
```csv
match_id,league,match_time,status,home_team,score,away_team
1199477,意杯,2025-11-29 21:00,已完场,比萨,1-2,国际米兰
1202951,荷甲,2025-11-29 18:30,已完场,特尔斯达,0-3,费耶诺德
```

### 未开始比赛（带赔率）
```csv
match_id,league,match_time,status,home_team,score,away_team,euro_current_win,euro_current_draw,euro_current_lose,euro_initial_win,euro_initial_draw,euro_initial_lose,...
1216031,西甲,2025-11-30 21:00,,皇家社会,vs,比利亚雷,2.79,3.31,2.53,2.85,3.20,2.40,...
```

## 注意事项

1. **日期格式**：必须使用 `YYYY-MM-DD` 格式，如：`2025-11-30`
2. **比分数据**：只有已结束的比赛才有完整的比分数据（如：2-1）
3. **赔率数据**：只为未开始的比赛爬取赔率
4. **请求频率**：代码已内置延迟机制，避免请求过快被封
5. **数据准确性**：建议在比赛结束后再爬取最终比分

## 常见问题

**Q: 为什么有些比赛没有赔率数据？**  
A: 赔率数据只针对未开始的比赛爬取。已结束的比赛重点是保存最终比分。

**Q: 如何获取某场已结束比赛的详细信息？**  
A: 使用该比赛的日期参数爬取，程序会自动保存已结束比赛及其最终比分。

**Q: 比分显示为"vs"是什么意思？**  
A: 表示比赛未开始，还没有比分。已结束的比赛会显示类似"2-1"的格式。

**Q: 可以爬取未来日期的比赛吗？**  
A: 可以，500彩票网通常会提前发布未来几天的赛程。

## 技术实现

### URL格式
```
https://live.500.com/?e=2025-11-30
```
- 不带日期参数：显示当天比赛
- 带日期参数 `?e=YYYY-MM-DD`：显示指定日期的比赛

### 比赛状态判断
```python
# 未开始：status为空或包含"未"
# 已结束：status包含"完"或"结束"，且有完整比分（如：2-1）
# 进行中：其他情况
```

## 更新日志

**2025-11-30**
- ✅ 支持通过 `--date` 参数指定日期
- ✅ 自动分类比赛状态（未开始/已结束/进行中）
- ✅ 已结束比赛保存最终比分
- ✅ 分别保存不同状态的比赛数据

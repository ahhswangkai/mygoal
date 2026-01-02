# 足彩网站爬虫工具 ⚽

一个用于爬取足彩网站每日比赛信息和赔率数据的Python爬虫工具。

## 功能特点

- ✅ 爬取每日足球比赛信息（联赛、球队、时间等）
- ✅ 爬取比赛赔率数据（欧赔、亚盘等）
- ✅ 支持多个足彩网站（500彩票网、中国足彩网、澳客网等）
- ✅ 多种数据导出格式（CSV、JSON、Excel）
- ✅ 智能反爬虫策略（随机UA、请求延迟、重试机制）
- ✅ 完善的日志记录和异常处理
- ✅ 模块化设计，易于扩展

## 项目结构

```
football-crawler/
├── config.py           # 配置文件
├── crawler.py          # 爬虫核心模块
├── storage.py          # 数据存储模块
├── utils.py            # 工具函数
├── main.py             # 主程序入口
├── requirements.txt    # 依赖包列表
├── .env.example        # 环境变量示例
├── README.md           # 项目说明
├── data/               # 数据存储目录（自动创建）
└── logs/               # 日志目录（自动创建）
```

## 安装依赖

### 1. 创建虚拟环境（推荐）

```bash
cd football-crawler
python3 -m venv venv
source venv/bin/activate  # MacOS/Linux
# 或
venv\Scripts\activate  # Windows
```

### 2. 安装依赖包

```bash
pip install -r requirements.txt
```

## 配置说明

### 1. 复制环境变量文件

```bash
cp .env.example .env
```

### 2. 修改配置

编辑 `config.py` 文件，根据实际需求调整：

- `TARGET_URLS`: 目标网站URL
- `REQUEST_HEADERS`: 请求头
- `REQUEST_DELAY`: 请求延迟（秒）
- `EXPORT_FORMAT`: 默认导出格式

### 3. 自定义解析规则

**重要：** 由于每个足彩网站的HTML结构不同，需要根据实际网站修改解析规则。

在 `crawler.py` 中修改以下方法：

- `parse_match_list()`: 解析比赛列表页面
- `parse_odds()`: 解析赔率数据页面

## 使用方法

### 基本用法

```bash
# 使用默认配置（500彩票网，CSV格式）
python main.py

# 指定网站
python main.py --site zgzcw

# 指定导出格式
python main.py --format json

# 完整参数
python main.py --site okooo --format excel
```

### 参数说明

- `--site`: 目标网站
  - `500wan`: 500彩票网
  - `zgzcw`: 中国足彩网
  - `okooo`: 澳客网

- `--format`: 导出格式
  - `csv`: CSV格式（默认）
  - `json`: JSON格式
  - `excel`: Excel格式

## 数据说明

### 比赛数据字段

```python
{
    'match_id': '比赛ID',
    'league': '联赛名称',
    'match_time': '比赛时间',
    'home_team': '主队',
    'away_team': '客队',
    'status': '比赛状态'
}
```

### 赔率数据字段

```python
{
    'company': '博彩公司',
    'win_odds': '主胜赔率',
    'draw_odds': '平局赔率',
    'lose_odds': '客胜赔率',
    'update_time': '更新时间'
}
```

## 自定义开发

### 1. 添加新的目标网站

在 `config.py` 中添加URL：

```python
TARGET_URLS = {
    'your_site': 'https://example.com/',
}
```

### 2. 自定义解析逻辑

在 `crawler.py` 中修改或添加解析方法：

```python
def parse_custom_data(self, html_content):
    soup = BeautifulSoup(html_content, 'lxml')
    # 添加你的解析逻辑
    return data
```

### 3. 扩展存储格式

在 `storage.py` 中添加新的存储方法。

## 注意事项

⚠️ **重要提醒：**

1. **遵守网站规则**：请遵守目标网站的 `robots.txt` 和使用条款
2. **合理请求频率**：设置适当的请求延迟，避免对服务器造成压力
3. **个人使用**：本工具仅供学习和个人使用，不得用于商业用途
4. **网站结构变化**：网站HTML结构可能会变化，需要及时更新解析规则
5. **法律法规**：确保爬取行为符合当地法律法规

## 常见问题

### Q1: 无法获取数据？

- 检查目标URL是否正确
- 确认网站HTML结构是否发生变化
- 查看日志文件获取详细错误信息

### Q2: 请求被拒绝？

- 增加请求延迟时间
- 检查请求头配置
- 考虑使用代理IP

### Q3: 如何定时爬取？

使用系统定时任务：

```bash
# Linux/Mac - crontab
0 8 * * * cd /path/to/football-crawler && python main.py

# Windows - 任务计划程序
```

## 技术栈

- Python 3.7+
- requests: HTTP请求
- BeautifulSoup4: HTML解析
- pandas: 数据处理
- fake-useragent: UA随机化
- retry: 重试机制

## 更新日志

### v1.0.0 (2025-11-30)
- ✨ 初始版本发布
- ✨ 支持基本的比赛和赔率爬取
- ✨ 支持多种数据导出格式
- ✨ 完善的日志和异常处理

## 许可证

本项目仅供学习交流使用，请勿用于商业用途。

## 贡献

欢迎提交Issue和Pull Request！

---

**免责声明**：使用本工具时请遵守相关法律法规和网站使用条款，作者不对使用本工具产生的任何后果负责。

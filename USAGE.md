# 详细使用指南

## 快速开始

### 1. 一键启动（推荐）

```bash
cd football-crawler
./quickstart.sh
```

这个脚本会自动完成：
- 创建虚拟环境
- 安装所有依赖
- 配置环境变量
- 创建必要的目录

### 2. 手动安装

```bash
# 1. 进入项目目录
cd football-crawler

# 2. 创建虚拟环境
python3 -m venv venv

# 3. 激活虚拟环境
source venv/bin/activate  # MacOS/Linux
# 或
venv\Scripts\activate  # Windows

# 4. 安装依赖
pip install -r requirements.txt

# 5. 配置环境
cp .env.example .env
```

## 核心功能说明

### 1. 爬虫模块 (crawler.py)

`FootballCrawler` 类提供了核心爬虫功能：

```python
from crawler import FootballCrawler

crawler = FootballCrawler()

# 爬取比赛列表
matches = crawler.crawl_daily_matches(url)

# 爬取赔率数据
odds = crawler.crawl_match_odds(match_url)

# 关闭爬虫
crawler.close()
```

#### 主要方法：

- `crawl_daily_matches(url)`: 爬取每日比赛列表
- `crawl_match_odds(match_url)`: 爬取指定比赛的赔率
- `parse_match_list(html)`: 解析比赛列表页面
- `parse_odds(html)`: 解析赔率页面

### 2. 数据存储模块 (storage.py)

`DataStorage` 类负责数据的持久化：

```python
from storage import DataStorage

storage = DataStorage()

# 保存比赛数据
storage.save_matches(matches, format_type='csv')

# 保存赔率数据
storage.save_odds(odds_data, match_id='123')

# 保存组合数据
storage.save_combined_data(matches, odds_dict, format_type='json')
```

#### 支持的格式：

- **CSV**: 表格格式，适合Excel打开
- **JSON**: 结构化数据，适合程序处理
- **Excel**: .xlsx格式，原生Excel支持

### 3. 配置管理 (config.py)

所有配置集中管理：

```python
# 目标网站配置
TARGET_URLS = {
    '500wan': 'https://live.500.com/',
    'zgzcw': 'https://www.zgzcw.com/',
    'okooo': 'https://www.okooo.com/',
}

# 请求配置
REQUEST_TIMEOUT = 30  # 请求超时时间（秒）
REQUEST_DELAY = 2     # 请求延迟（秒）
MAX_RETRIES = 3       # 最大重试次数
```

## 自定义网站解析

### 步骤1：分析目标网站

使用浏览器开发者工具（F12）查看网页结构：

1. 打开目标网站
2. 按F12打开开发者工具
3. 选择"Elements"或"元素"标签
4. 找到比赛列表和赔率数据的HTML结构

### 步骤2：修改解析规则

在 `crawler.py` 中修改 `parse_match_list` 方法：

```python
def parse_match_list(self, html_content):
    """解析比赛列表"""
    soup = BeautifulSoup(html_content, 'lxml')
    matches = []
    
    # 根据实际网站的HTML结构调整选择器
    # 示例1：使用class选择器
    match_items = soup.find_all('div', class_='match-item')
    
    # 示例2：使用CSS选择器
    # match_items = soup.select('.match-list .match-row')
    
    # 示例3：使用标签和属性
    # match_items = soup.find_all('tr', attrs={'data-type': 'match'})
    
    for item in match_items:
        match_data = {
            # 根据实际结构提取数据
            'match_id': item.get('data-id', ''),
            'league': item.find('span', class_='league-name').text.strip(),
            'match_time': item.find('span', class_='time').text.strip(),
            'home_team': item.find('span', class_='home-team').text.strip(),
            'away_team': item.find('span', class_='away-team').text.strip(),
        }
        matches.append(match_data)
    
    return matches
```

### 步骤3：修改赔率解析

在 `crawler.py` 中修改 `parse_odds` 方法：

```python
def parse_odds(self, html_content):
    """解析赔率数据"""
    soup = BeautifulSoup(html_content, 'lxml')
    odds_data = []
    
    # 查找赔率表格
    odds_table = soup.find('table', id='odds-table')
    if not odds_table:
        return odds_data
    
    # 遍历表格行
    for row in odds_table.find_all('tr')[1:]:  # 跳过表头
        cells = row.find_all('td')
        if len(cells) >= 4:
            odds = {
                'company': cells[0].text.strip(),
                'win_odds': cells[1].text.strip(),
                'draw_odds': cells[2].text.strip(),
                'lose_odds': cells[3].text.strip(),
            }
            odds_data.append(odds)
    
    return odds_data
```

## 实际案例

### 案例1：爬取500彩票网

```python
from crawler import FootballCrawler
from storage import DataStorage

crawler = FootballCrawler()
storage = DataStorage()

# 爬取今日比赛
url = "https://live.500.com/"
matches = crawler.crawl_daily_matches(url)

# 保存数据
if matches:
    storage.save_matches(matches, format_type='csv')
    print(f"成功保存 {len(matches)} 场比赛数据")

crawler.close()
```

### 案例2：定时爬取

创建定时任务，每天早上8点自动爬取：

**Linux/Mac (crontab):**

```bash
# 编辑crontab
crontab -e

# 添加定时任务
0 8 * * * cd /path/to/football-crawler && /path/to/venv/bin/python main.py --format csv
```

**Windows (任务计划程序):**

1. 打开"任务计划程序"
2. 创建基本任务
3. 设置触发器：每天早上8:00
4. 操作：启动程序
   - 程序：`C:\path\to\venv\Scripts\python.exe`
   - 参数：`main.py --format csv`
   - 起始于：`C:\path\to\football-crawler`

### 案例3：数据分析

爬取的数据可以用于分析：

```python
import pandas as pd
import matplotlib.pyplot as plt

# 读取爬取的数据
df = pd.read_csv('data/matches_20251130.csv')

# 统计各联赛比赛数量
league_counts = df['league'].value_counts()
print(league_counts)

# 绘制图表
league_counts.plot(kind='bar', title='各联赛比赛数量')
plt.show()
```

## 常见问题解决

### Q1: 爬取失败或返回空数据

**原因**：
- 网站HTML结构已更改
- 网站有反爬虫机制
- 网络连接问题

**解决方法**：
1. 检查网站是否可访问
2. 使用浏览器访问，对比HTML结构
3. 增加请求延迟时间
4. 检查日志文件 `logs/crawler.log`

### Q2: 被网站封IP

**解决方法**：
1. 增加 `REQUEST_DELAY` 参数（如改为5秒）
2. 使用代理IP池
3. 减少并发请求数量

在 `crawler.py` 中添加代理支持：

```python
def _make_request(self, url, method='GET', **kwargs):
    proxies = {
        'http': 'http://your-proxy:port',
        'https': 'https://your-proxy:port',
    }
    response = self.session.get(
        url,
        proxies=proxies,
        **kwargs
    )
    return response
```

### Q3: 数据格式问题

如果导出的CSV文件中文乱码：

```python
# 在 storage.py 中确保使用正确的编码
df.to_csv(filepath, index=False, encoding='utf-8-sig')  # BOM编码，Excel兼容
```

### Q4: 动态网页无法获取数据

某些网站使用JavaScript动态加载数据，需要使用Selenium：

```python
from selenium import webdriver
from selenium.webdriver.common.by import By

def crawl_with_selenium(url):
    driver = webdriver.Chrome()
    driver.get(url)
    
    # 等待页面加载
    time.sleep(3)
    
    # 获取渲染后的HTML
    html = driver.page_source
    
    driver.quit()
    return html
```

## 进阶功能

### 1. 添加数据库存储

安装MySQL支持：
```bash
pip install pymysql sqlalchemy
```

创建数据库连接：
```python
from sqlalchemy import create_engine
import pandas as pd

engine = create_engine('mysql+pymysql://user:pass@localhost/football')
df.to_sql('matches', engine, if_exists='append', index=False)
```

### 2. 多线程爬取

```python
from concurrent.futures import ThreadPoolExecutor

def crawl_match_odds_batch(match_list, max_workers=5):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = executor.map(crawl_single_match, match_list)
    return list(results)
```

### 3. 添加通知功能

爬取完成后发送邮件通知：

```python
import smtplib
from email.mime.text import MIMEText

def send_notification(subject, content):
    msg = MIMEText(content)
    msg['Subject'] = subject
    msg['From'] = 'your-email@example.com'
    msg['To'] = 'receiver@example.com'
    
    with smtplib.SMTP('smtp.example.com', 587) as server:
        server.starttls()
        server.login('your-email@example.com', 'password')
        server.send_message(msg)
```

## 最佳实践

1. **遵守robots.txt**：检查网站的爬虫协议
2. **合理延迟**：不要过于频繁地请求
3. **错误处理**：完善的异常捕获和日志记录
4. **数据验证**：爬取后验证数据的完整性
5. **增量爬取**：避免重复爬取相同数据
6. **定期维护**：网站结构变化时及时更新解析规则

## 性能优化

1. **使用连接池**：复用HTTP连接
2. **异步请求**：使用aiohttp进行异步爬取
3. **缓存机制**：避免重复请求相同URL
4. **分布式爬取**：使用Celery进行任务分发

## 法律声明

- 仅用于学习和研究目的
- 遵守网站服务条款
- 不得用于商业用途
- 尊重网站知识产权
- 合理控制爬取频率

---

如有其他问题，请查看 README.md 或提交Issue。

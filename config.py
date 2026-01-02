"""
配置文件
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent

# 数据存储目录
DATA_DIR = os.getenv('DATA_DIR', './data')
os.makedirs(DATA_DIR, exist_ok=True)

# 目标网站配置
TARGET_URLS = {
    # 以下是常见的足彩网站示例，请根据实际需求修改
    '500wan': 'https://live.500.com/',  # 500彩票网
    'zgzcw': 'https://www.zgzcw.com/',  # 中国足彩网
    'okooo': 'https://www.okooo.com/',  # 澳客网
}

# 请求配置
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
}

REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', 30))
REQUEST_DELAY = int(os.getenv('REQUEST_DELAY', 2))
MAX_RETRIES = int(os.getenv('MAX_RETRIES', 3))

# 导出格式
EXPORT_FORMAT = os.getenv('EXPORT_FORMAT', 'csv')  # csv, json, excel

# 日志配置
LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'crawler.log')

WECHAT_WEBHOOK_URL = os.getenv('WECHAT_WEBHOOK_URL', 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=b0dd1c4a-8067-4e65-ba06-9f7cc736a02f')

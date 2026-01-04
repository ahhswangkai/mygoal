import sys
import os
from datetime import datetime, timedelta
import time
from crawler import FootballCrawler
from db_storage import MongoDBStorage
from utils import setup_logger

def crawl_history():
    logger = setup_logger()
    logger.info("开始爬取历史数据...")
    
    # Initialize storage and crawler
    try:
        storage = MongoDBStorage()
        crawler = FootballCrawler(mongo_storage=storage)
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        return

    # Start date: 2025-07-01 (Assuming user means the most recent July 1st)
    # If today is 2026-01-04, then July 1st 2025 is the target.
    start_date = datetime(2025, 8, 1)
    end_date = datetime.now()
    
    current_date = start_date
    total_days = (end_date - start_date).days + 1
    
    logger.info(f"计划爬取从 {start_date.strftime('%Y-%m-%d')} 到 {end_date.strftime('%Y-%m-%d')} 的数据，共 {total_days} 天")
    
    count = 0
    while current_date.date() <= end_date.date():
        date_str = current_date.strftime('%Y-%m-%d')
        logger.info(f"[{count+1}/{total_days}] 正在爬取 {date_str} 的数据...")
        
        try:
            # crawl_daily_matches will automatically use JSON list + Hybrid Odds
            matches = crawler.crawl_daily_matches(date_str)
            logger.info(f"日期 {date_str} 爬取完成，共 {len(matches)} 场比赛")
        except Exception as e:
            logger.error(f"爬取 {date_str} 失败: {e}")
        
        # Move to next day
        current_date += timedelta(days=1)
        count += 1
        
        # Add a small delay
        time.sleep(1) 

    logger.info("历史数据爬取任务完成")

if __name__ == "__main__":
    crawl_history()

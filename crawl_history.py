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

    # Start date: 2025-08-01
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
            # 1. 先获取并保存基本比赛列表 (fetch_odds=False)
            logger.info(f"正在获取 {date_str} 的比赛列表...")
            matches = crawler.crawl_daily_matches(date_str, fetch_odds=False)
            logger.info(f"日期 {date_str} 列表获取完成，共 {len(matches)} 场比赛")
            
            # 2. 逐个更新赔率
            logger.info(f"正在更新 {len(matches)} 场比赛的赔率详情...")
            for i, match in enumerate(matches):
                match_id = match.get('match_id')
                if not match_id: continue
                
                # 进度日志
                if (i + 1) % 5 == 0:
                    logger.info(f"进度: {i + 1}/{len(matches)}")
                
                # 更新赔率
                try:
                    # 显式获取赔率详情
                    odds = crawler.crawl_match_odds(match_id)
                    if odds:
                        # 映射到match对象
                        crawler._map_odds_details(match, odds)
                        # 1. 保存详细赔率到odds表
                        storage.save_odds(match_id, odds)
                        # 2. 保存更新后的比赛信息到matches表
                        storage.save_match(match)
                        # 随机延时
                        time.sleep(0.5)
                except Exception as e:
                    logger.error(f"更新比赛 {match_id} 赔率失败: {e}")

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

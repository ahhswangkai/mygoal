"""
定时任务爬取脚本 - 爬取7月份以来的所有数据
"""
from datetime import datetime, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from crawler import FootballCrawler
from db_storage import MongoDBStorage
from utils import setup_logger
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# 全局锁
log_lock = threading.Lock()


def crawl_single_match_odds(match, logger, mongo_storage):
    """
    爬取单场比赛的赔率数据（线程安全）
    
    Args:
        match: 比赛数据字典
        logger: 日志器
        mongo_storage: MongoDB存储实例
        
    Returns:
        tuple: (match_id, odds_data)
    """
    match_id = match.get('match_id', '')
    if not match_id:
        return (None, None)
    
    crawler = FootballCrawler()
    
    try:
        odds = crawler.crawl_match_odds(match_id)
        
        if odds and (odds.get('euro_odds') or odds.get('asian_handicap') or odds.get('over_under')):
            mongo_storage.save_odds(match_id, odds)
            with log_lock:
                logger.info(f"  写入赔率成功: {match_id} - {match.get('home_team', '')} vs {match.get('away_team', '')}")
            return (match_id, odds)
        else:
            with log_lock:
                logger.warning(f"  赔率为空: {match_id}")
            return (match_id, None)
            
    except Exception as e:
        with log_lock:
            logger.error(f"  爬取赔率失败 {match_id}: {str(e)}")
        return (match_id, None)
    finally:
        crawler.close()


def crawl_date_range(start_date, end_date):
    """
    爬取指定日期范围的数据
    
    Args:
        start_date: 开始日期 (datetime对象)
        end_date: 结束日期 (datetime对象)
    """
    logger = setup_logger()
    logger.info("=" * 60)
    logger.info(f"开始定时爬取任务: {start_date.strftime('%Y-%m-%d')} 到 {end_date.strftime('%Y-%m-%d')}")
    logger.info("=" * 60)
    
    # 初始化
    try:
        mongo_storage = MongoDBStorage()
        logger.info("已连接MongoDB")
    except Exception as e:
        logger.error(f"MongoDB连接失败: {str(e)}")
        return
    
    crawler = FootballCrawler()
    
    # 遍历日期范围
    current_date = start_date
    total_matches = 0
    total_odds = 0
    
    while current_date <= end_date:
        date_str = current_date.strftime('%Y-%m-%d')
        logger.info(f"\n>>> 爬取日期: {date_str}")
        
        try:
            url = f"https://live.500.com/?e={date_str}"
            matches = crawler.crawl_daily_matches(url)
            
            if not matches:
                logger.warning(f"  {date_str} 无比赛数据")
                current_date += timedelta(days=1)
                continue
            
            logger.info(f"  获取到 {len(matches)} 场比赛")
            
            # 保存比赛数据
            count = mongo_storage.save_matches(matches)
            total_matches += count
            logger.info(f"  已写入 {count} 场比赛到MongoDB")
            
            # 并发爬取赔率
            logger.info(f"  开始爬取赔率（并发数: 8）")
            odds_count = 0
            
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {}
                for m in matches:
                    mid = m.get('match_id')
                    if not mid:
                        continue
                    future = executor.submit(crawl_single_match_odds, m, logger, mongo_storage)
                    futures[future] = mid
                
                completed = 0
                for future in as_completed(futures):
                    completed += 1
                    try:
                        match_id, odds = future.result()
                        if match_id and odds:
                            odds_count += 1
                        
                        if completed % 10 == 0:
                            logger.info(f"  进度: {completed}/{len(futures)}")
                    except Exception as e:
                        logger.error(f"  任务异常: {str(e)}")
            
            total_odds += odds_count
            logger.info(f"  完成赔率爬取: {odds_count}/{len(matches)}")
            
        except Exception as e:
            logger.error(f"  爬取 {date_str} 失败: {str(e)}")
        
        # 下一天
        current_date += timedelta(days=1)
    
    crawler.close()
    
    logger.info("\n" + "=" * 60)
    logger.info("定时任务完成！")
    logger.info(f"总比赛数: {total_matches}")
    logger.info(f"总赔率数: {total_odds}")
    logger.info("=" * 60)


def crawl_july_to_now():
    """
    爬取7月1日到当前日期的所有数据
    """
    logger = setup_logger()
    logger.info("触发定时任务: 爬取7月份以来的数据")
    
    # 计算日期范围
    current_year = datetime.now().year
    start_date = datetime(current_year, 10, 1)  # 7月1日
    end_date = datetime.now()  # 当前日期
    
    crawl_date_range(start_date, end_date)


def main():
    """
    主函数 - 设置定时任务
    """
    logger = setup_logger()
    logger.info("=" * 60)
    logger.info("定时爬取任务启动")
    logger.info("=" * 60)
    
    scheduler = BlockingScheduler()
    
    # 立即执行一次
    logger.info("立即执行首次爬取...")
    crawl_july_to_now()
    
    # 设置定时任务：每天凌晨2点执行
    scheduler.add_job(
        crawl_july_to_now,
        CronTrigger(hour=2, minute=0),
        id='daily_crawl',
        name='每日爬取7月以来数据',
        replace_existing=True
    )
    
    logger.info("定时任务已设置: 每天凌晨2点执行")
    logger.info("按 Ctrl+C 停止任务")
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("定时任务已停止")


if __name__ == '__main__':
    main()

"""
定时任务爬取脚本
逻辑：
1. 每天11:00之前：爬取昨天的数据（只更新结果，不爬赔率）
2. 每天11:00之后：爬取当天的数据（更新结果 + 爬取赔率）
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
    """
    match_id = match.get('match_id', '')
    if not match_id:
        return (None, None)
    
    crawler = FootballCrawler()
    
    try:
        odds = crawler.crawl_match_odds(match_id)
        
        if odds and (odds.get('euro_odds') or odds.get('asian_handicap') or odds.get('over_under')):
            mongo_storage.save_odds(match_id, odds)
            # 同时更新match表中的赔率摘要
            crawler._map_odds_details(match, odds)
            mongo_storage.save_match(match)
            
            with log_lock:
                logger.info(f"  写入赔率成功: {match_id}")
            return (match_id, odds)
        else:
            with log_lock:
                # logger.warning(f"  赔率为空: {match_id}")
                pass
            return (match_id, None)
            
    except Exception as e:
        with log_lock:
            logger.error(f"  爬取赔率失败 {match_id}: {str(e)}")
        return (match_id, None)
    finally:
        crawler.close()


def smart_crawl_task():
    """
    智能爬取任务
    """
    logger = setup_logger()
    now = datetime.now()
    logger.info("=" * 60)
    logger.info(f"触发智能爬取任务 (当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')})")
    
    try:
        mongo_storage = MongoDBStorage()
        crawler = FootballCrawler(mongo_storage)
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        return

    # 逻辑判断
    # 昨天11点后到当天11点前 -> 使用昨天的日期，fetch_odds=False
    # 其他时间 -> 使用当天的日期，fetch_odds=True
    
    if now.hour < 11:
        target_date = now - timedelta(days=1)
        fetch_odds = False
        mode_str = "结果更新模式 (Yesterday, No Odds)"
    else:
        target_date = now
        fetch_odds = True
        mode_str = "常规爬取模式 (Today, With Odds)"
        
    date_str = target_date.strftime('%Y-%m-%d')
    logger.info(f"模式: {mode_str}")
    logger.info(f"目标日期: {date_str}")
    
    try:
        # Step 1: 获取比赛列表
        # 注意：crawl_daily_matches 内部会调用 save_match 保存基础信息
        logger.info(f"正在获取比赛列表...")
        matches = crawler.crawl_daily_matches(date_str, fetch_odds=False)
        logger.info(f"获取到 {len(matches)} 场比赛")
        
        # Step 2: 如果需要爬取赔率 (Today模式)
        if fetch_odds and matches:
            logger.info(f"开始并发爬取详细赔率 (共 {len(matches)} 场)...")
            
            # 过滤掉已完场且已有赔率的比赛，避免重复爬取？
            # 暂时全量爬取以保证数据最新
            
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(crawl_single_match_odds, m, logger, mongo_storage): m for m in matches}
                
                completed = 0
                for future in as_completed(futures):
                    completed += 1
                    if completed % 5 == 0:
                        logger.info(f"  赔率进度: {completed}/{len(matches)}")
                        
            logger.info("赔率爬取完成")
        else:
            logger.info("跳过赔率爬取 (fetch_odds=False)")
            
    except Exception as e:
        logger.error(f"任务执行失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        crawler.close()
        logger.info("任务结束")
        logger.info("=" * 60)


def main():
    """
    主函数 - 设置定时任务
    """
    logger = setup_logger()
    logger.info("=" * 60)
    logger.info("定时爬取任务启动")
    logger.info("计划: 每小时执行一次 smart_crawl_task")
    logger.info("=" * 60)
    
    scheduler = BlockingScheduler()
    
    # 立即执行一次
    logger.info("立即执行首次爬取...")
    smart_crawl_task()
    
    # 设置定时任务：每小时执行一次
    scheduler.add_job(
        smart_crawl_task,
        CronTrigger(minute=0), # 每小时的第0分执行
        id='smart_crawl',
        name='智能爬取任务',
        replace_existing=True
    )
    
    logger.info("定时任务已设置: 每小时整点执行")
    logger.info("按 Ctrl+C 停止任务")
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("定时任务已停止")


if __name__ == '__main__':
    main()

"""
测试多线程爬取功能
"""
import time
from crawler import FootballCrawler
from storage import DataStorage
from utils import setup_logger
from main import crawl_daily_data

def test_multithread_crawl():
    logger = setup_logger()
    logger.info("=" * 70)
    logger.info("测试：多线程爬取赔率数据（最多8个线程）")
    logger.info("=" * 70)
    
    # 记录开始时间
    start_time = time.time()
    
    # 执行爬取（使用2025-11-30的数据，这天有48场比赛）
    logger.info("\n开始爬取2025-11-30的比赛数据...")
    logger.info("使用多线程模式，最多8个线程并发")
    
    try:
        crawl_daily_data(site='500wan', save_format='csv', date='2025-11-30')
    except Exception as e:
        logger.error(f"爬取失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
    
    # 计算耗时
    end_time = time.time()
    elapsed_time = end_time - start_time
    
    logger.info("\n" + "=" * 70)
    logger.info(f"✅ 爬取完成！")
    logger.info(f"总耗时: {elapsed_time:.2f} 秒 ({elapsed_time/60:.2f} 分钟)")
    logger.info("=" * 70)

if __name__ == '__main__':
    test_multithread_crawl()

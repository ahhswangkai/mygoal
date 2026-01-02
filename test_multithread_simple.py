"""
简单测试多线程功能
"""
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from crawler import FootballCrawler
from storage import DataStorage
from utils import setup_logger

log_lock = threading.Lock()

def crawl_single_match(match_id, index, total, logger):
    """爬取单场比赛"""
    crawler = FootballCrawler()
    try:
        with log_lock:
            logger.info(f"[{index}/{total}] [线程-{threading.current_thread().name}] 开始爬取比赛 {match_id}")
        
        odds = crawler.crawl_match_odds(match_id)
        
        with log_lock:
            if odds and odds.get('euro_odds'):
                euro = odds['euro_odds'][0]
                logger.info(f"  ✅ [线程-{threading.current_thread().name}] 成功: 欧赔 {euro.get('win')}/{euro.get('draw')}/{euro.get('lose')}")
            else:
                logger.warning(f"  ❌ [线程-{threading.current_thread().name}] 未获取到数据")
        
        return (match_id, odds)
    except Exception as e:
        with log_lock:
            logger.error(f"  [线程-{threading.current_thread().name}] 失败: {str(e)}")
        return (match_id, None)
    finally:
        crawler.close()

def test_simple_multithread():
    logger = setup_logger()
    logger.info("=" * 70)
    logger.info("简单测试：多线程爬取5场比赛")
    logger.info("=" * 70)
    
    # 测试5场比赛
    test_matches = [
        '1202862',  # 阿贾克斯 vs 格罗宁根（未开始）
        '1326220',  # 福冈黄蜂 vs 大阪钢巴（已结束）
        '1327464',  # 东京绿茵 vs 鹿岛鹿角（已结束）
        '1327249',  # 新泻天鹅 vs 柏太阳神（已结束）
        '1326318',  # 横滨水手 vs 大阪樱花（已结束）
    ]
    
    start_time = time.time()
    logger.info(f"\n开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"测试比赛数: {len(test_matches)}")
    logger.info(f"使用线程池: 最多8个线程\n")
    
    results = {}
    
    # 使用线程池
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_match = {}
        for i, match_id in enumerate(test_matches, 1):
            future = executor.submit(crawl_single_match, match_id, i, len(test_matches), logger)
            future_to_match[future] = match_id
        
        # 获取结果
        for future in as_completed(future_to_match):
            match_id, odds = future.result()
            if match_id and odds:
                results[match_id] = odds
    
    end_time = time.time()
    elapsed = end_time - start_time
    
    logger.info("\n" + "=" * 70)
    logger.info(f"✅ 测试完成！")
    logger.info(f"总耗时: {elapsed:.2f} 秒")
    logger.info(f"成功爬取: {len(results)}/{len(test_matches)}")
    logger.info(f"平均每场: {elapsed/len(test_matches):.2f} 秒")
    logger.info("=" * 70)

if __name__ == '__main__':
    test_simple_multithread()

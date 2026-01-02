"""
足彩爬虫主程序
"""
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from crawler import FootballCrawler

from db_storage import MongoDBStorage
from config import TARGET_URLS
from utils import setup_logger

# 全局锁，用于线程安全的日志输出
log_lock = threading.Lock()


def crawl_single_match_odds(match, index, total, logger):
    """
    爬取单场比赛的赔率数据（线程安全）
    每个线程创建独立的crawler实例
    
    Args:
        match: 比赛数据字典
        index: 当前索引
        total: 总比赛数
        logger: 日志器
        
    Returns:
        tuple: (match_id, odds_data)
    """
    match_id = match.get('match_id', '')
    if not match_id:
        return (None, None)
    
    # 每个线程创建独立的crawler实例，确保线程安全
    crawler = FootballCrawler()
    
    try:
        status = match.get('status', '').strip()
        status_label = "未开始" if not status or '未' in status else ("已结束" if '完' in status or '结束' in status else "进行中")
        
        with log_lock:
            logger.info(f"[{index}/{total}] 爬取比赛 {match_id} ({match.get('home_team', '')} vs {match.get('away_team', '')}) [{status_label}] 的赔率")
        
        # 爬取赔率数据
        odds = crawler.crawl_match_odds(match_id)
        
        if odds and (odds.get('euro_odds') or odds.get('asian_handicap') or odds.get('over_under')):
            # 显示赔率信息
            with log_lock:
                if odds.get('euro_odds'):
                    euro = odds['euro_odds'][0]
                    logger.info(f"  [线程-{threading.current_thread().name}] 欧赔: 胜{euro['win']} 平{euro['draw']} 负{euro['lose']}")
                if odds.get('asian_handicap'):
                    asian = odds['asian_handicap'][0]
                    logger.info(f"  [线程-{threading.current_thread().name}] 亚盘: {asian['home_odds']} {asian['handicap']} {asian['away_odds']}")
                if odds.get('over_under'):
                    over_under = odds['over_under'][0]
                    logger.info(f"  [线程-{threading.current_thread().name}] 大小球: 大{over_under['over_odds']} {over_under['total']} 小{over_under['under_odds']}")
            
            return (match_id, odds)
        else:
            with log_lock:
                logger.warning(f"  [线程-{threading.current_thread().name}] 未获取到赔率数据")
            return (match_id, None)
            
    except Exception as e:
        with log_lock:
            logger.error(f"  [线程-{threading.current_thread().name}] 爬取比赛 {match_id} 失败: {str(e)}")
        return (match_id, None)
    finally:
        # 关闭该线程的crawler实例
        crawler.close()


def crawl_daily_data(site='500wan', save_format='csv', date=None):
    """
    爬取每日比赛和赔率数据
    
    Args:
        site: 目标网站（500wan/zgzcw/okooo）
        save_format: 保存格式（csv/json/excel）
        date: 指定日期（格式：YYYY-MM-DD，如：2025-11-30），不指定则爬取当天
    """
    logger = setup_logger()
    logger.info("=" * 50)
    logger.info("开始爬取足彩数据")
    logger.info(f"目标网站: {site}")
    logger.info(f"保存格式: {save_format}")
    if date:
        logger.info(f"指定日期: {date}")
    logger.info("=" * 50)
    
    # 初始化爬虫和存储
    crawler = FootballCrawler()
    # 仅使用MongoDB存储
    try:
        mongo_storage = MongoDBStorage()
        use_mongo = True
        logger.info("已启用MongoDB存储")
    except Exception as e:
        mongo_storage = None
        use_mongo = False
        logger.warning(f"MongoDB不可用: {str(e)}")
    
    try:
        # 获取目标URL
        if site not in TARGET_URLS:
            logger.error(f"不支持的网站: {site}")
            return
            
        base_url = TARGET_URLS[site]
        
        # 如果指定了日期，构建带日期参数的URL
        if date:
            base_url = f"{base_url}?e={date}"
        
        # 1. 爬取比赛列表
        logger.info("\n>>> 步骤1: 爬取每日比赛列表")
        matches = crawler.crawl_daily_matches(base_url)
        
        if not matches:
            logger.warning("未获取到比赛数据")
            return
        
        logger.info(f"成功获取 {len(matches)} 场比赛信息")
        
        # 分类比赛：未开始、已结束、进行中
        logger.info("\n>>> 分类比赛状态")
        pending_matches = []  # 未开始
        finished_matches = []  # 已结束
        live_matches = []  # 进行中
        
        for match in matches:
            status = match.get('status', 0)  # status现在是int类型：0=未开始，1=进行中，2=完场
            status_text = match.get('status_text', '')
            score = match.get('score', '').strip() if isinstance(match.get('score'), str) else match.get('score', '')
            
            # 判断比赛状态
            if status == 0:
                # 未开始的比赛
                pending_matches.append(match)
            elif status == 2:
                # 已结束的比赛（有完整比分）
                finished_matches.append(match)
            else:
                # 进行中的比赛
                live_matches.append(match)
        
        logger.info(f"未开始: {len(pending_matches)} 场")
        logger.info(f"已结束: {len(finished_matches)} 场")
        logger.info(f"进行中: {len(live_matches)} 场")
        
        # 保存所有比赛数据
        if use_mongo:
            mongo_storage.save_matches(matches)
            logger.info("已写入MongoDB: 比赛数据")
        
        # 仅写入MongoDB
        match_file = None
        
        # 2. 爬取未开始和进行中比赛的赔率数据（已完赛只需比分）
        logger.info("\n>>> 步骤2: 爬取赔率数据")
        odds_dict = {}
        
        # 只爬取未开始和进行中的比赛赔率
        matches_need_odds = pending_matches + live_matches
        
        if not matches_need_odds:
            logger.info("没有需要爬取赔率的比赛（所有比赛均已完场）")
        else:
            total = len(matches_need_odds)
            logger.info(f"开始爬取 {total} 场比赛的赔率（欧赔、亚盘、大小球、让球指数）")
            logger.info(f"使用多线程爬取，最多 8 个线程并发")
            logger.info(f"ℹ️  已完赛比赛 ({len(finished_matches)} 场) 跳过赔率爬取，仅保存比分")
            
            # 使用线程池进行并发爬取
            with ThreadPoolExecutor(max_workers=8) as executor:
                # 提交所有任务
                future_to_match = {}
                for i, match in enumerate(matches_need_odds, 1):
                    if not match.get('match_id'):
                        continue
                    # 每个线程会创建自己的crawler实例
                    future = executor.submit(crawl_single_match_odds, match, i, total, logger)
                    future_to_match[future] = match
                
                # 获取结果
                completed = 0
                for future in as_completed(future_to_match):
                    completed += 1
                    try:
                        match_id, odds = future.result()
                        if match_id and odds:
                            odds_dict[match_id] = odds
                        
                        # 显示进度
                        if completed % 5 == 0 or completed == total:
                            with log_lock:
                                logger.info(f"\n进度: {completed}/{total} ({completed*100//total}%)")
                                
                    except Exception as e:
                        with log_lock:
                            logger.error(f"处理任务结果失败: {str(e)}")
            
            logger.info(f"\n成功爬取 {len(odds_dict)} 场比赛的赔率")
        
        # 3. 保存组合数据（所有比赛+赔率）
        logger.info("\n>>> 步骤3: 保存组合数据")
        if use_mongo:
            for mid, odds in odds_dict.items():
                mongo_storage.save_odds(mid, odds)
            logger.info("已写入MongoDB: 赔率数据")
        combined_file = None
        
        # 总结
        logger.info("\n" + "=" * 50)
        logger.info("爬取完成！")
        logger.info("所有比赛数据: 已写入MongoDB")
        logger.info("所有比赛+赔率: 已写入MongoDB")
        logger.info(f"总比赛数: {len(matches)} 场")
        logger.info(f"  - 未开始: {len(pending_matches)} 场")
        logger.info(f"  - 已结束: {len(finished_matches)} 场（含最终比分）")
        logger.info(f"  - 进行中: {len(live_matches)} 场")
        logger.info(f"成功爬取赔率: {len(odds_dict)} 场")
        logger.info("=" * 50)
        
    except Exception as e:
        logger.error(f"爬取过程出错: {str(e)}", exc_info=True)
        
    finally:
        crawler.close()


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='足彩网站爬虫工具')
    parser.add_argument(
        '--site',
        type=str,
        default='500wan',
        choices=['500wan', 'zgzcw', 'okooo'],
        help='目标网站 (默认: 500wan)'
    )
    parser.add_argument(
        '--format',
        type=str,
        default='csv',
        choices=['csv', 'json', 'excel'],
        help='保存格式 (默认: csv)'
    )
    parser.add_argument(
        '--date',
        type=str,
        default=None,
        help='指定日期 (格式: YYYY-MM-DD，如: 2025-11-30)'
    )
    
    args = parser.parse_args()
    
    # 执行爬取
    crawl_daily_data(site=args.site, save_format=args.format, date=args.date)


if __name__ == '__main__':
    main()

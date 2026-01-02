# -*- coding: utf-8 -*-
"""测试大小球爬取功能"""
import sys
sys.path.append('.')

from crawler import FootballCrawler
from utils import setup_logger

logger = setup_logger()

# 测试比赛ID
match_id = "1216031"

logger.info("=" * 60)
logger.info("测试大小球赔率爬取功能")
logger.info("比赛ID: {}".format(match_id))
logger.info("=" * 60)

crawler = FootballCrawler()

# 爬取赔率数据
# 使用match_id直接调用
# odds_url = "https://odds.500.com/fenxi/shuju-{}.shtml".format(match_id)
logger.info("\n>>> 爬取赔率数据（包含大小球）")
odds = crawler.crawl_match_odds(match_id)

logger.info("\n>>> 赔率数据：")
if odds:
    if odds.get('euro_odds'):
        euro = odds['euro_odds'][0]
        logger.info("欧赔: 胜{} 平{} 负{}".format(euro['win'], euro['draw'], euro['lose']))
    
    if odds.get('asian_handicap'):
        asian = odds['asian_handicap'][0]
        logger.info("亚盘: {} {} {}".format(asian['home_odds'], asian['handicap'], asian['away_odds']))
    
    if odds.get('over_under'):
        ou = odds['over_under'][0]
        logger.info("大小球: 大{} {} 小{}".format(ou['over_odds'], ou['total'], ou['under_odds']))
    else:
        logger.warning("未获取到大小球数据")
else:
    logger.error("未获取到任何赔率数据")

crawler.close()
logger.info("\n" + "=" * 60)
logger.info("测试完成")
logger.info("=" * 60)

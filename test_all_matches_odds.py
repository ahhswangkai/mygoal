"""
测试所有比赛都爬取赔率数据
"""
from crawler import FootballCrawler
from storage import DataStorage
from utils import setup_logger

def test_all_matches_odds():
    logger = setup_logger()
    logger.info("=" * 60)
    logger.info("测试：所有比赛（不管状态）都爬取赔率数据")
    logger.info("=" * 60)
    
    # 模拟比赛数据
    matches = [
        {
            'match_id': '1202862',
            'round_id': '周日043',
            'league': '荷甲',
            'round': '第14轮',
            'match_time': '12-01 03:00',
            'status': '未',
            'home_team': '阿贾克斯',
            'score': '-',
            'away_team': '格罗宁根',
            'home_score': '',
            'away_score': '',
            'handicap': '平手/半球'
        },
        {
            'match_id': '1326220',
            'round_id': '周日001',
            'league': '日职',
            'round': '第37轮',
            'match_time': '11-30 13:00',
            'status': '完',
            'home_team': '福冈黄蜂',
            'score': '1-0',
            'away_team': '大阪钢巴',
            'home_score': '1',
            'away_score': '0',
            'handicap': '平手'
        }
    ]
    
    # 初始化爬虫
    crawler = FootballCrawler()
    storage = DataStorage()
    
    try:
        # 爬取所有比赛的赔率
        logger.info("\n开始爬取所有比赛的赔率数据...")
        odds_dict = {}
        
        for i, match in enumerate(matches, 1):
            match_id = match.get('match_id', '')
            status = match.get('status', '').strip()
            status_label = "未开始" if not status or '未' in status else ("已结束" if '完' in status else "进行中")
            
            logger.info(f"\n[{i}/{len(matches)}] 爬取比赛 {match_id} ({match.get('home_team', '')} vs {match.get('away_team', '')}) [{status_label}]")
            
            odds = crawler.crawl_match_odds(match_id)
            
            if odds and (odds.get('euro_odds') or odds.get('asian_handicap') or odds.get('over_under')):
                odds_dict[match_id] = odds
                
                # 显示赔率信息
                if odds.get('euro_odds'):
                    euro = odds['euro_odds'][0]
                    logger.info(f"  ✅ 欧赔: 胜{euro.get('win', '')} 平{euro.get('draw', '')} 负{euro.get('lose', '')}")
                    logger.info(f"     即时盘: {euro.get('current_win', '')}/{euro.get('current_draw', '')}/{euro.get('current_lose', '')}")
                    logger.info(f"     初盘: {euro.get('initial_win', '')}/{euro.get('initial_draw', '')}/{euro.get('initial_lose', '')}")
                
                if odds.get('asian_handicap'):
                    asian = odds['asian_handicap'][0]
                    logger.info(f"  ✅ 亚盘: {asian.get('home_odds', '')} {asian.get('handicap', '')} {asian.get('away_odds', '')}")
                    logger.info(f"     即时盘: {asian.get('current_home_odds', '')}/{asian.get('current_handicap', '')}/{asian.get('current_away_odds', '')}")
                    logger.info(f"     初盘: {asian.get('initial_home_odds', '')}/{asian.get('initial_handicap', '')}/{asian.get('initial_away_odds', '')}")
                
                if odds.get('over_under'):
                    over_under = odds['over_under'][0]
                    logger.info(f"  ✅ 大小球: 大{over_under.get('over_odds', '')} {over_under.get('total', '')} 小{over_under.get('under_odds', '')}")
                    logger.info(f"     即时盘: {over_under.get('current_over_odds', '')}/{over_under.get('current_total', '')}/{over_under.get('current_under_odds', '')}")
                    logger.info(f"     初盘: {over_under.get('initial_over_odds', '')}/{over_under.get('initial_total', '')}/{over_under.get('initial_under_odds', '')}")
            else:
                logger.warning(f"  ❌ 未获取到赔率数据")
        
        # 保存组合数据
        logger.info("\n" + "=" * 60)
        logger.info("保存组合数据...")
        combined_file = storage.save_combined_data(matches, odds_dict, 'csv')
        
        # 总结
        logger.info("\n" + "=" * 60)
        logger.info("测试完成！")
        logger.info(f"总比赛数: {len(matches)}")
        logger.info(f"成功爬取赔率: {len(odds_dict)}/{len(matches)}")
        logger.info(f"组合数据文件: {combined_file}")
        logger.info("=" * 60)
        
        # 验证数据
        import pandas as pd
        df = pd.read_csv(combined_file, encoding='utf-8-sig')
        logger.info("\n数据验证:")
        logger.info(f"  CSV行数: {len(df)}")
        logger.info(f"  列数: {len(df.columns)}")
        
        # 检查赔率列
        odds_cols = [col for col in df.columns if 'euro' in col or 'asian' in col or 'over' in col or 'under' in col or 'total' in col]
        logger.info(f"  赔率列数: {len(odds_cols)}")
        logger.info(f"  赔率列: {odds_cols[:10]}...")
        
        # 显示数据样本
        logger.info(f"\n数据样本:")
        for idx, row in df.iterrows():
            logger.info(f"\n  比赛{idx+1}: {row['home_team']} vs {row['away_team']} ({row['status']})")
            logger.info(f"    比分: {row.get('score', 'N/A')}")
            logger.info(f"    欧赔: {row.get('euro_win', 'N/A')}/{row.get('euro_draw', 'N/A')}/{row.get('euro_lose', 'N/A')}")
            logger.info(f"    亚盘: {row.get('asian_home_odds', 'N/A')} {row.get('asian_handicap', 'N/A')} {row.get('asian_away_odds', 'N/A')}")
            logger.info(f"    大小球: {row.get('over_odds', 'N/A')} {row.get('total_goals', 'N/A')} {row.get('under_odds', 'N/A')}")
        
    finally:
        crawler.close()

if __name__ == '__main__':
    test_all_matches_odds()

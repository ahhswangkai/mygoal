"""
测试初盘数据保存功能
"""
from crawler import FootballCrawler
from storage import DataStorage
from utils import setup_logger

def test_initial_odds_save():
    logger = setup_logger()
    logger.info("=" * 70)
    logger.info("测试：初盘数据保存功能")
    logger.info("=" * 70)
    
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
        }
    ]
    
    # 初始化爬虫和存储
    crawler = FootballCrawler()
    storage = DataStorage()
    
    try:
        # 爬取赔率数据
        logger.info("\n爬取比赛赔率数据...")
        match_id = matches[0]['match_id']
        odds = crawler.crawl_match_odds(match_id)
        
        if odds:
            logger.info("\n✅ 成功获取赔率数据")
            
            # 显示欧赔数据
            if odds.get('euro_odds'):
                euro = odds['euro_odds'][0]
                logger.info("\n【欧赔】")
                logger.info(f"  即时盘: 主胜={euro.get('current_win')} 平局={euro.get('current_draw')} 客胜={euro.get('current_lose')}")
                logger.info(f"  初盘:   主胜={euro.get('initial_win')} 平局={euro.get('initial_draw')} 客胜={euro.get('initial_lose')}")
            
            # 显示亚盘数据
            if odds.get('asian_handicap'):
                asian = odds['asian_handicap'][0]
                logger.info("\n【亚盘】")
                logger.info(f"  即时盘: {asian.get('current_home_odds')} {asian.get('current_handicap')} {asian.get('current_away_odds')}")
                logger.info(f"  初盘:   {asian.get('initial_home_odds')} {asian.get('initial_handicap')} {asian.get('initial_away_odds')}")
            
            # 显示大小球数据
            if odds.get('over_under'):
                ou = odds['over_under'][0]
                logger.info("\n【大小球】")
                logger.info(f"  即时盘: 大={ou.get('current_over_odds')} 盘口={ou.get('current_total')} 小={ou.get('current_under_odds')}")
                logger.info(f"  初盘:   大={ou.get('initial_over_odds')} 盘口={ou.get('initial_total')} 小={ou.get('initial_under_odds')}")
            
            # 保存组合数据
            odds_dict = {match_id: odds}
            logger.info("\n保存组合数据到CSV...")
            combined_file = storage.save_combined_data(matches, odds_dict, 'csv')
            
            # 读取并验证保存的数据
            import pandas as pd
            df = pd.read_csv(combined_file, encoding='utf-8-sig')
            
            logger.info("\n" + "=" * 70)
            logger.info("CSV文件内容验证")
            logger.info("=" * 70)
            
            logger.info(f"\n总列数: {len(df.columns)}")
            
            # 检查欧赔列
            euro_cols = [col for col in df.columns if 'euro' in col]
            logger.info(f"\n欧赔相关列 ({len(euro_cols)} 列):")
            for col in euro_cols:
                logger.info(f"  - {col}: {df[col].iloc[0]}")
            
            # 检查亚盘列
            asian_cols = [col for col in df.columns if 'asian' in col]
            logger.info(f"\n亚盘相关列 ({len(asian_cols)} 列):")
            for col in asian_cols:
                logger.info(f"  - {col}: {df[col].iloc[0]}")
            
            # 检查大小球列
            ou_cols = [col for col in df.columns if 'ou_' in col or col in ['over_odds', 'total_goals', 'under_odds']]
            logger.info(f"\n大小球相关列 ({len(ou_cols)} 列):")
            for col in ou_cols:
                logger.info(f"  - {col}: {df[col].iloc[0]}")
            
            # 验证初盘数据
            logger.info("\n" + "=" * 70)
            logger.info("初盘数据验证")
            logger.info("=" * 70)
            
            has_euro_initial = pd.notna(df['euro_initial_win'].iloc[0]) and df['euro_initial_win'].iloc[0] != ''
            has_asian_initial = pd.notna(df['asian_initial_home_odds'].iloc[0]) and df['asian_initial_home_odds'].iloc[0] != ''
            has_ou_initial = pd.notna(df['ou_initial_over_odds'].iloc[0]) and df['ou_initial_over_odds'].iloc[0] != ''
            
            if has_euro_initial:
                logger.info(f"✅ 欧赔初盘数据已保存: {df['euro_initial_win'].iloc[0]}/{df['euro_initial_draw'].iloc[0]}/{df['euro_initial_lose'].iloc[0]}")
            else:
                logger.warning("❌ 欧赔初盘数据缺失")
            
            if has_asian_initial:
                logger.info(f"✅ 亚盘初盘数据已保存: {df['asian_initial_home_odds'].iloc[0]} {df['asian_initial_handicap'].iloc[0]} {df['asian_initial_away_odds'].iloc[0]}")
            else:
                logger.warning("❌ 亚盘初盘数据缺失")
            
            if has_ou_initial:
                logger.info(f"✅ 大小球初盘数据已保存: {df['ou_initial_over_odds'].iloc[0]} {df['ou_initial_total'].iloc[0]} {df['ou_initial_under_odds'].iloc[0]}")
            else:
                logger.warning("❌ 大小球初盘数据缺失")
            
            logger.info("\n" + "=" * 70)
            if has_euro_initial and has_asian_initial and has_ou_initial:
                logger.info("🎉 所有初盘数据保存成功！")
            else:
                logger.warning("⚠️ 部分初盘数据缺失")
            logger.info("=" * 70)
            
            logger.info(f"\n保存的文件: {combined_file}")
            
        else:
            logger.error("❌ 未获取到赔率数据")
    
    finally:
        crawler.close()

if __name__ == '__main__':
    test_initial_odds_save()

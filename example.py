"""
使用示例
"""
from crawler import FootballCrawler
from storage import DataStorage


def example_crawl_500wan():
    """示例：爬取500彩票网数据"""
    print("=" * 60)
    print("示例：爬取500彩票网足球比赛和赔率数据")
    print("=" * 60)
    
    crawler = FootballCrawler()
    storage = DataStorage()
    
    try:
        # 1. 爬取比赛列表
        print("\n>>> 步骤1: 爬取比赛列表")
        url = "https://live.500.com/"
        matches = crawler.crawl_daily_matches(url)
        
        if matches:
            print(f"成功获取 {len(matches)} 场比赛")
            # 打印前3场比赛信息
            for i, match in enumerate(matches[:3], 1):
                print(f"\n比赛{i}:")
                for key, value in match.items():
                    print(f"  {key}: {value}")
            
            # 保存比赛数据
            print("\n>>> 步骤2: 保存比赛数据")
            filepath = storage.save_matches(matches, format_type='csv')
            print(f"数据已保存: {filepath}")
        else:
            print("未获取到比赛数据")
            
    except Exception as e:
        print(f"错误: {str(e)}")
    finally:
        crawler.close()


def example_crawl_with_odds():
    """示例：爬取比赛和赔率数据"""
    print("=" * 60)
    print("示例：爬取比赛和赔率组合数据")
    print("=" * 60)
    
    crawler = FootballCrawler()
    storage = DataStorage()
    
    try:
        # 1. 爬取比赛列表
        print("\n>>> 步骤1: 爬取比赛列表")
        matches_url = "https://live.500.com/"
        matches = crawler.crawl_daily_matches(matches_url)
        
        if not matches:
            print("未获取到比赛数据")
            return
        
        print(f"获取到 {len(matches)} 场比赛")
        
        # 2. 爬取赔率数据
        print("\n>>> 步骤2: 爬取赔率数据")
        odds_dict = {}
        
        # 只爬取前3场比赛的赔率（示例）
        for i, match in enumerate(matches[:3], 1):
            match_id = match.get('match_id', '')
            if not match_id:
                continue
            
            print(f"\n[{i}/3] 爬取比赛 {match_id} 的赔率")
            odds = crawler.crawl_match_odds(match_id)
            
            if odds:
                odds_dict[match_id] = odds
                print(f"  获取到 {len(odds)} 条赔率数据")
                # 打印前2条赔率
                for j, odd in enumerate(odds[:2], 1):
                    print(f"    赔率{j}: {odd}")
        
        # 3. 保存组合数据
        print("\n>>> 步骤3: 保存组合数据")
        filepath = storage.save_combined_data(matches, odds_dict, format_type='json')
        print(f"组合数据已保存: {filepath}")
        
    except Exception as e:
        print(f"错误: {str(e)}")
    finally:
        crawler.close()


def example_export_formats():
    """示例：不同格式导出"""
    print("=" * 60)
    print("示例：数据导出为不同格式")
    print("=" * 60)
    
    # 模拟数据
    matches = [
        {
            'match_id': '001',
            'league': '英超',
            'match_time': '2025-11-30 20:00',
            'home_team': '曼联',
            'away_team': '利物浦',
            'status': '未开始'
        },
        {
            'match_id': '002',
            'league': '西甲',
            'match_time': '2025-11-30 21:00',
            'home_team': '皇马',
            'away_team': '巴萨',
            'status': '未开始'
        }
    ]
    
    odds_dict = {
        '001': [
            {'company': '威廉希尔', 'win_odds': '2.10', 'draw_odds': '3.40', 'lose_odds': '3.20'},
            {'company': 'Bet365', 'win_odds': '2.05', 'draw_odds': '3.50', 'lose_odds': '3.30'}
        ],
        '002': [
            {'company': '威廉希尔', 'win_odds': '1.85', 'draw_odds': '3.60', 'lose_odds': '4.20'},
            {'company': 'Bet365', 'win_odds': '1.90', 'draw_odds': '3.50', 'lose_odds': '4.00'}
        ]
    }
    
    storage = DataStorage()
    
    # 导出为CSV
    print("\n>>> 导出为CSV格式")
    csv_file = storage.save_combined_data(matches, odds_dict, format_type='csv')
    print(f"CSV文件: {csv_file}")
    
    # 导出为JSON
    print("\n>>> 导出为JSON格式")
    json_file = storage.save_combined_data(matches, odds_dict, format_type='json')
    print(f"JSON文件: {json_file}")
    
    # 导出为Excel
    print("\n>>> 导出为Excel格式")
    excel_file = storage.save_combined_data(matches, odds_dict, format_type='excel')
    print(f"Excel文件: {excel_file}")


if __name__ == '__main__':
    print("\n请选择示例:")
    print("1. 爬取500彩票网比赛数据")
    print("2. 爬取比赛和赔率组合数据")
    print("3. 数据导出格式示例（使用模拟数据）")
    
    choice = input("\n请输入选项 (1/2/3): ").strip()
    
    if choice == '1':
        example_crawl_500wan()
    elif choice == '2':
        example_crawl_with_odds()
    elif choice == '3':
        example_export_formats()
    else:
        print("无效选项")

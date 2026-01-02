#!/usr/bin/env python3
"""
MongoDB使用示例
演示如何使用MongoDB存储和查询足球数据
"""
from db_storage import MongoDBStorage, FootballDataAnalyzer
from crawler import FootballCrawler
import json


def example_basic_operations():
    """基础操作示例"""
    print("\n" + "="*60)
    print("示例1: 基础数据操作")
    print("="*60)
    
    # 初始化存储
    storage = MongoDBStorage()
    
    # 保存单条数据
    match_data = {
        'match_id': 'demo_001',
        'league': '英超',
        'match_time': '2025-12-01 20:00',
        'home_team': '曼联',
        'away_team': '利物浦',
        'status': '未开始',
        'euro_current_win': '2.50',
        'euro_current_draw': '3.20',
        'euro_current_lose': '2.80'
    }
    
    print("\n保存比赛数据...")
    storage.save_match(match_data)
    
    # 查询数据
    print("\n查询比赛...")
    match = storage.get_match_by_id('demo_001')
    print(f"查询结果: {match['home_team']} vs {match['away_team']}")
    
    storage.close()


def example_crawl_and_save():
    """爬取并保存数据示例"""
    print("\n" + "="*60)
    print("示例2: 爬取数据并保存到MongoDB")
    print("="*60)
    
    # 初始化
    crawler = FootballCrawler()
    storage = MongoDBStorage()
    
    # 爬取比赛列表
    print("\n正在爬取比赛数据...")
    url = "https://live.500.com/wanchang.php"
    matches = crawler.crawl_daily_matches(url)
    
    if matches:
        print(f"爬取到 {len(matches)} 场比赛")
        
        # 保存到MongoDB
        print("\n保存到MongoDB...")
        count = storage.save_matches(matches)
        print(f"成功保存 {count} 场比赛")
        
        # 爬取前3场比赛的赔率
        print("\n爬取赔率数据（前3场）...")
        for i, match in enumerate(matches[:3], 1):
            match_id = match.get('match_id')
            if match_id:
                print(f"  [{i}] 正在爬取比赛 {match_id} 的赔率...")
                odds = crawler.crawl_match_odds(match_id)
                if odds:
                    storage.save_odds(match_id, odds)
                    print(f"  ✅ 赔率已保存")
    
    storage.close()
    crawler.close()


def example_query_data():
    """数据查询示例"""
    print("\n" + "="*60)
    print("示例3: 数据查询")
    print("="*60)
    
    storage = MongoDBStorage()
    
    # 查询所有英超比赛
    print("\n查询英超比赛...")
    epl_matches = storage.get_matches_by_league('英超')
    print(f"找到 {len(epl_matches)} 场英超比赛")
    
    # 查询已完场比赛
    print("\n查询已完场比赛...")
    finished = storage.get_matches_by_status('完场')
    print(f"找到 {len(finished)} 场已完场比赛")
    
    # 高级查询：英超已完场比赛
    print("\n高级查询: 英超已完场比赛...")
    matches = storage.get_matches(
        filters={'league': '英超', 'status': '完场'},
        limit=5,
        sort_by='match_time',
        sort_order=-1
    )
    print(f"找到 {len(matches)} 场比赛")
    for match in matches:
        print(f"  - {match['home_team']} {match.get('home_score', '?')}:{match.get('away_score', '?')} {match['away_team']}")
    
    storage.close()


def example_statistics():
    """统计分析示例"""
    print("\n" + "="*60)
    print("示例4: 数据统计")
    print("="*60)
    
    storage = MongoDBStorage()
    
    # 获取统计信息
    stats = storage.get_stats()
    
    print(f"\n📊 总比赛数: {stats['total_matches']}")
    print(f"🏆 联赛数: {stats['total_leagues']}")
    
    print("\n📈 按状态统计:")
    for status, count in sorted(stats['status_stats'].items(), key=lambda x: x[1], reverse=True):
        print(f"  {status}: {count}")
    
    print("\n🏅 按联赛统计 (前10):")
    sorted_leagues = sorted(stats['league_stats'].items(), key=lambda x: x[1], reverse=True)[:10]
    for league, count in sorted_leagues:
        print(f"  {league}: {count}")
    
    storage.close()


def example_data_analysis():
    """数据分析示例"""
    print("\n" + "="*60)
    print("示例5: 数据分析")
    print("="*60)
    
    storage = MongoDBStorage()
    analyzer = FootballDataAnalyzer(storage)
    
    # 查找高赔率比赛
    print("\n查找高赔率比赛（主胜赔率>3.0）...")
    high_odds = analyzer.find_high_odds_matches(min_win_odds=3.0)
    
    if high_odds:
        print(f"找到 {len(high_odds)} 场高赔率比赛:")
        for match in high_odds[:5]:
            print(f"  - {match['home_team']} vs {match['away_team']}: {match.get('euro_current_win', '?')}")
    else:
        print("  未找到高赔率比赛")
    
    # 联赛分析
    print("\n英超联赛分析...")
    epl_analysis = analyzer.analyze_league_performance('英超')
    if epl_analysis:
        print(f"  联赛: {epl_analysis['league']}")
        print(f"  总比赛数: {epl_analysis['total_matches']}")
        print(f"  已完场: {epl_analysis['finished_matches']}")
        print(f"  未完场: {epl_analysis['pending_matches']}")
    
    storage.close()


def example_aggregation():
    """聚合查询示例"""
    print("\n" + "="*60)
    print("示例6: MongoDB聚合查询")
    print("="*60)
    
    storage = MongoDBStorage()
    
    # 计算各联赛平均主队得分
    print("\n计算各联赛平均主队得分...")
    pipeline = [
        {'$match': {'status': '完场', 'home_score': {'$exists': True, '$ne': ''}}},
        {'$addFields': {
            'home_score_num': {'$toDouble': '$home_score'}
        }},
        {'$group': {
            '_id': '$league',
            'avg_home_score': {'$avg': '$home_score_num'},
            'total_matches': {'$sum': 1}
        }},
        {'$sort': {'avg_home_score': -1}},
        {'$limit': 10}
    ]
    
    try:
        results = list(storage.matches_collection.aggregate(pipeline))
        for result in results:
            print(f"  {result['_id']}: 平均 {result['avg_home_score']:.2f} 球 ({result['total_matches']} 场)")
    except Exception as e:
        print(f"  聚合查询失败: {str(e)}")
    
    storage.close()


def example_export_data():
    """数据导出示例"""
    print("\n" + "="*60)
    print("示例7: 数据导出")
    print("="*60)
    
    storage = MongoDBStorage()
    
    # 导出英超比赛
    print("\n导出英超比赛数据到JSON...")
    epl_matches = storage.get_matches_by_league('英超')
    
    if epl_matches:
        with open('data/epl_export.json', 'w', encoding='utf-8') as f:
            json.dump(epl_matches, f, ensure_ascii=False, indent=2, default=str)
        print(f"✅ 成功导出 {len(epl_matches)} 场比赛到 data/epl_export.json")
    else:
        print("  没有英超比赛数据")
    
    storage.close()


def main():
    """主函数"""
    print("\n" + "="*60)
    print("MongoDB 使用示例脚本")
    print("="*60)
    
    print("\n请选择要运行的示例:")
    print("1. 基础数据操作")
    print("2. 爬取并保存数据")
    print("3. 数据查询")
    print("4. 数据统计")
    print("5. 数据分析")
    print("6. 聚合查询")
    print("7. 数据导出")
    print("8. 运行所有示例")
    print("0. 退出")
    
    choice = input("\n请输入选项 (0-8): ").strip()
    
    try:
        if choice == '1':
            example_basic_operations()
        elif choice == '2':
            example_crawl_and_save()
        elif choice == '3':
            example_query_data()
        elif choice == '4':
            example_statistics()
        elif choice == '5':
            example_data_analysis()
        elif choice == '6':
            example_aggregation()
        elif choice == '7':
            example_export_data()
        elif choice == '8':
            example_basic_operations()
            example_query_data()
            example_statistics()
            example_data_analysis()
            example_aggregation()
            example_export_data()
            # 注意: 不包括示例2（爬取），避免频繁请求
        elif choice == '0':
            print("\n再见！")
            return
        else:
            print("\n无效选项！")
            return
        
        print("\n" + "="*60)
        print("示例运行完成！")
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"\n❌ 运行失败: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()

"""
大小球赢盘概率分析工具
"""
from db_storage import MongoDBStorage
from utils import setup_logger
import sys


def analyze_over_under_probability(league=None, min_goals=None, max_goals=None):
    """
    分析大小球赢盘概率
    
    Args:
        league: 联赛名称，如"西甲"
        min_goals: 最小总进球数
        max_goals: 最大总进球数
    """
    logger = setup_logger()
    
    try:
        storage = MongoDBStorage()
        logger.info("成功连接MongoDB")
    except Exception as e:
        logger.error(f"MongoDB连接失败: {str(e)}")
        return
    
    # 构建查询条件
    filters = {'status': 2}  # 只查完场比赛
    if league:
        filters['league'] = league
    
    matches = storage.get_matches(filters=filters)
    logger.info(f"共找到 {len(matches)} 场完场比赛")
    
    if not matches:
        logger.warning("没有符合条件的比赛数据")
        return
    
    # 统计数据
    total_analyzed = 0
    over_win = 0  # 大球赢盘
    under_win = 0  # 小球赢盘
    push = 0  # 走盘
    no_odds = 0  # 无赔率数据
    
    goal_range_matches = []  # 符合进球范围的比赛
    
    for match in matches:
        home_score = match.get('home_score')
        away_score = match.get('away_score')
        
        if not home_score or not away_score or home_score == '-' or away_score == '-':
            continue
        
        try:
            home = int(home_score)
            away = int(away_score)
            actual_total = home + away
            
            # 筛选进球范围
            if min_goals is not None and actual_total < min_goals:
                continue
            if max_goals is not None and actual_total > max_goals:
                continue
            
            # 获取盘口
            total_line = match.get('ou_current_total') or match.get('ou_initial_total')
            if not total_line:
                no_odds += 1
                continue
            
            try:
                total_line = float(total_line)
            except ValueError:
                no_odds += 1
                continue
            
            total_analyzed += 1
            
            # 判断输赢
            if actual_total > total_line:
                result = 'over'
                over_win += 1
            elif actual_total < total_line:
                result = 'under'
                under_win += 1
            else:
                result = 'push'
                push += 1
            
            goal_range_matches.append({
                'match_id': match.get('match_id'),
                'league': match.get('league'),
                'match_time': match.get('match_time'),
                'home_team': match.get('home_team'),
                'away_team': match.get('away_team'),
                'score': f"{home}-{away}",
                'total_goals': actual_total,
                'total_line': total_line,
                'result': result
            })
            
        except Exception as e:
            logger.warning(f"解析比赛失败: {str(e)}")
            continue
    
    # 输出统计结果
    print("\n" + "=" * 70)
    print("大小球赢盘概率分析报告")
    print("=" * 70)
    
    if league:
        print(f"联赛筛选: {league}")
    if min_goals is not None or max_goals is not None:
        goal_range = f"{min_goals or 0}-{max_goals or '∞'}"
        print(f"进球范围: {goal_range} 球")
    
    print(f"\n总完场比赛数: {len(matches)}")
    print(f"符合条件的比赛数: {total_analyzed}")
    print(f"无赔率数据: {no_odds}")
    
    if total_analyzed > 0:
        print("\n--- 大小球赢盘统计 ---")
        print(f"大球赢盘: {over_win} 场 ({over_win/total_analyzed*100:.2f}%)")
        print(f"小球赢盘: {under_win} 场 ({under_win/total_analyzed*100:.2f}%)")
        print(f"走盘: {push} 场 ({push/total_analyzed*100:.2f}%)")
        
        # 显示前10场比赛示例
        print("\n--- 比赛示例（前10场）---")
        print(f"{'联赛':<10} {'时间':<15} {'主队':<15} {'比分':<8} {'客队':<15} {'盘口':<6} {'结果':<8}")
        print("-" * 95)
        for m in goal_range_matches[:10]:
            result_cn = {'over': '大球赢', 'under': '小球赢', 'push': '走盘'}.get(m['result'], m['result'])
            print(f"{m['league']:<10} {m['match_time']:<15} {m['home_team']:<15} {m['score']:<8} {m['away_team']:<15} {m['total_line']:<6} {result_cn:<8}")
        
        if len(goal_range_matches) > 10:
            print(f"\n... 还有 {len(goal_range_matches) - 10} 场比赛")
    
    print("\n" + "=" * 70)


def main():
    """主函数 - 支持命令行参数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='大小球赢盘概率分析工具')
    parser.add_argument('--league', type=str, help='联赛名称，如：西甲')
    parser.add_argument('--min-goals', type=int, help='最小总进球数')
    parser.add_argument('--max-goals', type=int, help='最大总进球数')
    
    args = parser.parse_args()
    
    # 示例：分析西甲2-3球的大小球概率
    # python3 analyze_odds.py --league 西甲 --min-goals 2 --max-goals 3
    
    analyze_over_under_probability(
        league=args.league,
        min_goals=args.min_goals,
        max_goals=args.max_goals
    )


if __name__ == '__main__':
    main()

"""
大小球投注比赛推荐系统
基于历史数据分析，智能推荐高胜率的投注机会
"""
from db_storage import MongoDBStorage
from utils import setup_logger
from collections import defaultdict


def analyze_league_pattern(storage, league):
    """
    分析特定联赛的大小球规律
    
    Returns:
        dict: 包含不同盘口的胜率统计
    """
    filters = {'status': 2, 'league': league}
    matches = storage.get_matches(filters=filters)
    
    line_stats = defaultdict(lambda: {'over': 0, 'under': 0, 'push': 0, 'total': 0})
    
    for match in matches:
        home_score = match.get('home_score')
        away_score = match.get('away_score')
        total_line = match.get('ou_current_total') or match.get('ou_initial_total')
        
        if not all([home_score, away_score, total_line]) or home_score == '-' or away_score == '-':
            continue
        
        try:
            home = int(home_score)
            away = int(away_score)
            actual_total = home + away
            line = float(total_line)
            
            line_key = str(line)
            line_stats[line_key]['total'] += 1
            
            if actual_total > line:
                line_stats[line_key]['over'] += 1
            elif actual_total < line:
                line_stats[line_key]['under'] += 1
            else:
                line_stats[line_key]['push'] += 1
                
        except Exception:
            continue
    
    return dict(line_stats)


def recommend_matches(min_confidence=60.0):
    """
    推荐适合投注的比赛
    
    Args:
        min_confidence: 最低置信度（百分比），默认60%
    """
    logger = setup_logger()
    
    try:
        storage = MongoDBStorage()
        logger.info("成功连接MongoDB")
    except Exception as e:
        logger.error(f"MongoDB连接失败: {str(e)}")
        return
    
    # 获取未开始的比赛
    upcoming_matches = storage.get_matches(filters={'status': 0})
    
    if not upcoming_matches:
        print("\n暂无未开始的比赛")
        return
    
    print("\n" + "=" * 90)
    print("🎯 大小球投注推荐系统")
    print("=" * 90)
    print(f"最低置信度要求: {min_confidence}%")
    print(f"待分析比赛数: {len(upcoming_matches)}")
    print("=" * 90)
    
    # 分析各联赛规律
    league_patterns = {}
    leagues = set(m.get('league') for m in upcoming_matches if m.get('league'))
    
    for league in leagues:
        league_patterns[league] = analyze_league_pattern(storage, league)
    
    # 推荐列表
    recommendations = []
    
    for match in upcoming_matches:
        league = match.get('league')
        total_line = match.get('ou_current_total') or match.get('ou_initial_total')
        
        if not league or not total_line:
            continue
        
        try:
            line = float(total_line)
            line_key = str(line)
            
            # 获取该联赛该盘口的历史数据
            if league not in league_patterns or line_key not in league_patterns[league]:
                continue
            
            stats = league_patterns[league][line_key]
            total = stats['total']
            
            if total < 5:  # 样本量太小，不推荐
                continue
            
            over_rate = stats['over'] / total * 100
            under_rate = stats['under'] / total * 100
            
            # 判断推荐方向
            recommendation = None
            confidence = 0
            
            if over_rate >= min_confidence:
                recommendation = '大球'
                confidence = over_rate
            elif under_rate >= min_confidence:
                recommendation = '小球'
                confidence = under_rate
            
            if recommendation:
                recommendations.append({
                    'match': match,
                    'line': line,
                    'recommendation': recommendation,
                    'confidence': confidence,
                    'sample_size': total,
                    'over_rate': over_rate,
                    'under_rate': under_rate
                })
                
        except Exception:
            continue
    
    # 按置信度排序
    recommendations.sort(key=lambda x: x['confidence'], reverse=True)
    
    if not recommendations:
        print(f"\n❌ 未找到满足 {min_confidence}% 置信度的推荐")
        print("\n💡 建议：")
        print("   1. 降低置信度要求（如 --min-confidence 55）")
        print("   2. 等待更多历史数据积累")
        print("   3. 爬取更多历史比赛数据")
    else:
        print(f"\n✅ 找到 {len(recommendations)} 个推荐投注机会\n")
        print(f"{'序号':<4} {'联赛':<10} {'时间':<15} {'主队':<15} {'客队':<15} {'盘口':<6} {'推荐':<6} {'置信度':<8} {'样本':<6}")
        print("-" * 100)
        
        for idx, rec in enumerate(recommendations, 1):
            m = rec['match']
            print(f"{idx:<4} {m.get('league', ''):<10} {m.get('match_time', ''):<15} "
                  f"{m.get('home_team', ''):<15} {m.get('away_team', ''):<15} "
                  f"{rec['line']:<6.1f} {rec['recommendation']:<6} "
                  f"{rec['confidence']:<7.1f}% {rec['sample_size']:<6}")
        
        print("\n" + "=" * 90)
        print("📊 推荐说明")
        print("=" * 90)
        print("• 置信度: 基于该联赛该盘口的历史赢盘概率")
        print("• 样本量: 历史数据中该联赛该盘口的比赛场次")
        print("• 推荐逻辑: 历史胜率 >= 最低置信度要求")
        print("\n⚠️  风险提示")
        print("• 历史数据仅供参考，不构成投注建议")
        print("• 需结合球队状态、伤停情况等因素综合判断")
        print("• 样本量越大，参考价值越高")
        print("=" * 90)


def analyze_team_over_under(team_name, last_n=10):
    """
    分析特定球队的大小球走势
    
    Args:
        team_name: 球队名称
        last_n: 最近N场比赛
    """
    logger = setup_logger()
    
    try:
        storage = MongoDBStorage()
    except Exception as e:
        logger.error(f"MongoDB连接失败: {str(e)}")
        return
    
    # 获取该队所有完场比赛
    all_matches = storage.get_matches(filters={'status': 2})
    team_matches = [m for m in all_matches 
                   if team_name in [m.get('home_team'), m.get('away_team')]]
    
    # 按时间排序（最新的在前）
    team_matches.sort(key=lambda x: x.get('match_time', ''), reverse=True)
    team_matches = team_matches[:last_n]
    
    if not team_matches:
        print(f"\n未找到 {team_name} 的比赛数据")
        return
    
    print(f"\n{team_name} 最近 {len(team_matches)} 场比赛大小球分析")
    print("=" * 100)
    
    over_count = 0
    under_count = 0
    push_count = 0
    
    print(f"{'时间':<15} {'主队':<15} {'比分':<8} {'客队':<15} {'盘口':<6} {'总进球':<6} {'结果':<8}")
    print("-" * 100)
    
    for m in team_matches:
        home_score = m.get('home_score')
        away_score = m.get('away_score')
        total_line = m.get('ou_current_total') or m.get('ou_initial_total')
        
        if not all([home_score, away_score]) or home_score == '-' or away_score == '-':
            continue
        
        try:
            home = int(home_score)
            away = int(away_score)
            actual_total = home + away
            
            result = '-'
            if total_line:
                line = float(total_line)
                if actual_total > line:
                    result = '大球赢'
                    over_count += 1
                elif actual_total < line:
                    result = '小球赢'
                    under_count += 1
                else:
                    result = '走盘'
                    push_count += 1
            
            score = f"{home}-{away}"
            print(f"{m.get('match_time', ''):<15} {m.get('home_team', ''):<15} "
                  f"{score:<8} {m.get('away_team', ''):<15} "
                  f"{total_line or '-':<6} {actual_total:<6} {result:<8}")
                  
        except Exception:
            continue
    
    total = over_count + under_count + push_count
    if total > 0:
        print("\n统计汇总:")
        print(f"大球赢盘: {over_count} 场 ({over_count/total*100:.1f}%)")
        print(f"小球赢盘: {under_count} 场 ({under_count/total*100:.1f}%)")
        print(f"走盘: {push_count} 场 ({push_count/total*100:.1f}%)")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='大小球投注推荐系统')
    parser.add_argument('--min-confidence', type=float, default=60.0,
                       help='最低置信度（百分比），默认60')
    parser.add_argument('--team', type=str, help='分析特定球队的大小球走势')
    parser.add_argument('--last-n', type=int, default=10,
                       help='分析最近N场比赛，默认10场')
    
    args = parser.parse_args()
    
    if args.team:
        analyze_team_over_under(args.team, args.last_n)
    else:
        recommend_matches(args.min_confidence)


if __name__ == '__main__':
    main()

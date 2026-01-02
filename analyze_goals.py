from db_storage import MongoDBStorage
import argparse
from utils import setup_logger

def analyze_goals_distribution(league_name="西甲"):
    logger = setup_logger()
    try:
        storage = MongoDBStorage()
        # Filter for finished matches in the specified league
        matches = storage.get_matches({'status': 2, 'league': league_name})
        
        total_matches = len(matches)
        if total_matches == 0:
            print(f"未找到 {league_name} 的完场比赛数据。")
            return

        goals_2_3 = 0
        goals_distribution = {}

        print(f"\n=== {league_name} 进球数分布分析 (共 {total_matches} 场) ===")
        
        for match in matches:
            try:
                home_score = int(match.get('home_score', 0))
                away_score = int(match.get('away_score', 0))
                total_goals = home_score + away_score
                
                # Update distribution
                goals_distribution[total_goals] = goals_distribution.get(total_goals, 0) + 1
                
                # Check for 2-3 goals
                if 2 <= total_goals <= 3:
                    goals_2_3 += 1
            except (ValueError, TypeError):
                continue

        # Calculate probabilities
        prob_2_3 = (goals_2_3 / total_matches) * 100
        print(f"\n总进球数 2-3 球: {goals_2_3} 场")
        print(f"概率: {prob_2_3:.2f}%")
        
        print("\n详细进球分布:")
        sorted_goals = sorted(goals_distribution.keys())
        for goals in sorted_goals:
            count = goals_distribution[goals]
            percentage = (count / total_matches) * 100
            print(f"{goals} 球: {count} 场 ({percentage:.2f}%)")
            
        # Grouped stats for context
        under_2 = sum(count for g, count in goals_distribution.items() if g < 2)
        over_3 = sum(count for g, count in goals_distribution.items() if g > 3)
        
        print("\n区间统计:")
        print(f"0-1 球: {under_2} 场 ({under_2/total_matches*100:.2f}%)")
        print(f"2-3 球: {goals_2_3} 场 ({prob_2_3:.2f}%)")
        print(f"4+  球: {over_3} 场 ({over_3/total_matches*100:.2f}%)")

    except Exception as e:
        logger.error(f"分析出错: {str(e)}")
        print(f"分析出错: {str(e)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='联赛进球数分布分析')
    parser.add_argument('--league', type=str, default='西甲', help='联赛名称')
    args = parser.parse_args()
    
    analyze_goals_distribution(args.league)



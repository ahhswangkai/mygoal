from db_storage import MongoDBStorage
import argparse
from utils import setup_logger

def analyze_team_goals_average(league_name="英超"):
    logger = setup_logger()
    try:
        storage = MongoDBStorage()
        # Get finished matches for the league
        matches = storage.get_matches({'status': 2, 'league': league_name})
        
        if not matches:
            print(f"未找到 {league_name} 的完场比赛数据。")
            return

        team_stats = {}

        for match in matches:
            try:
                home_team = match.get('home_team')
                away_team = match.get('away_team')
                home_score = int(match.get('home_score', 0))
                away_score = int(match.get('away_score', 0))
                
                # Initialize if not exists
                if home_team not in team_stats:
                    team_stats[home_team] = {'goals': 0, 'matches': 0, 'conceded': 0}
                if away_team not in team_stats:
                    team_stats[away_team] = {'goals': 0, 'matches': 0, 'conceded': 0}
                
                # Update Home Team
                team_stats[home_team]['goals'] += home_score
                team_stats[home_team]['conceded'] += away_score
                team_stats[home_team]['matches'] += 1
                
                # Update Away Team
                team_stats[away_team]['goals'] += away_score
                team_stats[away_team]['conceded'] += home_score
                team_stats[away_team]['matches'] += 1
                
            except (ValueError, TypeError):
                continue

        # Calculate averages and prepare list for sorting
        results = []
        for team, stats in team_stats.items():
            avg_goals = stats['goals'] / stats['matches'] if stats['matches'] > 0 else 0
            avg_conceded = stats['conceded'] / stats['matches'] if stats['matches'] > 0 else 0
            avg_total = (stats['goals'] + stats['conceded']) / stats['matches'] if stats['matches'] > 0 else 0
            
            results.append({
                'team': team,
                'matches': stats['matches'],
                'avg_goals': avg_goals,
                'avg_conceded': avg_conceded,
                'avg_total': avg_total,
                'total_goals': stats['goals']
            })

        # Sort by Average Goals Scored (descending)
        results.sort(key=lambda x: x['avg_goals'], reverse=True)

        print(f"\n=== {league_name} 球队进球数据统计 (共 {len(results)} 支球队) ===")
        print(f"{'球队':<15} {'场次':<5} {'场均进球':<10} {'场均失球':<10} {'场均总球':<10}")
        print("-" * 65)
        
        for r in results:
            print(f"{r['team']:<15} {r['matches']:<5} {r['avg_goals']:<10.2f} {r['avg_conceded']:<10.2f} {r['avg_total']:<10.2f}")

    except Exception as e:
        logger.error(f"分析出错: {str(e)}")
        print(f"分析出错: {str(e)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='球队场均进球分析')
    parser.add_argument('--league', type=str, default='英超', help='联赛名称')
    args = parser.parse_args()
    
    analyze_team_goals_average(args.league)



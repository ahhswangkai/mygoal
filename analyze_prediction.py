from db_storage import MongoDBStorage

def analyze_teams(home_team, away_team):
    storage = MongoDBStorage()
    
    print(f"=== 分析球队: {home_team} (主) vs {away_team} (客) ===\n")
    
    # 1. Recent Form (Last 5 matches for each)
    def get_team_matches(team_name):
        # Find matches where team is home OR away, and status is finished
        query = {
            '$or': [{'home_team': team_name}, {'away_team': team_name}],
            'status': 2
        }
        matches = list(storage.db.matches.find(query).sort('match_time', -1).limit(10))
        return matches

    def print_form(team_name, matches):
        print(f"--- {team_name} 近况 (近 {len(matches)} 场) ---")
        goals_scored = 0
        goals_conceded = 0
        wins = 0
        draws = 0
        losses = 0
        
        for m in matches:
            is_home = m['home_team'] == team_name
            h_score = int(m['home_score'])
            a_score = int(m['away_score'])
            
            if is_home:
                goals_scored += h_score
                goals_conceded += a_score
                if h_score > a_score: wins += 1
                elif h_score == a_score: draws += 1
                else: losses += 1
                res = "胜" if h_score > a_score else "平" if h_score == a_score else "负"
                print(f"主 vs {m['away_team']:<10} {h_score}-{a_score} ({res})")
            else:
                goals_scored += a_score
                goals_conceded += h_score
                if a_score > h_score: wins += 1
                elif a_score == h_score: draws += 1
                else: losses += 1
                res = "胜" if a_score > h_score else "平" if a_score == h_score else "负"
                print(f"客 vs {m['home_team']:<10} {h_score}-{a_score} ({res})")
                
        if matches:
            print(f"战绩: {wins}胜 {draws}平 {losses}负")
            print(f"场均进球: {goals_scored/len(matches):.2f}, 场均失球: {goals_conceded/len(matches):.2f}\n")

    home_matches = get_team_matches(home_team)
    away_matches = get_team_matches(away_team)
    
    print_form(home_team, home_matches)
    print_form(away_team, away_matches)
    
    # 2. Head to Head (H2H)
    print("--- 历史交锋 (最近) ---")
    h2h_query = {
        '$or': [
            {'home_team': home_team, 'away_team': away_team},
            {'home_team': away_team, 'away_team': home_team}
        ],
        'status': 2
    }
    h2h_matches = list(storage.db.matches.find(h2h_query).sort('match_time', -1).limit(5))
    
    if not h2h_matches:
        print("无历史交锋数据")
    else:
        for m in h2h_matches:
            print(f"{m['match_time']} {m['home_team']} {m['home_score']}-{m['away_score']} {m['away_team']}")

if __name__ == "__main__":
    analyze_teams("巴萨", "马竞")



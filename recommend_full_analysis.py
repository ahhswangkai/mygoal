from db_storage import MongoDBStorage
import itertools

def recommend_with_full_analysis():
    storage = MongoDBStorage()
    upcoming_matches = storage.get_matches({'status': 0})
    finished_matches = storage.get_matches({'status': 2})
    
    print("=" * 70)
    print("🔍 AI 智能二串一推荐系统 (目标赔率 ~3.0)")
    print("=" * 70)
    
    print(f"\n📊 数据源: {len(upcoming_matches)} 场未开赛 | {len(finished_matches)} 场历史数据")
    
    if not upcoming_matches:
        print("⚠️ 暂无未开赛数据。")
        return

    # Step 1: 构建球队近期表现数据库
    print("\n【第一步】构建球队近期表现档案...")
    team_stats = {}
    
    for m in finished_matches:
        try:
            home = m['home_team']
            away = m['away_team']
            h_score = int(m['home_score'])
            a_score = int(m['away_score'])
            total_goals = h_score + a_score
            
            # 初始化
            for t in [home, away]:
                if t not in team_stats:
                    team_stats[t] = {'matches': 0, 'goals_for': 0, 'goals_against': 0, 
                                     'wins': 0, 'draws': 0, 'losses': 0, 'big_games': 0}
            
            # 主队
            team_stats[home]['matches'] += 1
            team_stats[home]['goals_for'] += h_score
            team_stats[home]['goals_against'] += a_score
            if h_score > a_score: team_stats[home]['wins'] += 1
            elif h_score == a_score: team_stats[home]['draws'] += 1
            else: team_stats[home]['losses'] += 1
            if total_goals >= 3: team_stats[home]['big_games'] += 1
            
            # 客队
            team_stats[away]['matches'] += 1
            team_stats[away]['goals_for'] += a_score
            team_stats[away]['goals_against'] += h_score
            if a_score > h_score: team_stats[away]['wins'] += 1
            elif a_score == h_score: team_stats[away]['draws'] += 1
            else: team_stats[away]['losses'] += 1
            if total_goals >= 3: team_stats[away]['big_games'] += 1
            
        except:
            continue
    
    print(f"   已建档球队: {len(team_stats)} 支")

    # Step 2: 分析每场未开赛比赛
    print("\n【第二步】逐场分析未开赛比赛...")
    print("-" * 70)
    
    candidates = []
    
    for m in upcoming_matches:
        try:
            home = m['home_team']
            away = m['away_team']
            league = m['league']
            match_name = f"{league} {home} vs {away}"
            
            # 获取赔率
            win_odds = float(m.get('euro_initial_win') or 0)
            draw_odds = float(m.get('euro_initial_draw') or 0)
            lose_odds = float(m.get('euro_initial_lose') or 0)
            ou_line = float(m.get('ou_initial_total') or 0)
            ou_over = float(m.get('ou_initial_over_odds') or 0)
            real_ou_odds = ou_over + 1.0  # 香港盘转欧赔
            
            # 获取球队数据
            h_stats = team_stats.get(home, {})
            a_stats = team_stats.get(away, {})
            
            analysis = []
            recommendation = None
            rec_odds = 0
            rec_reason = ""
            
            # 分析1: 主胜可能性
            if 1.65 <= win_odds <= 1.95:
                h_win_rate = h_stats.get('wins', 0) / max(h_stats.get('matches', 1), 1) * 100
                a_loss_rate = a_stats.get('losses', 0) / max(a_stats.get('matches', 1), 1) * 100
                
                if h_win_rate >= 40 or a_loss_rate >= 40:
                    analysis.append(f"主胜分析: 赔率{win_odds}在价值区间")
                    analysis.append(f"  - {home}胜率: {h_win_rate:.0f}%")
                    analysis.append(f"  - {away}败率: {a_loss_rate:.0f}%")
                    
                    if h_win_rate >= 50:
                        recommendation = "主胜"
                        rec_odds = win_odds
                        rec_reason = f"{home}近期胜率{h_win_rate:.0f}%，状态火热"
            
            # 分析2: 客胜可能性
            if 1.65 <= lose_odds <= 1.95:
                a_win_rate = a_stats.get('wins', 0) / max(a_stats.get('matches', 1), 1) * 100
                h_loss_rate = h_stats.get('losses', 0) / max(h_stats.get('matches', 1), 1) * 100
                
                if a_win_rate >= 40 or h_loss_rate >= 40:
                    analysis.append(f"客胜分析: 赔率{lose_odds}在价值区间")
                    analysis.append(f"  - {away}胜率: {a_win_rate:.0f}%")
                    analysis.append(f"  - {home}败率: {h_loss_rate:.0f}%")
                    
                    if a_win_rate >= 50 and not recommendation:
                        recommendation = "客胜"
                        rec_odds = lose_odds
                        rec_reason = f"{away}近期胜率{a_win_rate:.0f}%，客场有威胁"
            
            # 分析3: 大小球
            if 1.70 <= real_ou_odds <= 2.0 and ou_line >= 2.5:
                h_big_rate = h_stats.get('big_games', 0) / max(h_stats.get('matches', 1), 1) * 100
                a_big_rate = a_stats.get('big_games', 0) / max(a_stats.get('matches', 1), 1) * 100
                avg_big_rate = (h_big_rate + a_big_rate) / 2
                
                h_avg_goals = (h_stats.get('goals_for', 0) + h_stats.get('goals_against', 0)) / max(h_stats.get('matches', 1), 1)
                a_avg_goals = (a_stats.get('goals_for', 0) + a_stats.get('goals_against', 0)) / max(a_stats.get('matches', 1), 1)
                
                analysis.append(f"大球分析: 盘口{ou_line}球，赔率{real_ou_odds:.2f}")
                analysis.append(f"  - {home}大球率: {h_big_rate:.0f}% (场均{h_avg_goals:.1f}球)")
                analysis.append(f"  - {away}大球率: {a_big_rate:.0f}% (场均{a_avg_goals:.1f}球)")
                
                if avg_big_rate >= 50 and not recommendation:
                    recommendation = "大球"
                    rec_odds = real_ou_odds
                    rec_reason = f"双方大球率均超50%，场面开放"
                elif ou_line >= 3.0 and not recommendation:
                    # 盘口深开，机构看好
                    recommendation = "大球"
                    rec_odds = real_ou_odds
                    rec_reason = f"盘口深开至{ou_line}球，机构信心足"
            
            # 如果有推荐，输出分析并加入候选
            if recommendation and 1.65 <= rec_odds <= 2.0:
                print(f"\n📌 {match_name} ({m['match_time']})")
                for line in analysis:
                    print(f"   {line}")
                print(f"   ✅ 推荐: {recommendation} @ {rec_odds:.2f}")
                print(f"   📝 理由: {rec_reason}")
                
                candidates.append({
                    'match': m,
                    'type': recommendation,
                    'odds': rec_odds,
                    'reason': rec_reason,
                    'analysis': analysis
                })
                
        except Exception as e:
            continue
    
    print("-" * 70)
    print(f"\n【第三步】组合优化 (目标: 总赔率 ≈ 3.0)")
    print(f"   候选数量: {len(candidates)} 个")
    
    if len(candidates) < 2:
        print("⚠️ 候选不足，无法组成二串一。")
        return

    # 寻找最优组合
    best_combo = None
    min_diff = 999
    
    for c1, c2 in itertools.combinations(candidates, 2):
        if c1['match']['match_id'] == c2['match']['match_id']:
            continue
        total_odds = c1['odds'] * c2['odds']
        diff = abs(total_odds - 3.0)
        
        if 2.8 <= total_odds <= 3.5 and diff < min_diff:
            min_diff = diff
            best_combo = (c1, c2)
    
    if not best_combo:
        print("⚠️ 未找到满足条件的组合。")
        return
        
    c1, c2 = best_combo
    total_sp = c1['odds'] * c2['odds']
    
    print(f"   最优组合: {c1['odds']:.2f} × {c2['odds']:.2f} = {total_sp:.2f}")
    
    # 最终输出
    print("\n" + "=" * 70)
    print(f"🎯 最终推荐方案 (总赔率: {total_sp:.2f})")
    print("=" * 70)
    
    for idx, item in enumerate([c1, c2], 1):
        m = item['match']
        print(f"\n【关卡{idx}】{m['league']} | {m['match_time']}")
        print(f"   对阵: {m['home_team']} vs {m['away_team']}")
        print(f"   推荐: 【{item['type']}】 @ {item['odds']:.2f}")
        print(f"   核心理由: {item['reason']}")
        print("   详细分析:")
        for line in item['analysis']:
            print(f"      {line}")
    
    print("\n" + "=" * 70)
    print(f"💰 投注建议: 100元 → 预计回报 {total_sp * 100:.0f}元")
    print("⚠️ 风险提示: 竞技体育无绝对，建议轻注娱乐，理性投注。")
    print("=" * 70)

if __name__ == "__main__":
    recommend_with_full_analysis()



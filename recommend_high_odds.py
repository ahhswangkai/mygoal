from db_storage import MongoDBStorage
import itertools

def recommend_high_odds_2chuan1():
    storage = MongoDBStorage()
    upcoming_matches = storage.get_matches({'status': 0})
    
    print("=== 🕵️‍♂️ 智能推荐分析过程日志 ===\n")
    print(f"1. 数据扫描: 发现 {len(upcoming_matches)} 场未开赛比赛")
    
    if not upcoming_matches:
        print("⚠️ 暂无数据，流程终止。")
        return

    print("2. 筛选高价值候选 (策略: 寻找单场赔率 1.65-1.95 的稳健选项)")
    print("   - 胜平负: 寻找势均力敌的主胜或强队客胜")
    print("   - 大小球: 寻找盘口>=2.5且赔率适中的大球")
    print("-" * 60)

    candidates = []
    for m in upcoming_matches:
        try:
            match_name = f"{m['league']} {m['home_team']} vs {m['away_team']}"
            
            # 1. 胜平负 (欧赔)
            win_odds = float(m.get('euro_initial_win') or 0)
            lose_odds = float(m.get('euro_initial_lose') or 0)
            
            added = False
            # 寻找 1.65 - 1.95
            if 1.65 <= win_odds <= 1.95:
                print(f"   ✅ [入选] {match_name} -> 主胜 (SP:{win_odds})")
                candidates.append({
                    'match': m,
                    'type': '主胜',
                    'odds': win_odds,
                    'reason': '主胜赔率适中，回报可观'
                })
                added = True
            elif 1.65 <= lose_odds <= 1.95:
                print(f"   ✅ [入选] {match_name} -> 客胜 (SP:{lose_odds})")
                candidates.append({
                    'match': m,
                    'type': '客胜',
                    'odds': lose_odds,
                    'reason': '客队实力占优，赔率诱人'
                })
                added = True
                
            # 2. 大小球
            ou_line = float(m.get('ou_initial_total') or 0)
            ou_over = float(m.get('ou_initial_over_odds') or 0)
            # 香港盘转欧赔
            real_ou_odds = ou_over + 1.0
            
            if 1.70 <= real_ou_odds <= 2.0 and ou_line >= 2.5:
                print(f"   ✅ [入选] {match_name} -> 大球 (SP:{real_ou_odds:.2f}) [盘口:{ou_line}]")
                candidates.append({
                    'match': m,
                    'type': '大球',
                    'odds': real_ou_odds,
                    'reason': f'盘口 {ou_line}球，看好打出'
                })
                added = True
            
        except Exception as e:
            continue
            
    print("-" * 60)
    print(f"3. 候选池构建完成: 共 {len(candidates)} 个选项")
    
    if len(candidates) < 2:
        print("⚠️ 候选不足，无法组成二串一。")
        return

    print("4. 组合计算 (寻找总赔率最接近 3.0 的最优解)...")
    
    best_combo = None
    min_diff = 999
    
    combinations = list(itertools.combinations(candidates, 2))
    
    # Show top 3 closest calculations for transparency
    calculations = []
    
    for c1, c2 in combinations:
        # 避免同一场比赛
        if c1['match']['match_id'] == c2['match']['match_id']:
            continue
            
        total_odds = c1['odds'] * c2['odds']
        diff = abs(total_odds - 3.0)
        
        calculations.append({
            'c1': c1, 'c2': c2, 'total': total_odds, 'diff': diff
        })
        
        if 2.8 <= total_odds <= 3.5:
            if diff < min_diff:
                min_diff = diff
                best_combo = (c1, c2)
                
    # Sort calculations by closeness to 3.0 to show "Thought Process"
    calculations.sort(key=lambda x: x['diff'])
    
    print("   前3名备选方案计算:")
    for idx, calc in enumerate(calculations[:3]):
        c1, c2 = calc['c1'], calc['c2']
        m1 = c1['match']
        m2 = c2['match']
        print(f"   [{idx+1}] {c1['type']}({c1['odds']:.2f}) x {c2['type']}({c2['odds']:.2f}) = {calc['total']:.2f}")
        print(f"       A: {m1['home_team']} vs {m1['away_team']}")
        print(f"       B: {m2['home_team']} vs {m2['away_team']}")
    
    if best_combo:
        c1, c2 = best_combo
        total_sp = c1['odds'] * c2['odds']
        
        print("\n" + "="*50)
        print(f"🎯 最终推荐方案 (总赔率: {total_sp:.2f})")
        print("="*50)
        
        for item in [c1, c2]:
            m = item['match']
            print(f"🏅 {m['league']} | {m['home_team']} vs {m['away_team']}")
            print(f"   👉 推荐: {item['type']} @ {item['odds']:.2f}")
            print(f"   📝 理由: {item['reason']}")
            print("-" * 50)
    else:
        print("\n⚠️ 未找到完美匹配 3.0 倍率的组合")

if __name__ == "__main__":
    recommend_high_odds_2chuan1()

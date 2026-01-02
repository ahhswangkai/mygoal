from db_storage import MongoDBStorage
from datetime import datetime
import random

def recommend_2chuan1():
    storage = MongoDBStorage()
    
    # 1. 查找近期(未来24小时)的高信心比赛
    # 策略：
    # A. 强队主场打弱队 (赔率 1.2 - 1.5) -> 稳胆
    # B. 进球数大球 (近期大球率高 + 盘口合适)
    
    print("=== 正在寻找稳妥二串一方案 ===\n")
    
    # 模拟：获取未开赛的比赛 (实际应从DB获取 status=0 的比赛)
    # 由于现在是回测环境，我们找一些 status=2 (完场) 的比赛来模拟"推荐"，
    # 并验证如果是当时推荐的话，结果如何。
    
    matches = storage.get_matches({'status': 2}) # 使用完场数据模拟推荐验证
    
    # 筛选标准：
    # 1. 强弱分明：主胜赔率 < 1.50
    # 2. 大球概率：大小球盘口 >= 2.5 且 主队近期大球率高
    
    candidates = []
    
    for m in matches:
        try:
            # 解析赔率
            home_win_odds = float(m.get('euro_initial_win', 0) or 0)
            if 1.1 < home_win_odds < 1.65: # 筛选低赔稳胆
                candidates.append(m)
        except:
            continue
            
    if not candidates:
        print("暂无符合'稳胆'条件的比赛。")
        return

    # 随机选2场模拟 "二串一"
    # 实际推荐逻辑会更复杂，这里演示思路
    
    selected = random.sample(candidates, min(2, len(candidates)))
    
    total_sp = 1.0
    
    print("🔥 推荐方案 (基于赔率模型):")
    for idx, m in enumerate(selected):
        home = m['home_team']
        away = m['away_team']
        win_odds = float(m['euro_initial_win'])
        
        # 验证结果
        score_home = int(m['home_score'])
        score_away = int(m['away_score'])
        result = "红" if score_home > score_away else "黑"
        
        print(f"关卡{idx+1}: {m['league']} - {home} vs {away}")
        print(f"  推荐: 主胜 (SP: {win_odds})")
        print(f"  理由: 主胜赔率 {win_odds} 区间，机构防范力度大")
        print(f"  验证: 比分 {score_home}-{score_away} -> [{result}]")
        
        if result == "红":
            total_sp *= win_odds
        else:
            total_sp = 0
            
    print(f"\n理论回报率: {total_sp:.2f}倍")
    if total_sp > 0:
        print("✅ 方案红单！")
    else:
        print("❌ 方案未打出")

if __name__ == "__main__":
    recommend_2chuan1()



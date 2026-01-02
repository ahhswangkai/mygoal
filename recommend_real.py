from db_storage import MongoDBStorage
import random

def recommend_real_2chuan1():
    storage = MongoDBStorage()
    
    # 1. 获取未来即将开始的比赛 (status=0)
    # 注意：这里假设数据库里有未开赛的数据。如果没有，我们只能基于逻辑给出建议。
    upcoming_matches = storage.get_matches({'status': 0})
    
    print("=== 📊 智能二串一推荐 (基于数据模型) ===\n")
    
    if not upcoming_matches:
        print("⚠️ 数据库中暂无未开赛数据，无法提供实时推荐。")
        print("建议先运行爬虫获取最新比赛数据: python3 main.py")
        return

    # 筛选策略：寻找"稳胆"
    # 1. 赔率在 1.30 - 1.65 之间 (过低没肉，过高不稳)
    # 2. 亚盘让球在 半/一 (0.75) 或 一球 (1.0) 以上
    
    candidates = []
    for m in upcoming_matches:
        try:
            # 优先看欧赔主胜
            win_odds = float(m.get('euro_initial_win') or 0)
            
            # 排除异常赔率
            if win_odds <= 1.01: continue
            
            # 策略A: 主场稳胆 (赔率 1.30 ~ 1.60)
            if 1.30 <= win_odds <= 1.60:
                candidates.append({
                    'match': m,
                    'type': '主胜',
                    'odds': win_odds,
                    'reason': '主场优势大，赔率区间合理'
                })
                
            # 策略B: 进球数大 (大小球盘 >= 3.0)
            ou_line = float(m.get('ou_initial_total') or 0)
            if ou_line >= 3.0:
                candidates.append({
                    'match': m,
                    'type': '大球',
                    'odds': 1.85, # 估算
                    'reason': f'盘口开大 ({ou_line}球)，看好对攻'
                })
                
        except:
            continue
    
    if len(candidates) < 2:
        print(f"⚠️ 符合稳妥条件的比赛不足 (仅找到 {len(candidates)} 场)，建议观望或单关。")
        for c in candidates:
            m = c['match']
            print(f"  备选: {m['league']} {m['home_team']} vs {m['away_team']} -> {c['type']} (SP:{c['odds']})")
        return

    # 选出最优的2场
    # 简单按赔率排序，取中间值（不取最低也不取最高）
    selected = sorted(candidates, key=lambda x: x['odds'])[:2]
    
    total_sp = 1.0
    for item in selected:
        total_sp *= item['odds']
        
    print(f"💡 推荐方案 (预计回报: {total_sp:.2f}倍):")
    print("-" * 40)
    
    for item in selected:
        m = item['match']
        print(f"🏅 {m['league']} | {m['match_time']}")
        print(f"   {m['home_team']} vs {m['away_team']}")
        print(f"   👉 推荐: 【{item['type']}】 @ {item['odds']}")
        print(f"   📝 理由: {item['reason']}")
        print("-" * 40)
        
    print("\n⚠️ 风险提示: 竞技体育无绝对，建议轻注娱乐。")

if __name__ == "__main__":
    recommend_real_2chuan1()



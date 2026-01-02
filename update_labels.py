"""
为现有比赛数据补充盘口变动标签
"""
from db_storage import MongoDBStorage
import re

def update_all_labels():
    storage = MongoDBStorage()
    
    # 获取所有比赛
    matches = storage.get_matches()
    print(f"共找到 {len(matches)} 场比赛，开始更新标签...")
    
    def parse_handicap(h):
        if not h:
            return None
        handicap_map = {
            '平手': 0, '平/半': 0.25, '平手/半球': 0.25,
            '半球': 0.5, '半/一': 0.75, '半球/一球': 0.75,
            '一球': 1.0, '一/球半': 1.25, '一球/球半': 1.25,
            '球半': 1.5, '球半/两': 1.75, '球半/两球': 1.75,
            '两球': 2.0, '两/两球半': 2.25, '两球半': 2.5
        }
        clean = str(h).replace('受', '')
        is_receiver = '受' in str(h)
        
        if clean in handicap_map:
            val = handicap_map[clean]
            return -val if is_receiver else val
        
        nums = re.findall(r'\d+\.?\d*', str(h))
        if nums:
            val = float(nums[0])
            return -val if is_receiver else val
        return None
    
    def safe_float(x):
        try:
            return float(x)
        except:
            return None
    
    def calc_label(m):
        h_init = parse_handicap(m.get('asian_initial_handicap'))
        h_curr = parse_handicap(m.get('asian_current_handicap'))
        home_init = safe_float(m.get('asian_initial_home_odds'))
        home_curr = safe_float(m.get('asian_current_home_odds'))
        
        if h_init is None or h_curr is None or home_init is None or home_curr is None:
            return None
        
        handicap_change = h_curr - h_init
        water_change = home_curr - home_init
        
        if abs(handicap_change) < 0.01 and abs(water_change) < 0.02:
            return '无变化'
        
        if handicap_change > 0.01:
            if water_change < -0.02:
                return '升盘降水'
            elif water_change > 0.02:
                return '升盘升水'
            else:
                return '升盘'
        elif handicap_change < -0.01:
            if water_change < -0.02:
                return '降盘降水'
            elif water_change > 0.02:
                return '降盘升水'
            else:
                return '降盘'
        else:
            if water_change < -0.02:
                return '降水'
            elif water_change > 0.02:
                return '升水'
            else:
                return '无变化'
    
    updated = 0
    for m in matches:
        label = calc_label(m)
        if label:
            storage.matches_collection.update_one(
                {'match_id': m['match_id']},
                {'$set': {'asian_movement_label': label}}
            )
            updated += 1
    
    print(f"更新完成！共更新 {updated} 场比赛的标签")
    
    # 统计标签分布
    labels = {}
    for m in storage.get_matches():
        l = m.get('asian_movement_label', '无数据')
        labels[l] = labels.get(l, 0) + 1
    
    print("\n标签分布:")
    for l, c in sorted(labels.items(), key=lambda x: -x[1]):
        print(f"  {l}: {c} 场")

if __name__ == "__main__":
    update_all_labels()



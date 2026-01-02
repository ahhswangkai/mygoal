from db_storage import MongoDBStorage
import re

def analyze_handicap_movement(league="英超"):
    """
    分析盘口变动情况
    """
    storage = MongoDBStorage()
    
    matches = storage.get_matches({'status': 2, 'league': league})
    
    print(f"=== {league} 盘口变动分析 ===")
    print(f"共找到 {len(matches)} 场完场比赛\n")
    
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
        clean = h.replace('受', '')
        is_receiver = '受' in h
        
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
    
    # 分类统计
    shengpan_jiangshui = []  # 升盘降水
    shengpan_shengshui = []  # 升盘升水
    jiangpan_jiangshui = []  # 降盘降水
    jiangpan_shengshui = []  # 降盘升水
    no_change = []           # 无变化
    
    for m in matches:
        h_init = parse_handicap(m.get('asian_initial_handicap'))
        h_curr = parse_handicap(m.get('asian_current_handicap'))
        home_init = safe_float(m.get('asian_initial_home_odds'))
        home_curr = safe_float(m.get('asian_current_home_odds'))
        
        if h_init is None or h_curr is None or home_init is None or home_curr is None:
            continue
        
        handicap_change = h_curr - h_init
        water_change = home_curr - home_init
        
        home_score = int(m.get('home_score', 0))
        away_score = int(m.get('away_score', 0))
        
        # 判断主队是否赢盘
        adjusted = home_score + h_curr - away_score
        if adjusted > 0:
            result = "赢"
        elif adjusted < 0:
            result = "输"
        else:
            result = "走"
        
        record = {
            'match': m,
            'h_init': h_init,
            'h_curr': h_curr,
            'h_change': handicap_change,
            'w_init': home_init,
            'w_curr': home_curr,
            'w_change': water_change,
            'score': f"{home_score}-{away_score}",
            'result': result
        }
        
        # 分类
        if abs(handicap_change) < 0.01 and abs(water_change) < 0.01:
            no_change.append(record)
        elif handicap_change > 0 and water_change < 0:
            shengpan_jiangshui.append(record)
        elif handicap_change > 0 and water_change > 0:
            shengpan_shengshui.append(record)
        elif handicap_change < 0 and water_change < 0:
            jiangpan_jiangshui.append(record)
        elif handicap_change < 0 and water_change > 0:
            jiangpan_shengshui.append(record)
        else:
            # 只有水位变化，盘口不变
            if water_change < 0:
                jiangpan_jiangshui.append(record)  # 归为降水
            else:
                jiangpan_shengshui.append(record)  # 归为升水
    
    def print_category(name, records):
        if not records:
            print(f"\n【{name}】: 0 场")
            return
        
        win = sum(1 for r in records if r['result'] == '赢')
        lose = sum(1 for r in records if r['result'] == '输')
        push = sum(1 for r in records if r['result'] == '走')
        
        print(f"\n【{name}】: {len(records)} 场 (主队赢盘率: {win/len(records)*100:.1f}%)")
        print("-" * 100)
        print(f"{'时间':<12} {'主队':<10} {'比分':<6} {'客队':<10} {'初盘':<15} {'即盘':<15} {'盘变':<6} {'水变':<8} {'结果'}")
        print("-" * 100)
        
        for r in records:
            m = r['match']
            init_str = f"{r['w_init']:.2f} / {r['h_init']:+.2f}"
            curr_str = f"{r['w_curr']:.2f} / {r['h_curr']:+.2f}"
            h_str = f"{r['h_change']:+.2f}"
            w_str = f"{r['w_change']:+.2f}"
            result_icon = "✅" if r['result'] == '赢' else ("❌" if r['result'] == '输' else "➖")
            
            print(f"{m.get('match_time', ''):<12} {m.get('home_team', ''):<10} {r['score']:<6} {m.get('away_team', ''):<10} {init_str:<15} {curr_str:<15} {h_str:<6} {w_str:<8} {result_icon}")
        
        print(f"\n统计: 赢盘 {win} | 输盘 {lose} | 走盘 {push}")
    
    print_category("升盘降水 (机构看好主队)", shengpan_jiangshui)
    print_category("升盘升水 (可能诱盘)", shengpan_shengshui)
    print_category("降盘降水 (机构看好客队)", jiangpan_jiangshui)
    print_category("降盘升水 (可能诱盘)", jiangpan_shengshui)
    print_category("盘口水位无变化", no_change)
    
    print("\n" + "=" * 50)
    print("总结:")
    total = len(shengpan_jiangshui) + len(shengpan_shengshui) + len(jiangpan_jiangshui) + len(jiangpan_shengshui) + len(no_change)
    print(f"有效数据: {total} 场")

if __name__ == "__main__":
    analyze_handicap_movement("英超")

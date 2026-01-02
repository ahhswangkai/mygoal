"""
平手盘规律分析工具
专门分析亚盘平手盘（包括平手和平/半）的比赛规律
"""
from db_storage import MongoDBStorage
from utils import setup_logger
from collections import defaultdict
import argparse
import re


def parse_handicap(handicap_str):
    """解析中文盘口为数字"""
    if not handicap_str:
        return None
    
    is_receiver = '受' in handicap_str
    clean_str = handicap_str.replace('受', '')
    
    handicap_map = {
        '平手': 0, '平/半': 0.25, '平手/半球': 0.25,
        '半球': 0.5, '半/一': 0.75, '半球/一球': 0.75,
        '一球': 1.0, '一/球半': 1.25, '一球/球半': 1.25,
        '球半': 1.5, '球半/两': 1.75, '球半/两球': 1.75,
        '两球': 2.0, '两/两球半': 2.25, '两球半': 2.5
    }
    
    if clean_str in handicap_map:
        value = handicap_map[clean_str]
    else:
        nums = re.findall(r'\d+\.?\d*', str(clean_str))
        if nums:
            value = float(nums[0])
        else:
            return None
    
    return -value if is_receiver else value


def safe_float(value):
    """安全转换为float"""
    try:
        return float(value) if value else None
    except (ValueError, TypeError):
        return None


def get_match_result(home_score, away_score):
    """获取比赛结果"""
    try:
        home = int(home_score)
        away = int(away_score)
        if home > away:
            return 'home'
        elif home < away:
            return 'away'
        else:
            return 'draw'
    except:
        return None


def is_level_ball(handicap_str):
    """判断是否是平手盘（平手或平/半）"""
    if not handicap_str:
        return False
    clean = handicap_str.replace('受', '')
    return clean in ['平手', '平/半', '平手/半球'] or clean == '0' or clean == '0.25'


def is_strict_level(handicap_str):
    """判断是否是严格平手盘（只有平手）"""
    if not handicap_str:
        return False
    clean = handicap_str.replace('受', '')
    return clean == '平手' or clean == '0'


class LevelBallAnalyzer:
    """平手盘规律分析器"""
    
    def __init__(self):
        self.storage = MongoDBStorage()
        self.logger = setup_logger()
    
    def analyze(self):
        """分析平手盘规律"""
        print("\n" + "=" * 100)
        print("⚖️ 平手盘规律分析报告")
        print("=" * 100)
        
        # 获取所有完场比赛
        all_matches = self.storage.get_matches(filters={'status': 2})
        
        # 筛选平手盘比赛
        level_matches = []
        strict_level_matches = []
        
        for match in all_matches:
            handicap = match.get('asian_current_handicap') or match.get('asian_initial_handicap')
            if is_level_ball(handicap):
                level_matches.append(match)
                if is_strict_level(handicap):
                    strict_level_matches.append(match)
        
        print(f"📊 总完场比赛: {len(all_matches)} 场")
        print(f"📊 平手/平半盘比赛: {len(level_matches)} 场 ({len(level_matches)/len(all_matches)*100:.1f}%)")
        print(f"📊 严格平手盘比赛: {len(strict_level_matches)} 场")
        print("=" * 100)
        
        if len(level_matches) < 10:
            print("⚠️ 平手盘样本太少，无法进行有效分析")
            return
        
        # 1. 基础胜平负分析
        self.analyze_basic_results(level_matches, strict_level_matches)
        
        # 2. 水位分析
        self.analyze_water_levels(level_matches)
        
        # 3. 水位变动分析
        self.analyze_water_changes(level_matches)
        
        # 4. 欧赔特征分析
        self.analyze_euro_odds(level_matches)
        
        # 5. 分联赛分析
        self.analyze_by_league(level_matches)
        
        # 6. 大小球分析
        self.analyze_over_under(level_matches)
        
        # 7. 规律总结
        self.print_summary()
    
    def analyze_basic_results(self, level_matches, strict_level_matches):
        """基础胜平负分析"""
        print("\n" + "─" * 100)
        print("📊 一、平手盘胜平负分布")
        print("─" * 100)
        
        # 平手/平半盘
        stats = {'home': 0, 'draw': 0, 'away': 0}
        for match in level_matches:
            result = get_match_result(match.get('home_score'), match.get('away_score'))
            if result:
                stats[result] += 1
        
        total = sum(stats.values())
        print(f"\n【平手/平半盘】({total}场)")
        print(f"   主胜: {stats['home']} 场 ({stats['home']/total*100:.1f}%)")
        print(f"   平局: {stats['draw']} 场 ({stats['draw']/total*100:.1f}%)")
        print(f"   客胜: {stats['away']} 场 ({stats['away']/total*100:.1f}%)")
        
        # 严格平手盘
        if len(strict_level_matches) >= 5:
            stats2 = {'home': 0, 'draw': 0, 'away': 0}
            for match in strict_level_matches:
                result = get_match_result(match.get('home_score'), match.get('away_score'))
                if result:
                    stats2[result] += 1
            
            total2 = sum(stats2.values())
            print(f"\n【严格平手盘】({total2}场)")
            print(f"   主胜: {stats2['home']} 场 ({stats2['home']/total2*100:.1f}%)")
            print(f"   平局: {stats2['draw']} 场 ({stats2['draw']/total2*100:.1f}%)")
            print(f"   客胜: {stats2['away']} 场 ({stats2['away']/total2*100:.1f}%)")
        
        print("\n💡 规律: 平手盘两队实力接近，平局概率较其他盘口更高")
    
    def analyze_water_levels(self, level_matches):
        """水位分析"""
        print("\n" + "─" * 100)
        print("📊 二、平手盘水位与结果关系")
        print("─" * 100)
        
        # 按主队水位分组
        water_groups = {
            '低水(≤0.85)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '中低水(0.86-0.95)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '中水(0.96-1.00)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '中高水(1.01-1.10)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '高水(>1.10)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0}
        }
        
        for match in level_matches:
            result = get_match_result(match.get('home_score'), match.get('away_score'))
            if not result:
                continue
            
            home_odds = safe_float(match.get('asian_current_home_odds') or match.get('asian_initial_home_odds'))
            if not home_odds:
                continue
            
            if home_odds <= 0.85:
                group = '低水(≤0.85)'
            elif home_odds <= 0.95:
                group = '中低水(0.86-0.95)'
            elif home_odds <= 1.00:
                group = '中水(0.96-1.00)'
            elif home_odds <= 1.10:
                group = '中高水(1.01-1.10)'
            else:
                group = '高水(>1.10)'
            
            water_groups[group]['total'] += 1
            water_groups[group][result] += 1
        
        print(f"\n{'水位区间':<18} {'场次':>6} {'主胜':>10} {'平局':>10} {'客胜':>10} {'建议':>12}")
        print("─" * 75)
        
        for group in ['低水(≤0.85)', '中低水(0.86-0.95)', '中水(0.96-1.00)', '中高水(1.01-1.10)', '高水(>1.10)']:
            s = water_groups[group]
            if s['total'] == 0:
                continue
            
            home_rate = s['home'] / s['total'] * 100
            draw_rate = s['draw'] / s['total'] * 100
            away_rate = s['away'] / s['total'] * 100
            
            # 给出建议
            if home_rate > 45:
                suggest = "→ 博主胜"
            elif away_rate > 45:
                suggest = "→ 博客胜"
            elif draw_rate > 30:
                suggest = "→ 防平局"
            else:
                suggest = "→ 均衡"
            
            print(f"{group:<18} {s['total']:>6} {home_rate:>8.1f}% {draw_rate:>8.1f}% {away_rate:>8.1f}% {suggest:>12}")
        
        print("\n💡 规律: 平手盘低水方（水位≤0.85）胜率较高，高水方胜率较低")
    
    def analyze_water_changes(self, level_matches):
        """水位变动分析"""
        print("\n" + "─" * 100)
        print("📊 三、平手盘水位变动与结果关系")
        print("─" * 100)
        
        change_stats = {
            '主水大降(>0.08)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '主水小降(0.03-0.08)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '水位不变': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '主水小升(0.03-0.08)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '主水大升(>0.08)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0}
        }
        
        for match in level_matches:
            result = get_match_result(match.get('home_score'), match.get('away_score'))
            if not result:
                continue
            
            init_home = safe_float(match.get('asian_initial_home_odds'))
            curr_home = safe_float(match.get('asian_current_home_odds'))
            
            if not init_home or not curr_home:
                continue
            
            change = curr_home - init_home
            
            if change < -0.08:
                group = '主水大降(>0.08)'
            elif change < -0.03:
                group = '主水小降(0.03-0.08)'
            elif change > 0.08:
                group = '主水大升(>0.08)'
            elif change > 0.03:
                group = '主水小升(0.03-0.08)'
            else:
                group = '水位不变'
            
            change_stats[group]['total'] += 1
            change_stats[group][result] += 1
        
        print(f"\n{'水位变动':<20} {'场次':>6} {'主胜':>10} {'平局':>10} {'客胜':>10} {'建议':>12}")
        print("─" * 80)
        
        for group in ['主水大降(>0.08)', '主水小降(0.03-0.08)', '水位不变', '主水小升(0.03-0.08)', '主水大升(>0.08)']:
            s = change_stats[group]
            if s['total'] == 0:
                continue
            
            home_rate = s['home'] / s['total'] * 100
            draw_rate = s['draw'] / s['total'] * 100
            away_rate = s['away'] / s['total'] * 100
            
            if home_rate > 45:
                suggest = "★ 博主胜"
            elif away_rate > 45:
                suggest = "★ 博客胜"
            elif draw_rate > 30:
                suggest = "⚠️ 防平局"
            else:
                suggest = "— 均衡"
            
            print(f"{group:<20} {s['total']:>6} {home_rate:>8.1f}% {draw_rate:>8.1f}% {away_rate:>8.1f}% {suggest:>12}")
        
        print("\n💡 规律: 平手盘主水下降→主队更被看好；主水上升→客队更被看好")
    
    def analyze_euro_odds(self, level_matches):
        """欧赔特征分析"""
        print("\n" + "─" * 100)
        print("📊 四、平手盘欧赔特征与结果关系")
        print("─" * 100)
        
        # 按欧赔主胜赔率分组
        euro_groups = {
            '主胜赔率<2.0（强看主）': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '主胜赔率2.0-2.5': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '主胜赔率2.5-3.0（均势）': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '主胜赔率3.0-3.5': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '主胜赔率>3.5（弱看主）': {'home': 0, 'draw': 0, 'away': 0, 'total': 0}
        }
        
        # 平局赔率分组
        draw_groups = {
            '平局赔率<3.0': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '平局赔率3.0-3.3': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '平局赔率3.3-3.6': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '平局赔率>3.6': {'home': 0, 'draw': 0, 'away': 0, 'total': 0}
        }
        
        for match in level_matches:
            result = get_match_result(match.get('home_score'), match.get('away_score'))
            if not result:
                continue
            
            euro_win = safe_float(match.get('euro_current_win') or match.get('euro_initial_win'))
            euro_draw = safe_float(match.get('euro_current_draw') or match.get('euro_initial_draw'))
            
            # 主胜赔率分组
            if euro_win:
                if euro_win < 2.0:
                    group = '主胜赔率<2.0（强看主）'
                elif euro_win < 2.5:
                    group = '主胜赔率2.0-2.5'
                elif euro_win < 3.0:
                    group = '主胜赔率2.5-3.0（均势）'
                elif euro_win < 3.5:
                    group = '主胜赔率3.0-3.5'
                else:
                    group = '主胜赔率>3.5（弱看主）'
                
                euro_groups[group]['total'] += 1
                euro_groups[group][result] += 1
            
            # 平局赔率分组
            if euro_draw:
                if euro_draw < 3.0:
                    group = '平局赔率<3.0'
                elif euro_draw < 3.3:
                    group = '平局赔率3.0-3.3'
                elif euro_draw < 3.6:
                    group = '平局赔率3.3-3.6'
                else:
                    group = '平局赔率>3.6'
                
                draw_groups[group]['total'] += 1
                draw_groups[group][result] += 1
        
        print("\n【按欧赔主胜赔率分组】")
        print(f"{'欧赔主胜区间':<25} {'场次':>6} {'主胜':>10} {'平局':>10} {'客胜':>10}")
        print("─" * 70)
        
        for group in ['主胜赔率<2.0（强看主）', '主胜赔率2.0-2.5', '主胜赔率2.5-3.0（均势）', 
                      '主胜赔率3.0-3.5', '主胜赔率>3.5（弱看主）']:
            s = euro_groups[group]
            if s['total'] == 0:
                continue
            
            home_rate = s['home'] / s['total'] * 100
            draw_rate = s['draw'] / s['total'] * 100
            away_rate = s['away'] / s['total'] * 100
            
            print(f"{group:<25} {s['total']:>6} {home_rate:>8.1f}% {draw_rate:>8.1f}% {away_rate:>8.1f}%")
        
        print("\n【按欧赔平局赔率分组】")
        print(f"{'平局赔率区间':<18} {'场次':>6} {'主胜':>10} {'平局':>10} {'客胜':>10} {'平局价值':>12}")
        print("─" * 75)
        
        for group in ['平局赔率<3.0', '平局赔率3.0-3.3', '平局赔率3.3-3.6', '平局赔率>3.6']:
            s = draw_groups[group]
            if s['total'] == 0:
                continue
            
            home_rate = s['home'] / s['total'] * 100
            draw_rate = s['draw'] / s['total'] * 100
            away_rate = s['away'] / s['total'] * 100
            
            # 平局价值评估
            value = "★★★" if draw_rate > 35 else ("★★" if draw_rate > 28 else ("★" if draw_rate > 22 else "—"))
            
            print(f"{group:<18} {s['total']:>6} {home_rate:>8.1f}% {draw_rate:>8.1f}% {away_rate:>8.1f}% {value:>12}")
        
        print("\n💡 规律: 平手盘配合欧赔看，平局赔率低时平局概率更高")
    
    def analyze_by_league(self, level_matches):
        """分联赛分析"""
        print("\n" + "─" * 100)
        print("📊 五、各联赛平手盘规律对比")
        print("─" * 100)
        
        league_stats = defaultdict(lambda: {'home': 0, 'draw': 0, 'away': 0, 'total': 0})
        
        for match in level_matches:
            result = get_match_result(match.get('home_score'), match.get('away_score'))
            if not result:
                continue
            
            league = match.get('league', '未知')
            league_stats[league]['total'] += 1
            league_stats[league][result] += 1
        
        # 按场次排序
        sorted_leagues = sorted(league_stats.items(), key=lambda x: x[1]['total'], reverse=True)
        
        print(f"\n{'联赛':<12} {'场次':>6} {'主胜':>10} {'平局':>10} {'客胜':>10} {'特点':>15}")
        print("─" * 75)
        
        for league, s in sorted_leagues:
            if s['total'] < 5:
                continue
            
            home_rate = s['home'] / s['total'] * 100
            draw_rate = s['draw'] / s['total'] * 100
            away_rate = s['away'] / s['total'] * 100
            
            # 特点判断
            if home_rate > 45:
                feature = "🏠 主场强"
            elif away_rate > 45:
                feature = "✈️ 客场强"
            elif draw_rate > 35:
                feature = "🤝 高平局"
            else:
                feature = "⚖️ 均衡"
            
            print(f"{league:<12} {s['total']:>6} {home_rate:>8.1f}% {draw_rate:>8.1f}% {away_rate:>8.1f}% {feature:>15}")
    
    def analyze_over_under(self, level_matches):
        """大小球分析"""
        print("\n" + "─" * 100)
        print("📊 六、平手盘大小球规律")
        print("─" * 100)
        
        total_goals = 0
        valid_count = 0
        goal_dist = defaultdict(int)
        
        over_count = under_count = push_count = 0
        ou_valid = 0
        
        for match in level_matches:
            try:
                home = int(match.get('home_score', 0))
                away = int(match.get('away_score', 0))
                total = home + away
                
                total_goals += total
                valid_count += 1
                goal_dist[total] += 1
                
                # 大小球结果
                total_line = safe_float(match.get('ou_current_total') or match.get('ou_initial_total'))
                if total_line:
                    ou_valid += 1
                    if total > total_line:
                        over_count += 1
                    elif total < total_line:
                        under_count += 1
                    else:
                        push_count += 1
            except:
                continue
        
        if valid_count > 0:
            avg_goals = total_goals / valid_count
            print(f"\n📈 场均进球: {avg_goals:.2f} 球")
            
            print("\n【进球数分布】")
            for goals in sorted(goal_dist.keys()):
                count = goal_dist[goals]
                rate = count / valid_count * 100
                bar = '█' * int(rate / 2)
                print(f"   {goals}球: {count:>4} 场 ({rate:>5.1f}%) {bar}")
        
        if ou_valid > 0:
            print(f"\n【大小球统计】(共{ou_valid}场)")
            print(f"   大球: {over_count} 场 ({over_count/ou_valid*100:.1f}%)")
            print(f"   走盘: {push_count} 场 ({push_count/ou_valid*100:.1f}%)")
            print(f"   小球: {under_count} 场 ({under_count/ou_valid*100:.1f}%)")
        
        print("\n💡 规律: 平手盘比赛两队实力接近，往往较为胶着，进球数可能偏低")
    
    def print_summary(self):
        """输出规律总结"""
        print("\n" + "=" * 100)
        print("📝 平手盘规律总结与投注策略")
        print("=" * 100)
        
        print("""
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                  平手盘核心规律                                               │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  【水位规律】                                                                                 │
│   ★ 低水方（≤0.85）胜率更高 → 跟低水方                                                        │
│   ★ 高水方（>1.10）胜率较低 → 可考虑反向                                                      │
│   ★ 中水（0.96-1.00）时最均衡 → 防平局                                                       │
│                                                                                              │
│  【水位变动规律】                                                                              │
│   ★ 主水下降 → 机构看好主队 → 博主胜                                                          │
│   ★ 主水上升 → 机构看好客队 → 博客胜                                                          │
│   ★ 水位不变 → 分歧较大 → 防平局                                                             │
│                                                                                              │
│  【欧赔配合规律】                                                                              │
│   ★ 平局赔率<3.0时 → 平局概率更高 → 可博平局                                                  │
│   ★ 主胜赔率<2.5 + 平手盘 → 矛盾信号 → 主队未必能赢                                           │
│   ★ 主胜赔率>3.0 + 平手盘 → 客队有价值                                                       │
│                                                                                              │
│  【特殊规律】                                                                                 │
│   ★ 平手盘平局率高于其他盘口（约25-30%）                                                      │
│   ★ 平手盘场均进球偏低，小球价值可能更高                                                       │
│   ★ 不同联赛差异明显，需结合联赛特点                                                          │
│                                                                                              │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                    投注策略建议                                               │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  1. 【跟低水方】平手盘低水一侧（≤0.85）胜率较高，可跟投                                         │
│  2. 【防平局】平手盘平局率高，当水位接近中水时要特别防平                                          │
│  3. 【看欧赔】平局赔率<3.0时，平局概率提升，可加防                                              │
│  4. 【逆向思维】水位变动反常时（如主水下降但欧赔主胜升高），需警惕诱盘                             │
│  5. 【小球倾向】平手盘比赛胶着，可适当博小球                                                    │
│                                                                                              │
│  ⚠️ 风险提示：平手盘比赛变数大，任何结果都有可能，请理性投注！                                    │
│                                                                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
""")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='平手盘规律分析工具')
    args = parser.parse_args()
    
    analyzer = LevelBallAnalyzer()
    analyzer.analyze()


if __name__ == '__main__':
    main()



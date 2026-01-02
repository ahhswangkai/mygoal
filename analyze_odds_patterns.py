"""
赔率变动与比赛结果规律分析工具
分析欧赔、亚盘、让球数变动与比赛结果之间的规律
"""
from db_storage import MongoDBStorage
from utils import setup_logger
from collections import defaultdict
import argparse
import re


def parse_handicap(handicap_str):
    """
    解析中文盘口为数字
    平手 -> 0, 平/半 -> 0.25, 半球 -> 0.5, 半/一 -> 0.75, 一球 -> 1.0
    受... -> 负值
    """
    if not handicap_str:
        return None
    
    is_receiver = '受' in handicap_str
    clean_str = handicap_str.replace('受', '')
    
    handicap_map = {
        '平手': 0, '平/半': 0.25, '平手/半球': 0.25,
        '半球': 0.5, '半/一': 0.75, '半球/一球': 0.75,
        '一球': 1.0, '一/球半': 1.25, '一球/球半': 1.25,
        '球半': 1.5, '球半/两': 1.75, '球半/两球': 1.75,
        '两球': 2.0, '两/两球半': 2.25, '两球半': 2.5,
        '两球半/三': 2.75, '三球': 3.0
    }
    
    if clean_str in handicap_map:
        value = handicap_map[clean_str]
    else:
        # 尝试直接解析数字
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
    """获取比赛结果: home=主胜, draw=平, away=客胜"""
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


def get_asian_result(home_score, away_score, handicap_str):
    """
    获取亚盘结果: home=让方赢盘, away=受让方赢盘, push=走盘
    盘口是主队让球数，正数为主让，负数为主受让
    """
    try:
        home = int(home_score)
        away = int(away_score)
        handicap = parse_handicap(handicap_str)
        
        if handicap is None:
            return None
        
        # 主队让球后的净胜球
        adjusted_diff = home - away + handicap  # 注意：让球是加在主队上的
        
        # 如果是主让（正数盘口），handicap是正数，实际是主队让球
        # 这里需要修正逻辑
        if '受' in str(handicap_str):
            # 主受让：主队加上让球数
            adjusted_diff = home + abs(handicap) - away
        else:
            # 主让：主队减去让球数
            adjusted_diff = home - handicap - away
        
        if adjusted_diff > 0:
            return 'home'
        elif adjusted_diff < 0:
            return 'away'
        else:
            return 'push'
    except:
        return None


def get_ou_result(home_score, away_score, total_line):
    """获取大小球结果: over=大球, under=小球, push=走盘"""
    try:
        home = int(home_score)
        away = int(away_score)
        total = float(total_line)
        actual_total = home + away
        
        if actual_total > total:
            return 'over'
        elif actual_total < total:
            return 'under'
        else:
            return 'push'
    except:
        return None


class OddsPatternAnalyzer:
    """赔率变动规律分析器"""
    
    def __init__(self):
        self.storage = MongoDBStorage()
        self.logger = setup_logger()
    
    def analyze_all_patterns(self, league=None, min_matches=10):
        """
        分析所有赔率变动规律
        
        Args:
            league: 联赛筛选（可选）
            min_matches: 最小样本数量
        """
        print("\n" + "=" * 100)
        print("🔍 赔率变动与比赛结果规律分析报告")
        print("=" * 100)
        
        # 获取完场比赛
        filters = {'status': 2}
        if league:
            filters['league'] = league
            print(f"📋 联赛筛选: {league}")
        
        matches = self.storage.get_matches(filters=filters)
        print(f"📊 分析样本: {len(matches)} 场完场比赛")
        print("=" * 100)
        
        if len(matches) < min_matches:
            print(f"⚠️ 样本数量不足 {min_matches} 场，分析结果可能不具统计意义")
        
        # 1. 亚盘变动标签与胜负结果
        self.analyze_asian_movement_patterns(matches)
        
        # 2. 欧赔变动与胜负结果
        self.analyze_euro_movement_patterns(matches)
        
        # 3. 盘口大小与胜负结果
        self.analyze_handicap_size_patterns(matches)
        
        # 4. 水位变动与胜负结果
        self.analyze_water_change_patterns(matches)
        
        # 5. 大小球规律
        self.analyze_ou_patterns(matches)
        
        # 6. 让球指数规律
        self.analyze_handicap_index_patterns(matches)
        
        # 7. 综合规律总结
        self.print_summary()
    
    def analyze_asian_movement_patterns(self, matches):
        """分析亚盘变动标签与比赛结果的规律"""
        print("\n" + "─" * 100)
        print("📊 一、亚盘变动标签与比赛结果")
        print("─" * 100)
        
        # 按变动标签分组统计
        label_stats = defaultdict(lambda: {'home': 0, 'draw': 0, 'away': 0, 'total': 0,
                                           'asian_home': 0, 'asian_away': 0, 'asian_push': 0})
        
        for match in matches:
            result = get_match_result(match.get('home_score'), match.get('away_score'))
            if not result:
                continue
            
            label = match.get('asian_movement_label') or '无标签'
            label_stats[label]['total'] += 1
            label_stats[label][result] += 1
            
            # 亚盘结果
            asian_handicap = match.get('asian_current_handicap') or match.get('asian_initial_handicap')
            asian_result = get_asian_result(match.get('home_score'), match.get('away_score'), asian_handicap)
            if asian_result:
                label_stats[label][f'asian_{asian_result}'] += 1
        
        # 输出表格
        print(f"\n{'变动类型':<12} {'场次':>6} {'主胜':>8} {'平局':>8} {'客胜':>8} │ {'让胜':>8} {'走盘':>6} {'让负':>8}")
        print("─" * 90)
        
        sorted_labels = ['升盘降水', '升盘升水', '升盘', '降盘降水', '降盘升水', '降盘', '升水', '降水', '无变化', '无标签']
        
        for label in sorted_labels:
            if label not in label_stats:
                continue
            s = label_stats[label]
            if s['total'] == 0:
                continue
            
            home_rate = s['home'] / s['total'] * 100
            draw_rate = s['draw'] / s['total'] * 100
            away_rate = s['away'] / s['total'] * 100
            
            asian_total = s['asian_home'] + s['asian_away'] + s['asian_push']
            asian_home_rate = s['asian_home'] / asian_total * 100 if asian_total > 0 else 0
            asian_push_rate = s['asian_push'] / asian_total * 100 if asian_total > 0 else 0
            asian_away_rate = s['asian_away'] / asian_total * 100 if asian_total > 0 else 0
            
            print(f"{label:<12} {s['total']:>6} {home_rate:>6.1f}% {draw_rate:>6.1f}% {away_rate:>6.1f}% │ {asian_home_rate:>6.1f}% {asian_push_rate:>4.1f}% {asian_away_rate:>6.1f}%")
        
        # 规律解读
        print("\n💡 规律解读:")
        if '升盘降水' in label_stats and label_stats['升盘降水']['total'] >= 5:
            s = label_stats['升盘降水']
            home_rate = s['home'] / s['total'] * 100
            print(f"   • 升盘降水: 主胜率 {home_rate:.1f}% - 机构强看主队，但需结合实力差距")
        
        if '降盘升水' in label_stats and label_stats['降盘升水']['total'] >= 5:
            s = label_stats['降盘升水']
            away_rate = s['away'] / s['total'] * 100
            print(f"   • 降盘升水: 客胜率 {away_rate:.1f}% - 可能诱盘，需谨慎判断")
    
    def analyze_euro_movement_patterns(self, matches):
        """分析欧赔变动与比赛结果的规律"""
        print("\n" + "─" * 100)
        print("📊 二、欧赔变动与比赛结果")
        print("─" * 100)
        
        # 按欧赔变动分组统计
        euro_movement_stats = {
            '主胜赔率大降(>0.15)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '主胜赔率小降(0.05-0.15)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '主胜赔率基本不变': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '主胜赔率小升(0.05-0.15)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '主胜赔率大升(>0.15)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '平局赔率大降(>0.15)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0},
            '客胜赔率大降(>0.15)': {'home': 0, 'draw': 0, 'away': 0, 'total': 0}
        }
        
        for match in matches:
            result = get_match_result(match.get('home_score'), match.get('away_score'))
            if not result:
                continue
            
            euro_init_win = safe_float(match.get('euro_initial_win'))
            euro_curr_win = safe_float(match.get('euro_current_win'))
            euro_init_draw = safe_float(match.get('euro_initial_draw'))
            euro_curr_draw = safe_float(match.get('euro_current_draw'))
            euro_init_lose = safe_float(match.get('euro_initial_lose'))
            euro_curr_lose = safe_float(match.get('euro_current_lose'))
            
            if not all([euro_init_win, euro_curr_win]):
                continue
            
            win_change = euro_curr_win - euro_init_win
            
            # 主胜赔率变动
            if win_change < -0.15:
                key = '主胜赔率大降(>0.15)'
            elif win_change < -0.05:
                key = '主胜赔率小降(0.05-0.15)'
            elif win_change > 0.15:
                key = '主胜赔率大升(>0.15)'
            elif win_change > 0.05:
                key = '主胜赔率小升(0.05-0.15)'
            else:
                key = '主胜赔率基本不变'
            
            euro_movement_stats[key]['total'] += 1
            euro_movement_stats[key][result] += 1
            
            # 平局赔率变动
            if euro_init_draw and euro_curr_draw:
                draw_change = euro_curr_draw - euro_init_draw
                if draw_change < -0.15:
                    euro_movement_stats['平局赔率大降(>0.15)']['total'] += 1
                    euro_movement_stats['平局赔率大降(>0.15)'][result] += 1
            
            # 客胜赔率变动
            if euro_init_lose and euro_curr_lose:
                lose_change = euro_curr_lose - euro_init_lose
                if lose_change < -0.15:
                    euro_movement_stats['客胜赔率大降(>0.15)']['total'] += 1
                    euro_movement_stats['客胜赔率大降(>0.15)'][result] += 1
        
        print(f"\n{'欧赔变动类型':<25} {'场次':>6} {'主胜':>8} {'平局':>8} {'客胜':>8}")
        print("─" * 65)
        
        for key in ['主胜赔率大降(>0.15)', '主胜赔率小降(0.05-0.15)', '主胜赔率基本不变',
                    '主胜赔率小升(0.05-0.15)', '主胜赔率大升(>0.15)', '平局赔率大降(>0.15)', '客胜赔率大降(>0.15)']:
            s = euro_movement_stats[key]
            if s['total'] == 0:
                continue
            
            home_rate = s['home'] / s['total'] * 100
            draw_rate = s['draw'] / s['total'] * 100
            away_rate = s['away'] / s['total'] * 100
            
            print(f"{key:<25} {s['total']:>6} {home_rate:>6.1f}% {draw_rate:>6.1f}% {away_rate:>6.1f}%")
        
        print("\n💡 规律解读:")
        print("   • 主胜赔率大降 → 机构倾向主胜，主胜概率通常较高")
        print("   • 客胜赔率大降 → 机构倾向客胜，客胜概率通常较高")
        print("   • 平局赔率大降 → 机构倾向平局，但平局本身概率较低，需谨慎")
    
    def analyze_handicap_size_patterns(self, matches):
        """分析盘口大小与比赛结果的规律"""
        print("\n" + "─" * 100)
        print("📊 三、亚盘盘口大小与比赛结果")
        print("─" * 100)
        
        # 按盘口大小分组
        handicap_stats = defaultdict(lambda: {'home': 0, 'draw': 0, 'away': 0, 'total': 0,
                                               'asian_home': 0, 'asian_away': 0, 'asian_push': 0})
        
        for match in matches:
            result = get_match_result(match.get('home_score'), match.get('away_score'))
            if not result:
                continue
            
            handicap_str = match.get('asian_current_handicap') or match.get('asian_initial_handicap')
            handicap = parse_handicap(handicap_str)
            
            if handicap is None:
                continue
            
            # 分组
            if handicap >= 1.5:
                group = '主让球半及以上'
            elif handicap >= 1.0:
                group = '主让一球到球半'
            elif handicap >= 0.5:
                group = '主让半球到一球'
            elif handicap >= 0.25:
                group = '主让平半到半球'
            elif handicap >= 0:
                group = '平手到平半'
            elif handicap >= -0.5:
                group = '主受让半球内'
            else:
                group = '主受让半球以上'
            
            handicap_stats[group]['total'] += 1
            handicap_stats[group][result] += 1
            
            # 亚盘结果
            asian_result = get_asian_result(match.get('home_score'), match.get('away_score'), handicap_str)
            if asian_result:
                handicap_stats[group][f'asian_{asian_result}'] += 1
        
        print(f"\n{'盘口范围':<18} {'场次':>6} {'主胜':>8} {'平局':>8} {'客胜':>8} │ {'让胜':>8} {'走盘':>6} {'让负':>8}")
        print("─" * 95)
        
        order = ['主让球半及以上', '主让一球到球半', '主让半球到一球', '主让平半到半球', 
                 '平手到平半', '主受让半球内', '主受让半球以上']
        
        for group in order:
            if group not in handicap_stats:
                continue
            s = handicap_stats[group]
            if s['total'] == 0:
                continue
            
            home_rate = s['home'] / s['total'] * 100
            draw_rate = s['draw'] / s['total'] * 100
            away_rate = s['away'] / s['total'] * 100
            
            asian_total = s['asian_home'] + s['asian_away'] + s['asian_push']
            asian_home_rate = s['asian_home'] / asian_total * 100 if asian_total > 0 else 0
            asian_push_rate = s['asian_push'] / asian_total * 100 if asian_total > 0 else 0
            asian_away_rate = s['asian_away'] / asian_total * 100 if asian_total > 0 else 0
            
            print(f"{group:<18} {s['total']:>6} {home_rate:>6.1f}% {draw_rate:>6.1f}% {away_rate:>6.1f}% │ {asian_home_rate:>6.1f}% {asian_push_rate:>4.1f}% {asian_away_rate:>6.1f}%")
        
        print("\n💡 规律解读:")
        print("   • 深盘（让球半以上）让方赢盘概率偏低，深盘博受让方价值较高")
        print("   • 浅盘（平手到半球）比赛结果更难预测，需结合其他因素判断")
    
    def analyze_water_change_patterns(self, matches):
        """分析水位变动与比赛结果的规律"""
        print("\n" + "─" * 100)
        print("📊 四、亚盘水位变动与比赛结果")
        print("─" * 100)
        
        # 按水位变动分组
        water_stats = defaultdict(lambda: {'home': 0, 'draw': 0, 'away': 0, 'total': 0,
                                           'asian_home': 0, 'asian_away': 0, 'asian_push': 0})
        
        for match in matches:
            result = get_match_result(match.get('home_score'), match.get('away_score'))
            if not result:
                continue
            
            home_init = safe_float(match.get('asian_initial_home_odds'))
            home_curr = safe_float(match.get('asian_current_home_odds'))
            
            if not all([home_init, home_curr]):
                continue
            
            water_change = home_curr - home_init
            
            # 分组
            if water_change < -0.08:
                group = '主水大降(>0.08)'
            elif water_change < -0.03:
                group = '主水小降(0.03-0.08)'
            elif water_change > 0.08:
                group = '主水大升(>0.08)'
            elif water_change > 0.03:
                group = '主水小升(0.03-0.08)'
            else:
                group = '水位基本不变'
            
            water_stats[group]['total'] += 1
            water_stats[group][result] += 1
            
            # 亚盘结果
            handicap_str = match.get('asian_current_handicap') or match.get('asian_initial_handicap')
            asian_result = get_asian_result(match.get('home_score'), match.get('away_score'), handicap_str)
            if asian_result:
                water_stats[group][f'asian_{asian_result}'] += 1
        
        print(f"\n{'水位变动':<18} {'场次':>6} {'主胜':>8} {'平局':>8} {'客胜':>8} │ {'让胜':>8} {'走盘':>6} {'让负':>8}")
        print("─" * 95)
        
        order = ['主水大降(>0.08)', '主水小降(0.03-0.08)', '水位基本不变', 
                 '主水小升(0.03-0.08)', '主水大升(>0.08)']
        
        for group in order:
            if group not in water_stats:
                continue
            s = water_stats[group]
            if s['total'] == 0:
                continue
            
            home_rate = s['home'] / s['total'] * 100
            draw_rate = s['draw'] / s['total'] * 100
            away_rate = s['away'] / s['total'] * 100
            
            asian_total = s['asian_home'] + s['asian_away'] + s['asian_push']
            asian_home_rate = s['asian_home'] / asian_total * 100 if asian_total > 0 else 0
            asian_push_rate = s['asian_push'] / asian_total * 100 if asian_total > 0 else 0
            asian_away_rate = s['asian_away'] / asian_total * 100 if asian_total > 0 else 0
            
            print(f"{group:<18} {s['total']:>6} {home_rate:>6.1f}% {draw_rate:>6.1f}% {away_rate:>6.1f}% │ {asian_home_rate:>6.1f}% {asian_push_rate:>4.1f}% {asian_away_rate:>6.1f}%")
        
        print("\n💡 规律解读:")
        print("   • 主水下降 → 机构倾向主队赢盘，但需结合盘口变化判断是否诱盘")
        print("   • 主水上升 → 机构倾向客队赢盘，或在分散注码")
    
    def analyze_ou_patterns(self, matches):
        """分析大小球规律"""
        print("\n" + "─" * 100)
        print("📊 五、大小球规律分析")
        print("─" * 100)
        
        # 按盘口分组
        ou_stats = defaultdict(lambda: {'over': 0, 'under': 0, 'push': 0, 'total': 0})
        
        # 按进球数统计
        goal_stats = defaultdict(int)
        
        for match in matches:
            try:
                home = int(match.get('home_score', 0))
                away = int(match.get('away_score', 0))
            except:
                continue
            
            total_goals = home + away
            goal_stats[total_goals] += 1
            
            total_line = safe_float(match.get('ou_current_total') or match.get('ou_initial_total'))
            if not total_line:
                continue
            
            ou_result = get_ou_result(match.get('home_score'), match.get('away_score'), total_line)
            if not ou_result:
                continue
            
            # 按盘口分组
            if total_line <= 1.75:
                group = '小于2球'
            elif total_line <= 2.25:
                group = '2-2.25球'
            elif total_line <= 2.75:
                group = '2.5-2.75球'
            elif total_line <= 3.25:
                group = '3-3.25球'
            else:
                group = '3.5球以上'
            
            ou_stats[group]['total'] += 1
            ou_stats[group][ou_result] += 1
        
        # 进球数分布
        print("\n📈 进球数分布:")
        total_matches = sum(goal_stats.values())
        for goals in sorted(goal_stats.keys()):
            count = goal_stats[goals]
            rate = count / total_matches * 100
            bar = '█' * int(rate / 2)
            print(f"   {goals}球: {count:>4} 场 ({rate:>5.1f}%) {bar}")
        
        avg_goals = sum(k * v for k, v in goal_stats.items()) / total_matches if total_matches > 0 else 0
        print(f"\n   场均进球: {avg_goals:.2f} 球")
        
        # 大小球统计
        print(f"\n{'盘口范围':<12} {'场次':>6} {'大球':>10} {'走盘':>8} {'小球':>10}")
        print("─" * 55)
        
        order = ['小于2球', '2-2.25球', '2.5-2.75球', '3-3.25球', '3.5球以上']
        
        for group in order:
            if group not in ou_stats:
                continue
            s = ou_stats[group]
            if s['total'] == 0:
                continue
            
            over_rate = s['over'] / s['total'] * 100
            push_rate = s['push'] / s['total'] * 100
            under_rate = s['under'] / s['total'] * 100
            
            print(f"{group:<12} {s['total']:>6} {over_rate:>8.1f}% {push_rate:>6.1f}% {under_rate:>8.1f}%")
        
        print("\n💡 规律解读:")
        print("   • 低盘口（2球以下）大球率通常较高")
        print("   • 高盘口（3球以上）小球率通常较高")
        print("   • 2.5球盘口是分水岭，大小球概率相对均衡")
    
    def analyze_handicap_index_patterns(self, matches):
        """分析让球指数规律"""
        print("\n" + "─" * 100)
        print("📊 六、让球指数规律分析")
        print("─" * 100)
        
        # 按让球数分组统计
        hi_stats = defaultdict(lambda: {'home': 0, 'draw': 0, 'away': 0, 'total': 0,
                                        'hi_home': 0, 'hi_draw': 0, 'hi_away': 0})
        
        for match in matches:
            result = get_match_result(match.get('home_score'), match.get('away_score'))
            if not result:
                continue
            
            hi_handicap = match.get('hi_handicap_value')
            if not hi_handicap:
                continue
            
            # 解析让球指数
            handicap = parse_handicap(hi_handicap)
            if handicap is None:
                continue
            
            # 分组
            if handicap >= 2:
                group = '让两球及以上'
            elif handicap >= 1:
                group = '让一球到两球'
            elif handicap > 0:
                group = '让半球到一球'
            elif handicap == 0:
                group = '平手'
            elif handicap >= -1:
                group = '受让半球到一球'
            else:
                group = '受让一球以上'
            
            hi_stats[group]['total'] += 1
            hi_stats[group][result] += 1
            
            # 计算让球指数结果（需要实际比分 + 让球数）
            try:
                home = int(match.get('home_score', 0))
                away = int(match.get('away_score', 0))
                
                # 让球后的结果
                if '受' in str(hi_handicap):
                    adjusted_diff = home + abs(handicap) - away
                else:
                    adjusted_diff = home - handicap - away
                
                if adjusted_diff > 0:
                    hi_stats[group]['hi_home'] += 1
                elif adjusted_diff < 0:
                    hi_stats[group]['hi_away'] += 1
                else:
                    hi_stats[group]['hi_draw'] += 1
            except:
                pass
        
        if not hi_stats:
            print("\n⚠️ 没有让球指数数据")
            return
        
        print(f"\n{'让球指数':<18} {'场次':>6} {'主胜':>8} {'平局':>8} {'客胜':>8} │ {'让胜':>8} {'让平':>6} {'让负':>8}")
        print("─" * 95)
        
        order = ['让两球及以上', '让一球到两球', '让半球到一球', '平手', '受让半球到一球', '受让一球以上']
        
        for group in order:
            if group not in hi_stats:
                continue
            s = hi_stats[group]
            if s['total'] == 0:
                continue
            
            home_rate = s['home'] / s['total'] * 100
            draw_rate = s['draw'] / s['total'] * 100
            away_rate = s['away'] / s['total'] * 100
            
            hi_total = s['hi_home'] + s['hi_draw'] + s['hi_away']
            hi_home_rate = s['hi_home'] / hi_total * 100 if hi_total > 0 else 0
            hi_draw_rate = s['hi_draw'] / hi_total * 100 if hi_total > 0 else 0
            hi_away_rate = s['hi_away'] / hi_total * 100 if hi_total > 0 else 0
            
            print(f"{group:<18} {s['total']:>6} {home_rate:>6.1f}% {draw_rate:>6.1f}% {away_rate:>6.1f}% │ {hi_home_rate:>6.1f}% {hi_draw_rate:>4.1f}% {hi_away_rate:>6.1f}%")
    
    def print_summary(self):
        """输出规律总结"""
        print("\n" + "=" * 100)
        print("📝 规律总结与投注建议")
        print("=" * 100)
        
        print("""
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                  核心规律总结                                                  │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  【亚盘变动规律】                                                                              │
│   ★ 升盘降水 = 机构强看主队 → 主胜/让胜概率较高                                                  │
│   ★ 降盘降水 = 机构强看客队 → 客胜/让负概率较高                                                  │
│   ⚠️ 升盘升水 = 可能诱盘 → 需警惕，反向操作可能更佳                                              │
│   ⚠️ 降盘升水 = 可能诱盘 → 需警惕，反向操作可能更佳                                              │
│                                                                                              │
│  【欧赔变动规律】                                                                              │
│   ★ 主胜赔率大降（>0.15）→ 机构倾向主胜                                                        │
│   ★ 客胜赔率大降（>0.15）→ 机构倾向客胜                                                        │
│   ★ 平局赔率大降（>0.15）→ 机构倾向平局（但平局本身概率低，需谨慎）                               │
│                                                                                              │
│  【盘口大小规律】                                                                              │
│   ★ 深盘（让球半以上）→ 让方赢盘难度大，受让方价值较高                                           │
│   ★ 浅盘（平手到半球）→ 结果更难预测，需结合其他因素                                             │
│                                                                                              │
│  【大小球规律】                                                                               │
│   ★ 低盘口（2球以下）→ 大球率较高                                                             │
│   ★ 高盘口（3球以上）→ 小球率较高                                                             │
│   ★ 2.5球是分水岭 → 大小球概率相对均衡                                                        │
│                                                                                              │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                   投注策略建议                                                 │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  1. 优先关注「升盘降水」和「降盘降水」的比赛，这两种变动最能反映机构真实意图                         │
│  2. 遇到「升盘升水」或「降盘升水」要警惕诱盘，可考虑反向操作                                       │
│  3. 深盘比赛（让球半以上）博受让方，浅盘比赛结合欧赔判断                                          │
│  4. 大小球关注盘口与场均进球的偏离度，偏离大时有机会                                              │
│  5. 结合多家公司赔率对比，避免被单一公司误导                                                     │
│                                                                                              │
│  ⚠️ 风险提示：以上规律基于历史数据统计，仅供参考，不构成投注建议。请理性投注！                      │
│                                                                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
""")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='赔率变动与比赛结果规律分析工具')
    parser.add_argument('--league', type=str, help='联赛名称筛选，如：西甲')
    parser.add_argument('--min-matches', type=int, default=10, help='最小样本数量，默认10')
    
    args = parser.parse_args()
    
    analyzer = OddsPatternAnalyzer()
    analyzer.analyze_all_patterns(
        league=args.league,
        min_matches=args.min_matches
    )


if __name__ == '__main__':
    main()



"""
分联赛赔率变动与比赛结果规律分析工具
分析不同联赛的欧赔、亚盘、让球数变动与比赛结果之间的规律差异
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
        '两球': 2.0, '两/两球半': 2.25, '两球半': 2.5,
        '两球半/三': 2.75, '三球': 3.0
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


def get_asian_result(home_score, away_score, handicap_str):
    """获取亚盘结果"""
    try:
        home = int(home_score)
        away = int(away_score)
        handicap = parse_handicap(handicap_str)
        
        if handicap is None:
            return None
        
        if '受' in str(handicap_str):
            adjusted_diff = home + abs(handicap) - away
        else:
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
    """获取大小球结果"""
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


class LeaguePatternAnalyzer:
    """分联赛规律分析器"""
    
    def __init__(self):
        self.storage = MongoDBStorage()
        self.logger = setup_logger()
        self.league_stats = {}
    
    def analyze_all_leagues(self, min_matches=30, top_n=20):
        """
        分析所有联赛的规律
        
        Args:
            min_matches: 最小样本数量（少于这个数的联赛不分析）
            top_n: 只分析比赛数最多的前N个联赛
        """
        print("\n" + "=" * 120)
        print("🏆 分联赛赔率变动与比赛结果规律分析报告")
        print("=" * 120)
        
        # 获取所有完场比赛
        all_matches = self.storage.get_matches(filters={'status': 2})
        print(f"📊 总完场比赛: {len(all_matches)} 场")
        
        # 按联赛分组
        league_matches = defaultdict(list)
        for match in all_matches:
            league = match.get('league')
            if league:
                league_matches[league].append(match)
        
        # 按比赛数排序，取前N个
        sorted_leagues = sorted(league_matches.items(), key=lambda x: len(x[1]), reverse=True)
        top_leagues = [(league, matches) for league, matches in sorted_leagues 
                       if len(matches) >= min_matches][:top_n]
        
        print(f"📋 符合条件的联赛: {len(top_leagues)} 个（比赛数≥{min_matches}场）")
        print("=" * 120)
        
        # 分析每个联赛
        for league, matches in top_leagues:
            self.analyze_single_league(league, matches)
        
        # 输出联赛对比总结
        self.print_league_comparison(top_leagues)
        
        # 输出投注策略
        self.print_betting_strategies()
    
    def analyze_single_league(self, league, matches):
        """分析单个联赛"""
        stats = {
            'name': league,
            'total': len(matches),
            'home_win': 0,
            'draw': 0,
            'away_win': 0,
            'avg_goals': 0,
            'over_rate': 0,
            'under_rate': 0,
            # 亚盘变动
            'water_down_home_rate': 0,
            'water_down_asian_home_rate': 0,
            'water_up_home_rate': 0,
            'water_up_asian_home_rate': 0,
            # 欧赔变动
            'euro_win_down_home_rate': 0,
            'euro_win_down_away_rate': 0,
            'euro_lose_down_home_rate': 0,
            # 深盘
            'deep_handicap_asian_away_rate': 0,
            # 样本数
            'water_down_count': 0,
            'water_up_count': 0,
            'euro_win_down_count': 0,
            'euro_lose_down_count': 0,
            'deep_handicap_count': 0
        }
        
        total_goals = 0
        over_count = under_count = 0
        ou_total = 0
        
        # 水位变动统计
        water_down_stats = {'home': 0, 'draw': 0, 'away': 0, 'asian_home': 0, 'asian_away': 0}
        water_up_stats = {'home': 0, 'draw': 0, 'away': 0, 'asian_home': 0, 'asian_away': 0}
        
        # 欧赔变动统计
        euro_win_down_stats = {'home': 0, 'draw': 0, 'away': 0}
        euro_lose_down_stats = {'home': 0, 'draw': 0, 'away': 0}
        
        # 深盘统计
        deep_handicap_stats = {'asian_home': 0, 'asian_away': 0, 'asian_push': 0}
        
        for match in matches:
            result = get_match_result(match.get('home_score'), match.get('away_score'))
            if not result:
                continue
            
            # 基础统计
            if result == 'home':
                stats['home_win'] += 1
            elif result == 'draw':
                stats['draw'] += 1
            else:
                stats['away_win'] += 1
            
            # 进球数
            try:
                home = int(match.get('home_score', 0))
                away = int(match.get('away_score', 0))
                total_goals += home + away
                
                # 大小球
                total_line = safe_float(match.get('ou_current_total') or match.get('ou_initial_total'))
                if total_line:
                    ou_total += 1
                    ou_result = get_ou_result(match.get('home_score'), match.get('away_score'), total_line)
                    if ou_result == 'over':
                        over_count += 1
                    elif ou_result == 'under':
                        under_count += 1
            except:
                pass
            
            # 亚盘水位变动
            home_init = safe_float(match.get('asian_initial_home_odds'))
            home_curr = safe_float(match.get('asian_current_home_odds'))
            handicap_str = match.get('asian_current_handicap') or match.get('asian_initial_handicap')
            asian_result = get_asian_result(match.get('home_score'), match.get('away_score'), handicap_str)
            
            if home_init and home_curr:
                water_change = home_curr - home_init
                
                if water_change < -0.03:  # 降水
                    stats['water_down_count'] += 1
                    water_down_stats[result] += 1
                    if asian_result and asian_result != 'push':
                        water_down_stats[f'asian_{asian_result}'] += 1
                elif water_change > 0.03:  # 升水
                    stats['water_up_count'] += 1
                    water_up_stats[result] += 1
                    if asian_result and asian_result != 'push':
                        water_up_stats[f'asian_{asian_result}'] += 1
            
            # 欧赔变动
            euro_init_win = safe_float(match.get('euro_initial_win'))
            euro_curr_win = safe_float(match.get('euro_current_win'))
            euro_init_lose = safe_float(match.get('euro_initial_lose'))
            euro_curr_lose = safe_float(match.get('euro_current_lose'))
            
            if euro_init_win and euro_curr_win:
                win_change = euro_curr_win - euro_init_win
                if win_change < -0.10:  # 主胜赔率下降
                    stats['euro_win_down_count'] += 1
                    euro_win_down_stats[result] += 1
            
            if euro_init_lose and euro_curr_lose:
                lose_change = euro_curr_lose - euro_init_lose
                if lose_change < -0.10:  # 客胜赔率下降
                    stats['euro_lose_down_count'] += 1
                    euro_lose_down_stats[result] += 1
            
            # 深盘统计（让球半以上）
            handicap = parse_handicap(handicap_str)
            if handicap and handicap >= 1.5:
                stats['deep_handicap_count'] += 1
                if asian_result:
                    deep_handicap_stats[f'asian_{asian_result}'] += 1
        
        # 计算比率
        valid_matches = stats['home_win'] + stats['draw'] + stats['away_win']
        if valid_matches > 0:
            stats['home_win_rate'] = stats['home_win'] / valid_matches * 100
            stats['draw_rate'] = stats['draw'] / valid_matches * 100
            stats['away_win_rate'] = stats['away_win'] / valid_matches * 100
            stats['avg_goals'] = total_goals / valid_matches
        
        if ou_total > 0:
            stats['over_rate'] = over_count / ou_total * 100
            stats['under_rate'] = under_count / ou_total * 100
        
        # 水位变动比率
        if stats['water_down_count'] > 0:
            wdc = stats['water_down_count']
            stats['water_down_home_rate'] = water_down_stats['home'] / wdc * 100
            asian_total = water_down_stats['asian_home'] + water_down_stats['asian_away']
            if asian_total > 0:
                stats['water_down_asian_home_rate'] = water_down_stats['asian_home'] / asian_total * 100
        
        if stats['water_up_count'] > 0:
            wuc = stats['water_up_count']
            stats['water_up_home_rate'] = water_up_stats['home'] / wuc * 100
            asian_total = water_up_stats['asian_home'] + water_up_stats['asian_away']
            if asian_total > 0:
                stats['water_up_asian_home_rate'] = water_up_stats['asian_home'] / asian_total * 100
        
        # 欧赔变动比率
        if stats['euro_win_down_count'] > 0:
            ewdc = stats['euro_win_down_count']
            stats['euro_win_down_home_rate'] = euro_win_down_stats['home'] / ewdc * 100
            stats['euro_win_down_away_rate'] = euro_win_down_stats['away'] / ewdc * 100
        
        if stats['euro_lose_down_count'] > 0:
            eldc = stats['euro_lose_down_count']
            stats['euro_lose_down_home_rate'] = euro_lose_down_stats['home'] / eldc * 100
        
        # 深盘比率
        if stats['deep_handicap_count'] > 0:
            asian_total = deep_handicap_stats['asian_home'] + deep_handicap_stats['asian_away']
            if asian_total > 0:
                stats['deep_handicap_asian_away_rate'] = deep_handicap_stats['asian_away'] / asian_total * 100
        
        self.league_stats[league] = stats
    
    def print_league_comparison(self, top_leagues):
        """输出联赛对比表格"""
        
        # 1. 基础数据对比
        print("\n" + "─" * 120)
        print("📊 一、联赛基础数据对比")
        print("─" * 120)
        print(f"{'联赛':<12} {'场次':>6} {'主胜率':>8} {'平局率':>8} {'客胜率':>8} {'场均球':>8} {'大球率':>8} {'小球率':>8}")
        print("─" * 90)
        
        for league, _ in top_leagues:
            s = self.league_stats.get(league, {})
            print(f"{league:<12} {s.get('total', 0):>6} "
                  f"{s.get('home_win_rate', 0):>6.1f}% "
                  f"{s.get('draw_rate', 0):>6.1f}% "
                  f"{s.get('away_win_rate', 0):>6.1f}% "
                  f"{s.get('avg_goals', 0):>7.2f} "
                  f"{s.get('over_rate', 0):>6.1f}% "
                  f"{s.get('under_rate', 0):>6.1f}%")
        
        # 2. 水位变动对比
        print("\n" + "─" * 120)
        print("📊 二、亚盘水位变动规律对比（降水 vs 升水）")
        print("─" * 120)
        print(f"{'联赛':<12} │ {'降水样本':>8} {'降水主胜':>10} {'降水让胜':>10} │ {'升水样本':>8} {'升水主胜':>10} {'升水让胜':>10} │ {'降水价值':>10}")
        print("─" * 120)
        
        for league, _ in top_leagues:
            s = self.league_stats.get(league, {})
            
            # 计算降水价值（降水让胜率 - 升水让胜率）
            value_diff = s.get('water_down_asian_home_rate', 0) - s.get('water_up_asian_home_rate', 0)
            value_indicator = "★★★" if value_diff > 15 else ("★★" if value_diff > 8 else ("★" if value_diff > 0 else "—"))
            
            print(f"{league:<12} │ "
                  f"{s.get('water_down_count', 0):>8} "
                  f"{s.get('water_down_home_rate', 0):>8.1f}% "
                  f"{s.get('water_down_asian_home_rate', 0):>8.1f}% │ "
                  f"{s.get('water_up_count', 0):>8} "
                  f"{s.get('water_up_home_rate', 0):>8.1f}% "
                  f"{s.get('water_up_asian_home_rate', 0):>8.1f}% │ "
                  f"{value_indicator:>10}")
        
        # 3. 欧赔变动对比
        print("\n" + "─" * 120)
        print("📊 三、欧赔变动规律对比")
        print("─" * 120)
        print(f"{'联赛':<12} │ {'主赔降样本':>10} {'主赔降→主胜':>12} {'主赔降→客胜':>12} │ {'客赔降样本':>10} {'客赔降→主胜':>12} │ {'诱盘程度':>10}")
        print("─" * 120)
        
        for league, _ in top_leagues:
            s = self.league_stats.get(league, {})
            
            # 诱盘程度：主赔降时客胜率越高，诱盘程度越高
            trap_rate = s.get('euro_win_down_away_rate', 0)
            trap_indicator = "🚨高" if trap_rate > 45 else ("⚠️中" if trap_rate > 35 else "✅低")
            
            print(f"{league:<12} │ "
                  f"{s.get('euro_win_down_count', 0):>10} "
                  f"{s.get('euro_win_down_home_rate', 0):>10.1f}% "
                  f"{s.get('euro_win_down_away_rate', 0):>10.1f}% │ "
                  f"{s.get('euro_lose_down_count', 0):>10} "
                  f"{s.get('euro_lose_down_home_rate', 0):>10.1f}% │ "
                  f"{trap_indicator:>10}")
        
        # 4. 深盘规律对比
        print("\n" + "─" * 120)
        print("📊 四、深盘（让球半以上）规律对比")
        print("─" * 120)
        print(f"{'联赛':<12} {'深盘样本':>10} {'受让方赢率':>12} {'博受让价值':>12}")
        print("─" * 70)
        
        for league, _ in top_leagues:
            s = self.league_stats.get(league, {})
            
            away_rate = s.get('deep_handicap_asian_away_rate', 0)
            value_indicator = "★★★" if away_rate > 55 else ("★★" if away_rate > 50 else ("★" if away_rate > 45 else "—"))
            
            if s.get('deep_handicap_count', 0) >= 5:  # 至少5场才显示
                print(f"{league:<12} {s.get('deep_handicap_count', 0):>10} "
                      f"{away_rate:>10.1f}% "
                      f"{value_indicator:>12}")
        
        # 5. 联赛特点总结
        self.print_league_features(top_leagues)
    
    def print_league_features(self, top_leagues):
        """输出每个联赛的特点总结"""
        print("\n" + "=" * 120)
        print("🏆 各联赛特点总结")
        print("=" * 120)
        
        for league, _ in top_leagues:
            s = self.league_stats.get(league, {})
            if s.get('total', 0) < 30:
                continue
            
            features = []
            
            # 主场优势
            home_rate = s.get('home_win_rate', 0)
            if home_rate > 50:
                features.append(f"🏠 主场优势明显（主胜率{home_rate:.1f}%）")
            elif home_rate < 40:
                features.append(f"✈️ 客队强势（主胜率仅{home_rate:.1f}%）")
            
            # 进球特点
            avg_goals = s.get('avg_goals', 0)
            if avg_goals > 3.0:
                features.append(f"⚽ 高进球联赛（场均{avg_goals:.2f}球，大球价值高）")
            elif avg_goals < 2.5:
                features.append(f"🛡️ 低进球联赛（场均{avg_goals:.2f}球，小球价值高）")
            
            # 降水价值
            water_down_asian = s.get('water_down_asian_home_rate', 0)
            water_up_asian = s.get('water_up_asian_home_rate', 0)
            if water_down_asian > 55 and s.get('water_down_count', 0) >= 10:
                features.append(f"💧 降水信号强（让胜率{water_down_asian:.1f}%，可跟）")
            elif water_up_asian > 55 and s.get('water_up_count', 0) >= 10:
                features.append(f"📈 升水反向价值高（让胜率{water_up_asian:.1f}%）")
            
            # 诱盘程度
            trap_rate = s.get('euro_win_down_away_rate', 0)
            if trap_rate > 45 and s.get('euro_win_down_count', 0) >= 10:
                features.append(f"🚨 诱盘严重（主赔降时客胜率{trap_rate:.1f}%，需反向）")
            elif trap_rate < 30 and s.get('euro_win_down_count', 0) >= 10:
                features.append(f"✅ 欧赔可信（主赔降时主胜率高）")
            
            # 深盘特点
            deep_away = s.get('deep_handicap_asian_away_rate', 0)
            if deep_away > 55 and s.get('deep_handicap_count', 0) >= 5:
                features.append(f"🎯 深盘博受让（受让方赢率{deep_away:.1f}%）")
            
            if features:
                print(f"\n【{league}】({s.get('total', 0)}场)")
                for f in features:
                    print(f"   {f}")
    
    def print_betting_strategies(self):
        """输出投注策略建议"""
        print("\n" + "=" * 120)
        print("📝 分联赛投注策略建议")
        print("=" * 120)
        
        # 找出各类最佳联赛
        best_home = max(self.league_stats.items(), 
                       key=lambda x: x[1].get('home_win_rate', 0) if x[1].get('total', 0) >= 30 else 0)
        best_goals = max(self.league_stats.items(), 
                        key=lambda x: x[1].get('avg_goals', 0) if x[1].get('total', 0) >= 30 else 0)
        best_water_down = max(self.league_stats.items(), 
                             key=lambda x: x[1].get('water_down_asian_home_rate', 0) 
                             if x[1].get('water_down_count', 0) >= 10 else 0)
        highest_trap = max(self.league_stats.items(), 
                          key=lambda x: x[1].get('euro_win_down_away_rate', 0) 
                          if x[1].get('euro_win_down_count', 0) >= 10 else 0)
        best_deep = max(self.league_stats.items(), 
                       key=lambda x: x[1].get('deep_handicap_asian_away_rate', 0) 
                       if x[1].get('deep_handicap_count', 0) >= 5 else 0)
        
        print(f"""
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                         分联赛策略建议                                                    │
├─────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                          │
│  【主场优势策略】                                                                                          │
│   ★ 主场优势最强: {best_home[0]} (主胜率 {best_home[1].get('home_win_rate', 0):.1f}%)                                             │
│   → 该联赛主队胜平选择更安全                                                                               │
│                                                                                                          │
│  【大小球策略】                                                                                            │
│   ★ 进球最多联赛: {best_goals[0]} (场均 {best_goals[1].get('avg_goals', 0):.2f} 球)                                               │
│   → 该联赛适合博大球                                                                                      │
│                                                                                                          │
│  【亚盘水位策略】                                                                                          │
│   ★ 降水信号最强: {best_water_down[0]} (降水让胜率 {best_water_down[1].get('water_down_asian_home_rate', 0):.1f}%)                              │
│   → 该联赛降水时跟让胜价值高                                                                               │
│                                                                                                          │
│  【欧赔诱盘警示】                                                                                          │
│   🚨 诱盘最严重: {highest_trap[0]} (主赔降时客胜率 {highest_trap[1].get('euro_win_down_away_rate', 0):.1f}%)                             │
│   → 该联赛主赔下降时反向操作（博客胜）                                                                       │
│                                                                                                          │
│  【深盘策略】                                                                                              │
│   ★ 深盘受让最佳: {best_deep[0]} (受让方赢率 {best_deep[1].get('deep_handicap_asian_away_rate', 0):.1f}%)                                │
│   → 该联赛让球半以上博受让方                                                                               │
│                                                                                                          │
├─────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                         通用策略提醒                                                      │
├─────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                          │
│  1. 不同联赛规律差异明显，不能一概而论                                                                       │
│  2. 欧洲五大联赛数据更可靠，小联赛需谨慎                                                                     │
│  3. 样本数少的联赛（<30场）规律参考价值有限                                                                  │
│  4. 结合球队实力和近期状态综合判断                                                                          │
│                                                                                                          │
│  ⚠️ 风险提示：以上规律基于历史数据统计，仅供参考，请理性投注！                                                  │
│                                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
""")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='分联赛赔率变动规律分析工具')
    parser.add_argument('--min-matches', type=int, default=30, 
                       help='最小样本数量，默认30场')
    parser.add_argument('--top-n', type=int, default=20, 
                       help='分析前N个联赛，默认20')
    
    args = parser.parse_args()
    
    analyzer = LeaguePatternAnalyzer()
    analyzer.analyze_all_leagues(
        min_matches=args.min_matches,
        top_n=args.top_n
    )


if __name__ == '__main__':
    main()



# -*- coding: utf-8 -*-
"""
筛选下盘获胜的比赛

下盘定义：
- 主让球时，客队是下盘（受让方）
- 客让球时，主队是下盘（受让方）

下盘获胜 = 让负（让球方输，受让方赢）

使用方法：
    python filter_lower_plate_wins.py [options]

参数：
    --league LEAGUE     筛选指定联赛
    --min-odds ODDS     最低让负赔率（默认3.0为冷门）
    --days DAYS         最近N天的比赛（默认30）
    --upcoming          显示未来可能的下盘机会
"""

from db_storage import MongoDBStorage
from collections import defaultdict
import argparse
from datetime import datetime, timedelta


def safe_float(value):
    try:
        return float(value) if value else None
    except:
        return None


def safe_int(value):
    try:
        return int(value) if value else 0
    except:
        return 0


def calc_handicap_result(home_score, away_score, hi_handicap):
    """
    计算让球盘结果
    
    返回: (result, is_lower_win, upper_team_position, lower_team_position)
    - result: '上盘赢', '下盘赢', '走盘'
    - is_lower_win: 是否下盘赢
    - upper_team_position: 'home' 或 'away'
    - lower_team_position: 'home' 或 'away'
    """
    if hi_handicap < 0:
        # 主让球，主队是上盘，客队是下盘
        adjusted_diff = home_score + hi_handicap - away_score
        upper_pos = 'home'
        lower_pos = 'away'
    else:
        # 客让球，客队是上盘，主队是下盘
        adjusted_diff = away_score + (-hi_handicap) - home_score
        upper_pos = 'away'
        lower_pos = 'home'
    
    if adjusted_diff > 0:
        return ('上盘赢', False, upper_pos, lower_pos)
    elif adjusted_diff < 0:
        return ('下盘赢', True, upper_pos, lower_pos)
    else:
        return ('走盘', False, upper_pos, lower_pos)


def filter_lower_wins(storage, league=None, min_odds=None):
    """筛选下盘获胜的完场比赛"""
    finished = storage.get_matches(filters={"status": 2})
    
    results = []
    
    for m in finished:
        hi_handicap = safe_float(m.get("hi_handicap_value"))
        
        if hi_handicap is None or hi_handicap == 0:
            continue
        
        # 联赛筛选
        if league and league not in m.get("league", ""):
            continue
        
        home_score = safe_int(m.get("home_score"))
        away_score = safe_int(m.get("away_score"))
        
        result, is_lower_win, upper_pos, lower_pos = calc_handicap_result(
            home_score, away_score, hi_handicap
        )
        
        if not is_lower_win:
            continue
        
        hi_away_odds = safe_float(m.get("hi_current_away_odds"))
        
        # 赔率筛选
        if min_odds and hi_away_odds and hi_away_odds < min_odds:
            continue
        
        upper_team = m.get("home_team" if upper_pos == "home" else "away_team", "")
        lower_team = m.get("home_team" if lower_pos == "home" else "away_team", "")
        
        if hi_handicap < 0:
            handicap_desc = "主让%d球" % abs(int(hi_handicap))
        else:
            handicap_desc = "客让%d球" % int(hi_handicap)
        
        results.append({
            "match_time": m.get("match_time", ""),
            "league": m.get("league", ""),
            "home": m.get("home_team", ""),
            "away": m.get("away_team", ""),
            "score": "%d-%d" % (home_score, away_score),
            "hi_handicap": hi_handicap,
            "handicap_desc": handicap_desc,
            "upper_team": upper_team,
            "lower_team": lower_team,
            "hi_home_odds": safe_float(m.get("hi_current_home_odds")),
            "hi_draw_odds": safe_float(m.get("hi_current_draw_odds")),
            "hi_away_odds": hi_away_odds,
            "euro_home": safe_float(m.get("euro_current_win")),
            "euro_draw": safe_float(m.get("euro_current_draw")),
            "euro_away": safe_float(m.get("euro_current_lose")),
        })
    
    return results


def find_upcoming_lower_opportunities(storage, min_away_odds=3.0):
    """找出未来可能的下盘机会（让负赔率高的比赛）"""
    upcoming = storage.get_matches(filters={"status": 0})
    
    opportunities = []
    
    for m in upcoming:
        hi_handicap = safe_float(m.get("hi_handicap_value"))
        hi_away_odds = safe_float(m.get("hi_current_away_odds"))
        
        if hi_handicap is None or hi_handicap == 0:
            continue
        
        if hi_away_odds and hi_away_odds >= min_away_odds:
            if hi_handicap < 0:
                upper_team = m.get("home_team", "")
                lower_team = m.get("away_team", "")
                handicap_desc = "主让%d球" % abs(int(hi_handicap))
            else:
                upper_team = m.get("away_team", "")
                lower_team = m.get("home_team", "")
                handicap_desc = "客让%d球" % int(hi_handicap)
            
            opportunities.append({
                "match_time": m.get("match_time", ""),
                "league": m.get("league", ""),
                "home": m.get("home_team", ""),
                "away": m.get("away_team", ""),
                "hi_handicap": hi_handicap,
                "handicap_desc": handicap_desc,
                "upper_team": upper_team,
                "lower_team": lower_team,
                "hi_home_odds": safe_float(m.get("hi_current_home_odds")),
                "hi_draw_odds": safe_float(m.get("hi_current_draw_odds")),
                "hi_away_odds": hi_away_odds,
                "euro_home": safe_float(m.get("euro_current_win")),
                "euro_away": safe_float(m.get("euro_current_lose")),
            })
    
    return sorted(opportunities, key=lambda x: -(x["hi_away_odds"] or 0))


def calc_league_stats(storage):
    """计算各联赛下盘获胜率"""
    finished = storage.get_matches(filters={"status": 2})
    
    league_stats = defaultdict(lambda: {"total": 0, "lower_win": 0})
    
    for m in finished:
        hi_handicap = safe_float(m.get("hi_handicap_value"))
        
        if hi_handicap is None or hi_handicap == 0:
            continue
        
        league = m.get("league", "")
        home_score = safe_int(m.get("home_score"))
        away_score = safe_int(m.get("away_score"))
        
        _, is_lower_win, _, _ = calc_handicap_result(home_score, away_score, hi_handicap)
        
        league_stats[league]["total"] += 1
        if is_lower_win:
            league_stats[league]["lower_win"] += 1
    
    return league_stats


def main():
    parser = argparse.ArgumentParser(description='筛选下盘获胜的比赛')
    parser.add_argument('--league', type=str, help='筛选指定联赛')
    parser.add_argument('--min-odds', type=float, default=None, help='最低让负赔率')
    parser.add_argument('--upcoming', action='store_true', help='显示未来可能的下盘机会')
    parser.add_argument('--stats', action='store_true', help='显示联赛统计')
    args = parser.parse_args()
    
    storage = MongoDBStorage()
    
    print("=" * 130)
    print("🎯 下盘获胜比赛筛选工具")
    print("=" * 130)
    print()
    print("📖 说明：")
    print("   • 上盘 = 让球方（强队）")
    print("   • 下盘 = 受让方（弱队）")
    print("   • 下盘获胜 = 让负（受让方在让球后赢盘）")
    print()
    
    if args.upcoming:
        # 显示未来机会
        print("=" * 130)
        print("📊 未来下盘机会（让负赔率 ≥ 3.0）")
        print("=" * 130)
        print()
        
        opportunities = find_upcoming_lower_opportunities(storage, min_away_odds=3.0)
        
        if opportunities:
            print("%-11s %-6s %-12s vs %-12s  让球    上盘     下盘      让负赔  欧赔" % 
                  ("时间", "联赛", "主队", "客队"))
            print("-" * 130)
            
            for r in opportunities[:20]:
                cold_mark = "🔥" if r["hi_away_odds"] >= 4.0 else ""
                print("%-11s %-6s %-12s vs %-12s  %-6s  %-8s %-8s  %.2f%s  %.2f/%.2f" % 
                      (r["match_time"][:11], r["league"][:6], r["home"][:12], r["away"][:12],
                       r["handicap_desc"], r["upper_team"][:8], r["lower_team"][:8],
                       r["hi_away_odds"], cold_mark,
                       r["euro_home"] or 0, r["euro_away"] or 0))
        else:
            print("暂无符合条件的比赛")
        
        print()
        return
    
    if args.stats:
        # 显示联赛统计
        print("=" * 130)
        print("📈 各联赛下盘获胜率统计")
        print("=" * 130)
        print()
        
        league_stats = calc_league_stats(storage)
        
        for league, stats in sorted(league_stats.items(), 
                                    key=lambda x: -x[1]["lower_win"] / x[1]["total"] if x[1]["total"] >= 10 else 0):
            if stats["total"] >= 10:
                rate = stats["lower_win"] / stats["total"] * 100
                bar = "█" * int(rate / 5)
                print("  %-12s: %3d/%3d = %.1f%% %s" % (league[:12], stats["lower_win"], stats["total"], rate, bar))
        
        print()
        return
    
    # 筛选下盘获胜的完场比赛
    results = filter_lower_wins(storage, league=args.league, min_odds=args.min_odds)
    
    # 按让负赔率排序
    results.sort(key=lambda x: -(x["hi_away_odds"] or 0))
    
    print("=" * 130)
    print("📊 下盘获胜比赛列表" + (f"（联赛: {args.league}）" if args.league else "") + 
          (f"（让负赔率 ≥ {args.min_odds}）" if args.min_odds else ""))
    print("=" * 130)
    print()
    print("共找到 %d 场下盘获胜比赛" % len(results))
    print()
    
    if results:
        print("%-11s %-6s %-12s vs %-12s  比分   让球    上盘     下盘      让负赔  欧赔" % 
              ("时间", "联赛", "主队", "客队"))
        print("-" * 130)
        
        for r in results[:30]:
            cold_mark = "🔥" if r["hi_away_odds"] and r["hi_away_odds"] >= 3.0 else ""
            print("%-11s %-6s %-12s vs %-12s  %-5s  %-6s  %-8s %-8s  %.2f%s  %.2f/%.2f" % 
                  (r["match_time"][:11], r["league"][:6], r["home"][:12], r["away"][:12],
                   r["score"], r["handicap_desc"], r["upper_team"][:8], r["lower_team"][:8],
                   r["hi_away_odds"] or 0, cold_mark,
                   r["euro_home"] or 0, r["euro_away"] or 0))
    
    print()
    print("=" * 130)
    print("💡 使用提示：")
    print("   python filter_lower_plate_wins.py --upcoming      # 查看未来下盘机会")
    print("   python filter_lower_plate_wins.py --stats         # 查看联赛统计")
    print("   python filter_lower_plate_wins.py --min-odds 4.0  # 筛选高赔冷门")
    print("   python filter_lower_plate_wins.py --league 英超   # 筛选指定联赛")
    print("=" * 130)


if __name__ == "__main__":
    main()



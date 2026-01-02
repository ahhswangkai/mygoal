"""
赔率变动分析工具 - 分析亚盘和欧赔从初盘到即时盘的变化
"""
from db_storage import MongoDBStorage
from utils import setup_logger


def analyze_euro_movement(initial_win, initial_draw, initial_lose, 
                          current_win, current_draw, current_lose):
    """
    分析欧赔变动
    
    Returns:
        dict: 变动分析结果
    """
    if not all([initial_win, initial_draw, initial_lose, 
                current_win, current_draw, current_lose]):
        return None
    
    try:
        init_win = float(initial_win)
        init_draw = float(initial_draw)
        init_lose = float(initial_lose)
        curr_win = float(current_win)
        curr_draw = float(current_draw)
        curr_lose = float(current_lose)
        
        # 计算变化
        win_change = curr_win - init_win
        draw_change = curr_draw - init_draw
        lose_change = curr_lose - init_lose
        
        # 判断主要变化方向
        movements = []
        if abs(win_change) > 0.05:
            direction = "↑" if win_change > 0 else "↓"
            movements.append(f"胜 {direction} {abs(win_change):.2f}")
        
        if abs(draw_change) > 0.05:
            direction = "↑" if draw_change > 0 else "↓"
            movements.append(f"平 {direction} {abs(draw_change):.2f}")
        
        if abs(lose_change) > 0.05:
            direction = "↑" if lose_change > 0 else "↓"
            movements.append(f"负 {direction} {abs(lose_change):.2f}")
        
        # 分析倾向
        tendency = None
        if win_change < -0.1:  # 主胜赔率下降明显
            tendency = "看好主队"
        elif lose_change < -0.1:  # 客胜赔率下降明显
            tendency = "看好客队"
        elif draw_change < -0.1:  # 平局赔率下降明显
            tendency = "看好平局"
        
        return {
            'movements': movements,
            'tendency': tendency,
            'win_change': win_change,
            'draw_change': draw_change,
            'lose_change': lose_change
        }
    except Exception:
        return None


def analyze_asian_movement(initial_handicap, initial_home_odds, initial_away_odds,
                           current_handicap, current_home_odds, current_away_odds):
    """
    分析亚盘变动
    
    Returns:
        dict: 变动分析结果
    """
    if not all([initial_handicap, current_handicap]):
        return None
    
    try:
        # 解析盘口数值
        def parse_handicap(h):
            if not h or h == '-':
                return 0
            # 提取数字
            import re
            nums = re.findall(r'\d+\.?\d*', str(h))
            if nums:
                return float(nums[0])
            return 0
        
        init_h = parse_handicap(initial_handicap)
        curr_h = parse_handicap(current_handicap)
        
        init_home = float(initial_home_odds) if initial_home_odds else 0
        init_away = float(initial_away_odds) if initial_away_odds else 0
        curr_home = float(current_home_odds) if current_home_odds else 0
        curr_away = float(current_away_odds) if current_away_odds else 0
        
        # 盘口变化
        handicap_change = curr_h - init_h
        home_odds_change = curr_home - init_home
        away_odds_change = curr_away - init_away
        
        movements = []
        tendency = None
        
        # 盘口变化分析
        if abs(handicap_change) > 0.1:
            direction = "↑" if handicap_change > 0 else "↓"
            movements.append(f"盘口 {direction} {abs(handicap_change):.2f}")
            
            if handicap_change > 0.2:
                tendency = "升盘看好主队"
            elif handicap_change < -0.2:
                tendency = "降盘看淡主队"
        
        # 水位变化分析
        if abs(home_odds_change) > 0.05:
            direction = "↑" if home_odds_change > 0 else "↓"
            movements.append(f"主队水位 {direction} {abs(home_odds_change):.2f}")
        
        if abs(away_odds_change) > 0.05:
            direction = "↑" if away_odds_change > 0 else "↓"
            movements.append(f"客队水位 {direction} {abs(away_odds_change):.2f}")
        
        # 组合判断：盘口+水位
        if handicap_change > 0.1 and home_odds_change < 0:
            tendency = "升盘降水，强力看好主队"
        elif handicap_change < -0.1 and away_odds_change < 0:
            tendency = "降盘降水，强力看好客队"
        elif handicap_change > 0.1 and home_odds_change > 0:
            tendency = "升盘升水，诱盘可能"
        elif handicap_change < -0.1 and away_odds_change > 0:
            tendency = "降盘升水，诱盘可能"
        
        return {
            'movements': movements,
            'tendency': tendency,
            'handicap_change': handicap_change,
            'home_odds_change': home_odds_change,
            'away_odds_change': away_odds_change
        }
    except Exception:
        return None


def analyze_match_odds_movement(league=None, status=None, limit=20):
    """
    分析比赛的赔率变动
    
    Args:
        league: 联赛筛选
        status: 状态筛选（0=未开始，1=进行中，2=完场）
        limit: 显示数量
    """
    logger = setup_logger()
    
    try:
        storage = MongoDBStorage()
        logger.info("成功连接MongoDB")
    except Exception as e:
        logger.error(f"MongoDB连接失败: {str(e)}")
        return
    
    # 构建筛选条件
    filters = {}
    if league:
        filters['league'] = league
    if status is not None:
        filters['status'] = status
    
    matches = storage.get_matches(filters=filters, limit=limit)
    
    if not matches:
        print("\n未找到符合条件的比赛")
        return
    
    print("\n" + "=" * 120)
    print("⚖️  赔率变动分析报告")
    print("=" * 120)
    
    if league:
        print(f"联赛筛选: {league}")
    if status is not None:
        status_text = {0: '未开始', 1: '进行中', 2: '完场'}.get(status, '未知')
        print(f"状态筛选: {status_text}")
    
    print(f"分析比赛数: {len(matches)}")
    print("=" * 120)
    
    # 分析每场比赛
    analyzed_count = 0
    
    for match in matches:
        # 检查是否有初盘和即时盘数据
        has_euro_initial = match.get('euro_initial_win') and match.get('euro_initial_draw') and match.get('euro_initial_lose')
        has_euro_current = match.get('euro_current_win') and match.get('euro_current_draw') and match.get('euro_current_lose')
        has_asian_initial = match.get('asian_initial_handicap')
        has_asian_current = match.get('asian_current_handicap')
        
        if not (has_euro_initial and has_euro_current) and not (has_asian_initial and has_asian_current):
            continue
        
        analyzed_count += 1
        
        print(f"\n【比赛 {analyzed_count}】")
        print(f"联赛: {match.get('league', '-')}")
        print(f"时间: {match.get('match_time', '-')}")
        print(f"对阵: {match.get('home_team', '-')} vs {match.get('away_team', '-')}")
        
        if match.get('status') == 2:
            print(f"比分: {match.get('home_score', '-')}-{match.get('away_score', '-')}")
        
        print("-" * 120)
        
        # 欧赔变动分析
        if has_euro_initial and has_euro_current:
            euro_analysis = analyze_euro_movement(
                match.get('euro_initial_win'),
                match.get('euro_initial_draw'),
                match.get('euro_initial_lose'),
                match.get('euro_current_win'),
                match.get('euro_current_draw'),
                match.get('euro_current_lose')
            )
            
            if euro_analysis:
                print("\n📊 欧赔变动:")
                print(f"  初盘: {match.get('euro_initial_win')}/{match.get('euro_initial_draw')}/{match.get('euro_initial_lose')}")
                print(f"  即时: {match.get('euro_current_win')}/{match.get('euro_current_draw')}/{match.get('euro_current_lose')}")
                
                if euro_analysis['movements']:
                    print(f"  变化: {', '.join(euro_analysis['movements'])}")
                else:
                    print("  变化: 无明显变化")
                
                if euro_analysis['tendency']:
                    print(f"  ➤ 倾向: {euro_analysis['tendency']}")
        
        # 亚盘变动分析
        if has_asian_initial and has_asian_current:
            asian_analysis = analyze_asian_movement(
                match.get('asian_initial_handicap'),
                match.get('asian_initial_home_odds'),
                match.get('asian_initial_away_odds'),
                match.get('asian_current_handicap'),
                match.get('asian_current_home_odds'),
                match.get('asian_current_away_odds')
            )
            
            if asian_analysis:
                print("\n📊 亚盘变动:")
                print(f"  初盘: {match.get('asian_initial_home_odds')} {match.get('asian_initial_handicap')} {match.get('asian_initial_away_odds')}")
                print(f"  即时: {match.get('asian_current_home_odds')} {match.get('asian_current_handicap')} {match.get('asian_current_away_odds')}")
                
                if asian_analysis['movements']:
                    print(f"  变化: {', '.join(asian_analysis['movements'])}")
                else:
                    print("  变化: 无明显变化")
                
                if asian_analysis['tendency']:
                    print(f"  ➤ 倾向: {asian_analysis['tendency']}")
        
        print("-" * 120)
    
    print(f"\n分析完成，共分析 {analyzed_count} 场比赛")
    print("=" * 120)
    
    # 输出分析说明
    print("\n📖 赔率变动解读指南")
    print("=" * 120)
    print("\n【欧赔变动】")
    print("• 主胜赔率下降 → 看好主队胜")
    print("• 平局赔率下降 → 看好平局")
    print("• 客胜赔率下降 → 看好客队胜")
    print("• 赔率变化>0.1 为明显变化")
    
    print("\n【亚盘变动】")
    print("• 升盘降水（盘口升+主队水位降）→ 强力看好主队")
    print("• 降盘降水（盘口降+客队水位降）→ 强力看好客队")
    print("• 升盘升水（盘口升+主队水位升）→ 可能诱盘，需谨慎")
    print("• 降盘升水（盘口降+客队水位升）→ 可能诱盘，需谨慎")
    print("• 盘口不变，水位对调 → 资金流向变化")
    
    print("\n⚠️  投注建议")
    print("• 关注临场最后1-2小时的变化最为重要")
    print("• 大额资金流入会引起明显赔率变化")
    print("• 结合多家公司赔率对比，避免被单一公司误导")
    print("• 诱盘识别：盘口变化与实际实力不符时需警惕")
    print("=" * 120)


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='赔率变动分析工具')
    parser.add_argument('--league', type=str, help='联赛名称，如：西甲')
    parser.add_argument('--status', type=int, choices=[0, 1, 2],
                       help='比赛状态：0=未开始，1=进行中，2=完场')
    parser.add_argument('--limit', type=int, default=20,
                       help='显示数量，默认20场')
    
    args = parser.parse_args()
    
    analyze_match_odds_movement(
        league=args.league,
        status=args.status,
        limit=args.limit
    )


if __name__ == '__main__':
    main()

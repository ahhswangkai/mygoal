from db_storage import MongoDBStorage
import argparse
from utils import setup_logger
import re

def parse_handicap(handicap_str):
    """
    解析中文盘口为数字
    例如: 
    平手 -> 0
    平/半 -> 0.25
    半球 -> 0.5
    半/一 -> 0.75
    一球 -> 1.0
    受... -> 负值
    """
    if not handicap_str:
        return None
        
    is_receiver = '受' in handicap_str
    clean_str = handicap_str.replace('受', '')
    
    value = 0.0
    if clean_str == '平手':
        value = 0.0
    elif clean_str in ['平/半', '平手/半球']:
        value = 0.25
    elif clean_str == '半球':
        value = 0.5
    elif clean_str in ['半/一', '半球/一球']:
        value = 0.75
    elif clean_str == '一球':
        value = 1.0
    elif clean_str in ['一/球半', '一球/球半']:
        value = 1.25
    elif clean_str == '球半':
        value = 1.5
    elif clean_str in ['球半/两', '球半/两球']:
        value = 1.75
    elif clean_str == '两球':
        value = 2.0
    else:
        # 尝试直接解析数字 (有些数据可能是直接存储数字)
        try:
            value = float(clean_str)
        except ValueError:
            return None
            
    return -value if is_receiver else value

def analyze_market_stats(league_name="英超"):
    logger = setup_logger()
    try:
        storage = MongoDBStorage()
        matches = storage.get_matches({'status': 2, 'league': league_name})
        
        if not matches:
            print(f"未找到 {league_name} 的数据")
            return

        print(f"\n=== {league_name} 深度盘口分析 (共 {len(matches)} 场) ===\n")

        # --- 1. 大小球按盘口统计 ---
        ou_stats = {}
        
        for match in matches:
            try:
                home = int(match.get('home_score', 0))
                away = int(match.get('away_score', 0))
                total = home + away
                
                # 优先使用初盘
                line_str = match.get('ou_initial_total')
                if not line_str:
                    continue
                    
                line = float(line_str)
                
                if line not in ou_stats:
                    ou_stats[line] = {'over': 0, 'under': 0, 'push': 0, 'total': 0}
                
                stats = ou_stats[line]
                stats['total'] += 1
                
                if total > line:
                    stats['over'] += 1
                elif total < line:
                    stats['under'] += 1
                else:
                    stats['push'] += 1
                    
            except (ValueError, TypeError):
                continue
        
        print("--- 大小球(初盘) 概率统计 ---")
        print(f"{'盘口':<8} {'场次':<6} {'大球率':<10} {'小球率':<10} {'走盘率':<10}")
        print("-" * 50)
        
        sorted_lines = sorted(ou_stats.keys())
        for line in sorted_lines:
            s = ou_stats[line]
            over_rate = (s['over'] / s['total']) * 100
            under_rate = (s['under'] / s['total']) * 100
            push_rate = (s['push'] / s['total']) * 100
            
            print(f"{line:<8} {s['total']:<6} {over_rate:>6.1f}%   {under_rate:>6.1f}%   {push_rate:>6.1f}%")

        # --- 2. 胜平负分布 ---
        wdl_stats = {'home': 0, 'draw': 0, 'away': 0}
        for match in matches:
            try:
                home = int(match.get('home_score', 0))
                away = int(match.get('away_score', 0))
                
                if home > away:
                    wdl_stats['home'] += 1
                elif home < away:
                    wdl_stats['away'] += 1
                else:
                    wdl_stats['draw'] += 1
            except:
                continue
                
        print("\n--- 胜平负分布 ---")
        total_matches = sum(wdl_stats.values())
        print(f"主胜: {wdl_stats['home']} ({wdl_stats['home']/total_matches*100:.1f}%)")
        print(f"平局: {wdl_stats['draw']} ({wdl_stats['draw']/total_matches*100:.1f}%)")
        print(f"客胜: {wdl_stats['away']} ({wdl_stats['away']/total_matches*100:.1f}%)")

    except Exception as e:
        logger.error(f"分析出错: {str(e)}")
        print(f"分析出错: {str(e)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--league', type=str, default='英超')
    args = parser.parse_args()
    analyze_market_stats(args.league)



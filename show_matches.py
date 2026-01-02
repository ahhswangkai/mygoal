#!/usr/bin/env python3
"""
展示比赛数据脚本
"""
import json
import os
from datetime import datetime
from crawler import FootballCrawler
from storage import DataStorage

def show_matches_data():
    """展示11月30日的比赛数据"""
    print("\n" + "="*80)
    print("⚽ 11月30日足球比赛数据")
    print("="*80 + "\n")
    
    # 检查现有数据文件
    data_dir = './data'
    json_files = [f for f in os.listdir(data_dir) if f.endswith('.json')]
    
    matches = []
    
    # 读取现有数据
    if json_files:
        print(f"📁 找到 {len(json_files)} 个数据文件\n")
        for json_file in json_files:
            file_path = os.path.join(data_dir, json_file)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        matches.extend(data)
                print(f"✅ 加载: {json_file} ({len(data) if isinstance(data, list) else 0} 场)")
            except:
                pass
    
    # 筛选11月30日的比赛
    nov30_matches = [m for m in matches if '2025-11-30' in m.get('match_time', '') or '11-30' in m.get('match_time', '')]
    
    if nov30_matches:
        print(f"\n📊 11月30日共有 {len(nov30_matches)} 场比赛：\n")
        print("-" * 80)
        
        for i, match in enumerate(nov30_matches, 1):
            print(f"\n【比赛 {i}】")
            print(f"  🆔 ID: {match.get('match_id', 'N/A')}")
            print(f"  🏆 联赛: {match.get('league', 'N/A')}")
            print(f"  🕐 时间: {match.get('match_time', 'N/A')}")
            print(f"  🏠 主队: {match.get('home_team', 'N/A')}")
            print(f"  🚀 客队: {match.get('away_team', 'N/A')}")
            print(f"  📊 状态: {match.get('status', 'N/A')}")
            
            # 显示比分（如果有）
            if match.get('home_score') and match.get('away_score'):
                print(f"  ⚽ 比分: {match['home_score']} - {match['away_score']}")
            
            # 显示赔率（如果有）
            if match.get('euro_current_win'):
                print(f"  💰 欧赔: {match.get('euro_current_win')}/{match.get('euro_current_draw')}/{match.get('euro_current_lose')}")
            
            print("-" * 80)
    else:
        print("\n⚠️  没有找到11月30日的比赛数据")
        print("\n💡 建议：")
        print("1. 访问 Web 界面爬取数据: http://127.0.0.1:5001")
        print("2. 或运行: python3 main.py")
    
    print("\n" + "="*80)
    print(f"📈 统计信息")
    print("="*80)
    print(f"  总比赛数: {len(matches)}")
    print(f"  11月30日: {len(nov30_matches)}")
    
    # 按联赛统计
    if nov30_matches:
        leagues = {}
        for m in nov30_matches:
            league = m.get('league', '未知')
            leagues[league] = leagues.get(league, 0) + 1
        
        print(f"\n  联赛分布:")
        for league, count in sorted(leagues.items(), key=lambda x: x[1], reverse=True):
            print(f"    - {league}: {count} 场")
    
    print("\n" + "="*80)
    print("🌐 在 Web 界面查看: http://127.0.0.1:5001")
    print("="*80 + "\n")

if __name__ == '__main__':
    show_matches_data()

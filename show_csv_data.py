#!/usr/bin/env python3
"""
展示CSV文件中的11月30日比赛数据
"""
import pandas as pd
import glob
import os
from datetime import datetime

def show_nov30_matches():
    """展示11月30日的比赛数据"""
    print("\n" + "="*100)
    print("⚽ 11月30日足球比赛数据（来自爬虫CSV文件）")
    print("="*100 + "\n")
    
    # 查找所有CSV文件
    csv_files = glob.glob('./data/*.csv')
    
    if not csv_files:
        print("❌ 未找到CSV数据文件")
        return
    
    print(f"📁 找到 {len(csv_files)} 个CSV文件\n")
    
    all_matches = []
    
    # 读取所有CSV文件
    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file, encoding='utf-8-sig')
            # 筛选11月30日的数据
            if 'match_time' in df.columns:
                nov30_df = df[df['match_time'].str.contains('11-30', na=False)]
                if not nov30_df.empty:
                    all_matches.append(nov30_df)
                    print(f"✅ {os.path.basename(csv_file)}: 找到 {len(nov30_df)} 场比赛")
        except Exception as e:
            print(f"⚠️  读取 {os.path.basename(csv_file)} 失败: {str(e)}")
    
    if not all_matches:
        print("\n❌ 未找到11月30日的比赛数据")
        return
    
    # 合并所有数据
    combined_df = pd.concat(all_matches, ignore_index=True)
    
    # 去重（基于match_id）
    if 'match_id' in combined_df.columns:
        combined_df = combined_df.drop_duplicates(subset=['match_id'], keep='first')
    
    total_matches = len(combined_df)
    
    print(f"\n{'='*100}")
    print(f"📊 11月30日共有 {total_matches} 场比赛")
    print("="*100 + "\n")
    
    # 显示详细数据
    for idx, row in combined_df.iterrows():
        print(f"【比赛 {idx+1}】")
        print(f"  🆔 编号: {row.get('round_id', 'N/A')}")
        print(f"  🏆 联赛: {row.get('league', 'N/A')}")
        print(f"  🎯 轮次: {row.get('round', 'N/A')}")
        print(f"  🕐 时间: {row.get('match_time', 'N/A')}")
        print(f"  🏠 主队: {row.get('home_team', 'N/A')}")
        print(f"  🚀 客队: {row.get('away_team', 'N/A')}")
        print(f"  📊 状态: {row.get('status', 'N/A') if pd.notna(row.get('status')) else '未开始'}")
        
        # 显示比分
        if pd.notna(row.get('score')) and row.get('score'):
            print(f"  ⚽ 比分: {row['score']}")
        
        # 显示欧赔
        if pd.notna(row.get('euro_win')):
            print(f"  💰 欧赔(胜/平/负): {row.get('euro_win', '-')}/{row.get('euro_draw', '-')}/{row.get('euro_lose', '-')}")
        
        # 显示亚盘
        if pd.notna(row.get('asian_handicap')):
            print(f"  📈 亚盘: {row.get('asian_home_odds', '-')} {row.get('asian_handicap', '-')} {row.get('asian_away_odds', '-')}")
        
        # 显示大小球
        if pd.notna(row.get('total_goals')):
            print(f"  🎯 大小球: 大球{row.get('over_odds', '-')} {row.get('total_goals', '-')} 小球{row.get('under_odds', '-')}")
        
        print("-" * 100)
    
    # 统计信息
    print(f"\n{'='*100}")
    print("📈 统计信息")
    print("="*100)
    
    # 按联赛统计
    if 'league' in combined_df.columns:
        league_stats = combined_df['league'].value_counts()
        print(f"\n🏆 联赛分布 (前10):")
        for league, count in league_stats.head(10).items():
            print(f"  {league}: {count} 场")
    
    # 按状态统计
    if 'status' in combined_df.columns:
        status_stats = combined_df['status'].fillna('未开始').value_counts()
        print(f"\n📊 状态分布:")
        for status, count in status_stats.items():
            print(f"  {status}: {count} 场")
    
    print(f"\n{'='*100}")
    print("💡 提示:")
    print("  - 在Web界面查看: http://127.0.0.1:5001")
    print("  - CSV文件位置: ./data/")
    print("  - 系统已自动使用最新的CSV文件")
    print("="*100 + "\n")

if __name__ == '__main__':
    show_nov30_matches()

#!/usr/bin/env python3
"""
比赛预测命令行工具
"""
import argparse
from db_storage import MongoDBStorage
from prediction_engine import PredictionEngine
from prediction_review import PredictionReviewer


def predict_all():
    """预测所有未开始的比赛"""
    print("=" * 80)
    print("开始预测所有未开始的比赛")
    print("=" * 80)
    
    storage = MongoDBStorage()
    engine = PredictionEngine()
    
    upcoming = storage.get_matches(filters={'status': 0})
    
    if not upcoming:
        print("\n暂无未开始的比赛")
        return
    
    print(f"\n找到 {len(upcoming)} 场未开始的比赛\n")
    
    for i, match in enumerate(upcoming, 1):
        home_team = match.get('home_team')
        away_team = match.get('away_team')
        
        # 检查赔率数据
        if not match.get('euro_current_win') and not match.get('euro_initial_win'):
            print(f"[{i}/{len(upcoming)}] 跳过 {home_team} vs {away_team} (无赔率)")
            continue
        
        try:
            prediction = engine.predict_match(match)
            
            if prediction:
                storage.save_prediction(prediction)
                print(f"[{i}/{len(upcoming)}] {home_team} vs {away_team}")
                print(f"  预测: {prediction['win_prediction']} (置信度{prediction['win_confidence']:.1f}%)")
                print(f"  比分: {prediction['predicted_home_score']}-{prediction['predicted_away_score']}")
                print()
        except Exception as e:
            print(f"[{i}/{len(upcoming)}] 预测失败 {home_team} vs {away_team}: {str(e)}")
    
    print("=" * 80)
    print("预测完成")
    print("=" * 80)


def predict_one(match_id):
    """预测单场比赛"""
    storage = MongoDBStorage()
    engine = PredictionEngine()
    
    match = storage.get_match_by_id(match_id)
    
    if not match:
        print(f"❌ 未找到比赛 {match_id}")
        return
    
    print("=" * 80)
    print(f"预测比赛: {match.get('home_team')} vs {match.get('away_team')}")
    print("=" * 80)
    
    prediction = engine.predict_match(match)
    
    if prediction:
        storage.save_prediction(prediction)
        
        print(f"\n✅ 预测成功！\n")
        print(f"📊 胜负预测: {prediction['win_prediction']} (置信度 {prediction['win_confidence']:.1f}%)")
        print(f"⚖️  亚盘预测: {prediction['asian_prediction']} 让{prediction['asian_handicap']} (置信度 {prediction['asian_confidence']:.1f}%)")
        print(f"⚽ 大小球预测: {prediction['ou_prediction']} {prediction['ou_total']} (置信度 {prediction['ou_confidence']:.1f}%)")
        print(f"🎯 比分预测: {prediction['predicted_home_score']}-{prediction['predicted_away_score']}")
        
        if prediction.get('home_form'):
            home_form = prediction['home_form']
            print(f"\n🏠 {match.get('home_team')} 近况:")
            print(f"   胜率 {home_form['win_rate']*100:.1f}% | 场均进球 {home_form['avg_goals_scored']:.1f} | 大球率 {home_form['over_rate']*100:.1f}%")
        
        if prediction.get('away_form'):
            away_form = prediction['away_form']
            print(f"✈️  {match.get('away_team')} 近况:")
            print(f"   胜率 {away_form['win_rate']*100:.1f}% | 场均进球 {away_form['avg_goals_scored']:.1f} | 大球率 {away_form['over_rate']*100:.1f}%")
        
        print("\n✅ 预测已保存到数据库")
    else:
        print("❌ 预测失败")


def review_all():
    """复盘所有完场比赛"""
    print("=" * 80)
    print("开始复盘所有已完场比赛")
    print("=" * 80)
    
    reviewer = PredictionReviewer()
    results = reviewer.review_all_finished_matches()
    
    if not results:
        print("\n暂无需要复盘的比赛")
        return
    
    print(f"\n✅ 复盘了 {len(results)} 场比赛\n")
    
    # 统计
    total_accuracy = sum(r.get('accuracy', 0) for r in results) / len(results)
    win_correct = sum(1 for r in results if r.get('win_correct'))
    asian_correct = sum(1 for r in results if r.get('asian_correct'))
    ou_correct = sum(1 for r in results if r.get('ou_correct'))
    
    print("📊 复盘统计:")
    print(f"  总体准确度: {total_accuracy:.1f}%")
    print(f"  胜负准确率: {win_correct/len(results)*100:.1f}% ({win_correct}/{len(results)})")
    print(f"  亚盘准确率: {asian_correct/len(results)*100:.1f}% ({asian_correct}/{len(results)})")
    print(f"  大小球准确率: {ou_correct/len(results)*100:.1f}% ({ou_correct}/{len(results)})")
    
    print("\n详细结果:")
    for r in results:
        status = '✅' if r.get('accuracy', 0) >= 75 else '⚠️' if r.get('accuracy', 0) >= 50 else '❌'
        print(f"{status} {r['league']}: {r['home_team']} {r['actual_home_score']}-{r['actual_away_score']} {r['away_team']}")
        print(f"   准确度{r['accuracy']:.0f}% | 胜负{'✅' if r['win_correct'] else '❌'} 亚盘{'✅' if r['asian_correct'] else '❌'} 大小球{'✅' if r['ou_correct'] else '❌'}")
    
    print("\n=" * 80)
    print("复盘完成")
    print("=" * 80)


def show_summary(days=7):
    """显示汇总报告"""
    reviewer = PredictionReviewer()
    summary = reviewer.generate_summary_report(days=days)
    
    if not summary:
        print(f"暂无最近{days}天的复盘数据")
        return
    
    print("=" * 80)
    print(f"📊 最近{days}天预测汇总报告")
    print("=" * 80)
    
    print(f"\n总体统计:")
    print(f"  总预测场次: {summary['total_matches']}")
    print(f"  胜负准确率: {summary['win_accuracy']:.1f}%")
    print(f"  亚盘准确率: {summary['asian_accuracy']:.1f}%")
    print(f"  大小球准确率: {summary['ou_accuracy']:.1f}%")
    print(f"  平均准确度: {summary['avg_accuracy']:.1f}%")
    
    print(f"\n各联赛表现:")
    for league, stats in sorted(summary['league_stats'].items(), key=lambda x: x[1]['total'], reverse=True):
        total = stats['total']
        win_pct = stats['win_correct'] / total * 100
        asian_pct = stats['asian_correct'] / total * 100
        ou_pct = stats['ou_correct'] / total * 100
        
        print(f"  {league:10s} ({total:2d}场): 胜负{win_pct:5.1f}% | 亚盘{asian_pct:5.1f}% | 大小球{ou_pct:5.1f}%")
    
    print("\n=" * 80)


def main():
    parser = argparse.ArgumentParser(description='比赛预测与复盘工具')
    subparsers = parser.add_subparsers(dest='command', help='命令')
    
    # 预测命令
    predict_parser = subparsers.add_parser('predict', help='预测比赛')
    predict_parser.add_argument('match_id', nargs='?', help='比赛ID（留空则预测所有）')
    
    # 复盘命令
    subparsers.add_parser('review', help='复盘已完场比赛')
    
    # 汇总命令
    summary_parser = subparsers.add_parser('summary', help='显示预测汇总')
    summary_parser.add_argument('--days', type=int, default=7, help='统计天数（默认7天）')
    
    args = parser.parse_args()
    
    if args.command == 'predict':
        if args.match_id:
            predict_one(args.match_id)
        else:
            predict_all()
    elif args.command == 'review':
        review_all()
    elif args.command == 'summary':
        show_summary(args.days)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()

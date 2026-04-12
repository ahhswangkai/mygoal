#!/usr/bin/env python3
"""
比赛预测命令行工具
"""
import argparse
from db_storage import MongoDBStorage
from prediction_engine import PredictionEngine


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


def main():
    parser = argparse.ArgumentParser(description='比赛预测工具')
    subparsers = parser.add_subparsers(dest='command', help='命令')
    
    # 预测命令
    predict_parser = subparsers.add_parser('predict', help='预测比赛')
    predict_parser.add_argument('match_id', nargs='?', help='比赛ID（留空则预测所有）')
    
    args = parser.parse_args()
    
    if args.command == 'predict':
        if args.match_id:
            predict_one(args.match_id)
        else:
            predict_all()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()

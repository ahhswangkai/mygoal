# -*- coding: utf-8 -*-
"""
基于规律的比赛预测工具
根据分析出的赔率变动规律预测比赛结果
"""
from db_storage import MongoDBStorage
from utils import setup_logger
import argparse
import re
from datetime import datetime


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


class PatternPredictor:
    """基于规律的预测器"""
    
    def __init__(self):
        self.storage = MongoDBStorage()
        self.logger = setup_logger()
        
        # 联赛特征库（基于之前分析）
        self.league_features = {
            # 降水信号强的联赛
            'water_down_leagues': ['欧罗巴', '法乙', '英冠', '葡超', '世外欧洲', '日职', '英超', '法甲'],
            # 诱盘严重的联赛（主赔降时反向）
            'trap_leagues': ['瑞典超', '葡超', '世外欧洲', '挪超', '日职乙', '德甲', '日职', '美职联'],
            # 高进球联赛
            'high_goal_leagues': ['荷甲', '世外欧洲', '德甲', '挪超', '欧冠', '美职联'],
            # 低进球联赛
            'low_goal_leagues': ['日职乙', '法乙', '意甲', '德乙', '瑞典超'],
            # 深盘博受让联赛
            'deep_handicap_leagues': ['世外欧洲', '葡超', '荷甲'],
            # 平手盘高平局联赛
            'level_draw_leagues': ['葡超', '英超', '法乙', 'K1联赛', '瑞典超'],
            # 平手盘客胜联赛
            'level_away_leagues': ['日职乙', '美职联', '荷乙']
        }
    
    def predict_matches(self, date_filter=None):
        """
        预测未开始的比赛
        
        Args:
            date_filter: 日期筛选（格式：12-07 或 2024-12-07）
        """
        print("\n" + "=" * 120)
        print("🎯 基于规律的比赛预测")
        print("=" * 120)
        
        # 获取未开始的比赛
        filters = {'status': 0}
        matches = self.storage.get_matches(filters=filters)
        
        # 日期筛选
        if date_filter:
            filtered = []
            for m in matches:
                match_time = m.get('match_time', '')
                if date_filter in match_time:
                    filtered.append(m)
            matches = filtered
        
        print(f"📊 待预测比赛: {len(matches)} 场")
        if date_filter:
            print(f"📅 日期筛选: {date_filter}")
        print("=" * 120)
        
        if not matches:
            print("⚠️ 没有找到符合条件的比赛")
            return
        
        # 对每场比赛进行预测
        predictions = []
        for match in matches:
            pred = self.predict_single_match(match)
            predictions.append(pred)
        
        # 按置信度排序
        predictions.sort(key=lambda x: x['confidence'], reverse=True)
        
        # 输出预测结果
        self.print_predictions(predictions)
        
        # 输出推荐组合
        self.print_recommendations(predictions)
    
    def predict_single_match(self, match):
        """预测单场比赛"""
        prediction = {
            'match_id': match.get('match_id'),
            'league': match.get('league', '未知'),
            'match_time': match.get('match_time', ''),
            'home_team': match.get('home_team', '主队'),
            'away_team': match.get('away_team', '客队'),
            'home_rank': match.get('home_rank', ''),
            'away_rank': match.get('away_rank', ''),
            'result': None,  # 预测结果
            'confidence': 50,  # 置信度
            'reasons': [],  # 预测理由
            'asian_prediction': None,  # 亚盘预测
            'ou_prediction': None,  # 大小球预测
            'warnings': []  # 风险提示
        }
        
        league = match.get('league', '')
        
        # 获取赔率数据
        euro_win = safe_float(match.get('euro_current_win') or match.get('euro_initial_win'))
        euro_draw = safe_float(match.get('euro_current_draw') or match.get('euro_initial_draw'))
        euro_lose = safe_float(match.get('euro_current_lose') or match.get('euro_initial_lose'))
        
        euro_init_win = safe_float(match.get('euro_initial_win'))
        euro_init_lose = safe_float(match.get('euro_initial_lose'))
        
        asian_home = safe_float(match.get('asian_current_home_odds') or match.get('asian_initial_home_odds'))
        asian_away = safe_float(match.get('asian_current_away_odds') or match.get('asian_initial_away_odds'))
        asian_init_home = safe_float(match.get('asian_initial_home_odds'))
        
        handicap_str = match.get('asian_current_handicap') or match.get('asian_initial_handicap')
        handicap = parse_handicap(handicap_str)
        
        asian_label = match.get('asian_movement_label', '')
        
        ou_total = safe_float(match.get('ou_current_total') or match.get('ou_initial_total'))
        
        # 初始化预测分数
        home_score = 0
        draw_score = 0
        away_score = 0
        
        # ========== 规律1: 欧赔分析 ==========
        if euro_win and euro_lose:
            if euro_win < 1.8:
                home_score += 30
                prediction['reasons'].append(f"欧赔主胜{euro_win}极低，强看主")
            elif euro_win < 2.2:
                home_score += 15
                prediction['reasons'].append(f"欧赔主胜{euro_win}偏低")
            elif euro_lose < 1.8:
                away_score += 30
                prediction['reasons'].append(f"欧赔客胜{euro_lose}极低，强看客")
            elif euro_lose < 2.2:
                away_score += 15
                prediction['reasons'].append(f"欧赔客胜{euro_lose}偏低")
            
            # 欧赔变动（诱盘检测）
            if euro_init_win and euro_win:
                win_change = euro_win - euro_init_win
                if win_change < -0.15:  # 主赔大降
                    if league in self.league_features['trap_leagues']:
                        # 诱盘联赛，反向操作
                        away_score += 20
                        prediction['reasons'].append(f"⚠️ {league}主赔降{abs(win_change):.2f}，诱盘信号，反向博客")
                        prediction['warnings'].append("诱盘联赛")
                    else:
                        home_score += 10
                        prediction['reasons'].append(f"主赔降{abs(win_change):.2f}")
        
        # ========== 规律2: 亚盘分析 ==========
        if handicap is not None:
            # 平手盘特殊规律
            if abs(handicap) <= 0.25:  # 平手/平半
                if asian_home and 1.01 <= asian_home <= 1.10:
                    draw_score += 25
                    prediction['reasons'].append(f"平手盘中高水{asian_home}，高平局概率")
                
                if league in self.league_features['level_draw_leagues']:
                    draw_score += 15
                    prediction['reasons'].append(f"{league}平手盘高平局")
                elif league in self.league_features['level_away_leagues']:
                    away_score += 15
                    prediction['reasons'].append(f"{league}平手盘客胜率高")
            
            # 深盘规律（让球半以上）
            elif handicap >= 1.5:
                if league in self.league_features['deep_handicap_leagues']:
                    prediction['asian_prediction'] = '受让方'
                    prediction['reasons'].append(f"深盘{handicap_str}，{league}博受让")
                else:
                    prediction['asian_prediction'] = '受让方'
                    prediction['reasons'].append(f"深盘{handicap_str}，博受让")
            elif handicap <= -1.5:
                prediction['asian_prediction'] = '让方'
                prediction['reasons'].append(f"反向深盘{handicap_str}，博让方")
        
        # ========== 规律3: 水位变动分析 ==========
        if asian_init_home and asian_home:
            water_change = asian_home - asian_init_home
            
            if water_change < -0.05:  # 降水
                if league in self.league_features['water_down_leagues']:
                    home_score += 20
                    prediction['asian_prediction'] = '让胜'
                    prediction['reasons'].append(f"降水{abs(water_change):.2f}，{league}降水信号强")
                else:
                    home_score += 10
                    prediction['reasons'].append(f"主水降{abs(water_change):.2f}")
            elif water_change > 0.05:  # 升水
                away_score += 10
                prediction['reasons'].append(f"主水升{water_change:.2f}")
        
        # ========== 规律4: 盘口变动标签 ==========
        if asian_label:
            if asian_label == '升盘降水':
                home_score += 15
                prediction['reasons'].append("升盘降水，机构看好主队")
            elif asian_label == '降盘降水':
                away_score += 15
                prediction['reasons'].append("降盘降水，机构看好客队")
            elif asian_label in ['升盘升水', '降盘升水']:
                prediction['warnings'].append(f"{asian_label}可能诱盘")
        
        # ========== 规律5: 大小球分析 ==========
        if ou_total:
            if league in self.league_features['high_goal_leagues']:
                prediction['ou_prediction'] = '大球'
                prediction['reasons'].append(f"{league}高进球联赛，倾向大球")
            elif league in self.league_features['low_goal_leagues']:
                prediction['ou_prediction'] = '小球'
                prediction['reasons'].append(f"{league}低进球联赛，倾向小球")
            
            # 平手盘小球倾向
            if handicap is not None and abs(handicap) <= 0.25:
                prediction['ou_prediction'] = '小球'
                prediction['reasons'].append("平手盘比赛胶着，倾向小球")
        
        # ========== 计算最终预测 ==========
        max_score = max(home_score, draw_score, away_score)
        
        if max_score == 0:
            prediction['result'] = '难以判断'
            prediction['confidence'] = 40
        elif home_score == max_score:
            prediction['result'] = '主胜'
            prediction['confidence'] = min(90, 50 + home_score)
        elif away_score == max_score:
            prediction['result'] = '客胜'
            prediction['confidence'] = min(90, 50 + away_score)
        else:
            prediction['result'] = '平局'
            prediction['confidence'] = min(85, 50 + draw_score)
        
        # 如果没有足够理由，降低置信度
        if len(prediction['reasons']) < 2:
            prediction['confidence'] = max(40, prediction['confidence'] - 20)
        
        return prediction
    
    def print_predictions(self, predictions):
        """输出预测结果"""
        print("\n" + "─" * 120)
        print("📊 预测结果（按置信度排序）")
        print("─" * 120)
        
        for i, pred in enumerate(predictions, 1):
            conf = pred['confidence']
            conf_bar = '★' * (conf // 20) + '☆' * (5 - conf // 20)
            
            # 置信度颜色标记
            if conf >= 70:
                conf_mark = "🔥"
            elif conf >= 60:
                conf_mark = "✅"
            else:
                conf_mark = "⚠️"
            
            print(f"\n【{i}】{pred['league']} | {pred['match_time']}")
            print(f"    {pred['home_team']} vs {pred['away_team']}")
            print(f"    预测: {pred['result']} | 置信度: {conf}% {conf_bar} {conf_mark}")
            
            if pred['asian_prediction']:
                print(f"    亚盘: {pred['asian_prediction']}")
            if pred['ou_prediction']:
                print(f"    大小球: {pred['ou_prediction']}")
            
            if pred['reasons']:
                print(f"    理由: {' | '.join(pred['reasons'][:3])}")
            
            if pred['warnings']:
                print(f"    ⚠️ 风险: {', '.join(pred['warnings'])}")
    
    def print_recommendations(self, predictions):
        """输出推荐组合"""
        print("\n" + "=" * 120)
        print("🎯 今日推荐")
        print("=" * 120)
        
        # 高置信度推荐（>= 65%）
        high_conf = [p for p in predictions if p['confidence'] >= 65 and p['result'] != '难以判断']
        
        if high_conf:
            print("\n🔥 高置信度推荐:")
            print("─" * 80)
            for pred in high_conf[:5]:
                print(f"   {pred['league']} {pred['home_team']} vs {pred['away_team']}")
                print(f"   → {pred['result']} ({pred['confidence']}%)")
                if pred['asian_prediction']:
                    print(f"   → 亚盘: {pred['asian_prediction']}")
                print()
        
        # 平局推荐
        draw_preds = [p for p in predictions if p['result'] == '平局' and p['confidence'] >= 55]
        if draw_preds:
            print("\n🤝 平局推荐:")
            print("─" * 80)
            for pred in draw_preds[:3]:
                print(f"   {pred['league']} {pred['home_team']} vs {pred['away_team']} ({pred['confidence']}%)")
        
        # 大小球推荐
        over_preds = [p for p in predictions if p['ou_prediction'] == '大球']
        under_preds = [p for p in predictions if p['ou_prediction'] == '小球']
        
        if over_preds:
            print("\n⚽ 大球推荐:")
            print("─" * 80)
            for pred in over_preds[:3]:
                print(f"   {pred['league']} {pred['home_team']} vs {pred['away_team']}")
        
        if under_preds:
            print("\n🛡️ 小球推荐:")
            print("─" * 80)
            for pred in under_preds[:3]:
                print(f"   {pred['league']} {pred['home_team']} vs {pred['away_team']}")
        
        # 风险提示
        trap_preds = [p for p in predictions if '诱盘' in str(p.get('warnings', []))]
        if trap_preds:
            print("\n⚠️ 诱盘风险比赛（谨慎）:")
            print("─" * 80)
            for pred in trap_preds[:3]:
                print(f"   {pred['league']} {pred['home_team']} vs {pred['away_team']}")
        
        print("\n" + "=" * 120)
        print("⚠️ 免责声明：以上预测基于历史数据规律，仅供参考，不构成投注建议。请理性投注！")
        print("=" * 120)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='基于规律的比赛预测工具')
    parser.add_argument('--date', type=str, help='日期筛选，如：12-07 或 2024-12-07')
    
    args = parser.parse_args()
    
    predictor = PatternPredictor()
    predictor.predict_matches(date_filter=args.date)


if __name__ == '__main__':
    main()

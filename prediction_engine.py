"""
比赛预测引擎
基于历史数据和赔率分析，智能预测比赛结果
"""
from db_storage import MongoDBStorage
from utils import setup_logger
from collections import defaultdict


class PredictionEngine:
    """比赛预测引擎"""
    
    def __init__(self):
        self.storage = MongoDBStorage()
        self.logger = setup_logger()
    
    def analyze_team_form(self, team_name, last_n=10):
        """
        分析球队近期状态
        
        Args:
            team_name: 球队名称
            last_n: 最近N场比赛
            
        Returns:
            dict: 球队状态分析
        """
        all_matches = self.storage.get_matches(filters={'status': 2})
        team_matches = [m for m in all_matches 
                       if team_name in [m.get('home_team'), m.get('away_team')]]
        
        # 按时间排序
        team_matches.sort(key=lambda x: x.get('match_time', ''), reverse=True)
        team_matches = team_matches[:last_n]
        
        if not team_matches:
            return None
        
        wins = draws = losses = 0
        goals_scored = goals_conceded = 0
        over_count = under_count = 0
        
        for m in team_matches:
            try:
                home_score = int(m.get('home_score', 0))
                away_score = int(m.get('away_score', 0))
                is_home = m.get('home_team') == team_name
                
                if is_home:
                    goals_scored += home_score
                    goals_conceded += away_score
                    if home_score > away_score:
                        wins += 1
                    elif home_score == away_score:
                        draws += 1
                    else:
                        losses += 1
                else:
                    goals_scored += away_score
                    goals_conceded += home_score
                    if away_score > home_score:
                        wins += 1
                    elif away_score == home_score:
                        draws += 1
                    else:
                        losses += 1
                
                # 大小球统计
                total_line = float(m.get('ou_current_total') or m.get('ou_initial_total') or 2.5)
                actual_total = home_score + away_score
                if actual_total > total_line:
                    over_count += 1
                elif actual_total < total_line:
                    under_count += 1
                    
            except (ValueError, TypeError):
                continue
        
        total_games = len(team_matches)
        return {
            'team': team_name,
            'games': total_games,
            'wins': wins,
            'draws': draws,
            'losses': losses,
            'win_rate': wins / total_games if total_games > 0 else 0,
            'goals_scored': goals_scored,
            'goals_conceded': goals_conceded,
            'avg_goals_scored': goals_scored / total_games if total_games > 0 else 0,
            'avg_goals_conceded': goals_conceded / total_games if total_games > 0 else 0,
            'over_rate': over_count / total_games if total_games > 0 else 0,
            'under_rate': under_count / total_games if total_games > 0 else 0
        }
    
    def predict_match(self, match):
        """
        预测单场比赛结果
        
        Args:
            match: 比赛数据
            
        Returns:
            dict: 预测结果
        """
        match_id = match.get('match_id')
        home_team = match.get('home_team')
        away_team = match.get('away_team')
        
        self.logger.info(f"开始预测比赛 {match_id}: {home_team} vs {away_team}")
        
        # 1. 分析球队状态
        home_form = self.analyze_team_form(home_team, last_n=10)
        away_form = self.analyze_team_form(away_team, last_n=10)
        
        # 2. 分析赔率
        euro_win = self._safe_float(match.get('euro_current_win') or match.get('euro_initial_win'))
        euro_draw = self._safe_float(match.get('euro_current_draw') or match.get('euro_initial_draw'))
        euro_lose = self._safe_float(match.get('euro_current_lose') or match.get('euro_initial_lose'))
        
        asian_home_odds = self._safe_float(match.get('asian_current_home_odds') or match.get('asian_initial_home_odds'))
        asian_away_odds = self._safe_float(match.get('asian_current_away_odds') or match.get('asian_initial_away_odds'))
        asian_handicap = match.get('asian_current_handicap') or match.get('asian_initial_handicap')
        
        ou_total = self._safe_float(match.get('ou_current_total') or match.get('ou_initial_total'))
        ou_over_odds = self._safe_float(match.get('ou_current_over_odds') or match.get('ou_initial_over_odds'))
        ou_under_odds = self._safe_float(match.get('ou_current_under_odds') or match.get('ou_initial_under_odds'))
        
        # 3. 胜负预测
        win_prediction, win_confidence = self._predict_winner(
            home_form, away_form, euro_win, euro_draw, euro_lose
        )
        
        # 4. 亚盘预测
        asian_prediction, asian_confidence = self._predict_asian_handicap(
            home_form, away_form, asian_home_odds, asian_away_odds, asian_handicap
        )
        
        # 5. 大小球预测
        ou_prediction, ou_confidence = self._predict_over_under(
            home_form, away_form, ou_total, ou_over_odds, ou_under_odds
        )
        
        # 6. 比分预测
        predicted_score = self._predict_score(home_form, away_form, win_prediction)
        
        prediction = {
            'match_id': match_id,
            'league': match.get('league'),
            'match_time': match.get('match_time'),
            'home_team': home_team,
            'away_team': away_team,
            
            # 胜负预测
            'win_prediction': win_prediction,  # 'home', 'draw', 'away'
            'win_confidence': win_confidence,
            
            # 亚盘预测
            'asian_prediction': asian_prediction,  # 'home', 'away'
            'asian_handicap': asian_handicap,
            'asian_confidence': asian_confidence,
            
            # 大小球预测
            'ou_prediction': ou_prediction,  # 'over', 'under'
            'ou_total': ou_total,
            'ou_confidence': ou_confidence,
            
            # 比分预测
            'predicted_home_score': predicted_score[0],
            'predicted_away_score': predicted_score[1],
            
            # 分析数据
            'home_form': home_form,
            'away_form': away_form,
            
            # 赔率数据
            'euro_odds': {'win': euro_win, 'draw': euro_draw, 'lose': euro_lose},
            'asian_odds': {'home': asian_home_odds, 'away': asian_away_odds},
            'ou_odds': {'over': ou_over_odds, 'under': ou_under_odds}
        }
        
        self.logger.info(f"预测完成: {win_prediction} (置信度{win_confidence:.1f}%)")
        return prediction
    
    def _predict_winner(self, home_form, away_form, euro_win, euro_draw, euro_lose):
        """预测胜负"""
        confidence = 50.0
        prediction = 'draw'
        
        # 基于欧赔
        if euro_win and euro_draw and euro_lose:
            if euro_win < 1.5:
                prediction = 'home'
                confidence = 85.0
            elif euro_lose < 1.5:
                prediction = 'away'
                confidence = 85.0
            elif euro_win < 2.0:
                prediction = 'home'
                confidence = 70.0
            elif euro_lose < 2.0:
                prediction = 'away'
                confidence = 70.0
            elif euro_draw < 3.2:
                prediction = 'draw'
                confidence = 60.0
            elif euro_win < euro_lose:
                prediction = 'home'
                confidence = 55.0
            elif euro_lose < euro_win:
                prediction = 'away'
                confidence = 55.0
        
        # 结合球队状态调整
        if home_form and away_form:
            home_win_rate = home_form.get('win_rate', 0)
            away_win_rate = away_form.get('win_rate', 0)
            
            if home_win_rate - away_win_rate > 0.3:
                if prediction == 'home':
                    confidence = min(90, confidence + 10)
                elif prediction == 'away':
                    confidence = max(50, confidence - 10)
            elif away_win_rate - home_win_rate > 0.3:
                if prediction == 'away':
                    confidence = min(90, confidence + 10)
                elif prediction == 'home':
                    confidence = max(50, confidence - 10)
        
        return prediction, confidence
    
    def _predict_asian_handicap(self, home_form, away_form, home_odds, away_odds, handicap):
        """预测亚盘"""
        confidence = 50.0
        prediction = 'home'
        
        if home_odds and away_odds:
            if home_odds > 1.0 and away_odds < 0.85:
                prediction = 'home'
                confidence = 70.0
            elif away_odds > 1.0 and home_odds < 0.85:
                prediction = 'away'
                confidence = 70.0
            elif home_odds > away_odds:
                prediction = 'home'
                confidence = 60.0
            else:
                prediction = 'away'
                confidence = 60.0
        
        return prediction, confidence
    
    def _predict_over_under(self, home_form, away_form, total, over_odds, under_odds):
        """预测大小球"""
        confidence = 50.0
        prediction = 'over'
        
        # 基于球队大小球走势
        if home_form and away_form:
            avg_over_rate = (home_form.get('over_rate', 0.5) + away_form.get('over_rate', 0.5)) / 2
            
            if avg_over_rate > 0.7:
                prediction = 'over'
                confidence = 70.0
            elif avg_over_rate < 0.3:
                prediction = 'under'
                confidence = 70.0
            elif avg_over_rate > 0.55:
                prediction = 'over'
                confidence = 60.0
            elif avg_over_rate < 0.45:
                prediction = 'under'
                confidence = 60.0
        
        # 基于赔率调整
        if over_odds and under_odds:
            if over_odds > 1.0 and under_odds < 0.85:
                if prediction == 'over':
                    confidence = min(75, confidence + 10)
            elif under_odds > 1.0 and over_odds < 0.85:
                if prediction == 'under':
                    confidence = min(75, confidence + 10)
        
        return prediction, confidence
    
    def _predict_score(self, home_form, away_form, win_prediction):
        """预测比分"""
        if not home_form or not away_form:
            return (1, 1) if win_prediction == 'draw' else (2, 0) if win_prediction == 'home' else (0, 2)
        
        home_avg = home_form.get('avg_goals_scored', 1.5)
        away_avg = away_form.get('avg_goals_scored', 1.5)
        
        home_score = round(home_avg)
        away_score = round(away_avg)
        
        # 根据胜负预测调整
        if win_prediction == 'home':
            if home_score <= away_score:
                home_score = away_score + 1
        elif win_prediction == 'away':
            if away_score <= home_score:
                away_score = home_score + 1
        elif win_prediction == 'draw':
            home_score = away_score = max(1, min(home_score, away_score))
        
        return (home_score, away_score)
    
    def _safe_float(self, value):
        """安全转换为float"""
        try:
            return float(value) if value else None
        except (ValueError, TypeError):
            return None

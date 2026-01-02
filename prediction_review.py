"""
预测复盘模块
对比预测结果与实际结果，生成复盘报告
"""
from db_storage import MongoDBStorage
from utils import setup_logger


class PredictionReviewer:
    """预测复盘器"""
    
    def __init__(self):
        self.storage = MongoDBStorage()
        self.logger = setup_logger()
    
    def review_match(self, match_id):
        """
        复盘单场比赛预测
        
        Args:
            match_id: 比赛ID
            
        Returns:
            dict: 复盘结果
        """
        # 获取预测数据
        predictions = self.storage.get_predictions(filters={'match_id': match_id})
        if not predictions:
            self.logger.warning(f"未找到比赛 {match_id} 的预测数据")
            return None
        
        prediction = predictions[0]
        
        # 获取实际比赛结果
        match = self.storage.get_match_by_id(match_id)
        if not match:
            self.logger.warning(f"未找到比赛 {match_id} 的数据")
            return None
        
        # 检查比赛是否完场
        if match.get('status') != 2:
            self.logger.info(f"比赛 {match_id} 尚未完场，无法复盘")
            return None
        
        home_score = match.get('home_score')
        away_score = match.get('away_score')
        
        if not home_score or not away_score or home_score == '-' or away_score == '-':
            self.logger.warning(f"比赛 {match_id} 比分数据不完整")
            return None
        
        try:
            home_score = int(home_score)
            away_score = int(away_score)
        except (ValueError, TypeError):
            self.logger.warning(f"比赛 {match_id} 比分数据无效")
            return None
        
        # 开始复盘
        self.logger.info(f"开始复盘比赛 {match_id}: {match.get('home_team')} {home_score}-{away_score} {match.get('away_team')}")
        
        # 判断是手动预测还是AI预测
        is_manual = prediction.get('manual') or prediction.get('source') == 'manual'
        
        # 1. 复盘胜负预测
        actual_winner = self._get_actual_winner(home_score, away_score)
        # 优先使用手动预测，其次使用AI预测
        win_prediction = prediction.get('manual_win_prediction') or prediction.get('win_prediction')
        win_correct = (win_prediction == actual_winner) if win_prediction else None
        
        # 2. 复盘亚盘预测
        asian_prediction = prediction.get('manual_asian_prediction') or prediction.get('asian_prediction')
        asian_handicap = prediction.get('manual_asian_handicap') or prediction.get('asian_handicap')
        asian_correct = self._check_asian_handicap(
            home_score, away_score, 
            asian_prediction, 
            asian_handicap
        ) if asian_prediction else None
        
        # 3. 复盘大小球预测
        ou_prediction = prediction.get('ou_prediction')  # 手动预测暂不支持大小球
        ou_total = prediction.get('ou_total')
        ou_correct = self._check_over_under(
            home_score, away_score,
            ou_prediction,
            ou_total
        ) if ou_prediction else None
        
        # 4. 复盘比分预测
        predicted_home = prediction.get('predicted_home_score')
        predicted_away = prediction.get('predicted_away_score')
        score_correct = (
            predicted_home == home_score and
            predicted_away == away_score
        ) if (predicted_home is not None and predicted_away is not None) else None
        
        # 计算总体准确度（只计算有预测的项目）
        predictions_made = [win_correct, asian_correct, ou_correct, score_correct]
        valid_predictions = [p for p in predictions_made if p is not None]
        
        if not valid_predictions:
            self.logger.warning(f"比赛 {match_id} 没有可复盘的预测项")
            return None
        
        correct_count = sum(1 for p in valid_predictions if p)
        total_predictions = len(valid_predictions)
        accuracy = correct_count / total_predictions * 100 if total_predictions > 0 else 0
        
        review_result = {
            'actual_home_score': home_score,
            'actual_away_score': away_score,
            'actual_winner': actual_winner,
            
            'win_correct': win_correct if win_correct is not None else False,
            'asian_correct': asian_correct if asian_correct is not None else False,
            'ou_correct': ou_correct if ou_correct is not None else False,
            'score_correct': score_correct if score_correct is not None else False,
            
            'accuracy': accuracy,
            'correct_count': correct_count,
            'total_predictions': total_predictions,
            'is_manual': is_manual
        }
        
        # 保存复盘结果
        self.storage.update_prediction_review(match_id, review_result)
        
        self.logger.info(f"复盘完成: 准确度 {accuracy:.1f}% ({correct_count}/{total_predictions})")
        return review_result
    
    def review_all_finished_matches(self):
        """
        复盘所有已完场但未复盘的比赛
        
        Returns:
            list: 复盘结果列表
        """
        self.logger.info("开始批量复盘...")
        
        # 获取所有未复盘的预测
        unreviewed_predictions = self.storage.get_predictions(filters={'is_reviewed': False})
        
        if not unreviewed_predictions:
            self.logger.info("没有需要复盘的预测")
            return []
        
        self.logger.info(f"找到 {len(unreviewed_predictions)} 场未复盘的预测")
        
        results = []
        reviewed_count = 0
        
        for prediction in unreviewed_predictions:
            match_id = prediction.get('match_id')
            
            # 检查比赛是否完场
            match = self.storage.get_match_by_id(match_id)
            if not match or match.get('status') != 2:
                continue
            
            # 复盘
            result = self.review_match(match_id)
            if result:
                results.append({
                    'match_id': match_id,
                    'home_team': match.get('home_team'),
                    'away_team': match.get('away_team'),
                    'league': match.get('league'),
                    **result
                })
                reviewed_count += 1
        
        self.logger.info(f"批量复盘完成: {reviewed_count} 场")
        return results
    
    def generate_summary_report(self, days=7):
        """
        生成复盘汇总报告
        
        Args:
            days: 统计最近N天
            
        Returns:
            dict: 汇总报告
        """
        from datetime import datetime, timedelta
        
        # 获取最近N天的已复盘预测
        cutoff_date = datetime.now() - timedelta(days=days)
        all_predictions = self.storage.get_predictions(filters={'is_reviewed': True})
        
        recent_predictions = [
            p for p in all_predictions 
            if p.get('review_date') and p.get('review_date') > cutoff_date
        ]
        
        if not recent_predictions:
            self.logger.warning(f"最近{days}天没有复盘数据")
            return None
        
        total_matches = len(recent_predictions)
        
        # 统计各项准确率
        win_correct = sum(1 for p in recent_predictions if p.get('win_correct'))
        asian_correct = sum(1 for p in recent_predictions if p.get('asian_correct'))
        ou_correct = sum(1 for p in recent_predictions if p.get('ou_correct'))
        score_correct = sum(1 for p in recent_predictions if p.get('score_correct'))
        
        # 统计平均准确度
        avg_accuracy = sum(p.get('accuracy', 0) for p in recent_predictions) / total_matches
        
        # 按联赛统计
        league_stats = {}
        for p in recent_predictions:
            league = p.get('league', '未知')
            if league not in league_stats:
                league_stats[league] = {
                    'total': 0,
                    'win_correct': 0,
                    'asian_correct': 0,
                    'ou_correct': 0
                }
            league_stats[league]['total'] += 1
            if p.get('win_correct'):
                league_stats[league]['win_correct'] += 1
            if p.get('asian_correct'):
                league_stats[league]['asian_correct'] += 1
            if p.get('ou_correct'):
                league_stats[league]['ou_correct'] += 1
        
        report = {
            'period': f'最近{days}天',
            'total_matches': total_matches,
            'win_accuracy': win_correct / total_matches * 100,
            'asian_accuracy': asian_correct / total_matches * 100,
            'ou_accuracy': ou_correct / total_matches * 100,
            'score_accuracy': score_correct / total_matches * 100,
            'avg_accuracy': avg_accuracy,
            'league_stats': league_stats
        }
        
        return report
    
    def _get_actual_winner(self, home_score, away_score):
        """获取实际胜负结果"""
        if home_score > away_score:
            return 'home'
        elif home_score < away_score:
            return 'away'
        else:
            return 'draw'
    
    def _check_asian_handicap(self, home_score, away_score, prediction, handicap):
        """检查亚盘预测是否正确"""
        if not prediction or not handicap:
            return False
        
        # 解析盘口
        handicap_value = self._parse_handicap(handicap)
        if handicap_value is None:
            return False
        
        # 计算让球后的比分
        adjusted_home_score = home_score + handicap_value
        
        if adjusted_home_score > away_score:
            actual_result = 'home'
        elif adjusted_home_score < away_score:
            actual_result = 'away'
        else:
            return False  # 走盘不算对也不算错
        
        return prediction == actual_result
    
    def _check_over_under(self, home_score, away_score, prediction, total):
        """检查大小球预测是否正确"""
        if not prediction or not total:
            return False
        
        try:
            total_line = float(total)
        except (ValueError, TypeError):
            return False
        
        actual_total = home_score + away_score
        
        if actual_total > total_line:
            actual_result = 'over'
        elif actual_total < total_line:
            actual_result = 'under'
        else:
            return False  # 走盘
        
        return prediction == actual_result
    
    def _parse_handicap(self, handicap_str):
        """解析亚盘盘口为数字"""
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
            try:
                value = float(clean_str)
            except ValueError:
                return None
        
        return -value if is_receiver else value

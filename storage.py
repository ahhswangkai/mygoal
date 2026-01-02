"""
数据存储模块
"""
import os
import json
import pandas as pd
from datetime import datetime
from config import DATA_DIR, EXPORT_FORMAT
from utils import setup_logger


class DataStorage:
    """数据存储类"""
    
    def __init__(self):
        """初始化存储"""
        self.logger = setup_logger()
        self.data_dir = DATA_DIR
        
    def _get_filename(self, data_type, format_type=None):
        """
        生成文件名
        
        Args:
            data_type: 数据类型（matches/odds）
            format_type: 文件格式（csv/json/excel）
            
        Returns:
            filename: 完整文件路径
        """
        if format_type is None:
            format_type = EXPORT_FORMAT
            
        date_str = datetime.now().strftime('%Y%m%d')
        time_str = datetime.now().strftime('%H%M%S')
        # 添加微秒以避免文件名冲突
        microsecond_str = datetime.now().strftime('%f')[:3]
        
        if format_type == 'csv':
            ext = 'csv'
        elif format_type == 'json':
            ext = 'json'
        elif format_type == 'excel':
            ext = 'xlsx'
        else:
            ext = 'csv'
            
        filename = f"{data_type}_{date_str}_{time_str}_{microsecond_str}.{ext}"
        return os.path.join(self.data_dir, filename)
    
    def save_matches(self, matches, format_type=None):
        """
        保存比赛数据
        
        Args:
            matches: 比赛数据列表
            format_type: 保存格式
            
        Returns:
            filepath: 保存的文件路径
        """
        if not matches:
            self.logger.warning("没有比赛数据需要保存")
            return None
            
        filepath = self._get_filename('matches', format_type)
        
        try:
            df = pd.DataFrame(matches)
            
            if format_type == 'json' or (format_type is None and EXPORT_FORMAT == 'json'):
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(matches, f, ensure_ascii=False, indent=2)
            elif format_type == 'excel' or (format_type is None and EXPORT_FORMAT == 'excel'):
                df.to_excel(filepath, index=False, engine='openpyxl')
            else:  # CSV
                df.to_csv(filepath, index=False, encoding='utf-8-sig')
                
            self.logger.info(f"比赛数据已保存到: {filepath}")
            return filepath
            
        except Exception as e:
            self.logger.error(f"保存比赛数据失败: {str(e)}")
            return None
    
    def save_odds(self, odds_data, match_id=None, format_type=None):
        """
        保存赔率数据
        
        Args:
            odds_data: 赔率数据列表
            match_id: 比赛ID（可选）
            format_type: 保存格式
            
        Returns:
            filepath: 保存的文件路径
        """
        if not odds_data:
            self.logger.warning("没有赔率数据需要保存")
            return None
            
        data_type = f"odds_{match_id}" if match_id else "odds"
        filepath = self._get_filename(data_type, format_type)
        
        try:
            df = pd.DataFrame(odds_data)
            
            if format_type == 'json' or (format_type is None and EXPORT_FORMAT == 'json'):
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(odds_data, f, ensure_ascii=False, indent=2)
            elif format_type == 'excel' or (format_type is None and EXPORT_FORMAT == 'excel'):
                df.to_excel(filepath, index=False, engine='openpyxl')
            else:  # CSV
                df.to_csv(filepath, index=False, encoding='utf-8-sig')
                
            self.logger.info(f"赔率数据已保存到: {filepath}")
            return filepath
            
        except Exception as e:
            self.logger.error(f"保存赔率数据失败: {str(e)}")
            return None
    
    def save_combined_data(self, matches, odds_dict, format_type=None):
        """
        保存组合数据（比赛+赔率）
        
        Args:
            matches: 比赛数据列表
            odds_dict: 赔率数据字典 {match_id: odds_list}
            format_type: 保存格式
            
        Returns:
            filepath: 保存的文件路径
        """
        if not matches:
            self.logger.warning("没有数据需要保存")
            return None
            
        filepath = self._get_filename('combined', format_type)
        
        try:
            # 将赔率数据合并到比赛数据中
            for match in matches:
                match_id = match.get('match_id', '')
                if match_id in odds_dict:
                    # 赔率数据是字典格式，包含 euro_odds, asian_handicap 等
                    odds_data = odds_dict[match_id]
                    # 将赔率数据平展到比赛数据中
                    if isinstance(odds_data, dict):
                        # 欧赔（即时盘 + 初盘）
                        if odds_data.get('euro_odds') and len(odds_data['euro_odds']) > 0:
                            euro = odds_data['euro_odds'][0]
                            # 即时盘
                            match['euro_current_win'] = euro.get('current_win', '')
                            match['euro_current_draw'] = euro.get('current_draw', '')
                            match['euro_current_lose'] = euro.get('current_lose', '')
                            # 初盘
                            match['euro_initial_win'] = euro.get('initial_win', '')
                            match['euro_initial_draw'] = euro.get('initial_draw', '')
                            match['euro_initial_lose'] = euro.get('initial_lose', '')
                            # 兼容旧字段（即时盘）
                            match['euro_win'] = euro.get('win', '')
                            match['euro_draw'] = euro.get('draw', '')
                            match['euro_lose'] = euro.get('lose', '')
                        
                        # 亚盘（即时盘 + 初盘）
                        if odds_data.get('asian_handicap') and len(odds_data['asian_handicap']) > 0:
                            asian = odds_data['asian_handicap'][0]
                            # 即时盘
                            match['asian_current_home_odds'] = asian.get('current_home_odds', '')
                            match['asian_current_handicap'] = asian.get('current_handicap', '')
                            match['asian_current_away_odds'] = asian.get('current_away_odds', '')
                            # 初盘
                            match['asian_initial_home_odds'] = asian.get('initial_home_odds', '')
                            match['asian_initial_handicap'] = asian.get('initial_handicap', '')
                            match['asian_initial_away_odds'] = asian.get('initial_away_odds', '')
                            # 兼容旧字段（即时盘）
                            match['asian_home_odds'] = asian.get('home_odds', '')
                            match['asian_handicap'] = asian.get('handicap', '')
                            match['asian_away_odds'] = asian.get('away_odds', '')
                        
                        # 大小球（即时盘 + 初盘）
                        if odds_data.get('over_under') and len(odds_data['over_under']) > 0:
                            over_under = odds_data['over_under'][0]
                            # 即时盘
                            match['ou_current_over_odds'] = over_under.get('current_over_odds', '')
                            match['ou_current_total'] = over_under.get('current_total', '')
                            match['ou_current_under_odds'] = over_under.get('current_under_odds', '')
                            # 初盘
                            match['ou_initial_over_odds'] = over_under.get('initial_over_odds', '')
                            match['ou_initial_total'] = over_under.get('initial_total', '')
                            match['ou_initial_under_odds'] = over_under.get('initial_under_odds', '')
                            # 兼容旧字段（即时盘）
                            match['over_odds'] = over_under.get('over_odds', '')
                            match['total_goals'] = over_under.get('total', '')
                            match['under_odds'] = over_under.get('under_odds', '')
                else:
                    # 欧赔空值
                    match['euro_current_win'] = ''
                    match['euro_current_draw'] = ''
                    match['euro_current_lose'] = ''
                    match['euro_initial_win'] = ''
                    match['euro_initial_draw'] = ''
                    match['euro_initial_lose'] = ''
                    match['euro_win'] = ''
                    match['euro_draw'] = ''
                    match['euro_lose'] = ''
                    # 亚盘空值
                    match['asian_current_home_odds'] = ''
                    match['asian_current_handicap'] = ''
                    match['asian_current_away_odds'] = ''
                    match['asian_initial_home_odds'] = ''
                    match['asian_initial_handicap'] = ''
                    match['asian_initial_away_odds'] = ''
                    match['asian_home_odds'] = ''
                    match['asian_handicap'] = ''
                    match['asian_away_odds'] = ''
                    # 大小球空值
                    match['ou_current_over_odds'] = ''
                    match['ou_current_total'] = ''
                    match['ou_current_under_odds'] = ''
                    match['ou_initial_over_odds'] = ''
                    match['ou_initial_total'] = ''
                    match['ou_initial_under_odds'] = ''
                    match['over_odds'] = ''
                    match['total_goals'] = ''
                    match['under_odds'] = ''
            
            if format_type == 'json' or (format_type is None and EXPORT_FORMAT == 'json'):
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(matches, f, ensure_ascii=False, indent=2)
            else:
                # CSV和Excel格式直接保存，赔率已经平展到match中
                df = pd.DataFrame(matches)
                if format_type == 'excel' or (format_type is None and EXPORT_FORMAT == 'excel'):
                    df.to_excel(filepath, index=False, engine='openpyxl')
                else:
                    df.to_csv(filepath, index=False, encoding='utf-8-sig')
                
            self.logger.info(f"组合数据已保存到: {filepath}")
            return filepath
            
        except Exception as e:
            self.logger.error(f"保存组合数据失败: {str(e)}")
            return None

"""
MongoDB数据存储模块
"""
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure, DuplicateKeyError
from datetime import datetime
from utils import setup_logger
import os
import certifi


class MongoDBStorage:
    """MongoDB数据存储类"""
    
    def __init__(self, connection_string=None, database_name='football_data'):
        """
        初始化MongoDB连接
        
        Args:
            connection_string: MongoDB连接字符串，默认为本地MongoDB
            database_name: 数据库名称
        """
        self.logger = setup_logger()
        
        # 从环境变量或参数获取连接字符串
        if connection_string is None:
            connection_string = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
        
        try:
            if connection_string.startswith('mongodb+srv') or 'tls=true' in connection_string:
                self.client = MongoClient(connection_string, serverSelectionTimeoutMS=5000, tlsCAFile=certifi.where())
            else:
                self.client = MongoClient(connection_string, serverSelectionTimeoutMS=5000)
            # 测试连接
            self.client.admin.command('ping')
            self.logger.info(f"成功连接到MongoDB: {connection_string}")
        except ConnectionFailure as e:
            self.logger.error(f"MongoDB连接失败: {str(e)}")
            raise
        
        database_name = os.getenv('MONGODB_DATABASE', database_name)
        self.db = self.client[database_name]
        self.matches_collection = self.db['matches']
        self.odds_collection = self.db['odds']
        self.predictions_collection = self.db['predictions']
        
        # 创建索引
        self._create_indexes()
    
    def _create_indexes(self):
        """创建数据库索引"""
        try:
            # 比赛表索引
            self.matches_collection.create_index([('match_id', ASCENDING)], unique=True)
            self.matches_collection.create_index([('league', ASCENDING)])
            self.matches_collection.create_index([('status', ASCENDING)])
            self.matches_collection.create_index([('match_time', DESCENDING)])
            self.matches_collection.create_index([('created_at', DESCENDING)])
            
            # 赔率表索引
            self.odds_collection.create_index([('match_id', ASCENDING)])
            self.odds_collection.create_index([('created_at', DESCENDING)])
            
            # 预测表索引
            self.predictions_collection.create_index([('match_id', ASCENDING)], unique=True)
            self.predictions_collection.create_index([('predict_date', DESCENDING)])
            self.predictions_collection.create_index([('is_reviewed', ASCENDING)])
            
            self.logger.info("数据库索引创建成功")
        except Exception as e:
            self.logger.warning(f"创建索引时出现警告: {str(e)}")
    
    def save_match(self, match_data):
        """
        保存单场比赛数据
        
        Args:
            match_data: 比赛数据字典
            
        Returns:
            result: 插入结果
        """
        try:
            # 添加时间戳
            match_data['created_at'] = datetime.now()
            match_data['updated_at'] = datetime.now()
            
            # 使用upsert：如果存在则更新，不存在则插入
            result = self.matches_collection.update_one(
                {'match_id': match_data.get('match_id')},
                {'$set': match_data},
                upsert=True
            )
            
            if result.upserted_id:
                self.logger.info(f"新增比赛数据: {match_data.get('match_id')}")
            else:
                self.logger.info(f"更新比赛数据: {match_data.get('match_id')}")
                
            return result
            
        except Exception as e:
            self.logger.error(f"保存比赛数据失败: {str(e)}")
            return None
    
    def save_matches(self, matches):
        """
        批量保存比赛数据
        
        Args:
            matches: 比赛数据列表
            
        Returns:
            count: 保存成功的数量
        """
        if not matches:
            self.logger.warning("没有比赛数据需要保存")
            return 0
        
        success_count = 0
        for match in matches:
            if self.save_match(match):
                success_count += 1
        
        self.logger.info(f"批量保存比赛数据完成: {success_count}/{len(matches)}")
        return success_count
    
    def save_odds(self, match_id, odds_data):
        """
        保存赔率数据
        
        Args:
            match_id: 比赛ID
            odds_data: 赔率数据字典
            
        Returns:
            result: 插入结果
        """
        try:
            # 构建赔率文档
            odds_doc = {
                'match_id': match_id,
                'odds_data': odds_data,
                'created_at': datetime.now(),
                'updated_at': datetime.now()
            }
            
            # 使用upsert
            result = self.odds_collection.update_one(
                {'match_id': match_id},
                {'$set': odds_doc},
                upsert=True
            )
            
            if result.upserted_id:
                self.logger.info(f"新增赔率数据: {match_id}")
            else:
                self.logger.info(f"更新赔率数据: {match_id}")
                
            # 同时更新比赛表中的赔率字段
            self._update_match_odds(match_id, odds_data)
            
            return result
            
        except Exception as e:
            self.logger.error(f"保存赔率数据失败: {str(e)}")
            return None
    
    def _update_match_odds(self, match_id, odds_data):
        """
        更新比赛表中的赔率字段（扁平化存储，方便查询）
        
        Args:
            match_id: 比赛ID
            odds_data: 赔率数据
        """
        try:
            update_fields = {'updated_at': datetime.now()}
            
            # 欧赔
            if odds_data.get('euro_odds') and len(odds_data['euro_odds']) > 0:
                euro = odds_data['euro_odds'][0]
                update_fields.update({
                    'euro_current_win': euro.get('current_win', ''),
                    'euro_current_draw': euro.get('current_draw', ''),
                    'euro_current_lose': euro.get('current_lose', ''),
                    'euro_initial_win': euro.get('initial_win', ''),
                    'euro_initial_draw': euro.get('initial_draw', ''),
                    'euro_initial_lose': euro.get('initial_lose', '')
                })
            
            # 亚盘
            if odds_data.get('asian_handicap') and len(odds_data['asian_handicap']) > 0:
                asian = odds_data['asian_handicap'][0]
                update_fields.update({
                    'asian_current_home_odds': asian.get('current_home_odds', ''),
                    'asian_current_handicap': asian.get('current_handicap', ''),
                    'asian_current_away_odds': asian.get('current_away_odds', ''),
                    'asian_initial_home_odds': asian.get('initial_home_odds', ''),
                    'asian_initial_handicap': asian.get('initial_handicap', ''),
                    'asian_initial_away_odds': asian.get('initial_away_odds', '')
                })
            
            # 大小球
            if odds_data.get('over_under') and len(odds_data['over_under']) > 0:
                ou = odds_data['over_under'][0]
                update_fields.update({
                    'ou_current_over_odds': ou.get('current_over_odds', ''),
                    'ou_current_total': ou.get('current_total', ''),
                    'ou_current_under_odds': ou.get('current_under_odds', ''),
                    'ou_initial_over_odds': ou.get('initial_over_odds', ''),
                    'ou_initial_total': ou.get('initial_total', ''),
                    'ou_initial_under_odds': ou.get('initial_under_odds', '')
                })
            
            # 让球指数
            if odds_data.get('handicap_index'):
                hi = odds_data['handicap_index']
                update_fields.update({
                    'hi_handicap_value': hi.get('handicap_value', ''),
                    'hi_current_home_odds': hi.get('current_home_odds', ''),
                    'hi_current_draw_odds': hi.get('current_draw_odds', ''),
                    'hi_current_away_odds': hi.get('current_away_odds', ''),
                    'hi_initial_home_odds': hi.get('initial_home_odds', ''),
                    'hi_initial_draw_odds': hi.get('initial_draw_odds', ''),
                    'hi_initial_away_odds': hi.get('initial_away_odds', '')
                })
            
            # 计算盘口变动标签
            asian_label = self._calc_asian_movement_label(update_fields)
            if asian_label:
                update_fields['asian_movement_label'] = asian_label
            
            self.matches_collection.update_one(
                {'match_id': match_id},
                {'$set': update_fields}
            )
            
        except Exception as e:
            self.logger.error(f"更新比赛赔率字段失败: {str(e)}")
    
    def _calc_asian_movement_label(self, fields):
        """
        计算亚盘变动标签
        
        升盘降水: 盘口上升 + 主水下降 (机构强力看好主队)
        升盘升水: 盘口上升 + 主水上升 (可能诱盘)
        降盘降水: 盘口下降 + 主水下降 (机构强力看好客队)
        降盘升水: 盘口下降 + 主水上升 (可能诱盘)
        
        Returns:
            label: 变动标签字符串，或 None
        """
        import re
        
        def parse_handicap(h):
            if not h:
                return None
            handicap_map = {
                '平手': 0, '平/半': 0.25, '平手/半球': 0.25,
                '半球': 0.5, '半/一': 0.75, '半球/一球': 0.75,
                '一球': 1.0, '一/球半': 1.25, '一球/球半': 1.25,
                '球半': 1.5, '球半/两': 1.75, '球半/两球': 1.75,
                '两球': 2.0, '两/两球半': 2.25, '两球半': 2.5
            }
            clean = str(h).replace('受', '')
            is_receiver = '受' in str(h)
            
            if clean in handicap_map:
                val = handicap_map[clean]
                return -val if is_receiver else val
            
            nums = re.findall(r'\d+\.?\d*', str(h))
            if nums:
                val = float(nums[0])
                return -val if is_receiver else val
            return None
        
        def safe_float(x):
            try:
                return float(x)
            except:
                return None
        
        h_init = parse_handicap(fields.get('asian_initial_handicap'))
        h_curr = parse_handicap(fields.get('asian_current_handicap'))
        home_init = safe_float(fields.get('asian_initial_home_odds'))
        home_curr = safe_float(fields.get('asian_current_home_odds'))
        
        if h_init is None or h_curr is None or home_init is None or home_curr is None:
            return None
        
        handicap_change = h_curr - h_init  # 正=升盘，负=降盘
        water_change = home_curr - home_init  # 正=升水，负=降水
        
        # 阈值：盘口变化超过0.01，水位变化超过0.02才算有变化
        if abs(handicap_change) < 0.01 and abs(water_change) < 0.02:
            return '无变化'
        
        if handicap_change > 0.01:
            if water_change < -0.02:
                return '升盘降水'
            elif water_change > 0.02:
                return '升盘升水'
            else:
                return '升盘'
        elif handicap_change < -0.01:
            if water_change < -0.02:
                return '降盘降水'
            elif water_change > 0.02:
                return '降盘升水'
            else:
                return '降盘'
        else:
            # 盘口没变，只有水位变化
            if water_change < -0.02:
                return '降水'
            elif water_change > 0.02:
                return '升水'
            else:
                return '无变化'
    
    def get_match_by_id(self, match_id):
        """
        根据ID获取比赛数据
        
        Args:
            match_id: 比赛ID
            
        Returns:
            match: 比赛数据字典
        """
        try:
            match = self.matches_collection.find_one({'match_id': match_id}, {'_id': 0})
            return match
        except Exception as e:
            self.logger.error(f"获取比赛数据失败: {str(e)}")
            return None
    
    def get_matches(self, filters=None, limit=None, sort_by='match_time', sort_order=-1):
        """
        获取比赛列表
        
        Args:
            filters: 筛选条件字典
            limit: 返回数量限制
            sort_by: 排序字段
            sort_order: 排序方向（1升序，-1降序）
            
        Returns:
            matches: 比赛数据列表
        """
        try:
            query = filters or {}
            cursor = self.matches_collection.find(query, {'_id': 0})
            
            # 排序
            cursor = cursor.sort(sort_by, sort_order)
            
            # 限制数量
            if limit:
                cursor = cursor.limit(limit)
            
            matches = list(cursor)
            return matches
            
        except Exception as e:
            self.logger.error(f"获取比赛列表失败: {str(e)}")
            return []
    
    def get_matches_by_league(self, league):
        """
        按联赛获取比赛
        
        Args:
            league: 联赛名称
            
        Returns:
            matches: 比赛列表
        """
        return self.get_matches(filters={'league': league})
    
    def get_matches_by_status(self, status):
        """
        按状态获取比赛
        
        Args:
            status: 比赛状态
            
        Returns:
            matches: 比赛列表
        """
        return self.get_matches(filters={'status': status})
    
    def get_all_leagues(self):
        """
        获取所有联赛列表
        
        Returns:
            leagues: 联赛名称列表
        """
        try:
            leagues = self.matches_collection.distinct('league')
            return sorted([l for l in leagues if l])
        except Exception as e:
            self.logger.error(f"获取联赛列表失败: {str(e)}")
            return []
    
    def get_stats(self):
        """
        获取统计信息
        
        Returns:
            stats: 统计数据字典
        """
        try:
            # 总比赛数
            total_matches = self.matches_collection.count_documents({})
            
            # 联赛数
            total_leagues = len(self.get_all_leagues())
            
            # 按状态统计
            status_stats = {}
            status_map = {0: '未开始', 1: '进行中', 2: '完场'}
            status_pipeline = [
                {'$group': {'_id': '$status', 'count': {'$sum': 1}}}
            ]
            for item in self.matches_collection.aggregate(status_pipeline):
                status_code = item['_id']
                if status_code is not None:
                    # 转换为中文名称
                    status_name = status_map.get(status_code, str(status_code))
                    status_stats[status_name] = item['count']
            
            # 按联赛统计
            league_stats = {}
            league_pipeline = [
                {'$group': {'_id': '$league', 'count': {'$sum': 1}}},
                {'$sort': {'count': -1}}
            ]
            for item in self.matches_collection.aggregate(league_pipeline):
                if item['_id']:
                    league_stats[item['_id']] = item['count']
            
            return {
                'total_matches': total_matches,
                'total_leagues': total_leagues,
                'status_stats': status_stats,
                'league_stats': league_stats
            }
            
        except Exception as e:
            self.logger.error(f"获取统计信息失败: {str(e)}")
            return {
                'total_matches': 0,
                'total_leagues': 0,
                'status_stats': {},
                'league_stats': {}
            }
    
    def get_odds(self, match_id):
        """
        获取赔率数据
        
        Args:
            match_id: 比赛ID
            
        Returns:
            odds: 赔率数据
        """
        try:
            odds_doc = self.odds_collection.find_one({'match_id': match_id}, {'_id': 0})
            if odds_doc:
                return odds_doc.get('odds_data', {})
            return None
        except Exception as e:
            self.logger.error(f"获取赔率数据失败: {str(e)}")
            return None
    
    def delete_match(self, match_id):
        """
        删除比赛数据
        
        Args:
            match_id: 比赛ID
            
        Returns:
            success: 是否成功
        """
        try:
            # 删除比赛
            result1 = self.matches_collection.delete_one({'match_id': match_id})
            # 删除赔率
            result2 = self.odds_collection.delete_one({'match_id': match_id})
            
            self.logger.info(f"删除比赛数据: {match_id}")
            return result1.deleted_count > 0 or result2.deleted_count > 0
            
        except Exception as e:
            self.logger.error(f"删除比赛数据失败: {str(e)}")
            return False
    
    def clear_all_data(self):
        """
        清空所有数据（慎用）
        
        Returns:
            success: 是否成功
        """
        try:
            self.matches_collection.delete_many({})
            self.odds_collection.delete_many({})
            self.logger.warning("已清空所有数据")
            return True
        except Exception as e:
            self.logger.error(f"清空数据失败: {str(e)}")
            return False
    
    def save_prediction(self, prediction_data):
        """
        保存比赛预测结果
        
        Args:
            prediction_data: 预测数据字典
            
        Returns:
            result: 插入结果
        """
        try:
            # 添加时间戳
            prediction_data['predict_date'] = datetime.now()
            prediction_data['is_reviewed'] = False
            
            # 使用upsert
            result = self.predictions_collection.update_one(
                {'match_id': prediction_data.get('match_id')},
                {'$set': prediction_data},
                upsert=True
            )
            
            if result.upserted_id:
                self.logger.info(f"新增预测数据: {prediction_data.get('match_id')}")
            else:
                self.logger.info(f"更新预测数据: {prediction_data.get('match_id')}")
                
            return result
            
        except Exception as e:
            self.logger.error(f"保存预测数据失败: {str(e)}")
            return None
    
    def get_predictions(self, filters=None, limit=None):
        """
        获取预测列表
        
        Args:
            filters: 筛选条件
            limit: 返回数量限制
            
        Returns:
            predictions: 预测列表
        """
        try:
            query = filters or {}
            cursor = self.predictions_collection.find(query, {'_id': 0})
            cursor = cursor.sort('predict_date', DESCENDING)
            
            if limit:
                cursor = cursor.limit(limit)
            
            return list(cursor)
            
        except Exception as e:
            self.logger.error(f"获取预测列表失败: {str(e)}")
            return []
    
    def update_prediction_review(self, match_id, review_data):
        """
        更新预测复盘结果
        
        Args:
            match_id: 比赛ID
            review_data: 复盘数据
            
        Returns:
            success: 是否成功
        """
        try:
            review_data['is_reviewed'] = True
            review_data['review_date'] = datetime.now()
            
            result = self.predictions_collection.update_one(
                {'match_id': match_id},
                {'$set': review_data}
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            self.logger.error(f"更新复盘数据失败: {str(e)}")
            return False
    
    def close(self):
        """关闭数据库连接"""
        try:
            self.client.close()
            self.logger.info("MongoDB连接已关闭")
        except Exception as e:
            self.logger.error(f"关闭MongoDB连接失败: {str(e)}")


# 数据分析辅助函数
class FootballDataAnalyzer:
    """足球数据分析类"""
    
    def __init__(self, db_storage):
        """
        初始化分析器
        
        Args:
            db_storage: MongoDBStorage实例
        """
        self.storage = db_storage
        self.logger = setup_logger()
    
    def analyze_odds_trends(self, match_id):
        """
        分析赔率趋势（需要历史赔率数据）
        
        Args:
            match_id: 比赛ID
            
        Returns:
            analysis: 分析结果
        """
        # TODO: 实现赔率趋势分析
        pass
    
    def analyze_league_performance(self, league):
        """
        分析联赛表现
        
        Args:
            league: 联赛名称
            
        Returns:
            analysis: 分析结果
        """
        try:
            matches = self.storage.get_matches_by_league(league)
            
            total = len(matches)
            finished = len([m for m in matches if m.get('status') == '完场'])
            
            return {
                'league': league,
                'total_matches': total,
                'finished_matches': finished,
                'pending_matches': total - finished
            }
            
        except Exception as e:
            self.logger.error(f"分析联赛数据失败: {str(e)}")
            return None
    
    def find_high_odds_matches(self, min_win_odds=3.0):
        """
        查找高赔率比赛
        
        Args:
            min_win_odds: 最小主胜赔率
            
        Returns:
            matches: 符合条件的比赛列表
        """
        try:
            # 使用聚合查询
            pipeline = [
                {
                    '$match': {
                        'euro_current_win': {'$exists': True, '$ne': ''}
                    }
                },
                {
                    '$addFields': {
                        'win_odds_num': {'$toDouble': '$euro_current_win'}
                    }
                },
                {
                    '$match': {
                        'win_odds_num': {'$gte': min_win_odds}
                    }
                },
                {'$sort': {'win_odds_num': -1}}
            ]
            
            matches = list(self.storage.matches_collection.aggregate(pipeline))
            # 移除_id字段
            for m in matches:
                m.pop('_id', None)
            
            return matches
            
        except Exception as e:
            self.logger.error(f"查找高赔率比赛失败: {str(e)}")
            return []

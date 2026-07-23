"""
MongoDB数据存储模块
"""
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure, DuplicateKeyError
from datetime import datetime, timedelta
from hashlib import sha256
from utils import setup_logger
import os
import certifi
import re
import json

from football_ai.version import (
    DEFAULT_RULE_WEIGHTS,
    ENGINE_VERSION,
    VERSION_MANIFEST,
)
from football_ai.parlay import build_draw_parlays
from football_ai.draw_review import aggregate_draw_reviews
from football_ai.daily_review import aggregate_daily_ai_reviews
from football_ai.review_memory import build_review_memory
from football_ai.league_profile import (
    build_match_goal_margin_models,
    build_league_profiles,
    classify_asian_risk_patterns,
    classify_market_favorite,
    league_aliases,
)
from football_ai.skills import (
    SKILL_DEFINITIONS,
    baseline_skill_documents,
    build_draw_skill_candidate,
    build_rule_skill_candidate,
)


def clean_asian_handicap(value):
    """标准化亚盘盘口；升/降是走势，不属于盘口文本。"""
    if value is None:
        return ''
    text = re.sub(r'\s+', '', str(value))
    return re.sub(r'(?:[↑↓]|升|降)+$', '', text)


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
        self.match_fundamentals_collection = self.db['match_fundamentals']
        self.predictions_collection = self.db['predictions']
        # ai_analyses 保留为 V1 只读兼容集合。
        self.ai_analyses_collection = self.db['ai_analyses']
        self.fae_analyses_collection = self.db['fae_analyses']
        self.fae_analysis_history_collection = self.db['fae_analysis_history']
        self.fae_reviews_collection = self.db['fae_reviews']
        self.fae_rule_weights_collection = self.db['fae_rule_weights']
        self.fae_versions_collection = self.db['fae_versions']
        self.fae_draw_snapshots_collection = self.db['fae_draw_snapshots']
        self.fae_draw_reviews_collection = self.db['fae_draw_reviews']
        self.fae_draw_strategy_weights_collection = self.db['fae_draw_strategy_weights']
        self.fae_skill_versions_collection = self.db['fae_skill_versions']
        self.fae_skill_candidates_collection = self.db['fae_skill_candidates']
        self.fae_skill_deployments_collection = self.db['fae_skill_deployments']
        self.fae_daily_ai_runs_collection = self.db['fae_daily_ai_runs']
        self.fae_daily_ai_matches_collection = self.db['fae_daily_ai_matches']
        self.fae_daily_ai_batches_collection = self.db['fae_daily_ai_batches']
        self.fae_daily_ai_reviews_collection = self.db['fae_daily_ai_reviews']
        self.wecom_deliveries_collection = self.db['wecom_deliveries']
        self.user_picks_collection = self.db['user_picks']
        self.bets_collection = self.db['bets']
        
        # 创建索引
        self._create_indexes()
        self.ensure_fae_skill_registry()
    
    def _create_indexes(self):
        """创建数据库索引"""
        try:
            # 比赛表索引
            self.matches_collection.create_index([('match_id', ASCENDING)], unique=True)
            self.matches_collection.create_index([('league', ASCENDING)])
            self.matches_collection.create_index([('status', ASCENDING)])
            self.matches_collection.create_index([
                ('league', ASCENDING),
                ('status', ASCENDING),
                ('owner_date', DESCENDING),
            ])
            self.matches_collection.create_index([('match_time', DESCENDING)])
            self.matches_collection.create_index([('created_at', DESCENDING)])
            
            # 赔率表索引
            self.odds_collection.create_index([('match_id', ASCENDING)])
            self.odds_collection.create_index([('created_at', DESCENDING)])

            # 500 基本面单独存储，避免比赛列表接口携带大体积阵容/赛程。
            self.match_fundamentals_collection.create_index(
                [('match_id', ASCENDING)], unique=True
            )
            self.match_fundamentals_collection.create_index([
                ('updated_at', DESCENDING)
            ])
            
            # 预测表索引
            self.predictions_collection.create_index([('match_id', ASCENDING)], unique=True)
            self.predictions_collection.create_index([('predict_date', DESCENDING)])
            self.predictions_collection.create_index([('is_reviewed', ASCENDING)])

            # V1 生成式 AI 分析（兼容）
            self.ai_analyses_collection.create_index([('match_id', ASCENDING)], unique=True)
            self.ai_analyses_collection.create_index([('generated_at', DESCENDING)])
            self.ai_analyses_collection.create_index([('data_hash', ASCENDING)])

            # Football AI Engine
            self.fae_analyses_collection.create_index([('match_id', ASCENDING)], unique=True)
            self.fae_analyses_collection.create_index([('owner_date', ASCENDING)])
            self.fae_analyses_collection.create_index([('generated_at', DESCENDING)])
            self.fae_analyses_collection.create_index([('data_hash', ASCENDING)])
            self.fae_analyses_collection.create_index([('review_status', ASCENDING)])
            self.fae_analysis_history_collection.create_index([
                ('match_id', ASCENDING), ('generated_at', DESCENDING)
            ])
            self.fae_reviews_collection.create_index([
                ('match_id', ASCENDING), ('engine_version', ASCENDING)
            ], unique=True)
            self.fae_reviews_collection.create_index([('owner_date', DESCENDING)])
            self.fae_rule_weights_collection.create_index([('rule_id', ASCENDING)], unique=True)
            self.fae_versions_collection.create_index([('version', ASCENDING)], unique=True)
            self.fae_draw_snapshots_collection.create_index(
                [('snapshot_hash', ASCENDING)], unique=True
            )
            self.fae_draw_snapshots_collection.create_index([
                ('owner_date', ASCENDING), ('eligible_for_review', ASCENDING),
                ('generated_at', DESCENDING)
            ])
            self.fae_draw_reviews_collection.create_index([
                ('owner_date', ASCENDING), ('engine_version', ASCENDING)
            ], unique=True)
            self.fae_draw_strategy_weights_collection.create_index(
                [('selection', ASCENDING)], unique=True
            )
            self.fae_skill_versions_collection.create_index([
                ('skill_id', ASCENDING), ('version', ASCENDING)
            ], unique=True)
            self.fae_skill_versions_collection.create_index([
                ('skill_id', ASCENDING), ('status', ASCENDING)
            ])
            self.fae_skill_candidates_collection.create_index([
                ('candidate_id', ASCENDING)
            ], unique=True)
            self.fae_skill_candidates_collection.create_index([
                ('skill_id', ASCENDING), ('status', ASCENDING),
                ('updated_at', DESCENDING),
            ])
            self.fae_skill_deployments_collection.create_index([
                ('skill_id', ASCENDING), ('deployed_at', DESCENDING)
            ])
            self.fae_daily_ai_runs_collection.create_index(
                [('run_id', ASCENDING)], unique=True
            )
            self.fae_daily_ai_runs_collection.create_index([
                ('owner_date', ASCENDING), ('generated_at', DESCENDING)
            ])
            self.fae_daily_ai_runs_collection.create_index([
                ('owner_date', ASCENDING), ('input_hash', ASCENDING)
            ])
            # V1 used one mutable document per date/match. Daily review needs
            # the exact pre-match run, so preserve one document per run/match.
            for name, info in (
                self.fae_daily_ai_matches_collection.index_information().items()
            ):
                keys = tuple(info.get('key') or [])
                if (
                    info.get('unique')
                    and keys == (
                        ('owner_date', ASCENDING), ('match_id', ASCENDING)
                    )
                ):
                    self.fae_daily_ai_matches_collection.drop_index(name)
            self.fae_daily_ai_matches_collection.create_index([
                ('run_id', ASCENDING), ('match_id', ASCENDING)
            ], unique=True)
            self.fae_daily_ai_matches_collection.create_index([
                ('run_id', ASCENDING), ('generated_at', DESCENDING)
            ])
            self.fae_daily_ai_matches_collection.create_index([
                ('owner_date', ASCENDING), ('match_id', ASCENDING),
                ('generated_at', DESCENDING)
            ])
            self.fae_daily_ai_batches_collection.create_index(
                [('batch_hash', ASCENDING)], unique=True
            )
            self.fae_daily_ai_batches_collection.create_index([
                ('owner_date', ASCENDING), ('generated_at', DESCENDING)
            ])
            self.fae_daily_ai_reviews_collection.create_index(
                [('run_id', ASCENDING)], unique=True
            )
            self.fae_daily_ai_reviews_collection.create_index([
                ('owner_date', ASCENDING), ('reviewed_at', DESCENDING)
            ])
            self.wecom_deliveries_collection.create_index(
                [('delivery_key', ASCENDING)], unique=True
            )
            self.wecom_deliveries_collection.create_index([
                ('event_type', ASCENDING), ('updated_at', DESCENDING)
            ])
            
            # 用户选择表索引
            self.user_picks_collection.create_index([('device_id', ASCENDING), ('match_id', ASCENDING)], unique=True)
            self.user_picks_collection.create_index([('created_at', DESCENDING)])
            
            # 投注表索引
            self.bets_collection.create_index([('device_id', ASCENDING)])
            self.bets_collection.create_index([('created_at', DESCENDING)])
            self.bets_collection.create_index([('status', ASCENDING)])
            
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
        # 校验数据有效性
        if not match_data.get('home_team') or not match_data.get('away_team'):
            self.logger.warning(f"比赛数据不完整(无主客队名)，跳过保存: {match_data.get('match_id')}")
            return None

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

    def save_match_fundamentals(self, match_id, source_analysis):
        """缓存单场 500 基本面，供详情页和全日研判复用。"""
        if not match_id or not isinstance(source_analysis, dict):
            return None
        try:
            now = datetime.now()
            payload = dict(source_analysis)
            payload['cached_at'] = now
            return self.match_fundamentals_collection.update_one(
                {'match_id': str(match_id)},
                {
                    '$set': {
                        'match_id': str(match_id),
                        'source': '500彩票网',
                        'data': payload,
                        'updated_at': now,
                    }
                },
                upsert=True,
            )
        except Exception as e:
            self.logger.error(f"保存500基本面失败: {str(e)}")
            return None

    def get_match_fundamentals(self, match_id):
        """读取单场已缓存的 500 基本面。"""
        try:
            document = self.match_fundamentals_collection.find_one(
                {'match_id': str(match_id)}, {'_id': 0}
            )
            return (document or {}).get('data') or {}
        except Exception as e:
            self.logger.error(f"读取500基本面失败: {str(e)}")
            return {}

    def get_match_fundamentals_bulk(self, match_ids):
        """批量读取基本面缓存，返回 match_id -> data。"""
        ids = [str(value) for value in match_ids if value not in (None, '')]
        if not ids:
            return {}
        try:
            return {
                str(document.get('match_id')): document.get('data') or {}
                for document in self.match_fundamentals_collection.find(
                    {'match_id': {'$in': ids}}, {'_id': 0}
                )
            }
        except Exception as e:
            self.logger.error(f"批量读取500基本面失败: {str(e)}")
            return {}
    
    def save_odds(self, match_id, odds_data):
        """
        保存赔率数据
        
        Args:
            match_id: 比赛ID
            odds_data: 赔率数据字典
            
        Returns:
            result: 插入结果
        """
        # 校验数据有效性：至少包含一项数据
        has_data = False
        if odds_data.get('euro_odds'): has_data = True
        elif odds_data.get('asian_handicap'): has_data = True
        elif odds_data.get('over_under'): has_data = True
        elif odds_data.get('handicap_index'): has_data = True
        
        if not has_data:
            self.logger.warning(f"赔率数据为空，跳过保存: {match_id}")
            return None

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
            
            # 更新时间字段（如果有）
            if 'euro_odds_update_time' in odds_data:
                update_fields['euro_odds_update_time'] = odds_data['euro_odds_update_time']
            if 'odds_update_time' in odds_data:
                update_fields['odds_update_time'] = odds_data['odds_update_time']
            
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
                    'asian_current_handicap': clean_asian_handicap(asian.get('current_handicap', '')),
                    'asian_current_away_odds': asian.get('current_away_odds', ''),
                    'asian_initial_home_odds': asian.get('initial_home_odds', ''),
                    'asian_initial_handicap': clean_asian_handicap(asian.get('initial_handicap', '')),
                    'asian_initial_away_odds': asian.get('initial_away_odds', ''),
                    'asian_source_company_id': asian.get('source_company_id', ''),
                    'asian_source_company_name': asian.get('source_company_name', ''),
                    'asian_source_fallback': asian.get('source_fallback', False),
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
                    'ou_initial_under_odds': ou.get('initial_under_odds', ''),
                    'ou_source_company_id': ou.get('source_company_id', ''),
                    'ou_source_company_name': ou.get('source_company_name', ''),
                    'ou_source_fallback': ou.get('source_fallback', False),
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
            status_map = {0: '未开始', 1: '进行中', 2: '完场', 6: '改期'}
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

    def save_ai_analysis(self, analysis_data):
        """保存 FAE 最新分析，并保留每次生成的版本历史。"""
        try:
            payload = dict(analysis_data)
            payload['match_id'] = str(payload.get('match_id') or '')
            payload['updated_at'] = datetime.utcnow().isoformat() + 'Z'
            previous = self.fae_analyses_collection.find_one(
                {'match_id': payload['match_id']},
                {'_id': 0, 'data_hash': 1}
            )
            result = self.fae_analyses_collection.update_one(
                {'match_id': payload['match_id']},
                {'$set': payload},
                upsert=True
            )
            if not previous or previous.get('data_hash') != payload.get('data_hash'):
                self.fae_analysis_history_collection.insert_one(dict(payload))
            self.ensure_fae_version()
            self.logger.info(f"保存 FAE 分析: {payload['match_id']}")
            return result
        except Exception as e:
            self.logger.error(f"保存 FAE 分析失败: {str(e)}")
            return None

    def get_ai_analysis(self, match_id):
        """读取 FAE 最新分析；没有时回退到 V1 历史数据。"""
        try:
            current = self.fae_analyses_collection.find_one(
                {'match_id': str(match_id)},
                {'_id': 0}
            )
            if current:
                return current
            return self.ai_analyses_collection.find_one(
                {'match_id': str(match_id)}, {'_id': 0}
            )
        except Exception as e:
            self.logger.error(f"获取 FAE 分析失败: {str(e)}")
            return None

    def get_fae_analyses(self, filters=None, limit=None):
        """按日期、复盘状态等条件读取 FAE 分析。"""
        try:
            cursor = self.fae_analyses_collection.find(filters or {}, {'_id': 0})
            cursor = cursor.sort('generated_at', DESCENDING)
            if limit:
                cursor = cursor.limit(limit)
            return list(cursor)
        except Exception as e:
            self.logger.error(f"获取 FAE 分析列表失败: {str(e)}")
            return []

    def save_fae_daily_ai_run(self, analysis_run):
        """Save a daily run and preserve its immutable per-match snapshot."""
        try:
            payload = dict(analysis_run or {})
            run_id = str(payload.get('run_id') or '')
            owner_date = str(payload.get('owner_date') or '')[:10]
            matches = [
                dict(item) for item in (payload.pop('matches', None) or [])
                if item.get('match_id')
            ]
            if not run_id or not owner_date or not matches:
                raise ValueError('全日研判缺少 run_id、日期或逐场结果')
            match_ids = [str(item.get('match_id')) for item in matches]
            statuses = {
                str(item.get('match_id')): item.get('status')
                for item in self.matches_collection.find(
                    {'match_id': {'$in': match_ids}},
                    {'_id': 0, 'match_id': 1, 'status': 1},
                )
            }
            prediction_statuses = {
                str(item.get('match_id')): (
                    item.get('status_at_prediction')
                    if item.get('status_at_prediction') is not None
                    else statuses.get(str(item.get('match_id')))
                )
                for item in matches
            }
            eligible = bool(match_ids) and all(
                prediction_statuses.get(match_id) in (0, '0')
                for match_id in match_ids
            )
            payload.update({
                'run_id': run_id,
                'owner_date': owner_date,
                'match_ids': match_ids,
                'eligible_for_review': eligible,
                'updated_at': datetime.utcnow().isoformat() + 'Z',
            })
            self.fae_daily_ai_runs_collection.update_one(
                {'run_id': run_id},
                {'$set': payload},
                upsert=True,
            )
            for item in matches:
                match_payload = {
                    **item,
                    'match_id': str(item.get('match_id') or ''),
                    'run_id': run_id,
                    'owner_date': owner_date,
                    'status_at_prediction': prediction_statuses.get(
                        str(item.get('match_id') or '')
                    ),
                    'updated_at': datetime.utcnow().isoformat() + 'Z',
                }
                self.fae_daily_ai_matches_collection.update_one(
                    {
                        'run_id': run_id,
                        'match_id': match_payload['match_id'],
                    },
                    {'$set': match_payload},
                    upsert=True,
                )
            self.logger.info(
                f"保存 FAE 全日研判: {owner_date}，{len(matches)} 场"
            )
            return True
        except Exception as e:
            self.logger.error(f"保存 FAE 全日研判失败: {str(e)}")
            return False

    def get_fae_daily_ai_run(self, owner_date, input_hash=None):
        """Read a daily run, optionally requiring an exact input cache key."""
        try:
            query = {'owner_date': str(owner_date or '')[:10]}
            if input_hash:
                query['input_hash'] = str(input_hash)
            run = self.fae_daily_ai_runs_collection.find_one(
                query,
                {'_id': 0},
                sort=[('generated_at', DESCENDING)],
            )
            if not run:
                return None
            matches = list(self.fae_daily_ai_matches_collection.find(
                {'run_id': run.get('run_id')},
                {'_id': 0},
            ).sort([('match_time', ASCENDING), ('match_number', ASCENDING)]))
            run['matches'] = matches
            return run
        except Exception as e:
            self.logger.error(f"读取 FAE 全日研判失败: {str(e)}")
            return None

    def get_wecom_delivery(self, delivery_key):
        """Read one deduplication record without exposing webhook secrets."""
        try:
            return self.wecom_deliveries_collection.find_one(
                {'delivery_key': str(delivery_key or '')},
                {'_id': 0},
            )
        except Exception as e:
            self.logger.error(f"读取企业微信投递记录失败: {str(e)}")
            return None

    def claim_wecom_delivery(
        self,
        delivery_key,
        event_type,
        *,
        content_hash=None,
    ):
        """Atomically claim one delivery; failed records may be retried."""
        key = str(delivery_key or '')
        now = datetime.utcnow().isoformat() + 'Z'
        try:
            self.wecom_deliveries_collection.insert_one({
                'delivery_key': key,
                'event_type': str(event_type or ''),
                'content_hash': str(content_hash or ''),
                'status': 'sending',
                'success': False,
                'created_at': now,
                'updated_at': now,
            })
            return True
        except DuplicateKeyError:
            claimed = self.wecom_deliveries_collection.update_one(
                {
                    'delivery_key': key,
                    'success': {'$ne': True},
                    'status': {'$ne': 'sending'},
                },
                {'$set': {
                    'status': 'sending',
                    'content_hash': str(content_hash or ''),
                    'updated_at': now,
                }},
            )
            return bool(claimed.modified_count)
        except Exception as e:
            self.logger.error(f"占用企业微信投递任务失败: {str(e)}")
            return False

    def save_wecom_delivery(
        self,
        delivery_key,
        event_type,
        result,
        *,
        content_hash=None,
    ):
        """Persist a sanitized delivery result for scheduler deduplication."""
        try:
            payload = {
                'delivery_key': str(delivery_key or ''),
                'event_type': str(event_type or ''),
                'content_hash': str(content_hash or ''),
                'status': str((result or {}).get('status') or 'failed'),
                'success': bool((result or {}).get('success')),
                'status_code': (result or {}).get('status_code'),
                'errcode': (result or {}).get('errcode'),
                'message': str((result or {}).get('message') or '')[:240],
                'updated_at': datetime.utcnow().isoformat() + 'Z',
            }
            if payload['success']:
                payload['sent_at'] = payload['updated_at']
            self.wecom_deliveries_collection.update_one(
                {'delivery_key': payload['delivery_key']},
                {'$set': payload},
                upsert=True,
            )
            return True
        except Exception as e:
            self.logger.error(f"保存企业微信投递记录失败: {str(e)}")
            return False

    def get_fae_daily_ai_snapshot(self, owner_date):
        """Return the latest daily AI run created while every match was pre-game."""
        try:
            run = self.fae_daily_ai_runs_collection.find_one(
                {
                    'owner_date': str(owner_date or '')[:10],
                    'eligible_for_review': True,
                },
                {'_id': 0},
                sort=[('generated_at', DESCENDING)],
            )
            if not run:
                return None
            run['matches'] = list(
                self.fae_daily_ai_matches_collection.find(
                    {'run_id': run.get('run_id')},
                    {'_id': 0},
                ).sort([
                    ('match_time', ASCENDING),
                    ('match_number', ASCENDING),
                ])
            )
            return run if run['matches'] else None
        except Exception as e:
            self.logger.error(f"读取 FAE 全日AI赛前快照失败: {str(e)}")
            return None

    def get_fae_daily_ai_snapshot_dates(self, limit=14):
        try:
            dates = sorted(
                value
                for value in self.fae_daily_ai_runs_collection.distinct(
                    'owner_date', {'eligible_for_review': True}
                )
                if value
            )
            return dates[-max(1, int(limit)):]
        except Exception as e:
            self.logger.error(f"读取 FAE 全日AI快照日期失败: {str(e)}")
            return []

    def save_fae_daily_ai_review(self, review):
        """Persist AI-primary settlement and feed staged Skill learning."""
        try:
            run_id = str(review.get('run_id') or '')
            owner_date = str(review.get('owner_date') or '')[:10]
            if not run_id or not owner_date:
                raise ValueError('AI复盘缺少 run_id 或日期')
            payload = {
                **review,
                'run_id': run_id,
                'owner_date': owner_date,
            }
            self.fae_daily_ai_reviews_collection.update_one(
                {'run_id': run_id}, {'$set': payload}, upsert=True
            )
            weights = self._recalculate_fae_draw_strategy_weights(apply=False)
            candidates = self.generate_fae_skill_candidates()
            return {
                'saved': True,
                'review': payload,
                'strategy_weights': weights,
                'candidates': candidates,
            }
        except Exception as e:
            self.logger.error(f"保存 FAE 全日AI复盘失败: {str(e)}")
            return {'saved': False, 'message': str(e)}

    def get_fae_daily_ai_review(self, owner_date):
        try:
            return self.fae_daily_ai_reviews_collection.find_one(
                {'owner_date': str(owner_date or '')[:10]},
                {'_id': 0},
                sort=[('reviewed_at', DESCENDING)],
            )
        except Exception as e:
            self.logger.error(f"读取 FAE 全日AI复盘失败: {str(e)}")
            return None

    def get_fae_daily_ai_review_stats(self):
        reviews = list(
            self.fae_daily_ai_reviews_collection.find({}, {'_id': 0})
        )
        weight_docs = list(self.fae_draw_strategy_weights_collection.find(
            {}, {'_id': 0}
        ))
        active_weights = self.get_fae_draw_strategy_weights()
        weights = {
            item.get('selection'): {
                **item,
                'weight': active_weights.get(item.get('selection'), 1.0),
            }
            for item in weight_docs if item.get('selection')
        }
        for selection, weight in active_weights.items():
            weights.setdefault(selection, {
                'selection': selection,
                'weight': weight,
                'action': 'hold',
            })
        return aggregate_daily_ai_reviews(reviews, weights)

    def get_fae_review_memory(self, before_date):
        """Return compact review memory using only strictly earlier match days."""
        try:
            window_days = max(
                2, min(30, int(os.getenv('FAE_REVIEW_MEMORY_DAYS', '7')))
            )
            observation_days = max(
                1,
                min(
                    window_days,
                    int(os.getenv(
                        'FAE_REVIEW_MEMORY_OBSERVATION_DAYS', '3'
                    )),
                ),
            )
            minimum_pattern_days = max(
                2,
                int(os.getenv(
                    'FAE_REVIEW_MEMORY_MIN_PATTERN_DAYS', '2'
                )),
            )
            minimum_evidence = max(
                10,
                int(os.getenv(
                    'FAE_REVIEW_MEMORY_MIN_EVIDENCE', '10'
                )),
            )
            reviews = list(
                self.fae_daily_ai_reviews_collection.find(
                    {
                        'owner_date': {
                            '$lt': str(before_date or '')[:10]
                        },
                        'ai_deep_review.status': 'completed',
                    },
                    {'_id': 0},
                ).sort('owner_date', DESCENDING).limit(window_days)
            )
            return build_review_memory(
                reviews,
                str(before_date or '')[:10],
                window_days=window_days,
                observation_days=observation_days,
                minimum_pattern_days=minimum_pattern_days,
                minimum_evidence=minimum_evidence,
            )
        except Exception as e:
            self.logger.error(f"读取 FAE 复盘记忆失败: {str(e)}")
            return build_review_memory([], str(before_date or '')[:10])

    def get_fae_league_profiles(self, before_date, leagues):
        """Build time-decayed league baselines without using future matches."""
        try:
            target_date = str(before_date or '')[:10]
            names = sorted({
                str(league or '').strip() for league in leagues
                if str(league or '').strip()
            })
            if not names:
                return {}
            lookback_days = max(
                90, int(os.getenv('FAE_LEAGUE_HISTORY_DAYS', '730'))
            )
            half_life_days = max(
                30, int(os.getenv('FAE_LEAGUE_HALF_LIFE_DAYS', '180'))
            )
            minimum_samples = max(
                10, int(os.getenv('FAE_LEAGUE_MIN_SAMPLES', '30'))
            )
            per_league_limit = max(
                50, int(os.getenv('FAE_LEAGUE_MAX_MATCHES', '500'))
            )
            global_limit = max(
                500, int(os.getenv('FAE_LEAGUE_GLOBAL_MAX_MATCHES', '5000'))
            )
            target = datetime.strptime(target_date, '%Y-%m-%d')
            cutoff = (
                target - timedelta(days=lookback_days)
            ).strftime('%Y-%m-%d')
            base_query = {
                'status': 2,
                'owner_date': {'$gte': cutoff, '$lt': target_date},
                'home_score': {'$nin': [None, '']},
                'away_score': {'$nin': [None, '']},
            }
            projection = {
                '_id': 0,
                'league': 1,
                'owner_date': 1,
                'home_score': 1,
                'away_score': 1,
                'euro_current_win': 1,
                'euro_current_lose': 1,
                'euro_initial_win': 1,
                'euro_initial_lose': 1,
                'asian_current_home_odds': 1,
                'asian_current_handicap': 1,
                'asian_current_away_odds': 1,
                'asian_initial_home_odds': 1,
                'asian_initial_handicap': 1,
                'asian_initial_away_odds': 1,
                'hi_handicap_value': 1,
                'handicap': 1,
                'hi_current_home_odds': 1,
                'hi_current_draw_odds': 1,
                'hi_current_away_odds': 1,
                'ou_current_total': 1,
                'ou_initial_total': 1,
            }
            rows_by_league = {}
            for league in names:
                rows_by_league[league] = list(
                    self.matches_collection.find(
                        {
                            **base_query,
                            'league': {'$in': league_aliases(league)},
                        },
                        projection,
                    ).sort('owner_date', DESCENDING).limit(per_league_limit)
                )
            global_rows = list(
                self.matches_collection.find(
                    base_query, projection
                ).sort('owner_date', DESCENDING).limit(global_limit)
            )
            return build_league_profiles(
                rows_by_league,
                target_date,
                global_matches=global_rows,
                half_life_days=half_life_days,
                minimum_samples=minimum_samples,
            )
        except Exception as e:
            self.logger.error(f"读取 FAE 联赛历史画像失败: {str(e)}")
            return {}

    def get_fae_match_goal_margin_models(self, before_date, matches):
        """Estimate draw/handicap-draw from leakage-safe similar history."""
        try:
            target_date = str(before_date or '')[:10]
            current_matches = [
                dict(match) for match in (matches or [])
                if match.get('match_id')
            ]
            if not current_matches:
                return {}
            lookback_days = max(
                180, int(os.getenv('FAE_GOAL_MARGIN_HISTORY_DAYS', '730'))
            )
            half_life_days = max(
                30, int(os.getenv('FAE_GOAL_MARGIN_HALF_LIFE_DAYS', '180'))
            )
            minimum_effective_sample = max(
                10.0,
                float(os.getenv('FAE_GOAL_MARGIN_MIN_EFFECTIVE', '25')),
            )
            maximum_history = max(
                1000, int(os.getenv('FAE_GOAL_MARGIN_MAX_MATCHES', '5000'))
            )
            target = datetime.strptime(target_date, '%Y-%m-%d')
            cutoff = (
                target - timedelta(days=lookback_days)
            ).strftime('%Y-%m-%d')
            projection = {
                '_id': 0,
                'match_id': 1,
                'league': 1,
                'owner_date': 1,
                'home_score': 1,
                'away_score': 1,
                'euro_current_win': 1,
                'euro_current_draw': 1,
                'euro_current_lose': 1,
                'asian_current_handicap': 1,
                'asian_initial_handicap': 1,
                'hi_handicap_value': 1,
                'handicap': 1,
                'hi_current_home_odds': 1,
                'hi_current_draw_odds': 1,
                'hi_current_away_odds': 1,
                'ou_current_total': 1,
                'ou_initial_total': 1,
            }
            history = list(self.matches_collection.find(
                {
                    'status': 2,
                    'owner_date': {'$gte': cutoff, '$lt': target_date},
                    'home_score': {'$nin': [None, '']},
                    'away_score': {'$nin': [None, '']},
                },
                projection,
            ).sort('owner_date', DESCENDING).limit(maximum_history))
            return build_match_goal_margin_models(
                current_matches,
                history,
                target_date,
                half_life_days=half_life_days,
                minimum_effective_sample=minimum_effective_sample,
            )
        except Exception as e:
            self.logger.error(f"读取 FAE 历史进球差模型失败: {str(e)}")
            return {}

    def get_fae_league_profile_matches(
        self,
        before_date,
        league,
        *,
        kind='surprise',
        page=1,
        page_size=20,
    ):
        """Return auditable finished matches behind one league profile metric."""
        try:
            target_date = str(before_date or '')[:10]
            target = datetime.strptime(target_date, '%Y-%m-%d')
            lookback_days = max(
                90, int(os.getenv('FAE_LEAGUE_HISTORY_DAYS', '730'))
            )
            cutoff = (
                target - timedelta(days=lookback_days)
            ).strftime('%Y-%m-%d')
            rows = list(
                self.matches_collection.find(
                    {
                        'status': 2,
                        'owner_date': {'$gte': cutoff, '$lt': target_date},
                        'league': {'$in': league_aliases(league)},
                        'home_score': {'$nin': [None, '']},
                        'away_score': {'$nin': [None, '']},
                    },
                    {
                        '_id': 0,
                        'match_id': 1,
                        'match_number': 1,
                        'league': 1,
                        'owner_date': 1,
                        'match_time': 1,
                        'home_team': 1,
                        'away_team': 1,
                        'home_score': 1,
                        'away_score': 1,
                        'euro_current_win': 1,
                        'euro_current_draw': 1,
                        'euro_current_lose': 1,
                        'euro_initial_win': 1,
                        'euro_initial_draw': 1,
                        'euro_initial_lose': 1,
                        'asian_current_home_odds': 1,
                        'asian_current_handicap': 1,
                        'asian_current_away_odds': 1,
                        'asian_initial_home_odds': 1,
                        'asian_initial_handicap': 1,
                        'asian_initial_away_odds': 1,
                        'hi_handicap_value': 1,
                        'handicap': 1,
                        'hi_current_home_odds': 1,
                        'hi_current_draw_odds': 1,
                        'hi_current_away_odds': 1,
                        'hi_initial_home_odds': 1,
                        'hi_initial_draw_odds': 1,
                        'hi_initial_away_odds': 1,
                    },
                ).sort([
                    ('owner_date', DESCENDING),
                    ('match_time', DESCENDING),
                ]).limit(max(
                    50,
                    int(os.getenv('FAE_LEAGUE_MAX_MATCHES', '500')),
                ))
            )
            allowed_kinds = {
                'all', 'surprise', 'draw', 'upset', 'follow', 'not_cover',
            }
            selected_kind = kind if kind in allowed_kinds else 'surprise'
            items = []
            for match in rows:
                classification = classify_market_favorite(match)
                if (
                    not classification
                    or not classification.get('clear_favorite')
                ):
                    continue
                matches_kind = {
                    'all': True,
                    'surprise': classification.get('favorite_failed'),
                    'draw': classification.get('result_type') == 'draw',
                    'upset': classification.get('result_type') == 'upset',
                    'follow': classification.get('result_type') == 'follow',
                    'not_cover': classification.get('favorite_not_cover'),
                }[selected_kind]
                if not matches_kind:
                    continue
                favorite_side = classification.get('favorite_side')
                favorite_team = (
                    match.get('home_team')
                    if favorite_side == 'home'
                    else match.get('away_team')
                )
                item = dict(match)
                asian_risk = classify_asian_risk_patterns(
                    match, classification
                )
                item.update({
                    'favorite_side': favorite_side,
                    'favorite_team': favorite_team,
                    'favorite_odds': classification.get('favorite_odds'),
                    'favorite_band': classification.get('favorite_band'),
                    'result_type': classification.get('result_type'),
                    'favorite_not_cover': classification.get(
                        'favorite_not_cover'
                    ),
                    'hhad_result': classification.get('hhad_result'),
                    'asian_risk': asian_risk,
                })
                items.append(item)

            safe_page = max(1, int(page))
            safe_size = max(1, min(50, int(page_size)))
            start = (safe_page - 1) * safe_size
            return {
                'before_date': target_date,
                'league': str(league or '').strip(),
                'kind': selected_kind,
                'total': len(items),
                'page': safe_page,
                'page_size': safe_size,
                'total_pages': (
                    (len(items) + safe_size - 1) // safe_size
                ),
                'items': items[start:start + safe_size],
            }
        except Exception as e:
            self.logger.error(f"读取联赛画像样本比赛失败: {str(e)}")
            return {
                'before_date': str(before_date or '')[:10],
                'league': str(league or '').strip(),
                'kind': kind,
                'total': 0,
                'page': 1,
                'page_size': page_size,
                'total_pages': 0,
                'items': [],
            }

    def get_fae_daily_ai_match(self, match_id, owner_date=None):
        """Read the latest saved Ark judgement for one match."""
        try:
            query = {'match_id': str(match_id or '')}
            if owner_date:
                query['owner_date'] = str(owner_date)[:10]
            return self.fae_daily_ai_matches_collection.find_one(
                query,
                {'_id': 0},
                sort=[('generated_at', DESCENDING)],
            )
        except Exception as e:
            self.logger.error(f"读取 FAE 逐场全日研判失败: {str(e)}")
            return None

    def save_fae_daily_ai_batch(self, batch):
        """Persist a paid Ark batch checkpoint so retries resume safely."""
        try:
            payload = dict(batch or {})
            batch_hash = str(payload.get('batch_hash') or '')
            if not batch_hash or not isinstance(payload.get('output'), dict):
                return False
            self.fae_daily_ai_batches_collection.update_one(
                {'batch_hash': batch_hash},
                {'$set': payload},
                upsert=True,
            )
            return True
        except Exception as e:
            self.logger.error(f"保存 FAE 全日批次检查点失败: {str(e)}")
            return False

    def get_fae_daily_ai_batch(self, batch_hash):
        try:
            return self.fae_daily_ai_batches_collection.find_one(
                {'batch_hash': str(batch_hash or '')},
                {'_id': 0},
            )
        except Exception as e:
            self.logger.error(f"读取 FAE 全日批次检查点失败: {str(e)}")
            return None

    def ensure_fae_version(self):
        """登记当前引擎版本与能力清单。"""
        try:
            payload = dict(VERSION_MANIFEST)
            payload['updated_at'] = datetime.utcnow().isoformat() + 'Z'
            self.fae_versions_collection.update_one(
                {'version': ENGINE_VERSION},
                {'$set': payload, '$setOnInsert': {
                    'created_at': datetime.utcnow().isoformat() + 'Z'
                }},
                upsert=True
            )
            return True
        except Exception as e:
            self.logger.error(f"登记 FAE 版本失败: {str(e)}")
            return False

    def _legacy_fae_rule_weights(self):
        """Read the pre-Skill weight collection for migration and fallback."""
        weights = dict(DEFAULT_RULE_WEIGHTS)
        for item in self.fae_rule_weights_collection.find({}, {'_id': 0}):
            try:
                weights[str(item.get('rule_id'))] = float(item.get('weight'))
            except (TypeError, ValueError):
                continue
        return weights

    def _legacy_fae_draw_strategy_weights(self):
        weights = {'平局': 1.0, '让平': 1.0}
        for item in self.fae_draw_strategy_weights_collection.find(
            {}, {'_id': 0}
        ):
            if item.get('selection') in weights:
                try:
                    weights[item['selection']] = float(item.get('weight') or 1)
                except (TypeError, ValueError):
                    continue
        return weights

    def ensure_fae_skill_registry(self):
        """Seed one active version for every built-in FAE Skill."""
        try:
            baseline = baseline_skill_documents(
                self._legacy_fae_rule_weights(),
                self._legacy_fae_draw_strategy_weights(),
            )
            now = datetime.utcnow().isoformat() + 'Z'
            for document in baseline:
                skill_id = document['skill_id']
                active = self.fae_skill_versions_collection.find_one({
                    'skill_id': skill_id, 'status': 'active'
                })
                if active:
                    continue
                existing = self.fae_skill_versions_collection.find_one(
                    {'skill_id': skill_id},
                    sort=[('activated_at', DESCENDING), ('created_at', DESCENDING)],
                )
                if existing:
                    self.fae_skill_versions_collection.update_one(
                        {'_id': existing['_id']},
                        {'$set': {'status': 'active', 'activated_at': now}},
                    )
                    continue
                payload = {
                    **document,
                    'created_at': now,
                    'activated_at': now,
                }
                self.fae_skill_versions_collection.insert_one(payload)
            return True
        except Exception as e:
            self.logger.error(f"初始化 FAE Skill 注册表失败: {str(e)}")
            return False

    def get_active_fae_skills(self):
        """Return active Skill documents without Mongo-specific fields."""
        try:
            self.ensure_fae_skill_registry()
            rows = list(self.fae_skill_versions_collection.find(
                {'status': 'active'}, {'_id': 0}
            ))
            rows.sort(key=lambda item: str(item.get('skill_id') or ''))
            return rows
        except Exception as e:
            self.logger.error(f"读取 FAE Skill 失败: {str(e)}")
            return []

    def get_active_fae_skill_versions(self):
        return {
            str(item.get('skill_id')): str(item.get('version'))
            for item in self.get_active_fae_skills()
            if item.get('skill_id') and item.get('version')
        }

    def get_fae_rule_weights(self):
        """Return weights from active Skill versions, with legacy fallback."""
        weights = dict(DEFAULT_RULE_WEIGHTS)
        try:
            active_skills = self.get_active_fae_skills()
            found_versioned_weights = False
            for skill in active_skills:
                configured = (
                    (skill.get('parameters') or {}).get('rule_weights') or {}
                )
                for rule_id, value in configured.items():
                    weights[str(rule_id)] = float(value)
                    found_versioned_weights = True
            if not found_versioned_weights:
                weights.update(self._legacy_fae_rule_weights())
        except Exception as e:
            self.logger.error(f"读取 FAE 规则权重失败: {str(e)}")
        return weights

    def get_fae_skill_center(self):
        """Return active versions, validated candidates and deployment history."""
        try:
            active = self.get_active_fae_skills()
            version_counts = {
                item['_id']: item['count']
                for item in self.fae_skill_versions_collection.aggregate([
                    {'$group': {'_id': '$skill_id', 'count': {'$sum': 1}}},
                ])
            }
            for item in active:
                latest_deployment = self.fae_skill_deployments_collection.find_one(
                    {
                        'skill_id': item.get('skill_id'),
                        'version': item.get('version'),
                    },
                    {'_id': 0, 'action': 1},
                    sort=[('deployed_at', DESCENDING)],
                )
                item['can_rollback'] = (
                    version_counts.get(item.get('skill_id'), 0) > 1
                    and (latest_deployment or {}).get('action') == 'promote'
                )
            candidates = list(self.fae_skill_candidates_collection.find(
                {'status': {'$in': ['validated', 'needs_review']}},
                {'_id': 0},
            ).sort('updated_at', DESCENDING).limit(30))
            deployments = list(self.fae_skill_deployments_collection.find(
                {}, {'_id': 0}
            ).sort('deployed_at', DESCENDING).limit(30))
            return {
                'engine_version': ENGINE_VERSION,
                'mode': 'staged',
                'active': active,
                'candidates': candidates,
                'deployments': deployments,
                'minimum_samples': max(
                    3, int(os.getenv('FAE_LEARNING_MIN_SAMPLES', '10'))
                ),
                'minimum_new_samples': max(
                    1, int(os.getenv('FAE_SKILL_MIN_NEW_SAMPLES', '10'))
                ),
            }
        except Exception as e:
            self.logger.error(f"读取 FAE Skill 中心失败: {str(e)}")
            return {
                'engine_version': ENGINE_VERSION,
                'mode': 'staged',
                'active': [],
                'candidates': [],
                'deployments': [],
                'error': str(e),
            }

    def generate_fae_skill_candidates(self):
        """Build validated Skill candidates from accumulated review evidence."""
        try:
            active_skills = self.get_active_fae_skills()
            rule_stats = list(self.fae_rule_weights_collection.find(
                {}, {'_id': 0}
            ))
            ai_reviews = list(
                self.fae_daily_ai_reviews_collection.find({}, {'_id': 0})
            )
            if ai_reviews:
                draw_stats = aggregate_daily_ai_reviews(ai_reviews)
            else:
                reviews = list(
                    self.fae_draw_reviews_collection.find({}, {'_id': 0})
                )
                draw_stats = aggregate_draw_reviews(reviews)
            ai_scope_skills = {
                'euro': {'euro-odds'},
                'asian': {'asian-handicap'},
                'sporttery': {'draw-strategy'},
                'total': {'over-under'},
                'consistency': {'euro-odds', 'risk-control'},
                'risk': {'risk-control'},
                'guardrail': {'risk-control'},
                'combination': {'draw-strategy'},
                'history_calibration': {
                    'draw-strategy', 'risk-control'
                },
            }
            ai_advisories = []
            for ai_review in ai_reviews:
                deep_review = ai_review.get('ai_deep_review') or {}
                for advisory in deep_review.get(
                    'learning_candidates'
                ) or []:
                    scope = str(advisory.get('scope') or '')
                    for skill_id in ai_scope_skills.get(scope, set()):
                        ai_advisories.append({
                            **advisory,
                            'skill_id': skill_id,
                            'owner_date': ai_review.get('owner_date'),
                            'review_run_id': ai_review.get('run_id'),
                            'review_model': deep_review.get('model'),
                        })
            minimum_samples = max(
                3, int(os.getenv('FAE_LEARNING_MIN_SAMPLES', '10'))
            )
            minimum_new_samples = max(
                1, int(os.getenv('FAE_SKILL_MIN_NEW_SAMPLES', '10'))
            )
            generated = []
            now = datetime.utcnow().isoformat() + 'Z'
            for active in active_skills:
                if active.get('skill_id') == 'draw-strategy':
                    selection_stats = dict(
                        draw_stats.get('by_selection') or {}
                    )
                    selection_stats['让平'] = (
                        (draw_stats.get('handicap_by_selection') or {}).get(
                            '让平'
                        )
                        or selection_stats.get('让平')
                        or {}
                    )
                    candidate = build_draw_skill_candidate(
                        active,
                        selection_stats,
                        minimum_samples=minimum_samples,
                        minimum_new_samples=minimum_new_samples,
                    )
                else:
                    candidate = build_rule_skill_candidate(
                        active,
                        rule_stats,
                        minimum_samples=minimum_samples,
                        minimum_new_samples=minimum_new_samples,
                    )
                if not candidate or candidate.get('status') != 'validated':
                    continue
                # Ark proposals are attached for audit/explanation only. The
                # candidate parameters above still come exclusively from
                # minimum-sample historical replay.
                matching_advisories = [
                    item for item in ai_advisories
                    if item.get('skill_id') == candidate.get('skill_id')
                ][-20:]
                candidate['ai_review_evidence'] = matching_advisories
                candidate['evaluation']['ai_advisory_count'] = len(
                    matching_advisories
                )
                open_candidate = self.fae_skill_candidates_collection.find_one({
                    'skill_id': candidate['skill_id'],
                    'parent_version': candidate['parent_version'],
                    'status': {'$in': ['validated', 'needs_review']},
                })
                if open_candidate:
                    candidate_id = open_candidate['candidate_id']
                    created_at = open_candidate.get('created_at') or now
                else:
                    fingerprint = json.dumps(
                        {
                            'skill_id': candidate['skill_id'],
                            'parent_version': candidate['parent_version'],
                            'parameters': candidate['parameters'],
                            'learning_snapshot': candidate['learning_snapshot'],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(',', ':'),
                    ).encode('utf-8')
                    candidate_id = sha256(fingerprint).hexdigest()[:24]
                    created_at = now
                payload = {
                    **candidate,
                    'candidate_id': candidate_id,
                    'created_at': created_at,
                    'updated_at': now,
                }
                self.fae_skill_candidates_collection.update_one(
                    {'candidate_id': candidate_id},
                    {'$set': payload},
                    upsert=True,
                )
                generated.append(payload)
            return generated
        except Exception as e:
            self.logger.error(f"生成 FAE Skill 候选失败: {str(e)}")
            return []

    def _apply_fae_skill_parameters(self, skill):
        now = datetime.utcnow().isoformat() + 'Z'
        parameters = skill.get('parameters') or {}
        for rule_id, weight in (parameters.get('rule_weights') or {}).items():
            self.fae_rule_weights_collection.update_one(
                {'rule_id': str(rule_id)},
                {'$set': {
                    'rule_id': str(rule_id),
                    'weight': round(float(weight), 3),
                    'engine_version': ENGINE_VERSION,
                    'skill_id': skill.get('skill_id'),
                    'skill_version': skill.get('version'),
                    'updated_at': now,
                }},
                upsert=True,
            )
        for selection, weight in (
            parameters.get('strategy_weights') or {}
        ).items():
            self.fae_draw_strategy_weights_collection.update_one(
                {'selection': str(selection)},
                {'$set': {
                    'selection': str(selection),
                    'weight': round(float(weight), 3),
                    'engine_version': ENGINE_VERSION,
                    'skill_id': skill.get('skill_id'),
                    'skill_version': skill.get('version'),
                    'updated_at': now,
                }},
                upsert=True,
            )

    def promote_fae_skill_candidate(self, skill_id, candidate_id, actor=None):
        """Promote a validated candidate and apply its parameters atomically enough for local Mongo."""
        try:
            candidate = self.fae_skill_candidates_collection.find_one({
                'candidate_id': str(candidate_id),
                'skill_id': str(skill_id),
                'status': 'validated',
            }, {'_id': 0})
            if not candidate:
                return {'success': False, 'message': '候选版本不存在或尚未通过验证'}
            active = self.fae_skill_versions_collection.find_one({
                'skill_id': str(skill_id), 'status': 'active'
            }, {'_id': 0})
            if not active or active.get('version') != candidate.get('parent_version'):
                return {'success': False, 'message': '线上版本已经变化，请重新生成候选'}
            now = datetime.utcnow().isoformat() + 'Z'
            self.fae_skill_versions_collection.update_many(
                {'skill_id': str(skill_id), 'status': 'active'},
                {'$set': {'status': 'superseded', 'superseded_at': now}},
            )
            version_payload = {
                'skill_id': str(skill_id),
                'label': candidate.get('label') or active.get('label'),
                'description': active.get('description'),
                'guidance': candidate.get('guidance') or active.get('guidance'),
                'schema_version': active.get('schema_version'),
                'engine_version': ENGINE_VERSION,
                'version': candidate.get('proposed_version'),
                'parent_version': active.get('version'),
                'status': 'active',
                'parameters': candidate.get('parameters') or {},
                'learning_snapshot': candidate.get('learning_snapshot') or {},
                'evaluation': candidate.get('evaluation') or {},
                'changes': candidate.get('changes') or [],
                'source': 'promoted-candidate',
                'candidate_id': candidate.get('candidate_id'),
                'created_at': now,
                'activated_at': now,
                'activated_by': actor,
            }
            self.fae_skill_versions_collection.update_one(
                {
                    'skill_id': str(skill_id),
                    'version': version_payload['version'],
                },
                {'$set': version_payload},
                upsert=True,
            )
            self._apply_fae_skill_parameters(version_payload)
            self.fae_skill_candidates_collection.update_one(
                {'candidate_id': str(candidate_id)},
                {'$set': {
                    'status': 'promoted',
                    'promoted_at': now,
                    'promoted_by': actor,
                }},
            )
            deployment = {
                'deployment_id': sha256(
                    f"{skill_id}:{version_payload['version']}:{now}".encode()
                ).hexdigest()[:24],
                'skill_id': str(skill_id),
                'label': version_payload.get('label'),
                'action': 'promote',
                'previous_version': active.get('version'),
                'version': version_payload['version'],
                'candidate_id': str(candidate_id),
                'deployed_at': now,
                'deployed_by': actor,
            }
            self.fae_skill_deployments_collection.insert_one(deployment)
            return {'success': True, 'data': version_payload}
        except Exception as e:
            self.logger.error(f"发布 FAE Skill 失败: {str(e)}")
            return {'success': False, 'message': str(e)}

    def rollback_fae_skill(self, skill_id, actor=None):
        """Rollback an active Skill to the version it replaced."""
        try:
            active = self.fae_skill_versions_collection.find_one({
                'skill_id': str(skill_id), 'status': 'active'
            }, {'_id': 0})
            if not active:
                return {'success': False, 'message': '没有找到当前线上版本'}
            deployment = self.fae_skill_deployments_collection.find_one(
                {
                    'skill_id': str(skill_id),
                    'version': active.get('version'),
                    'previous_version': {'$exists': True},
                },
                {'_id': 0},
                sort=[('deployed_at', DESCENDING)],
            )
            if not deployment or deployment.get('action') != 'promote':
                return {'success': False, 'message': '当前版本没有可执行的回滚点'}
            previous_version = (
                deployment.get('previous_version') if deployment else None
            )
            target = (
                self.fae_skill_versions_collection.find_one({
                    'skill_id': str(skill_id),
                    'version': previous_version,
                }, {'_id': 0})
                if previous_version else None
            )
            if not target:
                return {'success': False, 'message': '没有可回滚的历史版本'}
            now = datetime.utcnow().isoformat() + 'Z'
            self.fae_skill_versions_collection.update_one(
                {
                    'skill_id': str(skill_id),
                    'version': active.get('version'),
                },
                {'$set': {'status': 'rolled_back', 'rolled_back_at': now}},
            )
            self.fae_skill_versions_collection.update_one(
                {
                    'skill_id': str(skill_id),
                    'version': target.get('version'),
                },
                {'$set': {
                    'status': 'active',
                    'activated_at': now,
                    'activated_by': actor,
                }},
            )
            target['status'] = 'active'
            target['activated_at'] = now
            self._apply_fae_skill_parameters(target)
            self.fae_skill_deployments_collection.insert_one({
                'deployment_id': sha256(
                    f"{skill_id}:rollback:{target.get('version')}:{now}".encode()
                ).hexdigest()[:24],
                'skill_id': str(skill_id),
                'label': target.get('label'),
                'action': 'rollback',
                'previous_version': active.get('version'),
                'version': target.get('version'),
                'deployed_at': now,
                'deployed_by': actor,
            })
            return {'success': True, 'data': target}
        except Exception as e:
            self.logger.error(f"回滚 FAE Skill 失败: {str(e)}")
            return {'success': False, 'message': str(e)}

    def save_fae_review(self, review_data):
        """保存复盘、累计证据，并生成待发布的 Skill 候选。"""
        try:
            match_id = str(review_data.get('match_id') or '')
            version = str(review_data.get('engine_version') or ENGINE_VERSION)
            key = {'match_id': match_id, 'engine_version': version}
            existing = self.fae_reviews_collection.find_one(key, {'_id': 0})
            if existing:
                return {'saved': True, 'new': False, 'review': existing, 'adjustments': []}

            adjustments = []
            learning_signals = []
            minimum_samples = max(3, int(os.getenv('FAE_LEARNING_MIN_SAMPLES', '10')))
            active_weights = self.get_fae_rule_weights()
            for result in review_data.get('rule_results') or []:
                if result.get('hit') not in (True, False) or not result.get('rule_id'):
                    continue
                rule_id = str(result['rule_id'])
                current = self.fae_rule_weights_collection.find_one(
                    {'rule_id': rule_id}, {'_id': 0}
                ) or {}
                samples = int(current.get('samples') or 0) + 1
                hits = int(current.get('hits') or 0) + (1 if result['hit'] else 0)
                accuracy = hits / samples
                previous_weight = float(active_weights.get(
                    rule_id, DEFAULT_RULE_WEIGHTS.get(rule_id, 1.0)
                ))
                suggested_weight = previous_weight
                action = 'hold'
                if samples >= minimum_samples:
                    if accuracy >= 0.80:
                        suggested_weight = min(1.30, previous_weight + 0.05)
                        action = 'increase'
                    elif accuracy < 0.60:
                        suggested_weight = max(0.70, previous_weight - 0.05)
                        action = 'decrease'
                self.fae_rule_weights_collection.update_one(
                    {'rule_id': rule_id},
                    {'$set': {
                        'rule_id': rule_id,
                        'engine_version': version,
                        'weight': round(previous_weight, 3),
                        'suggested_weight': round(suggested_weight, 3),
                        'suggested_action': action,
                        'samples': samples,
                        'hits': hits,
                        'accuracy': round(accuracy, 4),
                        'updated_at': datetime.utcnow().isoformat() + 'Z',
                    }},
                    upsert=True
                )
                if suggested_weight != previous_weight:
                    learning_signals.append({
                        'rule_id': rule_id,
                        'previous_weight': round(previous_weight, 3),
                        'suggested_weight': round(suggested_weight, 3),
                        'samples': samples,
                        'accuracy': round(accuracy, 4),
                        'action': action,
                    })

            payload = dict(review_data)
            payload['learning_adjustments'] = adjustments
            payload['learning_signals'] = learning_signals
            payload['learning_mode'] = 'staged-skill-version'
            self.fae_reviews_collection.insert_one(dict(payload))
            self.fae_analyses_collection.update_one(
                {'match_id': match_id},
                {'$set': {
                    'review_status': 'completed',
                    'reviewed_at': payload.get('reviewed_at'),
                    'review_summary': payload.get('prediction'),
                }}
            )
            candidates = self.generate_fae_skill_candidates()
            return {
                'saved': True,
                'new': True,
                'review': payload,
                'adjustments': adjustments,
                'candidates': candidates,
            }
        except Exception as e:
            self.logger.error(f"保存 FAE 复盘失败: {str(e)}")
            return {'saved': False, 'new': False, 'message': str(e), 'adjustments': []}

    def get_fae_review(self, match_id, engine_version=None):
        """读取指定比赛的赛后复盘。"""
        try:
            query = {'match_id': str(match_id)}
            if engine_version:
                query['engine_version'] = str(engine_version)
            return self.fae_reviews_collection.find_one(query, {'_id': 0})
        except Exception as e:
            self.logger.error(f"读取 FAE 复盘失败: {str(e)}")
            return None

    def get_fae_rankings(self, owner_date):
        """每个玩法独立评分，输出每日榜单与危险盘口。"""
        analyses = list(self.fae_analyses_collection.find(
            {'owner_date': str(owner_date)[:10]}, {'_id': 0}
        ))
        match_ids = [
            str(item.get('match_id')) for item in analyses if item.get('match_id')
        ]
        match_by_id = {
            str(item.get('match_id')): item
            for item in self.matches_collection.find(
                {'match_id': {'$in': match_ids}},
                {
                    '_id': 0,
                    'match_id': 1,
                    'league': 1, 'match_time': 1, 'handicap': 1, 'status': 1,
                    'euro_current_win': 1, 'euro_initial_win': 1,
                    'euro_current_draw': 1, 'euro_initial_draw': 1,
                    'euro_current_lose': 1, 'euro_initial_lose': 1,
                    'hi_current_home_odds': 1, 'hi_initial_home_odds': 1,
                    'hi_current_draw_odds': 1, 'hi_initial_draw_odds': 1,
                    'hi_current_away_odds': 1, 'hi_initial_away_odds': 1,
                    'ou_current_over_odds': 1, 'ou_initial_over_odds': 1,
                    'ou_current_under_odds': 1, 'ou_initial_under_odds': 1,
                }
            )
        }
        groups = {}
        dangerous = []
        ranking_labels = {'主胜', '平局', '客胜', '让胜', '让平', '让负'}

        odds_fields = {
            '主胜': ('euro_current_win', 'euro_initial_win'),
            '平局': ('euro_current_draw', 'euro_initial_draw'),
            '客胜': ('euro_current_lose', 'euro_initial_lose'),
            '让胜': ('hi_current_home_odds', 'hi_initial_home_odds'),
            '让平': ('hi_current_draw_odds', 'hi_initial_draw_odds'),
            '让负': ('hi_current_away_odds', 'hi_initial_away_odds'),
            '大球': ('ou_current_over_odds', 'ou_initial_over_odds'),
            '小球': ('ou_current_under_odds', 'ou_initial_under_odds'),
        }

        def category_odds(match, label):
            fields = odds_fields.get(label)
            if not fields:
                return None, None
            current, initial = match.get(fields[0]), match.get(fields[1])
            if current not in (None, ''):
                return current, '即时'
            if initial not in (None, ''):
                return initial, '初盘'
            return None, None

        def candidate(label, probability, baseline, overall, risk_score, market):
            edge = max(0, probability - baseline)
            score = max(0, min(
                99,
                round(48 + edge * 0.75 + overall * 0.25 - risk_score * 0.16)
            ))
            confidence = min(88, max(35, round((score + probability) / 2)))
            stars = max(1.0, min(5.0, round((score / 20) * 2) / 2))
            full_stars = max(0, min(5, round(stars)))
            return {
                'label': label,
                'probability': probability,
                'score': score,
                'confidence': confidence,
                'stars': stars,
                'star_text': '★' * full_stars + '☆' * (5 - full_stars),
                'market': market,
            }

        for item in analyses:
            analysis = item.get('analysis') or {}
            recommendation = analysis.get('recommendation') or {}
            risk = analysis.get('risk') or {}
            match = match_by_id.get(str(item.get('match_id')), {})
            probabilities = analysis.get('probabilities') or {}
            categories = recommendation.get('category_scores') or []
            if not categories:
                overall = int(analysis.get('overall_score') or 0)
                risk_score = int(risk.get('score') or 0)
                outcome_fields = (
                    ('主胜', probabilities.get('home_win'), 34, '胜平负'),
                    ('平局', probabilities.get('draw'), 34, '胜平负'),
                    ('客胜', probabilities.get('away_win'), 34, '胜平负'),
                )
                for label, probability, baseline, market in outcome_fields:
                    if probability is not None:
                        categories.append(candidate(
                            label, int(probability), baseline, overall, risk_score, market
                        ))
                hhad = probabilities.get('hhad') or {}
                for key, label in (('win', '让胜'), ('draw', '让平'), ('lose', '让负')):
                    if hhad.get(key) is not None:
                        categories.append(candidate(
                            label, int(hhad[key]), 34, overall, risk_score, '竞彩让球'
                        ))
                totals = probabilities.get('over_under') or {}
                for key, label in (('over', '大球'), ('under', '小球')):
                    if totals.get(key) is not None:
                        categories.append(candidate(
                            label, int(totals[key]), 50, overall, risk_score, '大小球'
                        ))

            base_row = {
                'match_id': item.get('match_id'),
                'match_number': item.get('match_number'),
                'home_team': item.get('home_team'),
                'away_team': item.get('away_team'),
                'league': match.get('league'),
                'match_time': match.get('match_time'),
                'status': match.get('status'),
                'risk': risk,
                'engine_version': (item.get('engine') or {}).get('version'),
            }
            for category in categories:
                label = category.get('label')
                if not label or label not in ranking_labels:
                    continue
                if category.get('no_bet'):
                    continue
                odds, odds_source = category_odds(match, label)
                row = {
                    **base_row,
                    'recommendation': label,
                    'market': category.get('market'),
                    'handicap': (
                        probabilities.get('sporttery_handicap')
                        if str(label).startswith('让') else None
                    ),
                    'odds': odds,
                    'odds_source': odds_source,
                    'probability': category.get('probability', 0),
                    'score': category.get('score', 0),
                    'prediction_score': category.get('prediction_score'),
                    'bet_score': category.get(
                        'bet_score', category.get('score', 0)
                    ),
                    'value_score': category.get('value_score'),
                    'market_implied_probability': category.get(
                        'market_implied_probability'
                    ),
                    'expected_return': category.get('expected_return'),
                    'confidence': category.get('confidence', 0),
                    'stars': category.get('stars', 0),
                    'star_text': category.get('star_text'),
                    'is_primary': label == recommendation.get('primary'),
                }
                groups.setdefault(label, []).append(row)
            if (
                risk.get('dangerous')
                or risk.get('score', 0) >= 65
                or recommendation.get('no_bet')
            ):
                primary_odds, primary_odds_source = category_odds(
                    match, recommendation.get('primary')
                )
                dangerous.append({
                    **base_row,
                    'recommendation': recommendation.get('primary'),
                    'odds': primary_odds,
                    'odds_source': primary_odds_source,
                    'score': recommendation.get('score', 0),
                    'bet_score': recommendation.get(
                        'bet_score', recommendation.get('score', 0)
                    ),
                    'value_score': recommendation.get('value_score'),
                    'market_confidence': recommendation.get(
                        'market_confidence'
                    ),
                    'no_bet': recommendation.get('no_bet', False),
                    'no_bet_reasons': recommendation.get(
                        'no_bet_reasons', []
                    ),
                    'confidence': recommendation.get('confidence', 0),
                    'stars': recommendation.get('stars', 0),
                    'star_text': recommendation.get('star_text'),
                })
        for rows in groups.values():
            rows.sort(key=lambda row: (row['score'], row['confidence']), reverse=True)
        dangerous.sort(key=lambda row: row['risk'].get('score', 0), reverse=True)
        return {
            'date': str(owner_date)[:10],
            'engine_version': ENGINE_VERSION,
            'groups': groups,
            'dangerous': dangerous,
            'count': len(analyses),
        }

    def get_fae_draw_parlays(self, owner_date):
        """生成当天每场平/让平方向及2串1、3串1组合。"""
        return build_draw_parlays(
            self.get_fae_rankings(owner_date),
            strategy_weights=self.get_fae_draw_strategy_weights(),
        )

    def get_fae_draw_strategy_weights(self):
        """读取线上 draw-strategy Skill 权重。"""
        weights = {'平局': 1.0, '让平': 1.0}
        try:
            active = self.fae_skill_versions_collection.find_one({
                'skill_id': 'draw-strategy', 'status': 'active'
            }, {'_id': 0})
            configured = (
                ((active or {}).get('parameters') or {}).get('strategy_weights')
                or {}
            )
            if configured:
                for selection in weights:
                    weights[selection] = float(configured.get(selection) or 1)
            else:
                weights.update(self._legacy_fae_draw_strategy_weights())
        except Exception as e:
            self.logger.error(f"读取平/让平策略权重失败: {str(e)}")
        return weights

    def save_fae_draw_snapshot(self, plan):
        """保存不可变推荐快照；相同内容不会重复写入。"""
        try:
            if not plan or not plan.get('match_recommendations'):
                return None
            content = {
                key: plan.get(key)
                for key in (
                    'date', 'engine_version', 'focus', 'strategy_weights',
                    'match_count', 'match_recommendations', 'two_leg',
                    'three_leg', 'method', 'disclaimer',
                )
            }
            encoded = json.dumps(
                content, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), default=str
            ).encode('utf-8')
            snapshot_hash = sha256(encoded).hexdigest()
            existing = self.fae_draw_snapshots_collection.find_one(
                {'snapshot_hash': snapshot_hash}, {'_id': 0}
            )
            if existing:
                return existing
            picks = content.get('match_recommendations') or []
            eligible = bool(picks) and all(
                pick.get('status') in (0, '0') for pick in picks
            )
            payload = {
                **content,
                'owner_date': str(content.get('date') or '')[:10],
                'snapshot_hash': snapshot_hash,
                'eligible_for_review': eligible,
                'generated_at': datetime.utcnow().isoformat() + 'Z',
            }
            self.fae_draw_snapshots_collection.insert_one(dict(payload))
            return payload
        except Exception as e:
            self.logger.error(f"保存平/让平推荐快照失败: {str(e)}")
            return None

    def get_fae_draw_snapshot(self, owner_date):
        """返回当天最后一份所有比赛均未开赛的可复盘快照。"""
        try:
            query = {
                'owner_date': str(owner_date)[:10],
                'eligible_for_review': True,
            }
            return self.fae_draw_snapshots_collection.find_one(
                query, {'_id': 0}, sort=[('generated_at', DESCENDING)]
            )
        except Exception as e:
            self.logger.error(f"读取平/让平推荐快照失败: {str(e)}")
            return None

    def get_fae_draw_snapshot_dates(self, limit=7):
        try:
            dates = sorted(
                value for value in self.fae_draw_snapshots_collection.distinct(
                    'owner_date'
                ) if value
            )
            return dates[-max(1, int(limit)):]
        except Exception as e:
            self.logger.error(f"读取平/让平快照日期失败: {str(e)}")
            return []

    def save_fae_draw_review(self, review):
        """保存专项复盘、更新统计，并生成待发布的 Skill 候选。"""
        try:
            key = {
                'owner_date': str(review.get('owner_date') or '')[:10],
                'engine_version': str(
                    review.get('engine_version') or ENGINE_VERSION
                ),
            }
            payload = {**review, **key}
            self.fae_draw_reviews_collection.update_one(
                key, {'$set': payload}, upsert=True
            )
            weights = self._recalculate_fae_draw_strategy_weights(apply=False)
            candidates = self.generate_fae_skill_candidates()
            return {
                'saved': True,
                'review': payload,
                'strategy_weights': weights,
                'candidates': candidates,
            }
        except Exception as e:
            self.logger.error(f"保存平/让平专项复盘失败: {str(e)}")
            return {'saved': False, 'message': str(e)}

    def _recalculate_fae_draw_strategy_weights(self, apply=False):
        ai_reviews = list(
            self.fae_daily_ai_reviews_collection.find({}, {'_id': 0})
        )
        if ai_reviews:
            stats = aggregate_daily_ai_reviews(ai_reviews)
        else:
            reviews = list(
                self.fae_draw_reviews_collection.find({}, {'_id': 0})
            )
            stats = aggregate_draw_reviews(reviews)
        minimum_samples = max(
            3, int(os.getenv('FAE_LEARNING_MIN_SAMPLES', '10'))
        )
        active_weights = self.get_fae_draw_strategy_weights()
        weights = {}
        for selection in ('平局', '让平'):
            if selection == '让平':
                summary = (
                    (stats.get('handicap_by_selection') or {}).get(selection)
                    or (stats.get('by_selection') or {}).get(selection)
                    or {}
                )
            else:
                summary = (
                    (stats.get('by_selection') or {}).get(selection) or {}
                )
            samples = int(summary.get('settled') or 0)
            roi = float(summary.get('roi') or 0)
            weight = float(active_weights.get(selection) or 1.0)
            suggested_weight = weight
            action = 'hold'
            if samples >= minimum_samples and abs(roi) >= 5:
                steps = min(6, max(1, int(abs(roi) / 10)))
                if roi > 0:
                    suggested_weight = min(1.30, 1 + steps * 0.05)
                    action = 'increase'
                else:
                    suggested_weight = max(0.70, 1 - steps * 0.05)
                    action = 'decrease'
            stored_weight = suggested_weight if apply else weight
            payload = {
                'selection': selection,
                'engine_version': ENGINE_VERSION,
                'weight': round(stored_weight, 3),
                'suggested_weight': round(suggested_weight, 3),
                'suggested_action': action,
                'samples': samples,
                'hits': int(summary.get('hits') or 0),
                'hit_rate': float(summary.get('hit_rate') or 0),
                'roi': roi,
                'action': action if apply else 'hold',
                'updated_at': datetime.utcnow().isoformat() + 'Z',
            }
            self.fae_draw_strategy_weights_collection.update_one(
                {'selection': selection}, {'$set': payload}, upsert=True
            )
            weights[selection] = payload
        return weights

    def get_fae_draw_review(self, owner_date):
        try:
            return self.fae_draw_reviews_collection.find_one(
                {'owner_date': str(owner_date)[:10]}, {'_id': 0},
                sort=[('reviewed_at', DESCENDING)]
            )
        except Exception as e:
            self.logger.error(f"读取平/让平专项复盘失败: {str(e)}")
            return None

    def get_fae_draw_review_stats(self):
        reviews = list(self.fae_draw_reviews_collection.find({}, {'_id': 0}))
        weight_docs = list(self.fae_draw_strategy_weights_collection.find(
            {}, {'_id': 0}
        ))
        active_weights = self.get_fae_draw_strategy_weights()
        weights = {
            item.get('selection'): {
                **item,
                'weight': active_weights.get(item.get('selection'), 1.0),
            } for item in weight_docs
            if item.get('selection')
        }
        for selection, weight in active_weights.items():
            weights.setdefault(selection, {
                'selection': selection,
                'weight': weight,
                'action': 'hold',
            })
        return aggregate_draw_reviews(reviews, weights)

    def get_fae_version_info(self):
        """返回版本能力与实时规则命中率。"""
        self.ensure_fae_version()
        version = self.fae_versions_collection.find_one(
            {'version': ENGINE_VERSION}, {'_id': 0}
        ) or dict(VERSION_MANIFEST)
        version['rules'] = list(self.fae_rule_weights_collection.find(
            {}, {'_id': 0}
        ).sort('accuracy', DESCENDING))
        return version
    
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
    
    def save_user_pick(self, pick_data):
        """
        保存用户选择
        
        Args:
            pick_data: 用户选择数据字典
            
        Returns:
            result: 插入结果
        """
        try:
            # 添加时间戳
            if 'created_at' not in pick_data:
                pick_data['created_at'] = datetime.now()
            pick_data['updated_at'] = datetime.now()
            
            # 使用upsert，根据device_id和match_id唯一确定
            result = self.user_picks_collection.update_one(
                {
                    'device_id': pick_data.get('device_id'),
                    'match_id': pick_data.get('match_id')
                },
                {'$set': pick_data},
                upsert=True
            )
            
            if result.upserted_id:
                self.logger.info(f"新增用户选择: {pick_data.get('match_id')} - {pick_data.get('device_id')}")
            else:
                self.logger.info(f"更新用户选择: {pick_data.get('match_id')} - {pick_data.get('device_id')}")
                
            return result
            
        except Exception as e:
            self.logger.error(f"保存用户选择失败: {str(e)}")
            return None
            
    def get_user_picks(self, device_id, limit=None):
        """
        获取用户选择列表
        
        Args:
            device_id: 设备ID
            limit: 返回数量限制
            
        Returns:
            picks: 用户选择列表
        """
        try:
            query = {'device_id': device_id}
            cursor = self.user_picks_collection.find(query, {'_id': 0})
            cursor = cursor.sort('created_at', DESCENDING)
            
            if limit:
                cursor = cursor.limit(limit)
            
            return list(cursor)
            
        except Exception as e:
            self.logger.error(f"获取用户选择列表失败: {str(e)}")
            return []

    def delete_user_pick(self, device_id, match_id):
        """
        删除用户选择
        
        Args:
            device_id: 设备ID
            match_id: 比赛ID
            
        Returns:
            success: 是否成功
        """
        try:
            result = self.user_picks_collection.delete_one({
                'device_id': device_id,
                'match_id': match_id
            })
            if result.deleted_count > 0:
                self.logger.info(f"删除用户选择: {match_id} - {device_id}")
                return True
            return False
        except Exception as e:
            self.logger.error(f"删除用户选择失败: {str(e)}")
            return False

    def close(self):
        """关闭数据库连接"""
        try:
            self.client.close()
            self.logger.info("MongoDB连接已关闭")
        except Exception as e:
            self.logger.error(f"关闭MongoDB连接失败: {str(e)}")

    def save_bet(self, bet_data):
        """
        保存投注记录
        """
        try:
            if 'created_at' not in bet_data:
                bet_data['created_at'] = datetime.utcnow()
            bet_data['updated_at'] = datetime.utcnow()
            
            # 自动生成 bet_id (如果未提供)
            if 'bet_id' not in bet_data:
                import uuid
                bet_data['bet_id'] = str(uuid.uuid4())
            
            # 兼容旧数据：如果没有group_id，使用bet_id
            if 'group_id' not in bet_data:
                bet_data['group_id'] = bet_data['bet_id']
            
            self.bets_collection.insert_one(bet_data)
            self.logger.info(f"保存投注记录: {bet_data['bet_id']}")
            return True
        except Exception as e:
            self.logger.error(f"保存投注失败: {str(e)}")
            return False

    def get_bets(self, device_id, limit=100, status=None):
        """获取投注列表"""
        try:
            query = {'device_id': device_id}
            if status:
                query['status'] = status
            
            cursor = self.bets_collection.find(query, {'_id': 0}).sort('created_at', DESCENDING)
            if limit:
                cursor = cursor.limit(limit)
            return list(cursor)
        except Exception as e:
            self.logger.error(f"获取投注列表失败: {str(e)}")
            return []
            
    def get_bet_groups(self, device_id, limit=50):
        """获取分组后的投注列表"""
        try:
            pipeline = [
                {'$match': {'device_id': device_id}},
                {'$sort': {'created_at': -1}},
                {'$group': {
                    '_id': {'$ifNull': ['$group_id', '$bet_id']},
                    'created_at': {'$max': '$created_at'},
                    'total_stake': {'$sum': '$stake'},
                    'total_return': {'$sum': {'$ifNull': ['$actual_return', 0]}},
                    'ticket_count': {'$sum': 1},
                    'statuses': {'$addToSet': '$status'},
                    'desc_list': {'$push': '$desc'},
                    'bets': {'$push': '$$ROOT'}
                }},
                {'$sort': {'created_at': -1}},
                {'$limit': limit}
            ]
            
            groups = list(self.bets_collection.aggregate(pipeline))
            
            # 处理分组状态和结构
            result = []
            for g in groups:
                statuses = g['statuses']
                # 状态逻辑：
                # 只要有一个pending -> pending
                # 只要有一个won -> won (或者finished)
                # 全部lost -> lost
                # 实际上如果是一单多注，通常是只要有一注中奖就算中奖（total_return > 0）
                
                final_status = 'lost'
                if 'pending' in statuses:
                    final_status = 'pending'
                elif g['total_return'] > 0:
                    final_status = 'won'
                
                # 提取第一注的信息作为摘要
                first_bet = g['bets'][0] if g['bets'] else {}
                
                # 处理 bets 中的 ObjectId
                cleaned_bets = []
                for b in g['bets']:
                    if '_id' in b:
                        b['_id'] = str(b['_id'])
                    cleaned_bets.append(b)
                
                result.append({
                    'group_id': g['_id'],
                    'created_at': g['created_at'],
                    'total_stake': g['total_stake'],
                    'total_return': g['total_return'],
                    'ticket_count': g['ticket_count'],
                    'status': final_status,
                    'desc': first_bet.get('desc', '多注组合'), # 简单展示
                    'bets': cleaned_bets # 包含详情
                })
            return result
            
        except Exception as e:
            self.logger.error(f"获取分组投注失败: {str(e)}")
            return []

    def delete_bet_group(self, device_id, group_id):
        """删除投注记录（按组）"""
        try:
            # 兼容：如果group_id匹配不到，尝试匹配bet_id
            result = self.bets_collection.delete_many({
                'device_id': device_id,
                '$or': [
                    {'group_id': group_id},
                    {'bet_id': group_id}
                ]
            })
            if result.deleted_count > 0:
                self.logger.info(f"删除投注组: {group_id} - {device_id}")
                return True
            return False
        except Exception as e:
            self.logger.error(f"删除投注组失败: {str(e)}")
            return False

    def update_bet(self, bet_id, updates):
        """更新投注状态"""
        try:
            updates['updated_at'] = datetime.utcnow()
            result = self.bets_collection.update_one(
                {'bet_id': bet_id},
                {'$set': updates}
            )
            return result.modified_count > 0
        except Exception as e:
            self.logger.error(f"更新投注失败: {str(e)}")
            return False

    def get_bet_stats(self, device_id):
        """获取投注统计"""
        try:
            pipeline = [
                {'$match': {'device_id': device_id}},
                {'$group': {
                    '_id': None,
                    'total_bets': {'$sum': 1},
                    'total_stake': {'$sum': '$stake'},
                    'total_return': {'$sum': {'$ifNull': ['$actual_return', 0]}},
                    'pending_bets': {'$sum': {'$cond': [{'$eq': ['$status', 'pending']}, 1, 0]}},
                    'won_bets': {'$sum': {'$cond': [{'$eq': ['$status', 'won']}, 1, 0]}}
                }}
            ]
            result = list(self.bets_collection.aggregate(pipeline))
            if result:
                stats = result[0]
                stats.pop('_id', None)
                stats['net_profit'] = stats['total_return'] - stats['total_stake']
                return stats
            return {'total_bets': 0, 'total_stake': 0, 'total_return': 0, 'net_profit': 0, 'pending_bets': 0, 'won_bets': 0}
        except Exception as e:
            self.logger.error(f"获取投注统计失败: {str(e)}")
            return {}

    def get_daily_stats(self, device_id):
        """获取每日收益统计"""
        try:
            pipeline = [
                {'$match': {'device_id': device_id}},
                {'$addFields': {
                    'date_str': {'$dateToString': {'format': '%Y-%m-%d', 'date': '$created_at'}}
                }},
                {'$group': {
                    '_id': '$date_str',
                    'daily_stake': {'$sum': '$stake'},
                    'daily_return': {'$sum': {'$ifNull': ['$actual_return', 0]}},
                    'bet_count': {'$sum': 1}
                }},
                {'$sort': {'_id': -1}},
                {'$limit': 30}
            ]
            results = list(self.bets_collection.aggregate(pipeline))
            formatted = []
            for r in results:
                formatted.append({
                    'date': r['_id'],
                    'stake': r['daily_stake'],
                    'return': r['daily_return'],
                    'profit': r['daily_return'] - r['daily_stake'],
                    'count': r['bet_count']
                })
            return formatted
        except Exception as e:
            self.logger.error(f"获取每日统计失败: {str(e)}")
            return []


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

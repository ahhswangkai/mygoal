"""
足球数据展示Web服务
"""
from flask import Flask, render_template, jsonify, request, Response, stream_with_context, session


from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
import base64
import hashlib
import json
from bet_settlement import (
    available_bet_results,
    merge_database_results,
    merge_rescheduled_void_results,
    settle_bet,
)
from calculator_math import calculate_max_bonus, calculate_notes
from crawler import FootballCrawler, is_pregame_match
from fae_schedule import app_timezone, prematch_analysis_due
from match_time_utils import sort_matches_by_datetime
from user_storage import UserStorage
from football_ai import (
    FAEAIReviewAnalyzer,
    FAEDailyAIAnalyzer,
    FAEDailyAIReviewEngine,
    FAESupervisedBacktestEngine,
    FAESupervisedPredictor,
    FAEShadowBacktestEngine,
    FAEDrawReviewEngine,
    FAEError,
    FAEReviewEngine,
    FootballAIEngine,
    ArkNarrativeClient,
    DAILY_AI_MAX_BATCH_SIZE,
    ENGINE_VERSION,
    SKILL_DEFINITIONS,
    build_daily_match_input,
    build_training_days,
    compact_daily_ai_run,
)

from db_storage import MongoDBStorage
from prediction_engine import PredictionEngine
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except Exception as e:
    print(f"⚠️  APScheduler导入失败: {str(e)}")
    BackgroundScheduler = None
    CronTrigger = None
import os
import re
import requests
import random
import threading
import uuid
from config import WECOM_WEBHOOK_URL
from wecom_notifier import (
    WeComNotifier,
    delivery_key as wecom_delivery_key,
    format_daily_ai_message,
    format_live_alert_message,
    format_review_message,
)
from json_repair import repair_json



app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.config['JSONIFY_MIMETYPE'] = "application/json; charset=utf-8"
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'mygoal-local-session-key-change-in-production')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

user_storage = UserStorage(
    os.getenv('USER_DATABASE_PATH', os.path.join(os.path.dirname(__file__), 'data', 'users.db'))
)
# 计算器访问体彩接口时必须直连。requests 默认读取 HTTP(S)_PROXY
# 环境变量，服务器若配置了代理会导致体彩接口超时或被 WAF 拦截。
sporttery_calculator_session = requests.Session()
sporttery_calculator_session.trust_env = False
settlement_lock = threading.Lock()
wecom_notifier = WeComNotifier(WECOM_WEBHOOK_URL)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id or not user_storage.get_user(user_id):
            session.pop('user_id', None)
            return jsonify({'success': False, 'message': '请先登录', 'code': 'AUTH_REQUIRED'}), 401
        return view(*args, **kwargs)
    return wrapped


def _fae_admin_user():
    user_id = session.get('user_id')
    user = user_storage.get_user(user_id) if user_id else None
    if not user:
        return None
    allowed = {
        item.strip().lower()
        for item in os.getenv('FAE_ADMIN_USERNAMES', '').split(',')
        if item.strip()
    }
    return user if str(user.get('username') or '').lower() in allowed else None


def fae_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get('user_id')
        user = user_storage.get_user(user_id) if user_id else None
        if not user:
            return jsonify({
                'success': False,
                'message': '请先登录',
                'code': 'AUTH_REQUIRED',
            }), 401
        if not _fae_admin_user():
            return jsonify({
                'success': False,
                'message': '当前账号没有 FAE Skill 发布权限',
                'code': 'FAE_ADMIN_REQUIRED',
            }), 403
        return view(*args, **kwargs)
    return wrapped


@app.after_request
def after_request(response):
    """添加CORS响应头"""
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# 初始化爬虫和存储
crawler = FootballCrawler()
ai_analysis_service = FootballAIEngine()
fae_review_engine = FAEReviewEngine()
fae_draw_review_engine = FAEDrawReviewEngine()
fae_daily_ai_review_engine = FAEDailyAIReviewEngine()
fae_shadow_backtest_engine = FAEShadowBacktestEngine()
fae_supervised_backtest_engine = FAESupervisedBacktestEngine()
fae_ai_review_analyzer = FAEAIReviewAnalyzer(ArkNarrativeClient(
    timeout=max(90, int(os.getenv('FAE_AI_REVIEW_TIMEOUT', '180'))),
    stream=True,
    max_tokens=max(
        1000, int(os.getenv('FAE_AI_REVIEW_MAX_TOKENS', '4096'))
    ),
    thinking=os.getenv('FAE_AI_REVIEW_THINKING', 'disabled'),
    json_mode=True,
))
fae_ai_review_analyzer.client.max_retries = max(
    0, int(os.getenv('FAE_AI_REVIEW_MAX_RETRIES', '0'))
)
fae_daily_ai_analyzer = FAEDailyAIAnalyzer(ArkNarrativeClient(
    timeout=max(90, int(os.getenv('FAE_DAILY_AI_TIMEOUT', '180'))),
    stream=True,
    max_tokens=max(1000, int(os.getenv('FAE_DAILY_AI_MAX_TOKENS', '4096'))),
    thinking=os.getenv('FAE_DAILY_AI_THINKING', 'disabled'),
    json_mode=True,
))
fae_daily_ai_analyzer.client.max_retries = max(
    0, int(os.getenv('FAE_DAILY_AI_MAX_RETRIES', '0'))
)
fae_daily_ai_lock = threading.Lock()
fae_ai_review_lock = threading.Lock()


# 初始化MongoDB存储（优先使用MongoDB，如果连接失败则使用文件存储）
try:
    mongo_storage = MongoDBStorage()
    use_mongodb = True
    print("✅ 使用MongoDB数据库")
except Exception as e:
    mongo_storage = None
    use_mongodb = False
    print(f"⚠️  MongoDB连接失败，使用文件存储: {str(e)}")


def _env_enabled(name, default=True):
    fallback = 'true' if default else 'false'
    return os.getenv(name, fallback).lower() in ('1', 'true', 'yes', 'on')


def _wecom_delivery_status():
    return {
        'configured': wecom_notifier.configured,
        'enabled': _env_enabled('WECOM_ENABLED', True),
        'daily_ai': _env_enabled('WECOM_PUSH_DAILY_AI', True),
        'ai_review': _env_enabled('WECOM_PUSH_AI_REVIEW', True),
        'live_alerts': _env_enabled('WECOM_PUSH_LIVE_ALERTS', True),
    }


def _deliver_wecom_once(event_type, identity, content):
    """Send one sanitized markdown event with persistent deduplication."""
    if not _env_enabled('WECOM_ENABLED', True):
        return {
            'success': False,
            'status': 'disabled',
            'message': '企业微信通知已停用',
        }
    if not wecom_notifier.configured:
        return {
            'success': False,
            'status': 'not_configured',
            'message': '企业微信机器人 Webhook 尚未配置',
        }
    key = wecom_delivery_key(event_type, identity)
    if mongo_storage:
        existing = mongo_storage.get_wecom_delivery(key) or {}
        if existing.get('success'):
            return {
                'success': True,
                'status': 'deduplicated',
                'message': '该消息已经投递，已跳过重复发送',
                'delivery_key': key,
            }
        if not mongo_storage.claim_wecom_delivery(
            key,
            event_type,
            content_hash=wecom_delivery_key('content', content),
        ):
            return {
                'success': False,
                'status': 'in_progress',
                'message': '同一消息正在投递或已被其他进程处理',
                'delivery_key': key,
            }
    result = wecom_notifier.send_markdown(content)
    if mongo_storage:
        mongo_storage.save_wecom_delivery(
            key,
            event_type,
            result,
            content_hash=wecom_delivery_key('content', content),
        )
    return {**result, 'delivery_key': key}


def _push_wecom_daily_ai(result):
    if not _env_enabled('WECOM_PUSH_DAILY_AI', True):
        return {'success': False, 'status': 'disabled'}
    identity = (
        result.get('run_id')
        or result.get('input_hash')
        or result.get('owner_date')
    )
    return _deliver_wecom_once(
        'daily_ai',
        identity,
        format_daily_ai_message(result),
    )


def _push_wecom_ai_review(review):
    if (
        not _env_enabled('WECOM_PUSH_AI_REVIEW', True)
        or not review
        or not review.get('completed')
    ):
        return {'success': False, 'status': 'not_ready'}
    return _deliver_wecom_once(
        'ai_review',
        review.get('run_id') or review.get('owner_date'),
        format_review_message(review),
    )





def load_match_data():
    """加载比赛数据（仅MongoDB）"""
    if mongo_storage:
        return mongo_storage.get_matches()
    return []


@app.route('/')
def index():
    """首页 - 比赛列表"""
    return render_template('index.html')


@app.route('/stats')
def stats():
    """历史数据统计分析页面"""
    return render_template('stats.html')


@app.route('/api/stats/leagues')
def get_stats_leagues():
    """API - 获取所有联赛列表"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500

        # 获取所有有完场比赛的联赛
        pipeline = [
            {'$match': {'status': 2}},
            {'$group': {'_id': '$league'}},
            {'$sort': {'_id': 1}}
        ]
        result = list(mongo_storage.matches_collection.aggregate(pipeline))
        leagues = [r['_id'] for r in result if r['_id']]

        return jsonify({'success': True, 'leagues': leagues})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/stats/analyze')
def analyze_stats():
    """API - 分析历史数据统计"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500

        # 获取查询参数
        group_by = request.args.get('group_by', 'league')
        filter_type = request.args.get('filter_type', 'hi_home_up')
        min_change = float(request.args.get('min_change', 0.05))
        league_filter = request.args.get('league', '')
        min_count = int(request.args.get('min_count', 5))
        time_range = request.args.get('time_range', '')

        # 解析筛选类型
        # 格式: 赔率类型_方向_变化
        # hi_home_up = 让球盘 主胜 升水
        parts = filter_type.split('_')
        odds_type = parts[0]  # hi, euro, asian
        target = parts[1]  # home, away, win
        direction = parts[2]  # up=升水, down=降水

        # 构建查询条件 - 只统计完场比赛
        match_query = {'status': 2}

        if league_filter:
            match_query['league'] = league_filter

        # 时间范围筛选
        if time_range:
            # 计算 cutoff 日期
            from datetime import datetime, timedelta
            days = int(time_range)
            cutoff = datetime.now() - timedelta(days=days)
            # owner_date 格式是 YYYY-MM-DD，转换为日期比较
            match_query['owner_date'] = {'$gte': cutoff.strftime('%Y-%m-%d')}

        # 获取所有符合条件的比赛并计算
        matches = list(mongo_storage.matches_collection.find(match_query))

        # 根据条件筛选，并提取需要分组键和计算结果
        filtered_matches = []
        for m in matches:
            # 获取初始和当前的水位
            initial = None
            current = None

            if odds_type == 'hi':
                # 让球盘
                if target == 'home':
                    initial = m.get('hi_initial_home_odds')
                    current = m.get('hi_current_home_odds')
                elif target == 'away':
                    initial = m.get('hi_initial_away_odds')
                    current = m.get('hi_current_away_odds')
            elif odds_type == 'euro':
                # 欧赔
                if target == 'win':
                    initial = m.get('euro_initial_win')
                    current = m.get('euro_current_win')
            elif odds_type == 'asian':
                # 亚盘
                if target == 'home':
                    initial = m.get('asian_initial_home_odds')
                    current = m.get('asian_current_home_odds')

            if initial is None or current is None:
                continue

            # 转换为浮点数
            try:
                initial = float(initial)
                current = float(current)
            except (ValueError, TypeError):
                continue

            change = current - initial

            # 根据方向筛选
            if direction == 'up':
                # 升水，需要变化大于最小变化
                if change < min_change:
                    continue
            else:
                # 降水，需要变化小于负的最小变化
                if change > -min_change:
                    continue

            # 获取比赛结果
            home_score = m.get('home_score')
            away_score = m.get('away_score')

            if home_score is None or away_score is None:
                continue

            try:
                home_score = int(home_score) if str(home_score).isdigit() else None
                away_score = int(away_score) if str(away_score).isdigit() else None
            except:
                continue

            if home_score is None or away_score is None:
                continue

            # 确定分组键
            group_key = None
            if group_by == 'league':
                group_key = m.get('league', '未知联赛')
            elif group_by == 'handicap':
                # 按盘口大小分组
                handicap = m.get('hi_handicap_value')
                if handicap is None:
                    handicap = m.get('asian_current_handicap')
                if handicap is None:
                    continue
                try:
                    h = float(handicap)
                    if h <= -1.5:
                        group_key = '让一球/球半以上'
                    elif h <= -0.5:
                        group_key = '平手/半球 ~ 让一球'
                    elif h < 0:
                        group_key = '平手以下'
                    elif h <= 0.5:
                        group_key = '受让平手/半球'
                    else:
                        group_key = '受让一球以上'
                except:
                    continue
            elif group_by == 'change_range':
                # 按变化幅度分组
                abs_change = abs(change)
                if abs_change < 0.1:
                    group_key = '0.00-0.10'
                elif abs_change < 0.2:
                    group_key = '0.10-0.20'
                elif abs_change < 0.3:
                    group_key = '0.20-0.30'
                elif abs_change < 0.5:
                    group_key = '0.30-0.50'
                else:
                    group_key = '0.50以上'

            if not group_key:
                continue

            # 判断盘口方向是否命中
            # 对于让球盘主胜，命中 = 主队赢了就是盘口方向命中
            home_win = home_score > away_score
            handi_win = False

            if odds_type in ['hi', 'asian']:
                if target == 'home':
                    handi_win = home_win
                elif target == 'away':
                    handi_win = away_score > home_score
            elif odds_type == 'euro':
                if target == 'win':
                    handi_win = home_win

            filtered_matches.append({
                'group_key': group_key,
                'home_win': home_win,
                'draw': home_score == away_score,
                'away_win': away_score > home_score,
                'handi_win': handi_win,
                'change': change
            })

        # 分组统计
        from collections import defaultdict
        groups = defaultdict(lambda: {
            'total': 0,
            'home_win': 0,
            'draw': 0,
            'away_win': 0,
            'handi_win': 0,
            'sum_change': 0
        })

        overall_total = len(filtered_matches)
        overall_home_win = sum(1 for m in filtered_matches if m['home_win'])
        overall_draw = sum(1 for m in filtered_matches if m['draw'])
        overall_away_win = sum(1 for m in filtered_matches if m['away_win'])
        overall_sum_change = sum(m['change'] for m in filtered_matches)

        for m in filtered_matches:
            g = groups[m['group_key']]
            g['total'] += 1
            if m['home_win']:
                g['home_win'] += 1
            if m['draw']:
                g['draw'] += 1
            if m['away_win']:
                g['away_win'] += 1
            if m['handi_win']:
                g['handi_win'] += 1
            g['sum_change'] += m['change']

        # 转换为输出格式，过滤最小样本量
        result_groups = []
        for group_name, stats in groups.items():
            if stats['total'] >= min_count:
                result_groups.append({
                    'group': group_name,
                    'stats': {
                        'total': stats['total'],
                        'home_win': stats['home_win'],
                        'draw': stats['draw'],
                        'away_win': stats['away_win'],
                        'home_win_rate': (stats['home_win'] / stats['total']) * 100,
                        'handi_win_rate': (stats['handi_win'] / stats['total']) * 100,
                        'avg_change': stats['sum_change'] / stats['total']
                    }
                })

        # 总体统计
        overall = {
            'total_matches': overall_total,
            'home_win': overall_home_win,
            'draw': overall_draw,
            'away_win': overall_away_win,
            'home_win_rate': (overall_home_win / overall_total) * 100 if overall_total > 0 else 0,
            'avg_change': overall_sum_change / overall_total if overall_total > 0 else 0
        }

        return jsonify({
            'success': True,
            'overall': overall,
            'groups': result_groups
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/stats/detail')
def get_stats_detail():
    """API - 获取符合筛选条件的具体比赛详情列表"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500

        # 获取查询参数
        filter_type = request.args.get('filter_type', 'hi_home_up')
        min_change = float(request.args.get('min_change', 0.05))
        league_filter = request.args.get('league', '')
        time_range = request.args.get('time_range', '')
        group_by = request.args.get('group_by', 'league')
        group_value = request.args.get('group_value', '')

        # 解析筛选类型
        parts = filter_type.split('_')
        odds_type = parts[0]  # hi, euro, asian
        target = parts[1]  # home, away, win
        direction = parts[2]  # up=升水, down=降水

        # 构建查询条件 - 只统计完场比赛
        match_query = {'status': 2}

        if league_filter:
            match_query['league'] = league_filter

        # 时间范围筛选
        if time_range:
            from datetime import datetime, timedelta
            days = int(time_range)
            cutoff = datetime.now() - timedelta(days=days)
            match_query['owner_date'] = {'$gte': cutoff.strftime('%Y-%m-%d')}

        # 获取所有比赛
        matches = list(mongo_storage.matches_collection.find(match_query))

        # 根据条件筛选，保存详细信息
        result = []
        for m in matches:
            # 获取初始和当前的水位
            initial = None
            current = None

            if odds_type == 'hi':
                if target == 'home':
                    initial = m.get('hi_initial_home_odds')
                    current = m.get('hi_current_home_odds')
                elif target == 'away':
                    initial = m.get('hi_initial_away_odds')
                    current = m.get('hi_current_away_odds')
            elif odds_type == 'euro':
                if target == 'win':
                    initial = m.get('euro_initial_win')
                    current = m.get('euro_current_win')
            elif odds_type == 'asian':
                if target == 'home':
                    initial = m.get('asian_initial_home_odds')
                    current = m.get('asian_current_home_odds')

            if initial is None or current is None:
                continue

            try:
                initial = float(initial)
                current = float(current)
            except (ValueError, TypeError):
                continue

            change = current - initial

            # 根据方向筛选
            if direction == 'up':
                if change < min_change:
                    continue
            else:
                if change > -min_change:
                    continue

            # 获取比赛结果
            home_score = m.get('home_score')
            away_score = m.get('away_score')

            if home_score is None or away_score is None:
                continue

            try:
                home_score = int(home_score) if str(home_score).isdigit() else None
                away_score = int(away_score) if str(away_score).isdigit() else None
            except:
                continue

            if home_score is None or away_score is None:
                continue

            # 获取盘口
            handicap = m.get('hi_handicap_value')
            if handicap is None:
                handicap = m.get('asian_current_handicap')

            # 按分组筛选
            matched_group = True
            if group_by == 'league':
                if m.get('league') != group_value:
                    matched_group = False
            elif group_by == 'handicap':
                # 按盘口分组筛选
                h = None
                if handicap is not None:
                    try:
                        h = float(handicap)
                    except:
                        pass
                if h is None:
                    matched_group = False
                else:
                    # 和统计时相同的分组逻辑
                    if h <= -1.5 and group_value != '让一球/球半以上':
                        matched_group = False
                    elif h <= -0.5 and group_value not in ['平手/半球 ~ 让一球', '让一球/球半以上']:
                        matched_group = False
                    elif h < 0 and group_value not in ['平手以下', '平手/半球 ~ 让一球']:
                        matched_group = False
                    elif h <= 0.5 and group_value not in ['受让平手/半球', '平手以下']:
                        matched_group = False
                    elif group_value != '受让一球以上':
                        matched_group = False
            elif group_by == 'change_range':
                # 按变化幅度分组筛选
                abs_change = abs(change)
                group_key = None
                if abs_change < 0.1:
                    group_key = '0.00-0.10'
                elif abs_change < 0.2:
                    group_key = '0.10-0.20'
                elif abs_change < 0.3:
                    group_key = '0.20-0.30'
                elif abs_change < 0.5:
                    group_key = '0.30-0.50'
                else:
                    group_key = '0.50以上'
                if group_key != group_value:
                    matched_group = False

            if not matched_group:
                continue

            # 添加到结果，返回所有赔率信息
            result.append({
                'match_id': m.get('match_id', ''),
                'league': m.get('league', ''),
                'match_time': m.get('match_time', ''),
                'home_team': m.get('home_team', ''),
                'away_team': m.get('away_team', ''),
                'home_score': home_score,
                'away_score': away_score,
                'initial': initial,
                'current': current,
                'change': change,
                'handicap': handicap,
                # 完整的所有赔率
                'hi_initial_home': m.get('hi_initial_home_odds') if m.get('hi_initial_home_odds') is not None else None,
                'hi_initial_draw': m.get('hi_initial_draw_odds') if m.get('hi_initial_draw_odds') is not None else None,
                'hi_initial_away': m.get('hi_initial_away_odds') if m.get('hi_initial_away_odds') is not None else None,
                'hi_current_home': m.get('hi_current_home_odds') if m.get('hi_current_home_odds') is not None else None,
                'hi_current_draw': m.get('hi_current_draw_odds') if m.get('hi_current_draw_odds') is not None else None,
                'hi_current_away': m.get('hi_current_away_odds') if m.get('hi_current_away_odds') is not None else None,
                'euro_initial_win': m.get('euro_initial_win') if m.get('euro_initial_win') is not None else None,
                'euro_initial_draw': m.get('euro_initial_draw') if m.get('euro_initial_draw') is not None else None,
                'euro_initial_lose': m.get('euro_initial_lose') if m.get('euro_initial_lose') is not None else None,
                'euro_current_win': m.get('euro_current_win') if m.get('euro_current_win') is not None else None,
                'euro_current_draw': m.get('euro_current_draw') if m.get('euro_current_draw') is not None else None,
                'euro_current_lose': m.get('euro_current_lose') if m.get('euro_current_lose') is not None else None,
                'asian_initial_home': m.get('asian_initial_home_odds') if m.get('asian_initial_home_odds') is not None else None,
                'asian_initial_away': m.get('asian_initial_away_odds') if m.get('asian_initial_away_odds') is not None else None,
                'asian_current_home': m.get('asian_current_home_odds') if m.get('asian_current_home_odds') is not None else None,
                'asian_current_away': m.get('asian_current_away_odds') if m.get('asian_current_away_odds') is not None else None,
                'asian_handicap': m.get('asian_current_handicap') if m.get('asian_current_handicap') is not None else None
            })

        # 按时间倒序
        result.sort(key=lambda x: x['match_time'], reverse=True)

        return jsonify({
            'success': True,
            'matches': result
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/matches')
def get_matches():
    """API - 获取比赛列表 (实时接口)"""
    # 获取日期参数
    date_str = request.args.get('date')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    if not date_str and not start_date:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    # 从数据库读取数据
    filters = {}
    if start_date and end_date:
        filters['owner_date'] = {'$gte': start_date, '$lte': end_date}
    elif date_str:
        # 使用 owner_date 进行筛选，而不是 match_time
        # match_time 可能会跨天（例如凌晨比赛），导致 regex 匹配遗漏
        filters['owner_date'] = date_str
    
    if mongo_storage:
        matches = mongo_storage.get_matches(filters)
    else:
        matches = []
    
    # 支持按状态筛选
    status = request.args.get('status')
    status_code = None
    if status:
        try:
            status_code = int(status)
            # 如果请求未开始(0)，同时也返回改期(6)
            if status_code == 0:
                matches = [m for m in matches if m.get('status') in [0, 6]]
            else:
                matches = [m for m in matches if m.get('status') == status_code]
        except ValueError:
            pass
    elif (not start_date) and (not date_str or date_str == datetime.now().strftime('%Y-%m-%d')):
        # 默认仅展示未开始比赛（仅在未指定日期或指定为今天时生效）
        # 如果用户明确查看历史日期，则不默认过滤状态
        # 修改：同时展示未开始(0)和改期(6)的比赛
        matches = [m for m in matches if m.get('status') in [0, 6]]

    # 联赛筛选弹层需要完整候选列表，因此先按日期和状态统计，再筛选联赛。
    available_leagues = sorted({
        str(match.get('league')).strip()
        for match in matches
        if match.get('league')
    })
    available_total = len(matches)
    league_value = str(request.args.get('league') or '').strip()
    selected_leagues = {
        item.strip() for item in league_value.split(',') if item.strip()
    }
    if selected_leagues:
        matches = [
            match for match in matches
            if str(match.get('league') or '').strip() in selected_leagues
        ]
    
    # 分页前使用完整比赛时间排序。赛果倒序，未开赛列表升序。
    if status_code == 2:
        matches = sort_matches_by_datetime(matches, descending=True)
    elif not status or status_code == 0:
        matches = sort_matches_by_datetime(matches)
    
    # 分页参数
    page = request.args.get('page', '1')
    page_size = request.args.get('page_size', '50')
    try:
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))  # 最大200条
    except ValueError:
        page = 1
        page_size = 50
    
    # 计算分页
    total = len(matches)
    start = (page - 1) * page_size
    end = start + page_size
    paginated_matches = matches[start:end]
    
    return jsonify({
        'success': True,
        'data': paginated_matches,
        'count': len(paginated_matches),
        'total': total,
        'available_total': available_total,
        'leagues': available_leagues,
        'page': page,
        'page_size': page_size,
        'total_pages': (total + page_size - 1) // page_size
    })


@app.route('/api/match/<match_id>')
def get_match_detail(match_id):
    """API - 获取比赛详情（仅MongoDB）"""
    match = mongo_storage.get_match_by_id(match_id) if mongo_storage else None
    
    if not match:
        return jsonify({
            'success': False,
            'message': '比赛不存在'
        }), 404

    # 兼容清理历史记录：500.com 盘口单元格会把“升/降”走势附在文本末尾。
    for field in ('asian_initial_handicap', 'asian_current_handicap'):
        if match.get(field) is not None:
            match[field] = re.sub(r'(?:[↑↓]|升|降)+$', '', str(match[field]).strip())
    
    return jsonify({
        'success': True,
        'data': match
    })


@app.route('/api/match/<match_id>/sporttery-preview')
def get_sporttery_preview(match_id):
    """代理中国体彩网的赛前数据，避免前端跨域并统一上游异常处理。"""
    match = mongo_storage.get_match_by_id(match_id) if mongo_storage else None
    if not match:
        return jsonify({'success': False, 'message': '比赛不存在'}), 404

    known_match_ids = {
        # 500.com 比赛 ID -> 中国竞彩 sportteryMatchId
        '1362704': '2040532',
    }
    sporttery_match_id = (
        request.args.get('sporttery_mid')
        or match.get('sporttery_match_id')
        or known_match_ids.get(str(match_id))
    )
    if not sporttery_match_id:
        return jsonify({
            'success': False,
            'message': '当前比赛尚未关联竞彩比赛 ID'
        }), 422

    base_url = 'https://webapi.sporttery.cn/gateway/uniform/football'
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                      'AppleWebKit/605.1.15 Mobile/15E148',
        'Referer': 'https://m.sporttery.cn/',
        'Accept': 'application/json, text/plain, */*',
    }
    endpoints = {
        'history': ('getResultHistoryV1.qry', {
            'termLimits': 10, 'tournamentFlag': 0, 'homeAwayFlag': 0
        }),
        'recent': ('getMatchResultV1.qry', {
            'termLimits': 10, 'tournamentFlag': 0, 'homeAwayFlag': 0
        }),
        'feature': ('getMatchFeatureV1.qry', {'termLimits': 10}),
        'table': ('getMatchTablesV1.qry', {}),
        'future': ('getFutureMatchesV1.qry', {'termLimits': 4}),
        'injury': ('getInjurySuspensionV1.qry', {}),
    }
    result = {}
    unavailable = []
    for key, (endpoint, params) in endpoints.items():
        params['sportteryMatchId'] = sporttery_match_id
        try:
            response = requests.get(
                f'{base_url}/{endpoint}',
                params=params,
                headers=headers,
                timeout=8,
            )
            if response.status_code != 200 or 'application/json' not in response.headers.get('Content-Type', ''):
                unavailable.append(key)
                continue
            payload = response.json()
            if payload.get('errorCode') == '0':
                result[key] = payload.get('value') or {}
            else:
                unavailable.append(key)
        except (requests.RequestException, ValueError):
            unavailable.append(key)

    if not result:
        return jsonify({
            'success': False,
            'message': '竞彩数据源暂时不可用',
            'sporttery_match_id': sporttery_match_id,
        }), 502

    return jsonify({
        'success': True,
        'data': result,
        'sporttery_match_id': sporttery_match_id,
        'unavailable': unavailable,
        'source': '中国体彩网',
    })


@app.route('/api/match/<match_id>/500-analysis')
def get_500_match_analysis(match_id):
    """500 官方数据分析页的结构化代理接口。"""
    if not re.fullmatch(r'\d+', str(match_id)):
        return jsonify({'success': False, 'message': '比赛 ID 格式错误'}), 400
    match = mongo_storage.get_match_by_id(match_id) if mongo_storage else {
        'match_id': str(match_id)
    }
    try:
        data = _get_500_fundamentals(
            match,
            refresh=request.args.get('refresh') in ('1', 'true'),
        )
        return jsonify({'success': True, 'data': data})
    except requests.RequestException as exc:
        app.logger.warning('500 analysis request failed for %s: %s', match_id, exc)
        return jsonify({'success': False, 'message': '500 数据源请求失败'}), 502
    except (ValueError, AttributeError, IndexError) as exc:
        app.logger.warning('500 analysis parse failed for %s: %s', match_id, exc)
        return jsonify({'success': False, 'message': '500 数据解析失败'}), 502


def _cache_datetime(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(
            tzinfo=None
        )
    except (TypeError, ValueError):
        return None


def _cached_fundamentals_fresh(data):
    if not isinstance(data, dict) or not data:
        return False
    cached_at = _cache_datetime(data.get('cached_at'))
    if not cached_at:
        return False
    ttl_hours = max(
        1, int(os.getenv('FAE_FUNDAMENTALS_CACHE_HOURS', '4'))
    )
    return datetime.now() - cached_at <= timedelta(hours=ttl_hours)


def _get_500_fundamentals(match, refresh=False, source_crawler=None):
    """读取缓存或抓取一场 500 基本面，并回写比赛文档。"""
    match = match or {}
    match_id = str(match.get('match_id') or '')
    cached = (
        mongo_storage.get_match_fundamentals(match_id)
        if mongo_storage else {}
    )
    if cached and not refresh and _cached_fundamentals_fresh(cached):
        return cached
    try:
        data = (source_crawler or crawler).crawl_match_analysis(match_id)
    except (
        requests.RequestException, ValueError, AttributeError, IndexError
    ):
        if cached:
            stale = dict(cached)
            stale['cache_status'] = 'stale'
            return stale
        raise
    if mongo_storage:
        mongo_storage.save_match_fundamentals(match_id, data)
    return data


def _load_daily_fundamentals(matches):
    """并发加载当天基本面；缓存命中时不访问 500。"""
    results = {}
    pending = []
    cached_by_id = (
        mongo_storage.get_match_fundamentals_bulk([
            match.get('match_id') for match in matches
        ])
        if mongo_storage else {}
    )
    for match in matches:
        match_id = str(match.get('match_id') or '')
        cached = cached_by_id.get(match_id) or {}
        if _cached_fundamentals_fresh(cached):
            results[match_id] = cached
        else:
            pending.append(match)
    if not pending:
        return results

    workers = max(
        1, min(3, int(os.getenv('FAE_FUNDAMENTALS_WORKERS', '2')))
    )

    def fetch(match):
        match_id = str(match.get('match_id') or '')
        return match_id, FootballCrawler().crawl_match_analysis(match_id)

    with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as executor:
        futures = {executor.submit(fetch, match): match for match in pending}
        for future in as_completed(futures):
            match = futures[future]
            match_id = str(match.get('match_id') or '')
            try:
                _, data = future.result()
                results[match_id] = data
                if mongo_storage:
                    mongo_storage.save_match_fundamentals(match_id, data)
            except (
                requests.RequestException, ValueError, AttributeError, IndexError
            ) as exc:
                app.logger.warning(
                    '500 fundamentals unavailable for %s: %s', match_id, exc
                )
                stale = cached_by_id.get(match_id) or {}
                if stale:
                    stale = dict(stale)
                    stale['cache_status'] = 'stale'
                results[match_id] = stale
    return results


def _generate_fae_for_match(match, use_ai=True):
    """组装数据并运行 FAE；核心计算不依赖大模型。"""
    match_id = str(match.get('match_id') or '')
    source_analysis = {}
    try:
        source_analysis = _get_500_fundamentals(match)
    except (requests.RequestException, ValueError, AttributeError, IndexError) as exc:
        app.logger.warning('FAE source unavailable for %s: %s', match_id, exc)
    predictions = mongo_storage.get_predictions(
        filters={'match_id': match_id}, limit=1
    ) if mongo_storage else []
    context = ai_analysis_service.build_context(
        match=match,
        source_analysis=source_analysis,
        prediction=predictions[0] if predictions else None,
    )
    return ai_analysis_service.generate_from_context(
        context,
        rule_weights=mongo_storage.get_fae_rule_weights() if mongo_storage else None,
        use_ai=use_ai,
        active_skills=mongo_storage.get_active_fae_skills() if mongo_storage else None,
    )


def _build_daily_ai_inputs(
    matches,
    league_profiles=None,
    goal_margin_models=None,
    draw_selection_policy=None,
    supervised_model=None,
):
    """Build current deterministic FAE snapshots without making per-match LLM calls."""
    rule_weights = (
        mongo_storage.get_fae_rule_weights() if mongo_storage else None
    )
    active_skills = (
        mongo_storage.get_active_fae_skills() if mongo_storage else None
    )
    source_analyses = _load_daily_fundamentals(matches)
    rows = []
    supervised_predictor = None
    if supervised_model:
        try:
            supervised_predictor = FAESupervisedPredictor(supervised_model)
        except (TypeError, ValueError):
            app.logger.exception('FAE监督模型加载失败，继续使用现有研判')
    for match in sorted(
        matches,
        key=lambda item: (
            str(item.get('match_time') or ''),
            str(item.get('match_number') or ''),
        ),
    ):
        source_analysis = source_analyses.get(
            str(match.get('match_id') or '')
        ) or {}
        context = ai_analysis_service.build_context(
            match=match,
            source_analysis=source_analysis,
        )
        core_result = ai_analysis_service.generate_from_context(
            context,
            rule_weights=rule_weights,
            use_ai=False,
            active_skills=active_skills,
        )
        row = build_daily_match_input(
            match,
            core_result,
            league_profile=(league_profiles or {}).get(
                str(match.get('league') or '').strip()
            ),
            goal_margin_model=(goal_margin_models or {}).get(
                str(match.get('match_id') or '')
            ),
            source_analysis=source_analysis,
            draw_selection_policy=draw_selection_policy,
        )
        if supervised_predictor:
            try:
                row['supervised_shadow'] = supervised_predictor.predict(
                    row,
                    owner_date=str(match.get('owner_date') or '')[:10],
                    daily_match_count=len(matches),
                )
            except (TypeError, ValueError, ArithmeticError):
                app.logger.exception(
                    'FAE监督模型预测失败 %s', match.get('match_id')
                )
        rows.append(row)
    return rows


def _persist_fae_daily_ai_run(result, failure_message):
    """Persist one run and synchronously rebuild its deterministic review.

    The review rebuild never spends another Ark call.  It only rebinds the
    main/secondary selections and their settlement rows to this exact run_id;
    the regular review scheduler remains responsible for AI deep diagnosis.
    """
    if not mongo_storage.save_fae_daily_ai_run(result):
        raise FAEError(failure_message)
    try:
        _review_fae_daily_ai(
            str(result.get('owner_date') or '')[:10],
            run_ai=False,
            snapshot=result,
        )
    except Exception:
        app.logger.exception(
            'FAE deterministic review sync failed for run %s',
            result.get('run_id'),
        )


def _run_fae_daily_ai(
    owner_date,
    force=False,
    draw_selection_policy=None,
    target_match_ids=None,
):
    """Send current market snapshots to Ark and persist per-match results.

    ``target_match_ids`` powers the T-minus-30 scheduler.  Only those fixtures
    incur a model call; previously analysed fixtures are retained in the new
    daily run so the recommendation page remains complete.
    """
    if not mongo_storage:
        raise FAEError('MongoDB不可用')
    date_str = str(owner_date or '')[:10]
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_str):
        raise FAEError('日期格式应为 YYYY-MM-DD')
    all_matches = mongo_storage.get_matches(
        filters={'owner_date': date_str},
        sort_by='match_time',
        sort_order=1,
    )
    target_ids = {
        str(value) for value in (target_match_ids or []) if value is not None
    }
    matches = [
        match for match in all_matches
        if (
            match.get('status') in (0, '0')
            and (
                not target_ids
                or str(match.get('match_id') or '') in target_ids
            )
        )
    ]
    selected_ids = {
        str(match.get('match_id') or '') for match in matches
    }
    retained_matches = []
    for match in all_matches:
        match_id = str(match.get('match_id') or '')
        if match_id in selected_ids:
            continue
        if not target_ids and match.get('status') in (0, '0'):
            continue
        snapshot = mongo_storage.get_fae_daily_ai_match(
            match_id, date_str
        )
        if not snapshot:
            continue
        snapshot = dict(snapshot)
        snapshot['current_status'] = match.get('status')
        snapshot['retained_from_run_id'] = snapshot.get('run_id')
        retained_matches.append(snapshot)

    previous_run = mongo_storage.get_fae_daily_ai_run(date_str)
    if not matches:
        if not retained_matches or not previous_run:
            raise FAEError('当天没有未开赛比赛，且没有可保留的赛前研判')
        result = dict(previous_run)
        result['matches'] = []
        result = fae_daily_ai_analyzer.merge_retained_matches(
            result, retained_matches
        )
        if target_ids:
            result = fae_daily_ai_analyzer.rebuild_incremental_summary(
                result,
                [previous_run.get('daily_summary') or {}],
            )
        result['cache_hit'] = True
        _persist_fae_daily_ai_run(
            result, '保留已开赛比赛的赛前研判失败'
        )
        return result
    league_profiles = mongo_storage.get_fae_league_profiles(
        date_str,
        [match.get('league') for match in matches],
    )
    goal_margin_models = mongo_storage.get_fae_match_goal_margin_models(
        date_str,
        matches,
    )
    supervised_model = (
        mongo_storage.get_latest_fae_supervised_model(include_shadow=True)
        if _env_enabled('FAE_SUPERVISED_SHADOW_ENABLED', True)
        else None
    )
    match_inputs = _build_daily_ai_inputs(
        matches,
        league_profiles=league_profiles,
        goal_margin_models=goal_margin_models,
        draw_selection_policy=draw_selection_policy,
        supervised_model=supervised_model,
    )
    review_memory = mongo_storage.get_fae_review_memory(date_str)
    input_hash = fae_daily_ai_analyzer.input_hash(
        date_str,
        match_inputs,
        review_memory=review_memory,
        draw_selection_policy=draw_selection_policy,
    )
    cached = mongo_storage.get_fae_daily_ai_run(
        date_str, input_hash=input_hash
    )
    if cached and not force:
        cached = fae_daily_ai_analyzer.merge_retained_matches(
            cached, retained_matches
        )
        if target_ids:
            cached = fae_daily_ai_analyzer.rebuild_incremental_summary(
                cached,
                [(previous_run or {}).get('daily_summary') or {}],
            )
        _persist_fae_daily_ai_run(
            cached, '合并已开赛比赛的赛前研判失败'
        )
        cached['cache_hit'] = True
        return cached
    batch_size = max(
        1,
        min(
            DAILY_AI_MAX_BATCH_SIZE,
            int(os.getenv(
                'FAE_DAILY_AI_BATCH_SIZE',
                str(DAILY_AI_MAX_BATCH_SIZE),
            )),
        ),
    )
    with fae_daily_ai_lock:
        if not force:
            cached = mongo_storage.get_fae_daily_ai_run(
                date_str, input_hash=input_hash
            )
            if cached:
                cached = fae_daily_ai_analyzer.merge_retained_matches(
                    cached, retained_matches
                )
                if target_ids:
                    cached = fae_daily_ai_analyzer.rebuild_incremental_summary(
                        cached,
                        [(previous_run or {}).get('daily_summary') or {}],
                    )
                _persist_fae_daily_ai_run(
                    cached, '合并已开赛比赛的赛前研判失败'
                )
                cached['cache_hit'] = True
                return cached
        result = fae_daily_ai_analyzer.analyze(
            date_str,
            match_inputs,
            batch_size=batch_size,
            batch_cache_get=mongo_storage.get_fae_daily_ai_batch,
            batch_cache_save=mongo_storage.save_fae_daily_ai_batch,
            review_memory=review_memory,
            draw_selection_policy=draw_selection_policy,
        )
        result = fae_daily_ai_analyzer.merge_retained_matches(
            result, retained_matches
        )
        if target_ids:
            result = fae_daily_ai_analyzer.rebuild_incremental_summary(
                result,
                [(previous_run or {}).get('daily_summary') or {}],
            )
        _persist_fae_daily_ai_run(
            result, '全日研判已生成，但写入MongoDB失败'
        )
        result['cache_hit'] = False
        return result


def _review_finished_fae_matches(matches=None):
    """复盘所有已完场且尚未复盘的 FAE 分析。"""
    if not mongo_storage:
        return {'reviewed': 0, 'adjustments': 0, 'items': []}
    candidates = matches or mongo_storage.get_matches(filters={'status': 2})
    reviewed = []
    adjustment_count = 0
    for match in candidates:
        if match.get('status') != 2:
            continue
        analysis = mongo_storage.get_ai_analysis(match.get('match_id'))
        if not analysis or not analysis.get('engine'):
            continue
        review = fae_review_engine.review(analysis, match)
        if not review:
            continue
        saved = mongo_storage.save_fae_review(review)
        if saved.get('new'):
            adjustment_count += len(saved.get('adjustments') or [])
            reviewed.append(saved.get('review'))
    return {
        'reviewed': len(reviewed),
        'adjustments': adjustment_count,
        'items': reviewed,
    }


def _snapshot_fae_draw_plan(owner_date):
    """保存用户实际看到的当日平/让平方案，内容相同则自动去重。"""
    if not mongo_storage:
        return None, None
    plan = mongo_storage.get_fae_draw_parlays(owner_date)
    snapshot = mongo_storage.save_fae_draw_snapshot(plan)
    return plan, snapshot


def _review_fae_draw_plan(owner_date):
    """按当天最后一份全赛前快照进行专项复盘。"""
    if not mongo_storage:
        return None
    snapshot = mongo_storage.get_fae_draw_snapshot(owner_date)
    if not snapshot:
        return None
    matches = {
        str(pick.get('match_id')): (
            mongo_storage.get_match_by_id(pick.get('match_id')) or {}
        )
        for pick in snapshot.get('match_recommendations') or []
        if pick.get('match_id')
    }
    review = fae_draw_review_engine.review(snapshot, matches)
    saved = mongo_storage.save_fae_draw_review(review)
    return saved.get('review') if saved.get('saved') else None


def _review_fae_daily_ai(
    owner_date,
    force_ai=False,
    run_ai=True,
    snapshot=None,
):
    """Settle the immutable Ark run and cache an AI post-match diagnosis."""
    if not mongo_storage:
        return None
    # Settlement must follow the final analysis run for the match day.  Do not
    # fall back to an older all-pregame run, otherwise the review page and the
    # recommendation page can point at different run_ids.
    snapshot = snapshot or mongo_storage.get_fae_daily_ai_snapshot(
        owner_date, latest=True
    )
    if not snapshot:
        return None
    matches = {
        str(item.get('match_id')): (
            mongo_storage.get_match_by_id(item.get('match_id')) or {}
        )
        for item in snapshot.get('matches') or []
        if item.get('match_id')
    }
    review = fae_daily_ai_review_engine.review(snapshot, matches)
    ai_review_enabled = os.getenv(
        'FAE_AI_REVIEW_ENABLED', 'true'
    ).lower() in ('1', 'true', 'yes', 'on')
    reviewable_count = sum(
        1 for row in review.get('match_results') or []
        if row.get('status') in ('hit', 'miss', 'push')
        or (
            row.get('status') == 'skipped'
            and row.get('result_score')
        )
    )
    existing = mongo_storage.get_fae_daily_ai_review(
        owner_date, run_id=snapshot.get('run_id')
    ) or {}
    desired_hash = (
        fae_ai_review_analyzer.input_hash(snapshot, review)
        if reviewable_count else None
    )
    cached = existing.get('ai_deep_review') or {}
    if (
        not force_ai
        and desired_hash
        and cached.get('input_hash') == desired_hash
    ):
        review['ai_deep_review'] = cached
        review['ai_deep_review_cache_hit'] = True
    if (
        run_ai
        and ai_review_enabled
        and fae_ai_review_analyzer.configured
        and reviewable_count
        and not review.get('ai_deep_review')
    ):
        try:
            # A manual request and the 15-minute scheduler can overlap.
            # Re-check inside the lock before spending another Ark call.
            with fae_ai_review_lock:
                latest = mongo_storage.get_fae_daily_ai_review(
                    owner_date, run_id=snapshot.get('run_id')
                ) or {}
                latest_ai = latest.get('ai_deep_review') or {}
                if (
                    not force_ai
                    and latest_ai.get('input_hash') == desired_hash
                ):
                    review['ai_deep_review'] = latest_ai
                    review['ai_deep_review_cache_hit'] = True
                else:
                    review['ai_deep_review'] = (
                        fae_ai_review_analyzer.analyze(snapshot, review)
                    )
                    review['ai_deep_review_cache_hit'] = False
        except FAEError as exc:
            # Deterministic settlement must remain available even when Ark
            # is temporarily unavailable. Preserve this run's last diagnosis.
            if cached:
                review['ai_deep_review'] = cached
                review['ai_deep_review_stale'] = True
            review['ai_deep_review_error'] = str(exc)
            app.logger.warning(
                'FAE AI deep review failed for %s: %s',
                owner_date,
                exc,
            )
        except Exception as exc:
            if cached:
                review['ai_deep_review'] = cached
                review['ai_deep_review_stale'] = True
            review['ai_deep_review_error'] = f'AI 深度复盘失败: {exc}'
            app.logger.exception(
                'Unexpected FAE AI deep review error for %s',
                owner_date,
            )
    elif run_ai and reviewable_count:
        review['ai_deep_review_unavailable'] = (
            'AI 深度复盘未启用'
            if not ai_review_enabled
            else '火山方舟尚未配置'
        )
    elif reviewable_count and not run_ai:
        review['ai_deep_review_pending'] = True
    if review.get('ai_deep_review'):
        review['ai_deep_review'] = (
            fae_ai_review_analyzer.humanize_review_match_ids(
                review.get('ai_deep_review') or {},
                (review.get('ai_deep_review') or {}).get('matches')
                or review.get('match_results')
                or [],
            )
        )
    saved = mongo_storage.save_fae_daily_ai_review(review)
    return saved.get('review') if saved.get('saved') else None


def _run_fae_shadow_backtest(requested_days=28, save=True):
    """Replay old/new guards on immutable prematch snapshots."""
    if not mongo_storage:
        return None
    days = max(7, min(60, int(requested_days or 28)))
    snapshot_days = []
    for owner_date in mongo_storage.get_fae_daily_ai_snapshot_dates(days):
        snapshot = mongo_storage.get_fae_daily_ai_snapshot(owner_date)
        if not snapshot:
            continue
        match_ids = [
            str(item.get('match_id') or '')
            for item in snapshot.get('matches') or []
            if item.get('match_id')
        ]
        snapshot_days.append({
            'snapshot': snapshot,
            'results': mongo_storage.get_matches_by_ids(match_ids),
        })
    report = fae_shadow_backtest_engine.build(
        snapshot_days,
        requested_days=days,
        validation_start_date=os.getenv(
            'FAE_SHADOW_VALIDATION_START_DATE', '2026-08-24'
        ),
    )
    if save:
        mongo_storage.save_fae_backtest_report(report)
    return report


def _run_fae_supervised_training(requested_days=120, save=True):
    """Train and walk-forward-test on immutable pre-match snapshots."""
    if not mongo_storage:
        return None
    days = max(30, min(365, int(requested_days or 120)))
    snapshot_days = []
    for owner_date in mongo_storage.get_fae_daily_ai_snapshot_dates(days):
        snapshot = mongo_storage.get_fae_daily_ai_snapshot(owner_date)
        if not snapshot:
            continue
        # Older immutable runs predate the formal-single field. Reconstruct
        # that decision exclusively from their frozen pre-match inputs before
        # building labels, so the candidate meta-model can use the full
        # history without copying any final-score information into features.
        replay_matches = fae_daily_ai_analyzer.calibrate_daily_matches(
            snapshot.get('matches') or []
        )
        replay_matches = fae_daily_ai_analyzer.apply_draw_radar(
            replay_matches
        )
        replay_matches = (
            fae_daily_ai_analyzer
            .apply_draw_radar_recommendation_overrides(replay_matches)
        )
        replay_matches = (
            fae_daily_ai_analyzer.apply_two_option_recommendations(
                replay_matches
            )
        )
        replay_matches = (
            fae_daily_ai_analyzer.apply_official_bet_recommendations(
                replay_matches
            )
        )
        snapshot = {**snapshot, 'matches': replay_matches}
        match_ids = [
            str(item.get('match_id') or '')
            for item in snapshot.get('matches') or []
            if item.get('match_id')
        ]
        snapshot_days.append({
            'snapshot': snapshot,
            'results': mongo_storage.get_matches_by_ids(match_ids),
        })
    training_days = build_training_days(snapshot_days)
    result = fae_supervised_backtest_engine.build(training_days)
    model = result.get('model') or {}
    report = result.get('report') or {}
    # Ordinary-draw and handicap-draw rankings remain shadow-only until their
    # own release guard is promoted.  The formal-single policy has a separate
    # three-block temporal profit gate and may become active independently.
    model['status'] = 'shadow'
    model['release_eligible'] = bool(
        (report.get('release_guard') or {}).get('can_promote')
    )
    model['profit_single_release_eligible'] = bool(
        ((report.get('profit_single') or {}).get('policy') or {}).get(
            'active'
        )
    )
    model['profit_parlay_release_eligible'] = bool(
        ((report.get('profit_parlay') or {}).get('policy') or {}).get(
            'active'
        )
    )
    report['requested_days'] = days
    report['available_snapshot_days'] = len(snapshot_days)
    report['usable_training_days'] = len(training_days)
    report['model_status'] = 'shadow'
    report['profit_single_status'] = (
        ((report.get('profit_single') or {}).get('policy') or {}).get(
            'status'
        ) or 'shadow_only'
    )
    report['profit_parlay_status'] = (
        ((report.get('profit_parlay') or {}).get('policy') or {}).get(
            'status'
        ) or 'shadow_only'
    )
    if save:
        mongo_storage.save_fae_supervised_model(model)
        mongo_storage.save_fae_supervised_backtest(report)
    return {'model': model, 'report': report}


@app.route('/api/match/<match_id>/fae-analysis', methods=['GET'])
@app.route('/api/match/<match_id>/ai-analysis', methods=['GET'])
def get_match_ai_analysis(match_id):
    """读取缓存的 FAE 分析，不触发模型调用。"""
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    match = mongo_storage.get_match_by_id(match_id)
    if not match:
        return jsonify({'success': False, 'message': '比赛不存在'}), 404
    cached = mongo_storage.get_ai_analysis(match_id)
    return jsonify({
        'success': True,
        'data': cached,
        'configured': True,
        'narrative_configured': ai_analysis_service.configured,
        'engine': 'FAE',
        'engine_version': ENGINE_VERSION,
        'legacy': bool(cached and not cached.get('engine')),
        'model': ai_analysis_service.client.model if ai_analysis_service.configured else None,
    })


@app.route('/api/match/<match_id>/fae-analysis', methods=['POST'])
@app.route('/api/match/<match_id>/ai-analysis', methods=['POST'])
@login_required
def generate_match_ai_analysis(match_id):
    """运行 FAE 核心计算，并可选调用方舟生成说明层。"""
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    match = mongo_storage.get_match_by_id(match_id)
    if not match:
        return jsonify({'success': False, 'message': '比赛不存在'}), 404

    payload = request.get_json(silent=True) or {}
    force = bool(payload.get('force'))
    use_ai = payload.get('narrative', True) is not False
    cached = mongo_storage.get_ai_analysis(match_id)
    if cached and cached.get('engine') and not force:
        return jsonify({
            'success': True,
            'data': cached,
            'cache_hit': True,
        })

    if cached and force:
        refresh_seconds = max(60, int(os.getenv('AI_MIN_REFRESH_SECONDS', '300')))
        generated_at = str(cached.get('generated_at') or '')
        try:
            generated_time = datetime.fromisoformat(generated_at.replace('Z', '+00:00'))
            now = datetime.now(generated_time.tzinfo) if generated_time.tzinfo else datetime.now()
            age_seconds = (now - generated_time).total_seconds()
            if age_seconds < refresh_seconds:
                return jsonify({
                    'success': True,
                    'data': cached,
                    'cache_hit': True,
                    'message': f'分析生成未满{refresh_seconds // 60}分钟，已返回缓存',
                })
        except (TypeError, ValueError):
            pass

    try:
        analysis = _generate_fae_for_match(match, use_ai=use_ai)
        if not mongo_storage.save_ai_analysis(analysis):
            return jsonify({'success': False, 'message': 'FAE分析保存失败'}), 500
        return jsonify({
            'success': True,
            'data': analysis,
            'cache_hit': False,
        })
    except FAEError as exc:
        app.logger.warning('FAE analysis failed for %s: %s', match_id, exc)
        return jsonify({'success': False, 'message': str(exc)}), 502
    except Exception as exc:
        app.logger.exception('Unexpected FAE analysis error for %s', match_id)
        return jsonify({'success': False, 'message': f'FAE分析生成失败: {exc}'}), 500


@app.route('/api/match/<match_id>/fae-review', methods=['GET'])
def get_match_fae_review(match_id):
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    return jsonify({
        'success': True,
        'data': mongo_storage.get_fae_review(match_id),
    })


@app.route('/api/fae/rankings', methods=['GET'])
def get_fae_rankings():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    date_str = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
    return jsonify({
        'success': True,
        'data': mongo_storage.get_fae_rankings(date_str),
    })


@app.route('/api/fae/league-profile/<match_id>', methods=['GET'])
def get_fae_league_profile(match_id):
    """Return the leakage-safe pre-match league profile for a result card."""
    if not mongo_storage:
        return jsonify({
            'success': False,
            'message': 'MongoDB不可用',
        }), 503
    match = mongo_storage.get_match_by_id(str(match_id or ''))
    if not match:
        return jsonify({
            'success': False,
            'message': '比赛不存在',
        }), 404
    league = str(match.get('league') or '').strip()
    before_date = str(match.get('owner_date') or '')[:10]
    if not league or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', before_date):
        return jsonify({
            'success': False,
            'message': '比赛缺少联赛或业务日期',
        }), 422
    profile = mongo_storage.get_fae_league_profiles(
        before_date,
        [league],
    ).get(league)
    if not profile:
        return jsonify({
            'success': False,
            'message': '暂无联赛历史画像',
        }), 404
    return jsonify({
        'success': True,
        'data': {
            'match_id': str(match.get('match_id') or ''),
            'match_number': str(match.get('match_number') or ''),
            'league': league,
            'profile': profile,
        },
    })


@app.route('/api/fae/league-profiles', methods=['GET'])
def get_fae_league_profiles():
    """Return league profiles in one request for the standalone results tab."""
    if not mongo_storage:
        return jsonify({
            'success': False,
            'message': 'MongoDB不可用',
        }), 503

    before_date = str(
        request.args.get('before_date')
        or datetime.now().strftime('%Y-%m-%d')
    )[:10]
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', before_date):
        return jsonify({
            'success': False,
            'message': '统计日期格式错误',
        }), 422

    requested = sorted({
        item.strip()
        for item in str(request.args.get('leagues') or '').split(',')
        if item.strip()
    })
    if requested:
        leagues = requested[:80]
    else:
        leagues = sorted({
            str(item or '').strip()
            for item in mongo_storage.matches_collection.distinct(
                'league',
                {
                    'status': 2,
                    'owner_date': {'$lt': before_date},
                },
            )
            if str(item or '').strip()
        })[:80]

    profiles = mongo_storage.get_fae_league_profiles(
        before_date,
        leagues,
    )
    items = sorted(
        [
            {
                'league': league,
                'profile': profile,
            }
            for league, profile in profiles.items()
            if profile
        ],
        key=lambda item: (
            not bool(
                item['profile'].get('eligible_for_adjustment')
            ),
            -int(item['profile'].get('sample_size') or 0),
            item['league'],
        ),
    )
    return jsonify({
        'success': True,
        'data': {
            'before_date': before_date,
            'count': len(items),
            'items': items,
        },
    })


@app.route('/api/fae/league-profile-matches', methods=['GET'])
def get_fae_league_profile_matches():
    """Return the finished matches behind a league profile metric."""
    if not mongo_storage:
        return jsonify({
            'success': False,
            'message': 'MongoDB不可用',
        }), 503
    league = str(request.args.get('league') or '').strip()
    before_date = str(
        request.args.get('before_date')
        or datetime.now().strftime('%Y-%m-%d')
    )[:10]
    if not league:
        return jsonify({
            'success': False,
            'message': '缺少联赛名称',
        }), 422
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', before_date):
        return jsonify({
            'success': False,
            'message': '统计日期格式错误',
        }), 422
    kind = str(request.args.get('kind') or 'surprise').strip()
    try:
        page = max(1, int(request.args.get('page') or 1))
        page_size = max(
            1, min(50, int(request.args.get('page_size') or 20))
        )
    except (TypeError, ValueError):
        return jsonify({
            'success': False,
            'message': '分页参数错误',
        }), 422
    return jsonify({
        'success': True,
        'data': mongo_storage.get_fae_league_profile_matches(
            before_date,
            league,
            kind=kind,
            page=page,
            page_size=page_size,
        ),
    })


@app.route('/api/fae/daily-ai', methods=['GET'])
def get_fae_daily_ai():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    date_str = str(
        request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
    )[:10]
    compact = str(request.args.get('compact') or '').lower() in (
        '1', 'true', 'yes', 'on'
    )
    data = mongo_storage.get_fae_daily_ai_run(date_str, compact=compact)
    if data:
        # Keep immutable pre-match recommendations, but attach the latest
        # match status and score at read time so completed selections can be
        # graded without removing them from the recommendation history.
        daily_matches = data.get('matches') or []
        live_matches = mongo_storage.get_matches_by_ids(
            item.get('match_id') for item in daily_matches
        )
        for item in daily_matches:
            live = live_matches.get(str(item.get('match_id') or '')) or {}
            if not live:
                continue
            item['current_status'] = live.get(
                'status', item.get('current_status')
            )
            score = live.get('score')
            if not score:
                home_score = live.get('home_score')
                away_score = live.get('away_score')
                if home_score not in (None, '') and away_score not in (None, ''):
                    score = f'{home_score}:{away_score}'
            if score:
                item['result_score'] = str(score).replace('-', ':')
    if data and not compact:
        data['matches'] = fae_daily_ai_analyzer.calibrate_daily_matches(
            data.get('matches') or []
        )
        data['matches'] = (
            fae_daily_ai_analyzer.normalize_match_memory_governance(
                data.get('matches') or [],
                data.get('review_memory') or {},
            )
        )
        data['matches'] = fae_daily_ai_analyzer.apply_draw_radar(
            data.get('matches') or []
        )
        data['matches'] = (
            fae_daily_ai_analyzer.apply_draw_radar_recommendation_overrides(
                data.get('matches') or []
            )
        )
        data['matches'] = (
            fae_daily_ai_analyzer.apply_two_option_recommendations(
                data.get('matches') or []
            )
        )
        data['matches'] = (
            fae_daily_ai_analyzer.apply_official_bet_recommendations(
                data.get('matches') or []
            )
        )
        data['matches'] = (
            fae_daily_ai_analyzer
            .apply_high_confidence_single_recommendations(
                data.get('matches') or []
            )
        )
        data['daily_summary'] = (
            fae_daily_ai_analyzer.normalize_summary_pool_semantics(
                data.get('daily_summary') or {},
                data.get('matches') or [],
            )
        )
        data['daily_summary'] = fae_daily_ai_analyzer._apply_no_bet_summary(
            data.get('daily_summary') or {},
            data.get('matches') or [],
        )
        data['daily_summary'] = (
            fae_daily_ai_analyzer.attach_draw_radar_summary(
                data.get('daily_summary') or {},
                data.get('matches') or [],
            )
        )
        data['daily_summary'] = (
            fae_daily_ai_analyzer.attach_supervised_shadow_summary(
                data.get('daily_summary') or {},
                data.get('matches') or [],
            )
        )
        data['daily_summary'] = (
            fae_daily_ai_analyzer.attach_league_model_rankings(
                data.get('daily_summary') or {},
                data.get('matches') or [],
            )
        )
        data['daily_summary'] = (
            fae_daily_ai_analyzer.attach_upset_warning_summary(
                data.get('daily_summary') or {},
                data.get('matches') or [],
            )
        )
        data['daily_summary'] = (
            fae_daily_ai_analyzer.attach_odds_band_summary(
                data.get('daily_summary') or {},
                data.get('matches') or [],
            )
        )
        data['daily_summary'] = fae_daily_ai_analyzer.align_summary_ratings(
            data.get('daily_summary') or {},
            data.get('matches') or [],
        )
        data['daily_summary'] = (
            fae_daily_ai_analyzer.promote_draw_radar_recommendations(
                data.get('daily_summary') or {},
                data.get('matches') or [],
            )
        )
        data['daily_summary']['recommended_combinations'] = (
            fae_daily_ai_analyzer._ensure_mixed_combinations(
                data.get('daily_summary') or {}
            )
        )
        data['daily_summary'] = (
            fae_daily_ai_analyzer.normalize_summary_memory_governance(
                data.get('daily_summary') or {},
                data.get('review_memory') or {},
            )
        )
        data['daily_summary'] = (
            fae_daily_ai_analyzer._humanize_summary_match_ids(
                data.get('daily_summary') or {},
                data.get('matches') or [],
            )
        )
    if data and compact:
        data = compact_daily_ai_run(data)
    response = jsonify({
        'success': True,
        'data': data,
        'configured': fae_daily_ai_analyzer.configured,
        'can_manage': bool(_fae_admin_user()),
    })
    if compact:
        response.headers['Cache-Control'] = 'private, max-age=30'
    return response


@app.route('/api/fae/daily-ai', methods=['POST'])
@fae_admin_required
def run_fae_daily_ai():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    payload = request.get_json(silent=True) or {}
    date_str = str(
        payload.get('date') or datetime.now().strftime('%Y-%m-%d')
    )[:10]
    draw_selection_policy = payload.get('draw_selection_policy')
    if draw_selection_policy is None:
        draw_selection_policy = payload.get('draw_policy')
    try:
        data = _run_fae_daily_ai(
            date_str,
            force=bool(payload.get('force')),
            draw_selection_policy=draw_selection_policy,
        )
        wecom_delivery = (
            _push_wecom_daily_ai(data)
            if payload.get('push_wecom') is not False else None
        )
        if data.get('cache_hit'):
            message = '赔率数据未变化，已返回上次全日研判'
        elif data.get('partial_success'):
            message = (
                f"分批研判完成：大模型成功 "
                f"{data.get('ai_analyzed_match_count', 0)} 场，"
                f"暂用FAE核心 {data.get('fallback_match_count', 0)} 场"
            )
        else:
            message = (
                f"已重新研判 "
                f"{data.get('analyzed_match_count', data.get('match_count', 0))} 场，"
                f"保留 {data.get('retained_match_count', 0)} 场已开赛的赛前研判"
            )
        response_data = (
            compact_daily_ai_run(data)
            if payload.get('compact') else data
        )
        return jsonify({
            'success': True,
            'data': response_data,
            'cache_hit': bool(data.get('cache_hit')),
            'wecom_delivery': wecom_delivery,
            'draw_selection_policy': data.get('draw_selection_policy'),
            'message': message,
        })
    except FAEError as exc:
        app.logger.warning('FAE daily AI failed for %s: %s', date_str, exc)
        return jsonify({'success': False, 'message': str(exc)}), 502
    except Exception as exc:
        app.logger.exception('Unexpected FAE daily AI error for %s', date_str)
        return jsonify({
            'success': False,
            'message': f'全日研判运行失败: {exc}',
        }), 500


@app.route('/api/wecom/status', methods=['GET'])
@login_required
def get_wecom_status():
    return jsonify({
        'success': True,
        'data': {
            **_wecom_delivery_status(),
            'can_manage': bool(_fae_admin_user()),
        },
    })


@app.route('/api/wecom/test', methods=['POST'])
@fae_admin_required
def test_wecom_delivery():
    payload = request.get_json(silent=True) or {}
    user = _fae_admin_user() or {}
    custom = str(payload.get('message') or '').strip()
    content = "\n".join([
        "## MyGoal 企业微信通知测试",
        custom[:500] or "机器人连接成功，后续可接收全日研判和赛后复盘。",
        "",
        f"> 操作账号：{user.get('username') or '-'}",
        f"> 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ])
    result = _deliver_wecom_once(
        'manual_test',
        uuid.uuid4().hex,
        content,
    )
    return jsonify({
        'success': bool(result.get('success')),
        'data': result,
        'message': result.get('message'),
    }), 200 if result.get('success') else 502


@app.route('/api/fae/daily-ai/match/<match_id>', methods=['GET'])
def get_fae_daily_ai_match(match_id):
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    date_str = request.args.get('date')
    data = mongo_storage.get_fae_daily_ai_match(match_id, date_str)
    if data:
        rows = fae_daily_ai_analyzer.calibrate_daily_matches([data])
        data = rows[0] if rows else data
        owner_date = str(
            date_str or data.get('owner_date') or ''
        )[:10]
        data = (
            fae_daily_ai_analyzer.normalize_match_memory_governance(
                [data],
                mongo_storage.get_fae_review_memory(owner_date)
                if owner_date else {},
            )[0]
        )
        rows = fae_daily_ai_analyzer.apply_draw_radar([data])
        rows = fae_daily_ai_analyzer.apply_draw_radar_recommendation_overrides(
            rows
        )
        rows = fae_daily_ai_analyzer.apply_two_option_recommendations(rows)
        rows = fae_daily_ai_analyzer.apply_official_bet_recommendations(rows)
        rows = (
            fae_daily_ai_analyzer
            .apply_high_confidence_single_recommendations(rows)
        )
        data = rows[0] if rows else data
    return jsonify({'success': True, 'data': data})


@app.route('/api/fae/daily-ai/review', methods=['GET'])
def get_fae_daily_ai_review():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    date_str = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
    latest_run_id = mongo_storage.get_latest_fae_daily_ai_run_id(date_str)
    data = (
        mongo_storage.get_fae_daily_ai_review(
            date_str, run_id=latest_run_id
        )
        if latest_run_id else None
    )
    if data and data.get('ai_deep_review'):
        data['ai_deep_review'] = (
            fae_ai_review_analyzer.humanize_review_match_ids(
                data.get('ai_deep_review') or {},
                (data.get('ai_deep_review') or {}).get('matches')
                or data.get('match_results')
                or [],
            )
        )
    return jsonify({
        'success': True,
        'data': data,
        'primary_source': 'fae-daily-ai',
        'ai_review_enabled': os.getenv(
            'FAE_AI_REVIEW_ENABLED', 'true'
        ).lower() in ('1', 'true', 'yes', 'on'),
        'ai_review_configured': fae_ai_review_analyzer.configured,
        'run_id': latest_run_id,
    })


@app.route('/api/fae/daily-ai/review/stats', methods=['GET'])
def get_fae_daily_ai_review_stats():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    return jsonify({
        'success': True,
        'data': mongo_storage.get_fae_daily_ai_review_stats(),
    })


@app.route('/api/fae/backtest', methods=['GET'])
@login_required
def get_fae_shadow_backtest():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    try:
        days = max(7, min(60, int(request.args.get('days') or 28)))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '回测天数无效'}), 422
    report = mongo_storage.get_fae_backtest_report(days)
    if not report:
        report = _run_fae_shadow_backtest(days, save=False)
    return jsonify({
        'success': True,
        'data': report,
        'can_manage': bool(_fae_admin_user()),
    })


@app.route('/api/fae/backtest', methods=['POST'])
@fae_admin_required
def run_fae_shadow_backtest():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    payload = request.get_json(silent=True) or {}
    try:
        days = max(7, min(60, int(payload.get('days') or 28)))
        report = _run_fae_shadow_backtest(days, save=True)
        return jsonify({
            'success': True,
            'data': report,
            'message': '影子回测已使用最新赛果重新生成',
        })
    except Exception as exc:
        app.logger.exception('FAE shadow backtest failed')
        return jsonify({
            'success': False,
            'message': f'影子回测失败: {exc}',
        }), 500


def _public_supervised_model(model):
    if not model:
        return None
    hidden = {
        'draw_weights', 'margin_weights', 'feature_means',
        'feature_scales', 'league_priors', 'single_meta_weights',
        'single_meta_feature_means', 'single_meta_feature_scales',
    }
    return {
        key: value for key, value in dict(model).items()
        if key not in hidden
    }


@app.route('/api/fae/supervised-model', methods=['GET'])
def get_fae_supervised_model():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    model = mongo_storage.get_latest_fae_supervised_model(
        include_shadow=True
    )
    report = mongo_storage.get_latest_fae_supervised_backtest()
    return jsonify({
        'success': True,
        'data': {
            'model': _public_supervised_model(model),
            'backtest': report,
        },
        'can_manage': bool(_fae_admin_user()),
    })


@app.route('/api/fae/supervised-model/train', methods=['POST'])
@fae_admin_required
def train_fae_supervised_model():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    payload = request.get_json(silent=True) or {}
    try:
        days = max(30, min(365, int(payload.get('days') or 120)))
        result = _run_fae_supervised_training(days, save=True)
        return jsonify({
            'success': True,
            'data': {
                'model': _public_supervised_model(
                    (result or {}).get('model')
                ),
                'backtest': (result or {}).get('report'),
            },
            'message': (
                '监督模型已训练并进入影子验证，未覆盖正式推荐'
            ),
        })
    except (TypeError, ValueError) as exc:
        return jsonify({
            'success': False,
            'message': f'监督模型训练失败: {exc}',
        }), 422
    except Exception as exc:
        app.logger.exception('FAE supervised training failed')
        return jsonify({
            'success': False,
            'message': f'监督模型训练失败: {exc}',
        }), 500


@app.route('/api/fae/daily-ai/review', methods=['POST'])
@login_required
def run_fae_daily_ai_review():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    payload = request.get_json(silent=True) or {}
    date_str = str(
        payload.get('date') or datetime.now().strftime('%Y-%m-%d')
    )[:10]
    force_ai = bool(payload.get('force_ai') or payload.get('force'))
    review = _review_fae_daily_ai(date_str, force_ai=force_ai)
    wecom_delivery = _push_wecom_ai_review(review) if review else None
    return jsonify({
        'success': bool(review),
        'data': review,
        'wecom_delivery': wecom_delivery,
        'message': (
            (
                '复盘已完成，AI 深度诊断已写入数据库'
                if review and review.get('ai_deep_review')
                else '确定性复盘已完成，暂无可用的 AI 深度诊断'
            ) if review
            else '当天没有全场均未开赛时生成的 AI 研判快照'
        ),
    }), 200 if review else 404


@app.route('/api/fae/draw-parlays', methods=['GET'])
def get_fae_draw_parlays():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    date_str = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
    plan, snapshot = _snapshot_fae_draw_plan(date_str)
    data = dict(plan or {})
    data['snapshot_saved'] = bool(snapshot)
    data['snapshot_at'] = (snapshot or {}).get('generated_at')
    return jsonify({
        'success': True,
        'data': data,
    })


@app.route('/api/fae/draw-review', methods=['GET'])
def get_fae_draw_review():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    date_str = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
    return jsonify({
        'success': True,
        'data': mongo_storage.get_fae_draw_review(date_str),
    })


@app.route('/api/fae/draw-review/stats', methods=['GET'])
def get_fae_draw_review_stats():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    return jsonify({
        'success': True,
        'data': mongo_storage.get_fae_draw_review_stats(),
    })


@app.route('/api/fae/draw-review', methods=['POST'])
@login_required
def run_fae_draw_review():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    payload = request.get_json(silent=True) or {}
    date_str = str(
        payload.get('date') or datetime.now().strftime('%Y-%m-%d')
    )[:10]
    review = _review_fae_draw_plan(date_str)
    return jsonify({
        'success': bool(review),
        'data': review,
        'message': None if review else '当天没有可复盘的赛前快照',
    }), 200 if review else 404


@app.route('/api/fae/version', methods=['GET'])
def get_fae_version():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    return jsonify({
        'success': True,
        'data': mongo_storage.get_fae_version_info(),
    })


@app.route('/api/fae/skills', methods=['GET'])
def get_fae_skills():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    data = mongo_storage.get_fae_skill_center()
    data['can_manage'] = bool(_fae_admin_user())
    data['definitions'] = [
        {
            'skill_id': skill_id,
            'label': definition.get('label'),
            'description': definition.get('description'),
        }
        for skill_id, definition in SKILL_DEFINITIONS.items()
    ]
    return jsonify({'success': True, 'data': data})


@app.route('/api/fae/skills/candidates', methods=['POST'])
@fae_admin_required
def generate_fae_skill_candidates():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    candidates = mongo_storage.generate_fae_skill_candidates()
    return jsonify({
        'success': True,
        'data': mongo_storage.get_fae_skill_center(),
        'generated': len(candidates),
        'message': (
            f'已生成或更新 {len(candidates)} 个候选版本'
            if candidates else '当前没有满足样本与验证条件的新候选'
        ),
    })


@app.route('/api/fae/skills/<skill_id>/promote', methods=['POST'])
@fae_admin_required
def promote_fae_skill(skill_id):
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    if skill_id not in SKILL_DEFINITIONS:
        return jsonify({'success': False, 'message': '未知的 Skill'}), 404
    payload = request.get_json(silent=True) or {}
    candidate_id = str(payload.get('candidate_id') or '')
    if not candidate_id:
        return jsonify({'success': False, 'message': '缺少候选版本ID'}), 400
    user = _fae_admin_user()
    result = mongo_storage.promote_fae_skill_candidate(
        skill_id, candidate_id, actor=(user or {}).get('username')
    )
    return jsonify(result), 200 if result.get('success') else 409


@app.route('/api/fae/skills/<skill_id>/rollback', methods=['POST'])
@fae_admin_required
def rollback_fae_skill(skill_id):
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    if skill_id not in SKILL_DEFINITIONS:
        return jsonify({'success': False, 'message': '未知的 Skill'}), 404
    user = _fae_admin_user()
    result = mongo_storage.rollback_fae_skill(
        skill_id, actor=(user or {}).get('username')
    )
    return jsonify(result), 200 if result.get('success') else 409


@app.route('/api/fae/analyze-daily', methods=['POST'])
@login_required
def run_daily_fae_analysis():
    """手动运行指定日期；定时任务默认运行无模型费用的 FAE 核心。"""
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    payload = request.get_json(silent=True) or {}
    date_str = str(payload.get('date') or datetime.now().strftime('%Y-%m-%d'))[:10]
    force = bool(payload.get('force'))
    use_ai = payload.get('narrative', True) is not False
    matches = mongo_storage.get_matches(filters={'owner_date': date_str, 'status': 0})
    results = []
    for match in matches:
        cached = mongo_storage.get_ai_analysis(match.get('match_id'))
        if cached and cached.get('engine') and not force:
            results.append({'match_id': match.get('match_id'), 'cache_hit': True})
            continue
        analysis = _generate_fae_for_match(match, use_ai=use_ai)
        mongo_storage.save_ai_analysis(analysis)
        results.append({
            'match_id': match.get('match_id'),
            'cache_hit': False,
            'recommendation': (analysis.get('analysis') or {}).get('recommendation'),
        })
    _snapshot_fae_draw_plan(date_str)
    return jsonify({
        'success': True,
        'date': date_str,
        'count': len(results),
        'data': results,
    })


@app.route('/api/fae/review', methods=['POST'])
@login_required
def run_fae_review():
    if not mongo_storage:
        return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
    return jsonify({'success': True, 'data': _review_finished_fae_matches()})


@app.route('/api/leagues')
def get_leagues():
    """API - 获取所有联赛列表（仅MongoDB）"""
    leagues = mongo_storage.get_all_leagues() if mongo_storage else []
    return jsonify({
        'success': True,
        'data': leagues
    })


@app.route('/api/stats')
def get_stats():
    """API - 获取统计信息（仅MongoDB）"""
    stats = mongo_storage.get_stats() if mongo_storage else {
        'total_matches': 0,
        'total_leagues': 0,
        'status_stats': {},
        'league_stats': {}
    }
    return jsonify({
        'success': True,
        'data': stats
    })


@app.route('/api/crawl_stream')
def crawl_stream():
    def generate():
        try:
            date_str = request.args.get('date')
            if not date_str:
                date_str = datetime.now().strftime('%Y-%m-%d')
            url = f"https://live.500.com/?e={date_str}"
            yield f"data: 开始爬取日期 {date_str}\n\n"
            # 批量获取比赛列表 (fetch_odds=False 避免在crawler内部进行耗时的赔率抓取)
            matches = crawler.crawl_daily_matches(url, fetch_odds=False)
            if not matches:
                yield "data: 未能爬取到数据\n\n"
                yield "event: done\ndata: fail\n\n"
                return
            if not mongo_storage:
                yield "data: MongoDB不可用\n\n"
                yield "event: done\ndata: fail\n\n"
                return
            count = mongo_storage.save_matches(matches)
            yield f"data: 已写入比赛 {count} 条\n\n"
            odds_count = 0
            total = len(matches)
            workers = request.args.get('workers', '8')
            try:
                workers = max(1, min(16, int(workers)))
            except Exception:
                workers = 8
            yield f"data: 并发线程数: {workers}\n\n"
            def fetch(mid):
                return crawler.crawl_match_odds(mid)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {}
                i = 0
                for m in matches:
                    mid = m.get('match_id')
                    if not mid or not is_pregame_match(m):
                        continue
                    i += 1
                    futures[executor.submit(fetch, mid)] = m
                    yield f"data: [{i}/{total}] 提交赔率任务 {mid} - {m.get('home_team', '')} vs {m.get('away_team', '')}\n\n"
                completed = 0
                for fut in as_completed(futures):
                    completed += 1
                    m = futures[fut]
                    mid = m.get('match_id')
                    home = m.get('home_team', '')
                    away = m.get('away_team', '')
                    try:
                        odds = fut.result()
                        if odds:
                            # 映射赔率详情到比赛对象
                            crawler._map_odds_details(m, odds)
                            # 保存赔率数据
                            mongo_storage.save_odds(mid, odds)
                            # 更新比赛基础数据(包含映射后的赔率字符串)
                            mongo_storage.save_match(m)
                            odds_count += 1
                            yield f"data: 完成[{completed}/{i}] 写入赔率 {mid} - {home} vs {away}\n\n"
                        else:
                            yield f"data: 完成[{completed}/{i}] 赔率为空 {mid} - {home} vs {away}\n\n"
                    except Exception as e:
                        yield f"data: 赔率任务异常 {mid} - {home} vs {away}: {str(e)}\n\n"
            yield f"data: 完成。比赛 {count}，赔率 {odds_count}\n\n"
            yield "event: done\ndata: success\n\n"
        except Exception as e:
            yield f"data: 发生错误: {str(e)}\n\n"
            yield "event: done\ndata: fail\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/api/crawl')
def crawl_new_data():
    """API - 爬取新数据（仅写入MongoDB）"""
    try:
        # 爬取指定日期或当天比赛
        date_str = request.args.get('date')
        if not date_str:
            date_str = datetime.now().strftime('%Y-%m-%d')
        url = f"https://live.500.com/?e={date_str}"
        
        # 传入 mongo_storage 实现边爬边存
        c = FootballCrawler(mongo_storage)
        # 批量获取比赛列表
        matches = c.crawl_daily_matches(url, fetch_odds=False)
        
        if not matches:
            return jsonify({
                'success': False,
                'message': '未能爬取到数据'
            })
        
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        
        # 之前在 crawl_daily_matches 内部已经逐条保存了比赛基本信息
        # 现在需要爬取每场比赛的赔率（欧赔/亚盘/大小球）并发执行
        count = len(matches)
        odds_count = 0
        workers = request.args.get('workers', '8')
        try:
            workers = max(1, min(16, int(workers)))
        except Exception:
            workers = 8
        
        def fetch(mid):
            # 这里的 crawl_match_odds 内部并没有自动保存赔率，所以需要在这里保存
            # 如果也想改成边爬边存，需要修改 crawler.crawl_match_odds
            # 暂时保持在这里手动保存，或者修改 crawler.crawl_match_odds
            return crawler.crawl_match_odds(mid)
            
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for m in matches:
                mid = m.get('match_id')
                if not mid or not is_pregame_match(m):
                    continue
                futures[executor.submit(fetch, mid)] = mid
            for fut in as_completed(futures):
                mid = futures[fut]
                try:
                    odds = fut.result()
                    if odds:
                        mongo_storage.save_odds(mid, odds)
                        odds_count += 1
                except Exception:
                    pass
        
        return jsonify({
            'success': True,
            'message': f'成功爬取 {count} 场比赛，并写入 {odds_count} 场赔率',
            'count': count,
            'odds_count': odds_count
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'爬取失败: {str(e)}'
        }), 500


@app.route('/api/crawl_odds/<match_id>')
def crawl_match_odds(match_id):
    """API - 爬取指定比赛的赔率"""
    try:
        existing_match = (
            mongo_storage.get_match_by_id(match_id)
            if mongo_storage else None
        )
        if existing_match and not is_pregame_match(existing_match):
            return jsonify({
                'success': False,
                'message': '比赛已开赛，拒绝覆盖赛前即时盘',
                'code': 'PREGAME_ODDS_LOCKED',
            }), 409

        odds = crawler.crawl_match_odds(match_id)
        
        # 保存到MongoDB
        if mongo_storage and odds:
            mongo_storage.save_odds(match_id, odds)
        
        return jsonify({
            'success': True,
            'data': odds
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'爬取赔率失败: {str(e)}'
        }), 500


@app.route('/api/match/<match_id>/movement')
def get_match_movement(match_id):
    """API - 获取比赛赔率变动分析"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        m = mongo_storage.get_match_by_id(match_id)
        if not m:
            return jsonify({'success': False, 'message': '比赛不存在'}), 404
        
        def safe_float(x):
            try:
                return float(x)
            except Exception:
                return None
        
        # 欧赔变动
        euro = None
        win_i = safe_float(m.get('euro_initial_win'))
        draw_i = safe_float(m.get('euro_initial_draw'))
        lose_i = safe_float(m.get('euro_initial_lose'))
        win_c = safe_float(m.get('euro_current_win'))
        draw_c = safe_float(m.get('euro_current_draw'))
        lose_c = safe_float(m.get('euro_current_lose'))
        if all(v is not None for v in [win_i, draw_i, lose_i, win_c, draw_c, lose_c]):
            def mov(init, curr):
                diff = curr - init
                return {'change': round(diff, 2), 'dir': '↑' if diff > 0 else ('↓' if diff < 0 else '—')}
            euro = {
                'initial': {'win': m.get('euro_initial_win'), 'draw': m.get('euro_initial_draw'), 'lose': m.get('euro_initial_lose')},
                'current': {'win': m.get('euro_current_win'), 'draw': m.get('euro_current_draw'), 'lose': m.get('euro_current_lose')},
                'movement': {'win': mov(win_i, win_c), 'draw': mov(draw_i, draw_c), 'lose': mov(lose_i, lose_c)},
                'tendency': (('看好主队') if (win_i is not None and win_c is not None and (win_c - win_i) < -0.1)
                             else (('看好客队') if (lose_i is not None and lose_c is not None and (lose_c - lose_i) < -0.1)
                             else (('看好平局') if (draw_i is not None and draw_c is not None and (draw_c - draw_i) < -0.1)
                             else None)))
            }
        
        # 亚盘变动
        asian = None
        def parse_handicap(h):
            if not h:
                return None
            import re
            nums = re.findall(r'\d+\.?\d*', str(h))
            return float(nums[0]) if nums else None
        h_i = parse_handicap(m.get('asian_initial_handicap'))
        h_c = parse_handicap(m.get('asian_current_handicap'))
        home_i = safe_float(m.get('asian_initial_home_odds'))
        away_i = safe_float(m.get('asian_initial_away_odds'))
        home_c = safe_float(m.get('asian_current_home_odds'))
        away_c = safe_float(m.get('asian_current_away_odds'))
        if (h_i is not None and h_c is not None):
            def movv(init, curr):
                if init is None or curr is None:
                    return None
                diff = curr - init
                return {'change': round(diff, 2), 'dir': '↑' if diff > 0 else ('↓' if diff < 0 else '—')}
            asian = {
                'initial': {'home': m.get('asian_initial_home_odds'), 'handicap': m.get('asian_initial_handicap'), 'away': m.get('asian_initial_away_odds')},
                'current': {'home': m.get('asian_current_home_odds'), 'handicap': m.get('asian_current_handicap'), 'away': m.get('asian_current_away_odds')},
                'movement': {'handicap': movv(h_i, h_c), 'home': movv(home_i, home_c), 'away': movv(away_i, away_c)}
            }
            tend = None
            if asian['movement']['handicap'] and asian['movement']['home'] and asian['movement']['handicap']['change'] > 0.1 and asian['movement']['home']['change'] < 0:
                tend = '升盘降水，强力看好主队'
            elif asian['movement']['handicap'] and asian['movement']['away'] and asian['movement']['handicap']['change'] < -0.1 and asian['movement']['away']['change'] < 0:
                tend = '降盘降水，强力看好客队'
            elif asian['movement']['handicap'] and asian['movement']['home'] and asian['movement']['handicap']['change'] > 0.1 and asian['movement']['home']['change'] > 0:
                tend = '升盘升水，可能诱盘'
            elif asian['movement']['handicap'] and asian['movement']['away'] and asian['movement']['handicap']['change'] < -0.1 and asian['movement']['away']['change'] > 0:
                tend = '降盘升水，可能诱盘'
            asian['tendency'] = tend
        
        return jsonify({'success': True, 'data': {'euro': euro, 'asian': asian}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/match/<match_id>')
def match_detail(match_id):
    """比赛详情页面"""
    return render_template('match_detail.html', match_id=match_id)


@app.route('/my_picks')
def my_picks_page():
    """我的选择页面"""
    return render_template('my_picks.html')


@app.route('/calculator')
def calculator_page():
    """计算器页面"""
    return render_template('calculator.html')


@app.route('/api/calculator/matches')
def get_calculator_matches():
    """获取计算器比赛数据（代理体彩API）"""
    try:
        import requests
        url = 'https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry'
        params = {
            'poolCode': request.args.get('poolCode', 'had,hhad,crs,ttg,hafu')
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1',
            'Referer': 'https://www.sporttery.cn/'
        }

        resp = sporttery_calculator_session.get(
            url,
            params=params,
            headers=headers,
            timeout=15,
        )

        if resp.status_code == 200:
            try:
                data = resp.json()
                return jsonify({'success': True, 'data': data})
            except:
                # If not JSON, return raw
                return jsonify({'success': True, 'raw': resp.text})
        else:
            return jsonify({'success': False, 'status': resp.status_code, 'message': 'API request failed'})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/predictions')
def get_predictions():
    """API - 获取预测列表"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        
        # 获取筛选参数
        filters = {}
        match_id = request.args.get('match_id')
        if match_id:
            filters['match_id'] = str(match_id)
        
        # 获取数量限制
        limit = request.args.get('limit', '50')
        try:
            limit = max(1, min(200, int(limit)))
        except ValueError:
            limit = 50
        
        predictions = mongo_storage.get_predictions(filters=filters, limit=limit)
        
        return jsonify({
            'success': True,
            'data': predictions,
            'count': len(predictions)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取预测失败: {str(e)}'
        }), 500

scheduler = None


def _run_scheduled_fae_daily_ai():
    """Daily paid Ark job, kept separate from the 15-minute deterministic crawl."""
    if os.getenv('FAE_DAILY_AI_ENABLED', 'true').lower() not in (
        '1', 'true', 'yes', 'on'
    ):
        return
    date_str = datetime.now().strftime('%Y-%m-%d')
    try:
        result = _run_fae_daily_ai(date_str)
        print(
            f"✅ FAE 火山全日研判: {date_str}，"
            f"{result.get('match_count', 0)} 场，"
            f"{'缓存命中' if result.get('cache_hit') else '已生成并逐场入库'}"
        )
        delivery = _push_wecom_daily_ai(result)
        if delivery.get('success'):
            print(f"✅ 企业微信全日研判: {delivery.get('status')}")
        elif delivery.get('status') not in (
            'disabled', 'not_configured'
        ):
            print(
                f"⚠️  企业微信全日研判未发送: "
                f"{delivery.get('message') or delivery.get('status')}"
            )
    except Exception as exc:
        print(f"❌ FAE 火山全日研判失败 {date_str}: {str(exc)}")


def _run_scheduled_fae_shadow_backtest():
    """Refresh the deterministic old/new comparison without Ark calls."""
    if not _env_enabled('FAE_SHADOW_BACKTEST_ENABLED', True):
        return
    try:
        report = _run_fae_shadow_backtest(
            int(os.getenv('FAE_SHADOW_BACKTEST_DAYS', '28')),
            save=True,
        )
        if report:
            candidate = report.get('candidate') or {}
            print(
                "✅ FAE 影子回测: "
                f"{candidate.get('hits', 0)}/"
                f"{candidate.get('settled', 0)}，"
                f"ROI {candidate.get('roi', 0)}%，"
                f"{(report.get('release_guard') or {}).get('status')}"
            )
    except Exception as exc:
        print(f"❌ FAE 影子回测失败: {str(exc)}")


def _run_scheduled_fae_supervised_training():
    """Retrain once new immutable, settled match days are available."""
    if not _env_enabled('FAE_SUPERVISED_TRAINING_ENABLED', True):
        return
    if not mongo_storage:
        return
    try:
        days = max(
            30,
            min(365, int(os.getenv('FAE_SUPERVISED_TRAINING_DAYS', '120'))),
        )
        dates = mongo_storage.get_fae_daily_ai_snapshot_dates(days)
        latest_source = dates[-1] if dates else None
        current = mongo_storage.get_latest_fae_supervised_model(
            include_shadow=True
        ) or {}
        if latest_source and current.get('training_end_date') == latest_source:
            return
        result = _run_fae_supervised_training(days, save=True)
        report = (result or {}).get('report') or {}
        guard = report.get('release_guard') or {}
        print(
            "✅ FAE 监督模型影子训练: "
            f"{report.get('training_sample_count', 0)}场，"
            f"平局Brier {((report.get('ordinary_draw') or {}).get('brier', 0))}，"
            f"让平Brier {((report.get('handicap_draw') or {}).get('brier', 0))}，"
            f"{guard.get('status', 'shadow_only')}"
        )
    except Exception as exc:
        print(f"❌ FAE 监督模型影子训练失败: {str(exc)}")


def _run_scheduled_fae_prematch_ai():
    """Run paid Ark analysis once per fixture in its T-minus window."""
    if not _env_enabled('FAE_DAILY_AI_ENABLED', True):
        return
    if not mongo_storage:
        return
    try:
        lead_minutes = max(
            5, min(180, int(os.getenv('FAE_PREMATCH_AI_LEAD_MINUTES', '30')))
        )
    except ValueError:
        lead_minutes = 30
    now = datetime.now(app_timezone())
    owner_dates = sorted({
        now.strftime('%Y-%m-%d'),
        (now - timedelta(days=1)).strftime('%Y-%m-%d'),
    })
    candidates = mongo_storage.get_matches(filters={
        'owner_date': {'$in': owner_dates},
        'status': 0,
    })
    due_by_date = {}
    for match in candidates:
        match_id = str(match.get('match_id') or '')
        owner_date = str(match.get('owner_date') or '')[:10]
        if not match_id or not owner_date:
            continue
        latest = mongo_storage.get_fae_daily_ai_match(match_id, owner_date)
        if not prematch_analysis_due(
            match,
            now=now,
            latest_generated_at=(latest or {}).get('generated_at'),
            lead_minutes=lead_minutes,
        ):
            continue
        due_by_date.setdefault(owner_date, []).append(match)

    for owner_date, due_matches in sorted(due_by_date.items()):
        due_ids = [str(item.get('match_id')) for item in due_matches]
        # Refresh only the due fixtures immediately before the paid model call.
        # The regular 15-minute crawl remains the broad data refresh.
        with ThreadPoolExecutor(max_workers=min(8, len(due_ids))) as executor:
            futures = {
                executor.submit(crawler.crawl_match_odds, match_id): match_id
                for match_id in due_ids
            }
            for future in as_completed(futures):
                match_id = futures[future]
                try:
                    odds = future.result()
                    if odds:
                        mongo_storage.save_odds(match_id, odds)
                except Exception as exc:
                    print(
                        f"⚠️  FAE赛前研判赔率刷新失败 {match_id}: {str(exc)}"
                    )
        try:
            result = _run_fae_daily_ai(
                owner_date,
                force=True,
                target_match_ids=due_ids,
            )
            print(
                f"✅ FAE开赛前{lead_minutes}分钟研判: {owner_date}，"
                f"本轮 {len(due_ids)} 场，累计保留 "
                f"{result.get('match_count', 0)} 场"
            )
            if _env_enabled('WECOM_PUSH_PREMATCH_AI', False):
                delivery = _push_wecom_daily_ai(result)
                if delivery.get('success'):
                    print("✅ 企业微信赛前研判已发送")
        except Exception as exc:
            print(
                f"❌ FAE开赛前{lead_minutes}分钟研判失败 "
                f"{owner_date} ({','.join(due_ids)}): {str(exc)}"
            )

def _crawl_latest():
    try:
        print(f"⏰ 开始定时爬取任务: {datetime.now()}")
        
        # 1. 确定爬取日期范围：总是爬取今天；部分美洲赛事会挂在昨天竞彩日，
        #    但北京时间中午后才完场，所以结果回刷窗口要覆盖到下午。
        now = datetime.now()
        dates_to_crawl = []

        def add_crawl_date(value):
            date_value = str(value or '')[:10]
            if date_value and date_value not in dates_to_crawl:
                dates_to_crawl.append(date_value)

        add_crawl_date(now.strftime('%Y-%m-%d'))
        try:
            backfill_until_hour = int(os.getenv('RESULT_BACKFILL_UNTIL_HOUR', '16'))
        except ValueError:
            backfill_until_hour = 16
        if now.hour < max(1, min(24, backfill_until_hour)):
            yesterday = now - timedelta(days=1)
            add_crawl_date(yesterday.strftime('%Y-%m-%d'))
            print(f"ℹ️  赛果回刷窗口内，额外爬取昨天数据: {dates_to_crawl[-1]}")

        # 待结算票据兜底：只要用户还有近几天的待结算票，就继续回刷对应竞彩日。
        # 这样不会因为美职联/南美等跨自然日完赛，错过最终状态。
        try:
            pending_backfill_days = max(1, int(os.getenv('RESULT_PENDING_BACKFILL_DAYS', '3')))
        except ValueError:
            pending_backfill_days = 3
        try:
            today_date = now.date()
            pending_dates = set()
            for bet in user_storage.list_pending_bets():
                for item in bet.get('selected_items') or []:
                    date_value = str(item.get('date') or '')[:10]
                    if not date_value:
                        continue
                    try:
                        item_date = datetime.strptime(date_value, '%Y-%m-%d').date()
                    except ValueError:
                        continue
                    age_days = (today_date - item_date).days
                    if 0 <= age_days <= pending_backfill_days:
                        pending_dates.add(date_value)
            for date_value in sorted(pending_dates):
                add_crawl_date(date_value)
            if pending_dates:
                print(f"ℹ️  待结算票据回刷日期: {', '.join(sorted(pending_dates))}")
        except Exception as e:
            print(f"⚠️  读取待结算票据回刷日期失败: {str(e)}")
        
        all_matches = []
        for date_str in dates_to_crawl:
            url = f"https://live.500.com/?e={date_str}"
            # 使用全局 crawler (不带 mongo_storage，需手动保存)
            # 批量获取比赛列表
            try:
                ms = crawler.crawl_daily_matches(url, fetch_odds=False)
                if ms:
                    all_matches.extend(ms)
            except Exception as e:
                print(f"⚠️  爬取 {date_str} 失败: {str(e)}")
        
        matches = all_matches
        
        if not matches:
            print("⚠️  定时任务: 未爬取到比赛数据")
            return

        # 2. 保存比赛基本信息
        if mongo_storage:
            count = mongo_storage.save_matches(matches)
            print(f"✅ 定时任务: 已更新 {count} 场比赛基本信息")
        
        # 3. 并发爬取赔率。开赛后锁定赛前即时盘，不再覆盖。
        workers = 8
        odds_count = 0
        
        def fetch(mid):
            return crawler.crawl_match_odds(mid)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for m in matches:
                mid = m.get('match_id')
                # 仅未开始比赛可更新；进行中和完场均保留赛前最后值。
                if mid and is_pregame_match(m):
                    futures[executor.submit(fetch, mid)] = m
            
            for fut in as_completed(futures):
                m = futures[fut]
                mid = m.get('match_id')
                try:
                    odds = fut.result()
                    if odds and mongo_storage:
                        mongo_storage.save_odds(mid, odds)
                        odds_count += 1
                except Exception as e:
                    print(f"❌ 定时任务: 爬取赔率失败 {mid}: {str(e)}")
        
        print(f"✅ 定时任务: 已更新 {odds_count} 场比赛赔率")

        # 4. FAE 自动分析与赛后复盘。
        # 默认只运行无模型费用的确定性核心；说明层可通过环境变量单独开启。
        if mongo_storage and os.getenv('FAE_AUTO_ANALYZE', 'true').lower() in ('1', 'true', 'yes', 'on'):
            use_ai_narrative = os.getenv('FAE_AUTO_NARRATIVE', 'false').lower() in ('1', 'true', 'yes', 'on')
            refresh_minutes = max(15, int(os.getenv('FAE_AUTO_REFRESH_MINUTES', '60')))
            fae_count = 0
            for listed_match in matches:
                if listed_match.get('status') != 0:
                    continue
                match_id = str(listed_match.get('match_id') or '')
                fresh_match = mongo_storage.get_match_by_id(match_id) or listed_match
                cached = mongo_storage.get_ai_analysis(match_id)
                if cached and cached.get('engine'):
                    try:
                        generated = datetime.fromisoformat(
                            str(cached.get('generated_at') or '').replace('Z', '+00:00')
                        )
                        now_for_cache = datetime.now(generated.tzinfo) if generated.tzinfo else datetime.now()
                        if (now_for_cache - generated).total_seconds() < refresh_minutes * 60:
                            continue
                    except (TypeError, ValueError):
                        pass
                try:
                    fae_analysis = _generate_fae_for_match(
                        fresh_match, use_ai=use_ai_narrative
                    )
                    if mongo_storage.save_ai_analysis(fae_analysis):
                        fae_count += 1
                except Exception as exc:
                    print(f"❌ FAE 自动分析失败 {match_id}: {str(exc)}")
            print(f"✅ FAE 自动分析: 更新 {fae_count} 场")

        review_result = _review_finished_fae_matches(
            [mongo_storage.get_match_by_id(m.get('match_id')) or m for m in matches]
            if mongo_storage else []
        )
        if review_result.get('reviewed'):
            print(
                f"✅ FAE 自动复盘: {review_result['reviewed']} 场，"
                f"权重调整 {review_result['adjustments']} 项"
            )

        if mongo_storage:
            owner_date = datetime.now().strftime('%Y-%m-%d')
            try:
                ai_review_count = 0
                for snapshot_date in (
                    mongo_storage.get_fae_daily_ai_snapshot_dates(
                        14, eligible_only=False
                    )
                ):
                    daily_review = _review_fae_daily_ai(snapshot_date)
                    if daily_review:
                        ai_review_count += 1
                        _push_wecom_ai_review(daily_review)
                if ai_review_count:
                    print(
                        f"✅ FAE AI主复盘: 更新 {ai_review_count} 天"
                    )
            except Exception as exc:
                print(f"❌ FAE AI主复盘失败: {str(exc)}")
            try:
                _snapshot_fae_draw_plan(owner_date)
                draw_review_count = 0
                for snapshot_date in mongo_storage.get_fae_draw_snapshot_dates(7):
                    if _review_fae_draw_plan(snapshot_date):
                        draw_review_count += 1
                if draw_review_count:
                    print(f"✅ FAE 平/让平专项复盘: 更新 {draw_review_count} 天")
            except Exception as exc:
                print(f"❌ FAE 平/让平快照或复盘失败: {str(exc)}")

        # 5. 企业微信比赛提醒
        def to_float(x):
            try:
                return float(x)
            except Exception:
                return None
                
        def meets_alert(m):
            if m.get('status') != 1:
                return False
            draw_odds = to_float(m.get('euro_current_draw') or m.get('euro_initial_draw'))
            let_val = str(m.get('hi_handicap_value') or '').strip()
            cond_ping = draw_odds is not None and 2.85 <= draw_odds <= 3.5
            cond_rangping = let_val in ['0', '平手'] or ('平' in let_val)
            return cond_ping or cond_rangping
            
        alert_matches = []
        for m in matches or []:
            if meets_alert(m):
                alert_matches.append(m)
                
        if (
            alert_matches
            and _env_enabled('WECOM_PUSH_LIVE_ALERTS', True)
        ):
            content = format_live_alert_message(alert_matches)
            alert_identity = '|'.join(sorted(
                str(item.get('match_id') or item.get('match_number') or '')
                for item in alert_matches
                if item.get('match_id') or item.get('match_number')
            ))
            delivery = _deliver_wecom_once(
                'live_alerts',
                alert_identity,
                content,
            )
            if delivery.get('success'):
                print(f"✅ 已推送 {len(alert_matches)} 场企业微信比赛提醒")
    except Exception as e:
        print(f"❌ 定时爬取任务异常: {str(e)}")
        import traceback
        traceback.print_exc()

def _start_scheduler():
    global scheduler
    try:
        if BackgroundScheduler and CronTrigger:
            if scheduler is None:
                scheduler = BackgroundScheduler()
                scheduler.add_job(_crawl_latest, CronTrigger(minute='*/15'), id='crawl_every_15m', replace_existing=True)
                scheduler.add_job(
                    _settle_pending_calculator_bets,
                    CronTrigger(minute='*/5'),
                    id='settle_calculator_bets_every_5m',
                    replace_existing=True,
                )
                try:
                    prematch_check_minutes = max(
                        1,
                        min(
                            15,
                            int(os.getenv('FAE_PREMATCH_AI_CHECK_MINUTES', '5')),
                        ),
                    )
                except ValueError:
                    prematch_check_minutes = 5
                scheduler.add_job(
                    _run_scheduled_fae_prematch_ai,
                    CronTrigger(minute=f'*/{prematch_check_minutes}'),
                    id='fae_prematch_ai',
                    replace_existing=True,
                    coalesce=True,
                    max_instances=1,
                )
                scheduler.add_job(
                    _run_scheduled_fae_shadow_backtest,
                    CronTrigger(hour='*/2', minute=7),
                    id='fae_shadow_backtest',
                    replace_existing=True,
                    coalesce=True,
                    max_instances=1,
                )
                scheduler.add_job(
                    _run_scheduled_fae_supervised_training,
                    CronTrigger(hour=6, minute=23),
                    id='fae_supervised_training',
                    replace_existing=True,
                    coalesce=True,
                    max_instances=1,
                    # First deployment should not wait until the next morning
                    # to create its initial shadow artifact.  Subsequent
                    # starts return quickly when the newest snapshot is
                    # already represented by the persisted model.
                    next_run_time=datetime.now() + timedelta(seconds=45),
                )
                baseline_label = '关闭'
                if _env_enabled('FAE_DAILY_AI_BASELINE_ENABLED', False):
                    daily_hour = max(
                        0, min(23, int(os.getenv('FAE_DAILY_AI_HOUR', '12')))
                    )
                    daily_minute = max(
                        0,
                        min(59, int(os.getenv('FAE_DAILY_AI_MINUTE', '10'))),
                    )
                    scheduler.add_job(
                        _run_scheduled_fae_daily_ai,
                        CronTrigger(hour=daily_hour, minute=daily_minute),
                        id='fae_daily_ai',
                        replace_existing=True,
                    )
                    baseline_label = f'{daily_hour:02d}:{daily_minute:02d}'
                scheduler.start()
                print(
                    "✅ 定时任务调度器已启动 "
                    f"(每15分钟刷新，每5分钟结算投注，"
                    f"每{prematch_check_minutes}分钟检查开赛前30分钟研判，"
                    f"每2小时刷新影子回测，每日06:23训练监督模型，"
                    f"固定全日研判{baseline_label})"
                )
        else:
            print("⚠️  无法启动定时任务: APScheduler未安装")
    except Exception as e:
        print(f"❌ 启动定时任务失败: {str(e)}")
        scheduler = None

@app.before_first_request
def _init_jobs():
    _start_scheduler()

def format_all_odds(m):
    def val(x):
        return str(x) if x is not None else ''
    euro_init = f"{val(m.get('euro_initial_win'))}/{val(m.get('euro_initial_draw'))}/{val(m.get('euro_initial_lose'))}"
    euro_curr = f"{val(m.get('euro_current_win'))}/{val(m.get('euro_current_draw'))}/{val(m.get('euro_current_lose'))}"
    asian_init = f"{val(m.get('asian_initial_home_odds'))}/{val(m.get('asian_initial_handicap'))}/{val(m.get('asian_initial_away_odds'))}"
    asian_curr = f"{val(m.get('asian_current_home_odds'))}/{val(m.get('asian_current_handicap'))}/{val(m.get('asian_current_away_odds'))}"
    ou_init = f"{val(m.get('ou_initial_under_odds'))}/{val(m.get('ou_initial_total'))}/{val(m.get('ou_initial_over_odds'))}"
    ou_curr = f"{val(m.get('ou_current_under_odds'))}/{val(m.get('ou_current_total'))}/{val(m.get('ou_current_over_odds'))}"
    hi_val = val(m.get('hi_handicap_value'))
    hi_init = f"{val(m.get('hi_initial_home_odds'))}/{val(m.get('hi_initial_draw_odds'))}/{val(m.get('hi_initial_away_odds'))}"
    hi_curr = f"{val(m.get('hi_current_home_odds'))}/{val(m.get('hi_current_draw_odds'))}/{val(m.get('hi_current_away_odds'))}"
    parts = []
    parts.append(f"欧赔 初:{euro_init} 即:{euro_curr}")
    parts.append(f"亚盘 初:{asian_init} 即:{asian_curr}")
    parts.append(f"大小球 初:{ou_init} 即:{ou_curr}")
    parts.append(f"让球指数 盘:{hi_val} 初:{hi_init} 即:{hi_curr}")
    return "\n".join(parts)


@app.route('/api/predict/<match_id>')
def predict_match(match_id):
    """API - 预测指定比赛"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        
        # 获取比赛数据
        match = mongo_storage.get_match_by_id(match_id)
        if not match:
            return jsonify({'success': False, 'message': '比赛不存在'}), 404
        
        # 执行预测
        engine = PredictionEngine()
        prediction = engine.predict_match(match)
        
        if prediction:
            # 保存预测结果
            mongo_storage.save_prediction(prediction)
            
            return jsonify({
                'success': True,
                'data': prediction
            })
        else:
            return jsonify({
                'success': False,
                'message': '预测失败'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'预测失败: {str(e)}'
        }), 500





@app.route('/api/predict/manual/<match_id>', methods=['POST'])
def manual_predict(match_id):
    """API - 手动选择投注方向并保存到预测表（支持多选）"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        
        match = mongo_storage.get_match_by_id(match_id)
        if not match:
            return jsonify({'success': False, 'message': '比赛不存在'}), 404
        
        data = request.get_json(silent=True) or {}
        
        # 获取设备ID（必需）
        device_id = data.get('device_id')
        if not device_id:
            return jsonify({'success': False, 'message': '未提供设备标识'}), 400
            
        opts = data.get('options')
        if isinstance(opts, str):
            opts = [opts]
        if not isinstance(opts, list):
            # 兼容旧字段
            opt = str(data.get('option') or '').strip()
            opts = [opt] if opt else []
        allowed = {'win','draw','lose','h_win','h_draw','h_lose'}
        options = [o for o in (opts or []) if o in allowed]
        if not options:
            return jsonify({'success': False, 'message': '未选择有效选项'}), 400
        
        # 获取信心指数，默认90
        confidence = data.get('confidence')
        try:
            confidence = float(confidence) if confidence is not None else 90.0
            confidence = max(0.0, min(100.0, confidence))
        except Exception:
            confidence = 90.0
            
        pick = {
            'match_id': match_id,
            'device_id': device_id,
            'source': 'manual',
            'manual': True,
            'manual_options': options,
        }
        
        # 1X2映射
        ones = [o for o in options if o in {'win','draw','lose'}]
        if len(set(ones)) == 1:
            o = ones[0]
            pick['manual_win_prediction'] = {'win':'home','draw':'draw','lose':'away'}[o]
            pick['manual_win_confidence'] = confidence
            
        # 让球映射
        aopts = [o for o in options if o in {'h_win','h_lose'}]
        if len(set(aopts)) == 1:
            o = aopts[0]
            pick['manual_asian_prediction'] = 'home' if o=='h_win' else 'away'
            pick['manual_asian_confidence'] = confidence
            pick['manual_asian_handicap'] = match.get('asian_current_handicap') or match.get('asian_initial_handicap') or ''
            
        # 保存到用户选择表
        mongo_storage.save_user_pick(pick)
        
        return jsonify({'success': True, 'data': pick})
    except Exception as e:
        return jsonify({'success': False, 'message': f'手动预测失败: {str(e)}'}), 500
@app.route('/api/predict/manual/<match_id>', methods=['DELETE'])
def delete_manual_predict(match_id):
    """API - 删除手动预测"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
            
        data = request.get_json(silent=True) or {}
        device_id = data.get('device_id') or request.args.get('device_id')
        
        if not device_id:
            return jsonify({'success': False, 'message': '未提供设备标识'}), 400
            
        success = mongo_storage.delete_user_pick(device_id, match_id)
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': '删除失败或记录不存在'}), 404
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500


@app.route('/api/recommend')
def get_recommend():
    """API - 获取N串1推荐方案"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        
        # 获取参数
        n = request.args.get('n', '2')
        target_odds = request.args.get('target_odds', '3.0')
        try:
            n = max(2, min(5, int(n)))
            target_odds = max(2.0, min(10.0, float(target_odds)))
        except ValueError:
            n = 2
            target_odds = 3.0
        
        # 获取未开赛比赛
        upcoming_matches = mongo_storage.get_matches({'status': 0})
        finished_matches = mongo_storage.get_matches({'status': 2})
        
        if not upcoming_matches:
            return jsonify({'success': False, 'message': '暂无未开赛比赛'}), 404
        
        # 构建球队统计
        team_stats = {}
        for m in finished_matches:
            try:
                home = m['home_team']
                away = m['away_team']
                h_score = int(m['home_score'])
                a_score = int(m['away_score'])
                total_goals = h_score + a_score
                
                for t in [home, away]:
                    if t not in team_stats:
                        team_stats[t] = {'matches': 0, 'goals_for': 0, 'goals_against': 0, 
                                         'wins': 0, 'draws': 0, 'losses': 0, 'big_games': 0}
                
                team_stats[home]['matches'] += 1
                team_stats[home]['goals_for'] += h_score
                team_stats[home]['goals_against'] += a_score
                if h_score > a_score: team_stats[home]['wins'] += 1
                elif h_score == a_score: team_stats[home]['draws'] += 1
                else: team_stats[home]['losses'] += 1
                if total_goals >= 3: team_stats[home]['big_games'] += 1
                
                team_stats[away]['matches'] += 1
                team_stats[away]['goals_for'] += a_score
                team_stats[away]['goals_against'] += h_score
                if a_score > h_score: team_stats[away]['wins'] += 1
                elif a_score == h_score: team_stats[away]['draws'] += 1
                else: team_stats[away]['losses'] += 1
                if total_goals >= 3: team_stats[away]['big_games'] += 1
            except:
                continue
        
        # 筛选候选
        candidates = []
        for m in upcoming_matches:
            try:
                home = m['home_team']
                away = m['away_team']
                
                win_odds = float(m.get('euro_initial_win') or 0)
                lose_odds = float(m.get('euro_initial_lose') or 0)
                ou_line = float(m.get('ou_initial_total') or 0)
                ou_over = float(m.get('ou_initial_over_odds') or 0)
                real_ou_odds = ou_over + 1.0
                
                h_stats = team_stats.get(home, {})
                a_stats = team_stats.get(away, {})
                
                # 主胜
                if 1.50 <= win_odds <= 2.0:
                    h_win_rate = h_stats.get('wins', 0) / max(h_stats.get('matches', 1), 1) * 100
                    a_loss_rate = a_stats.get('losses', 0) / max(a_stats.get('matches', 1), 1) * 100
                    if h_win_rate >= 40 or a_loss_rate >= 40:
                        candidates.append({
                            'match': m,
                            'type': '主胜',
                            'odds': win_odds,
                            'reason': f'{home}胜率{h_win_rate:.0f}%，{away}败率{a_loss_rate:.0f}%'
                        })
                
                # 客胜
                if 1.50 <= lose_odds <= 2.0:
                    a_win_rate = a_stats.get('wins', 0) / max(a_stats.get('matches', 1), 1) * 100
                    h_loss_rate = h_stats.get('losses', 0) / max(h_stats.get('matches', 1), 1) * 100
                    if a_win_rate >= 40 or h_loss_rate >= 40:
                        candidates.append({
                            'match': m,
                            'type': '客胜',
                            'odds': lose_odds,
                            'reason': f'{away}胜率{a_win_rate:.0f}%，{home}败率{h_loss_rate:.0f}%'
                        })
                
                # 大球
                if 1.70 <= real_ou_odds <= 2.0 and ou_line >= 2.5:
                    h_big_rate = h_stats.get('big_games', 0) / max(h_stats.get('matches', 1), 1) * 100
                    a_big_rate = a_stats.get('big_games', 0) / max(a_stats.get('matches', 1), 1) * 100
                    if (h_big_rate + a_big_rate) / 2 >= 50 or ou_line >= 3.0:
                        candidates.append({
                            'match': m,
                            'type': '大球',
                            'odds': real_ou_odds,
                            'reason': f'{home}大球率{h_big_rate:.0f}%，{away}大球率{a_big_rate:.0f}%'
                        })
            except:
                continue
        
        if len(candidates) < n:
            return jsonify({'success': False, 'message': f'候选不足，仅找到{len(candidates)}个'}), 404
        
        # 寻找最优组合
        import itertools
        best_combo = None
        min_diff = 999
        
        for combo in itertools.combinations(candidates, n):
            # 检查是否有重复比赛
            match_ids = [c['match']['match_id'] for c in combo]
            if len(match_ids) != len(set(match_ids)):
                continue
            
            total_odds = 1.0
            for c in combo:
                total_odds *= c['odds']
            
            diff = abs(total_odds - target_odds)
            if diff < min_diff:
                min_diff = diff
                best_combo = combo
        
        if not best_combo:
            return jsonify({'success': False, 'message': '未找到合适组合'}), 404
        
        # 构建返回数据
        total_odds = 1.0
        selections = []
        for item in best_combo:
            m = item['match']
            total_odds *= item['odds']
            selections.append({
                'match_id': m['match_id'],
                'league': m.get('league'),
                'match_time': m.get('match_time'),
                'home_team': m.get('home_team'),
                'away_team': m.get('away_team'),
                'type': item['type'],
                'odds': item['odds'],
                'reason': item['reason']
            })
        
        return jsonify({
            'success': True,
            'data': {
                'n': n,
                'target_odds': target_odds,
                'actual_odds': round(total_odds, 2),
                'selections': selections
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'推荐失败: {str(e)}'
        }), 500


@app.route('/api/daily_predictions')
def get_daily_predictions():
    """API - 获取每日比赛预测（胜负+让球+进球）"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        
        # 获取日期参数
        date_str = request.args.get('date')
        if not date_str:
            date_str = datetime.now().strftime('%Y-%m-%d')
        
        # 获取指定日期的未开始比赛
        matches = mongo_storage.get_matches(filters={'owner_date': date_str, 'status': 0})
        
        if not matches:
            return jsonify({'success': False, 'message': f'{date_str} 暂无未开始的比赛'}), 404
        
        # 获取完场比赛统计联赛进球数
        finished = mongo_storage.get_matches(filters={'status': 2})
        league_stats = {}
        for m in finished:
            try:
                home = int(m.get('home_score', 0))
                away = int(m.get('away_score', 0))
                total = home + away
                league = m.get('league', '未知')
                if league not in league_stats:
                    league_stats[league] = {'matches': 0, 'total_goals': 0}
                league_stats[league]['matches'] += 1
                league_stats[league]['total_goals'] += total
            except:
                continue
        
        def safe_float(value):
            try:
                return float(value) if value else None
            except:
                return None
        
        predictions = []
        
        for match in matches:
            league = match.get('league', '')
            home = match.get('home_team', '')
            away = match.get('away_team', '')
            match_time = match.get('match_time', '')
            match_id = match.get('match_id', '')
            
            # 欧赔
            euro_home_init = safe_float(match.get('euro_initial_win'))
            euro_draw_init = safe_float(match.get('euro_initial_draw'))
            euro_away_init = safe_float(match.get('euro_initial_lose'))
            euro_home_cur = safe_float(match.get('euro_current_win'))
            euro_draw_cur = safe_float(match.get('euro_current_draw'))
            euro_away_cur = safe_float(match.get('euro_current_lose'))
            
            # 让球指数
            hi_handicap = safe_float(match.get('hi_handicap_value'))
            hi_home_cur = safe_float(match.get('hi_current_home_odds'))
            hi_draw_cur = safe_float(match.get('hi_current_draw_odds'))
            hi_away_cur = safe_float(match.get('hi_current_away_odds'))
            hi_home_init = safe_float(match.get('hi_initial_home_odds'))
            hi_draw_init = safe_float(match.get('hi_initial_draw_odds'))
            hi_away_init = safe_float(match.get('hi_initial_away_odds'))
            
            # 大小球
            ou_total = safe_float(match.get('ou_current_total') or match.get('ou_initial_total'))
            ou_over = safe_float(match.get('ou_current_over_odds') or match.get('ou_initial_over_odds'))
            ou_under = safe_float(match.get('ou_current_under_odds') or match.get('ou_initial_under_odds'))
            
            # 联赛场均进球
            league_avg = 2.8
            if league in league_stats and league_stats[league]['matches'] >= 10:
                league_avg = league_stats[league]['total_goals'] / league_stats[league]['matches']
            
            pred = {
                'match_id': match_id,
                'league': league,
                'match_time': match_time,
                'home_team': home,
                'away_team': away,
                'hi_handicap': hi_handicap,
                'hi_odds': f"{hi_home_cur or '-'}/{hi_draw_cur or '-'}/{hi_away_cur or '-'}",
                'ou_total': ou_total,
                'result_pred': '',
                'result_conf': 0,
                'result_reasons': [],
                'hi_pred': '',
                'hi_conf': 0,
                'hi_reasons': [],
                'goal_pred': '',
                'goal_conf': 0,
                'goal_reasons': [],
            }
            
            # === 1. 胜负分析 ===
            home_score, draw_score, away_score = 0, 0, 0
            
            if euro_home_cur and euro_draw_cur and euro_away_cur:
                if euro_home_cur < 1.50:
                    home_score += 4
                    pred['result_reasons'].append(f'主胜赔极低{euro_home_cur:.2f}')
                elif euro_home_cur < 1.70:
                    home_score += 3
                    pred['result_reasons'].append(f'主胜赔低{euro_home_cur:.2f}')
                elif euro_home_cur < 2.00:
                    home_score += 2
                elif euro_home_cur > 2.60:
                    away_score += 2
                    pred['result_reasons'].append(f'主胜赔高{euro_home_cur:.2f}')
                
                if euro_away_cur < 1.70:
                    away_score += 4
                    pred['result_reasons'].append(f'客胜赔极低{euro_away_cur:.2f}')
                elif euro_away_cur < 2.00:
                    away_score += 3
                    pred['result_reasons'].append(f'客胜赔低{euro_away_cur:.2f}')
                elif euro_away_cur < 2.30:
                    away_score += 2
                
                if euro_draw_cur < 3.10:
                    draw_score += 2
                    pred['result_reasons'].append(f'平赔低{euro_draw_cur:.2f}')
                elif euro_draw_cur > 3.60:
                    draw_score -= 1
            
            # 赔率变化
            if euro_home_init and euro_home_cur:
                change = euro_home_init - euro_home_cur
                if change > 0.15:
                    home_score += 2
                    pred['result_reasons'].append('主胜赔降')
                elif change < -0.12:
                    home_score -= 1
            
            if euro_away_init and euro_away_cur:
                change = euro_away_init - euro_away_cur
                if change > 0.15:
                    away_score += 2
                    pred['result_reasons'].append('客胜赔降')
            
            # 联赛特点
            high_draw = ['意甲', '德乙', '西甲', '法乙']
            if league in high_draw:
                draw_score += 2
                pred['result_reasons'].append('高平局联赛')
            
            scores = [('主胜', home_score), ('平局', draw_score), ('客胜', away_score)]
            scores.sort(key=lambda x: x[1], reverse=True)
            pred['result_pred'] = scores[0][0]
            diff = scores[0][1] - scores[1][1]
            pred['result_conf'] = min(90, 50 + diff * 8 + scores[0][1] * 3)
            
            # === 2. 让球盘分析 ===
            if hi_home_cur and hi_draw_cur and hi_away_cur:
                let_home, let_draw, let_away = 0, 0, 0
                
                min_odds = min(hi_home_cur, hi_draw_cur, hi_away_cur)
                if hi_home_cur == min_odds:
                    let_home += 3
                if hi_draw_cur == min_odds:
                    let_draw += 3
                if hi_away_cur == min_odds:
                    let_away += 3
                
                if hi_home_cur < 1.80:
                    let_home += 3
                    pred['hi_reasons'].append(f'让胜赔低{hi_home_cur:.2f}')
                elif hi_home_cur < 2.10:
                    let_home += 2
                
                if hi_draw_cur < 3.00:
                    let_draw += 2
                    pred['hi_reasons'].append(f'让平赔低{hi_draw_cur:.2f}')
                elif hi_draw_cur < 3.50:
                    let_draw += 1
                
                if hi_away_cur < 1.80:
                    let_away += 3
                    pred['hi_reasons'].append(f'让负赔低{hi_away_cur:.2f}')
                elif hi_away_cur < 2.20:
                    let_away += 2
                    pred['hi_reasons'].append(f'让负赔较低{hi_away_cur:.2f}')
                
                # 赔率变化
                if hi_home_init and hi_home_cur and hi_home_init - hi_home_cur > 0.15:
                    let_home += 2
                    pred['hi_reasons'].append('让胜赔降')
                if hi_draw_init and hi_draw_cur and hi_draw_init - hi_draw_cur > 0.15:
                    let_draw += 2
                    pred['hi_reasons'].append('让平赔降')
                if hi_away_init and hi_away_cur and hi_away_init - hi_away_cur > 0.15:
                    let_away += 2
                    pred['hi_reasons'].append('让负赔降')
                
                if hi_handicap is not None:
                    abs_hc = abs(hi_handicap)
                    if abs_hc >= 2:
                        pred['hi_reasons'].append(f'深让{abs(int(hi_handicap))}球')
                        let_away += 1
                    elif abs_hc == 1:
                        pred['hi_reasons'].append('让1球')
                    elif abs_hc == 0:
                        pred['hi_reasons'].append('平手盘')
                        let_draw += 1
                
                let_scores = [('让胜', let_home), ('让平', let_draw), ('让负', let_away)]
                let_scores.sort(key=lambda x: x[1], reverse=True)
                pred['hi_pred'] = let_scores[0][0]
                diff = let_scores[0][1] - let_scores[1][1]
                pred['hi_conf'] = min(90, 50 + diff * 8 + let_scores[0][1] * 3)
                
                # 如果最低赔率明显低于其他，提高信心
                odds_list = sorted([hi_home_cur, hi_draw_cur, hi_away_cur])
                if odds_list[1] - odds_list[0] > 0.5:
                    pred['hi_conf'] = min(90, pred['hi_conf'] + 10)
            
            # === 3. 总进球分析 ===
            if ou_total:
                if ou_total <= 2.0:
                    pred['goal_pred'] = '0-1球'
                    pred['goal_conf'] = 85
                    pred['goal_reasons'].append(f'极低盘口{ou_total:.1f}')
                elif ou_total <= 2.25:
                    pred['goal_pred'] = '1-2球'
                    pred['goal_conf'] = 78
                    pred['goal_reasons'].append(f'低盘口{ou_total:.1f}')
                elif ou_total <= 2.5:
                    if league_avg < 2.5:
                        pred['goal_pred'] = '1-2球'
                        pred['goal_conf'] = 72
                        pred['goal_reasons'].append(f'盘口{ou_total:.1f}+低进球联赛')
                    else:
                        pred['goal_pred'] = '2-3球'
                        pred['goal_conf'] = 72
                        pred['goal_reasons'].append(f'盘口{ou_total:.1f}')
                elif ou_total <= 2.75:
                    pred['goal_pred'] = '2-3球'
                    pred['goal_conf'] = 75
                    pred['goal_reasons'].append(f'盘口{ou_total:.1f}')
                elif ou_total <= 3.0:
                    if league_avg >= 3.0:
                        pred['goal_pred'] = '3-4球'
                        pred['goal_conf'] = 75
                        pred['goal_reasons'].append(f'高进球联赛+盘口{ou_total:.1f}')
                    else:
                        pred['goal_pred'] = '2-3球'
                        pred['goal_conf'] = 70
                        pred['goal_reasons'].append(f'盘口{ou_total:.1f}')
                elif ou_total <= 3.5:
                    pred['goal_pred'] = '3-4球'
                    pred['goal_conf'] = 68
                    pred['goal_reasons'].append(f'高盘口{ou_total:.1f}')
                else:
                    pred['goal_pred'] = '4-6球'
                    pred['goal_conf'] = 60
                    pred['goal_reasons'].append(f'极高盘口{ou_total:.1f}')
                
                if ou_over and ou_under:
                    if ou_over < 0.82:
                        if '2-3' in pred['goal_pred']:
                            pred['goal_pred'] = '3-4球'
                        pred['goal_conf'] += 5
                        pred['goal_reasons'].append('大球低水')
                    elif ou_under < 0.82:
                        if '2-3' in pred['goal_pred']:
                            pred['goal_pred'] = '1-2球'
                        elif '3-4' in pred['goal_pred']:
                            pred['goal_pred'] = '2-3球'
                        pred['goal_conf'] += 5
                        pred['goal_reasons'].append('小球低水')
            else:
                if league_avg >= 3.2:
                    pred['goal_pred'] = '3-4球'
                    pred['goal_conf'] = 65
                    pred['goal_reasons'].append(f'高进球联赛{league_avg:.1f}')
                elif league_avg <= 2.4:
                    pred['goal_pred'] = '1-2球'
                    pred['goal_conf'] = 65
                    pred['goal_reasons'].append(f'低进球联赛{league_avg:.1f}')
                else:
                    pred['goal_pred'] = '2-3球'
                    pred['goal_conf'] = 60
                    pred['goal_reasons'].append(f'联赛场均{league_avg:.1f}')
            
            pred['goal_conf'] = min(pred['goal_conf'], 90)
            predictions.append(pred)
        
        # 按时间排序
        predictions.sort(key=lambda x: x['match_time'])
        
        # 生成推荐方案
        top_result = sorted(predictions, key=lambda x: x['result_conf'], reverse=True)[:3]
        top_hi = sorted([p for p in predictions if p['hi_conf'] > 0], key=lambda x: x['hi_conf'], reverse=True)[:3]
        top_goal = sorted(predictions, key=lambda x: x['goal_conf'], reverse=True)[:3]
        
        return jsonify({
            'success': True,
            'date': date_str,
            'count': len(predictions),
            'predictions': predictions,
            'recommendations': {
                'result': top_result,
                'handicap': top_hi,
                'goals': top_goal
            }
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'预测失败: {str(e)}'
        }), 500



@app.route('/api/my_picks')
def get_my_picks():
    """API - 获取未开始比赛的手动标记结果（用于组合下注）"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        
        # 获取设备ID
        device_id = request.args.get('device_id')
        if not device_id:
            return jsonify({'success': False, 'message': '未提供设备标识'}), 400
        
        # 获取未开始的比赛
        upcoming_matches = mongo_storage.get_matches(filters={'status': 0})
        upcoming_ids = set(m.get('match_id') for m in upcoming_matches)
        
        # 获取该用户的所有手动预测
        user_picks = mongo_storage.get_user_picks(device_id, limit=500)
        
        # 筛选：未开始
        manual_picks = []
        for pick in user_picks:
            match_id = pick.get('match_id')
            if match_id not in upcoming_ids:
                continue
            
            # 获取比赛信息
            match = next((m for m in upcoming_matches if m.get('match_id') == match_id), None)
            if not match:
                continue
            
            # 解析手动选项
            options = pick.get('manual_options', [])
            if not options and pick.get('manual_option'):
                options = [pick.get('manual_option')]
            
            label_map = {
                'win': '主胜', 'draw': '平', 'lose': '客胜',
                'h_win': '让胜', 'h_draw': '让平', 'h_lose': '让负'
            }
            options_text = [label_map.get(o, o) for o in options]
            
            # 获取赔率
            euro_win = match.get('euro_initial_win') or match.get('euro_current_win')  # 即时盘
            euro_draw = match.get('euro_initial_draw') or match.get('euro_current_draw')
            euro_lose = match.get('euro_initial_lose') or match.get('euro_current_lose')
            hi_home = match.get('hi_current_home_odds') or match.get('hi_initial_home_odds')
            hi_draw = match.get('hi_current_draw_odds') or match.get('hi_initial_draw_odds')
            hi_away = match.get('hi_current_away_odds') or match.get('hi_initial_away_odds')
            hi_handicap = match.get('hi_handicap_value')
            
            # 根据选项获取对应赔率
            pick_odds = []
            for opt in options:
                if opt == 'win' and euro_win:
                    pick_odds.append(float(euro_win))
                elif opt == 'draw' and euro_draw:
                    pick_odds.append(float(euro_draw))
                elif opt == 'lose' and euro_lose:
                    pick_odds.append(float(euro_lose))
                elif opt == 'h_win' and hi_home:
                    pick_odds.append(float(hi_home))
                elif opt == 'h_draw' and hi_draw:
                    pick_odds.append(float(hi_draw))
                elif opt == 'h_lose' and hi_away:
                    pick_odds.append(float(hi_away))
            
            # 取最低赔率作为主推
            main_odds = min(pick_odds) if pick_odds else None
            
            confidence = pick.get('manual_win_confidence') or pick.get('manual_asian_confidence') or 90
            
            # 构建完整赔率数据供前端计算器使用
            grid_data = {
                'euro': {
                    'win': float(euro_win) if euro_win else 0,
                    'draw': float(euro_draw) if euro_draw else 0,
                    'lose': float(euro_lose) if euro_lose else 0
                },
                'handicap': {
                    'val': hi_handicap or '0',
                    'win': float(hi_home) if hi_home else 0,
                    'draw': float(hi_draw) if hi_draw else 0,
                    'lose': float(hi_away) if hi_away else 0
                }
            }
            
            manual_picks.append({
                'match_id': match_id,
                'match_number': match.get('match_number', ''),
                'league': match.get('league', ''),
                'match_time': match.get('match_time', ''),
                'home_team': match.get('home_team', ''),
                'away_team': match.get('away_team', ''),
                'options': options,
                'options_text': options_text,
                'confidence': confidence,
                'euro_odds': f"{euro_win or '-'}/{euro_draw or '-'}/{euro_lose or '-'}",
                'hi_handicap': hi_handicap,
                'hi_odds': f"{hi_home or '-'}/{hi_draw or '-'}/{hi_away or '-'}",
                'pick_odds': pick_odds,
                'main_odds': main_odds,
                'grid_data': grid_data
            })
        
        # 按时间排序
        manual_picks.sort(key=lambda x: x['match_time'])
        
        return jsonify({
            'success': True,
            'count': len(manual_picks),
            'data': manual_picks
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取失败: {str(e)}'
        }), 500





# --- Account and calculator bet routes ---

@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    user_id = session.get('user_id')
    user = user_storage.get_user(user_id) if user_id else None
    if not user:
        session.pop('user_id', None)
    return jsonify({'success': True, 'authenticated': bool(user), 'user': user})


@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username') or '').strip()
    display_name = str(data.get('display_name') or username).strip()
    password = str(data.get('password') or '')

    if not re.fullmatch(r'[A-Za-z0-9_\u4e00-\u9fff]{2,32}', username):
        return jsonify({'success': False, 'message': '用户名需为2-32位中文、字母、数字或下划线'}), 400
    if len(display_name) < 1 or len(display_name) > 32:
        return jsonify({'success': False, 'message': '昵称长度需为1-32位'}), 400
    if len(password) < 6 or len(password) > 128:
        return jsonify({'success': False, 'message': '密码长度需为6-128位'}), 400

    user = user_storage.create_user(username, display_name, password)
    if not user:
        return jsonify({'success': False, 'message': '用户名已存在'}), 409

    session.clear()
    session.permanent = True
    session['user_id'] = user['id']
    return jsonify({'success': True, 'user': user}), 201


@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '')
    user = user_storage.authenticate(username, password)
    if not user:
        return jsonify({'success': False, 'message': '用户名或密码错误'}), 401

    session.clear()
    session.permanent = True
    session['user_id'] = user['id']
    return jsonify({'success': True, 'user': user})


@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return jsonify({'success': True})


POOL_NAMES = {
    'had': '胜平负',
    'hhad': '让球胜平负',
    'score': '比分',
    'goals': '总进球',
    'hafu': '半全场',
}

TICKET_IMAGE_MAX_BYTES = int(os.getenv('TICKET_IMAGE_MAX_BYTES', '8000000'))
TICKET_IMAGE_MIME_TYPES = {
    'image/png',
    'image/jpeg',
    'image/jpg',
    'image/webp',
}
TICKET_RECOGNITION_PROMPT = (
    '你是足彩竞彩票据结构化解析助手。只返回JSON，不要输出其他文字。'
    '识别票据中的每一场投注，并输出：'
    '{"selected_items":[...], "pass_counts":[...], "multiplier": 数字, "ticket_date":"YYYY-MM-DD", "warnings":[...]}.'
    ' selected_items中每项应为：'
    '{"pool":"had|hhad|score|goals|hafu", "opt":"win|draw|lose 或对应玩法选项", '
    '"label":"投注标签", "odd":赔率, "match":{"match_num":"周X001", "league":"联赛", '
    '"home_team":"主队", "away_team":"客队", "date":"YYYY-MM-DD", "time":"HH:MM", "handicap":让球值}, '
    '"match_num":"周X001"}'
    ' 重要：票面出现“让球胜平负、主队让1球、主队让一球、让平、让胜、让负”等让球玩法时，'
    'pool必须输出hhad，不要输出had；主队让球handicap为负数（主队让1球=-1），'
    '主队受让或客队让球handicap为正数。'
    ' pass_counts支持 1,2,3,4,5,6,7,8 与如“2串1/3串1”中的数字。'
)


def _settle_pending_calculator_bets(user_id=None):
    with settlement_lock:
        pending_bets = user_storage.list_pending_bets(user_id)
        if not pending_bets:
            return {'checked': 0, 'settled': 0}

        dates = {
            str(item.get('date') or '')[:10]
            for bet in pending_bets
            for item in bet.get('selected_items') or []
            if item.get('date')
        }
        result_index = {}
        if mongo_storage and dates:
            try:
                database_matches = mongo_storage.get_matches({
                    'owner_date': {'$in': sorted(dates)},
                    'status': {'$in': [2, 6]},
                })
                merge_database_results(result_index, database_matches)
                merge_rescheduled_void_results(
                    result_index,
                    pending_bets,
                    [
                        match for match in database_matches
                        if int(match.get('status') or 0) == 6
                    ],
                )
            except Exception as exc:
                print("⚠️  获取数据库赛果失败: {}".format(exc))

        settled_count = 0
        for bet in pending_bets:
            settlement = settle_bet(bet, result_index)
            if settlement and user_storage.settle_bet(bet['id'], settlement):
                settled_count += 1
        return {'checked': len(pending_bets), 'settled': settled_count}


def _attach_pending_database_results(records):
    """Expose completed legs while the whole ticket is still pending."""
    pending = [
        record for record in records
        if record.get('status') == 'pending'
    ]
    if not pending or not mongo_storage:
        return records
    dates = {
        str(item.get('date') or '')[:10]
        for record in pending
        for item in record.get('selected_items') or []
        if item.get('date')
    }
    if not dates:
        return records
    matches = mongo_storage.get_matches({
        'owner_date': {'$in': sorted(dates)},
        'status': {'$in': [2, 6]},
    })
    result_index = {}
    merge_database_results(result_index, matches)
    merge_rescheduled_void_results(
        result_index,
        pending,
        [
            match for match in matches
            if int(match.get('status') or 0) == 6
        ],
    )
    for record in pending:
        partial_results = available_bet_results(record, result_index)
        record['partial_results'] = partial_results
        record['result_progress'] = {
            'completed': len(partial_results),
            'total': int(record.get('match_count') or 0),
        }
    return records


def _safe_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_text(value):
    text = str(value or '').strip()
    return text[:128]


def _normalize_pass_count_value(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if '串' in text:
        text = text.split('串')[0]
    digits = re.findall(r'\d+', text)
    if not digits:
        return None
    return _safe_int(digits[0], None)


def _normalize_pool(value):
    pool = str(value or '').strip().lower()
    if not pool:
        return ''
    aliases = {
        '胜平负': 'had',
        '主流赛果': 'had',
        'spf': 'had',
        '胜平负赔率': 'had',
        '让球胜平负': 'hhad',
        '让胜平负': 'hhad',
        '让球': 'hhad',
        '让球胜平负赔率': 'hhad',
        'hhad': 'hhad',
        '比分': 'score',
        '比 分': 'score',
        'crs': 'score',
        '总进球': 'goals',
        '总进球数': 'goals',
        'ttg': 'goals',
        '半全场': 'hafu',
        '半场胜平负': 'hafu',
        '半全场胜平负': 'hafu',
        'hafu': 'hafu',
    }
    if pool in aliases:
        return aliases[pool]
    return pool


def _normalize_option(value, pool):
    option = str(value or '').strip()
    if not option:
        return ''
    clean = option.replace(' ', '').replace('\u3000', '')
    if pool in {'had', 'hhad'}:
        if clean in {'win', '主胜', '胜', '赢', 'W'}:
            return 'win'
        if clean in {'draw', '平', '和', 'D'}:
            return 'draw'
        if clean in {'lose', '客胜', '负', 'L'}:
            return 'lose'
        if pool == 'hhad' and '让' in clean:
            if '平' in clean:
                return 'draw'
            if '负' in clean:
                return 'lose'
            if '胜' in clean:
                return 'win'
        return clean
    if pool == 'goals':
        if clean in {'7+', '7+球', '7', '7球'}:
            return '7+'
        if clean.endswith('球'):
            clean = clean[:-1]
        return clean
    if pool == 'score':
        if clean.endswith('球'):
            clean = clean[:-1]
        if clean and '胜其他' in clean:
            return '胜其他'
        if clean and '平其他' in clean:
            return '平其他'
        if clean and '负其他' in clean:
            return '负其他'
        return clean
    if pool == 'hafu':
        return clean
    return option[:32]


def _ticket_item_text(item):
    values = [
        item.get('pool'),
        item.get('opt'),
        item.get('label'),
        item.get('play'),
        item.get('selection'),
        item.get('content'),
        item.get('remark'),
    ]
    match = item.get('match') if isinstance(item.get('match'), dict) else {}
    values.extend([
        match.get('pool'),
        match.get('label'),
        match.get('handicap_text'),
        match.get('description'),
    ])
    return ''.join(str(value or '') for value in values).replace(' ', '').replace('\u3000', '')


def _ticket_item_implies_hhad(item):
    text = _ticket_item_text(item)
    if not text:
        return False
    hhad_tokens = (
        '让球胜平负',
        '让胜平负',
        '让球',
        '让胜',
        '让平',
        '让负',
        '主队让',
        '主让',
        '客队让',
        '客让',
        '主队受让',
        '客队受让',
        '受让',
    )
    return any(token in text for token in hhad_tokens)


CHINESE_HANDICAP_DIGITS = {
    '零': 0,
    '〇': 0,
    '一': 1,
    '二': 2,
    '两': 2,
    '三': 3,
    '四': 4,
    '五': 5,
}


def _extract_ticket_handicap_number(text):
    compact = str(text or '').replace(' ', '').replace('\u3000', '')
    match = re.search(r'(?:让|受让)([+-]?\d+(?:\.\d+)?)球?', compact)
    if match:
        return _safe_float(match.group(1))
    match = re.search(r'(?:让|受让)([零〇一二两三四五])球?', compact)
    if match:
        return CHINESE_HANDICAP_DIGITS.get(match.group(1))
    return None


def _normalize_ticket_handicap_sign(handicap, item):
    text = _ticket_item_text(item)
    number = _safe_float(handicap)
    if number is None:
        number = _extract_ticket_handicap_number(text)
    if number is None:
        return None
    if abs(number) < 0.0001:
        return 0.0

    absolute = abs(number)
    # hhad 的 handicap 以“主队加几球”为准：主队让球是负数，主队受让是正数。
    if any(token in text for token in ('主队受让', '主受让', '客队让', '客让')):
        return absolute
    if any(token in text for token in ('主队让', '主让', '客队受让', '客受让')):
        return -absolute
    return number


def _parse_ticket_date(value):
    text = _safe_text(value).strip()
    if not text:
        return None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d'):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    match = re.search(r'(\d{1,2})[/-](\d{1,2})', text)
    if match:
        try:
            return datetime(datetime.now().year, int(match.group(1)), int(match.group(2))).date()
        except ValueError:
            return None
    return None


def _candidate_owner_date(candidate):
    return _parse_ticket_date(candidate.get('owner_date') or candidate.get('date'))


def _normalize_match_text(value):
    return re.sub(r'\s+', '', str(value or '').strip().lower())


def _extract_ark_text(response_data):
    if isinstance(response_data.get('output_text'), str) and response_data.get('output_text').strip():
        return response_data['output_text'].strip()
    parts = []
    for item in response_data.get('output') or []:
        if not isinstance(item, dict):
            continue
        for content in item.get('content') or []:
            if isinstance(content, dict):
                value = content.get('text') or content.get('output_text')
                if isinstance(value, str):
                    parts.append(value)
    if parts:
        return '\n'.join(parts).strip()
    choices = response_data.get('choices') or []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get('message') or {}
        if isinstance(message.get('content'), str):
            return message['content'].strip()
    return ''


def _extract_json(payload_text):
    text = str(payload_text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```$', '', text)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        raise FAEError('AI返回不是JSON对象')
    source = text[start:end + 1]
    try:
        data = json.loads(source)
    except json.JSONDecodeError as exc:
        try:
            data = repair_json(source, return_objects=True)
        except Exception as exc2:
            raise FAEError(f'AI输出JSON解析失败: {exc.msg}') from exc2
    if not isinstance(data, dict):
        raise FAEError('AI输出应为JSON对象')
    return data


def _collect_match_candidates(match_num, match_info):
    if not mongo_storage:
        return []
    candidates = []
    if match_num:
        # 优先按场次匹配，通常场次（周X001）最稳定
        candidates.extend(mongo_storage.get_matches({'match_number': match_num}))
    if not candidates:
        candidates.extend(mongo_storage.get_matches())
    if not candidates:
        return []

    date_text = _safe_text(match_info.get('date'))
    target_date = _parse_ticket_date(date_text) or datetime.now().date()
    if date_text:
        by_date = [item for item in candidates if _safe_text(item.get('owner_date'))[:10] == date_text[:10]]
        if by_date:
            candidates = by_date
        else:
            by_window = [
                item for item in candidates
                if _candidate_owner_date(item)
                and abs((_candidate_owner_date(item) - target_date).days) <= 3
            ]
            if by_window:
                candidates = by_window
    else:
        # 周X编号每年会重复；票据缺少日期时，优先匹配最近几天的场次，避免串到历史年份。
        try:
            window_days = max(1, int(os.getenv('TICKET_MATCH_DATE_WINDOW_DAYS', '10')))
        except ValueError:
            window_days = 10
        by_recent_window = [
            item for item in candidates
            if _candidate_owner_date(item)
            and abs((_candidate_owner_date(item) - target_date).days) <= window_days
        ]
        if by_recent_window:
            candidates = by_recent_window
    return candidates


def _build_ticket_match_id(item):
    home_team = _normalize_match_text(item.get('home_team') or item.get('homeTeam') or '')
    away_team = _normalize_match_text(item.get('away_team') or item.get('awayTeam') or '')
    date_text = _safe_text(item.get('date') or '')
    match_num = _normalize_match_text(item.get('match_num') or item.get('num') or '')
    seed = '|'.join(filter(None, [date_text, match_num, home_team, away_team])) or str(uuid.uuid4())
    return hashlib.sha1(seed.encode('utf-8')).hexdigest()[:24]


def _resolve_ticket_match(item, warnings):
    match_num = _safe_text((item.get('match') or {}).get('match_num') or item.get('match_num'))
    match_info = {
        'home_team': (item.get('match') or {}).get('home_team') or item.get('home_team'),
        'away_team': (item.get('match') or {}).get('away_team') or item.get('away_team'),
        'date': (item.get('match') or {}).get('date') or item.get('date'),
    }
    candidates = _collect_match_candidates(match_num, match_info)
    if not candidates:
        return item.get('match') if isinstance(item.get('match'), dict) else None

    home_key = _normalize_match_text(match_info['home_team'])
    away_key = _normalize_match_text(match_info['away_team'])
    exact = []
    if home_key and away_key:
        for candidate in candidates:
            candidate_home = _normalize_match_text(candidate.get('home_team'))
            candidate_away = _normalize_match_text(candidate.get('away_team'))
            if not candidate_home or not candidate_away:
                continue
            if (home_key == candidate_home and away_key == candidate_away):
                exact.append((candidate, 100))
            elif home_key == candidate_away and away_key == candidate_home:
                exact.append((candidate, 60))

    if exact:
        best, score = sorted(exact, key=lambda it: it[1], reverse=True)[0]
        if score == 100:
            return best
        if score < 100:
            warnings.append('检测到主客队可能反向，请核对')
            return best

    scored = []
    for candidate in candidates:
        score = 0
        if match_num and _normalize_match_text(candidate.get('match_number')) == _normalize_match_text(match_num):
            score += 8
        if match_num and _normalize_match_text(candidate.get('round_id')) == _normalize_match_text(match_num):
            score += 8
        if match_info['date'] and _safe_text(candidate.get('owner_date'))[:10] == _safe_text(match_info['date'])[:10]:
            score += 5
        home_candidate = _normalize_match_text(candidate.get('home_team'))
        away_candidate = _normalize_match_text(candidate.get('away_team'))
        if home_key and home_key == home_candidate:
            score += 6
        elif home_key and home_candidate and home_key in home_candidate:
            score += 2
        if away_key and away_key == away_candidate:
            score += 6
        elif away_key and away_candidate and away_key in away_candidate:
            score += 2
        scored.append((candidate, score))

    scored.sort(key=lambda item: item[1], reverse=True)
    if scored:
        best, score = scored[0]
        if score <= 0:
            warnings.append('未能匹配到对应场次，默认保留票据中的场次字段')
            return {
                'match_number': match_num,
                'owner_date': match_info.get('date'),
                'home_team': match_info.get('home_team'),
                'away_team': match_info.get('away_team'),
            }
        if len(scored) > 1 and scored[1][1] == score:
            warnings.append('存在同名场次匹配，已按最高置信度匹配，建议核对')
        return best
    return {
        'match_number': match_num,
        'owner_date': match_info.get('date'),
        'home_team': match_info.get('home_team'),
        'away_team': match_info.get('away_team'),
    }


def _normalize_ticket_payload(data):
    raw_items = data.get('selected_items') or []
    if not isinstance(raw_items, list):
        raise ValueError('识别结果缺少 selected_items')
    if not raw_items:
        raise ValueError('票据中未解析到投注项')

    normalized_items = []
    option_counts = {}
    total_odds = 1.0
    warnings = list(data.get('warnings') or [])
    ticket_date = _safe_text(data.get('ticket_date') or data.get('date') or '')

    for item in raw_items:
        if not isinstance(item, dict):
            warnings.append('存在非结构化投注项，已跳过')
            continue
        item = dict(item)
        initial_match_info = dict(item.get('match')) if isinstance(item.get('match'), dict) else {}
        if ticket_date and not (initial_match_info.get('date') or item.get('date')):
            initial_match_info['date'] = ticket_date
            item['match'] = initial_match_info
        pool = _normalize_pool(item.get('pool'))
        if pool == 'had' and _ticket_item_implies_hhad(item):
            pool = 'hhad'
            warnings.append('检测到让球投注被识别为胜平负，已自动修正为让球胜平负')
        if pool not in POOL_NAMES:
            warnings.append(f'投注项玩法[{item.get("pool")}]不支持，已忽略')
            continue
        option = _normalize_option(item.get('opt') or item.get('label'), pool)
        if not option:
            warnings.append(f'{pool} 找不到可识别投注选项，已忽略')
            continue
        odds = _safe_float(item.get('odd'))
        if odds is None:
            warnings.append(f'{pool}-{option} 未识别赔率，已忽略')
            continue
        if odds <= 0 or odds > 10000:
            warnings.append(f'{pool}-{option} 赔率异常 {odds}，已忽略')
            continue

        match_info = item.get('match') if isinstance(item.get('match'), dict) else {}
        resolved = _resolve_ticket_match(item, warnings)
        if isinstance(resolved, dict):
            date_value = (
                resolved.get('owner_date')
                or resolved.get('date')
                or match_info.get('date')
                or ''
            )
            handicap = resolved.get('handicap')
            if handicap is None:
                handicap = item.get('handicap')
            match_num = resolved.get('match_num') or resolved.get('match_number') or resolved.get('num') or match_info.get('match_num')
            league = resolved.get('league') or match_info.get('league', '')
            home_team = resolved.get('home_team') or resolved.get('homeTeam') or match_info.get('home_team') or item.get('home_team')
            away_team = resolved.get('away_team') or resolved.get('awayTeam') or match_info.get('away_team') or item.get('away_team')
            time_value = resolved.get('time') or match_info.get('time')
            match_id = (
                str(resolved.get('match_id') or '')
                .strip()
                or (
                    str(resolved.get('id') or '').strip()
                    if isinstance(resolved, dict) else ''
                )
            )
        else:
            date_value = item.get('date') or match_info.get('date')
            handicap = item.get('handicap') or match_info.get('handicap')
            match_num = match_info.get('match_num') or item.get('match_num')
            league = match_info.get('league') or ''
            home_team = match_info.get('home_team') or item.get('home_team') or ''
            away_team = match_info.get('away_team') or item.get('away_team') or ''
            time_value = match_info.get('time') or ''
            match_id = ''

        if pool == 'hhad':
            handicap = _normalize_ticket_handicap_sign(handicap, item)

        normalized_match = dict(resolved) if isinstance(resolved, dict) else {}
        if not match_id:
            match_id = _build_ticket_match_id({
                'date': date_value,
                'match_num': match_num,
                'home_team': home_team,
                'away_team': away_team,
            })
            warnings.append(f'场次{match_num or ""}未匹配到数据库比赛ID，已临时生成本地ID')
        match_payload = {
            'match_id': str(match_id)[:64],
            'pool': pool,
            'pool_name': POOL_NAMES[pool],
            'opt': option[:32],
            'label': str(item.get('label') or option)[:32],
            'odd': round(odds, 2),
            'match_num': str(normalized_match.get('match_num') or normalized_match.get('num') or match_num or '')[:32],
            'league': _safe_text(league)[:64],
            'home_team': _safe_text(home_team)[:64],
            'away_team': _safe_text(away_team)[:64],
            'date': _safe_text(date_value)[:16],
            'time': _safe_text(time_value)[:16],
            'handicap': _safe_float(handicap),
            'match': {
                'num': match_num,
                'league': league,
                'homeTeam': home_team,
                'awayTeam': away_team,
                'date': date_value,
                'time': time_value,
                'handicap': handicap,
            },
        }
        normalized_items.append(match_payload)
        option_counts[match_id] = option_counts.get(match_id, 0) + 1
        total_odds *= odds

    if not normalized_items:
        raise ValueError('票据解析后未得到有效投注项')

    try:
        multiplier = _safe_int(data.get('multiplier'), 1) or 1
    except Exception:
        multiplier = 1
    if multiplier < 1 or multiplier > 9999:
        warnings.append('倍投超出范围，已重置为1')
        multiplier = 1

    raw_pass_counts = data.get('pass_counts') or []
    if not isinstance(raw_pass_counts, list):
        raise ValueError('过关方式格式错误')
    normalized_pass_counts = []
    for count in raw_pass_counts:
        normalized = _normalize_pass_count_value(count)
        if normalized is None:
            continue
        normalized_pass_counts.append(normalized)
    normalized_pass_counts = sorted(set(normalized_pass_counts))
    if not normalized_pass_counts:
        raise ValueError('未识别到过关方式')
    match_count = len(option_counts)
    if not match_count:
        raise ValueError('未识别到场次')
    if any(count < 1 or count > match_count or count > 8 for count in normalized_pass_counts):
        warnings.append('识别到的过关方式与场次不匹配，按实际场次数自动修正')
        normalized_pass_counts = [count for count in normalized_pass_counts if 1 <= count <= match_count]
        if not normalized_pass_counts:
            raise ValueError('过关方式与场次不匹配')

    notes = calculate_notes(normalized_items, normalized_pass_counts)
    if notes < 1:
        raise ValueError('无法计算注数，请手动核对票据')
    stake = round(notes * 2 * multiplier, 2)
    max_bonus = calculate_max_bonus(normalized_items, normalized_pass_counts, multiplier)
    return {
        'id': str(uuid.uuid4()),
        'status': 'pending',
        'multiplier': multiplier,
        'pass_counts': normalized_pass_counts,
        'selected_items': normalized_items,
        'match_count': match_count,
        'option_count': len(normalized_items),
        'notes': notes,
        'stake': stake,
        'total_odds': round(total_odds, 2),
        'max_bonus': max_bonus,
        'description': '{}场 · {} · {}倍'.format(
            match_count,
            '，'.join(
                '单关' if count == 1 else '{}串'.format(count)
                for count in normalized_pass_counts
            ),
            multiplier,
        ),
        'created_at': datetime.utcnow().replace(microsecond=0).isoformat() + 'Z',
        'warnings': warnings,
    }


def _call_ticket_vision(file_bytes, mime_type):
    if not fae_ai_review_analyzer.client.configured:
        raise ValueError('ARK 未配置，暂不支持图片识别')
    data_url = 'data:{};base64,{}'.format(
        mime_type or 'image/jpeg',
        base64.b64encode(file_bytes).decode('ascii'),
    )
    payloads = []
    client = fae_ai_review_analyzer.client
    if client.api_mode == 'chat_completions':
        payloads.append({
            'url': f"{client.base_url}/chat/completions",
            'payload': {
                'model': client.model,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': TICKET_RECOGNITION_PROMPT},
                        {'type': 'image_url', 'image_url': {'url': data_url}},
                    ],
                }],
                'temperature': 0.1,
                'response_format': {'type': 'json_object'},
            },
        })
        payloads.append({
            'url': f"{client.base_url}/chat/completions",
            'payload': {
                'model': client.model,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': TICKET_RECOGNITION_PROMPT},
                        {'type': 'image_url', 'image_url': {'url': data_url, 'detail': 'high'}},
                    ],
                }],
                'temperature': 0.1,
            },
        })
    else:
        payloads.append({
            'url': f"{client.base_url}/responses",
            'payload': {
                'model': client.model,
                'input': [{
                    'role': 'user',
                    'content': [
                        {'type': 'input_text', 'text': TICKET_RECOGNITION_PROMPT},
                        {'type': 'input_image', 'image_url': data_url},
                    ],
                }],
                'temperature': 0.1,
            },
        })
        payloads.append({
            'url': f"{client.base_url}/responses",
            'payload': {
                'model': client.model,
                'input': [{
                    'role': 'user',
                    'content': [
                        {'type': 'input_text', 'text': TICKET_RECOGNITION_PROMPT},
                        {'type': 'input_image', 'image_url': {'url': data_url}},
                    ],
                }],
                'temperature': 0.1,
            },
        })

    headers = {
        'Authorization': f'Bearer {client.api_key}',
        'Content-Type': 'application/json',
    }
    last_error = None
    for item in payloads:
        try:
            resp = requests.post(
                item['url'],
                headers=headers,
                json=item['payload'],
                timeout=max(30, int(os.getenv('AI_REQUEST_TIMEOUT', '90'))),
            )
            if resp.status_code >= 400:
                last_error = 'HTTP {}'.format(resp.status_code)
                continue
            text_data = resp.json()
            text = _extract_ark_text(text_data)
            if not text:
                last_error = '模型返回为空'
                continue
            parsed = _extract_json(text)
            if not isinstance(parsed, dict):
                last_error = '模型返回非对象'
                continue
            return parsed
        except Exception as exc:
            last_error = str(exc)
    raise ValueError('票据识别失败：{}'.format(last_error or '模型调用异常'))


def _calculator_bet_payload(data):
    raw_items = data.get('selected_items') or []
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError('请至少选择一个投注项')
    if len(raw_items) > 200:
        raise ValueError('投注选项过多')

    try:
        multiplier = int(data.get('multiplier', 1))
    except (TypeError, ValueError):
        raise ValueError('倍数格式错误')
    if multiplier < 1 or multiplier > 9999:
        raise ValueError('倍数需为1-9999')

    sanitized_items = []
    option_counts = {}
    total_odds = 1.0
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError('投注项格式错误')
        match_id = str(item.get('match_id') or item.get('matchId') or '').strip()
        pool = str(item.get('pool') or '').strip()
        option = str(item.get('opt') or '').strip()
        if not match_id or pool not in POOL_NAMES or not option:
            raise ValueError('投注项缺少必要字段')
        try:
            odds = float(item.get('odd'))
        except (TypeError, ValueError):
            raise ValueError('赔率格式错误')
        if odds <= 0 or odds > 10000:
            raise ValueError('赔率超出有效范围')

        match = item.get('match') if isinstance(item.get('match'), dict) else {}
        sanitized = {
            'match_id': match_id[:64],
            'pool': pool,
            'pool_name': POOL_NAMES[pool],
            'opt': option[:32],
            'label': str(item.get('label') or option)[:32],
            'odd': round(odds, 2),
            'match_num': str(match.get('num') or item.get('match_num') or '')[:32],
            'league': str(match.get('league') or item.get('league') or '')[:64],
            'home_team': str(match.get('homeTeam') or item.get('home_team') or '')[:64],
            'away_team': str(match.get('awayTeam') or item.get('away_team') or '')[:64],
            'date': str(match.get('date') or item.get('date') or '')[:16],
            'time': str(match.get('time') or item.get('time') or '')[:16],
            'handicap': match.get('handicap', item.get('handicap')),
        }
        sanitized_items.append(sanitized)
        option_counts[match_id] = option_counts.get(match_id, 0) + 1
        total_odds *= odds

    match_count = len(option_counts)
    if match_count > 8:
        raise ValueError('最多只能选择8场比赛')

    raw_pass_counts = data.get('pass_counts') or []
    if not isinstance(raw_pass_counts, list):
        raise ValueError('过关方式格式错误')
    try:
        pass_counts = sorted(set(int(count) for count in raw_pass_counts))
    except (TypeError, ValueError):
        raise ValueError('过关方式格式错误')
    if not pass_counts:
        raise ValueError('请选择过关方式')
    if any(count < 1 or count > match_count or count > 8 for count in pass_counts):
        raise ValueError('过关方式与已选比赛场数不匹配')

    notes = calculate_notes(sanitized_items, pass_counts)
    if notes < 1:
        raise ValueError('无法计算投注注数')

    stake = round(notes * 2 * multiplier, 2)
    max_bonus = calculate_max_bonus(
        sanitized_items,
        pass_counts,
        multiplier,
    )
    if pass_counts == [1]:
        pass_text = '单关'
    elif 1 in pass_counts:
        pass_text = '，'.join(
            '单关' if count == 1 else '{}关'.format(count)
            for count in pass_counts
        )
    else:
        pass_text = '{}关'.format('，'.join(str(count) for count in pass_counts))
    description = '{}场 · {} · {}倍'.format(match_count, pass_text, multiplier)
    return {
        'id': str(uuid.uuid4()),
        'status': 'pending',
        'multiplier': multiplier,
        'pass_counts': pass_counts,
        'selected_items': sanitized_items,
        'match_count': match_count,
        'option_count': len(sanitized_items),
        'notes': notes,
        'stake': stake,
        'total_odds': round(total_odds, 2),
        'max_bonus': max_bonus,
        'description': description,
        'created_at': datetime.utcnow().replace(microsecond=0).isoformat() + 'Z',
    }


@app.route('/api/user/bets', methods=['POST'])
@login_required
def create_user_bet():
    try:
        bet = _calculator_bet_payload(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    saved = user_storage.create_bet(session['user_id'], bet)
    return jsonify({'success': True, 'data': saved}), 201


@app.route('/api/user/bets/recognize-ticket', methods=['POST'])
@login_required
def recognize_user_ticket():
    """识别彩票图片，返回可入库的标准化投注单。"""
    if 'file' not in request.files and 'image' not in request.files and 'ticket' not in request.files:
        return jsonify({'success': False, 'message': '未检测到上传文件'}), 400

    file = request.files.get('file') or request.files.get('image') or request.files.get('ticket')
    if not file:
        return jsonify({'success': False, 'message': '未检测到有效文件字段（支持 file/image/ticket）'}), 400

    try:
        mime_type = str(file.mimetype or '').lower()
        file_bytes = file.read()
    except Exception:
        return jsonify({'success': False, 'message': '读取上传文件失败'}), 400

    if not file_bytes:
        return jsonify({'success': False, 'message': '文件为空'}), 400
    if len(file_bytes) > TICKET_IMAGE_MAX_BYTES:
        return jsonify({
            'success': False,
            'message': '图片过大，建议小于 {}MB'.format(TICKET_IMAGE_MAX_BYTES // 1024 // 1024),
        }), 413
    if mime_type and mime_type not in TICKET_IMAGE_MIME_TYPES:
        return jsonify({
            'success': False,
            'message': '不支持的图片格式（支持 png/jpg/webp）',
        }), 415

    try:
        parsed = _call_ticket_vision(file_bytes, mime_type or 'image/jpeg')
        normalized = _normalize_ticket_payload(parsed)
    except Exception as exc:
        return jsonify({'success': False, 'message': f'票据识别失败：{str(exc)}'}), 502

    return jsonify({'success': True, 'data': normalized})


@app.route('/api/user/bets/import-ticket', methods=['POST'])
@login_required
def import_user_ticket_bet():
    """将识别后的票据数据入库为投注记录。"""
    try:
        bet = _normalize_ticket_payload(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except Exception as exc:
        return jsonify({'success': False, 'message': f'入库数据异常：{str(exc)}'}), 400
    saved = user_storage.create_bet(session['user_id'], bet)
    return jsonify({'success': True, 'data': saved}), 201


@app.route('/api/user/bets', methods=['GET'])
@login_required
def list_user_bets():
    try:
        limit = max(1, min(100, int(request.args.get('limit', 50))))
        offset = max(0, int(request.args.get('offset', 0)))
    except ValueError:
        return jsonify({'success': False, 'message': '分页参数错误'}), 400
    status = str(request.args.get('status') or '').strip()
    if status and status not in {'pending', 'won', 'lost', 'draw'}:
        return jsonify({'success': False, 'message': '投注状态参数错误'}), 400
    _settle_pending_calculator_bets(session['user_id'])
    records = user_storage.list_bets(
        session['user_id'],
        limit=limit,
        offset=offset,
        status=status or None,
    )
    _attach_pending_database_results(records)
    return jsonify({'success': True, 'data': records})


@app.route('/api/user/bets/<bet_id>', methods=['DELETE'])
@login_required
def delete_user_bet(bet_id):
    deleted = user_storage.delete_bet(session['user_id'], bet_id)
    if not deleted:
        return jsonify({'success': False, 'message': '记录不存在'}), 404
    return jsonify({'success': True})


@app.route('/api/user/bet-stats', methods=['GET'])
@login_required
def get_user_bet_stats():
    month = str(request.args.get('month') or '').strip()
    if month and not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
        return jsonify({'success': False, 'message': '月份参数格式错误'}), 400
    _settle_pending_calculator_bets(session['user_id'])
    return jsonify({
        'success': True,
        'data': user_storage.get_stats(session['user_id'], month=month or None),
    })


# --- Legacy device-based betting system routes ---

@app.route('/bets')
def betting_list_page():
    """投注记录页面"""
    return render_template('betting_list.html')

@app.route('/api/bets', methods=['POST'])
def place_bet():
    """保存投注"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
            
        data = request.get_json(silent=True) or {}
        device_id = data.get('device_id')
        tickets = data.get('tickets') # List of tickets
        
        if not device_id or not tickets:
            return jsonify({'success': False, 'message': '参数不完整'}), 400
            
        count = 0
        import uuid
        group_id = str(uuid.uuid4())
        
        for t in tickets:
            # t structure: { odds, desc, combo: [...], multiple, stake }
            bet = {
                'bet_id': str(uuid.uuid4()),
                'group_id': group_id,
                'device_id': device_id,
                'type': 'parlay' if len(t['combo']) > 1 else 'single',
                'items': t['combo'], # List of {mid, opt, odds, name, team}
                'desc': t['desc'],
                'odds': t['odds'],
                'stake': t['stake'], # Total amount for this ticket
                'multiple': t['multiple'],
                'status': 'pending', # pending, won, lost
                'actual_return': 0,
                'created_at': datetime.utcnow()
            }
            if mongo_storage.save_bet(bet):
                count += 1
                
        return jsonify({'success': True, 'count': count})
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/bets', methods=['GET'])
def get_bets():
    """获取投注列表并更新状态"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
            
        device_id = request.args.get('device_id')
        if not device_id:
            return jsonify({'success': False, 'message': '未提供设备ID'}), 400
            
        # 1. 获取所有Pending的投注并更新状态
        pending_bets = mongo_storage.get_bets(device_id, status='pending', limit=1000)
        _update_bets_status(pending_bets)
        
        # 2. 获取分组后的列表
        # 使用 get_bet_groups 替代 get_bets
        groups = mongo_storage.get_bet_groups(device_id, limit=50)
        
        # 3. 丰富数据（添加比赛比分和单场结果）
        _enrich_bet_groups(groups)
        
        stats = mongo_storage.get_bet_stats(device_id)
        daily = mongo_storage.get_daily_stats(device_id)
        
        return jsonify({
            'success': True,
            'data': groups,
            'stats': stats,
            'daily': daily
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/bets/<group_id>', methods=['DELETE'])
def delete_bet_group(group_id):
    """删除投注记录"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
            
        device_id = request.args.get('device_id')
        if not device_id:
            return jsonify({'success': False, 'message': '未提供设备ID'}), 400
            
        if mongo_storage.delete_bet_group(device_id, group_id):
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': '删除失败或记录不存在'}), 404
            
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

def _update_bets_status(bets):
    """更新投注状态逻辑"""
    if not bets:
        return
        
    # 批量获取比赛信息缓存
    match_ids = set()
    for b in bets:
        for item in b['items']:
            match_ids.add(item['mid'])
    
    matches_cache = {}
    for mid in match_ids:
        m = mongo_storage.get_match_by_id(mid)
        if m: matches_cache[mid] = m
        
    for bet in bets:
        is_finished = True
        all_win = True
        any_lose = False
        
        # 检查每一场
        for item in bet['items']:
            mid = item['mid']
            opt = item['opt'] # win, draw, lose, h_win, h_draw, h_lose
            match = matches_cache.get(mid)
            
            # 如果比赛不存在或未完场
            if not match or match.get('status') != 2:
                is_finished = False
                break
            
            # 提取盘口（如果有）
            item_handicap = item.get('handicap')
            
            # 判断单场结果
            res = _check_leg_result(match, opt, item_handicap)
            if res == 'lose':
                any_lose = True
            elif res == 'pending':
                is_finished = False
                break
                
        if is_finished:
            new_status = 'lost' if any_lose else 'won'
            actual_return = (bet['stake'] * bet['odds']) if new_status == 'won' else 0
            
            mongo_storage.update_bet(bet['bet_id'], {
                'status': new_status,
                'actual_return': actual_return,
                'settled_at': datetime.utcnow()
            })

def _enrich_bet_groups(groups):
    """
    丰富投注分组数据，添加比赛实时信息和单场结果
    """
    if not groups:
        return

    # 1. 收集所有比赛ID
    match_ids = set()
    for g in groups:
        for b in g.get('bets', []):
            for item in b.get('items', []):
                if 'mid' in item:
                    match_ids.add(item['mid'])
    
    if not match_ids:
        return

    # 2. 批量获取比赛信息
    matches_cache = {}
    for mid in match_ids:
        m = mongo_storage.get_match_by_id(mid)
        if m: matches_cache[mid] = m
    
    # 3. 注入数据
    for g in groups:
        for b in g.get('bets', []):
            for item in b.get('items', []):
                mid = item.get('mid')
                match = matches_cache.get(mid)
                
                if match:
                    # 注入比赛基本信息
                    item['home_team'] = match.get('home_team', '')
                    item['away_team'] = match.get('away_team', '')
                    item['league'] = match.get('league', '')
                    item['match_number'] = match.get('match_number', '')
                    item['match_time'] = match.get('match_time', '')
                    item['score'] = f"{match.get('home_score', '-')}:{match.get('away_score', '-')}"
                    item['status'] = match.get('status')
                    
                    # 注入赔率信息
                    item['euro_odds'] = {
                        'init': f"{match.get('euro_initial_win') or '-'}/{match.get('euro_initial_draw') or '-'}/{match.get('euro_initial_lose') or '-'}",
                        'curr': f"{match.get('euro_current_win') or '-'}/{match.get('euro_current_draw') or '-'}/{match.get('euro_current_lose') or '-'}"
                    }
                    item['asian_odds'] = {
                        'init': f"{match.get('asian_initial_home_odds') or '-'}/{match.get('asian_initial_handicap') or '-'}/{match.get('asian_initial_away_odds') or '-'}",
                        'curr': f"{match.get('asian_current_home_odds') or '-'}/{match.get('asian_current_handicap') or '-'}/{match.get('asian_current_away_odds') or '-'}"
                    }
                    item['hi_odds'] = {
                        'val': match.get('hi_handicap_value') or '-',
                        'init': f"{match.get('hi_initial_home_odds') or '-'}/{match.get('hi_initial_draw_odds') or '-'}/{match.get('hi_initial_away_odds') or '-'}",
                        'curr': f"{match.get('hi_current_home_odds') or '-'}/{match.get('hi_current_draw_odds') or '-'}/{match.get('hi_current_away_odds') or '-'}"
                    }
                    
                    # 计算单场结果
                    res = _check_leg_result(match, item.get('opt'), item.get('handicap'))
                    item['result'] = res  # 'win', 'lose', 'pending'
                else:
                    item['result'] = 'pending'
                    item['score'] = '-:-'

def _check_leg_result(match, opt, item_handicap=None):
    """判断单注输赢"""
    try:
        home = int(match['home_score'])
        away = int(match['away_score'])
    except:
        return 'pending' # 分数无效
        
    # 胜平负
    if opt in ['win', 'draw', 'lose']:
        if home > away: res = 'win'
        elif home == away: res = 'draw'
        else: res = 'lose'
        return 'win' if opt == res else 'lose'
        
    # 让球
    if opt in ['h_win', 'h_draw', 'h_lose']:
        try:
            # 优先使用投注时的盘口，如果没有则使用比赛当前盘口
            if item_handicap is not None:
                handicap = float(item_handicap)
            else:
                val = match.get('hi_handicap_value')
                if val is None or val == '':
                    return 'pending' # 无盘口数据
                handicap = float(val)
                
            diff = (home + handicap) - away
            if diff > 0: res = 'h_win'
            elif diff == 0: res = 'h_draw'
            else: res = 'h_lose'
            
            return 'win' if opt == res else 'lose'
        except Exception as e:
            print(f"计算让球结果出错: {str(e)}")
            return 'pending'
            
    return 'pending'


# ============ 计算器API ============
@app.route('/api/calc/matches', methods=['GET'])
def get_calc_matches():
    """获取计算器比赛数据 - 从体彩官方API获取真实数据"""
    try:
        url = 'https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry'
        params = {'poolCode': 'had,hhad,crs,ttg,hafu'}

        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1',
            'Referer': 'https://www.sporttery.cn/'
        }

        resp = sporttery_calculator_session.get(
            url,
            params=params,
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            app.logger.warning(
                'Sporttery calculator API rejected server request: %s',
                resp.status_code,
            )
            return jsonify({
                'success': False,
                'message': '体彩接口暂时拒绝服务器访问',
                'status': resp.status_code,
            }), 502
        data = resp.json()

        if not data.get('success'):
            return jsonify({
                'success': False,
                'message': data.get('errorMessage', '获取数据失败')
            })

        matches = []
        value = data.get('value', {})
        match_info_list = value.get('matchInfoList', [])

        week_map = {'1': '周日', '2': '周一', '3': '周二', '4': '周三',
                    '5': '周四', '6': '周五', '7': '周六'}

        match_idx = 0
        for group in match_info_list:
            business_date = group.get('businessDate', '')
            sub_match_list = group.get('subMatchList', [])

            for sm in sub_match_list:
                match_id = str(sm.get('matchId', match_idx))
                match_num_str = sm.get('matchNumStr', '')
                match_week = str(sm.get('matchWeek', ''))
                week_name = week_map.get(match_week, '')

                match = {
                    'id': match_id,
                    'num': match_num_str or f'{week_name}{101 + match_idx}',
                    'league': sm.get('leagueAbbName') or sm.get('leagueAllName', sm.get('leagueName', '')),
                    'date': business_date,
                    'dateText': f'{week_name} {business_date}' if week_name else business_date,
                    'time': sm.get('matchTime', ''),
                    'homeTeam': sm.get('homeTeamAbbName') or sm.get('homeTeamAllName', ''),
                    'awayTeam': sm.get('awayTeamAbbName') or sm.get('awayTeamAllName', ''),
                    'handicap': 0,
                    'had': _parse_had_odds(sm.get('had')),
                    'hhad': _parse_hhad_odds(sm.get('hhad')),
                    'score': _parse_crs_odds(sm.get('crs')),
                    'goals': _parse_ttg_odds(sm.get('ttg')),
                    'hafu': _parse_hafu_odds(sm.get('hafu')),
                    'hadSingle': 0,
                    'hhadSingle': 0
                }

                handicap_val = sm.get('hhad', {}).get('goalLineValue', '')
                if handicap_val:
                    try:
                        match['handicap'] = float(handicap_val)
                    except:
                        pass

                # 从 poolList 中提取单关标识
                pool_list = sm.get('poolList', [])
                for p in pool_list:
                    if p.get('poolCode') == 'HAD':
                        match['hadSingle'] = int(p.get('bettingSingle', 0) or 0)
                    elif p.get('poolCode') == 'HHAD':
                        match['hhadSingle'] = int(p.get('bettingSingle', 0) or 0)

                matches.append(match)
                match_idx += 1

        return jsonify({
            'success': True,
            'data': matches
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


def _parse_had_odds(had):
    """解析胜平负赔率（体彩标志：0=无变化，1=上升，-1=下降）。"""
    if not had:
        return {'win': 0, 'draw': 0, 'lose': 0,
                'winFlag': 0, 'drawFlag': 0, 'loseFlag': 0}
    return {
        'win': float(had.get('h', 0)),
        'draw': float(had.get('d', 0)),
        'lose': float(had.get('a', 0)),
        'winFlag': int(had.get('hf', 0) or 0),
        'drawFlag': int(had.get('df', 0) or 0),
        'loseFlag': int(had.get('af', 0) or 0)
    }


def _parse_hhad_odds(hhad):
    """解析让球胜平负赔率（体彩标志：0=无变化，1=上升，-1=下降）。"""
    if not hhad:
        return {'win': 0, 'draw': 0, 'lose': 0,
                'winFlag': 0, 'drawFlag': 0, 'loseFlag': 0,
                'goalLine': ''}
    return {
        'win': float(hhad.get('h', 0)),
        'draw': float(hhad.get('d', 0)),
        'lose': float(hhad.get('a', 0)),
        'winFlag': int(hhad.get('hf', 0) or 0),
        'drawFlag': int(hhad.get('df', 0) or 0),
        'loseFlag': int(hhad.get('af', 0) or 0),
        'goalLine': hhad.get('goalLine', '')
    }


def _parse_crs_odds(crs):
    """解析比分赔率"""
    if not crs:
        return {}
    result = {}
    score_key_map = {
        's01s00': '1:0',
        's02s00': '2:0',
        's02s01': '2:1',
        's03s00': '3:0',
        's03s01': '3:1',
        's03s02': '3:2',
        's04s00': '4:0',
        's04s01': '4:1',
        's04s02': '4:2',
        's05s00': '5:0',
        's05s01': '5:1',
        's05s02': '5:2',
        's00s00': '0:0',
        's01s01': '1:1',
        's02s02': '2:2',
        's03s03': '3:3',
        's00s01': '0:1',
        's00s02': '0:2',
        's01s02': '1:2',
        's00s03': '0:3',
        's01s03': '1:3',
        's02s03': '2:3',
        's00s04': '0:4',
        's01s04': '1:4',
        's02s04': '2:4',
        's00s05': '0:5',
        's01s05': '1:5',
        's02s05': '2:5',
        's1sh': '胜其他',
        's1sd': '平其他',
        's1sa': '负其他',
    }
    for key, display in score_key_map.items():
        if key in crs:
            try:
                val = float(crs[key])
                if val > 0:
                    result[display] = val
            except:
                continue
    return result


def _parse_ttg_odds(ttg):
    """解析总进球赔率"""
    if not ttg:
        return {}
    result = {}
    ttg_key_map = {
        's0': '0',
        's1': '1',
        's2': '2',
        's3': '3',
        's4': '4',
        's5': '5',
        's6': '6',
        's7': '7+',
    }
    for key, display in ttg_key_map.items():
        if key in ttg:
            try:
                val = float(ttg[key])
                if val > 0:
                    result[display] = val
            except:
                continue
    return result


def _parse_hafu_odds(hafu):
    """解析半全场赔率"""
    if not hafu:
        return {}
    result = {}
    hafu_key_map = {
        'hh': '胜胜',
        'hd': '胜平',
        'ha': '胜负',
        'dh': '平胜',
        'dd': '平平',
        'da': '平负',
        'ah': '负胜',
        'ad': '负平',
        'aa': '负负',
    }
    for key, display in hafu_key_map.items():
        if key in hafu:
            try:
                val = float(hafu[key])
                if val > 0:
                    result[display] = val
            except:
                continue
    return result


if __name__ == '__main__':
    # 确保data目录存在

    
    print("=" * 50)
    print("足球数据展示系统已启动")
    print("访问地址: http://127.0.0.1:5002")
    print("=" * 50)
    
    # 直接启动调度器，_start_scheduler内部已经防止重复初始化
    _start_scheduler()
        
    app.run(debug=True, host='0.0.0.0', port=5002)

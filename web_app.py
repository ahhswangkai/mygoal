"""
足球数据展示Web服务
"""
from flask import Flask, render_template, jsonify, request, Response, stream_with_context


from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from crawler import FootballCrawler

from db_storage import MongoDBStorage
from prediction_engine import PredictionEngine
from prediction_review import PredictionReviewer
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except Exception as e:
    print(f"⚠️  APScheduler导入失败: {str(e)}")
    BackgroundScheduler = None
    CronTrigger = None
import os
import requests
from config import WECHAT_WEBHOOK_URL



app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.config['JSONIFY_MIMETYPE'] = "application/json; charset=utf-8"

# 初始化爬虫和存储
crawler = FootballCrawler()


# 初始化MongoDB存储（优先使用MongoDB，如果连接失败则使用文件存储）
try:
    mongo_storage = MongoDBStorage()
    use_mongodb = True
    print("✅ 使用MongoDB数据库")
except Exception as e:
    mongo_storage = None
    use_mongodb = False
    print(f"⚠️  MongoDB连接失败，使用文件存储: {str(e)}")





def load_match_data():
    """加载比赛数据（仅MongoDB）"""
    if mongo_storage:
        return mongo_storage.get_matches()
    return []


@app.route('/')
def index():
    """首页 - 比赛列表"""
    return render_template('index.html')


@app.route('/daily_recommendations')
def daily_recommendations():
    """每日推荐页面"""
    return render_template('daily_recommendations.html')


@app.route('/lower_plate')
def lower_plate():
    """下盘筛选页面"""
    return render_template('lower_plate.html')


@app.route('/odds_filter')
def odds_filter():
    """赔率筛选页面"""
    return render_template('odds_filter.html')


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
    
    # 支持按联赛筛选
    league = request.args.get('league')
    if league:
        matches = [m for m in matches if m.get('league') == league]
    
    # 支持按状态筛选
    status = request.args.get('status')
    if status:
        try:
            status_code = int(status)
            matches = [m for m in matches if m.get('status') == status_code]
        except ValueError:
            pass
    elif (not start_date) and (not date_str or date_str == datetime.now().strftime('%Y-%m-%d')):
        # 默认仅展示未开始比赛（仅在未指定日期或指定为今天时生效）
        # 如果用户明确查看历史日期，则不默认过滤状态
        # 修改：同时展示未开始(0)和改期(6)的比赛
        matches = [m for m in matches if m.get('status') in [0, 6]]
    
    # 排序：未开始的比赛按时间升序（默认或明确传入status=0）
    try:
        def parse_match_time(mt):
            if not mt:
                return datetime.max
            s = str(mt).strip()
            fmts = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M",
                "%m-%d %H:%M"
            ]
            for fmt in fmts:
                try:
                    if fmt == "%m-%d %H:%M":
                        s2 = f"{datetime.now().year}-{s}"
                        return datetime.strptime(s2, "%Y-%m-%d %H:%M")
                    return datetime.strptime(s, fmt)
                except Exception:
                    continue
            return datetime.max
        need_sort = (not status) or (status and str(status).isdigit() and int(status) == 0)
        if need_sort:
            matches.sort(key=lambda m: parse_match_time(m.get('match_time')))
    except Exception:
        pass
    
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
    
    return jsonify({
        'success': True,
        'data': match
    })


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
            matches = crawler.crawl_daily_matches(url)
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
                    if not mid:
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
                            mongo_storage.save_odds(mid, odds)
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
        matches = c.crawl_daily_matches(url)
        
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
                if not mid:
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


@app.route('/stats')
def stats():
    """统计页面"""
    return render_template('stats.html')


@app.route('/my_picks')
def my_picks_page():
    """我的选择页面"""
    return render_template('my_picks.html')


@app.route('/api/predictions')
def get_predictions():
    """API - 获取预测列表"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        
        # 获取筛选参数
        is_reviewed = request.args.get('is_reviewed')
        filters = {}
        if is_reviewed is not None:
            filters['is_reviewed'] = (is_reviewed.lower() == 'true')
        
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

def _crawl_latest():
    try:
        print(f"⏰ 开始定时爬取任务: {datetime.now()}")
        
        # 1. 爬取当天比赛列表 (逻辑与 /api/crawl_stream 保持一致)
        date_str = datetime.now().strftime('%Y-%m-%d')
        url = f"https://live.500.com/?e={date_str}"
        
        # 使用全局 crawler (不带 mongo_storage，需手动保存)
        matches = crawler.crawl_daily_matches(url)
        
        if not matches:
            print("⚠️  定时任务: 未爬取到比赛数据")
            return

        # 2. 保存比赛基本信息
        if mongo_storage:
            count = mongo_storage.save_matches(matches)
            print(f"✅ 定时任务: 已更新 {count} 场比赛基本信息")
        
        # 3. 并发爬取赔率 (逻辑与 /api/crawl_stream 保持一致)
        # 去掉时间限制，只要有未开始或进行中的比赛就爬取
        workers = 8
        odds_count = 0
        
        def fetch(mid):
            return crawler.crawl_match_odds(mid)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for m in matches:
                mid = m.get('match_id')
                status = m.get('status')
                # 仅对未开始(0)或进行中(1)的比赛爬取赔率
                # 相比 crawl_stream，这里保留了状态过滤，避免重复爬取已完场比赛的赔率
                if mid and status in [0, 1]:
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

        # 4. 微信推送逻辑 (保持不变)
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
        def build_msg(m):
            home = m.get('home_team', '')
            away = m.get('away_team', '')
            tm = m.get('match_time', '')
            num = m.get('match_number', '')
            draw_odds = m.get('euro_current_draw') or m.get('euro_initial_draw') or ''
            hi_val = m.get('hi_handicap_value') or ''
            tags = []
            if draw_odds:
                try:
                    v = float(draw_odds)
                    if 2.85 <= v <= 3.5:
                        tags.append('平')
                except Exception:
                    pass
            if hi_val and (hi_val in ['0', '平手'] or ('平' in hi_val)):
                tags.append('让平')
            tag_str = '、'.join(tags) if tags else '提示'
            odds_text = format_all_odds(m)
            return f"{num} {home} vs {away}\n时间: {tm}\n符合: {tag_str}\n{odds_text}"
        def send_wechat(text):
            if not WECHAT_WEBHOOK_URL:
                return
            try:
                payload = {"msgtype":"text","text":{"content":text}}
                headers = {"Content-Type":"application/json"}
                requests.post(WECHAT_WEBHOOK_URL, json=payload, headers=headers, timeout=10)
            except Exception:
                pass
        for m in matches or []:
            if meets_alert(m):
                send_wechat(build_msg(m))
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
                scheduler.start()
                print("✅ 定时任务调度器已启动 (每15分钟刷新)")
        else:
            print("⚠️  无法启动定时任务: APScheduler未安装")
    except Exception as e:
        print(f"❌ 启动定时任务失败: {str(e)}")
        scheduler = None

@app.before_first_request
def _init_jobs():
    _start_scheduler()

def send_wechat_message(text):
    if not WECHAT_WEBHOOK_URL:
        return False
    try:
        payload = {"msgtype": "text", "text": {"content": text}}
        headers = {"Content-Type": "application/json"}
        r = requests.post(WECHAT_WEBHOOK_URL, json=payload, headers=headers, timeout=10)
        return r.status_code == 200
    except Exception:
        return False

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

@app.route('/api/notify_test/<match_id>', methods=['POST'])
def notify_test(match_id):
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        m = mongo_storage.get_match_by_id(match_id)
        if not m:
            return jsonify({'success': False, 'message': '比赛不存在'}), 404
        home = m.get('home_team', '')
        away = m.get('away_team', '')
        num = m.get('match_number', '')
        tm = m.get('match_time', '')
        league = m.get('league', '')
        odds_text = format_all_odds(m)
        text = f"{num} {home} vs {away}\n联赛: {league}\n时间: {tm}\n{odds_text}"
        ok = send_wechat_message(text)
        return jsonify({'success': ok})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


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


@app.route('/api/review/<match_id>')
def review_match(match_id):
    """API - 复盘指定比赛"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        reviewer = PredictionReviewer()
        result = reviewer.review_match(match_id)
        if result:
            return jsonify({'success': True, 'data': result})
        else:
            return jsonify({'success': False, 'message': '复盘失败或比赛未完场'}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f'复盘失败: {str(e)}'}), 500


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


@app.route('/api/lower_plate')
def get_lower_plate():
    """API - 获取下盘筛选结果"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        
        def safe_float(value):
            try:
                return float(value) if value else None
            except:
                return None
        
        def safe_int(value):
            try:
                return int(value) if value else 0
            except:
                return 0
        
        def calc_handicap_result(home_score, away_score, hi_handicap):
            """计算让球盘结果"""
            if hi_handicap < 0:
                adjusted_diff = home_score + hi_handicap - away_score
                upper_pos = 'home'
                lower_pos = 'away'
            else:
                adjusted_diff = away_score + (-hi_handicap) - home_score
                upper_pos = 'away'
                lower_pos = 'home'
            
            if adjusted_diff > 0:
                return ('上盘赢', False, upper_pos, lower_pos)
            elif adjusted_diff < 0:
                return ('下盘赢', True, upper_pos, lower_pos)
            else:
                return ('走盘', False, upper_pos, lower_pos)
        
        # 获取参数
        mode = request.args.get('mode', 'history')  # history=历史, upcoming=未来机会
        league = request.args.get('league', '')
        min_odds = request.args.get('min_odds', '')
        
        try:
            min_odds = float(min_odds) if min_odds else None
        except:
            min_odds = None
        
        if mode == 'upcoming':
            # 未来下盘机会
            upcoming = mongo_storage.get_matches(filters={'status': 0})
            opportunities = []
            
            for m in upcoming:
                hi_handicap = safe_float(m.get('hi_handicap_value'))
                hi_away_odds = safe_float(m.get('hi_current_away_odds'))
                
                if hi_handicap is None or hi_handicap == 0:
                    continue
                
                if league and league not in m.get('league', ''):
                    continue
                
                threshold = min_odds if min_odds else 3.0
                if hi_away_odds and hi_away_odds >= threshold:
                    if hi_handicap < 0:
                        upper_team = m.get('home_team', '')
                        lower_team = m.get('away_team', '')
                        handicap_desc = f"主让{abs(int(hi_handicap))}球"
                    else:
                        upper_team = m.get('away_team', '')
                        lower_team = m.get('home_team', '')
                        handicap_desc = f"客让{int(hi_handicap)}球"
                    
                    opportunities.append({
                        'match_id': m.get('match_id', ''),
                        'match_time': m.get('match_time', ''),
                        'league': m.get('league', ''),
                        'home_team': m.get('home_team', ''),
                        'away_team': m.get('away_team', ''),
                        'hi_handicap': hi_handicap,
                        'handicap_desc': handicap_desc,
                        'upper_team': upper_team,
                        'lower_team': lower_team,
                        'hi_home_odds': safe_float(m.get('hi_current_home_odds')),
                        'hi_draw_odds': safe_float(m.get('hi_current_draw_odds')),
                        'hi_away_odds': hi_away_odds,
                        'euro_home': safe_float(m.get('euro_current_win')),
                        'euro_away': safe_float(m.get('euro_current_lose')),
                        'is_cold': hi_away_odds >= 4.0
                    })
            
            opportunities.sort(key=lambda x: -(x['hi_away_odds'] or 0))
            
            return jsonify({
                'success': True,
                'mode': 'upcoming',
                'count': len(opportunities),
                'data': opportunities[:30]
            })
        
        else:
            # 历史下盘获胜
            finished = mongo_storage.get_matches(filters={'status': 2})
            results = []
            total_with_handicap = 0
            
            for m in finished:
                hi_handicap = safe_float(m.get('hi_handicap_value'))
                
                if hi_handicap is None or hi_handicap == 0:
                    continue
                
                if league and league not in m.get('league', ''):
                    continue
                
                total_with_handicap += 1
                
                home_score = safe_int(m.get('home_score'))
                away_score = safe_int(m.get('away_score'))
                
                result, is_lower_win, upper_pos, lower_pos = calc_handicap_result(
                    home_score, away_score, hi_handicap
                )
                
                if not is_lower_win:
                    continue
                
                hi_away_odds = safe_float(m.get('hi_current_away_odds'))
                
                if min_odds and hi_away_odds and hi_away_odds < min_odds:
                    continue
                
                upper_team = m.get('home_team' if upper_pos == 'home' else 'away_team', '')
                lower_team = m.get('home_team' if lower_pos == 'home' else 'away_team', '')
                
                if hi_handicap < 0:
                    handicap_desc = f"主让{abs(int(hi_handicap))}球"
                else:
                    handicap_desc = f"客让{int(hi_handicap)}球"
                
                results.append({
                    'match_id': m.get('match_id', ''),
                    'match_time': m.get('match_time', ''),
                    'league': m.get('league', ''),
                    'home_team': m.get('home_team', ''),
                    'away_team': m.get('away_team', ''),
                    'score': f"{home_score}-{away_score}",
                    'hi_handicap': hi_handicap,
                    'handicap_desc': handicap_desc,
                    'upper_team': upper_team,
                    'lower_team': lower_team,
                    'hi_home_odds': safe_float(m.get('hi_current_home_odds')),
                    'hi_draw_odds': safe_float(m.get('hi_current_draw_odds')),
                    'hi_away_odds': hi_away_odds,
                    'euro_home': safe_float(m.get('euro_current_win')),
                    'euro_away': safe_float(m.get('euro_current_lose')),
                    'is_cold': hi_away_odds and hi_away_odds >= 3.0
                })
            
            results.sort(key=lambda x: -(x['hi_away_odds'] or 0))
            
            # 计算联赛统计
            from collections import defaultdict
            league_stats = defaultdict(lambda: {'total': 0, 'lower_win': 0})
            
            for m in finished:
                hi_handicap = safe_float(m.get('hi_handicap_value'))
                if hi_handicap is None or hi_handicap == 0:
                    continue
                
                lg = m.get('league', '')
                home_score = safe_int(m.get('home_score'))
                away_score = safe_int(m.get('away_score'))
                
                _, is_lower_win, _, _ = calc_handicap_result(home_score, away_score, hi_handicap)
                
                league_stats[lg]['total'] += 1
                if is_lower_win:
                    league_stats[lg]['lower_win'] += 1
            
            # 转换为列表并排序
            league_list = []
            for lg, stats in league_stats.items():
                if stats['total'] >= 5:
                    rate = stats['lower_win'] / stats['total'] * 100
                    league_list.append({
                        'league': lg,
                        'total': stats['total'],
                        'lower_win': stats['lower_win'],
                        'rate': round(rate, 1)
                    })
            
            league_list.sort(key=lambda x: -x['rate'])
            
            return jsonify({
                'success': True,
                'mode': 'history',
                'total_matches': total_with_handicap,
                'lower_win_count': len(results) if not min_odds else sum(1 for r in results),
                'lower_win_rate': round(len(results) / total_with_handicap * 100, 1) if total_with_handicap else 0,
                'count': len(results),
                'data': results[:50],
                'league_stats': league_list[:15]
            })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'筛选失败: {str(e)}'
        }), 500


@app.route('/api/odds_filter')
def get_odds_filter():
    """API - 赔率筛选（让球盘升水/欧赔降水等）"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        
        def safe_float(value):
            try:
                return float(value) if value else None
            except:
                return None
        
        def safe_int(value):
            try:
                return int(value) if value else 0
            except:
                return 0
        
        # 获取参数
        filter_type = request.args.get('type', 'hi_home_up')  # 筛选类型
        mode = request.args.get('mode', 'all')  # all=全部, upcoming=未开始, finished=完场
        league = request.args.get('league', '')
        
        # 获取比赛数据
        if mode == 'upcoming':
            matches = mongo_storage.get_matches(filters={'status': 0})
        elif mode == 'finished':
            # 获取有比分的比赛
            all_matches = mongo_storage.get_matches()
            matches = [m for m in all_matches if m.get('home_score') is not None and m.get('away_score') is not None]
        else:
            matches = mongo_storage.get_matches()
        
        results = []
        stats = {'total': 0, 'home_win': 0, 'draw': 0, 'away_win': 0, 'hi_win': 0, 'hi_draw': 0, 'hi_lose': 0}
        
        for m in matches:
            # 联赛筛选
            if league and league not in m.get('league', ''):
                continue
            
            # 获取让球盘数据
            hi_init_home = safe_float(m.get('hi_initial_home_odds'))
            hi_curr_home = safe_float(m.get('hi_current_home_odds'))
            hi_init_away = safe_float(m.get('hi_initial_away_odds'))
            hi_curr_away = safe_float(m.get('hi_current_away_odds'))
            hi_handicap = safe_float(m.get('hi_handicap_value'))
            
            # 获取欧赔数据
            euro_init_win = safe_float(m.get('euro_initial_win'))
            euro_curr_win = safe_float(m.get('euro_current_win'))
            euro_init_lose = safe_float(m.get('euro_initial_lose'))
            euro_curr_lose = safe_float(m.get('euro_current_lose'))
            
            # 获取亚盘数据
            asian_init_home = safe_float(m.get('asian_initial_home_odds'))
            asian_curr_home = safe_float(m.get('asian_current_home_odds'))
            
            # 根据筛选类型判断
            match_filter = False
            filter_desc = ''
            
            if filter_type == 'hi_home_up':
                # 让球盘主胜升水
                if hi_init_home and hi_curr_home and hi_curr_home > hi_init_home + 0.02:
                    match_filter = True
                    filter_desc = f"让胜升水 {hi_init_home:.2f}→{hi_curr_home:.2f} (+{hi_curr_home - hi_init_home:.2f})"
            
            elif filter_type == 'hi_home_up_euro_down':
                # 让球盘主胜升水 + 欧赔主胜降水
                if (hi_init_home and hi_curr_home and hi_curr_home > hi_init_home + 0.02 and
                    euro_init_win and euro_curr_win and euro_curr_win < euro_init_win - 0.02):
                    match_filter = True
                    filter_desc = f"让胜升 +{hi_curr_home - hi_init_home:.2f} | 欧主降 {euro_curr_win - euro_init_win:.2f}"
            
            elif filter_type == 'asian_up_euro_down':
                # 亚盘主水升 + 欧赔主胜降
                if (asian_init_home and asian_curr_home and asian_curr_home > asian_init_home + 0.02 and
                    euro_init_win and euro_curr_win and euro_curr_win < euro_init_win - 0.02):
                    match_filter = True
                    filter_desc = f"亚盘升 +{asian_curr_home - asian_init_home:.2f} | 欧主降 {euro_curr_win - euro_init_win:.2f}"
            
            elif filter_type == 'hi_away_down':
                # 让球盘让负降水（利好下盘）
                if hi_init_away and hi_curr_away and hi_curr_away < hi_init_away - 0.02:
                    match_filter = True
                    filter_desc = f"让负降水 {hi_init_away:.2f}→{hi_curr_away:.2f} ({hi_curr_away - hi_init_away:.2f})"
            
            elif filter_type == 'hi_home_up_low':
                # 让球盘主胜升水 + 让胜赔率<5
                if (hi_init_home and hi_curr_home and 
                    hi_curr_home > hi_init_home + 0.02 and 
                    hi_curr_home < 5.0):
                    match_filter = True
                    filter_desc = f"让胜升水 {hi_init_home:.2f}→{hi_curr_home:.2f} (+{hi_curr_home - hi_init_home:.2f})"
            
            if not match_filter:
                continue
            
            # 计算比赛结果（已完场）
            home_score = m.get('home_score')
            away_score = m.get('away_score')
            result = ''
            hi_result = ''
            
            if home_score is not None and away_score is not None:
                try:
                    hs = int(home_score)
                    aws = int(away_score)
                    
                    # 胜平负结果
                    if hs > aws:
                        result = '主胜'
                        stats['home_win'] += 1
                    elif hs == aws:
                        result = '平局'
                        stats['draw'] += 1
                    else:
                        result = '客胜'
                        stats['away_win'] += 1
                    
                    # 让球盘结果
                    if hi_handicap is not None:
                        adjusted_diff = (hs - aws) + hi_handicap
                        if adjusted_diff > 0:
                            hi_result = '让胜'
                            stats['hi_win'] += 1
                        elif adjusted_diff == 0:
                            hi_result = '走水'
                            stats['hi_draw'] += 1
                        else:
                            hi_result = '让负'
                            stats['hi_lose'] += 1
                except:
                    pass
            
            stats['total'] += 1
            
            results.append({
                'match_id': m.get('match_id', ''),
                'match_time': m.get('match_time', ''),
                'league': m.get('league', ''),
                'home_team': m.get('home_team', ''),
                'away_team': m.get('away_team', ''),
                'score': f"{home_score or '-'}:{away_score or '-'}",
                'status': m.get('status', 0),
                'hi_handicap': hi_handicap,
                'hi_init_home': hi_init_home,
                'hi_curr_home': hi_curr_home,
                'hi_init_away': hi_init_away,
                'hi_curr_away': hi_curr_away,
                'euro_init_win': euro_init_win,
                'euro_curr_win': euro_curr_win,
                'euro_init_lose': euro_init_lose,
                'euro_curr_lose': euro_curr_lose,
                'filter_desc': filter_desc,
                'result': result,
                'hi_result': hi_result
            })
        
        # 按时间倒序
        results.sort(key=lambda x: x['match_time'], reverse=True)
        
        # 计算统计
        if stats['total'] > 0:
            stats['home_win_rate'] = round(stats['home_win'] / stats['total'] * 100, 1)
            stats['draw_rate'] = round(stats['draw'] / stats['total'] * 100, 1)
            stats['away_win_rate'] = round(stats['away_win'] / stats['total'] * 100, 1)
            
            hi_total = stats['hi_win'] + stats['hi_draw'] + stats['hi_lose']
            if hi_total > 0:
                stats['hi_win_rate'] = round(stats['hi_win'] / hi_total * 100, 1)
                stats['hi_draw_rate'] = round(stats['hi_draw'] / hi_total * 100, 1)
                stats['hi_lose_rate'] = round(stats['hi_lose'] / hi_total * 100, 1)
        
        # 按联赛统计
        league_stats = {}
        for r in results:
            lg = r['league']
            if lg not in league_stats:
                league_stats[lg] = {'total': 0, 'hi_win': 0, 'hi_draw': 0, 'hi_lose': 0}
            league_stats[lg]['total'] += 1
            if r['hi_result'] == '让胜':
                league_stats[lg]['hi_win'] += 1
            elif r['hi_result'] == '走水':
                league_stats[lg]['hi_draw'] += 1
            elif r['hi_result'] == '让负':
                league_stats[lg]['hi_lose'] += 1
        
        # 转换并排序
        league_list = []
        for lg, st in league_stats.items():
            if st['total'] >= 3:
                hi_total = st['hi_win'] + st['hi_draw'] + st['hi_lose']
                if hi_total > 0:
                    league_list.append({
                        'league': lg,
                        'total': st['total'],
                        'hi_win': st['hi_win'],
                        'hi_draw': st['hi_draw'],
                        'hi_lose': st['hi_lose'],
                        'hi_lose_rate': round(st['hi_lose'] / hi_total * 100, 1)
                    })
        
        league_list.sort(key=lambda x: -x['hi_lose_rate'])
        
        return jsonify({
            'success': True,
            'filter_type': filter_type,
            'mode': mode,
            'count': len(results),
            'stats': stats,
            'league_stats': league_list[:15],
            'data': results[:100]  # 最多返回100条
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'筛选失败: {str(e)}'
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


@app.route('/api/review/summary')
def get_review_summary():
    """API - 获取复盘汇总报告"""
    try:
        if not mongo_storage:
            return jsonify({'success': False, 'message': 'MongoDB不可用'}), 500
        
        days = request.args.get('days', '7')
        try:
            days = max(1, min(30, int(days)))
        except ValueError:
            days = 7
        
        reviewer = PredictionReviewer()
        summary = reviewer.generate_summary_report(days=days)
        
        if summary:
            return jsonify({
                'success': True,
                'data': summary
            })
        else:
            return jsonify({
                'success': False,
                'message': '暂无复盘数据'
            }), 404
            
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取汇总失败: {str(e)}'
        }), 500


# --- Betting System Routes ---

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
                'created_at': datetime.now()
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
            
            # 判断单场结果
            res = _check_leg_result(match, opt)
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
                'settled_at': datetime.now()
            })

def _check_leg_result(match, opt):
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
            handicap = float(match.get('hi_handicap_value', 0))
            diff = (home + handicap) - away
            if diff > 0: res = 'h_win'
            elif diff == 0: res = 'h_draw' # 走水暂按赢处理？或者算1赔率？简单起见算h_draw命中
            else: res = 'h_lose'
            
            # 如果是走水 (diff==0)，且用户选的不是让平
            # 简化处理：如果 diff==0，且用户买 h_draw，算赢。
            # 实际上走水应该是赔率变1。这里为了简化，先严格判断。
            # 如果真实业务，走水应该把单场赔率置为1，重新计算总赔率。
            # 简单实现：只有买中才算赢。
            
            return 'win' if opt == res else 'lose'
        except:
            return 'pending'
            
    return 'pending'


if __name__ == '__main__':
    # 确保data目录存在

    
    print("=" * 50)
    print("足球数据展示系统已启动")
    print("访问地址: http://127.0.0.1:5002")
    print("=" * 50)
    
    # 在主进程(reloader=False)或Reloader子进程中启动调度器
    # 注意：app.run(debug=True)会启动reloader，WERKZEUG_RUN_MAIN在子进程为true
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        _start_scheduler()
        
    app.run(debug=True, host='0.0.0.0', port=5002)

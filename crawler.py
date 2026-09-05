"""
足彩爬虫核心模块
"""
import time
import requests
import re
import chardet
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from retry import retry
from datetime import datetime
from urllib.parse import urlparse
import random
import threading
from utils import setup_logger
from config import REQUEST_HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY, MAX_RETRIES


PREFERRED_ODDS_COMPANY_ID = '6'
OKOOO_PREFERRED_ODDS_COMPANY_ID = '27'
OKOOO_BASE_URL = 'https://m.okooo.com'
OKOOO_LIST_URL = f'{OKOOO_BASE_URL}/jczq/'
OKOOO_MOBILE_USER_AGENT = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
    'AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1'
)
OKOOO_LIST_CACHE_SECONDS = 300
_OKOOO_LIST_CACHE = {'fetched_at': 0.0, 'data': None}
_OKOOO_LIST_CACHE_LOCK = threading.Lock()
_OKOOO_REQUEST_SEMAPHORE = threading.BoundedSemaphore(2)
SPORTTERY_CALCULATOR_URL = (
    'https://webapi.sporttery.cn/gateway/uniform/football/'
    'getMatchCalculatorV1.qry'
)
SPORTTERY_CALCULATOR_CACHE_SECONDS = 60
_SPORTTERY_CALCULATOR_CACHE = {'fetched_at': 0.0, 'data': None}
_SPORTTERY_CALCULATOR_CACHE_LOCK = threading.Lock()


def clean_asian_handicap(value):
    """去掉盘口单元格末尾的升降走势标记，保留真正的盘口名称。"""
    if value is None:
        return ''
    text = re.sub(r'\s+', '', str(value))
    return re.sub(r'(?:[↑↓]|升|降)+$', '', text)


def is_pregame_match(match):
    """Only pre-match fixtures may update the stored closing odds."""
    if not isinstance(match, dict):
        return False
    try:
        return int(match.get('status')) == 0
    except (TypeError, ValueError):
        return False


class FootballCrawler:
    """足球比赛和赔率数据爬虫"""
    
    def __init__(self, mongo_storage=None):
        """
        初始化爬虫
        
        Args:
            mongo_storage: MongoDBStorage实例，用于实时保存
        """
        self.logger = setup_logger()
        self.mongo_storage = mongo_storage
        self.session = requests.Session()
        # 体彩接口必须直连，避免服务器代理触发 WAF 或超时。
        self.sporttery_session = requests.Session()
        self.sporttery_session.trust_env = False
        self.ua = UserAgent()
        self.headers = REQUEST_HEADERS.copy()
        self.host_last_ts = {}
        
    def _get_random_headers(self):
        """获取随机请求头"""
        headers = self.headers.copy()
        headers['User-Agent'] = self.ua.random
        return headers
    
    @retry(tries=MAX_RETRIES, delay=2, backoff=2, logger=None)
    def _make_request(self, url, method='GET', **kwargs):
        """
        发送HTTP请求（带重试机制）
        
        Args:
            url: 目标URL
            method: 请求方法
            **kwargs: 其他请求参数
            
        Returns:
            response: 响应对象
        """
        self.logger.info(f"请求URL: {url}")
        
        try:
            # 主机级节流，避免429
            host = urlparse(url).netloc
            if host:
                last = self.host_last_ts.get(host, 0)
                min_gap = REQUEST_DELAY + random.uniform(0.5, 1.5)
                now = time.time()
                if now - last < min_gap:
                    time.sleep(min_gap - (now - last))
            headers = self._get_random_headers()
            if '500.com' in url:
                headers['Referer'] = 'https://live.500.com/'
                headers['Pragma'] = 'no-cache'
                headers['Cache-Control'] = 'no-cache'
            elif host == 'm.okooo.com':
                # 澳客移动页要求正常的移动端导航上下文；不依赖 Cookie 或验证码。
                headers['User-Agent'] = OKOOO_MOBILE_USER_AGENT
                headers['Referer'] = OKOOO_LIST_URL
                headers['Accept-Language'] = 'zh-CN,zh;q=0.9'
                # requests 在未安装 brotli 扩展时不会自动解压 br 响应。
                headers['Accept-Encoding'] = 'gzip, deflate'
            custom_headers = kwargs.pop('headers', None)
            if custom_headers:
                headers.update(custom_headers)
            if method.upper() == 'GET':
                response = self.session.get(
                    url,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    **kwargs
                )
            else:
                response = self.session.post(
                    url,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    **kwargs
                )
            if response.status_code == 429:
                self.logger.warning(f"触发429限流: {url}，将延迟重试")
                time.sleep(max(REQUEST_DELAY * 5, 10))
                raise requests.HTTPError(f"429 Too Many Requests: {url}")
            response.raise_for_status()
            # 自动检测编码
            response.encoding = response.apparent_encoding
            time.sleep(REQUEST_DELAY)  # 延迟请求，避免被封
            if host:
                self.host_last_ts[host] = time.time()
            return response
        except requests.RequestException as e:
            self.logger.error(f"请求失败: {url}, 错误: {str(e)}")
            raise
    
    def _decode_html(self, response):
        """
        将HTTP响应安全解码为UTF-8字符串，避免乱码
        优先使用页面<meta charset>，其次使用response.encoding，最后使用chardet检测
        """
        raw = response.content
        enc = None
        # 尝试从<meta charset>中获取编码
        try:
            m = re.search(rb"charset=([a-zA-Z0-9\-]+)", raw[:2048])
            if m:
                enc = m.group(1).decode('ascii', 'ignore').lower()
        except Exception:
            pass
        # 回退到response.encoding
        if not enc and response.encoding:
            enc = response.encoding.lower()
        # 最后使用chardet检测
        if not enc:
            try:
                enc = (chardet.detect(raw) or {}).get('encoding') or 'utf-8'
            except Exception:
                enc = 'utf-8'
        # 标准化国标编码
        if enc in ('gb2312', 'gbk'):
            enc = 'gb18030'
        try:
            return raw.decode(enc, errors='ignore')
        except Exception:
            return raw.decode('utf-8', errors='ignore')

    def parse_match_list(self, html_content):
        """
        解析比赛列表页面 - 500彩票网
        
        Args:
            html_content: HTML内容
            
        Returns:
            matches: 比赛列表
        """
        soup = BeautifulSoup(html_content, 'lxml')
        matches = []
        
        try:
            # 找到比赛列表表格
            table = soup.find('table', id='table_match')
            if not table:
                self.logger.warning("未找到比赛列表表格 (id=table_match)")
                return matches
            
            tbody = table.find('tbody')
            if not tbody:
                self.logger.warning("表格中没有tbody")
                return matches
            
            # 遍历所有比赛行
            for tr in tbody.find_all('tr'):
                tds = tr.find_all('td')
                if len(tds) < 8:
                    continue
                
                # 判断是否为完场页面（列数为10）或普通页面（列数更多）
                is_finished_page = len(tds) == 10
                
                if is_finished_page:
                    # 完场页面结构：10列
                    # TD[0]=联赛, TD[1]=轮次, TD[2]=时间, TD[3]=状态, 
                    # TD[4]=主队, TD[5]=比分, TD[6]=客队, TD[7]=半场, TD[8]=分析, TD[9]=操作
                    match_number = ''  # 完场页面没有场次字段
                    home_td = tds[4]
                    score_td = tds[5]
                    away_td = tds[6]
                    status_td = tds[3]
                    league_td = tds[0]
                    round_td = tds[1]
                    time_td = tds[2]
                    half_td = tds[7]
                    match_id = tr.get('fid', '')
                else:
                    # 普通页面结构：14列
                    # TD[0]=场次, TD[1]=联赛, TD[2]=轮次, TD[3]=时间, TD[4]=状态
                    # TD[5]=主队, TD[6]=比分, TD[7]=客队...
                    match_number = tds[0].get_text(strip=True)  # 场次（如：周六001）
                    home_td = tds[5]
                    score_td = tds[6]
                    away_td = tds[7]
                    status_td = tds[4]
                    league_td = tds[1]
                    round_td = tds[2]
                    time_td = tds[3]
                    half_td = tds[8] if len(tds) > 8 else None
                    match_id = tr.get('fid', '')
                
                # 提取主队（包含排名）
                home_link = home_td.find('a')
                home_team = home_link.get_text(strip=True) if home_link else ''
                
                # 提取主队排名：查找 <span class="gray"> 标签中的排名
                home_rank = ''
                home_rank_span = home_td.find('span', class_='gray')
                if home_rank_span:
                    rank_text = home_rank_span.get_text(strip=True)
                    # 移除中括号，如 [07] -> 07
                    rank_text = rank_text.strip('[]')
                    if rank_text.isdigit():
                        home_rank = rank_text
                
                # 提取客队（包含排名）
                away_link = away_td.find('a')
                away_team = away_link.get_text(strip=True) if away_link else ''
                
                # 提取客队排名：查找 <span class="gray"> 标签中的排名
                away_rank = ''
                away_rank_span = away_td.find('span', class_='gray')
                if away_rank_span:
                    rank_text = away_rank_span.get_text(strip=True)
                    # 移除中括号，如 [08] -> 08
                    rank_text = rank_text.strip('[]')
                    if rank_text.isdigit():
                        away_rank = rank_text
                
                # 提取比分和盘口信息
                score_div = score_td.find('div', class_='pk')
                
                # 初始化比分和盘口
                score = ''
                handicap = ''
                home_score = ''
                away_score = ''
                home_half_score = ''
                away_half_score = ''
                half_score = ''
                
                if score_div:
                    score_links = score_div.find_all('a')
                    if len(score_links) >= 3:
                        # 第一个链接：主队比分
                        home_score = score_links[0].get_text(strip=True)
                        # 第二个链接：盘口信息
                        handicap = score_links[1].get_text(strip=True)
                        # 第三个链接：客队比分
                        away_score = score_links[2].get_text(strip=True)
                        
                        # 组合比分（如果是数字）
                        if home_score.isdigit() and away_score.isdigit():
                            score = f"{home_score}-{away_score}"
                        else:
                            # 未开始的比赛，盘口信息在中间链接
                            score = '-'
                    elif len(score_links) == 1:
                        # 只有盘口信息
                        handicap = score_links[0].get_text(strip=True)
                        score = '-'
                else:
                    score_match = re.search(
                        r'(\d+)\s*[-:：]\s*(\d+)',
                        score_td.get_text(' ', strip=True),
                    )
                    if score_match:
                        home_score, away_score = score_match.groups()
                        score = f"{home_score}-{away_score}"

                if half_td:
                    half_match = re.search(
                        r'(\d+)\s*[-:：]\s*(\d+)',
                        half_td.get_text(' ', strip=True),
                    )
                    if half_match:
                        home_half_score, away_half_score = half_match.groups()
                        half_score = f"{home_half_score}-{away_half_score}"
                
                # 标准化状态：0=未开始，1=进行中，2=完场
                raw_status = status_td.get_text(strip=True)
                status_code = 0
                status_classes = status_td.get('class') or []
                
                # 1. 优先根据class判断
                if 'td_living' in status_classes:
                    status_code = 1
                else:
                    # 2. 根据文本内容判断
                    if raw_status:
                        # 完场
                        if '完' in raw_status or '结束' in raw_status:
                            status_code = 2
                        # 进行中：分钟数 (如 34, 34', 90+2, 90+2') 或 特殊状态
                        elif (re.match(r'^\d+\'?$', raw_status) or 
                              re.match(r'^\d+\+\d+\'?$', raw_status) or
                              any(k in raw_status for k in ["中场", "半场", "加时", "点球"])):
                            status_code = 1
                        # 未开始：时间格式 (19:30) 或 明确文本
                        elif ':' in raw_status or raw_status in ['未', '未开', '推迟', '取消']:
                            status_code = 0
                        # 其他情况
                        else:
                            # 兜底：如果是纯数字认为是时间，设为进行中
                            if raw_status.isdigit():
                                status_code = 1
                            else:
                                status_code = 0 # 无法识别的文本，保守设为未开始
                
                # 3. 兜底逻辑：如果有比分且未完场，强制设为进行中
                # if status_code == 0 and home_score.isdigit() and away_score.isdigit():
                #     status_code = 1
                
                match_data = {
                    'match_id': match_id,
                    'match_number': match_number,  # 场次（如：周六001）
                    'round_id': tds[0].get_text(strip=True) if not is_finished_page else '',
                    'league': league_td.get_text(strip=True),
                    'round': round_td.get_text(strip=True),
                    'match_time': time_td.get_text(strip=True),
                    'status': status_code,
                    'status_text': raw_status,  # 保留原始文本
                    'home_team': home_team,
                    'home_rank': home_rank,  # 主队排名
                    'score': score,
                    'away_team': away_team,
                    'away_rank': away_rank,  # 客队排名
                    'home_score': home_score,
                    'away_score': away_score,
                    'home_half_score': home_half_score,
                    'away_half_score': away_half_score,
                    'half_score': half_score,
                    'handicap': handicap,
                }
                
                matches.append(match_data)
                
                # 逐条保存
                if self.mongo_storage:
                    self.mongo_storage.save_match(match_data)
                
            self.logger.info(f"解析到 {len(matches)} 场比赛")
            
        except Exception as e:
            self.logger.error(f"解析比赛列表失败: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            
        return matches
    
    def parse_odds(self, html_content):
        """
        解析赔率数据 - 500彩票网欧赔专门页面
        
        Args:
            html_content: HTML内容
            
        Returns:
            odds_data: 赔率数据字典，包含欧赔和亚盘的即时盘和初盘
        """
        soup = BeautifulSoup(html_content, 'lxml')
        odds_data = {
            'euro_odds': [],  # 欧赔（胜平负）
            'asian_handicap': [],  # 亚盘（让球）
            'over_under': []  # 大小球
        }
        
        try:
            # 查找欧赔数据表格 (id="datatb")
            table = soup.find('table', id='datatb')
            if not table:
                self.logger.warning("未找到欧赔数据表格 (id=datatb)")
                return odds_data
            
            # 优先选择包含"竞*官*"的数据行（与让球指数保持一致）；若没有则选择"t3*5"；最后取第一行
            rows = table.find_all('tr')
            preferred_row = None
                        
            # 第一优先级：竞*官*(中国)
            for r in rows:
                try:
                    txt = r.get_text(strip=True)
                except Exception:
                    txt = ''
                if txt and '竞*官*' in txt and '竞*官*(中国)' in txt:
                    preferred_row = r
                    break
                        
            # 第二优先级：t3*5
            if not preferred_row:
                for r in rows:
                    try:
                        txt = r.get_text(strip=True)
                    except Exception:
                        txt = ''
                    if txt and ('t3*5' in txt or '**t3*5' in txt):
                        preferred_row = r
                        break
                        
            # 第三优先级：第一行
            if not preferred_row:
                preferred_row = table.find('tr', class_='tr1')
                        
            if not preferred_row:
                self.logger.warning("未找到欧赔数据行")
                return odds_data
            
            tds = preferred_row.find_all('td')
            if len(tds) >= 9:
                try:
                    # 列3-5：初盘（主胜、平局、客胜）
                    initial_win = tds[3].get_text(strip=True)
                    initial_draw = tds[4].get_text(strip=True)
                    initial_lose = tds[5].get_text(strip=True)
                    
                    # 列6-8：即时盘（主胜、平局、客胜）
                    current_win = tds[6].get_text(strip=True)
                    current_draw = tds[7].get_text(strip=True)
                    current_lose = tds[8].get_text(strip=True)
                    
                    if current_win and current_draw and current_lose:
                        odds_data['euro_odds'].append({
                            # 即时盘
                            'current_win': current_win,
                            'current_draw': current_draw,
                            'current_lose': current_lose,
                            # 初盘
                            'initial_win': initial_win,
                            'initial_draw': initial_draw,
                            'initial_lose': initial_lose,
                            # 兼容旧字段（即时盘）
                            'win': current_win,
                            'draw': current_draw,
                            'lose': current_lose
                        })
                        # 判断使用的数据源
                        source_info = ''
                        try:
                            row_text = preferred_row.get_text(strip=True)
                            if '竞*官*' in row_text:
                                source_info = '(竞*官*)'
                            elif 't3*5' in row_text.lower():
                                source_info = '(t3*5)'
                        except:
                            pass
                        self.logger.info(f"解析到欧赔{source_info}: 即时盘 {current_win}/{current_draw}/{current_lose}, 初盘 {initial_win}/{initial_draw}/{initial_lose}")
                    
                except (IndexError, ValueError) as e:
                    self.logger.warning(f"解析欧赔数据失败: {str(e)}")
            
        except Exception as e:
            self.logger.error(f"解析欧赔页面失败: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            
        return odds_data
    
    def parse_asian_handicap(self, html_content):
        """
        解析亚盘赔率数据 - 500彩票网亚盘专门页面
        固定优先使用伟*（cid=6）；该公司缺失时回退现有来源。
        
        Args:
            html_content: HTML内容
            
        Returns:
            asian_data: 亚盘数据
        """
        soup = BeautifulSoup(html_content, 'lxml')
        asian_data = []
        
        try:
            # 查找亚盘数据表格 (id="datatb")
            table = soup.find('table', id='datatb')
            if not table:
                self.logger.warning("未找到亚盘数据表格 (id=datatb)")
                return asian_data
            
            # 第一优先级：伟*（cid=6），保证亚盘和大小球来源一致。
            rows = table.find_all('tr')
            preferred_row = next(
                (
                    row for row in rows
                    if str(row.get('id') or '') == PREFERRED_ODDS_COMPANY_ID
                ),
                None,
            )
            used_fallback = preferred_row is None

            # 伟*缺盘时保留原来的回退逻辑，避免整场赔率为空。
            # 回退优先 t3*5，最后取第一条有效公司行。
            if not preferred_row:
                for r in rows:
                    try:
                        txt = r.get_text(strip=True)
                    except Exception:
                        txt = ''
                    if txt and ('t3*5' in txt or '**t3*5' in txt):
                        preferred_row = r
                        break
            if not preferred_row:
                preferred_row = table.find('tr', class_='tr1')
            if not preferred_row:
                self.logger.warning("未找到亚盘数据行")
                return asian_data
            
            tds = preferred_row.find_all('td')
            if len(tds) >= 12:
                try:
                    # 列3-5：即时盘（主队赔率、让球数、客队赔率）
                    current_home = tds[3].get_text(strip=True)
                    current_handicap = clean_asian_handicap(tds[4].get_text(strip=True))
                    current_away = tds[5].get_text(strip=True)
                    
                    # 列9-11：初盘（主队赔率、让球数、客队赔率）
                    initial_home = tds[9].get_text(strip=True)
                    initial_handicap = clean_asian_handicap(tds[10].get_text(strip=True))
                    initial_away = tds[11].get_text(strip=True)
                    
                    # 清理箭头符号
                    current_home = current_home.replace('↑', '').replace('↓', '')
                    current_away = current_away.replace('↑', '').replace('↓', '')
                    initial_home = initial_home.replace('↑', '').replace('↓', '')
                    initial_away = initial_away.replace('↑', '').replace('↓', '')
                    
                    if current_home and current_handicap and current_away:
                        company_id = str(preferred_row.get('id') or '')
                        company_link = preferred_row.select_one('td.tb_plgs a')
                        company_name = (
                            company_link.get('title', '').strip()
                            if company_link else ''
                        )
                        asian_data.append({
                            # 即时盘
                            'current_home_odds': current_home,
                            'current_handicap': current_handicap,
                            'current_away_odds': current_away,
                            # 初盘
                            'initial_home_odds': initial_home,
                            'initial_handicap': initial_handicap,
                            'initial_away_odds': initial_away,
                            # 兼容旧字段（即时盘）
                            'home_odds': current_home,
                            'handicap': current_handicap,
                            'away_odds': current_away,
                            # 数据源
                            'source_company_id': company_id,
                            'source_company_name': company_name,
                            'source_fallback': used_fallback,
                        })
                        source_label = company_name or company_id or '未知'
                        fallback_label = '（回退）' if used_fallback else ''
                        self.logger.info(
                            f"解析到亚盘({source_label}{fallback_label}): "
                            f"即时盘 {current_home}/{current_handicap}/{current_away}, "
                            f"初盘 {initial_home}/{initial_handicap}/{initial_away}"
                        )
                    
                except (IndexError, ValueError) as e:
                    self.logger.warning(f"解析亚盘数据失败: {str(e)}")
            
        except Exception as e:
            self.logger.error(f"解析亚盘页面失败: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            
        return asian_data
    
    def parse_over_under(self, html_content):
        """
        解析大小球赔率数据 - 500彩票网专门页面
        固定优先使用伟*（cid=6）；该公司缺失时回退现有来源。

        Args:
            html_content: HTML内容

        Returns:
            over_under_data: 大小球赔率数据，包含即时盘和初盘
        """
        soup = BeautifulSoup(html_content, 'lxml')
        over_under_data = []

        try:
            # 查找大小球数据表格 (id="datatb")
            table = soup.find('table', id='datatb')
            if not table:
                self.logger.warning("未找到大小球数据表格 (id=datatb)")
                return over_under_data

            # 第一优先级：伟*（cid=6），保证亚盘和大小球来源一致。
            rows = table.find_all('tr')
            preferred_row = next(
                (
                    row for row in rows
                    if str(row.get('id') or '') == PREFERRED_ODDS_COMPANY_ID
                ),
                None,
            )
            used_fallback = preferred_row is None

            # 伟*缺盘时保留原来的回退顺序。
            if not preferred_row:
                for r in rows:
                    try:
                        txt = r.get_text(strip=True)
                    except Exception:
                        txt = ''
                    if txt and '竞*官*' in txt and '竞*官*(中国)' in txt:
                        preferred_row = r
                        break

            # 第二回退来源：t3*5
            if not preferred_row:
                for r in rows:
                    try:
                        txt = r.get_text(strip=True)
                    except Exception:
                        txt = ''
                    if txt and ('t3*5' in txt or '**t3*5' in txt):
                        preferred_row = r
                        break

            # 第三优先级：第一行 tr1
            if not preferred_row:
                preferred_row = table.find('tr', class_='tr1')

            if not preferred_row:
                self.logger.warning("未找到大小球数据行")
                return over_under_data

            tds = preferred_row.find_all('td')
            if len(tds) >= 12:
                try:
                    # 列3-5：即时盘（大球赔率、盘口、小球赔率）
                    current_over = tds[3].get_text(strip=True)
                    current_total = clean_asian_handicap(
                        tds[4].get_text(strip=True)
                    )
                    current_under = tds[5].get_text(strip=True)

                    # 列9-11：初盘（大球赔率、盘口、小球赔率）
                    initial_over = tds[9].get_text(strip=True)
                    initial_total = clean_asian_handicap(
                        tds[10].get_text(strip=True)
                    )
                    initial_under = tds[11].get_text(strip=True)

                    # 清理箭头符号
                    current_over = current_over.replace('↑', '').replace('↓', '')
                    current_under = current_under.replace('↑', '').replace('↓', '')
                    initial_over = initial_over.replace('↑', '').replace('↓', '')
                    initial_under = initial_under.replace('↑', '').replace('↓', '')

                    if current_over and current_total and current_under:
                        company_id = str(preferred_row.get('id') or '')
                        company_link = preferred_row.select_one('td.tb_plgs a')
                        company_name = (
                            company_link.get('title', '').strip()
                            if company_link else ''
                        )
                        over_under_data.append({
                            # 即时盘
                            'current_over_odds': current_over,
                            'current_total': current_total,
                            'current_under_odds': current_under,
                            # 初盘
                            'initial_over_odds': initial_over,
                            'initial_total': initial_total,
                            'initial_under_odds': initial_under,
                            # 兼容旧字段（即时盘）
                            'over_odds': current_over,
                            'total': current_total,
                            'under_odds': current_under,
                            # 数据源
                            'source_company_id': company_id,
                            'source_company_name': company_name,
                            'source_fallback': used_fallback,
                        })
                        source_label = company_name or company_id or '未知'
                        fallback_label = '（回退）' if used_fallback else ''
                        self.logger.info(
                            f"解析到大小球({source_label}{fallback_label}): "
                            f"即时盘 {current_over}/{current_total}/{current_under}, "
                            f"初盘 {initial_over}/{initial_total}/{initial_under}"
                        )
                except (IndexError, ValueError) as e:
                    self.logger.warning(f"解析大小球数据失败: {str(e)}")

        except Exception as e:
            self.logger.error(f"解析大小球页面失败: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())

        return over_under_data

    @staticmethod
    def _normalize_match_number(value):
        """将澳客的“三005”和本系统的“周三005”统一成同一场次号。"""
        text = re.sub(r'\s+', '', str(value or ''))
        matched = re.search(r'(?:周)?([一二三四五六日天])0*(\d{1,3})', text)
        if not matched:
            return text
        day = '日' if matched.group(1) == '天' else matched.group(1)
        return f"周{day}{int(matched.group(2)):03d}"

    @staticmethod
    def _normalize_team_name(value):
        text = str(value or '').lower()
        text = re.sub(r'[\[【（(].*?[\]】）)]', '', text)
        text = re.sub(r'\bfc\b|足球俱乐部', '', text, flags=re.I)
        return re.sub(r'[^0-9a-z\u4e00-\u9fff]', '', text)

    @staticmethod
    def _okooo_water_to_hk(value):
        """澳客移动页使用欧洲式水位；转换成项目现有的香港盘水位。"""
        text = re.sub(r'[↑↓\s]', '', str(value or ''))
        try:
            number = float(text)
        except (TypeError, ValueError):
            return ''
        if number >= 1:
            number -= 1
        return f'{number:.2f}'

    @staticmethod
    def _is_okooo_preferred_company(row):
        onclick = str(row.get('onclick') or '')
        company_id = re.escape(OKOOO_PREFERRED_ODDS_COMPANY_ID)
        if re.search(rf'(?:pid=|详情\W*){company_id}(?:\D|$)', onclick):
            return True
        company = row.find('a')
        name = company.get_text(strip=True) if company else ''
        compact = name.replace('*', '').replace(' ', '')
        return compact.lower().startswith('b') and compact.endswith('365')

    @staticmethod
    def _sporttery_update_time(market):
        market = market if isinstance(market, dict) else {}
        date = str(market.get('updateDate') or '').strip()
        time_text = str(market.get('updateTime') or '').strip()
        if date and time_text:
            return f'{date} {time_text}'
        return date or time_text

    @staticmethod
    def _valid_sporttery_triplet(market):
        market = market if isinstance(market, dict) else {}
        values = [market.get('h'), market.get('d'), market.get('a')]
        try:
            return all(float(value) > 0 for value in values)
        except (TypeError, ValueError):
            return False

    def parse_sporttery_calculator(self, payload):
        """解析体彩计算器 HAD/HHAD，并保留官方场次身份。"""
        root = payload if isinstance(payload, dict) else {}
        if isinstance(root.get('data'), dict):
            root = root['data']
        value = root.get('value') if isinstance(root.get('value'), dict) else root
        groups = value.get('matchInfoList') if isinstance(value, dict) else []
        matches = []
        for group in groups or []:
            business_date = str((group or {}).get('businessDate') or '')[:10]
            for item in (group or {}).get('subMatchList') or []:
                number = self._normalize_match_number(item.get('matchNumStr'))
                if not number:
                    continue
                matches.append({
                    'sporttery_match_id': str(item.get('matchId') or ''),
                    'match_number': number,
                    'owner_date': str(item.get('businessDate') or business_date)[:10],
                    'match_time': str(item.get('matchTime') or '')[:5],
                    'home_team': (
                        item.get('homeTeamAbbName')
                        or item.get('homeTeamAllName')
                        or ''
                    ),
                    'away_team': (
                        item.get('awayTeamAbbName')
                        or item.get('awayTeamAllName')
                        or ''
                    ),
                    'league': (
                        item.get('leagueAbbName')
                        or item.get('leagueAllName')
                        or ''
                    ),
                    'had': item.get('had') if isinstance(item.get('had'), dict) else {},
                    'hhad': item.get('hhad') if isinstance(item.get('hhad'), dict) else {},
                })
        return {'matches': matches}

    def get_sporttery_calculator_list(self, force=False):
        """获取并短时缓存体彩官方胜平负、让球胜平负数据。"""
        now = time.time()
        with _SPORTTERY_CALCULATOR_CACHE_LOCK:
            cached = _SPORTTERY_CALCULATOR_CACHE.get('data')
            if (
                not force and cached
                and now - float(
                    _SPORTTERY_CALCULATOR_CACHE.get('fetched_at') or 0
                ) < SPORTTERY_CALCULATOR_CACHE_SECONDS
            ):
                return cached
            response = self.sporttery_session.get(
                SPORTTERY_CALCULATOR_URL,
                params={'poolCode': 'had,hhad'},
                headers={
                    'User-Agent': OKOOO_MOBILE_USER_AGENT,
                    'Referer': 'https://www.sporttery.cn/',
                },
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get('success'):
                raise ValueError(
                    payload.get('errorMessage') or '体彩计算器返回失败'
                )
            data = self.parse_sporttery_calculator(payload)
            if data.get('matches'):
                _SPORTTERY_CALCULATOR_CACHE['data'] = data
                _SPORTTERY_CALCULATOR_CACHE['fetched_at'] = time.time()
            return data

    def resolve_sporttery_match(self, match):
        """用日期和竞彩场次号映射体彩计算器比赛。"""
        if not isinstance(match, dict):
            return None
        listing = self.get_sporttery_calculator_list()
        target_number = self._normalize_match_number(match.get('match_number'))
        candidates = [
            item for item in listing.get('matches', [])
            if item.get('match_number') == target_number
        ]
        if not candidates:
            return None
        owner_date = str(match.get('owner_date') or '')[:10]
        exact_date = [
            item for item in candidates
            if owner_date and item.get('owner_date') == owner_date
        ]
        if len(exact_date) == 1:
            return exact_date[0]
        if len(candidates) == 1:
            return candidates[0]

        stored_id = str(match.get('sporttery_match_id') or '')
        if stored_id:
            stored = next(
                (
                    item for item in candidates
                    if item.get('sporttery_match_id') == stored_id
                ),
                None,
            )
            if stored:
                return stored
        return None

    @staticmethod
    def _initial_or_current(match, field, current):
        existing = str((match or {}).get(field) or '').strip()
        return existing or str(current or '').strip()

    def crawl_sporttery_odds(self, match):
        """读取官方计算器的胜平负和让球胜平负赔率。"""
        result = {'euro_odds': [], 'handicap_index': {}}
        item = self.resolve_sporttery_match(match)
        if not item:
            self.logger.warning(
                f"体彩计算器未匹配到场次: {match.get('match_number', '')}"
            )
            return result

        had = item.get('had') or {}
        if self._valid_sporttery_triplet(had):
            current_win = str(had.get('h'))
            current_draw = str(had.get('d'))
            current_lose = str(had.get('a'))
            result['euro_odds'] = [{
                'current_win': current_win,
                'current_draw': current_draw,
                'current_lose': current_lose,
                'initial_win': self._initial_or_current(
                    match, 'euro_initial_win', current_win
                ),
                'initial_draw': self._initial_or_current(
                    match, 'euro_initial_draw', current_draw
                ),
                'initial_lose': self._initial_or_current(
                    match, 'euro_initial_lose', current_lose
                ),
                'win': current_win,
                'draw': current_draw,
                'lose': current_lose,
                'win_flag': int(had.get('hf') or 0),
                'draw_flag': int(had.get('df') or 0),
                'lose_flag': int(had.get('af') or 0),
                'updated_at': self._sporttery_update_time(had),
                'source_provider': 'sporttery-calculator',
            }]

        hhad = item.get('hhad') or {}
        if self._valid_sporttery_triplet(hhad):
            current_home = str(hhad.get('h'))
            current_draw = str(hhad.get('d'))
            current_away = str(hhad.get('a'))
            result['handicap_index'] = {
                'handicap_value': (
                    hhad.get('goalLineValue') or hhad.get('goalLine') or ''
                ),
                'current_home_odds': current_home,
                'current_draw_odds': current_draw,
                'current_away_odds': current_away,
                'initial_home_odds': self._initial_or_current(
                    match, 'hi_initial_home_odds', current_home
                ),
                'initial_draw_odds': self._initial_or_current(
                    match, 'hi_initial_draw_odds', current_draw
                ),
                'initial_away_odds': self._initial_or_current(
                    match, 'hi_initial_away_odds', current_away
                ),
                'home_odds': current_home,
                'draw_odds': current_draw,
                'away_odds': current_away,
                'home_flag': int(hhad.get('hf') or 0),
                'draw_flag': int(hhad.get('df') or 0),
                'away_flag': int(hhad.get('af') or 0),
                'updated_at': self._sporttery_update_time(hhad),
                'source_provider': 'sporttery-calculator',
            }
        result['sporttery_match_id'] = item.get('sporttery_match_id', '')
        return result

    def parse_okooo_match_list(self, html_content):
        """解析澳客竞彩列表，建立官方场次号到澳客 MatchID 的映射。"""
        soup = BeautifulSoup(html_content, 'lxml')
        date_match = re.search(
            r"var\s+lotterNo\s*=\s*['\"](\d{4}-\d{2}-\d{2})['\"]",
            html_content or '',
        )
        owner_date = date_match.group(1) if date_match else ''
        matches = []
        for node in soup.select('.jsMatchItem'):
            id_node = node.select_one('[matchid]')
            okooo_match_id = str(id_node.get('matchid') or '') if id_node else ''
            if not okooo_match_id:
                link = node.find('a', href=re.compile(r'MatchID=\d+', re.I))
                id_match = re.search(
                    r'MatchID=(\d+)', str(link.get('href') or '') if link else '', re.I
                )
                okooo_match_id = id_match.group(1) if id_match else ''
            number_node = node.select_one('.xuhao')
            match_number = self._normalize_match_number(
                number_node.get_text(strip=True) if number_node else ''
            )
            if not okooo_match_id or not match_number:
                continue
            home = node.select_one('.ctrl_homename')
            away = node.select_one('.ctrl_awayname')
            time_node = node.select_one('time.timetxt')
            league = node.select_one('.liansai')
            matches.append({
                'okooo_match_id': okooo_match_id,
                'match_number': match_number,
                'owner_date': owner_date,
                'match_time': time_node.get_text(strip=True) if time_node else '',
                'home_team': home.get_text(strip=True) if home else '',
                'away_team': away.get_text(strip=True) if away else '',
                'league': league.get_text(strip=True) if league else '',
            })
        return {'owner_date': owner_date, 'matches': matches}

    def _fetch_okooo_html(self, url, referer=OKOOO_LIST_URL):
        # 限制并发，降低公开移动页触发限流的概率。
        with _OKOOO_REQUEST_SEMAPHORE:
            response = self._make_request(url, headers={'Referer': referer})
        return self._decode_html(response)

    def get_okooo_match_list(self, force=False):
        """读取并短时缓存澳客当天竞彩列表，避免每场重复请求。"""
        now = time.time()
        with _OKOOO_LIST_CACHE_LOCK:
            cached = _OKOOO_LIST_CACHE.get('data')
            if (
                not force and cached
                and now - float(_OKOOO_LIST_CACHE.get('fetched_at') or 0)
                < OKOOO_LIST_CACHE_SECONDS
            ):
                return cached
            html = self._fetch_okooo_html(OKOOO_LIST_URL)
            data = self.parse_okooo_match_list(html)
            if data.get('matches'):
                _OKOOO_LIST_CACHE['data'] = data
                _OKOOO_LIST_CACHE['fetched_at'] = time.time()
            return data

    def resolve_okooo_match_id(self, match):
        """通过日期、竞彩场次号与球队校验映射澳客 MatchID。"""
        if not isinstance(match, dict):
            return ''
        stored_id = str(match.get('okooo_match_id') or '')
        if stored_id.isdigit():
            return stored_id
        listing = self.get_okooo_match_list()
        list_date = str(listing.get('owner_date') or '')[:10]
        owner_date = str(match.get('owner_date') or '')[:10]

        target_number = self._normalize_match_number(match.get('match_number'))
        candidates = [
            item for item in listing.get('matches', [])
            if item.get('match_number') == target_number
        ]
        if not candidates:
            return ''
        owner_weekday = ''
        if owner_date:
            try:
                owner_weekday = '一二三四五六日'[
                    datetime.strptime(owner_date, '%Y-%m-%d').weekday()
                ]
            except (TypeError, ValueError):
                owner_weekday = ''
        number_weekday_match = bool(
            owner_weekday
            and target_number.startswith(f'周{owner_weekday}')
        )
        # 澳客的竞彩列表会跨自然日展示下一比赛日，页面 lotterNo 仍可能是
        # 前一天。日期一致时唯一场次号即可确认；日期不一致时继续使用
        # 比赛日期的星期、球队或开赛时间校验，避免把有效的跨日比赛过滤掉。
        if len(candidates) == 1 and (
            (list_date and owner_date and list_date == owner_date)
            or number_weekday_match
        ):
            return candidates[0].get('okooo_match_id', '')

        target_home = self._normalize_team_name(match.get('home_team'))
        target_away = self._normalize_team_name(match.get('away_team'))
        time_matches = re.findall(
            r'(?<!\d)([01]\d|2[0-3]):[0-5]\d',
            str(match.get('match_time') or ''),
        )
        target_time = time_matches[-1] if time_matches else ''
        for item in candidates:
            item_home = self._normalize_team_name(item.get('home_team'))
            item_away = self._normalize_team_name(item.get('away_team'))
            teams_match = (
                target_home and target_away
                and (target_home in item_home or item_home in target_home)
                and (target_away in item_away or item_away in target_away)
            )
            time_match = target_time and target_time == item.get('match_time')
            if teams_match or time_match:
                return item.get('okooo_match_id', '')
        return ''

    def parse_okooo_asian_handicap(self, html_content):
        """解析澳客亚盘；优先B365，缺失时取首个有效公司。"""
        soup = BeautifulSoup(html_content, 'lxml')
        table = soup.select_one('#pankou table')
        if not table:
            return []

        def parse_cell(cell):
            home = cell.find('span')
            line = cell.find('em')
            spans = cell.find_all('span')
            away = spans[-1] if spans else None
            return (
                self._okooo_water_to_hk(home.get_text(strip=True) if home else ''),
                clean_asian_handicap(line.get_text(strip=True) if line else ''),
                self._okooo_water_to_hk(away.get_text(strip=True) if away else ''),
            )

        rows = table.select('tbody tr')
        ordered_rows = sorted(
            rows,
            key=lambda item: 0 if self._is_okooo_preferred_company(item) else 1,
        )
        for row in ordered_rows:
            cells = row.find_all('td', recursive=False)
            if len(cells) < 3:
                continue
            initial_home, initial_line, initial_away = parse_cell(cells[1])
            current_home, current_line, current_away = parse_cell(cells[2])
            if not (current_home and current_line and current_away):
                continue
            preferred = self._is_okooo_preferred_company(row)
            company = row.find('a')
            company_name = company.get_text(strip=True) if company else '首个有效公司'
            return [{
                'current_home_odds': current_home,
                'current_handicap': current_line,
                'current_away_odds': current_away,
                'initial_home_odds': initial_home,
                'initial_handicap': initial_line,
                'initial_away_odds': initial_away,
                'home_odds': current_home,
                'handicap': current_line,
                'away_odds': current_away,
                'source_company_id': (
                    OKOOO_PREFERRED_ODDS_COMPANY_ID if preferred else ''
                ),
                'source_company_name': company_name,
                'source_provider': 'okooo',
                'source_fallback': True,
            }]
        return []

    def parse_okooo_over_under(self, html_content):
        """解析澳客大小球；优先B365，缺失时取首个有效公司。"""
        soup = BeautifulSoup(html_content, 'lxml')

        def parse_cell(cell, prefix):
            over = cell.select_one(f'.sort-{prefix}-daqiu')
            total = cell.select_one(f'.filter-{prefix}')
            under = cell.select_one(f'.sort-{prefix}-xiaoqiu')
            return (
                self._okooo_water_to_hk(over.get_text(strip=True) if over else ''),
                clean_asian_handicap(total.get_text(strip=True) if total else ''),
                self._okooo_water_to_hk(under.get_text(strip=True) if under else ''),
            )

        rows = soup.select('tr')
        ordered_rows = sorted(
            rows,
            key=lambda item: 0 if self._is_okooo_preferred_company(item) else 1,
        )
        for row in ordered_rows:
            cells = row.find_all('td', recursive=False)
            if len(cells) < 3:
                continue
            initial_over, initial_total, initial_under = parse_cell(cells[1], 'chu')
            current_over, current_total, current_under = parse_cell(cells[2], 'xin')
            if not (current_over and current_total and current_under):
                continue
            preferred = self._is_okooo_preferred_company(row)
            company = row.find('a')
            company_name = company.get_text(strip=True) if company else '首个有效公司'
            return [{
                'current_over_odds': current_over,
                'current_total': current_total,
                'current_under_odds': current_under,
                'initial_over_odds': initial_over,
                'initial_total': initial_total,
                'initial_under_odds': initial_under,
                'over_odds': current_over,
                'total': current_total,
                'under_odds': current_under,
                'source_company_id': (
                    OKOOO_PREFERRED_ODDS_COMPANY_ID if preferred else ''
                ),
                'source_company_name': company_name,
                'source_provider': 'okooo',
                'source_fallback': True,
            }]
        return []

    def crawl_okooo_odds(self, match, need_asian=True, need_over_under=True):
        """从澳客补齐缺失的亚盘/大小球，不抓取或覆盖其他市场。"""
        result = {'asian_handicap': [], 'over_under': []}
        okooo_match_id = self.resolve_okooo_match_id(match)
        if not okooo_match_id:
            self.logger.warning(
                f"澳客未匹配到场次: {match.get('match_number', '')} "
                f"{match.get('home_team', '')} vs {match.get('away_team', '')}"
            )
            return result
        detail_url = (
            f'{OKOOO_BASE_URL}/match/handicap.php?MatchID={okooo_match_id}'
            '&from=%2Fjczq%2F'
        )
        if need_asian:
            detail_html = self._fetch_okooo_html(detail_url)
            result['asian_handicap'] = self.parse_okooo_asian_handicap(detail_html)
        if need_over_under:
            total_url = (
                f'{OKOOO_BASE_URL}/match/ah.php?type=daxiao&matchId={okooo_match_id}'
            )
            total_html = self._fetch_okooo_html(total_url, referer=detail_url)
            result['over_under'] = self.parse_okooo_over_under(total_html)
        result['okooo_match_id'] = okooo_match_id
        return result

    @staticmethod
    def _normalize_okooo_match_date(value):
        """将澳客的 ``26-09-05`` 日期补成项目统一的四位年份。"""
        text = re.sub(r'\s+', '', str(value or ''))
        if re.fullmatch(r'\d{2}-\d{2}-\d{2}', text):
            return f'20{text}'
        return text

    @staticmethod
    def _okooo_page_teams(soup):
        names = [
            node.get_text(' ', strip=True)
            for node in soup.select('.match-nav-content a[href*="/team/"]')
            if node.get_text(' ', strip=True)
        ]
        return names[:2]

    def _parse_okooo_fixture_rows(self, table, team_aliases=None):
        """解析澳客战绩/交锋/未来赛程共用的比赛行。"""
        aliases = team_aliases or {}
        rows = []
        if not table:
            return rows
        for tr in table.select('tbody tr'):
            cells = tr.find_all('td', recursive=False)
            if len(cells) < 4:
                continue
            league_node = cells[0].find('p')
            date_node = cells[0].select_one('.gray9')
            home_node = cells[1].select_one('.team-name-l b, b')
            away_node = cells[3].select_one('.team-name-r b, b')
            score_node = cells[2].select_one('a') or cells[2]
            home = home_node.get_text(' ', strip=True) if home_node else ''
            away = away_node.get_text(' ', strip=True) if away_node else ''
            score = re.sub(r'\s+', '', score_node.get_text(' ', strip=True))
            row = {
                'match_id': str(tr.get('data-matchid') or ''),
                'league': league_node.get_text(' ', strip=True)
                if league_node else '',
                'date': self._normalize_okooo_match_date(
                    date_node.get_text(' ', strip=True) if date_node else ''
                ),
                'home_team': aliases.get(home, home),
                'away_team': aliases.get(away, away),
                'score': score if re.fullmatch(r'\d+\s*[-:]\s*\d+', score) else '',
            }
            panlu = tr.select_one('td.panlu')
            if panlu:
                row['handicap_result'] = panlu.get_text(' ', strip=True)
            interval = tr.select_one('td.date')
            if interval:
                row['interval'] = interval.get_text(' ', strip=True)
            rows.append(row)
        return rows

    def parse_okooo_fundamentals_history(
        self, html_content, home_team='', away_team=''
    ):
        """解析澳客分析页中的近期战绩、交锋记录和未来赛程。"""
        soup = BeautifulSoup(html_content or '', 'lxml')
        page_teams = self._okooo_page_teams(soup)
        page_home = page_teams[0] if len(page_teams) > 0 else ''
        page_away = page_teams[1] if len(page_teams) > 1 else ''
        aliases = {}
        if page_home and home_team:
            aliases[page_home] = str(home_team)
        if page_away and away_team:
            aliases[page_away] = str(away_team)

        recent = {'home': [], 'away': []}
        recent_summary = {'home': '', 'away': ''}
        history = []
        history_summary = ''
        for section in soup.select('section.jsMatchTableBox'):
            side = str(section.get('type') or '').lower()
            table = section.select_one('table.matchtable')
            summary_node = section.select_one('.titlebox .text')
            summary = summary_node.get_text(' ', strip=True) if summary_node else ''
            if side in ('home', 'away'):
                recent[side] = self._parse_okooo_fixture_rows(table, aliases)
                recent_summary[side] = summary
            elif side == 'vs':
                history = self._parse_okooo_fixture_rows(table, aliases)
                history_summary = summary

        future = {'home': [], 'away': []}
        future_sections = []
        for section in soup.select('section.matchtabbox'):
            title = section.select_one('.titlebox')
            if title and '未来三场比赛' in title.get_text(' ', strip=True):
                future_sections.append(section)
        for side, section in zip(('home', 'away'), future_sections[:2]):
            future[side] = self._parse_okooo_fixture_rows(
                section.select_one('table.matchtable'), aliases
            )

        return {
            'teams': [
                str(home_team or page_home),
                str(away_team or page_away),
            ],
            'recent': recent,
            'recent_summary': recent_summary,
            'history': history,
            'history_summary': history_summary,
            'future': future,
        }

    def parse_okooo_fundamentals_standings(
        self, html_content, home_team='', away_team=''
    ):
        """解析澳客积分页，输出与现有 FAE 兼容的排名结构。"""
        soup = BeautifulSoup(html_content or '', 'lxml')
        page_teams = self._okooo_page_teams(soup)
        page_home = page_teams[0] if len(page_teams) > 0 else ''
        page_away = page_teams[1] if len(page_teams) > 1 else ''
        aliases = {}
        if page_home and home_team:
            aliases[page_home] = str(home_team)
        if page_away and away_team:
            aliases[page_away] = str(away_team)

        standings = []
        table = soup.select_one('.sai-table table.table')
        for tr in table.select('tbody tr') if table else []:
            values = [cell.get_text(' ', strip=True) for cell in tr.select('td')]
            if len(values) < 9:
                continue
            source_team = values[1]
            team = aliases.get(source_team, source_team)
            goal_difference = ''
            try:
                goal_difference = str(int(values[6]) - int(values[7]))
            except (TypeError, ValueError):
                pass
            standings.append({
                'rank': values[0],
                'team': team,
                'matches': values[2],
                'wins': values[3],
                'draws': values[4],
                'losses': values[5],
                'goals_for': values[6],
                'goals_against': values[7],
                'goal_difference': goal_difference,
                'points': values[8],
                'current_match_team': source_team in (page_home, page_away),
            })

        by_team = {str(item.get('team') or ''): item for item in standings}
        team_rankings = {}
        for side, team in (
            ('home', str(home_team or page_home)),
            ('away', str(away_team or page_away)),
        ):
            standing = by_team.get(team) or {}
            if not standing:
                continue
            try:
                matches = int(standing.get('matches') or 0)
                wins = int(standing.get('wins') or 0)
                win_rate = f'{wins / matches * 100:.1f}%' if matches else ''
            except (TypeError, ValueError):
                win_rate = ''
            team_rankings[side] = {
                'team': team,
                'league_rank': standing.get('rank'),
                'records': [{
                    'scope': '总成绩',
                    **{
                        key: standing.get(key)
                        for key in (
                            'matches', 'wins', 'draws', 'losses',
                            'goals_for', 'goals_against',
                            'goal_difference', 'points', 'rank',
                        )
                    },
                    'win_rate': win_rate,
                }],
            }
        return {'standings': standings, 'team_rankings': team_rankings}

    def crawl_okooo_fundamentals(self, match):
        """抓取澳客公开基本面：近期、交锋、积分和未来赛程。"""
        match = match or {}
        okooo_match_id = self.resolve_okooo_match_id(match)
        if not okooo_match_id:
            raise ValueError('澳客未匹配到比赛 ID')
        history_url = (
            f'{OKOOO_BASE_URL}/match/history.php?MatchID={okooo_match_id}'
        )
        game_url = f'{OKOOO_BASE_URL}/match/game.php?MatchID={okooo_match_id}'
        history_html = self._fetch_okooo_html(history_url)
        history_data = self.parse_okooo_fundamentals_history(
            history_html,
            home_team=match.get('home_team') or '',
            away_team=match.get('away_team') or '',
        )
        game_html = self._fetch_okooo_html(game_url, referer=history_url)
        standings_data = self.parse_okooo_fundamentals_standings(
            game_html,
            home_team=match.get('home_team') or '',
            away_team=match.get('away_team') or '',
        )
        has_public_data = any((
            history_data.get('recent', {}).get('home'),
            history_data.get('recent', {}).get('away'),
            history_data.get('history'),
            standings_data.get('standings'),
        ))
        if not has_public_data:
            raise ValueError('澳客基本面页面未返回可用数据')
        return {
            'source': '澳客',
            'source_url': history_url,
            'match_id': str(match.get('match_id') or ''),
            'okooo_match_id': str(okooo_match_id),
            **history_data,
            **standings_data,
            # 澳客公开战绩页不稳定提供伤停和确认阵容，保持缺失，禁止推断。
            'lineups': {},
            'injuries': {},
        }
    
    def _parse_chinese_handicap(self, text):
        """解析中文亚盘盘口为数字"""
        if not text: return 0
        try:
            # 移除常见干扰词
            text = clean_asian_handicap(text)
            
            is_receive = '受' in text
            clean_text = text.replace('受', '')
            
            val = 0.0
            mapping = {
                '平手': 0.0,
                '平/半': 0.25,
                '半球': 0.5,
                '半/一': 0.75,
                '一球': 1.0,
                '一/球半': 1.25,
                '球半': 1.5,
                '球半/两': 1.75, '球半/两球': 1.75,
                '两球': 2.0,
                '两/两球半': 2.25,
                '两球半': 2.5,
                '两球半/三': 2.75, '两球半/三球': 2.75,
                '三球': 3.0,
                '三/三球半': 3.25,
                '三球半': 3.5
            }
            
            if clean_text in mapping:
                val = mapping[clean_text]
            else:
                # 尝试直接解析数字 (e.g. "-1", "+1")
                import re
                nums = re.findall(r'[-+]?\d+\.?\d*', text)
                if nums:
                    return float(nums[0])
                return 0.0
                
            if is_receive:
                return val
            else:
                return -val
        except Exception:
            return 0.0

    def parse_handicap_index(self, html_content, asian_hint=None):
        """
        解析让球指数数据 - 500彩票网让球指数专门页面
        优先爬取'竞*官*'（竞彩官方）的数据
        
        Args:
            html_content: HTML内容
            asian_hint: 亚盘提示值（浮点数），用于处理多条记录时的消歧
            
        Returns:
            handicap_index_data: 让球指数数据，包含让球数和赔率
        """
        soup = BeautifulSoup(html_content, 'lxml')
        handicap_index_data = {}
        
        try:
            # 查找让球指数数据表格 (id="datatb")
            table = soup.find('table', id='datatb')
            if not table:
                self.logger.warning("未找到让球指数数据表格 (id=datatb)")
                return handicap_index_data
            
            # 查找'竞*官*'行数据
            rows = table.find_all('tr')
            candidates = []
            
            for r in rows:
                try:
                    txt = r.get_text(strip=True)
                except Exception:
                    txt = ''
                # 查找包含'竞*官*'的行
                if txt and '竞*官*' in txt and '竞*官*(中国)' in txt:
                    candidates.append(r)
            
            preferred_row = None
            if candidates:
                if len(candidates) == 1:
                    preferred_row = candidates[0]
                else:
                    # 多条记录，尝试消歧
                    if asian_hint is not None:
                        best_diff = float('inf')
                        best_row = None
                        for r in candidates:
                            try:
                                tds = r.find_all('td')
                                if len(tds) >= 3:
                                    val = float(tds[2].get_text(strip=True))
                                    # 比较符号是否一致
                                    if (val > 0 and asian_hint > 0) or (val < 0 and asian_hint < 0) or (val == 0 and abs(asian_hint) < 0.25):
                                        diff = abs(val - asian_hint)
                                        if diff < best_diff:
                                            best_diff = diff
                                            best_row = r
                            except:
                                pass
                        
                        if best_row:
                            preferred_row = best_row
                            self.logger.info(f"使用亚盘提示({asian_hint})从 {len(candidates)} 条记录中选择了最佳匹配")
                    
                    # 如果没有提示或匹配失败，默认取最后一条（通常是ID较大的或最新的）
                    if not preferred_row:
                        preferred_row = candidates[-1]
                        self.logger.info(f"存在 {len(candidates)} 条竞彩记录，默认选择最后一条")
            
            if not preferred_row:
                self.logger.warning("未找到竞*官*让球指数数据行")
                return handicap_index_data
            
            tds = preferred_row.find_all('td')
            if len(tds) >= 9:
                try:
                    # TD[2]: 让球数（如 -1, 0, +1）
                    handicap_value = tds[2].get_text(strip=True)
                    
                    # TD[4-6]: 初盘赔率（主队、平局、客队）
                    initial_home = tds[4].get_text(strip=True)
                    initial_draw = tds[5].get_text(strip=True)
                    initial_away = tds[6].get_text(strip=True)
                    
                    # TD[7-9]: 即时盘赔率（主队、平局、客队）
                    current_home = tds[7].get_text(strip=True)
                    current_draw = tds[8].get_text(strip=True)
                    current_away = tds[9].get_text(strip=True)
                    
                    if handicap_value and current_home and current_draw and current_away:
                        handicap_index_data = {
                            # 让球数
                            'handicap_value': handicap_value,
                            # 即时盘赔率
                            'current_home_odds': current_home,
                            'current_draw_odds': current_draw,
                            'current_away_odds': current_away,
                            # 初盘赔率
                            'initial_home_odds': initial_home,
                            'initial_draw_odds': initial_draw,
                            'initial_away_odds': initial_away,
                            # 兼容旧字段（即时盘）
                            'home_odds': current_home,
                            'draw_odds': current_draw,
                            'away_odds': current_away
                        }
                        self.logger.info(f"解析到让球指数(竞*官*): 让球数={handicap_value}, 即时盘={current_home}/{current_draw}/{current_away}, 初盘={initial_home}/{initial_draw}/{initial_away}")
                    
                except (IndexError, ValueError) as e:
                    self.logger.warning(f"解析让球指数数据失败: {str(e)}")
            
        except Exception as e:
            self.logger.error(f"解析让球指数页面失败: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            
        return handicap_index_data
    
    def parse_match_list_json(self, json_data):
        """
        解析比赛列表JSON数据 - 500彩票网新接口
        
        Args:
            json_data: JSON数据字典
            
        Returns:
            matches: 比赛列表
        """
        matches = []
        try:
            if not json_data or 'data' not in json_data or 'matches' not in json_data['data']:
                self.logger.warning("JSON数据格式不正确")
                return matches
            
            raw_matches = json_data['data']['matches']
            if not raw_matches:
                return matches
                
            for item in raw_matches:
                # 提取基本信息
                match_id = item.get('fid', '')
                match_number = item.get('order', '')  # 场次，如：周日001
                league = item.get('simpleleague', '')
                round_str = item.get('matchround', '')
                match_time = item.get('matchtime', '')
                
                # 格式化时间：保留 MM-DD HH:MM
                if match_time:
                    try:
                        dt = datetime.strptime(match_time, '%Y-%m-%d %H:%M:%S')
                        match_time = dt.strftime('%m-%d %H:%M')
                    except Exception:
                        pass
                
                # 状态映射
                status_desc = item.get('status_desc', '')
                raw_status = str(item.get('status', ''))
                status_code = 0
                
                if raw_status == '4':
                    status_code = 2  # 完场
                elif raw_status == '0':
                    status_code = 0  # 未开始
                elif raw_status == '6':
                    status_code = 6  # 改期
                elif raw_status in ['1', '2', '3']:
                    status_code = 1  # 进行中
                else:
                    # 兜底逻辑：如果status字段无法识别，尝试使用status_desc
                    if '完' in status_desc or '结束' in status_desc:
                        status_code = 2
                    elif '改期' in status_desc:
                        status_code = 6
                    elif '未' in status_desc or '推迟' in status_desc or '取消' in status_desc:
                        status_code = 0
                    else:
                        status_code = 1
                
                # 针对已结束或进行中的比赛，强制更新状态
                home_score = item.get('homescore', '')
                away_score = item.get('awayscore', '')
                home_half_score = item.get('homehalfscore', '')
                away_half_score = item.get('awayhalfscore', '')
                
                # 移除强制更新状态的逻辑，因为API返回的status=0是可信的
                # 有些未开始比赛 homescore/awayscore 可能是 "0"
                
                # 比分和排名
                home_team = item.get('homesxname', '')
                home_rank = item.get('homestanding', '')
                away_team = item.get('awaysxname', '')
                away_rank = item.get('awaystanding', '')
                
                handicap = item.get('rangqiu', '')
                owner_date = item.get('ownerdate', '')
                # 尝试标准化 owner_date 为 YYYY-MM-DD
                if owner_date and '-' not in owner_date and len(owner_date) == 8:
                    try:
                        owner_date = f"{owner_date[:4]}-{owner_date[4:6]}-{owner_date[6:]}"
                    except:
                        pass
                
                score = '-'
                if status_code != 0 and home_score and away_score:
                    score = f"{home_score}-{away_score}"
                half_score = ''
                if (
                    status_code != 0
                    and str(home_half_score).strip() != ''
                    and str(away_half_score).strip() != ''
                ):
                    half_score = f"{home_half_score}-{away_half_score}"
                
                match_data = {
                    'match_id': match_id,
                    'match_number': match_number,
                    'round_id': match_number,
                    'league': league,
                    'round': round_str,
                    'match_time': match_time,
                    'status': status_code,
                    'status_text': status_desc,
                    'home_team': home_team,
                    'home_rank': home_rank,
                    'score': score,
                    'away_team': away_team,
                    'away_rank': away_rank,
                    'home_score': home_score,
                    'away_score': away_score,
                    'home_half_score': home_half_score,
                    'away_half_score': away_half_score,
                    'half_score': half_score,
                    'handicap': handicap,
                    'owner_date': owner_date,
                }
                
                matches.append(match_data)
                
                # 逐条保存
                if self.mongo_storage:
                    self.mongo_storage.save_match(match_data)
                    
            self.logger.info(f"解析到 {len(matches)} 场比赛 (JSON)")
            
        except Exception as e:
            self.logger.error(f"解析比赛列表JSON失败: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            
        return matches

    def crawl_match_odds_xml(self):
        """
        从XML接口获取让球赔率数据
        URL: https://trade.500.com/static/public/jczq/newxml/pl/pl_spf_2.xml
        """
        odds_data = {}
        try:
            url = "https://trade.500.com/static/public/jczq/newxml/pl/pl_spf_2.xml"
            self.logger.info(f"获取让球赔率XML数据: {url}")
            response = self._make_request(url)
            content = self._decode_html(response)
            import xml.etree.ElementTree as ET
            root = ET.fromstring(content)
            for m in root.findall('m'):
                matchnum = m.get('matchnum')
                if not matchnum: continue
                rows = m.findall('row')
                if not rows: continue
                current_row = rows[0]
                initial_row = rows[-1]
                curr_win = current_row.get('win')
                curr_draw = current_row.get('draw')
                curr_lost = current_row.get('lost')
                init_win = initial_row.get('win')
                init_draw = initial_row.get('draw')
                init_lost = initial_row.get('lost')
                if current_row is not None:
                    odds_str = f"{curr_win}/{curr_draw}/{curr_lost}"
                    odds_data[matchnum] = {
                        'currodds': odds_str,
                        'updatetime': current_row.get('updatetime'),
                        'initial_win': init_win, 'initial_draw': init_draw, 'initial_lost': init_lost,
                        'current_win': curr_win, 'current_draw': curr_draw, 'current_lost': curr_lost
                    }
        except Exception as e:
            self.logger.error(f"解析赔率XML失败: {str(e)}")
        return odds_data

    def crawl_euro_odds_xml(self):
        """从XML接口获取欧赔数据"""
        odds_data = {}
        try:
            url = "https://trade.500.com/static/public/jczq/newxml/pl/pl_nspf_2.xml"
            self.logger.info(f"获取欧赔XML数据: {url}")
            response = self._make_request(url)
            content = self._decode_html(response)
            import xml.etree.ElementTree as ET
            root = ET.fromstring(content)
            for m in root.findall('m'):
                matchnum = m.get('matchnum')
                if not matchnum: continue
                rows = m.findall('row')
                if not rows: continue
                current_row = rows[0]
                initial_row = rows[-1]
                curr_win = current_row.get('win')
                curr_draw = current_row.get('draw')
                curr_lost = current_row.get('lost')
                init_win = initial_row.get('win')
                init_draw = initial_row.get('draw')
                init_lost = initial_row.get('lost')
                if current_row is not None:
                    odds_str = f"{curr_win}/{curr_draw}/{curr_lost}"
                    odds_data[matchnum] = {
                        'currodds': odds_str,
                        'updatetime': current_row.get('updatetime'),
                        'initial_win': init_win, 'initial_draw': init_draw, 'initial_lost': init_lost,
                        'current_win': curr_win, 'current_draw': curr_draw, 'current_lost': curr_lost
                    }
        except Exception as e:
            self.logger.error(f"解析欧赔XML失败: {str(e)}")
        return odds_data

    def crawl_over_under_xml(self):
        """从XML接口获取大小球数据"""
        # 500.com 大小球XML地址: https://trade.500.com/static/public/jczq/newxml/pl/pl_dxq_2.xml
        odds_data = {}
        try:
            url = "https://trade.500.com/static/public/jczq/newxml/pl/pl_dxq_2.xml"
            self.logger.info(f"获取大小球XML数据: {url}")
            response = self._make_request(url)
            content = self._decode_html(response)
            import xml.etree.ElementTree as ET
            root = ET.fromstring(content)
            for m in root.findall('m'):
                matchnum = m.get('matchnum')
                if not matchnum:
                    continue
                rows = m.findall('row')
                if not rows:
                    continue
                current_row = rows[0]
                initial_row = rows[-1]
                curr_over = current_row.get('over')
                curr_total = current_row.get('total')
                curr_under = current_row.get('under')
                init_over = initial_row.get('over')
                init_total = initial_row.get('total')
                init_under = initial_row.get('under')
                if current_row is not None:
                    odds_data[matchnum] = {
                        'updatetime': current_row.get('updatetime'),
                        'current_over': curr_over,
                        'current_total': curr_total,
                        'current_under': curr_under,
                        'initial_over': init_over,
                        'initial_total': init_total,
                        'initial_under': init_under
                    }
        except Exception as e:
            self.logger.error(f"解析大小球XML失败: {str(e)}")
        return odds_data

    def crawl_asian_odds_xml(self):
        """从XML接口获取亚盘数据 (注：500网可能没有直接的亚盘XML，这里暂用让球xml代替或忽略)"""
        # 实际情况中，可能需要寻找其他XML或忽略
        return {}

    def crawl_daily_matches(self, url_or_date, fetch_odds=True):
        """
        爬取每日比赛信息
        支持传入日期字符串(YYYY-MM-DD)或旧版URL
        
        Args:
            url_or_date: 日期或URL
            fetch_odds: 是否同时爬取赔率详情（仅对历史数据有效）
        """
        try:
            # 解析日期
            target_date = None
            if 'live.500.com' in url_or_date and 'e=' in url_or_date:
                try:
                    query = urlparse(url_or_date).query
                    params = dict(p.split('=') for p in query.split('&') if '=' in p)
                    date_str = params.get('e')
                    if date_str:
                        target_date = datetime.strptime(date_str, '%Y-%m-%d')
                except Exception:
                    pass
            elif 'ews.500.com' in url_or_date and '.json' in url_or_date:
                # 从JSON URL中提取日期 (e.g. .../20260109.json)
                try:
                    match = re.search(r'/(\d{8})\.json', url_or_date)
                    if match:
                        date_str = match.group(1)
                        target_date = datetime.strptime(date_str, '%Y%m%d')
                except Exception:
                    pass
            else:
                try:
                    target_date = datetime.strptime(url_or_date, '%Y-%m-%d')
                except Exception:
                    pass
            
            if not target_date:
                target_date = datetime.now()
                
            # 1. 始终使用JSON接口获取比赛列表
            if '500.com' in url_or_date and 'json' in url_or_date:
                # 如果传入的是完整的API URL，直接使用
                json_url = url_or_date
            else:
                # 否则构造URL
                ym = target_date.strftime('%Y%m')
                ymd = target_date.strftime('%Y%m%d')
                ts = int(time.time() * 1000)
                json_url = f"https://ews.500.com/static/ews/jczq/{ym}/{ymd}.json?random={ts}"
            
            self.logger.info(f"使用JSON接口抓取比赛列表: {json_url}")
            
            response = self._make_request(json_url)
            try:
                json_data = response.json()
            except Exception:
                content = self._decode_html(response)
                import json
                json_data = json.loads(content)
                
            matches = self.parse_match_list_json(json_data)
            
            # 2. 根据日期决定获取赔率的方式
            is_history = target_date.date() < datetime.now().date()
            
            if is_history:
                if not fetch_odds:
                    self.logger.info("历史数据模式: fetch_odds=False, 跳过详细赔率抓取")
                    return matches

                # 历史模式: 页面爬取详细赔率
                self.logger.info(f"历史数据模式: 正在抓取 {len(matches)} 场比赛的详细赔率(页面方式)...")
                for i, match in enumerate(matches):
                    match_id = match.get('match_id')
                    if not match_id: continue
                    
                    if (i + 1) % 5 == 0:
                        self.logger.info(f"进度: {i + 1}/{len(matches)}")
                        
                    try:
                        time.sleep(random.uniform(0.2, 0.5))
                        odds_details = self.crawl_match_odds(match_id, match=match)
                        self._map_odds_details(match, odds_details)
                        
                        # 保存赔率数据到数据库
                        if self.mongo_storage:
                            self.mongo_storage.save_odds(match_id, odds_details)
                            # 同时更新比赛基础表中的赔率字段
                            self.mongo_storage.save_match(match)
                            
                    except Exception as e:
                        self.logger.error(f"抓取比赛 {match_id} 详情失败: {e}")
            else:
                # 当天赔率由 crawl_match_odds 统一从体彩计算器与澳客读取。
                # 列表解析阶段不再请求 500 的赔率 XML，避免重复和来源混用。
                self.logger.info(
                    '比赛列表解析完成，详细赔率交由体彩计算器/澳客流程更新'
                )
            
            return matches
        except Exception as e:
            self.logger.error(f"爬取比赛信息失败: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return []
    
    def _fetch_data(self, url, parser_func, retries=3):
        """
        获取并解析数据，带重试机制
        
        Args:
            url: 目标URL
            parser_func: 解析函数
            retries: 重试次数
            
        Returns:
            parsed_data: 解析后的数据，失败返回None
        """
        for i in range(retries):
            try:
                response = self._make_request(url)
                html = self._decode_html(response)
                data = parser_func(html)
                
                # 验证数据是否为空
                is_valid = False
                if isinstance(data, dict):
                    if 'euro_odds' in data:
                        is_valid = bool(data['euro_odds'])
                    elif 'handicap_value' in data:
                        is_valid = bool(data['handicap_value'])
                    else:
                        is_valid = bool(data)
                elif isinstance(data, list):
                    is_valid = bool(data)
                
                if is_valid:
                    return data
                
                if i < retries - 1:
                    self.logger.warning(f"解析数据为空, 准备重试 ({i+1}/{retries}): {url}")
                    time.sleep(random.uniform(1, 2))
                    
            except Exception as e:
                if i < retries - 1:
                    self.logger.warning(f"获取数据异常: {str(e)}，准备重试 ({i+1}/{retries}): {url}")
                    time.sleep(random.uniform(1, 2))
                else:
                    self.logger.error(f"获取数据最终失败: {url}")
        
        return None

    def crawl_match_odds(self, match_id, match=None):
        """
        爬取指定比赛的赔率信息。

        数据源：
        - 体彩计算器：胜平负、让球胜平负
        - 澳客：亚盘、大小球

        此流程不再请求 500 赔率页面。
        
        Args:
            match_id: 比赛ID
            
        Returns:
            odds: 赔率数据
        """
        odds_data = {
            'euro_odds': [],
            'asian_handicap': [],
            'over_under': [],
            'handicap_index': {}  # 新增：让球指数
        }
        
        try:
            if not match:
                self.logger.warning(
                    f'缺少比赛场次信息，无法映射赔率数据源: {match_id}'
                )
                return odds_data

            # 1. 竞彩官方固定赔率。
            try:
                sporttery = self.crawl_sporttery_odds(match)
                if sporttery.get('euro_odds'):
                    odds_data['euro_odds'] = sporttery['euro_odds']
                if sporttery.get('handicap_index'):
                    odds_data['handicap_index'] = sporttery['handicap_index']
                if sporttery.get('sporttery_match_id'):
                    odds_data['sporttery_match_id'] = sporttery[
                        'sporttery_match_id'
                    ]
            except Exception as exc:
                self.logger.warning(f'体彩计算器赔率获取失败 {match_id}: {exc}')

            # 2. 澳客作为亚盘、大小球主数据源，不再等待其他来源失败后回退。
            try:
                okooo = self.crawl_okooo_odds(
                    match,
                    need_asian=True,
                    need_over_under=True,
                )
                if okooo.get('asian_handicap'):
                    odds_data['asian_handicap'] = okooo['asian_handicap']
                if okooo.get('over_under'):
                    odds_data['over_under'] = okooo['over_under']
                if okooo.get('okooo_match_id'):
                    odds_data['okooo_match_id'] = okooo['okooo_match_id']
            except Exception as exc:
                self.logger.warning(f'澳客赔率获取失败 {match_id}: {exc}')

            return odds_data

        except Exception as e:
            self.logger.error(f"爬取赔率信息失败: {str(e)}")
            return {}

    def crawl_match_analysis(self, match_id):
        """抓取 500 官方比赛数据分析页并转换为稳定的结构化数据。"""
        url = f"https://odds.500.com/fenxi/shuju-{match_id}.shtml"
        response = self._make_request(url)
        response.encoding = response.apparent_encoding or 'gb18030'
        soup = BeautifulSoup(response.text, 'lxml')

        title = soup.select_one('title')
        if not title or '数据分析' not in title.get_text():
            raise ValueError('500 比赛分析页返回异常')

        def text(node):
            return ' '.join(node.get_text(' ', strip=True).split()) if node else ''

        def parse_match_cell(cell):
            if not cell:
                return {'home_team': '', 'away_team': '', 'score': ''}
            home = cell.select_one('.dz-l')
            away = cell.select_one('.dz-r')
            score_node = cell.select_one('em')
            def team_name(node):
                if not node:
                    return ''
                clone = BeautifulSoup(str(node), 'lxml')
                for rank in clone.select('.gray'):
                    rank.decompose()
                return text(clone)
            return {
                'home_team': team_name(home),
                'away_team': team_name(away),
                'score': text(score_node).replace(' ', ''),
            }

        def parse_form_table(table):
            rows = []
            if not table:
                return rows
            for tr in table.select('tr')[1:]:
                cells = tr.select('td')
                if len(cells) < 6:
                    continue
                match_cell = parse_match_cell(tr.select_one('td.dz'))
                handicap_cell = cells[3]
                rows.append({
                    'match_id': tr.get('fid') or '',
                    'league': text(cells[0]),
                    'date': text(cells[1]),
                    **match_cell,
                    'handicap': text(handicap_cell),
                    'handicap_name': handicap_cell.get('title') or '',
                    'half_score': text(cells[4]).replace(' ', ''),
                    'result': text(cells[5]),
                    'handicap_result': text(cells[6]) if len(cells) > 6 else '',
                    'total_result': text(cells[7]) if len(cells) > 7 else '',
                })
            return rows

        history = []
        history_table = soup.select_one('#team_jiaozhan table.pub_table')
        if history_table:
            for tr in history_table.select('tr')[1:]:
                cells = tr.select('td')
                if len(cells) < 5:
                    continue
                match_cell = parse_match_cell(tr.select_one('td.dz'))
                asian_parts = [text(span) for span in cells[6].select('span')] if len(cells) > 6 else []
                history.append({
                    'match_id': tr.get('fid') or '',
                    'league': text(cells[0]),
                    'date': text(cells[1]),
                    **match_cell,
                    'half_score': text(cells[3]).replace(' ', ''),
                    'result': text(cells[4]),
                    'euro_odds': [text(span) for span in cells[5].select('span')] if len(cells) > 5 else [],
                    'asian_odds': asian_parts,
                    'handicap_result': text(cells[7]) if len(cells) > 7 else '',
                    'total_result': text(cells[8]) if len(cells) > 8 else '',
                })

        recent_box = next(
            (box for box in soup.select('div.M_box.record')
             if box.select_one('h4') and '近期战绩' in text(box.select_one('h4'))),
            None
        )
        recent_module = (
            recent_box.select_one('.odds_zj_tubiao.module_cur')
            if recent_box else None
        )
        if not recent_module and recent_box:
            recent_module = recent_box.select_one('.odds_zj_tubiao')
        recent = {
            'home': parse_form_table(
                recent_module.select_one('.team_a table.pub_table')
                if recent_module else None
            ),
            'away': parse_form_table(
                recent_module.select_one('.team_b table.pub_table')
                if recent_module else None
            ),
        }

        standings = []
        standings_table = soup.select_one('.hd_jfb table')
        if standings_table:
            for tr in standings_table.select('tr'):
                cells = tr.select('td')
                if len(cells) >= 3:
                    standings.append({
                        'rank': text(cells[0]),
                        'team': text(cells[1]),
                        'points': text(cells[2]),
                        'current_match_team': 'jfb_this' in (tr.get('class') or []),
                    })

        future = {'home': [], 'away': []}
        future_box = next(
            (box for box in soup.select('div.M_box.integral')
             if box.select_one('h4') and '未来赛事' in text(box.select_one('h4'))),
            None
        )
        if future_box:
            for side, table in zip(('home', 'away'), future_box.select('table.pub_table')[:2]):
                for tr in table.select('tr')[1:]:
                    cells = tr.select('td')
                    if len(cells) < 4:
                        continue
                    teams = cells[2].select('a')
                    future[side].append({
                        'league': text(cells[0]),
                        'date': text(cells[1]),
                        'home_team': text(teams[0]) if len(teams) > 0 else '',
                        'away_team': text(teams[1]) if len(teams) > 1 else '',
                        'interval': text(cells[3]),
                    })

        team_names = []
        team_rankings = {}
        rank_box = next(
            (box for box in soup.select('div.M_box')
             if box.select_one('h4') and '赛前联赛积分排名' in text(box.select_one('h4'))),
            None
        )
        if rank_box:
            for node in rank_box.select('.M_sub_title .team_name')[:2]:
                clone = BeautifulSoup(str(node), 'lxml')
                for detail in clone.select('span'):
                    detail.decompose()
                team_names.append(text(clone))

            stat_keys = (
                'scope', 'matches', 'wins', 'draws', 'losses',
                'goals_for', 'goals_against', 'goal_difference',
                'points', 'rank', 'win_rate',
            )
            for side, box in zip(
                ('home', 'away'),
                rank_box.select('.M_content .team_a, .M_content .team_b')[:2],
            ):
                rows = []
                for tr in box.select('table.pub_table tr')[1:]:
                    values = [text(cell) for cell in tr.select('td')]
                    if len(values) >= len(stat_keys):
                        rows.append(dict(zip(stat_keys, values[:len(stat_keys)])))
                heading = rank_box.select(
                    '.M_sub_title .team_name'
                )
                heading_text = text(heading[0 if side == 'home' else 1]) if len(heading) >= 2 else ''
                rank_match = re.search(r'\[([^\]]+)\]', heading_text)
                team_rankings[side] = {
                    'team': team_names[0 if side == 'home' else 1]
                    if len(team_names) >= 2 else '',
                    'league_rank': rank_match.group(1) if rank_match else '',
                    'records': rows,
                }

        def parse_player(cell):
            if not cell:
                return None
            number_node = cell.select_one('.td_sp3')
            number = text(number_node)
            clone = BeautifulSoup(str(cell), 'lxml')
            for node in clone.select('.td_sp3'):
                node.decompose()
            player_text = text(clone)
            if not player_text:
                return None
            position_match = re.search(r'[（(]([^()（）]+)[）)]$', player_text)
            return {
                'number': number,
                'name': re.sub(r'[（(][^()（）]+[）)]$', '', player_text).strip(),
                'position': position_match.group(1) if position_match else '',
            }

        lineups = {}
        injuries = {}
        starting_box = soup.select_one('div.M_box.starting')
        if starting_box:
            for side, team_box in zip(
                ('home', 'away'),
                starting_box.select('.M_content .team_a, .M_content .team_b')[:2],
            ):
                heading = text(team_box.select_one('.team_name'))
                formation = heading.split('阵型:', 1)[1].strip() if '阵型:' in heading else ''
                starters = []
                substitutes = []
                injured = []
                suspended = []
                injury_section = False
                for tr in team_box.select('table.pub_table tr'):
                    header_text = text(tr)
                    if tr.select('th'):
                        if '伤病' in header_text or '停赛' in header_text:
                            injury_section = True
                        continue
                    cells = tr.select('td')
                    if not cells:
                        continue
                    left = parse_player(cells[0])
                    right = parse_player(cells[1]) if len(cells) > 1 else None
                    if injury_section:
                        if left:
                            injured.append(left)
                        if right:
                            suspended.append(right)
                    else:
                        if left:
                            starters.append(left)
                        if right:
                            substitutes.append(right)
                lineups[side] = {
                    'team': team_names[0 if side == 'home' else 1]
                    if len(team_names) >= 2 else heading.split('阵型:', 1)[0],
                    'formation': formation,
                    'starters': starters,
                    'substitutes': substitutes,
                }
                injuries[side] = {
                    'injured': injured,
                    'suspended': suspended,
                }
            if lineups:
                lineups['status'] = 'predicted'
                lineups['label'] = '500彩票网预计阵容（非官方确认首发）'
                injuries['status'] = (
                    'listed'
                    if any(
                        injuries.get(side, {}).get(kind)
                        for side in ('home', 'away')
                        for kind in ('injured', 'suspended')
                    )
                    else 'no_listed_players'
                )
                injuries['label'] = (
                    '500彩票网伤病/停赛栏目'
                    if injuries['status'] == 'listed'
                    else '500彩票网伤病/停赛栏目未列出球员'
                )

        return {
            'source': '500彩票网',
            'source_url': url,
            'match_id': str(match_id),
            'title': text(title),
            'teams': team_names,
            'standings': standings,
            'team_rankings': team_rankings,
            'history': history,
            'recent': recent,
            'future': future,
            'lineups': lineups,
            'injuries': injuries,
        }
    
    def _map_odds_details(self, match, odds_details):
        """将爬取的详细赔率数据映射到比赛对象"""
        if odds_details.get('okooo_match_id'):
            match['okooo_match_id'] = str(odds_details['okooo_match_id'])
        if odds_details.get('sporttery_match_id'):
            match['sporttery_match_id'] = str(
                odds_details['sporttery_match_id']
            )
        # 1. 欧赔
        if odds_details.get('euro_odds'):
            euro = odds_details['euro_odds'][0]
            match['euro_current_win'] = euro.get('current_win')
            match['euro_current_draw'] = euro.get('current_draw')
            match['euro_current_lose'] = euro.get('current_lose')
            match['euro_initial_win'] = euro.get('initial_win')
            match['euro_initial_draw'] = euro.get('initial_draw')
            match['euro_initial_lose'] = euro.get('initial_lose')
            match['euro_win_flag'] = euro.get('win_flag', 0)
            match['euro_draw_flag'] = euro.get('draw_flag', 0)
            match['euro_lose_flag'] = euro.get('lose_flag', 0)
            match['euro_source_provider'] = euro.get(
                'source_provider', 'sporttery-calculator'
            )
            if euro.get('updated_at'):
                match['euro_odds_update_time'] = euro['updated_at']
            match['euro_odds'] = f"{match['euro_current_win']}/{match['euro_current_draw']}/{match['euro_current_lose']}"
            
        # 2. 亚盘
        if odds_details.get('asian_handicap'):
            asian = odds_details['asian_handicap'][0]
            match['asian_initial_home_odds'] = asian.get('initial_home_odds')
            match['asian_initial_handicap'] = clean_asian_handicap(asian.get('initial_handicap'))
            match['asian_initial_away_odds'] = asian.get('initial_away_odds')
            match['asian_current_home_odds'] = asian.get('current_home_odds')
            match['asian_current_handicap'] = clean_asian_handicap(asian.get('current_handicap'))
            match['asian_current_away_odds'] = asian.get('current_away_odds')
            match['asian_source_company_id'] = asian.get('source_company_id')
            match['asian_source_company_name'] = asian.get('source_company_name')
            match['asian_source_provider'] = asian.get('source_provider', '500')
            match['asian_source_fallback'] = asian.get('source_fallback', False)
            match['asian_odds'] = f"{match['asian_current_home_odds']}/{match['asian_current_handicap']}/{match['asian_current_away_odds']}"

        # 3. 大小球
        if odds_details.get('over_under'):
            ou = odds_details['over_under'][0]
            match['ou_initial_over_odds'] = ou.get('initial_over_odds')
            match['ou_initial_total'] = ou.get('initial_total')
            match['ou_initial_under_odds'] = ou.get('initial_under_odds')
            match['ou_current_over_odds'] = ou.get('current_over_odds')
            match['ou_current_total'] = ou.get('current_total')
            match['ou_current_under_odds'] = ou.get('current_under_odds')
            match['ou_source_company_id'] = ou.get('source_company_id')
            match['ou_source_company_name'] = ou.get('source_company_name')
            match['ou_source_provider'] = ou.get('source_provider', '500')
            match['ou_source_fallback'] = ou.get('source_fallback', False)
            match['ou_odds'] = f"{match['ou_current_over_odds']}/{match['ou_current_total']}/{match['ou_current_under_odds']}"

        # 4. 让球指数 (Handicap Index)
        if odds_details.get('handicap_index'):
            hi = odds_details['handicap_index']
            # 直接从hi字典中获取，因为parse_handicap_index返回的是扁平字典
            match['hi_initial_home_odds'] = hi.get('initial_home_odds')
            match['hi_initial_draw_odds'] = hi.get('initial_draw_odds')
            match['hi_initial_away_odds'] = hi.get('initial_away_odds')
            match['hi_current_home_odds'] = hi.get('current_home_odds')
            match['hi_current_draw_odds'] = hi.get('current_draw_odds')
            match['hi_current_away_odds'] = hi.get('current_away_odds')
            match['hi_handicap_value'] = hi.get('handicap_value')
            match['hi_home_flag'] = hi.get('home_flag', 0)
            match['hi_draw_flag'] = hi.get('draw_flag', 0)
            match['hi_away_flag'] = hi.get('away_flag', 0)
            match['hi_source_provider'] = hi.get(
                'source_provider', 'sporttery-calculator'
            )
            if hi.get('updated_at'):
                match['odds_update_time'] = hi['updated_at']

    def update_single_match_odds(self, match):
        """
        更新单个比赛的赔率信息（历史模式）
        """
        match_id = match.get('match_id')
        if not match_id:
            return False
            
        try:
            odds_details = self.crawl_match_odds(match_id, match=match)
            self._map_odds_details(match, odds_details)
            return True
        except Exception as e:
            self.logger.error(f"更新比赛 {match_id} 赔率失败: {e}")
            return False

    def close(self):
        """关闭会话"""
        self.session.close()
        if getattr(self, 'sporttery_session', None):
            self.sporttery_session.close()
        self.logger.info("爬虫会话已关闭")

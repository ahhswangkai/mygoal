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
from utils import setup_logger
from config import REQUEST_HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY, MAX_RETRIES


class FootballCrawler:
    """足球比赛和赔率数据爬虫"""
    
    def __init__(self):
        """初始化爬虫"""
        self.logger = setup_logger()
        self.session = requests.Session()
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
                score_td = tds[6]
                score_div = score_td.find('div', class_='pk')
                
                # 初始化比分和盘口
                score = ''
                handicap = ''
                home_score = ''
                away_score = ''
                
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
                if status_code == 0 and home_score.isdigit() and away_score.isdigit():
                    status_code = 1
                
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
                    'handicap': handicap,
                }
                
                matches.append(match_data)
                
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
                    # 列3-5：即时盘（主胜、平局、客胜）
                    current_win = tds[3].get_text(strip=True)
                    current_draw = tds[4].get_text(strip=True)
                    current_lose = tds[5].get_text(strip=True)
                    
                    # 列6-8：初盘（主胜、平局、客胜）
                    initial_win = tds[6].get_text(strip=True)
                    initial_draw = tds[7].get_text(strip=True)
                    initial_lose = tds[8].get_text(strip=True)
                    
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
            
            # 优先选择包含“t3*5”的数据行；若没有则取第一行
            rows = table.find_all('tr')
            preferred_row = None
            for r in rows:
                try:
                    txt = r.get_text(strip=True)
                except Exception:
                    txt = ''
                if txt and '**t3*5' in txt:
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
                    current_handicap = tds[4].get_text(strip=True)
                    current_away = tds[5].get_text(strip=True)
                    
                    # 列9-11：初盘（主队赔率、让球数、客队赔率）
                    initial_home = tds[9].get_text(strip=True)
                    initial_handicap = tds[10].get_text(strip=True)
                    initial_away = tds[11].get_text(strip=True)
                    
                    # 清理箭头符号
                    current_home = current_home.replace('↑', '').replace('↓', '')
                    current_away = current_away.replace('↑', '').replace('↓', '')
                    initial_home = initial_home.replace('↑', '').replace('↓', '')
                    initial_away = initial_away.replace('↑', '').replace('↓', '')
                    
                    if current_home and current_handicap and current_away:
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
                            'away_odds': current_away
                        })
                        self.logger.info(f"解析到亚盘(优先t3*5): 即时盘 {current_home}/{current_handicap}/{current_away}, 初盘 {initial_home}/{initial_handicap}/{initial_away}")
                    
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
            
            # 查找第一行数据（即时赔率）
            first_row = table.find('tr', class_='tr1')
            if not first_row:
                self.logger.warning("未找到大小球数据行")
                return over_under_data
            
            tds = first_row.find_all('td')
            if len(tds) >= 12:
                try:
                    # 列3-5：即时盘（大球赔率、盘口、小球赔率）
                    current_over = tds[3].get_text(strip=True)
                    current_total = tds[4].get_text(strip=True)
                    current_under = tds[5].get_text(strip=True)
                    
                    # 列9-11：初盘（大球赔率、盘口、小球赔率）
                    initial_over = tds[9].get_text(strip=True)
                    initial_total = tds[10].get_text(strip=True)
                    initial_under = tds[11].get_text(strip=True)
                    
                    # 清理箭头符号
                    current_over = current_over.replace('↑', '').replace('↓', '')
                    current_under = current_under.replace('↑', '').replace('↓', '')
                    initial_over = initial_over.replace('↑', '').replace('↓', '')
                    initial_under = initial_under.replace('↑', '').replace('↓', '')
                    
                    if current_over and current_total and current_under:
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
                            'under_odds': current_under
                        })
                        self.logger.info(f"解析到大小球: 即时盘 {current_over}/{current_total}/{current_under}, 初盘 {initial_over}/{initial_total}/{initial_under}")
                except (IndexError, ValueError) as e:
                    self.logger.warning(f"解析大小球数据失败: {str(e)}")
            
        except Exception as e:
            self.logger.error(f"解析大小球页面失败: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            
        return over_under_data
    
    def parse_handicap_index(self, html_content):
        """
        解析让球指数数据 - 500彩票网让球指数专门页面
        优先爬取'竞*官*'（竞彩官方）的数据
        
        Args:
            html_content: HTML内容
            
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
            preferred_row = None
            for r in rows:
                try:
                    txt = r.get_text(strip=True)
                except Exception:
                    txt = ''
                # 查找包含'竞*官*'的行
                if txt and '竞*官*' in txt and '竞*官*(中国)' in txt:
                    preferred_row = r
                    break
            
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
    
    def crawl_daily_matches(self, url):
        """
        爬取每日比赛信息
        
        Args:
            url: 目标网站URL
            
        Returns:
            matches: 比赛数据列表
        """
        try:
            response = self._make_request(url)
            html = self._decode_html(response)
            matches = self.parse_match_list(html)
            return matches
        except Exception as e:
            self.logger.error(f"爬取比赛信息失败: {str(e)}")
            return []
    
    def crawl_match_odds(self, match_id):
        """
        爬取指定比赛的赔率信息（包含即时盘和初盘）
        
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
            # 1. 爬取欧赔（使用欧赔专门页面）
            euro_url = f"https://odds.500.com/fenxi/ouzhi-{match_id}.shtml"
            try:
                euro_response = self._make_request(euro_url)
                euro_html = self._decode_html(euro_response)
                euro_data = self.parse_odds(euro_html)
                if euro_data.get('euro_odds'):
                    odds_data['euro_odds'] = euro_data['euro_odds']
            except Exception as e:
                self.logger.warning(f"爬取欧赔数据失败: {str(e)}")
            
            # 2. 爬取亚盘（使用亚盘专门页面）
            asian_url = f"https://odds.500.com/fenxi/yazhi-{match_id}.shtml"
            try:
                asian_response = self._make_request(asian_url)
                asian_html = self._decode_html(asian_response)
                asian_data = self.parse_asian_handicap(asian_html)
                if asian_data:
                    odds_data['asian_handicap'] = asian_data
            except Exception as e:
                self.logger.warning(f"爬取亚盘数据失败: {str(e)}")
            
            # 3. 爬取大小球（使用大小球专门页面）
            over_under_url = f"https://odds.500.com/fenxi/daxiao-{match_id}.shtml"
            try:
                ou_response = self._make_request(over_under_url)
                ou_html = self._decode_html(ou_response)
                over_under_data = self.parse_over_under(ou_html)
                if over_under_data:
                    odds_data['over_under'] = over_under_data
            except Exception as e:
                self.logger.warning(f"爬取大小球数据失败: {str(e)}")
            
            # 4. 爬取让球指数（使用让球指数专门页面）
            handicap_index_url = f"https://odds.500.com/fenxi/rangqiu-{match_id}.shtml"
            try:
                hi_response = self._make_request(handicap_index_url)
                hi_html = self._decode_html(hi_response)
                handicap_index_data = self.parse_handicap_index(hi_html)
                if handicap_index_data:
                    odds_data['handicap_index'] = handicap_index_data
            except Exception as e:
                self.logger.warning(f"爬取让球指数数据失败: {str(e)}")
            
            return odds_data
            
        except Exception as e:
            self.logger.error(f"爬取赔率信息失败: {str(e)}")
            return {}
    
    def close(self):
        """关闭会话"""
        self.session.close()
        self.logger.info("爬虫会话已关闭")

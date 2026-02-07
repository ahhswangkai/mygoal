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
    
    def __init__(self, mongo_storage=None):
        """
        初始化爬虫
        
        Args:
            mongo_storage: MongoDBStorage实例，用于实时保存
        """
        self.logger = setup_logger()
        self.mongo_storage = mongo_storage
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
    
    def _parse_chinese_handicap(self, text):
        """解析中文亚盘盘口为数字"""
        if not text: return 0
        try:
            # 移除常见干扰词
            text = text.strip()
            
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
                        odds_details = self.crawl_match_odds(match_id)
                        self._map_odds_details(match, odds_details)
                        
                        # 保存赔率数据到数据库
                        if self.mongo_storage:
                            self.mongo_storage.save_odds(match_id, odds_details)
                            # 同时更新比赛基础表中的赔率字段
                            self.mongo_storage.save_match(match)
                            
                    except Exception as e:
                        self.logger.error(f"抓取比赛 {match_id} 详情失败: {e}")
            else:
                # 实时模式: XML获取赔率
                try:
                    odds_data = self.crawl_match_odds_xml()
                    euro_odds_data = self.crawl_euro_odds_xml()
                    
                    for match in matches:
                        order = match.get('match_number', '')
                        if not order: continue
                        
                        # 转换场次号
                        day_map = {'周一': '1', '周二': '2', '周三': '3', '周四': '4', '周五': '5', '周六': '6', '周日': '7'}
                        matchnum = ''
                        for day_cn, day_num in day_map.items():
                            if order.startswith(day_cn):
                                num_part = order[len(day_cn):]
                                matchnum = day_num + num_part
                                break
                                
                        if matchnum:
                            if matchnum in odds_data:
                                odd_info = odds_data[matchnum]
                                if 'currodds' in odd_info: match['handicap_odds'] = odd_info['currodds']
                                if 'updatetime' in odd_info: match['odds_update_time'] = odd_info['updatetime']
                                if 'initial_win' in odd_info:
                                    match['hi_initial_home_odds'] = odd_info['initial_win']
                                    match['hi_initial_draw_odds'] = odd_info['initial_draw']
                                    match['hi_initial_away_odds'] = odd_info['initial_lost']
                                if 'current_win' in odd_info:
                                    match['hi_current_home_odds'] = odd_info['current_win']
                                    match['hi_current_draw_odds'] = odd_info['current_draw']
                                    match['hi_current_away_odds'] = odd_info['current_lost']
                                    
                            if matchnum in euro_odds_data:
                                euro_info = euro_odds_data[matchnum]
                                if 'currodds' in euro_info: match['euro_odds'] = euro_info['currodds']
                                if 'updatetime' in euro_info: match['euro_odds_update_time'] = euro_info['updatetime']
                                if 'initial_win' in euro_info:
                                    match['euro_initial_win'] = euro_info['initial_win']
                                    match['euro_initial_draw'] = euro_info['initial_draw']
                                    match['euro_initial_lose'] = euro_info['initial_lost']
                                if 'current_win' in euro_info:
                                    match['euro_current_win'] = euro_info['current_win']
                                    match['euro_current_draw'] = euro_info['current_draw']
                                    match['euro_current_lose'] = euro_info['current_lost']
                                    
                        # 保存更新后的比赛数据
                        if self.mongo_storage:
                            self.mongo_storage.save_match(match)
                                    
                except Exception as e:
                    self.logger.error(f"获取XML赔率数据失败: {str(e)}")
            
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
            euro_data = self._fetch_data(euro_url, self.parse_odds)
            if euro_data and euro_data.get('euro_odds'):
                odds_data['euro_odds'] = euro_data['euro_odds']
            
            # 2. 爬取亚盘（使用亚盘专门页面）
            asian_url = f"https://odds.500.com/fenxi/yazhi-{match_id}.shtml"
            asian_data = self._fetch_data(asian_url, self.parse_asian_handicap)
            if asian_data:
                odds_data['asian_handicap'] = asian_data
            
            # 3. 爬取大小球（使用大小球专门页面）
            over_under_url = f"https://odds.500.com/fenxi/daxiao-{match_id}.shtml"
            over_under_data = self._fetch_data(over_under_url, self.parse_over_under)
            if over_under_data:
                odds_data['over_under'] = over_under_data
            
            # 4. 爬取让球指数（使用让球指数专门页面）
            asian_hint = None
            if odds_data.get('asian_handicap'):
                ah_list = odds_data['asian_handicap']
                if ah_list:
                    h_str = ah_list[0].get('initial_handicap') or ah_list[0].get('current_handicap')
                    if h_str:
                        asian_hint = self._parse_chinese_handicap(h_str)

            handicap_index_url = f"https://odds.500.com/fenxi/rangqiu-{match_id}.shtml"
            handicap_index_data = self._fetch_data(
                handicap_index_url, 
                lambda html: self.parse_handicap_index(html, asian_hint=asian_hint)
            )
            if handicap_index_data:
                odds_data['handicap_index'] = handicap_index_data
            
            return odds_data
            
        except Exception as e:
            self.logger.error(f"爬取赔率信息失败: {str(e)}")
            return {}
    
    def _map_odds_details(self, match, odds_details):
        """将爬取的详细赔率数据映射到比赛对象"""
        # 1. 欧赔
        if odds_details.get('euro_odds'):
            euro = odds_details['euro_odds'][0]
            match['euro_current_win'] = euro.get('current_win')
            match['euro_current_draw'] = euro.get('current_draw')
            match['euro_current_lose'] = euro.get('current_lose')
            match['euro_initial_win'] = euro.get('initial_win')
            match['euro_initial_draw'] = euro.get('initial_draw')
            match['euro_initial_lose'] = euro.get('initial_lose')
            match['euro_odds'] = f"{match['euro_current_win']}/{match['euro_current_draw']}/{match['euro_current_lose']}"
            
        # 2. 亚盘
        if odds_details.get('asian_handicap'):
            asian = odds_details['asian_handicap'][0]
            match['asian_initial_home_odds'] = asian.get('initial_home_odds')
            match['asian_initial_handicap'] = asian.get('initial_handicap')
            match['asian_initial_away_odds'] = asian.get('initial_away_odds')
            match['asian_current_home_odds'] = asian.get('current_home_odds')
            match['asian_current_handicap'] = asian.get('current_handicap')
            match['asian_current_away_odds'] = asian.get('current_away_odds')
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

    def update_single_match_odds(self, match):
        """
        更新单个比赛的赔率信息（历史模式）
        """
        match_id = match.get('match_id')
        if not match_id:
            return False
            
        try:
            odds_details = self.crawl_match_odds(match_id)
            self._map_odds_details(match, odds_details)
            return True
        except Exception as e:
            self.logger.error(f"更新比赛 {match_id} 赔率失败: {e}")
            return False

    def close(self):
        """关闭会话"""
        self.session.close()
        self.logger.info("爬虫会话已关闭")

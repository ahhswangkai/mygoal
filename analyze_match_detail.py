# -*- coding: utf-8 -*-
"""
分析比赛详情页面 - 获取历史战绩、交锋记录等数据
"""
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent


def analyze_match_detail_page(match_id):
    """
    分析比赛详情页面的HTML结构
    
    Args:
        match_id: 比赛ID
    """
    # 500彩票网的比赛详情页URL格式
    odds_url = 'https://odds.500.com/fenxi/shuju-{}.shtml'.format(match_id)
    info_url = 'https://odds.500.com/fenxi/qingbao-{}.shtml'.format(match_id)
    history_url = 'https://odds.500.com/fenxi/duizhen-{}.shtml'.format(match_id)
    
    urls = {
        'odds': odds_url,
        'info': info_url,
        'history': history_url,
    }
    
    page_names = {
        'odds': '赔率分析',
        'info': '情报',
        'history': '对阵',
    }
    
    print("=" * 80)
    print("分析比赛ID: {} 的详情页面".format(match_id))
    print("=" * 80)
    
    ua = UserAgent()
    headers = {
        'User-Agent': ua.random,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    
    for page_key, url in urls.items():
        page_name = page_names[page_key]
        print("\n\n" + "="*80)
        print("页面: {}".format(page_name))
        print("URL: {}".format(url))
        print('='*80)
        
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.encoding = response.apparent_encoding
            
            print("状态码: {}".format(response.status_code))
            
            if response.status_code != 200:
                print("页面访问失败")
                continue
            
            soup = BeautifulSoup(response.text, 'lxml')
            
            # 保存HTML
            filename = 'data/{}_{}.html'.format(page_name, match_id)
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(soup.prettify())
            print("HTML已保存: {}".format(filename))
            
            # 分析内容
            print("\n页面结构分析:")
            print("-" * 80)
            
            if page_key == 'history':
                # 查找所有表格
                print("\n查找表格...")
                tables = soup.find_all('table')
                print("  找到 {} 个表格".format(len(tables)))
                for i, table in enumerate(tables[:5], 1):
                    print("\n  表格{}:".format(i))
                    print("    class: {}".format(table.get('class')))
                    print("    id: {}".format(table.get('id')))
                    
                    # 查找表头
                    thead = table.find('thead')
                    if thead:
                        headers_list = [th.get_text(strip=True) for th in thead.find_all('th')]
                        print("    表头: {}".format(headers_list))
                    
                    # 查找前几行数据
                    tbody = table.find('tbody')
                    if tbody:
                        rows = tbody.find_all('tr')[:3]
                        print("    数据行数: {}".format(len(tbody.find_all('tr'))))
                        for j, row in enumerate(rows, 1):
                            cells = [td.get_text(strip=True) for td in row.find_all('td')]
                            if cells:
                                print("      第{}行: {}".format(j, cells[:8]))  # 只显示前8列
            
            elif page_key == 'info':
                # 查找所有列表
                print("\n查找列表元素...")
                lists = soup.find_all(['ul', 'ol'])
                print("  找到 {} 个列表".format(len(lists)))
                for i, ul in enumerate(lists[:5], 1):
                    print("  列表{}: class={}, 项目数={}".format(i, ul.get('class'), len(ul.find_all('li'))))
            
            elif page_key == 'odds':
                # 查找大小球赔率
                print("\n查找大小球赔率...")
                
                # 查找包含"大小"关键字的表格
                all_tables = soup.find_all('table')
                for i, table in enumerate(all_tables, 1):
                    # 查找包含"大小"的表格
                    table_text = table.get_text()
                    if '大小' in table_text or 'Over' in table_text or '总进球' in table_text:
                        print("\n  表格{}可能包含大小球数据:".format(i))
                        print("    class: {}".format(table.get('class')))
                        print("    id: {}".format(table.get('id')))
                        
                        # 显示表头
                        thead = table.find('thead')
                        if thead:
                            headers_list = [th.get_text(strip=True) for th in thead.find_all('th')]
                            print("    表头: {}".format(headers_list))
                        
                        # 显示第一行数据
                        tbody = table.find('tbody')
                        if tbody:
                            first_row = tbody.find('tr')
                            if first_row:
                                cells = [td.get_text(strip=True) for td in first_row.find_all('td')]
                                print("    首行数据: {}".format(cells))
            
        except Exception as e:
            print("分析失败: {}".format(str(e)))
            import traceback
            traceback.print_exc()
    
    print("\n\n" + "=" * 80)
    print("分析完成！请查看 data/ 目录下的HTML文件")
    print("=" * 80)


if __name__ == '__main__':
    # 使用一个示例比赛ID进行分析
    print("请输入要分析的比赛ID（例如：2063529）")
    print("如果不知道ID，可以先运行 python main.py 查看比赛列表")
    
    match_id = input("\n比赛ID: ").strip()
    
    if match_id:
        analyze_match_detail_page(match_id)
    else:
        print("未输入比赛ID")

# -*- coding: utf-8 -*-
"""测试大小球页面"""
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

match_id = "1216031"
url = "https://odds.500.com/fenxi/daxiao-{}.shtml".format(match_id)

print("=" * 80)
print("分析大小球页面: {}".format(url))
print("=" * 80)

ua = UserAgent()
headers = {
    'User-Agent': ua.random,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

try:
    response = requests.get(url, headers=headers, timeout=30)
    response.encoding = response.apparent_encoding
    
    print("状态码: {}".format(response.status_code))
    
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'lxml')
        
        # 保存HTML
        with open('data/daxiao_{}.html'.format(match_id), 'w', encoding='utf-8') as f:
            f.write(soup.prettify())
        print("HTML已保存: data/daxiao_{}.html".format(match_id))
        
        # 查找大小球表格
        tables = soup.find_all('table', class_='pub_table')
        print("\n找到 {} 个表格".format(len(tables)))
        
        for i, table in enumerate(tables[:3], 1):
            print("\n表格{}:".format(i))
            print("  class: {}".format(table.get('class')))
            print("  id: {}".format(table.get('id')))
            
            # 显示表头
            thead = table.find('thead')
            if thead:
                headers_list = [th.get_text(strip=True) for th in thead.find_all('th')]
                print("  表头: {}".format(headers_list))
            
            # 显示前3行数据
            tbody = table.find('tbody')
            if tbody:
                rows = tbody.find_all('tr')[:3]
                for j, row in enumerate(rows, 1):
                    if row.get('fid'):
                        cells = [td.get_text(strip=True) for td in row.find_all('td')]
                        print("    第{}行: {}".format(j, cells))
    else:
        print("页面访问失败")
        
except Exception as e:
    print("错误: {}".format(str(e)))
    import traceback
    traceback.print_exc()


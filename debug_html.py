"""
HTML调试工具 - 用于查看网站HTML结构，制定爬取规则
"""
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import sys


def fetch_and_display_html(url, save_to_file=True):
    """
    获取并显示网页HTML内容
    
    Args:
        url: 目标URL
        save_to_file: 是否保存到文件
    """
    print("=" * 70)
    print(f"正在获取网页: {url}")
    print("=" * 70)
    
    try:
        # 设置请求头
        ua = UserAgent()
        headers = {
            'User-Agent': ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
        }
        
        # 发送请求
        print("\n>>> 发送HTTP请求...")
        response = requests.get(url, headers=headers, timeout=30)
        response.encoding = response.apparent_encoding  # 自动检测编码
        
        print(f"✅ 状态码: {response.status_code}")
        print(f"✅ 编码: {response.encoding}")
        print(f"✅ 内容长度: {len(response.text)} 字符")
        
        # 解析HTML
        soup = BeautifulSoup(response.text, 'lxml')
        
        # 美化HTML
        pretty_html = soup.prettify()
        
        # 保存到文件
        if save_to_file:
            filename = 'data/html_debug.html'
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(pretty_html)
            print(f"\n✅ HTML已保存到: {filename}")
        
        # 打印HTML结构摘要
        print("\n" + "=" * 70)
        print("HTML结构摘要:")
        print("=" * 70)
        
        # 1. 打印title
        if soup.title:
            print(f"\n📌 网页标题: {soup.title.string}")
        
        # 2. 查找常见的比赛列表容器
        print("\n📌 查找可能的比赛列表容器:")
        print("-" * 70)
        
        # 常见的class名称
        common_classes = [
            'match', 'game', 'event', 'fixture', 'contest',
            'match-item', 'match-list', 'match-row', 'match-box',
            'game-item', 'game-list', 'game-row',
            'list-item', 'table-row', 'data-row'
        ]
        
        found_containers = []
        for class_name in common_classes:
            # 查找包含这些class的元素
            elements = soup.find_all(class_=lambda x: x and class_name in x.lower())
            if elements:
                for elem in elements[:3]:  # 只显示前3个
                    found_containers.append({
                        'tag': elem.name,
                        'class': elem.get('class'),
                        'id': elem.get('id'),
                        'text_preview': elem.get_text(strip=True)[:100]
                    })
        
        if found_containers:
            for i, container in enumerate(found_containers[:10], 1):
                print(f"\n容器 {i}:")
                print(f"  标签: <{container['tag']}>")
                print(f"  class: {container['class']}")
                if container['id']:
                    print(f"  id: {container['id']}")
                print(f"  内容预览: {container['text_preview']}...")
        else:
            print("  未找到常见的比赛列表容器，请手动查看HTML文件")
        
        # 3. 查找表格
        print("\n📌 查找表格 (table):")
        print("-" * 70)
        tables = soup.find_all('table')
        if tables:
            print(f"  找到 {len(tables)} 个表格")
            for i, table in enumerate(tables[:3], 1):
                print(f"\n  表格 {i}:")
                print(f"    class: {table.get('class')}")
                print(f"    id: {table.get('id')}")
                # 显示表头
                thead = table.find('thead')
                if thead:
                    headers = [th.get_text(strip=True) for th in thead.find_all('th')]
                    print(f"    表头: {headers}")
        else:
            print("  未找到表格")
        
        # 4. 显示部分HTML（前1000字符）
        print("\n" + "=" * 70)
        print("HTML内容预览（前1000字符）:")
        print("=" * 70)
        print(pretty_html[:1000])
        print("\n... (更多内容请查看文件: data/html_debug.html)")
        
        # 5. 提供建议
        print("\n" + "=" * 70)
        print("📝 下一步建议:")
        print("=" * 70)
        print("1. 打开浏览器访问该网址，按F12查看开发者工具")
        print("2. 查看生成的 data/html_debug.html 文件")
        print("3. 在浏览器中找到比赛列表的HTML结构")
        print("4. 记录关键的标签、class、id等选择器")
        print("5. 修改 crawler.py 中的解析规则")
        print("=" * 70)
        
        return response.text
        
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """主函数"""
    print("\n" + "=" * 70)
    print("HTML调试工具 - 分析网站结构")
    print("=" * 70)
    
    # 预设的足彩网站URL
    urls = {
        '1': ('500彩票网', 'https://live.500.com/'),
        '2': ('中国足彩网', 'https://www.zgzcw.com/'),
        '3': ('澳客网', 'https://www.okooo.com/'),
        '4': ('自定义URL', None),
    }
    
    print("\n请选择要分析的网站:")
    for key, (name, url) in urls.items():
        if url:
            print(f"  {key}. {name} - {url}")
        else:
            print(f"  {key}. {name}")
    
    choice = input("\n请输入选项 (1/2/3/4): ").strip()
    
    if choice in urls:
        if choice == '4':
            url = input("请输入网址: ").strip()
        else:
            url = urls[choice][1]
        
        if url:
            fetch_and_display_html(url)
        else:
            print("❌ 无效的URL")
    else:
        print("❌ 无效的选项")


if __name__ == '__main__':
    main()

#!/bin/bash

echo "======================================"
echo "足彩爬虫快速启动脚本"
echo "======================================"

# 检查Python版本
echo ""
echo ">>> 检查Python环境..."
python3 --version

# 创建虚拟环境
if [ ! -d "venv" ]; then
    echo ""
    echo ">>> 创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
echo ""
echo ">>> 激活虚拟环境..."
source venv/bin/activate

# 安装依赖
echo ""
echo ">>> 安装依赖包..."
pip install -r requirements.txt

# 复制环境变量文件
if [ ! -f ".env" ]; then
    echo ""
    echo ">>> 创建环境变量文件..."
    cp .env.example .env
fi

# 创建必要目录
echo ""
echo ">>> 创建数据和日志目录..."
mkdir -p data logs

echo ""
echo "======================================"
echo "✅ 环境配置完成！"
echo "======================================"
echo ""
echo "使用方法："
echo "  1. 运行示例: python example.py"
echo "  2. 运行主程序: python main.py"
echo "  3. 指定参数: python main.py --site zgzcw --format json"
echo ""
echo "⚠️  注意："
echo "  - 请先根据目标网站修改 crawler.py 中的解析规则"
echo "  - 查看 README.md 了解详细使用说明"
echo ""

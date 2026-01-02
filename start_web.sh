#!/bin/bash

# 足球数据展示Web服务启动脚本

echo "正在启动足球数据展示系统..."

# 确保data目录存在
mkdir -p data

# 检查依赖
if ! python3 -c "import flask" 2>/dev/null; then
    echo "正在安装Flask依赖..."
    python3 -m pip install flask==2.0.3 werkzeug==2.0.3
fi

# 启动Web服务
python3 web_app.py

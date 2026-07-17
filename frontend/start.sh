#!/bin/bash
# 启动前端开发服务器

# 检查是否已安装依赖
if [ ! -d "node_modules" ]; then
    echo "📦 安装依赖中..."
    npm install
fi

echo "🚀 启动前端开发服务器 (端口 3000)"
echo "📱 访问地址: http://localhost:3000"
echo ""
npm run dev

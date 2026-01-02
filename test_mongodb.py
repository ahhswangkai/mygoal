#!/usr/bin/env python3
"""
MongoDB连接测试脚本
"""
from pymongo import MongoClient
import sys
import certifi

def test_mongodb_connection(uri='mongodb://localhost:27017/'):
    """测试MongoDB连接"""
    try:
        print("正在连接 MongoDB...")
        print(f"连接字符串: {uri}")
        
        # 连接 MongoDB（设置较短的超时时间）
        if uri.startswith('mongodb+srv') or 'tls=true' in uri:
            client = MongoClient(uri, serverSelectionTimeoutMS=3000, tlsCAFile=certifi.where())
        else:
            client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        
        # 测试连接
        client.admin.command('ping')
        
        # 获取版本信息
        server_info = client.server_info()
        
        print("\n" + "="*50)
        print("✅ MongoDB 连接成功！")
        print("="*50)
        print(f"\n📌 版本: {server_info['version']}")
        print(f"📌 主机: {uri}")
        
        # 测试数据库操作
        print("\n正在测试数据库操作...")
        db = client.test_database
        collection = db.test_collection
        
        # 插入测试数据
        test_doc = {
            "test": "success",
            "message": "MongoDB is working!"
        }
        result = collection.insert_one(test_doc)
        print(f"✅ 数据插入成功: ID = {result.inserted_id}")
        
        # 查询测试
        found = collection.find_one({"test": "success"})
        print(f"✅ 数据查询成功: {found['message']}")
        
        # 清理测试数据
        delete_result = collection.delete_many({})
        print(f"✅ 清理测试数据: 删除了 {delete_result.deleted_count} 条")
        
        # 列出所有数据库
        print("\n📊 当前数据库列表:")
        databases = client.list_database_names()
        for db_name in databases:
            print(f"  - {db_name}")
        
        client.close()
        
        print("\n" + "="*50)
        print("🎉 MongoDB 工作正常！可以开始使用了。")
        print("="*50)
        
        return True
        
    except Exception as e:
        print("\n" + "="*50)
        print("❌ MongoDB 连接失败")
        print("="*50)
        print(f"\n错误信息: {str(e)}")
        print("\n💡 可能的原因:")
        print("1. MongoDB 服务未启动")
        print("   - Docker: docker start mongodb")
        print("   - macOS: mongod --dbpath /usr/local/var/mongodb --fork")
        print("   - 或查看 INSTALL_MONGODB.md 获取安装帮助")
        print("\n2. 连接字符串不正确")
        print(f"   当前使用: {uri}")
        print("   请检查 .env 文件中的 MONGODB_URI 配置")
        print("\n3. 防火墙阻止连接")
        print("   确保端口 27017 未被阻止")
        print("\n4. MongoDB 未安装")
        print("   请参考 INSTALL_MONGODB.md 选择安装方案")
        
        print("\n⚠️  如果暂时不想使用 MongoDB，可以直接运行 Web 服务")
        print("   项目会自动降级到文件存储模式：")
        print("   $ python3 web_app.py")
        
        return False


def main():
    """主函数"""
    print("\n" + "="*50)
    print("MongoDB 连接测试工具")
    print("="*50)
    
    # 尝试从 .env 读取配置
    uri = 'mongodb://localhost:27017/'
    try:
        from dotenv import load_dotenv
        import os
        load_dotenv()
        env_uri = os.getenv('MONGODB_URI')
        if env_uri:
            uri = env_uri
            print(f"\n📝 从 .env 文件读取配置")
    except:
        print(f"\n📝 使用默认配置")
    
    # 测试连接
    success = test_mongodb_connection(uri)
    
    if success:
        print("\n✅ 下一步:")
        print("1. 导入现有数据: python3 migrate_data.py import-all")
        print("2. 启动 Web 服务: python3 web_app.py")
        print("3. 访问界面: http://127.0.0.1:5001")
    else:
        print("\n📚 需要帮助？")
        print("- 查看安装指南: cat INSTALL_MONGODB.md")
        print("- 或直接使用文件存储模式（无需 MongoDB）")
        sys.exit(1)


if __name__ == '__main__':
    main()

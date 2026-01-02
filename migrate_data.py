#!/usr/bin/env python3
"""
数据迁移工具：将JSON文件数据导入MongoDB
"""
import sys
import json
import os
import glob
from db_storage import MongoDBStorage
from utils import setup_logger


def import_json_to_mongodb(json_file_path):
    """
    将JSON文件导入MongoDB
    
    Args:
        json_file_path: JSON文件路径
    """
    logger = setup_logger()
    
    try:
        # 连接MongoDB
        logger.info("正在连接MongoDB...")
        storage = MongoDBStorage()
        
        # 读取JSON文件
        logger.info(f"正在读取文件: {json_file_path}")
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 确保数据是列表
        if not isinstance(data, list):
            logger.error("JSON文件格式错误，应该是数组格式")
            return False
        
        logger.info(f"读取到 {len(data)} 条记录")
        
        # 批量导入
        logger.info("开始导入数据到MongoDB...")
        count = storage.save_matches(data)
        
        logger.info(f"✅ 成功导入 {count}/{len(data)} 条记录")
        
        # 显示统计信息
        stats = storage.get_stats()
        logger.info("\n数据库统计信息:")
        logger.info(f"  总比赛数: {stats['total_matches']}")
        logger.info(f"  联赛数: {stats['total_leagues']}")
        logger.info(f"  按状态统计: {stats['status_stats']}")
        
        storage.close()
        return True
        
    except FileNotFoundError:
        logger.error(f"文件不存在: {json_file_path}")
        return False
    except json.JSONDecodeError as e:
        logger.error(f"JSON格式错误: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"导入失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def import_all_json_files(data_dir='./data'):
    """
    导入data目录下所有JSON文件
    
    Args:
        data_dir: 数据目录
    """
    logger = setup_logger()
    
    # 查找所有JSON文件
    json_pattern = os.path.join(data_dir, '*.json')
    json_files = glob.glob(json_pattern)
    
    if not json_files:
        logger.warning(f"在 {data_dir} 目录下未找到JSON文件")
        return
    
    logger.info(f"找到 {len(json_files)} 个JSON文件")
    
    success_count = 0
    for json_file in json_files:
        logger.info(f"\n处理文件: {os.path.basename(json_file)}")
        if import_json_to_mongodb(json_file):
            success_count += 1
    
    logger.info(f"\n导入完成！成功: {success_count}/{len(json_files)}")


def export_mongodb_to_json(output_file='export_matches.json'):
    """
    从MongoDB导出数据到JSON文件
    
    Args:
        output_file: 输出文件路径
    """
    logger = setup_logger()
    
    try:
        # 连接MongoDB
        logger.info("正在连接MongoDB...")
        storage = MongoDBStorage()
        
        # 获取所有比赛
        logger.info("正在导出数据...")
        matches = storage.get_matches()
        
        if not matches:
            logger.warning("数据库中没有数据")
            return False
        
        # 写入JSON文件
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(matches, f, ensure_ascii=False, indent=2, default=str)
        
        logger.info(f"✅ 成功导出 {len(matches)} 条记录到 {output_file}")
        
        storage.close()
        return True
        
    except Exception as e:
        logger.error(f"导出失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def clear_database():
    """清空MongoDB数据库（慎用）"""
    logger = setup_logger()
    
    # 二次确认
    confirm = input("⚠️  确定要清空所有数据吗？此操作不可恢复！(yes/no): ")
    if confirm.lower() != 'yes':
        logger.info("操作已取消")
        return
    
    try:
        storage = MongoDBStorage()
        storage.clear_all_data()
        logger.info("✅ 数据库已清空")
        storage.close()
    except Exception as e:
        logger.error(f"清空数据库失败: {str(e)}")


def show_stats():
    """显示数据库统计信息"""
    logger = setup_logger()
    
    try:
        storage = MongoDBStorage()
        stats = storage.get_stats()
        
        print("\n" + "="*50)
        print("MongoDB 数据库统计信息")
        print("="*50)
        print(f"\n📊 总比赛数: {stats['total_matches']}")
        print(f"🏆 联赛数: {stats['total_leagues']}")
        
        print("\n📈 按状态统计:")
        for status, count in sorted(stats['status_stats'].items(), key=lambda x: x[1], reverse=True):
            print(f"  {status}: {count}")
        
        print("\n🏅 按联赛统计 (前10):")
        sorted_leagues = sorted(stats['league_stats'].items(), key=lambda x: x[1], reverse=True)[:10]
        for league, count in sorted_leagues:
            print(f"  {league}: {count}")
        
        print("\n" + "="*50 + "\n")
        
        storage.close()
        
    except Exception as e:
        logger.error(f"获取统计信息失败: {str(e)}")


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("""
使用方法:

  1. 导入单个JSON文件:
     python3 migrate_data.py import <json_file>
     
  2. 导入data目录下所有JSON文件:
     python3 migrate_data.py import-all
     
  3. 从MongoDB导出到JSON:
     python3 migrate_data.py export [output_file]
     
  4. 显示统计信息:
     python3 migrate_data.py stats
     
  5. 清空数据库:
     python3 migrate_data.py clear

示例:
  python3 migrate_data.py import data/matches_20251201.json
  python3 migrate_data.py import-all
  python3 migrate_data.py export my_export.json
  python3 migrate_data.py stats
        """)
        return
    
    command = sys.argv[1]
    
    if command == 'import':
        if len(sys.argv) < 3:
            print("❌ 请指定要导入的JSON文件")
            return
        import_json_to_mongodb(sys.argv[2])
        
    elif command == 'import-all':
        data_dir = sys.argv[2] if len(sys.argv) > 2 else './data'
        import_all_json_files(data_dir)
        
    elif command == 'export':
        output_file = sys.argv[2] if len(sys.argv) > 2 else 'export_matches.json'
        export_mongodb_to_json(output_file)
        
    elif command == 'stats':
        show_stats()
        
    elif command == 'clear':
        clear_database()
        
    else:
        print(f"❌ 未知命令: {command}")
        print("支持的命令: import, import-all, export, stats, clear")


if __name__ == '__main__':
    main()

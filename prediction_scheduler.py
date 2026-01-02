"""
预测与复盘定时任务调度器
每天定时预测未开始的比赛，并复盘已完场的比赛
"""
from datetime import datetime, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from db_storage import MongoDBStorage
from prediction_engine import PredictionEngine
from prediction_review import PredictionReviewer
from utils import setup_logger


def daily_prediction_task():
    """每日预测任务 - 预测未开始的比赛"""
    logger = setup_logger()
    logger.info("=" * 80)
    logger.info("开始执行每日预测任务")
    logger.info(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)
    
    try:
        storage = MongoDBStorage()
        engine = PredictionEngine()
        
        # 获取未开始的比赛
        upcoming_matches = storage.get_matches(filters={'status': 0})
        
        if not upcoming_matches:
            logger.info("当前没有未开始的比赛需要预测")
            return
        
        logger.info(f"找到 {len(upcoming_matches)} 场未开始的比赛")
        
        predicted_count = 0
        skipped_count = 0
        
        for match in upcoming_matches:
            match_id = match.get('match_id')
            home_team = match.get('home_team')
            away_team = match.get('away_team')
            
            # 检查是否有赔率数据
            if not match.get('euro_current_win') and not match.get('euro_initial_win'):
                logger.warning(f"跳过比赛 {match_id} ({home_team} vs {away_team}): 无赔率数据")
                skipped_count += 1
                continue
            
            try:
                # 执行预测
                prediction = engine.predict_match(match)
                
                if prediction:
                    # 保存预测结果
                    storage.save_prediction(prediction)
                    predicted_count += 1
                    logger.info(
                        f"[{predicted_count}/{len(upcoming_matches)}] "
                        f"{home_team} vs {away_team} - "
                        f"预测: {prediction['win_prediction']} "
                        f"(置信度{prediction['win_confidence']:.1f}%)"
                    )
                else:
                    logger.warning(f"预测失败: {match_id}")
                    skipped_count += 1
                    
            except Exception as e:
                logger.error(f"预测比赛 {match_id} 时出错: {str(e)}")
                skipped_count += 1
        
        logger.info("\n" + "=" * 80)
        logger.info("每日预测任务完成")
        logger.info(f"成功预测: {predicted_count} 场")
        logger.info(f"跳过: {skipped_count} 场")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"每日预测任务执行失败: {str(e)}")


def daily_review_task():
    """每日复盘任务 - 复盘已完场的比赛"""
    logger = setup_logger()
    logger.info("=" * 80)
    logger.info("开始执行每日复盘任务")
    logger.info(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)
    
    try:
        reviewer = PredictionReviewer()
        
        # 复盘所有已完场但未复盘的比赛
        results = reviewer.review_all_finished_matches()
        
        if not results:
            logger.info("当前没有需要复盘的比赛")
            return
        
        logger.info(f"\n复盘了 {len(results)} 场比赛:")
        
        # 统计准确率
        total_accuracy = sum(r.get('accuracy', 0) for r in results) / len(results)
        win_correct = sum(1 for r in results if r.get('win_correct'))
        asian_correct = sum(1 for r in results if r.get('asian_correct'))
        ou_correct = sum(1 for r in results if r.get('ou_correct'))
        
        logger.info(f"\n本次复盘统计:")
        logger.info(f"  总体准确度: {total_accuracy:.1f}%")
        logger.info(f"  胜负预测准确率: {win_correct/len(results)*100:.1f}% ({win_correct}/{len(results)})")
        logger.info(f"  亚盘预测准确率: {asian_correct/len(results)*100:.1f}% ({asian_correct}/{len(results)})")
        logger.info(f"  大小球预测准确率: {ou_correct/len(results)*100:.1f}% ({ou_correct}/{len(results)})")
        
        # 生成汇总报告
        summary = reviewer.generate_summary_report(days=7)
        
        if summary:
            logger.info(f"\n{summary['period']} 预测总结:")
            logger.info(f"  总预测场次: {summary['total_matches']}")
            logger.info(f"  胜负准确率: {summary['win_accuracy']:.1f}%")
            logger.info(f"  亚盘准确率: {summary['asian_accuracy']:.1f}%")
            logger.info(f"  大小球准确率: {summary['ou_accuracy']:.1f}%")
            logger.info(f"  平均准确度: {summary['avg_accuracy']:.1f}%")
            
            logger.info("\n各联赛表现:")
            for league, stats in summary['league_stats'].items():
                total = stats['total']
                logger.info(
                    f"  {league}: "
                    f"胜负{stats['win_correct']/total*100:.0f}% "
                    f"亚盘{stats['asian_correct']/total*100:.0f}% "
                    f"大小球{stats['ou_correct']/total*100:.0f}% "
                    f"({total}场)"
                )
        
        logger.info("\n" + "=" * 80)
        logger.info("每日复盘任务完成")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"每日复盘任务执行失败: {str(e)}")


def main():
    """
    主函数 - 设置定时任务
    """
    logger = setup_logger()
    logger.info("=" * 80)
    logger.info("预测与复盘定时任务系统启动")
    logger.info("=" * 80)
    
    scheduler = BlockingScheduler()
    
    # 立即执行一次预测和复盘
    logger.info("\n>>> 立即执行首次预测...")
    daily_prediction_task()
    
    logger.info("\n>>> 立即执行首次复盘...")
    daily_review_task()
    
    # 设置定时任务
    # 每天早上8点执行预测任务（预测当天和未来的比赛）
    scheduler.add_job(
        daily_prediction_task,
        CronTrigger(hour=8, minute=0),
        id='daily_prediction',
        name='每日预测任务',
        replace_existing=True
    )
    logger.info("\n已设置定时预测任务: 每天早上8:00")
    
    # 每天下午14点执行预测任务（更新即时赔率后的预测）
    scheduler.add_job(
        daily_prediction_task,
        CronTrigger(hour=14, minute=0),
        id='afternoon_prediction',
        name='下午预测更新',
        replace_existing=True
    )
    logger.info("已设置定时预测任务: 每天下午14:00")
    
    # 每天晚上22点执行预测任务（临盘预测）
    scheduler.add_job(
        daily_prediction_task,
        CronTrigger(hour=22, minute=0),
        id='evening_prediction',
        name='晚间临盘预测',
        replace_existing=True
    )
    logger.info("已设置定时预测任务: 每天晚上22:00")
    
    # 每天凌晨3点执行复盘任务（大部分比赛已完场）
    scheduler.add_job(
        daily_review_task,
        CronTrigger(hour=3, minute=0),
        id='daily_review',
        name='每日复盘任务',
        replace_existing=True
    )
    logger.info("已设置定时复盘任务: 每天凌晨3:00")
    
    # 每天上午10点执行复盘任务（复盘凌晨结束的比赛）
    scheduler.add_job(
        daily_review_task,
        CronTrigger(hour=10, minute=0),
        id='morning_review',
        name='上午复盘任务',
        replace_existing=True
    )
    logger.info("已设置定时复盘任务: 每天上午10:00")
    
    logger.info("\n" + "=" * 80)
    logger.info("所有定时任务已设置完成")
    logger.info("按 Ctrl+C 停止任务")
    logger.info("=" * 80)
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("\n定时任务已停止")


if __name__ == '__main__':
    main()

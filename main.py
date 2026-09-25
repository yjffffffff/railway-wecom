#!/usr/bin/env python3
"""
主入口：单次运行模式，适配 Railway Cron / 手动触发
用法: python main.py --date 2026-09-24
"""
import argparse
import logging
import sys
import os
from datetime import datetime, date

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_loader import load_config
from data_collector import DataCollector
from stock_selector import StockSelector
from push_engine import WeComPusher, TriggerEngine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description='冰雪/旅游周期股触发推送')
    parser.add_argument('--date', type=str, help='目标交易日 YYYY-MM-DD，默认最近交易日')
    parser.add_argument('--mode', choices=['scan', 'test'], default='scan',
                        help='scan=完整扫描推送, test=仅打印不推送')
    parser.add_argument('--force', action='store_true', help='忽略频控强制推送')
    return parser.parse_args()


def main():
    args = parse_args()
    
    # 解析日期
    if args.date:
        try:
            target_date = datetime.strptime(args.date, '%Y-%m-%d').date()
        except ValueError:
            logger.error("日期格式错误，应为 YYYY-MM-DD")
            return 1
    else:
        target_date = date.today()
        # 简单判断是否为交易日（周末跳过）
        if target_date.weekday() >= 5:
            logger.info(f"{target_date} 非交易日，退出")
            return 0
    
    logger.info(f"=== 启动扫描: {target_date} ===")
    
    # 加载配置
    config = load_config()
    
    # 初始化组件
    collector = DataCollector(config)
    selector = StockSelector(config, collector, target_date)
    pusher = WeComPusher(config)
    engine = TriggerEngine(config, collector, selector, pusher)
    
    # 测试模式：替换 pusher.push 为打印
    if args.mode == 'test':
        def test_push(msg):
            logger.info(f"[TEST PUSH] {msg.msg_type} | {msg.title} | {msg.content}")
            print(f"\n--- 推送预览 ---\n{msg}")
            return True
        pusher.push = test_push
        logger.info("测试模式：不发送实际推送")
    
    # 预热：获取全市场数据缓存
    logger.info("预热数据缓存...")
    collector.warmup_cache(target_date)
    
    # 运行触发检查
    logger.info("运行触发引擎...")
    engine.run_all_checks()
    
    logger.info("=== 扫描完成 ===")
    return 0


if __name__ == '__main__':
    sys.exit(main())
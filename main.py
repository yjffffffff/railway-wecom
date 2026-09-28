#!/usr/bin/env python3
"""
主入口：单次运行模式，适配 Railway Cron / 手动触发
用法: python main.py --date 2026-09-24
"""
import argparse
import logging
import sys
import os
from datetime import datetime, date, timedelta

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


def resolve_trade_date() -> date:
    """未指定日期时自动定位最近交易日
    优先级：今天(若为交易日) > 上一交易日
    容错：最近10个交易日内回溯，遇数据源无数据自动再回溯
    """
    import akshare as ak
    import pandas as pd

    today = date.today()
    # 若今天已过收盘时间(15:30)，仍视为当日可用
    cutoff = datetime.now().time()
    if today.weekday() < 5 and cutoff.hour < 15:
        logger.info(f"今日 {today} 尚未收盘，跳过")
    else:
        try:
            cal = ak.tool_trade_date_hist_sina()
            days = pd.to_datetime(cal['trade_date']).dt.date
            past = days[days <= today]
            if len(past) == 0:
                return today
            picked = past.iloc[-1]
            logger.info(f"自动定位最近交易日: {picked} (今天 {today})")
            return picked
        except Exception as e:
            logger.warning(f"交易日历获取失败({e})，回退到最近工作日")

    # 兜底：跳过周末
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def parse_args():
    parser = argparse.ArgumentParser(description='A股多主线轮动与周期量化触发推送')
    parser.add_argument('--date', type=str, help='目标交易日 YYYY-MM-DD，默认最近交易日')
    parser.add_argument('--mode', choices=['scan', 'test', 'rebalance'], default='scan',
                        help='scan=完整扫描推送, test=仅打印不推送, rebalance=全市场动态轮换更新股票池')
    parser.add_argument('--force', action='store_true', help='忽略频控强制推送')
    parser.add_argument('--no-notify', action='store_true', help='动态轮换时不推送企微通知')
    return parser.parse_args()


def main():
    args = parse_args()

    # 若为 rebalance 模式，执行全市场白名单自动轮换
    if args.mode == 'rebalance':
        logger.info("=== 启动全市场自适应板块与股票池动态轮换 ===")
        from universe_updater import run_update
        ok = run_update(notify_wecom=not args.no_notify)
        return 0 if ok else 1
    
    # 解析日期
    if args.date:
        try:
            target_date = datetime.strptime(args.date, '%Y-%m-%d').date()
        except ValueError:
            logger.error("日期格式错误，应为 YYYY-MM-DD")
            return 1
    else:
        target_date = resolve_trade_date()
    
    logger.info(f"=== 启动扫描: {target_date} ===")
    
    # 加载配置
    config = load_config()
    
    # 初始化组件
    collector = DataCollector(config)
    selector = StockSelector(config, collector)
    pusher = WeComPusher(config)
    engine = TriggerEngine(config, collector, selector, pusher, force=args.force)
    
    # 测试模式：替换 pusher.push 为打印
    if args.mode == 'test':
        def test_push(msg, force=False):
            logger.info(f"[TEST PUSH] {msg.msg_type} | {msg.title} | {msg.content}")
            print(f"\n--- 推送预览 ---\n{msg}")
            return True
        pusher.push = test_push
        logger.info("测试模式：不发送实际推送")
    
    # 运行触发检查
    logger.info("运行触发引擎...")
    engine.run_all_checks()
    
    logger.info("=== 扫描完成 ===")
    return 0


if __name__ == '__main__':
    sys.exit(main())
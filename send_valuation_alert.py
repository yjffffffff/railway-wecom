"""
宽基估值分位加减仓提醒（收盘前约15分钟推送企微）

科创50 / 深证100 / 中证500 / 创业板50 / 中证A50 / 纳指100 的 PE、PB 历史百分位，
结合近3月分位变化给出 加仓 / 减仓 / 不动 建议。

定时稳健性：GitHub Actions 定时有 3~10 分钟（高峰更久）排队延迟，若直接定点推送可能滑到收盘后。
故工作流提前触发，脚本先取数、再「等待到目标时刻（默认北京时间 14:45，即收盘前15分钟）」才推送，
使实际推送时刻锚定在收盘前15分钟，不受排队延迟影响。

用法:
   python send_valuation_alert.py            # 取数并等待到目标时刻推送企微
   python send_valuation_alert.py --dry-run  # 仅本地预览，不推送（也不等待）
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta

from config_loader import load_config
from push_engine import WeComPusher
from index_valuation import fetch_all, build_markdown

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))  # 北京时间

# 目标推送时刻（北京时间 HH:MM），默认 14:45 = A股15:00收盘前15分钟
PUSH_AT = os.environ.get("VALUATION_PUSH_AT", "14:45")
# 最多等待分钟数（超过则不等待，立即推送，避免浪费 runner / 拖到收盘后）
MAX_WAIT_MIN = int(os.environ.get("VALUATION_MAX_WAIT_MIN", "45"))


def _wait_until_push_at() -> None:
    """等待到目标推送时刻（北京时间）；已过目标或等待过久则立即返回"""
    try:
        th, tm = map(int, PUSH_AT.split(":"))
    except Exception:
        logger.warning(f"VALUATION_PUSH_AT 格式错误: {PUSH_AT}，跳过等待")
        return
    now = datetime.now(CST)
    target = now.replace(hour=th, minute=tm, second=0, microsecond=0)
    wait_sec = (target - now).total_seconds()
    if wait_sec <= 0:
        logger.info(f"当前 {now:%H:%M:%S} 已过目标 {PUSH_AT}，立即推送")
        return
    if wait_sec > MAX_WAIT_MIN * 60:
        logger.info(f"距目标 {PUSH_AT} 还有 {wait_sec/60:.0f} 分钟（超过上限），立即推送")
        return
    logger.info(f"当前 {now:%H:%M:%S}，等待 {wait_sec/60:.1f} 分钟至目标时刻 {PUSH_AT}（收盘前15分钟）再推送")
    time.sleep(wait_sec)


def run(dry_run: bool = False) -> bool:
    print("正在获取宽基指数估值分位...")
    rows = fetch_all()
    md = build_markdown(rows)

    print("--- 预览推送内容 ---")
    print(md)

    if dry_run:
        print("\n[dry-run] 未实际推送（也未等待）")
        return True

    # 等待到收盘前目标时刻再推送，抵消 GitHub Actions 排队延迟
    _wait_until_push_at()

    cfg = load_config()
    pusher = WeComPusher(cfg)
    ok = pusher._push_via_webhook(md)
    print(f"\n企微 Webhook 推送结果: {ok}")
    return ok


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='宽基估值分位加减仓提醒推送')
    parser.add_argument('--dry-run', action='store_true', help='仅打印预览，不推送企微')
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        handlers=[logging.StreamHandler()]
    )
    # Windows 控制台默认 GBK，emoji 报告需强制 UTF-8 输出
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    sys.exit(0 if run(dry_run=args.dry_run) else 1)

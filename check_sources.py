#!/usr/bin/env python3
"""
数据源连通性自检脚本
用法: python check_sources.py [股票代码 ...]    # 默认 000962 600738

在 Railway / GitHub Actions 等云端环境定位“获取不了股票数据”的问题：
打印每个数据源（腾讯直连、东财直连、akshare 东财/新浪、交易日历）的可用性与耗时。
"""
import logging
import sys
import time

logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

SYMBOLS = sys.argv[1:] or ['000962', '600738']


def timed(name, fn):
    t0 = time.time()
    try:
        result = fn()
        detail = ''
        ok = result is not None
        if hasattr(result, 'empty'):
            ok = not result.empty
            detail = f'{0 if result.empty else len(result)} 行'
        elif isinstance(result, (list, tuple)):
            ok = len(result) > 0
            detail = f'{len(result)} 条'
        elif isinstance(result, dict):
            ok = len(result) > 0
            detail = f'{len(result)} 字段'
        print(f"[{'OK  ' if ok else 'EMPTY'}] {name:<18} {time.time() - t0:6.2f}s  {detail}")
        return result
    except Exception as e:
        print(f"[FAIL] {name:<18} {time.time() - t0:6.2f}s  {type(e).__name__}: {e}")
        return None


def main():
    print(f"检测股票: {', '.join(SYMBOLS)}\n")
    symbol = SYMBOLS[0]

    import tencent_client as tx
    timed('腾讯日线', lambda: tx.daily_to_dataframe(symbol, 60))
    timed('腾讯实时', lambda: tx.realtime_to_dataframe(SYMBOLS))

    import eastmoney_client as em
    timed('东财日线', lambda: em.daily_to_dataframe(symbol, 60))
    timed('东财实时', lambda: em.realtime_to_dataframe(SYMBOLS))

    import akshare as ak
    timed('akshare东财日线', lambda: ak.stock_zh_a_hist(
        symbol=symbol, period='daily', adjust='qfq', timeout=15))
    timed('akshare新浪日线', lambda: ak.stock_zh_a_daily(
        symbol=tx.to_tx_symbol(symbol), adjust='qfq'))
    timed('akshare新浪财务', lambda: ak.stock_financial_abstract(symbol=symbol))
    timed('交易日历(新浪)', lambda: ak.tool_trade_date_hist_sina())

    print("\n提示: 『腾讯日线』或『akshare新浪日线』为 OK 时，工作流即可正常取数；")
    print("      两者都 FAIL 说明运行环境到腾讯/新浪的外网出口被限制。")


if __name__ == '__main__':
    main()

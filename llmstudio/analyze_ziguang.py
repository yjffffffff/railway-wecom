# -*- coding: utf-8 -*-
"""紫光股份(000938)历史价格分析：判断当前价格是否处于低点
多源尝试：东财K线接口(多主机多令牌) + 腾讯接口(备用，全球可访问)"""
import urllib.request
import json
import ssl
import time
import statistics
from datetime import datetime, timezone, timedelta

try:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CST = timezone(timedelta(hours=8))
CONTEXT = ssl.create_default_context()
CONTEXT.check_hostname = False
CONTEXT.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"}

OUT = []


def log(s):
    OUT.append(str(s))


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CONTEXT) as r:
        return r.read().decode("utf-8", errors="replace")


def try_eastmoney_kline():
    """东财历史K线：多主机+多令牌组合尝试"""
    hosts = ["push2his.eastmoney.com", "push2delay.eastmoney.com",
             "92.push2his.eastmoney.com"]
    uts = ["fa5fd1943c67418ea634a5f3508544a5fee1ac",
           "bd1d9ddb04089700cf9c27f6f7426281"]
    for host in hosts:
        for ut in uts:
            try:
                u = (f"https://{host}/api/qt/stock/kline/get?"
                     "secid=0.000938&klt=101&fqt=1&lmt=250&end=20500101"
                     f"&ut={ut}"
                     "&fields1=f1,f2,f3,f4,f5,f6"
                     "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
                d = json.loads(fetch(u))
                kl = (d.get("data") or {}).get("klines", [])
                log(f"[东财 {host} ut={ut[:8]}...] 获取 {len(kl)} 根K线")
                if kl:
                    return kl
            except Exception as e:
                log(f"[东财 {host} ut={ut[:8]}...] 失败: {str(e)[:50]}")
            time.sleep(0.5)
    return None


def try_tencent_kline():
    """腾讯历史K线（全球可访问，无需特殊令牌）
    param: 日K=day, 年数范围; 返回格式 日期,开,收,最高,低,成交量"""
    for prefix in ["web.ifzq.gtimg.cn", "ifzq.gtimg.cn"]:
        try:
            u = (f"https://{prefix}/appstock/app/fqkline/get?"
                 "param=sz000938,day,,,250,qfq")
            d = json.loads(fetch(u))
            data = d.get("data") or {}
            sz = data.get("sz000938") or {}
            # 兼容 qfqday / day 两种key
            kl = sz.get("qfqday") or sz.get("day") or []
            log(f"[腾讯 {prefix}] 获取 {len(kl)} 根K线")
            if kl:
                return kl
        except Exception as e:
            log(f"[腾讯 {prefix}] 失败: {str(e)[:50]}")
    return None


def parse_rows(kl, source):
    """统一解析为 (date, close) 列表"""
    rows = []
    for item in kl:
        if source == "eastmoney":
            p = item.split(",")
            if len(p) > 8:
                rows.append((p[0], float(p[2])))   # 日期, 收盘
        else:  # tencent: ['2026-01-02','30.10','31.00',...]
            if len(item) > 2:
                rows.append((item[0], float(item[2])))
    return rows


def main():
    log(f"分析时间: {datetime.now(CST):%Y-%m-%d %H:%M} 北京时间")
    log("=" * 60)

    kl, source = None, None
    kl_em = try_eastmoney_kline()
    if kl_em:
        kl, source = kl_em, "eastmoney"
    else:
        kl_tx = try_tencent_kline()
        if kl_tx:
            kl, source = kl_tx, "tencent"

    if not kl:
        log("❌ 所有数据源均失败")
        return

    rows = parse_rows(kl, source)
    log(f"数据源: {source} | 有效交易日: {len(rows)}")
    log(f"区间: {rows[0][0]} ~ {rows[-1][0]}")
    log("-" * 60)

    closes = [c for _, c in rows]
    cur = closes[-1]
    cur_date = rows[-1][0]

    # ---- 1. 历史区间统计 ----
    hi = max(closes)
    lo = min(closes)
    hi_date = rows[closes.index(hi)][0]
    lo_date = rows[closes.index(lo)][0]

    # 历史分位（当前价格在区间中的位置）
    percentile = sum(1 for c in closes if c < cur) / len(closes) * 100

    log("【区间统计（近一年）】")
    log(f"  当前收盘({cur_date}): {cur:.2f} 元")
    log(f"  区间最高: {hi:.2f} 元 ({hi_date}) → 距最高回撤 {(cur/hi-1)*100:.1f}%")
    log(f"  区间最低: {lo:.2f} 元 ({lo_date}) → 高于最低 {(cur/lo-1)*100:.1f}%")
    log(f"  历史分位: 当前价格高于区间内 {percentile:.0f}% 的交易日")
    log("")

    # ---- 2. 均线系统 ----
    log("【均线系统】")
    for n, name in [(5, "MA5"), (10, "MA10"), (20, "MA20"),
                    (60, "MA60"), (120, "MA120(半年线)"), (250, "MA250(年线)")]:
        if len(closes) >= n:
            ma = sum(closes[-n:]) / n
            pos = "✅站上" if cur >= ma else "❌跌破"
            log(f"  {name} = {ma:.2f} | 当前价{pos} ({(cur/ma-1)*100:+.1f}%)")
        else:
            log(f"  {name} = 数据不足({len(closes)}日)")
    log("")

    # ---- 3. 各周期表现 ----
    log("【各周期涨跌幅】")
    for n, name in [(5, "近1周"), (10, "近2周"), (20, "近1月"),
                    (60, "近3月"), (120, "近半年")]:
        if len(closes) > n:
            chg = (cur / closes[-n-1] - 1) * 100
            log(f"  {name}: {chg:+.1f}%")
    log("")

    # ---- 4. 近期低点探测（局部最小值，窗口7日） ----
    lows = []
    for i in range(3, len(rows) - 3):
        window = closes[i-3:i+4]
        if closes[i] == min(window):
            lows.append((rows[i][0], closes[i]))
    recent_lows = [l for l in lows if l[0] >= rows[-60][0]] if len(rows) >= 60 else lows
    log("【近3月局部低点】")
    for d, c in recent_lows[-8:]:
        log(f"  {d}: {c:.2f}")
    log("")

    # ---- 5. 结论 ----
    log("=" * 60)
    log("【低点判定结论】")
    verdicts = []
    if percentile <= 20:
        verdicts.append(f"✅ 价格处于近一年低位区（分位{percentile:.0f}%）")
    elif percentile <= 40:
        verdicts.append(f"⚠️ 价格处于近一年中低位（分位{percentile:.0f}%）")
    elif percentile <= 60:
        verdicts.append(f"➖ 价格处于近一年中枢附近（分位{percentile:.0f}%）")
    else:
        verdicts.append(f"❌ 价格处于近一年中高位（分位{percentile:.0f}%）")

    if cur >= (sum(closes[-250:]) / len(closes[-250:]) if len(closes) >= 250 else closes[0]):
        verdicts.append("❌ 仍站在年线上方，非熊市底部形态")
    else:
        verdicts.append("✅ 已跌破年线，进入技术性底部探测区")

    ddrawdown = (cur / hi - 1) * 100
    if ddrawdown <= -30:
        verdicts.append(f"✅ 距年内高点回撤已达 {ddrawdown:.1f}%（深度回调）")
    elif ddrawdown <= -15:
        verdicts.append(f"⚠️ 距年内高点回撤 {ddrawdown:.1f}%（中等回调）")
    else:
        verdicts.append(f"➖ 距年内高点回撤仅 {ddrawdown:.1f}%")

    for v in verdicts:
        log(f"  {v}")

    with open("ziguang_analysis.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("saved to ziguang_analysis.txt")


if __name__ == "__main__":
    main()
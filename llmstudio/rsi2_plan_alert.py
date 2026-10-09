# -*- coding: utf-8 -*-
"""尾盘RSI2策略信号推送（交易日14:50运行，收盘前10分钟）
对 紫光股份/深科技/神州数码 三只股票，推送：
  现价、涨跌幅、RSI2、MA5、ATR14、买入触发价、建议股数、止盈线、止损位，
  以及是否已达买入条件（RSI2<15）
策略规则：RSI2<15超卖买入 / 收盘站上MA5止盈 / 买入价-2ATR止损 / 止损后冷却20日
数据源：腾讯日K(历史) + 东财实时行情(现价)
"""
import urllib.request
import json
import ssl
import sys
import os
import time
from datetime import datetime, timezone, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CST = timezone(timedelta(hours=8))
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 Chrome/120.0"}
UT = "bd1d9ddb04089700cf9c27f6f7426281"
EM_HOSTS = ["push2delay.eastmoney.com", "82.push2.eastmoney.com",
            "push2.eastmoney.com"]
CAPITAL = 100000.0
RSI2_BUY = 15          # 买入阈值

# (腾讯代码, 东财secid, 名称)
# 紫光/深科技/神州三只已于2026-09-23暂停尾盘推送（用户要求），需要时取消注释即可
STOCKS = [
    # ("sz000938", "0.000938", "紫光股份"),
    # ("sz000021", "0.000021", "深科技"),
    # ("sz000034", "0.000034", "神州数码"),
]


def fetch_json(url, retries=4):
    last = None
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception as e:
            last = e
            if k < retries - 1:
                time.sleep(min(2 ** k, 6))
    raise last


def get_kline(tc, days=130):
    """腾讯前复权日K：[[date, open, close, high, low, vol], ...]"""
    d = fetch_json(f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
                   f"param={tc},day,,,{days},qfq")
    sz = (d.get("data") or {}).get(tc) or {}
    return sz.get("qfqday") or sz.get("day") or []


def get_realtime(secid):
    """东财实时行情：(现价, 涨跌幅%)"""
    last = None
    for host in EM_HOSTS:
        try:
            d = fetch_json(f"https://{host}/api/qt/stock/get?secid={secid}"
                           f"&ut={UT}&fltt=2&invt=2"
                           "&fields=f43,f170,f60", retries=2)
            dd = d.get("data") or {}
            if dd.get("f43") not in (None, "-"):
                return float(dd["f43"]), float(dd.get("f170") or 0)
        except Exception as e:
            last = e
    raise last or RuntimeError(f"实时行情失败 {secid}")


def calc_rsi2(closes):
    """2周期RSI（最近两段涨跌：昨变+今变）"""
    n = len(closes)
    if n < 3:
        return 50.0
    gains = losses = 0.0
    for j in range(n - 2, n):
        ch = closes[j] - closes[j - 1]
        if ch > 0:
            gains += ch
        else:
            losses -= ch
    if gains + losses == 0:
        return 50.0
    return 100.0 * gains / (gains + losses)


def calc_atr(highs, lows, closes, period=14):
    """ATR14（与backtest一致）"""
    n = len(closes)
    if n < period + 1:
        return None
    trs = []
    for j in range(n - period, n):
        trs.append(max(highs[j] - lows[j],
                       abs(highs[j] - closes[j - 1]),
                       abs(lows[j] - closes[j - 1])))
    return sum(trs) / period


def solve_trigger(prev_close, d2):
    """解出使今日RSI2<15的收盘价上限
    d2=昨日涨跌额；今日变化d1=P-prev_close
    RSI2<15 ⟺ gains/(gains+losses)<0.15
    """
    if d2 >= 0:
        trig = prev_close - 5.6667 * d2
        return trig, f"需收跌至{trig:.2f}以下"
    else:
        trig = prev_close + 0.1765 * abs(d2)
        return trig, f"收盘<={trig:.2f}即达标"


def push_all(title, text):
    """推送：企业微信 + 飞书 + Bark + Server酱"""
    ok = False
    try:
        from wecom_push import push_wecom
        ok = push_wecom(title, text[:2000]) or ok
    except Exception as e:
        print(f"[wecom] err: {e}")
    try:
        from feishu_push import push_feishu
        ok = push_feishu(title, text[:2000]) or ok
    except Exception as e:
        print(f"[feishu] err: {e}")
    try:
        from bark_push import push_bark
        ok = push_bark(title, text[:400], level="timeSensitive") or ok
    except Exception as e:
        print(f"[bark] err: {e}")
    return ok


def main():
    now = datetime.now(CST)
    today = now.strftime("%Y-%m-%d")
    if not STOCKS:
        print("RSI2策略：监控名单为空（紫光/深科技/神州已于2026-09-23暂停推送），跳过")
        return
    per_stock = CAPITAL / len(STOCKS)
    lines = [f"RSI2策略尾盘信号 | {now:%m-%d %H:%M}",
             f"规则: RSI2<{RSI2_BUY}买入 / 收盘站上MA5止盈 / 买入价-2ATR止损",
             ""]
    triggered = []

    for tc, secid, name in STOCKS:
        try:
            kl = get_kline(tc)
            if len(kl) < 30:
                lines.append(f"【{name}】K线数据不足，跳过")
                continue
            hist = [[r[0], float(r[2]), float(r[3]), float(r[4])] for r in kl]
            price, pct = get_realtime(secid)
            in_trading = now.weekday() <= 4 and 9 <= now.hour < 15

            # 组装含今日现价的收盘序列
            if hist[-1][0] == today:
                closes = [h[1] for h in hist[:-1]] + [price]
                prev_close = hist[-2][1]
                d2 = hist[-2][1] - hist[-3][1]
            elif in_trading:
                closes = [h[1] for h in hist] + [price]
                prev_close = hist[-1][1]
                d2 = hist[-1][1] - hist[-2][1]
            else:
                closes = [h[1] for h in hist]
                prev_close = hist[-1][1]
                d2 = hist[-1][1] - hist[-2][1]

            r2 = calc_rsi2(closes)
            ma5 = sum(closes[-5:]) / 5
            atr14 = calc_atr([h[2] for h in hist], [h[3] for h in hist],
                             [h[1] for h in hist])
            trig, trig_desc = solve_trigger(prev_close, d2)
            shares = int(per_stock / price / 100) * 100
            invest = shares * price
            stop = price - 2 * atr14 if atr14 else None

            hit = r2 < RSI2_BUY
            tag = "🟢已达标" if hit else "⚪未达标"
            lines.append(f"【{name} {tc[2:]}】{price:.2f} ({pct:+.2f}%) {tag}")
            lines.append(f"  RSI2={r2:.0f} | MA5={ma5:.2f} | ATR14={atr14:.2f}"
                         if atr14 else f"  RSI2={r2:.0f} | MA5={ma5:.2f}")
            if hit:
                lines.append(f"  → 触发买入: 收盘前可买 {shares}股"
                             f"（约{invest:,.0f}元）")
                lines.append(f"  → 止盈: 收盘≥{ma5:.2f}（MA5）| "
                             f"止损: 跌破{stop:.2f}" if stop else "")
                triggered.append(name)
            else:
                lines.append(f"  → {trig_desc}（现价{price:.2f}）")
                lines.append(f"  → 若触发: 买{shares}股 "
                             f"止盈{ma5:.2f} 止损{stop:.2f}" if stop else "")
            lines.append("")
        except Exception as e:
            lines.append(f"【{name}】数据异常: {e}")
            lines.append("")

    if triggered:
        lines.insert(2, f"⚡ 今日达标: {'、'.join(triggered)} → 收盘前按纪律执行")
    else:
        lines.insert(2, "今日三只均未达标，空仓等待（不追价）")

    text = "\n".join(l for l in lines if l is not None)
    print(text)
    title = f"RSI2尾盘信号 {now:%m-%d}"
    push_on = os.environ.get("PUSH_ENABLED", "").lower() in ("1", "true", "yes")
    if push_on:
        push_all(title, text)
    else:
        print("(PUSH_ENABLED未开启，跳过推送)")


if __name__ == "__main__":
    main()
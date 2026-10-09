# -*- coding: utf-8 -*-
"""紫光股份(000938)日级交易信号卡：把"分析"变成"交易计划"
数据源：腾讯财经（日K 250日 + 60分钟K 32根）
输出：趋势状态/关键价位/ATR止损/仓位公式/次日触发条件模板"""
import urllib.request
import json
import ssl
import io
from datetime import datetime, timezone, timedelta

try:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CST = timezone(timedelta(hours=8))
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 Chrome/120.0"}

# ==== 参数（按自己情况修改）====
ACCOUNT_CAPITAL = 100000   # 账户总资金(元)
RISK_PER_TRADE = 0.01      # 单笔风险占总资金1%
MAX_POSITION_PCT = 0.30    # 单票仓位上限30%

# 自选股信号卡名单（与fetch_movers.py的WATCHLIST保持一致）
# 腾讯代码: sz=深圳 sh=上海
CARD_STOCKS = [
    {"name": "紫光股份", "code": "000938", "tencent": "sz000938"},
    # 比亚迪/长江电力暂时移出信号卡生成（节省推送额度与复盘篇幅），需要时取消注释
    # {"name": "比亚迪", "code": "002594", "tencent": "sz002594"},
    # {"name": "长江电力", "code": "600900", "tencent": "sh600900"},
]

OUT = []


def log(s):
    OUT.append(str(s))


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def get_day_k(tc):
    d = fetch("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
              f"param={tc},day,,,250,qfq")
    sz = (d.get("data") or {}).get(tc) or {}
    return sz.get("qfqday") or sz.get("day") or []


def get_m60(tc):
    for host in ["web.ifzq.gtimg.cn", "ifzq.gtimg.cn"]:
        try:
            d = fetch(f"https://{host}/appstock/app/kline/mkline?"
                      f"param={tc},m60,,32")
            return ((d.get("data") or {}).get(tc) or {}).get("m60") or []
        except Exception:
            continue
    return []


def f(v):
    try:
        return float(v)
    except Exception:
        return None


def build_card(stock):
    """为单只自选股生成交易信号卡，追加到OUT"""
    _mark_card_start()
    name, tc = stock["name"], stock["tencent"]
    log(f"{name}({stock['code']}) 日级交易信号卡 | {datetime.now(CST):%Y-%m-%d %H:%M}")
    log("=" * 60)

    dk = get_day_k(tc)
    if not dk or len(dk) < 70:
        log("❌ 日K数据不足")
        log("")
        return
    dates = [r[0] for r in dk]
    closes = [f(r[2]) for r in dk]
    highs = [f(r[3]) for r in dk]
    lows = [f(r[4]) for r in dk]
    vols = [f(r[5]) or 0 for r in dk]
    cur = closes[-1]

    # ---- 均线 ----
    def ma(n, arr=None):
        a = arr or closes
        return sum(a[-n:]) / n if len(a) >= n else None

    ma5, ma10, ma20, ma60 = ma(5), ma(10), ma(20), ma(60)
    ma20_prev = ma(20, closes[:-5])   # 5日前的MA20，判断斜率

    # ---- ATR(14) 真实波幅 ----
    trs = []
    for i in range(1, len(dk)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    atr14 = sum(trs[-14:]) / 14 if len(trs) >= 14 else None

    # ---- 摆动高低点（近60日，窗口±3）----
    n0 = max(3, len(dk) - 60)
    piv_hi, piv_lo = [], []
    for i in range(n0, len(dk) - 3):
        if highs[i] == max(highs[i - 3:i + 4]):
            piv_hi.append((dates[i], highs[i]))
        if lows[i] == min(lows[i - 3:i + 4]):
            piv_lo.append((dates[i], lows[i]))
    res = sorted({round(h, 2) for _, h in piv_hi if h > cur}, reverse=True)[:2]
    sup = sorted({round(l, 2) for _, l in piv_lo if l < cur}, reverse=True)[:2]

    # ---- 趋势判定 ----
    above = sum(1 for m in (ma5, ma10, ma20, ma60) if m and cur >= m)
    ma20_slope = (ma20 - ma20_prev) / 5 if (ma20 and ma20_prev) else 0
    if above >= 3 and ma20_slope > 0:
        trend = "多头趋势（均线多头排列+MA20上行）→ 回调到均线附近是买点思维"
        stance = "持股/回调低吸"
    elif above <= 1 and ma20_slope < 0:
        trend = "空头趋势（均线空头+MA20下行）→ 反弹到均线附近是减仓思维"
        stance = "空仓/反弹减仓，禁止抄底"
    else:
        trend = "震荡市（均线纠缠）→ 区间高抛低吸，轻仓试错"
        stance = "区间操作"
    log(f"【趋势状态】{trend}")
    log(f"  现价{cur:.2f} | 站上均线数: {above}/4 | MA20斜率: {ma20_slope:+.3f}/日")
    log("")

    # ---- 关键价位表 ----
    log("【关键价位（次日操作地图）】")
    if res:
        log("  压力位: " + " | ".join(f"{p:.2f}" for p in res))
    else:
        log("  压力位: 现价上方60日内无明确摆动高点")
    if sup:
        log("  支撑位: " + " | ".join(f"{p:.2f}" for p in sup))
    if ma20:
        log(f"  MA20(多空分界): {ma20:.2f}")
    if ma60:
        log(f"  MA60(中线生命线): {ma60:.2f}")
    stop = None
    if atr14:
        # 止损=最近支撑与2×ATR取低者（只用最近支撑，避免取到远期低点导致止损过宽）
        cands = [cur - 2 * atr14]
        if sup:
            cands.append(sup[0])
        stop = min(cands)
        log(f"  建议止损参考: {stop:.2f}（前低与 2×ATR 取低者）")
        log(f"     → 止损距离 {(cur / stop - 1) * 100:.1f}%"
            f"（ATR14={atr14:.2f}, 日均波动约{atr14 / cur * 100:.1f}%）")
    log("")

    # ---- 量能 ----
    v5 = sum(vols[-6:-1]) / 5
    if v5:
        log(f"【量能】今日量/5日均量 = {vols[-1] / v5:.2f}（>1.5放量, <0.7缩量）")
        log("")

    # ---- 60分钟结构（择时参考）----
    m60 = get_m60(tc)
    if m60 and len(m60) >= 10:
        mc = [f(r[2]) for r in m60 if f(r[2])]
        mma5 = sum(mc[-5:]) / 5
        state = "60分级别已企稳" if mc[-1] >= mma5 else "60分级别仍弱，日线信号需60分确认"
        log(f"【60分钟结构】60分MA5={mma5:.2f}，最新60分收{mc[-1]:.2f}（{state}）")
        log("")

    # ---- 仓位计算 ----
    if stop:
        risk_pct = (cur - stop) / cur
        pos_value = ACCOUNT_CAPITAL * RISK_PER_TRADE / risk_pct
        pos_cap = ACCOUNT_CAPITAL * MAX_POSITION_PCT
        final_pos = min(pos_value, pos_cap)
        shares = int(final_pos / cur / 100) * 100
        log("【仓位计算（固定风险法）】")
        log(f"  账户资金 {ACCOUNT_CAPITAL:,.0f}元 | 单笔风险 {RISK_PER_TRADE * 100:.0f}%"
            f" | 止损距离 {risk_pct * 100:.1f}%")
        log(f"  → 理论仓位 {pos_value:,.0f}元，受单票上限约束后 {final_pos:,.0f}元 ≈ {shares}股")
        log(f"  （若触发止损亏损约 {shares * (cur - stop):,.0f} 元"
            f" = 总资金 {shares * (cur - stop) / ACCOUNT_CAPITAL * 100:.1f}%）")
        log("")

    # ---- 次日触发条件模板 ----
    r1 = res[0] if res else (cur + atr14 if atr14 else cur * 1.05)
    log("【次日触发条件模板（照此执行，不盘中临时起意）】")
    log(f"  ▶ 当前策略基调：{stance}")
    log(f"    - 买入触发: 放量(量比>1.2)收复 {ma5:.2f}(MA5) 且60分钟同步转强 → 试仓 1/2 计划仓位")
    log(f"    - 加仓触发: 站稳 {ma20:.2f}(MA20) 且突破 {r1:.2f} → 补足剩余仓位")
    log(f"    - 止损触发: 收盘跌破 {stop:.2f} → 无条件离场，不等反弹")
    log(f"    - 止盈触发: 接近 {r1:.2f} 减半仓，剩余用MA10跟踪止盈")
    log("")

    # ---- 纪律清单 ----
    log("【日级交易纪律（分析之外必须补齐的部分）】")
    log("  1. T+1规则：今天买入明天才能卖，隔夜风险必须计入止损")
    log("  2. 成本：佣金约0.025%×2 + 印花税卖出0.05%，日级频繁调仓成本会侵蚀收益")
    log("  3. 事件日历：财报季提前决定持仓策略、限售解禁日查东财F10")
    log("  4. 每笔交易记录：买卖理由+结果，月底统计胜率与盈亏比——复盘系统已能存档预测，可直接复用")
    log("  5. 单票仓位上限30%：任何信号都不该让你满仓单票")
    log("  6. 9:15-9:25集合竞价先看量价再决定，避免开盘情绪化下单")
    log("")
    log("⚠️ 本卡片是规则模板而非投资建议；信号基于历史数据，不保证未来有效。")
    log("")


def main():
    for stock in CARD_STOCKS:
        build_card(stock)
        _save_card(stock)

# 按股票切片输出：build_card时记录每只股票的OUT起始位置
_card_ranges = []
def _mark_card_start():
    _card_ranges.append(len(OUT))

def _save_card(stock):
    idx = len(_card_ranges) - 1
    start = _card_ranges[idx]
    io.open(f"{stock['name']}_trading_card.txt", "w", encoding="utf-8").write("\n".join(OUT[start:]))
    print(f"saved {stock['name']}_trading_card.txt")
    print("saved")


if __name__ == "__main__":
    main()
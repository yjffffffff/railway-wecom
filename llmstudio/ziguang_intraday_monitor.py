# -*- coding: utf-8 -*-
"""紫光股份(000938) 盘中监控：客观判定"放量收复MA5"是否成立
盘中任意时间运行即可。数据源：东财实时+分时 / 腾讯日K
输出：开盘定性 / 量比 / 分时强弱 / 触发判定（✅/❌，无主观判断）"""
import urllib.request
import urllib.parse
import json
import os
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
UT = "bd1d9ddb04089700cf9c27f6f7426281"
HOSTS = ["82.push2.eastmoney.com", "33.push2.eastmoney.com",
         "17.push2.eastmoney.com", "push2.eastmoney.com",
         "push2delay.eastmoney.com"]

OUT = []
def log(s): OUT.append(str(s))


def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def em_get(path):
    """东财多主机轮询"""
    last = None
    for h in HOSTS:
        try:
            return fetch_json(f"https://{h}{path}")
        except Exception as e:
            last = e
    raise last


def push_serverchan(title, text):
    """Server酱推送：PUSH_ENABLED=true 且有SERVERCHAN_KEY时启用"""
    env = os.environ.get("PUSH_ENABLED", "")
    push_on = env.lower() in ("1", "true", "yes") if env else False
    if not push_on:
        print("(推送未启用：设PUSH_ENABLED=true开启)")
        return
    keys = []
    env_key = os.environ.get("SERVERCHAN_KEY", "")
    if env_key:
        keys = [k.strip() for k in env_key.split(",") if k.strip()]
    else:
        try:
            with open("push_config.json", "r", encoding="utf-8") as f:
                keys = json.load(f).get("serverchan_keys", [])
        except Exception:
            keys = []
    if not keys:
        print("(无SERVERCHAN_KEY，跳过推送)")
        return
    payload = urllib.parse.urlencode({"title": title, "desp": text}).encode("utf-8")
    for i, k in enumerate(keys, 1):
        try:
            urllib.request.urlopen(f"https://sctapi.ftqq.com/{k}.send",
                                   data=payload, timeout=15, context=ctx)
            print(f"push OK ({i})")
        except Exception as e:
            print(f"push FAIL ({i}): {e}")


def get_day_ma5():
    """腾讯日K → MA5 与昨收"""
    d = fetch_json("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
                   "param=sz000938,day,,,10,qfq")
    sz = (d.get("data") or {}).get("sz000938") or {}
    kl = sz.get("qfqday") or sz.get("day") or []
    closes = [float(r[2]) for r in kl if len(r) > 2]
    return (sum(closes[-5:]) / 5) if len(closes) >= 5 else None, closes[-2] if len(closes) >= 2 else None


def main():
    log(f"🔍 紫光股份(000938) 盘中监控 | {datetime.now(CST):%Y-%m-%d %H:%M:%S}")
    log("=" * 58)

    ma5, prev_close = get_day_ma5()
    if not ma5 or not prev_close:
        log("❌ 日K获取失败")
        return

    # ---- 实时快照 ----
    d = em_get(f"/api/qt/stock/get?secid=0.000938&ut={UT}&fltt=2&invt=2"
               "&fields=f43,f46,f47,f50,f170,f171")
    dd = (d.get("data") or {})
    price, open_p, vol, vr = dd.get("f43"), dd.get("f46"), dd.get("f47"), dd.get("f50")
    pct = dd.get("f170")
    if price in (None, "-"):
        log("❌ 实时行情不可用（可能非交易时段）")
        return

    # ---- 1. 开盘定性 ----
    gap = (open_p / prev_close - 1) * 100
    if gap > 2:
        gap_txt = f"大幅高开{gap:+.1f}%（强，但警惕获利盘抛压）"
    elif gap > 0.3:
        gap_txt = f"小幅高开{gap:+.1f}%（偏强）"
    elif gap > -0.3:
        gap_txt = f"平开{gap:+.1f}%（观望，看前30分钟方向）"
    elif gap > -2:
        gap_txt = f"小幅低开{gap:+.1f}%（偏弱）"
    else:
        gap_txt = f"大幅低开{gap:+.1f}%（弱，禁止接飞刀）"
    log("【1. 开盘定性（9:25竞价结果）】")
    log(f"  今开 {open_p} vs 昨收 {prev_close} → {gap_txt}")
    log("")

    # ---- 2. 量比（东财官方口径：现量/过去5日同时段均量）----
    log("【2. 量能（实时量比，客观标准）】")
    log(f"  现价 {price} ({pct}%) | 量比 = {vr}")
    if isinstance(vr, (int, float)):
        if vr >= 1.5:
            log("  → ✅ 显著放量（≥1.5）")
        elif vr >= 1.2:
            log("  → ✅ 有效放量（≥1.2，触发条件达标）")
        elif vr >= 0.8:
            log("  → ➖ 量能平平（0.8~1.2）")
        else:
            log("  → ❌ 缩量（<0.8，反弹大概率是假的）")
    log("")

    # ---- 3. 分时强弱（现价 vs 分时均价线）----
    intraday_state = "未知"
    try:
        t = em_get(f"/api/qt/stock/trends2/get?secid=0.000938&ut={UT}"
                   "&fields1=f1,f2,f3,f7,f8&fields2=f51,f53,f56,f57,f58&iscr=0&ndays=1")
        trends = (t.get("data") or {}).get("trends") or []
        if trends:
            # 每条: "时间,现价,成交量,成交额,均价"
            last_row = trends[-1].split(",")
            cur_p, avg_p = float(last_row[1]), float(last_row[4])
            first15 = trends[:15]
            v15 = sum(float(r.split(",")[2]) for r in first15)
            intraday_state = ("✅ 强（现价在分时均价线上方，盘中买盘承接）"
                              if cur_p >= avg_p else
                              "❌ 弱（现价在分时均价线下方，盘中抛压主导）")
            log("【3. 分时强弱】")
            log(f"  现价 {cur_p:.2f} vs 分时均价线 {avg_p:.2f} → {intraday_state}")
            log(f"  开盘15分钟累计成交 {v15/1e6:.1f}百万股")
            log("")
    except Exception:
        log("【3. 分时强弱】分时数据不可用（可能非交易时段）")
        log("")

    # ---- 4. 触发判定：放量收复MA5 ----
    log("【4. 触发判定：'放量收复MA5'】")
    log(f"  MA5 = {ma5:.2f} | 现价 = {price}")
    cond_price = isinstance(price, (int, float)) and price >= ma5
    cond_vol = isinstance(vr, (int, float)) and vr >= 1.2
    log(f"  条件① 价格站上MA5: {'✅' if cond_price else '❌'}"
        f"（差距 {(price/ma5-1)*100:+.1f}%）")
    log(f"  条件② 量比≥1.2:   {'✅' if cond_vol else '❌'}"
        f"（当前 {vr}）")
    if cond_price and cond_vol:
        log("  ═══════════════════════════")
        log("  🚨 触发！放量收复MA5成立 → 按信号卡执行试仓1/2")
        log("  ═══════════════════════════")
    else:
        miss = []
        if not cond_price:
            miss.append(f"价格还差 {(ma5/price-1)*100:.1f}% 到MA5")
        if not cond_vol:
            miss.append(f"量比还差 {1.2 - (vr if isinstance(vr,(int,float)) else 0):.2f}")
        log(f"  ⏸️ 未触发（{'；'.join(miss)}）→ 继续等待，不预判")

    log("")
    log("💡 用法：盘中任意时刻重跑本脚本即可刷新判定；收盘后跑=当日终判。")
    log("⚠️ 规则模板，不构成投资建议。")

    triggered = (price >= ma5) and (isinstance(vr, (int, float)) and vr >= 1.2)
    io.open("ziguang_intraday.txt", "w", encoding="utf-8").write("\n".join(OUT))
    print("saved")

    now = datetime.now(CST)
    tag = "触发" if triggered else "未触发"
    push_title = f"紫光监控 {now:%H:%M} | {tag}"
    push_text = "\n".join(OUT)
    push_serverchan(push_title, push_text)
    # Bark推送（触发时用timeSensitive突破专注模式；未触发用普通级别）
    try:
        from bark_push import push_bark
        push_bark(push_title, OUT[0][:200] if OUT else "", 
                  level="timeSensitive" if triggered else "active")
    except Exception as _bk:
        print(f"[bark] err: {_bk}")


if __name__ == "__main__":
    main()
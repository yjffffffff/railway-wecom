# -*- coding: utf-8 -*-
"""自选股盘中实时监控（长循环模式）
替代"定时跑一次"模式：单进程在交易时段内每30秒轮询一次，
只在信号状态变化时推送（去重），延迟<30秒。
监控逻辑（每只自选股）：
  ① 急拉/急跌：5分钟涨跌超±3%
  ② 放量：量比≥1.5
  ③ 触及信号卡关键位：突破MA5 / 跌破止损位
推送去重：同一股票同一信号当天只推一次（状态存内存+文件）"""
import urllib.request
import urllib.parse
import json
import os
import ssl
import io
import time
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

# 自选股（与fetch_movers.py的WATCHLIST一致；secid: 0=深 1=沪；腾讯代码sz/sh）
WATCHLIST = [
    # 紫光股份/深科技/神州数码暂时移出监控（2026-09-21暂停实时监控与推送），需要时取消注释即可
    # {"name": "紫光股份", "code": "000938", "secid": "0.000938", "tencent": "sz000938"},
    # {"name": "深科技", "code": "000021", "secid": "0.000021", "tencent": "sz000021"},
    # {"name": "神州数码", "code": "000034", "secid": "0.000034", "tencent": "sz000034"},
    # 比亚迪/长江电力暂时移出监控（节省推送额度），需要时取消注释即可
    # {"name": "比亚迪", "code": "002594", "secid": "0.002594", "tencent": "sz002594"},
    # {"name": "长江电力", "code": "600900", "secid": "1.600900", "tencent": "sh600900"},
]

POLL_INTERVAL = 30          # 轮询间隔(秒)
SPIKE_PCT = 3.0             # 5分钟急拉/急跌阈值(%)
VOL_RATIO_ALERT = 1.5       # 放量告警阈值
STATE_FILE = "knowledge/intraday_state.json"

# 交易时段（北京时间）
def now_cst():
    return datetime.now(CST)

def in_trading_time(t=None):
    t = t or now_cst()
    hm = t.hour * 100 + t.minute
    wd = t.weekday()
    if wd >= 5:
        return False
    return (925 <= hm <= 1135) or (1255 <= hm <= 1505)   # 含集合竞价与收盘缓冲


def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def em_get(path):
    last = None
    for h in HOSTS:
        try:
            return fetch_json(f"https://{h}{path}")
        except Exception as e:
            last = e
    raise last


def get_ma5(tc):
    """腾讯日K → MA5"""
    try:
        d = fetch_json("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
                       f"param={tc},day,,,10,qfq")
        sz = (d.get("data") or {}).get(tc) or {}
        kl = sz.get("qfqday") or sz.get("day") or []
        closes = [float(r[2]) for r in kl if len(r) > 2]
        return (sum(closes[-5:]) / 5) if len(closes) >= 5 else None
    except Exception:
        return None


def get_stop_loss(tencent):
    """从信号卡文件读止损位（收盘跌破 XXXX.XX 行）"""
    name_map = {"sz000938": "紫光股份", "sz000021": "深科技",
                "sz000034": "神州数码", "sz002594": "比亚迪",
                "sh600900": "长江电力"}
    # 注：比亚迪/长江电力已暂停监控，name_map保留以兼容历史信号卡文件
    path = f"{name_map.get(tencent, '')}_trading_card.txt"
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if "止损触发" in line or "建议止损" in line:
                    import re
                    m = re.search(r"(\d+\.\d{2})", line)
                    if m:
                        return float(m.group(1))
    except Exception:
        pass
    return None


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    today = now_cst().strftime("%Y-%m-%d")
    # 只保留当天的状态
    state = {k: v for k, v in state.items() if v.get("date") == today}
    os.makedirs("knowledge", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def push_serverchan(title, text):
    """推送：飞书/企业微信/Bark（2026-09-23起停用Server酱与微信测试号）"""
    env = os.environ.get("PUSH_ENABLED", "")
    push_on = env.lower() in ("1", "true", "yes") if env else False
    if not push_on:
        print(f"[PUSH OFF] {title}")
        return False
    ok = True

    # 飞书/企业微信机器人推送（内容直接在群里展示，不暴露GitHub地址）
    try:
        from feishu_push import push_feishu
        push_feishu(title, text[:2000])
    except Exception as _fs:
        print(f"[feishu] err: {_fs}")
    try:
        from wecom_push import push_wecom
        push_wecom(title, text[:1500])
    except Exception as _wc:
        print(f"[wecom] err: {_wc}")
    # Bark推送（iOS锁屏弹窗，无限量；盘中预警用timeSensitive级别）
    try:
        from bark_push import push_bark
        push_bark(title, text[:500], level="timeSensitive")
    except Exception as _bk:
        print(f"[bark] err: {_bk}")

    return ok


def check_stock(w, state):
    """检查单只股票的所有告警条件，返回告警列表（已去重的新告警）"""
    name = w["name"]
    key = w["code"]
    st = state.setdefault(key, {"date": now_cst().strftime("%Y-%m-%d"), "fired": {}})
    alerts = []
    try:
        d = em_get(f"/api/qt/stock/get?secid={w['secid']}&ut={UT}&fltt=2&invt=2"
                   "&fields=f43,f46,f60,f170,f50")
        dd = d.get("data") or {}
        price, vr, pct = dd.get("f43"), dd.get("f50"), dd.get("f170")
        if price in (None, "-"):
            return alerts
        price = float(price)

        # ① 5分钟急拉/急跌（分时数据对比5分钟前）
        try:
            t = em_get(f"/api/qt/stock/trends2/get?secid={w['secid']}&ut={UT}"
                       "&fields1=f1,f2,f3&fields2=f51,f53&iscr=0&ndays=1")
            trends = (t.get("data") or {}).get("trends") or []
            if len(trends) >= 6:
                cur = float(trends[-1].split(",")[1])
                p5 = float(trends[-6].split(",")[1])
                chg5 = (cur / p5 - 1) * 100 if p5 else 0
                spike_key = f"spike_{int(cur // 0.5)}"   # 按价格0.5元分档去重
                if abs(chg5) >= SPIKE_PCT and not st["fired"].get(spike_key):
                    direction = "急拉" if chg5 > 0 else "急跌"
                    alerts.append(("🚨", f"5分钟{direction} {chg5:+.1f}%，现价{cur:.2f}"))
                    st["fired"][spike_key] = True
        except Exception:
            pass

        # ② 放量告警
        if isinstance(vr, (int, float)) and vr >= VOL_RATIO_ALERT \
                and not st["fired"].get("vol"):
            alerts.append(("📊", f"量比达 {vr}（≥{VOL_RATIO_ALERT}，显著放量），现价{price}（{pct}%）"))

        # ③ 突破/跌破MA5
        ma5 = get_ma5(w["tencent"])
        if ma5 and not st["fired"].get("above_ma5") and price >= ma5 \
                and isinstance(pct, (int, float)) and pct > 0:
            st["fired"]["above_ma5"] = True
            alerts.append(("✅", f"放量站上MA5({ma5:.2f})，现价{price}（+{pct}%）→ 参考信号卡试仓条件"))

        # ④ 跌破止损位
        stop = get_stop_loss(w["tencent"])
        if stop and not st["fired"].get("break_stop") and price < stop:
            alerts.append(("🛑", f"跌破建议止损位{stop}，现价{price} → 按纪律执行止损"))

        # 记录已触发的信号类型（spike按价格分档，可多次触发不同档位）
        if any(a[0] == "📊" for a in alerts):
            st["fired"]["vol"] = True
        if any(a[0] == "🛑" for a in alerts):
            st["fired"]["break_stop"] = True
    except Exception as e:
        print(f"check {name} fail: {e}")
    return alerts


def check_market(state):
    """大盘急跌预警：上证/创业板指日内跌幅超2%推送一次"""
    alerts = []
    try:
        d = em_get(f"/api/qt/ulist.np/get?fltt=2&invt=2&ut={UT}"
                   "&secids=1.000001,0.399006&fields=f2,f3,f12,f14")
        for it in (d.get("data") or {}).get("diff", []):
            code, name, pct = it.get("f12"), it.get("f14"), it.get("f3")
            if not isinstance(pct, (int, float)):
                continue
            key = f"mkt_{code}"
            if pct <= -2.0 and not state["fired"].get(key):
                alerts.append(("🚨", f"**{name}日内急跌 {pct:+.2f}%**（跌破-2%警戒线），"
                                    f"现值{it.get('f2')}。注意控制仓位，检查持仓止损。"))
                state["fired"][key] = True
    except Exception as e:
        print(f"check market fail: {e}")
    return alerts


def main():
    print(f"盘中监控启动 {now_cst():%Y-%m-%d %H:%M:%S} | "
          f"轮询间隔{POLL_INTERVAL}s | 监控{len(WATCHLIST)}只")
    state = load_state()
    check_count = 0

    while True:
        t = now_cst()
        if not in_trading_time(t):
            hm = t.hour * 100 + t.minute
            if hm > 1505 or t.weekday() >= 5:
                print(f"{t:%H:%M} 非交易时段/已收盘，监控退出")
                break
            # 午休：睡到13:00前1分钟
            print(f"{t:%H:%M} 午休中，休眠5分钟")
            time.sleep(300)
            continue

        check_count += 1
        state = load_state()   # 每轮重读，兼容多进程
        for icon, msg in check_market(state):
            ts = now_cst().strftime("%H:%M:%S")
            push_serverchan(f"大盘急跌预警 {ts}", f"{icon} {msg}")
            print(f"{ts} 大盘预警已推送")
        for w in WATCHLIST:
            alerts = check_stock(w, state)
            if alerts:
                body = "\n\n".join(f"{icon} **{w['name']}({w['code']})** {msg}"
                                   for icon, msg in alerts)
                ts = now_cst().strftime("%H:%M:%S")
                push_serverchan(f"{w['name']} 盘中预警 {ts}", body)
                print(f"{ts} {w['name']}: {len(alerts)}条告警已推送")
        save_state(state)

        if check_count % 20 == 0:   # 每10分钟打印一次心跳
            print(f"{t:%H:%M} 心跳：已轮询{check_count}轮")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
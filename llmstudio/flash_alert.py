# -*- coding: utf-8 -*-
"""外围市场突发预警（美股交易时段每30分钟检查一次）
监控：标普500/纳斯达克/道琼斯，跌幅超阈值立即推送（当日每指数只推一次）
状态去重：knowledge/flash_state.json（工作流运行后commit持久化）"""
import urllib.request
import urllib.parse
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
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"}
UT = "bd1d9ddb04089700cf9c27f6f7426281"

# 监控指数（东财secid）与阈值
WATCH = [
    {"name": "标普500", "secid": "100.SPX", "crash": -2.0, "surge": 3.0},
    {"name": "纳斯达克", "secid": "100.NDX", "crash": -2.5, "surge": 3.5},
    {"name": "道琼斯", "secid": "100.DJIA", "crash": -2.0, "surge": 3.0},
]
STATE_FILE = "knowledge/flash_state.json"


def fetch_json(url, retries=3):
    """带重试的JSON抓取：东财接口偶发超时/拒绝连接，重试3次避免工作流误报失败"""
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception as e:
            last_err = e
            print(f"fetch retry {i + 1}/{retries} failed: {e}")
            if i < retries - 1:
                time.sleep(5)
    raise last_err


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs("knowledge", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def push_serverchan(title, text):
    """推送：飞书/企业微信/Bark（Server酱与微信测试号已停用 2026-09-23起）"""
    ok = 0

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
    # Bark推送（iOS锁屏弹窗，无限量；突发预警用timeSensitive级别突破专注模式）
    try:
        from bark_push import push_bark
        push_bark(title, text[:500], level="timeSensitive")
    except Exception as _bk:
        print(f"[bark] err: {_bk}")

    return ok


def main():
    try:
        _run()
    except Exception as e:
        # 兜底：偶发网络/接口错误不应让工作流报failure（30分钟后下一轮会再检查）
        print(f"⚠️ 本轮检查异常（下轮30分钟后自动重试）: {e}")
        sys.exit(0)


def _run():
    now = datetime.now(CST)
    today = now.strftime("%Y-%m-%d")
    state = load_state()
    if state.get("date") != today:
        state = {"date": today, "fired": {}}

    # 美股交易时段（北京时间21:30-04:00，冬令时22:30-05:00，放宽窗口）
    hm = now.hour * 100 + now.minute
    in_us_session = (2130 <= hm <= 2400) or (0 <= hm <= 500)
    if not in_us_session:
        print(f"{now:%H:%M} 非美股交易时段，跳过")
        return

    # 一次请求拿全部指数
    secids = ",".join(w["secid"] for w in WATCH)
    d = fetch_json(f"https://push2.eastmoney.com/api/qt/ulist.np/get?"
                   f"fltt=2&invt=2&ut={UT}&secids={secids}&fields=f2,f3,f12,f14")
    items = {it.get("f12"): it for it in (d.get("data") or {}).get("diff", [])}

    alerts = []
    for w in WATCH:
        it = items.get(w["secid"]) or {}
        pct = it.get("f3")
        if not isinstance(pct, (int, float)):
            continue
        key = w["secid"]
        if pct <= w["crash"] and not state["fired"].get(f"crash_{key}"):
            alerts.append(f"🚨 **{w['name']}暴跌 {pct:+.2f}%**（阈值{w['crash']}%），"
                          f"现值{it.get('f2')}。明早关注A股低开风险，持仓检查止损位。")
            state["fired"][f"crash_{key}"] = True
        elif pct >= w["surge"] and not state["fired"].get(f"surge_{key}"):
            alerts.append(f"🔥 **{w['name']}大涨 {pct:+.2f}%**（阈值+{w['surge']}%），"
                          f"现值{it.get('f2')}。关注相关板块（中概/算力链）明日高开机会与追高风险。")
            state["fired"][f"surge_{key}"] = True

    if alerts:
        body = (f"## 🌙 外围市场突发预警 | {now:%Y-%m-%d %H:%M}\n\n"
                + "\n\n".join(alerts)
                + "\n\n> 仅供参考，不构成投资建议。")
        n = push_serverchan(f"外围突发预警 {now:%H:%M}", body)
        print(f"已推送 {n} 条告警: {len(alerts)}")
    else:
        print(f"{now:%H:%M} 外围正常，无告警")

    save_state(state)


if __name__ == "__main__":
    main()
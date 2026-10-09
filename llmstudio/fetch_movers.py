# -*- coding: utf-8 -*-
"""A股收盘后抓取：指数收盘、异动股(涨幅>5%)、领涨板块、紫光股份数据 -> movers.json
参考AkShare开源实现：ut令牌 + 多镜像主机轮询 + 指数退避重试
主机池含 push2delay（东财海外专用延迟行情主机，适配GitHub Actions美国服务器）"""
import urllib.request
import json
import os
import ssl
import sys
import time
import subprocess
from datetime import datetime, timezone, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CONTEXT = ssl.create_default_context()
CONTEXT.check_hostname = False
CONTEXT.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"}

UT = "bd1d9ddb04089700cf9c27f6f7426281"   # akshare公开使用的固定ut令牌
UT_KLINE = "fa5fd1943c67418ea634a5f3508544a5fee1ac"   # 历史K线专用令牌
_HOSTS_CN = ["82.push2.eastmoney.com", "33.push2.eastmoney.com",
            "17.push2.eastmoney.com", "push2.eastmoney.com",
            "push2delay.eastmoney.com"]   # 末位为海外专用延迟行情主机
# 海外服务器（Railway/GitHub Actions等）设 EM_OVERSEAS=1，延迟主机优先
HOSTS = (_HOSTS_CN[-1:] + _HOSTS_CN[:-1]
         if os.environ.get("EM_OVERSEAS", "") in ("1", "true") else _HOSTS_CN)
KLINE_HOSTS = ["push2his.eastmoney.com", "push2delay.eastmoney.com"]

MIN_CHANGE = 5.0      # 涨幅阈值(%)
MIN_AMOUNT = 2e8      # 成交额下限(2亿元)，过滤流动性差的票
TOP_N = 15            # 最多分析股票数
CST = timezone(timedelta(hours=8))

# 自选股常驻名单（盘后复盘全维度专项分析）
# secid: 0=深圳 1=上海；sector: 供LLM归因参考的公司基本面定位
WATCHLIST = [
    {"name": "紫光股份", "code": "000938", "secid": "0.000938",
     "sector": "ICT设备与解决方案龙头（新华三为核心资产），算力基础设施（服务器/交换机/存储）"},
    # 比亚迪/长江电力暂时移出自选股抓取（节省推送额度与复盘篇幅），需要时取消注释
    # {"name": "比亚迪", "code": "002594", "secid": "0.002594",
    #  "sector": "新能源/汽车/电池龙头，全球插电式汽车销量领先"},
    # {"name": "长江电力", "code": "600900", "secid": "1.600900",
    #  "sector": "电力/水电龙头，长江流域三峡等六座电站，低估值高股息"},
]

LAST_HOST = ""


def get_json(path, hosts=None):
    """多主机轮询+指数退避重试（总窗口约30秒），应对东财限流/断连"""
    global LAST_HOST
    hosts = hosts or HOSTS
    last_err = None
    for i, sleep_s in enumerate((0, 1, 2, 4, 8, 8, 8)):
        host = hosts[i % len(hosts)]
        try:
            url = f"https://{host}{path}"
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15, context=CONTEXT) as r:
                data = json.loads(r.read().decode("utf-8"))
                LAST_HOST = host
                return data
        except Exception as e:
            last_err = e
            if sleep_s:
                time.sleep(sleep_s)
    raise last_err


def num(v):
    try:
        return float(v)
    except Exception:
        return None


# ---------- 1. 指数收盘 ----------
indices = []
try:
    d = get_json("/api/qt/ulist.np/get?fltt=2&invt=2"
                 f"&ut={UT}&secids=1.000001,0.399001,0.399006,1.000688"
                 "&fields=f2,f3,f12,f14")
    for it in (d.get("data") or {}).get("diff", []):
        chg = num(it.get("f3"))
        if chg is not None:
            indices.append({"name": it["f14"], "close": it.get("f2"), "change": chg})
except Exception as e:
    print(f"# INDEX-FAIL: {e}")

# ---------- 2. 异动股 ----------
movers = []
try:
    d = get_json("/api/qt/clist/get?pn=1&pz=100&po=1&np=1"
                 f"&ut={UT}&fltt=2&invt=2&fid=f3"
                 "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
                 "&fields=f2,f3,f6,f8,f12,f14,f62,f100")
    diff = (d.get("data") or {}).get("diff", [])
    print(f"# EM-RAW: {len(diff)} rows from clist")
    for it in diff:
        chg = num(it.get("f3"))
        amt = num(it.get("f6"))
        name = it.get("f14", "") or ""
        if chg is None or chg < MIN_CHANGE:
            continue
        if "ST" in name.upper() or "退" in name:
            continue
        if amt is None or amt < MIN_AMOUNT:
            continue
        inflow = num(it.get("f62"))
        movers.append({
            "code": it.get("f12"), "name": name, "change": chg,
            "price": it.get("f2"),
            "amount_yi": round(amt / 1e8, 2),
            "turnover": it.get("f8"),
            "main_inflow_yi": round(inflow / 1e8, 2) if inflow is not None else None,
            "sector": it.get("f100", "") or "",
        })
        if len(movers) >= TOP_N:
            break
except Exception as e:
    print(f"# MOVERS-FAIL: {e}")

# ---------- 3. 领涨板块 ----------
def boards(fs):
    d = get_json("/api/qt/clist/get?pn=1&pz=6&po=1&np=1"
                 f"&ut={UT}&fltt=2&invt=2&fid=f3&fs={fs}&fields=f3,f14,f128")
    out = []
    for it in (d.get("data") or {}).get("diff", []):
        chg = num(it.get("f3"))
        if chg is None:
            continue
        out.append({"name": it["f14"], "change": chg, "leader": it.get("f128", "") or ""})
    return out


ind_boards, con_boards = [], []
try:
    ind_boards = boards("m:90+t:2")   # 行业板块
except Exception as e:
    print(f"# IND-BOARD-FAIL: {e}")
try:
    con_boards = boards("m:90+t:3")   # 概念板块
except Exception as e:
    print(f"# CON-BOARD-FAIL: {e}")

# ---------- 3.5 自选股常驻数据（盘后复盘全维度专项分析素材） ----------
def fetch_watch_stock(w):
    """抓取单只自选股：实时指标+估值财务(稳定主源) + 历史K线算MA（尽力而为）
    w: WATCHLIST中的一项，含 name/code/secid/sector"""
    # ① 实时指标+估值+财务（稳定主源；量比为官方口径=现量/5日均量）
    d = get_json(f"/api/qt/stock/get?secid={w['secid']}"
                 f"&ut={UT}&fltt=2&invt=2"
                 "&fields=f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f168,f170,f171,"
                 "f116,f117,f162,f163,f164,f167,f173,f184,f186")
    dd = d.get("data") or {}
    if not dd or dd.get("f43") in (None, "-"):
        return {}
    info = {
        "name": w["name"], "code": w["code"], "secid": w["secid"],
        "sector": w["sector"],
        "date": datetime.now(CST).strftime("%Y-%m-%d"),
        "close": dd.get("f43"), "pct": dd.get("f170"),
        "high": dd.get("f44"), "low": dd.get("f45"), "open": dd.get("f46"),
        "volume_hand": dd.get("f47"),
        "amount_yi": round(dd["f48"] / 1e8, 2) if num(dd.get("f48")) else None,
        "vol_ratio": dd.get("f50"),          # 量比（官方口径）
        "turnover": dd.get("f168"),          # 换手率%
        "prev_close": dd.get("f60"),
        # 估值/财务（f116总市值 f117流通市值 f162PE动 f163PE-TTM f164PE静
        # f167PB f173ROE f184营收同比 f186净利同比）
        "mktcap_yi": round(dd["f116"] / 1e8) if num(dd.get("f116")) else None,
        "floatcap_yi": round(dd["f117"] / 1e8) if num(dd.get("f117")) else None,
        "pe_dyn": dd.get("f162"), "pe_ttm": dd.get("f163"),
        "pe_static": dd.get("f164"), "pb": dd.get("f167"),
        "roe": dd.get("f173"), "rev_yoy": dd.get("f184"), "np_yoy": dd.get("f186"),
    }
    # ② 历史K线算均线（尽力而为，接口时好时坏）
    closes = []
    try:
        d2 = get_json(f"/api/qt/stock/kline/get?secid={w['secid']}&klt=101&fqt=1"
                      "&lmt=130&end=20500101"
                      f"&ut={UT_KLINE}"
                      "&fields1=f1,f2,f3,f4,f5,f6"
                      "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                      hosts=KLINE_HOSTS)
        for line in (d2.get("data") or {}).get("klines", []):
            p = line.split(",")
            if len(p) > 2 and num(p[2]) is not None:
                closes.append(num(p[2]))
    except Exception as e:
        print(f"# {w['name']}-KLINE-FAIL: {e}")
    # ③ 累积价格文件（云端git持久化，随使用天数越来越全）
    ppath = f"knowledge/{w['code']}_prices.json"
    hist = []
    try:
        with open(ppath, "r", encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        hist = []
    wd = datetime.now(CST).weekday()
    if info.get("close") and wd <= 4 and \
            not any(h.get("date") == info["date"] for h in hist):
        hist.append({"date": info["date"], "close": info["close"]})
    hist = sorted(hist, key=lambda x: x.get("date", ""))[-130:]
    os.makedirs("knowledge", exist_ok=True)
    with open(ppath, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)
    # K线接口优先（数据全），不足则用累积价格
    all_closes = closes if len(closes) >= 60 else [h["close"] for h in hist]

    def ma(n):
        return round(sum(all_closes[-n:]) / n, 2) if len(all_closes) >= n else None

    ma5, ma10, ma20, ma60 = ma(5), ma(10), ma(20), ma(60)
    c = info["close"]

    def rel(ma_val):
        if ma_val is None:
            return "历史数据累积中"
        return f"站上(MA={ma_val})" if c >= ma_val else f"跌破(MA={ma_val})"

    info.update({
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "vs_ma5": rel(ma5), "vs_ma10": rel(ma10),
        "vs_ma20": rel(ma20), "vs_ma60": rel(ma60),
        "ma_source": "kline_api" if len(closes) >= 60 else "accumulating",
    })
    return info


watchlist = []
for w in WATCHLIST:
    try:
        info = fetch_watch_stock(w)
        if info:
            watchlist.append(info)
            print(f"# WATCH {info['name']}: 收盘{info.get('close')} "
                  f"{(info.get('pct') or 0):+.2f}% 量比{info.get('vol_ratio')} "
                  f"换手{info.get('turnover')}%")
    except Exception as e:
        print(f"# WATCH-{w['name']}-FAIL: {e}")

# ---------- 4. 市场情绪指标（涨跌家数/涨停跌停/炸板率/连板高度） ----------
sentiment = {}
try:
    subprocess.run([sys.executable, "fetch_sentiment.py"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=90,
                   env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    with open("sentiment.json", "r", encoding="utf-8") as f:
        sentiment = json.load(f)
except Exception as e:
    print(f"# SENTIMENT-FAIL: {e}")

result = {
    "generated_at": datetime.now(CST).isoformat(),
    "source": f"eastmoney({LAST_HOST or 'unreachable'})",
    "indices": indices,
    "industry_boards": ind_boards,
    "concept_boards": con_boards,
    "movers": movers,
    "watchlist": watchlist,
    "sentiment": sentiment,
}
with open("movers.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=1)

print(f"# MOVERS: {len(movers)} stocks > {MIN_CHANGE}%  source={result['source']}  saved to movers.json")
for i in indices:
    print(f"  [指数] {i['name']} {i['change']:+.2f}%")
for m in movers:
    inflow = f"主力{m['main_inflow_yi']:+.2f}亿" if m["main_inflow_yi"] is not None else "主力-"
    print(f"  {m['code']} {m['name']} +{m['change']}% 成交{m['amount_yi']}亿 {inflow} [{m['sector']}]")
# -*- coding: utf-8 -*-
"""程序化修改 fetch_movers.py：泛化紫光单股抓取为多自选股抓取（WATCHLIST已存在，只做函数泛化）"""
import io
import ast

path = "fetch_movers.py"
with io.open(path, "r", encoding="utf-8") as f:
    src = f.read()

# 1. 替换 fetch_ziguang 函数为泛化的 fetch_watch_stock
old_fn_start = "# ---------- 3.5 紫光股份(000938)常驻数据（自选股） ----------"
old_fn_end = "ziguang = {}"
idx_start = src.index(old_fn_start)
idx_end = src.index(old_fn_end)

new_fn = '''# ---------- 3.5 自选股常驻数据（盘后复盘全维度专项分析素材） ----------
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
    if info.get("close") and wd <= 4 and \\
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

'''
src = src[:idx_start] + new_fn + src[idx_end:]

# 2. 替换 ziguang 调用块为 watchlist 输出
old_zg = '''ziguang = {}
try:
    ziguang = fetch_ziguang()
    print(f"# ZIGUANG: {ziguang.get('date')} 收盘{ziguang.get('close')} "
          f"{(ziguang.get('pct') or 0):+.2f}% 量比{ziguang.get('vol_ratio')} "
          f"换手{ziguang.get('turnover')}%")
except Exception as e:
    print(f"# ZIGUANG-FAIL: {e}")

result = {
    "generated_at": datetime.now(CST).isoformat(),
    "source": f"eastmoney({LAST_HOST or 'unreachable'})",
    "indices": indices,
    "industry_boards": ind_boards,
    "concept_boards": con_boards,
    "movers": movers,
    "ziguang": ziguang,
}'''
new_zg = '''result = {
    "generated_at": datetime.now(CST).isoformat(),
    "source": f"eastmoney({LAST_HOST or 'unreachable'})",
    "indices": indices,
    "industry_boards": ind_boards,
    "concept_boards": con_boards,
    "movers": movers,
    "watchlist": watchlist,
}'''
assert old_zg in src, "ziguang block not found"
src = src.replace(old_zg, new_zg)

with io.open(path, "w", encoding="utf-8", newline="\n") as f:
    f.write(src)

# 语法检查
ast.parse(src)
print("OK: fetch_movers.py patched + syntax OK")
# -*- coding: utf-8 -*-
"""紫光股份估值+基本面数据补充（东财f10字段，fltt=2直读）"""
import urllib.request
import json
import ssl
import io
import glob
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

OUT = []


def log(s):
    OUT.append(str(s))


# 东财 qt/stock/get 财务字段:
# f116总市值 f117流通市值 f162PE(动) f163PE(TTM) f164PE(静) f167PB
# f173ROE f183营收 f184营收同比 f185净利润 f186净利同比 f187毛利率 f188净利率
FIELDS = ("f43,f57,f58,f60,f116,f117,f162,f163,f164,f167,f168,f170,"
          "f173,f183,f184,f185,f186,f187,f188")
hosts = ["82.push2.eastmoney.com", "33.push2.eastmoney.com",
         "17.push2.eastmoney.com", "push2.eastmoney.com",
         "push2delay.eastmoney.com"]
d = None
for h in hosts:
    try:
        u = (f"https://{h}/api/qt/stock/get?secid=0.000938"
             f"&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fields={FIELDS}")
        req = urllib.request.Request(u, headers=UA)
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            d = json.loads(r.read().decode("utf-8"))
        break
    except Exception:
        continue

if d and d.get("data"):
    dd = d["data"]
    log(f"分析时间: {datetime.now(CST):%Y-%m-%d %H:%M} | {dd.get('f58')}({dd.get('f57')})")
    log("=" * 56)
    log("【估值指标】")
    log(f"  现价: {dd.get('f43')} 元 (昨收 {dd.get('f60')}, 今日 {dd.get('f170')}%)")
    log(f"  总市值: {round(dd.get('f116', 0) / 1e8)} 亿 | 流通市值: {round(dd.get('f117', 0) / 1e8)} 亿")
    log(f"  市盈率PE(动): {dd.get('f162')} | PE(TTM): {dd.get('f163')} | PE(静): {dd.get('f164')}")
    log(f"  市净率PB: {dd.get('f167')} | ROE: {dd.get('f173')}")
    log("")
    log("【财务快照（最新报告期）】")
    rev, rev_yoy = dd.get("f183"), dd.get("f184")
    np_, np_yoy = dd.get("f185"), dd.get("f186")
    log(f"  营收: {round(rev / 1e8, 1) if isinstance(rev, (int, float)) else rev} 亿, 同比 {rev_yoy}%")
    log(f"  净利润: {round(np_ / 1e8, 2) if isinstance(np_, (int, float)) else np_} 亿, 同比 {np_yoy}%")
    log(f"  毛利率: {dd.get('f187')}% | 净利率: {dd.get('f188')}%")
else:
    log("❌ 东财估值接口失败（可稍后重试）")

log("")
log("【本地新闻库：紫光/新华三相关（近7日简报提及）】")
hits = []
for f in sorted(glob.glob("briefings/2026-09-0*.md")):
    try:
        text = io.open(f, encoding="utf-8").read()
    except Exception:
        continue
    for line in text.splitlines():
        if ("紫光" in line or "新华三" in line) and len(line.strip()) > 10:
            fn = f.replace("\\", "/").split("/")[-1]
            hits.append(f"{fn}: {line.strip()[:120]}")
if hits:
    for h in hits[-12:]:
        log(f"  · {h}")
else:
    log("  （近7日简报中无紫光/新华三专项提及）")

io.open("ziguang_fundamental.txt", "w", encoding="utf-8").write("\n".join(OUT))
print("saved")
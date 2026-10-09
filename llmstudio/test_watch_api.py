# -*- coding: utf-8 -*-
"""测试东财接口对多只自选股的可用性"""
import json
import ssl
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CONTEXT = ssl.create_default_context()
CONTEXT.check_hostname = False
CONTEXT.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"}
UT = "bd1d9ddb04089700cf9c27f6f7426281"

def get_json(path):
    url = f"https://push2.eastmoney.com{path}"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15, context=CONTEXT) as r:
        return json.loads(r.read().decode("utf-8"))

# 比亚迪 002594 (SZ), 长江电力 600900 (SH)
for name, secid in [("比亚迪", "0.002594"), ("长江电力", "1.600900")]:
    try:
        d = get_json(f"/api/qt/stock/get?secid={secid}"
                     f"&ut={UT}&fltt=2&invt=2"
                     "&fields=f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f168,f170,f171,"
                     "f116,f117,f162,f163,f164,f167,f173,f184,f186")
        dd = d.get("data") or {}
        print(f"=== {name} ({secid}) ===")
        print(f"  收盘: {dd.get('f43')} | 涨跌: {dd.get('f170')}% | 量比: {dd.get('f50')} | 换手: {dd.get('f168')}%")
        print(f"  市值: {dd.get('f116')} | PE动: {dd.get('f162')} | PE-TTM: {dd.get('f163')} | PB: {dd.get('f167')}")
        print(f"  ROE: {dd.get('f173')} | 营收同比: {dd.get('f184')} | 净利同比: {dd.get('f186')}")
    except Exception as e:
        print(f"=== {name} ({secid}) FAIL: {e} ===")
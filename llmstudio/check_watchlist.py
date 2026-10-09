# -*- coding: utf-8 -*-
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

with open("movers.json", "r", encoding="utf-8") as f:
    d = json.load(f)

wl = d.get("watchlist", [])
print("watchlist count:", len(wl))
for w in wl:
    print(f"  {w['name']} 收盘{w.get('close')} {w.get('pct')}% "
          f"量比{w.get('vol_ratio')} 换手{w.get('turnover')}% "
          f"PE-TTM:{w.get('pe_ttm')} MA5:{w.get('ma5')} MA20:{w.get('ma20')}")
# -*- coding: utf-8 -*-
"""龙虎榜晚间解读推送（交易日18:30运行）
数据源：东财龙虎榜API（上榜名单+买卖席位明细）
输出：上榜概况 + 重点个股席位拆解（机构/游资识别）+ 与当日异动股对照
保存 briefings/YYYY-MM-DD_lhb.md 并推送Server酱"""
import urllib.request
import urllib.parse
import json
import ssl
import sys
import os
import re
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

# 知名游资/席位标签（识别用，可自行扩充）
FAMOUS_SEATS = {
    "拉萨": "东财拉萨（散户大本营）",
    "华鑫证券上海分公司": "华鑫上海（知名游资）",
    "银河证券绍兴": "银河绍兴（赵老哥系）",
    "国泰君安南京太平南路": "南京太平南路（知名游资）",
    "东方财富证券拉萨团结路": "东财拉萨（散户）",
    "量化打板": "量化打板席位",
    "机构专用": "机构专用",
}


def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def get_lhb_list(date_str):
    """当日龙虎榜名单（按龙虎榜净买额降序）"""
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
           "reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&source=WEB"
           f"&filter=(trade_date%3D%27{date_str}%27)"
           "&sort=billboard_net_amt&order=desc&pageNumber=1&pageSize=100")
    d = fetch_json(url)
    return ((d.get("result") or {}).get("data")) or []


def get_seats(date_str, code, side):
    """个股买卖席位明细 side: BUY/SELL"""
    name = f"RPT_BILLBOARD_DAILYDETAILS{side}"
    sort = "buy_amount" if side == "BUY" else "sell_amount"
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
           f"reportName={name}&columns=ALL&source=WEB"
           f"&filter=(trade_date%3D%27{date_str}%27)(security_code%3D%27{code}%27)"
           f"&sort={sort}&order=desc&pageNumber=1&pageSize=5")
    try:
        d = fetch_json(url)
        return ((d.get("result") or {}).get("data")) or []
    except Exception:
        return []


def seat_label(name):
    for k, v in FAMOUS_SEATS.items():
        if k in name:
            return v
    return ""


def yi(v):
    try:
        return f"{v / 1e8:.2f}亿"
    except Exception:
        return "-"


def main():
    now = datetime.now(CST)
    date_str = now.strftime("%Y-%m-%d")
    print(f"[1/3] 抓取龙虎榜名单 {date_str} ...")
    rows = get_lhb_list(date_str)
    if not rows:
        print("今日无龙虎榜数据（可能非交易日或数据未更新），退出")
        return

    # 与当日异动股对照
    movers_codes = {}
    try:
        with open("movers.json", "r", encoding="utf-8") as f:
            for m in json.load(f).get("movers", []):
                movers_codes[m["code"]] = m
    except Exception:
        pass

    out = [f"# 🐉 龙虎榜晚间解读 | {date_str}", "",
           f"> 共{len(rows)}股上榜 | 数据源: 东方财富 | 机构/游资席位自动识别", ""]
    out.append("## 📊 上榜概况")
    top3 = rows[:3]
    for r in top3:
        out.append(f"- **{r['SECURITY_NAME_ABBR']}({r['SECURITY_CODE']})** "
                   f"{r.get('CHANGE_RATE', 0):+.2f}% | 榜净买 {yi(r.get('BILLBOARD_NET_AMT'))} | "
                   f"上榜原因: {r.get('EXPLANATION', '')}")
    out.append("")

    # 重点拆解：净买额前5 + 当日异动股上榜的（去重，最多8只）
    picked, seen = [], set()
    for r in rows:
        code = r["SECURITY_CODE"]
        if code in movers_codes or len(picked) < 5:
            if code not in seen:
                picked.append(r)
                seen.add(code)
        if len(picked) >= 8:
            break

    out.append("## 🔍 重点个股席位拆解")
    print(f"[2/3] 拆解{len(picked)}只重点股席位 ...")
    for r in picked:
        code = r["SECURITY_CODE"]
        name = r["SECURITY_NAME_ABBR"]
        tag = " ⭐今日异动股" if code in movers_codes else ""
        out.append(f"### {name}({code}) {r.get('CHANGE_RATE', 0):+.2f}%{tag}")
        out.append(f"上榜原因: {r.get('EXPLANATION', '')} | 榜净买 **{yi(r.get('BILLBOARD_NET_AMT'))}**")
        buy_seats = get_seats(date_str, code, "BUY")
        sell_seats = get_seats(date_str, code, "SELL")
        buy_inst = sum(1 for s in buy_seats if "机构专用" in (s.get("OPERATEDEPT_NAME") or ""))
        sell_inst = sum(1 for s in sell_seats if "机构专用" in (s.get("OPERATEDEPT_NAME") or ""))
        out.append(f"- **买五**: 机构{buy_inst}家 | " +
                   "；".join(f"{(s.get('OPERATEDEPT_NAME') or '')[:20]}"
                             f"({seat_label(s.get('OPERATEDEPT_NAME') or '') or '营业部'})"
                             f" 买{yi(s.get('BUY_AMOUNT'))}"
                             for s in buy_seats[:3]))
        out.append(f"- **卖五**: 机构{sell_inst}家 | " +
                   "；".join(f"{(s.get('OPERATEDEPT_NAME') or '')[:20]}"
                             f" 卖{yi(s.get('SELL_AMOUNT'))}"
                             for s in sell_seats[:3]))
        # 性质判断
        if buy_inst >= 2 and sell_inst == 0:
            judge = "机构净买入，可能有基本面逻辑，持续性看后续量能"
        elif buy_inst == 0 and sell_inst >= 2:
            judge = "机构卖出为主，警惕利好兑现/出货"
        elif buy_inst >= 1 and sell_inst >= 1:
            judge = "机构多空分歧，方向需看次日承接"
        else:
            judge = "营业部（游资）主导，短线博弈性质，注意快进快出"
        out.append(f"- **解读**: {judge}")
        out.append("")

    out.append("## ⚠️ 提示")
    out.append("龙虎榜数据为T+1公开信息，席位行为仅供参考，不构成投资建议。")

    text = "\n".join(out)
    os.makedirs("briefings", exist_ok=True)
    path = f"briefings/{date_str}_lhb.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[3/3] 已保存 {path}，推送中 ...")

    # 推送（Server酱已停用 2026-09-23起，仅走飞书/企微/Bark通道）
    push_on = os.environ.get("PUSH_ENABLED", "").lower() in ("1", "true", "yes")
    if not push_on:
        print("(推送未启用：PUSH_ENABLED未开启)")
    # 飞书/企业微信机器人推送（内容直接在群里展示，不暴露GitHub地址）
    if push_on:
        try:
            from feishu_push import push_feishu
            push_feishu(f"龙虎榜晚间解读 {date_str}", text[:3000])
        except Exception as _fs:
            print(f"  [feishu] err: {_fs}")
        try:
            from wecom_push import push_wecom
            push_wecom(f"龙虎榜晚间解读 {date_str}", text[:2000])
        except Exception as _wc:
            print(f"  [wecom] err: {_wc}")
        try:
            from bark_push import push_bark
            push_bark(f"龙虎榜晚间解读 {date_str}", text[:400], level="timeSensitive")
        except Exception as _bk:
            print(f"  [bark] err: {_bk}")
    # 微信测试号推送已停用（2026-09-23起）


if __name__ == "__main__":
    main()
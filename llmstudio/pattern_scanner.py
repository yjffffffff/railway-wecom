# -*- coding: utf-8 -*-
"""周期题材 + 技术形态扫描（交易日15:40运行，收盘后）
用户需求落地：
1. 周期性题材日历：按月份提示当下可做的周期产业（如9-11月冰雪产业、冬季煤炭供暖）
2. 板块首板识别：概念板块当日首次进入涨幅前列 → 新启动信号（散户易上车点）
3. 煤炭等周期板块监控：散户比较容易捞钱的板块，每日跟踪涨幅与龙头
4. 技术形态识别（参考通达创智类走势）：
   - 均线多头启动：MA5>MA10>MA20>MA30 全线向上，刚形成多头排列
   - 缩量止跌：连续下跌但成交量递减（无人抛售）+ 今日放量阳线
   - 放量阳线突破：量比≥2倍 + 实体阳线≥2%（主升浪启动信号）
   - 底部吸筹：60日低位窄幅震荡 + 温和放量
   - 主力抬轿：涨幅适中+量能健康+阳线（跟庄吃肉形态）
数据源：东财行情 clist（候选池/板块）+ 腾讯日K（形态计算）
推送：飞书/企微/Bark（Server酱与微信测试号已停用）
"""
import urllib.request
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
EM_HOSTS = ["82.push2.eastmoney.com", "33.push2.eastmoney.com",
            "17.push2.eastmoney.com", "push2.eastmoney.com",
            "push2delay.eastmoney.com"]
SECTOR_HIST_FILE = "knowledge/sector_history.json"

# 散户友好周期板块监控名单（东财概念/行业板块名称关键词）
SECTOR_WATCH = ["煤炭", "冰雪", "天然气", "供暖", "旅游", "电解铝", "磷化工"]

# 周期题材日历：月份 -> [(题材, 逻辑, 关注方向)]
SEASONAL_THEMES = {
    1: [("春节数字消费", "春节档电影/零售/旅游旺季", "传媒·零售·旅游"),
        ("年报预告行情", "业绩预增股炒作窗口", "预增公告+低估值")],
    2: [("春节数字消费", "春节档收官+元宵消费", "传媒·食品"),
        ("两会前瞻", "政策预期博弈（新质生产力/基建）", "AI·机器人·基建")],
    3: [("两会政策行情", "政府工作报告定调全年主线", "低空经济·设备更新"),
        ("春耕农资", "化肥/种子季节性旺季", "磷肥·钾肥·种业")],
    4: [("一季报行情", "业绩兑现+高送转", "业绩超预期+次新")],
    5: [("五一日经济", "旅游出行数据催化", "旅游·酒店·免税")],
    6: [("迎峰度夏", "电力负荷高峰，煤炭/电力旺季前布局", "煤炭·电力·绿电"),
        ("618消费", "电商大促催化", "跨境电商·消费电子")],
    7: [("迎峰度夏", "用电高峰，动力煤价格弹性", "煤炭·电力")],
    8: [("中报行情", "业绩披露+高景气方向确认", "光模块·算力·资源")],
    9: [("冰雪产业启动", "雪季前装备/场馆/文旅订单落地，11月-1月主升（政策+冬奥预期）",
         "冰雪装备·滑雪场·文旅·造雪设备"),
        ("国庆出行前置", "假期出行预订数据", "旅游·航空·酒店"),
        ("冬储煤布局", "冬储招标启动，煤价看涨预期", "动力煤·焦煤")],
    10: [("冰雪产业主升前夜", "雪场开板+冰雪游预订，板块热度快速上升",
          "冰雪装备·滑雪场·文旅"),
         ("冬储煤", "电厂冬储补库，煤价旺季", "动力煤·焦煤"),
         ("国庆消费复盘", "假期数据催化板块", "旅游·免税·影视")],
    11: [("冰雪产业主升", "雪季全面开启+元旦寒假预订，题材最热窗口",
          "冰雪装备·滑雪场·文旅·体育"),
         ("冬储煤旺季", "寒潮+供暖，煤价电力共振", "煤炭·热力"),
         ("年底机构调仓", "高股息防守+绩优白马", "红利·银行·家电")],
    12: [("冰雪产业高潮", "滑雪季高峰+跨年文旅，注意高位分歧",
          "冰雪·文旅·体育"),
         ("供暖能源", "寒潮催化天然气/煤炭", "天然气·煤炭"),
         ("年初布局", "基金调仓+春季躁动前夜", "超跌绩优+次新")],
}

MAX_SCAN = 40          # 最多扫描的个股数（控制请求量）
SCAN_TOP_N = 60        # 从涨幅榜取前N只作为候选池


def fetch_json(url, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def em_get(path):
    """多主机 + 三轮轮询 + 递增退避（应对偶发 502 网关错误 / Remote end closed 断连）"""
    last = None
    for rnd in range(3):
        for h in EM_HOSTS:
            try:
                return fetch_json(f"https://{h}{path}", retries=2)
            except Exception as e:
                last = e
                time.sleep(0.5)
        if rnd < 2:   # 轮次间递增退避，给东财网关恢复时间
            time.sleep(2 * (rnd + 1))
    raise last


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs("knowledge", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def push(title, text):
    push_on = os.environ.get("PUSH_ENABLED", "").lower() in ("1", "true", "yes")
    if not push_on:
        print(f"[PUSH OFF] {title}\n{text[:600]}")
        return False
    sent = False
    for mod_name, fn_name in (("feishu_push", "push_feishu"),
                              ("wecom_push", "push_wecom")):
        try:
            mod = __import__(mod_name)
            getattr(mod, fn_name)(title, text[:3000])
            sent = True
        except Exception as e:
            print(f"[{mod_name}] err: {e}")
    try:
        from bark_push import push_bark
        push_bark(title, text[:400], level="timeSensitive")
        sent = True
    except Exception as e:
        print(f"[bark] err: {e}")
    return sent


# ---------------- 1. 周期题材日历 ----------------

def seasonal_lines():
    now = datetime.now(CST)
    return [f"- **{name}**：{logic} → 关注: {direction}"
            for name, logic, direction in SEASONAL_THEMES.get(now.month, [])]


# ---------------- 2. 板块监控（周期板块+首板识别） ----------------

def get_board_list(fs):
    """板块涨幅榜 fs: m:90+t:3 概念 / m:90+t:2 行业"""
    d = em_get("/api/qt/clist/get?pn=1&pz=100&po=1&np=1"
               f"&ut={UT}&fltt=2&invt=2&fid=f3&fs={fs}"
               "&fields=f3,f12,f14,f104,f128,f136")
    return (d.get("data") or {}).get("diff") or []


def scan_boards():
    """返回 (周期板块行情lines, 板块首板lines)"""
    hist = load_json(SECTOR_HIST_FILE, {})
    today = datetime.now(CST).strftime("%Y-%m-%d")
    hist = {k: v for k, v in hist.items()
            if k == "date" or (isinstance(v, dict) and v.get("d", "") == today)}
    hist.setdefault("date", today)
    prev_top10 = set(hist.get("top10_concept", []))

    lines_cycle, lines_first = [], []
    try:
        boards = get_board_list("m:90+t:3")
        today_top10 = []
        for b in boards[:10]:
            code, name = str(b.get("f12")), b.get("f14", "")
            pct = b.get("f3")
            today_top10.append(code)
            if any(k in name for k in SECTOR_WATCH) and isinstance(pct, (int, float)):
                leader = b.get("f128", "") or "-"
                up_cnt = b.get("f104", "-")   # 上涨家数
                lines_cycle.append(f"- **{name}** {pct:+.2f}% | "
                                   f"龙头:{leader} | 上涨{up_cnt}家")
        # 板块首板：今日新进涨幅前10的概念板块
        for b in boards[:10]:
            code, name, pct = str(b.get("f12")), b.get("f14", ""), b.get("f3")
            if code not in prev_top10 and isinstance(pct, (int, float)) and pct >= 2:
                lines_first.append(f"- 🆕 **{name}** {pct:+.2f}% | "
                                   f"龙头:{b.get('f128', '') or '-'}（今日首次进入涨幅前十）")
        hist["top10_concept"] = today_top10
        hist["d"] = today
        save_json(SECTOR_HIST_FILE, hist)
    except Exception as e:
        print(f"板块扫描失败: {e}")
    return lines_cycle, lines_first


# ---------------- 3. 技术形态识别 ----------------

def get_kline(tencent, days=130):
    """腾讯前复权日K: [date, open, close, high, low, volume]"""
    d = fetch_json(f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
                   f"param={tencent},day,,,{days},qfq")
    dd = (d.get("data") or {}).get(tencent) or {}
    return dd.get("qfqday") or dd.get("day") or []


def detect_patterns(kl):
    """识别技术形态，返回标签列表。kl: 时间正序日K"""
    try:
        rows = [[r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                 float(r[5])] for r in kl]
    except Exception:
        return []
    if len(rows) < 35:
        return []
    o, c, h, l, v = ([x[i] for x in rows] for i in (1, 2, 3, 4, 5))
    n = len(rows)

    def ma(p, end):
        return sum(c[end - p + 1:end + 1]) / p if end >= p - 1 else None

    tags = []
    last = n - 1
    close = c[last]
    chg = (close / c[last - 1] - 1) * 100
    vol_ratio = v[last] / (sum(v[last - 5:last]) / 5) if sum(v[last - 5:last]) else 1
    is_yang = close > o[last] and chg >= 2

    # 均线多头启动：MA5>MA10>MA20>MA30 刚形成 + 各均线向上
    mas = {p: [ma(p, last - k) for k in range(3)] for p in (5, 10, 20, 30)}
    if all(mas[p][0] for p in mas):
        bull_now = mas[5][0] > mas[10][0] > mas[20][0] > mas[30][0]
        bull_before = (mas[5][2] > mas[10][2] > mas[20][2] > mas[30][2]
                       if all(mas[p][2] for p in mas) else False)
        rising = all(mas[p][0] >= mas[p][1] >= mas[p][2] for p in mas)
        if bull_now and not bull_before and rising:
            tags.append("均线多头启动")

    # 连续缩量下跌后放量阳线（止跌反转）
    down4 = all(c[last - i] < c[last - i - 1] for i in range(4))
    vol_shrink = all(v[last - i] < v[last - i - 1] for i in range(3))
    if down4 and vol_shrink and is_yang and vol_ratio >= 1.5:
        tags.append("缩量止跌反转")

    # 放量阳线突破：量比≥2 + 阳线2%~9.8% + 收在近10日高位
    if vol_ratio >= 2 and 2 <= chg <= 9.8 and close >= max(c[last - 9:last]):
        tags.append("放量阳线突破")

    # 底部吸筹（疑似）：60日低位 + 近10日窄幅震荡 + 今日温和放量
    c60 = c[-60:]
    if len(c60) >= 50:
        med = sorted(c60)[len(c60) // 2]
        amp = (max(h[last - 9:last + 1]) - min(l[last - 9:last + 1])) / close
        if close <= med * 1.05 and amp < 0.10 and 1.2 <= vol_ratio <= 2.5:
            tags.append("底部吸筹(疑似)")

    # 主力抬轿（形态）：涨幅3~9% + 量比1.5~4 + 阳线
    if 3 <= chg <= 9 and 1.5 <= vol_ratio <= 4 and close > o[last]:
        tags.append("主力抬轿(形态)")

    return [f"🏷️{t}" for t in tags]


def scan_patterns():
    """候选池 = 当日涨幅榜前列；逐只抓日K识别形态"""
    try:
        d = em_get(f"/api/qt/clist/get?pn=1&pz={SCAN_TOP_N}&po=1&np=1"
                   f"&ut={UT}&fltt=2&invt=2&fid=f3"
                   "&fields=f2,f3,f8,f12,f14,f62,f100")
        stocks = (d.get("data") or {}).get("diff") or []
    except Exception as e:
        print(f"候选池抓取失败: {e}")
        return []
    hits = []
    for it in stocks:
        if len(hits) >= MAX_SCAN:
            break
        code = str(it.get("f12") or "")
        name = it.get("f14", "") or ""
        chg = it.get("f3")
        if not isinstance(chg, (int, float)) or chg < 2:
            continue
        if "ST" in name.upper() or "退" in name:
            continue
        tencent = ("sh" if code.startswith(("6", "9")) else
                   "bj" if code.startswith(("4", "8")) else "sz") + code
        try:
            tags = detect_patterns(get_kline(tencent))
            if tags:
                inflow = it.get("f62")
                hits.append({
                    "name": name, "code": code, "chg": chg,
                    "sector": it.get("f100", "") or "",
                    "inflow_yi": round(inflow / 1e8, 2) if isinstance(inflow, (int, float)) else None,
                    "tags": tags,
                })
        except Exception:
            continue
        time.sleep(0.25)   # 控制频率防封
    return hits


# ---------------- 主流程 ----------------

def main():
    now = datetime.now(CST)
    lines = [f"## 🔭 周期题材+形态扫描 {now:%Y-%m-%d %H:%M}", ""]

    # 1. 周期题材日历
    lines.append("### 🗓️ 当月周期题材（历史季节性规律）")
    themes = seasonal_lines()
    lines.extend(themes if themes else ["（本月无明显季节性题材）"])
    lines.append("")

    # 2. 周期板块监控 + 板块首板
    cyc, first = scan_boards()
    if cyc:
        lines.append("### ⛏️ 周期板块跟踪（煤炭/冰雪/供暖等散户友好方向）")
        lines.extend(cyc)
        lines.append("")
    if first:
        lines.append("### 🆕 板块首板（新启动，适合低吸上车）")
        lines.extend(first)
        lines.append("")

    # 3. 技术形态扫描
    print("[3/3] 扫描个股技术形态 ...")
    hits = scan_patterns()
    if hits:
        lines.append(f"### 📐 技术形态命中（扫描涨幅榜前{SCAN_TOP_N}）")
        for h in hits:
            inflow = f" 主力{h['inflow_yi']:+.2f}亿" if h["inflow_yi"] is not None else ""
            lines.append(f"- **{h['name']}({h['code']})** {h['chg']:+.2f}%{inflow} | "
                         f"{h['sector']} | {' '.join(h['tags'])}")
    else:
        lines.append("### 📐 技术形态命中")
        lines.append("（今日候选池中未识别到典型形态）")
    lines.append("")
    lines.append("> 形态标签为规则识别，需结合题材/情绪使用：多头启动+放量突破优先，"
                 "底部吸筹需确认后放量再介入")
    lines.append("> 仅供参考，不构成投资建议")

    text = "\n".join(lines)
    os.makedirs("briefings", exist_ok=True)
    path = f"briefings/{now:%Y-%m-%d}_pattern.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"已保存 {path}")
    push("周期题材+形态扫描", text)


if __name__ == "__main__":
    main()
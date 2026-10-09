# -*- coding: utf-8 -*-
"""游资策略信号模块（龙头识别 + 打板/低吸信号 + 游资席位跟踪）
基于《游资大佬高收益策略调研》（docs/游资大佬高收益策略调研_2026-09-22.md）落地：

1. 情绪周期定位：复用 fetch_sentiment.py 的 sentiment.json（涨停数/炸板率/连板高度）
2. 龙头识别：从涨停池中按 连板高度/封单强度/首次封板时间/成交额 综合打分
3. 打板候选：情绪偏暖~亢奋时，输出 2/3 板确认接力候选 —— 仅限 00 开头（深主板）
4. 低吸候选：龙头分歧日（高位板炸板/大阴线）低吸信号
5. 游资席位跟踪：龙虎榜中出现知名游资席位净买入时推送提醒

调度（scheduler.py 已注册）：
  09:35 盘前情绪+龙头前瞻；14:45 盘中打板/低吸信号；18:35 游资席位跟踪
推送通道：飞书/企业微信/Bark（Server酱与微信测试号已于2026-09-23停用）
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
UT_EX = "7eea3edcaed734bea9cbfc24409ed989"   # push2ex 涨停池专用ut（旧ut已失效返回rc205）
UT = "bd1d9ddb04089700cf9c27f6f7426281"
EM_HOSTS = ["82.push2.eastmoney.com", "33.push2.eastmoney.com",
            "17.push2.eastmoney.com", "push2.eastmoney.com",
            "push2delay.eastmoney.com"]
STATE_FILE = "knowledge/dragon_signals_state.json"

# 打板候选仅限00开头（深主板），符合用户要求
DABAN_CODE_PREFIX = ("00",)
MAX_CANDIDATES = 8

# 知名游资席位标签库（扩展自 generate_lhb.py，可继续扩充）
FAMOUS_HOT_MONEY = {
    "银河证券绍兴": "赵老哥系（银河绍兴）",
    "华鑫证券上海分公司": "华鑫上海（顶流游资）",
    "国泰君安南京太平南路": "南京太平南路（知名游资）",
    "华泰证券厦门厦禾路": "厦门厦禾路（知名游资）",
    "中信证券上海溧阳路": "上海溧阳路（章盟主系）",
    "国泰君安上海江苏路": "上海江苏路（章盟主系）",
    "财通证券杭州上塘路": "杭州上塘路（炒股养家系）",
    "东方财富证券拉萨东环路": "东财拉萨（散户/量化）",
    "东方财富证券拉萨团结路": "东财拉萨（散户/量化）",
    "东方财富证券拉萨金珠西路": "东财拉萨（散户/量化）",
    "宁波桑田路": "宁波桑田路（知名游资）",
    "银河证券宁波大庆南路": "宁波大庆南路（敢死队）",
    "方正证券杭州保俶路": "杭州保俶路（知名游资）",
    "广发证券辽阳西路": "青岛辽阳西路（知名游资）",
    "招商证券深圳蛇口工业七路": "深圳蛇口（乔帮主系）",
    "量化打板": "量化打板席位",
}


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
    last = None
    for h in EM_HOSTS:
        try:
            return fetch_json(f"https://{h}{path}", retries=1)
        except Exception as e:
            last = e
    raise last


def load_state():
    """读取去重状态，仅保留今天的记录（跨天自动重置，避免昨日候选今日被误跳过）"""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    today = datetime.now(CST).strftime("%Y-%m-%d")
    if raw.get("date") != today:
        return {"date": today}
    return raw


def save_state(state):
    today = datetime.now(CST).strftime("%Y-%m-%d")
    state = {k: v for k, v in state.items() if k == "date" or v.get("d") == today}
    state["date"] = today
    os.makedirs("knowledge", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def push(title, text):
    """推送：飞书/企微/Bark（Server酱与微信测试号已停用）"""
    push_on = os.environ.get("PUSH_ENABLED", "").lower() in ("1", "true", "yes")
    if not push_on:
        print(f"[PUSH OFF] {title}\n{text[:500]}")
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
    print(f"推送{'成功' if sent else '跳过'}: {title}")
    return sent


# ---------------- 情绪周期 ----------------

def load_sentiment():
    try:
        with open("sentiment.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def cycle_stage(s):
    """由 sentiment 定位情绪周期阶段：返回 (阶段, 操作建议)"""
    zt = s.get("zt_count") or 0
    zb_rate = s.get("zb_rate")
    dt = s.get("dt_count") or 0
    max_lb = s.get("max_lianban") or 0
    if zt >= 60 and (zb_rate or 0) < 0.35:
        return "亢奋", "可打板/接力，仓位可放，但警惕一致加速后的分歧"
    if zt >= 30 and dt < 15:
        return "发酵", "打二板/三板确认接力为主，低吸龙头分歧"
    if zt >= 15:
        return "中性", "轻仓试错，只做最强龙头，杂毛不碰"
    if dt >= 15 or (zb_rate or 0) >= 0.5:
        return "退潮", "空仓观望，不接高位板，等冰点后的新周期"
    return "冰点", "空仓或低吸超跌人气股，等情绪启动信号"


# ---------------- 涨停池与龙头识别 ----------------

def get_zt_pool(date_str):
    d = fetch_json(f"https://push2ex.eastmoney.com/getTopicZTPool?"
                   f"ut={UT_EX}&dpt=wz.ztzt&Pageindex=0&pagesize=10000"
                   f"&sort=fbt%3Aasc&date={date_str}")
    return (d.get("data") or {}).get("pool") or []


def get_zt_pool_latest(max_back=5):
    """按日期回溯取最近一个有数据的涨停池。
    盘前(09:35)当日池尚未生成、非交易日池为空 → 自动回看上一交易日，
    返回 (pool, 用于查询的日期YYYYMMDD)"""
    d = datetime.now(CST)
    used = d.strftime("%Y%m%d")
    for i in range(max_back + 1):
        day = d - timedelta(days=i)
        if day.weekday() >= 5:   # 周末无池，跳过
            continue
        ds = day.strftime("%Y%m%d")
        try:
            pool = get_zt_pool(ds)
        except Exception as e:
            print(f"涨停池{ds}抓取失败: {e}")
            continue
        if pool:
            return pool, ds
    return [], used


def refresh_sentiment(today_only):
    """刷新 sentiment.json 并返回最新数据。
    today_only=True(盘中)：按今日日期重抓；抓坏则回退旧数据。
    today_only=False(盘前)：只在数据缺失/损坏时补抓（盘前应参考最近交易日情绪）。"""
    s = load_sentiment()
    need = (not s) or not s.get("zt_count")
    if today_only:
        need = need or s.get("date") != datetime.now(CST).strftime("%Y-%m-%d")
    if not need:
        return s
    backup = s
    try:
        import fetch_sentiment
        if not today_only:
            # 盘前：当日池未生成，指定抓上一交易日
            d = datetime.now(CST) - timedelta(days=1)
            while d.weekday() >= 5:
                d -= timedelta(days=1)
            os.environ["FETCH_DATE"] = d.strftime("%Y%m%d")
        try:
            fetch_sentiment.main()
        finally:
            os.environ.pop("FETCH_DATE", None)
        s2 = load_sentiment()
        if s2 and s2.get("zt_count"):
            return s2
        return backup or s2   # 今日池未生成/抓取失败 → 保留旧数据
    except Exception as e:
        print(f"情绪刷新失败: {e}")
        return backup or load_sentiment()


def score_leaders(zt_pool):
    """龙头打分：连板高度(40) + 封单强度(30) + 早封板(20) + 成交额(10)"""
    ranked = []
    for it in zt_pool:
        code = str(it.get("c") or "")
        name = it.get("n") or ""
        if "ST" in name.upper() or "退" in name:
            continue
        lbc = it.get("lbc") or 1              # 连板数
        fund = it.get("fund") or 0            # 封单金额(元)
        amount = it.get("amount") or 0        # 成交额(元)
        fbt = str(it.get("fbt") or "1500")    # 首次封板时间 HHMM
        try:
            fbt_val = int(fbt[:2] + fbt[2:4]) if len(fbt) >= 4 else 1500
        except Exception:
            fbt_val = 1500
        s = 0
        s += min(lbc, 6) / 6 * 40             # 连板高度
        s += min(fund / 5e8, 1) * 30          # 封单5亿封顶
        s += max(0, (1500 - fbt_val)) / (1500 - 925) * 20   # 越早封板越高
        s += min(amount / 3e9, 1) * 10        # 成交30亿封顶
        ranked.append({
            "code": code, "name": name, "lianban": lbc,
            "fund_yi": round(fund / 1e8, 2), "amount_yi": round(amount / 1e8, 2),
            "fbt": fbt, "score": round(s, 1),
        })
    ranked.sort(key=lambda x: -x["score"])
    return ranked


# ---------------- 打板/低吸信号 ----------------

def build_dababan_candidates(ranked, stage):
    """打板候选：情绪发酵~亢奋期，2/3板确认接力，仅00开头"""
    if stage not in ("亢奋", "发酵", "中性"):
        return []
    out = []
    for r in ranked:
        if not r["code"].startswith(DABAN_CODE_PREFIX):
            continue
        if r["lianban"] in (1, 2, 3):   # 首板信息量低，二/三板确认接力为主
            out.append(r)
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def build_dixi_candidates(ranked, stage):
    """低吸候选：情绪非退潮期，高连板龙头（≥3板）回调分歧低吸"""
    if stage in ("退潮",):
        return []
    return [r for r in ranked if r["lianban"] >= 3][:5]


# ---------------- 游资席位跟踪 ----------------

def get_lhb_rows(date_str):
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
           "reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&source=WEB"
           f"&filter=(trade_date%3D%27{date_str}%27)"
           "&sort=billboard_net_amt&order=desc&pageNumber=1&pageSize=200")
    d = fetch_json(url)
    return ((d.get("result") or {}).get("data")) or []


def get_seats(date_str, code, side):
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


def hotmoney_scan(date_str, state, top_n=10):
    """扫描龙虎榜净买额前N股的买五席位，命中知名游资则生成提醒"""
    rows = get_lhb_rows(date_str)
    hits = []
    for r in rows[:top_n]:
        code = r["SECURITY_CODE"]
        name = r["SECURITY_NAME_ABBR"]
        for s in get_seats(date_str, code, "BUY"):
            seat = s.get("OPERATEDEPT_NAME") or ""
            for k, label in FAMOUS_HOT_MONEY.items():
                if k in seat and "散户" not in label:
                    amt = s.get("BUY_AMOUNT") or 0
                    if amt >= 3e7:   # 3000万以上才算有信号
                        hits.append({
                            "stock": f"{name}({code})",
                            "seat": label,
                            "seat_full": seat,
                            "buy_yi": round(amt / 1e8, 2),
                            "net_yi": round((r.get("BILLBOARD_NET_AMT") or 0) / 1e8, 2),
                            "pct": r.get("CHANGE_RATE", 0),
                        })
                    break
    return hits


# ---------------- 任务入口 ----------------

def job_preopen():
    """09:35 盘前：情绪周期定位 + 最近交易日龙头前瞻（盘前当日池未生成，自动回看上一交易日）"""
    now = datetime.now(CST)
    s = refresh_sentiment(today_only=False)
    stage, advice = cycle_stage(s)
    try:
        pool, used_date = get_zt_pool_latest()
        ranked = score_leaders(pool)
    except Exception as e:
        print(f"涨停池抓取失败: {e}")
        ranked, used_date = [], now.strftime("%Y%m%d")
    asof = "" if used_date == now.strftime("%Y%m%d") else \
        f"（{used_date[:4]}-{used_date[4:6]}-{used_date[6:]}收盘数据）"
    lines = [f"## 🐉 龙头前瞻 {now:%Y-%m-%d %H:%M}", "",
             f"**情绪周期**{asof}: {stage}（涨停{s.get('zt_count', 0)} 炸板率"
             f"{(s.get('zb_rate') or 0) * 100:.0f}% 最高{s.get('max_lianban', 0)}板）",
             f"**操作基调**: {advice}", ""]
    if ranked:
        lines.append(f"### 龙头打分 TOP5（按连板/封单/封板时间）{asof}")
        for r in ranked[:5]:
            lines.append(f"- **{r['name']}({r['code']})** {r['lianban']}板 | "
                         f"封单{r['fund_yi']}亿 | 成交{r['amount_yi']}亿 | "
                         f"首封{r['fbt'][:2]}:{r['fbt'][2:]} | 综合分{r['score']}")
    else:
        lines.append("（暂无涨停数据，可能为非交易日）")
    lines.append("\n> 仅供参考，不构成投资建议")
    push("游资信号·龙头前瞻", "\n".join(lines))


def job_intraday():
    """14:45 盘中：打板/低吸信号（情绪按今日重抓，池子取最近有数据日）"""
    now = datetime.now(CST)
    s = refresh_sentiment(today_only=True)
    stage, advice = cycle_stage(s)
    try:
        ranked = score_leaders(get_zt_pool_latest()[0])
    except Exception as e:
        print(f"涨停池抓取失败: {e}")
        ranked = []
    state = load_state()
    lines = [f"## ⚡ 打板/低吸信号 {now:%Y-%m-%d %H:%M}", "",
             f"**情绪周期**: {stage} | **基调**: {advice}", ""]
    alerts = 0
    if ranked:
        daban = build_dababan_candidates(ranked, stage)
        dixi = build_dixi_candidates(ranked, stage)
        if daban:
            lines.append("### 🔨 打板候选（仅00开头·二/三板确认接力）")
            for r in daban:
                key = f"daban_{r['code']}"
                if state.get(key):
                    continue
                state[key] = {"d": now.strftime("%Y-%m-%d")}
                lines.append(f"- **{r['name']}({r['code']})** {r['lianban']}板 | "
                             f"封单{r['fund_yi']}亿 | 首封{r['fbt'][:2]}:{r['fbt'][2:]} | "
                             f"分{r['score']} → 情绪配合时可打确认板")
                alerts += 1
        if dixi:
            lines.append("### 💧 低吸候选（≥3板龙头分歧回调）")
            for r in dixi:
                key = f"dixi_{r['code']}"
                if state.get(key):
                    continue
                state[key] = {"d": now.strftime("%Y-%m-%d")}
                lines.append(f"- **{r['name']}({r['code']})** {r['lianban']}板 | "
                             f"成交{r['amount_yi']}亿 → 分歧回调-5%~-8%区间低吸博反包")
                alerts += 1
    if stage in ("退潮", "冰点"):
        lines.append("### 🧊 退潮/冰点提示")
        lines.append("- 空仓为主，不接高位板；等新题材首板带动情绪回暖再出手")
        alerts += 1
    if alerts == 0:
        lines.append("（今日候选均已推送过或暂无信号）")
    lines.append("\n> 打板失败止损纪律：次日低开不走强即走，单笔亏损≤5%")
    lines.append("> 仅供参考，不构成投资建议")
    save_state(state)
    push("游资信号·打板低吸", "\n".join(lines))


def job_hotmoney():
    """18:35 盘后：游资席位跟踪"""
    now = datetime.now(CST)
    date_str = now.strftime("%Y-%m-%d")
    try:
        hits = hotmoney_scan(date_str, load_state())
    except Exception as e:
        print(f"龙虎榜抓取失败: {e}")
        hits = []
    lines = [f"## 💰 游资席位跟踪 {date_str}", ""]
    if hits:
        lines.append("### 知名游资净买入（买五席位命中）")
        seen = set()
        for h in hits:
            key = h["stock"] + h["seat"]
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- **{h['stock']}** {h['pct']:+.2f}% | "
                         f"**{h['seat']}** 买{h['buy_yi']}亿 | 榜净买{h['net_yi']}亿")
        lines.append("")
        lines.append("> 顶级游资上榜≠次日必涨：关注'游资+机构'共票，回避纯游资高位接力")
    else:
        lines.append("（今日龙虎榜未发现知名游资大额净买入，或数据未更新）")
    lines.append("\n> 龙虎榜为T+1公开信息，仅供参考，不构成投资建议")
    push("游资信号·席位跟踪", "\n".join(lines))


def main():
    job_map = {
        "preopen": job_preopen,
        "intraday": job_intraday,
        "hotmoney": job_hotmoney,
    }
    mode = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get("DRAGON_MODE", "intraday"))
    job = job_map.get(mode)
    if not job:
        print(f"用法: python dragon_signals.py [preopen|intraday|hotmoney]")
        sys.exit(1)
    job()


if __name__ == "__main__":
    main()
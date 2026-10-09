# -*- coding: utf-8 -*-
"""市场情绪指标抓取：涨跌家数/涨停跌停/炸板率/连板高度/情绪周期定位 -> sentiment.json
数据源：东财涨停池/跌停池/炸板池 + 全A涨跌统计
供盘后复盘、午报引用，判断今天该不该出手"""
import urllib.request
import json
import ssl
import sys
import os
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


def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def fetch_json_retry(url, retries=3):
    """带重试的抓取：东财接口偶发限流/超时"""
    last = None
    for i in range(retries):
        try:
            return fetch_json(url)
        except Exception as e:
            last = e
            import time as _t
            _t.sleep(2 * (i + 1))
    raise last


def get_pool(kind, date_str):
    """kind: ZT=涨停池 DT=跌停池 ZB=炸板池（带重试，避免偶发失败导致0涨停假数据）"""
    try:
        d = fetch_json_retry(f"https://push2ex.eastmoney.com/getTopic{kind}Pool?"
                             f"ut={UT_EX}&dpt=wz.ztzt&Pageindex=0&pagesize=10000"
                             f"&sort=fbt%3Aasc&date={date_str}")
        return (d.get("data") or {}).get("pool") or []
    except Exception as e:
        print(f"# {kind}-POOL-FAIL: {e}")
        return []


def _clist_page(pn, po):
    """抓一页 clist（fid=f3 涨跌幅排序）。多主机轮询+退避重试。
    返回 (total, diff_list)；全部失败抛异常。"""
    last = None
    for _round in range(2):        # 两轮主机轮询
        for host in EM_HOSTS:
            try:
                d = fetch_json_retry(
                    f"https://{host}/api/qt/clist/get?"
                    f"pn={pn}&pz=100&po={po}&np=1"
                    f"&ut={UT}&fltt=2&invt=2&fid=f3"
                    "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f3",
                    retries=2)
                data = d.get("data") or {}
                return data.get("total") or 0, (data.get("diff") or [])
            except Exception as e:
                last = e
        import time as _t
        _t.sleep(5)                # 轮次间退避（应对路径级临时封禁）
    raise last


def get_up_down_count():
    """全A涨跌家数：双向停止扫描（降序数涨、升序数跌，遇分界即停）。
    相比旧版抓满54页，请求量降约90%，大幅降低触发东财clist路径限流概率。"""
    try:
        total, _ = _clist_page(1, 1)          # 降序第1页同时取 total
        if not total:
            raise RuntimeError("clist total为空")

        def count_side(po, keep):
            """keep(f3)->是否计入本侧；遇到首个不 keep 的即停止"""
            n, pn = 0, 1
            while pn <= 60:
                t, diff = _clist_page(pn, po)
                if not diff:
                    break
                stop = False
                for it in diff:
                    f3 = it.get("f3")
                    if not isinstance(f3, (int, float)):   # 停牌/新股，跳过
                        continue
                    if keep(f3):
                        n += 1
                    else:
                        stop = True
                        break
                if stop:
                    break
                pn += 1
            return n

        up = count_side(1, lambda f: f > 0)    # 降序：数到第一个<=0停
        down = count_side(0, lambda f: f < 0)  # 升序：数到第一个>=0停
        flat = max(total - up - down, 0)
        if up + down == 0:
            raise RuntimeError("双向扫描均未计数")
        return up, down, flat
    except Exception as e:
        print(f"# UPDOWN-FAIL: {e}")
        return None, None, None


def sentiment_label(zt, dt, zb_rate, max_lb):
    """规则化情绪周期定位"""
    if zt is None:
        return "未知", "情绪数据抓取失败"
    notes = []
    if zt >= 100 and zb_rate < 0.35:
        label = "亢奋（情绪高潮）"
    elif zt >= 60:
        label = "偏暖"
    elif zt >= 30:
        label = "中性"
    elif zt >= 15:
        label = "偏冷"
    else:
        label = "冰点（谨慎出手）"
    if dt >= 20:
        label += "+退潮警示"
        notes.append(f"跌停{dt}家偏多，亏钱效应明显")
    if max_lb >= 6:
        notes.append(f"最高连板{max_lb}板，空间板高度高，题材博弈活跃")
    elif max_lb <= 2:
        notes.append("最高仅2板，题材高度受限，谨慎追高")
    if zb_rate is not None and zb_rate >= 0.5:
        notes.append(f"炸板率{zb_rate * 100:.0f}%偏高，封板质量差，打板胜率低")
    elif zb_rate is not None and zb_rate <= 0.2 and zt >= 40:
        notes.append(f"炸板率仅{zb_rate * 100:.0f}%，封板质量好")
    if not notes:
        notes.append("情绪处于常规区间，按计划执行即可")
    return label, "；".join(notes)


def main():
    now = datetime.now(CST)
    # 支持 FETCH_DATE 环境变量指定查询日期（盘前抓上一交易日用）
    date_str = os.environ.get("FETCH_DATE", "") or now.strftime("%Y%m%d")
    zt_pool = get_pool("ZT", date_str)
    dt_pool = get_pool("DT", date_str)
    zb_pool = get_pool("ZB", date_str)
    up, down, flat = get_up_down_count()
    if up is None:
        # clist路径级临时封禁时回退上次有效值，避免推送显示"涨None/跌None"
        try:
            with open("sentiment.json", "r", encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old.get("up_count"), int) and isinstance(old.get("down_count"), int):
                up, down, flat = old.get("up_count"), old.get("down_count"), old.get("flat_count")
                print("# UPDOWN-FALLBACK: 沿用上次涨跌家数")
        except Exception:
            pass

    zt = len(zt_pool)
    dt = len(dt_pool)
    zb = len(zb_pool)
    zb_rate = zb / (zt + zb) if (zt + zb) > 0 else None
    lb_counts = {}
    max_lb = 0
    for it in zt_pool:
        lb = it.get("lbc") or 1
        lb_counts[lb] = lb_counts.get(lb, 0) + 1
        max_lb = max(max_lb, lb)
    label, explain = sentiment_label(zt, dt, zb_rate, max_lb)

    # 连板天梯（高度→数量）
    ladder = " | ".join(f"{k}板×{v}" for k, v in sorted(lb_counts.items(), reverse=True)[:6])

    result = {
        "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",  # 实际数据日期（盘前抓上一交易日时正确）
        "time": now.strftime("%H:%M"),
        "up_count": up, "down_count": down, "flat_count": flat,
        "zt_count": zt, "dt_count": dt, "zb_count": zb,
        "zb_rate": round(zb_rate, 3) if zb_rate is not None else None,
        "max_lianban": max_lb,
        "lianban_ladder": ladder,
        "sentiment": label,
        "explain": explain,
    }
    with open("sentiment.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print(f"# SENTIMENT: {label} | 涨{up}/跌{down} | 涨停{zt} 跌停{dt} 炸板{zb}"
          f"({(zb_rate or 0) * 100:.0f}%) | 最高{max_lb}板 | {explain}")
    return result


if __name__ == "__main__":
    main()

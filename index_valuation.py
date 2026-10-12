# -*- coding: utf-8 -*-
"""
宽基指数估值分位模块（PE/PB 百分位 + 历史分位变化 → 加仓/减仓/不动建议）

数据源（均为免登录、密钥直连接口）：
  - A股宽基（科创50/深证100/中证500/创业板50/中证A50）：
      乐咕乐股 https://legulegu.com/api/stockdata/index-basic-pe / index-basic-pb
      单次请求返回该指数「按月」的 PE(TTM) 与 PB 全历史，并自带百分位字段。
      同时用全历史自算「扩张窗口百分位」做交叉校验与历史分位趋势。
      token = md5(今天日期)，CSRF 从页面 <meta name="_csrf"> 正则提取（无需 bs4）。
      乐咕对高频请求会限流（返回非 JSON），故内置「换 CSRF 重试 + 2s 节流」。
  - 海外宽基（纳指100）：
      siblisresearch https://siblisresearch.com/data/nasdaq-100-pe-ratio/
      逐年 Trailing P/E 样本，用于计算 PE 百分位；PB 无免费历史源，置为 None。

分位定义：当前值在「该指数全部历史样本」中低于它的比例（0~100%），
          与蛋卷/乐咕「指数温度」口径一致（值越低越便宜，越适合加仓）。

⚠️ 仅供参考，不构成投资建议。
"""
import hashlib
import logging
import re
import time
from datetime import date, datetime
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

# A股宽基（走乐咕）：名称 -> legulegu indexCode
A_SHARE_INDICES = {
    "科创50": "000688.SH",
    "深证100": "399330.SZ",
    "中证500": "000905.SH",
    "创业板50": "399673.SZ",
    "中证A50": "930050.CSI",
}
# 展示顺序（与需求一致）
INDEX_ORDER = ["科创50", "深证100", "中证500", "创业板50", "中证A50", "纳指100"]

_LG_THROTTLE_SEC = 2.0   # 乐咕请求节流（秒）
_LG_MAX_TRIES = 5        # 换 CSRF 重试次数
_LG_BACKOFF_CAP = 15.0   # 单次重试退避上限（秒），应对 IP 级限流


def _round(v: Optional[float]) -> Optional[float]:
    return round(v, 1) if v is not None else None


def _strip(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s).strip()


# ============ 乐咕（A股宽基） ============
def _lg_token() -> str:
    """乐咕 token = md5(今天日期)，与 akshare 的 py_mini_racer 结果一致"""
    return hashlib.md5(date.today().isoformat().encode("utf-8")).hexdigest()


# 乐咕会话缓存：单次运行内复用同一 CSRF/cookie，避免每请求都打页面被限流
_LG_SESSION = {"cookies": None, "csrf": None}


def _lg_new_csrf() -> tuple:
    """抓取页面 <meta name="_csrf"> 与 cookie（正则，无需 bs4），并缓存复用"""
    r = requests.get("https://legulegu.com/stockdata/sz50-ttm-lyr", headers=UA, timeout=15)
    r.raise_for_status()
    m = (re.search(r'<meta[^>]+name=["\']_csrf["\'][^>]+content=["\']([^"\']+)["\']', r.text)
         or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']_csrf["\']', r.text))
    if not m:
        raise ValueError("未找到乐咕 _csrf meta")
    _LG_SESSION["cookies"], _LG_SESSION["csrf"] = r.cookies, m.group(1)
    return r.cookies, m.group(1)


def _lg_fetch(kind: str, index_code: str) -> List[Dict]:
    """乐咕 index-basic-pe / index-basic-pb。

    单次运行内复用同一 CSRF（实测一个 CSRF 可连续访问多个指数），
    仅在请求失败时刷新 CSRF 重试，配合节流降低限流概率。
    """
    token = _lg_token()
    if _LG_SESSION["csrf"] is None:
        _lg_new_csrf()
    last_err = None
    for attempt in range(_LG_MAX_TRIES):
        try:
            headers = {**UA, "X-CSRF-Token": _LG_SESSION["csrf"]}
            url = f"https://legulegu.com/api/stockdata/index-basic-{kind}"
            resp = requests.get(url, params={"token": token, "indexCode": index_code},
                                cookies=_LG_SESSION["cookies"], headers=headers, timeout=15)
            data = resp.json().get("data") or []
            if data:
                return data
            last_err = RuntimeError("乐咕返回空数据")
        except Exception as e:  # 限流返回非 JSON / 网络抖动 / CSRF 过期
            last_err = e
            logger.debug(f"乐咕 {kind} {index_code} 第{attempt + 1}次失败: {e}")
        # 失败后刷新 CSRF 再重试（限流/过期都会在此恢复）
        try:
            _lg_new_csrf()
        except Exception as ce:
            logger.debug(f"乐咕刷新 CSRF 失败: {ce}")
        time.sleep(min(_LG_THROTTLE_SEC * (attempt + 1), _LG_BACKOFF_CAP))
    logger.warning(f"乐咕 {kind} {index_code} 取数失败: {last_err}")
    return []


def _percentile(values: List[Optional[float]], current: Optional[float]) -> Optional[float]:
    """扩张窗口百分位：current 在全部历史 values 中低于它的比例（0~100）"""
    valid = [v for v in values if v is not None]
    if not valid or current is None:
        return None
    below = sum(1 for v in valid if v < current)
    return below / len(valid) * 100


def _expanding_pct_series(values: List[Optional[float]]) -> List[Optional[float]]:
    """逐点扩张窗口百分位序列（第 i 点 = 该点在前 i+1 个样本中的百分位）"""
    out: List[Optional[float]] = []
    for i, v in enumerate(values):
        out.append(_round(_percentile(values[:i + 1], v)))
    return out


def _lag(series: List[Optional[float]], months: int) -> Optional[float]:
    """取 months 个月前的分位（乐咕为月度数据，1 步≈1 个月）"""
    if len(series) > months:
        return series[-(months + 1)]
    return series[0] if series else None


def _empty_row(name: str, market: str = "A") -> Dict:
    return {"name": name, "market": market, "as_of": None,
            "pe": None, "pe_pct": None, "pb": None, "pb_pct": None,
            "pe_pct_3m_ago": None, "pe_pct_6m_ago": None,
            "pb_pct_3m_ago": None, "pb_pct_6m_ago": None,
            "pe_hist_n": 0, "pb_hist_n": 0}


def _fetch_a_share(name: str, index_code: str) -> Dict:
    """返回单个 A 股指数的 PE/PB 现值、百分位、以及历史分位序列"""
    pe_rows = _lg_fetch("pe", index_code)
    time.sleep(_LG_THROTTLE_SEC)
    pb_rows = _lg_fetch("pb", index_code)

    result = _empty_row(name, "A")
    result["pe_hist_n"], result["pb_hist_n"] = len(pe_rows), len(pb_rows)
    if not pe_rows and not pb_rows:
        return result

    if pe_rows:
        last = pe_rows[-1]
        result["as_of"] = last.get("date")
        result["pe"] = last.get("ttmPe")
        q = last.get("ttmPeQuantile")            # 乐咕自带分位（0~1）
        if q is not None:
            result["pe_pct"] = round(q * 100, 1)
        else:
            pe_series = [r.get("ttmPe") for r in pe_rows]
            result["pe_pct"] = _round(_percentile(pe_series, result["pe"]))
        pe_pct_series = _expanding_pct_series([r.get("ttmPe") for r in pe_rows])
        result["pe_pct_3m_ago"] = _lag(pe_pct_series, 3)
        result["pe_pct_6m_ago"] = _lag(pe_pct_series, 6)

    if pb_rows:
        last = pb_rows[-1]
        result["as_of"] = result["as_of"] or last.get("date")
        result["pb"] = last.get("pb")
        q = last.get("pbQuantile")
        if q is not None:
            result["pb_pct"] = round(q * 100, 1)
        else:
            pb_series = [r.get("pb") for r in pb_rows]
            result["pb_pct"] = _round(_percentile(pb_series, result["pb"]))
        pb_pct_series = _expanding_pct_series([r.get("pb") for r in pb_rows])
        result["pb_pct_3m_ago"] = _lag(pb_pct_series, 3)
        result["pb_pct_6m_ago"] = _lag(pb_pct_series, 6)

    return result


# ============ siblisresearch（纳指100） ============
def _fetch_nasdaq100() -> Dict:
    result = _empty_row("纳指100", "US")
    try:
        t = requests.get("https://siblisresearch.com/data/nasdaq-100-pe-ratio/",
                         headers=UA, timeout=25).text
        for tb in re.findall(r"<table.*?</table>", t, re.S):
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tb, re.S)
            if not rows:
                continue
            header = [_strip(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", rows[0], re.S)]
            if "NASDAQ 100" not in header or not any("Month" in h for h in header):
                continue
            idx = header.index("NASDAQ 100")
            data = []
            for row in rows[1:]:
                cells = [_strip(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
                if len(cells) > idx:
                    try:
                        data.append((cells[0], float(cells[idx])))
                    except ValueError:
                        pass
            if not data:
                continue
            cur = data[0][1]
            vals = [v for _, v in data]
            result["as_of"] = data[0][0]
            result["pe"] = cur
            result["pe_pct"] = _round(_percentile(vals, cur))
            result["pe_hist_n"] = len(vals)
            # 逐年样本：约1年前 = 上一条
            if len(data) > 1:
                result["pe_pct_3m_ago"] = _round(_percentile(vals, data[1][1]))
            break
    except Exception as e:
        logger.warning(f"纳指100 取数失败: {e}")
    return result


# ============ 汇总 ============
def fetch_all() -> List[Dict]:
    """按展示顺序抓取全部目标指数估值分位"""
    rows: List[Dict] = []
    for name, code in A_SHARE_INDICES.items():
        try:
            rows.append(_fetch_a_share(name, code))
        except Exception as e:
            logger.error(f"{name} 取数异常: {e}")
            rows.append(_empty_row(name, "A"))
        time.sleep(_LG_THROTTLE_SEC)
    try:
        rows.append(_fetch_nasdaq100())
    except Exception as e:
        logger.error(f"纳指100 取数异常: {e}")
        rows.append(_empty_row("纳指100", "US"))
    order = {n: i for i, n in enumerate(INDEX_ORDER)}
    rows.sort(key=lambda r: order.get(r["name"], 99))
    return rows


# ============ 加仓/减仓/不动 判定 ============
def verdict(row: Dict) -> Dict:
    """根据 PE/PB 分位 + 近3月分位变化，给出 加仓/减仓/不动 建议。

    返回 {action, action_emoji, reason, composite_pct, trend_note}
    """
    pe_pct, pb_pct = row.get("pe_pct"), row.get("pb_pct")
    pe_3m, pb_3m = row.get("pe_pct_3m_ago"), row.get("pb_pct_3m_ago")

    # 综合分位：PE 与 PB 分位均值（PB 缺失时仅用 PE）
    pcts = [p for p in (pe_pct, pb_pct) if p is not None]
    composite = round(sum(pcts) / len(pcts), 1) if pcts else None

    # 趋势：近3月综合分位变化（负=分位下行/变便宜，正=上行/变贵）
    trend_vals = []
    if pe_pct is not None and pe_3m is not None:
        trend_vals.append(pe_pct - pe_3m)
    if pb_pct is not None and pb_3m is not None:
        trend_vals.append(pb_pct - pb_3m)
    trend = round(sum(trend_vals) / len(trend_vals), 1) if trend_vals else None

    trend_note = "分位走平"
    if trend is not None:
        if trend <= -8:
            trend_note = "近3月分位明显下行（更便宜）"
        elif trend <= -3:
            trend_note = "近3月分位小幅下行"
        elif trend >= 8:
            trend_note = "近3月分位明显上行（更贵）"
        elif trend >= 3:
            trend_note = "近3月分位小幅上行"

    if composite is None:
        return {"action": "数据不足", "action_emoji": "⚪",
                "reason": "暂无估值分位数据，无法判断", "composite_pct": None, "trend_note": trend_note}

    if composite <= 30:
        action, emoji = "加仓", "🟢"
        if trend is not None and trend <= -3:
            reason = f"综合分位{composite:.0f}%偏低且{trend_note}，可分批加仓/加大定投"
        else:
            reason = f"综合分位{composite:.0f}%偏低（低估区），适合逐步加仓/定投"
    elif composite >= 70:
        action, emoji = "减仓", "🔴"
        if trend is not None and trend >= 3:
            reason = f"综合分位{composite:.0f}%偏高且{trend_note}，逢高分批止盈减仓"
        else:
            reason = f"综合分位{composite:.0f}%偏高（高估区），建议分批止盈/减仓"
    else:
        action, emoji = "不动", "🟡"
        if trend is not None and trend <= -5:
            reason = f"综合分位{composite:.0f}%处中枢但{trend_note}，可小额定投观察"
        elif trend is not None and trend >= 5:
            reason = f"综合分位{composite:.0f}%处中枢但{trend_note}，暂停加仓持有观察"
        else:
            reason = f"综合分位{composite:.0f}%处合理中枢，持有不动"

    return {"action": action, "action_emoji": emoji, "reason": reason,
            "composite_pct": composite, "trend_note": trend_note}


def _fmt(v) -> str:
    return f"{v:.2f}" if isinstance(v, (int, float)) else "—"


def _fmt_pct(v) -> str:
    return f"{v:.0f}%" if isinstance(v, (int, float)) else "—"


def build_markdown(rows: List[Dict], now: Optional[datetime] = None) -> str:
    """生成企微推送的 Markdown 文本"""
    now = now or datetime.now()
    md = f"## 📐 宽基估值分位与加减仓建议 | {now:%Y-%m-%d %H:%M}\n\n"
    md += "> PE/PB 历史百分位（越低越便宜）；综合分位 = PE、PB 分位均值\n\n"

    add, cut, hold, unknown = [], [], [], []
    for r in rows:
        v = verdict(r)
        comp_s = _fmt_pct(v["composite_pct"])
        md += f"{v['action_emoji']} **{r['name']}** → **{v['action']}**\n"
        md += (f"- PE {_fmt(r.get('pe'))}（分位{_fmt_pct(r.get('pe_pct'))}）"
               f" | PB {_fmt(r.get('pb'))}（分位{_fmt_pct(r.get('pb_pct'))}）"
               f" | 综合分位 {comp_s}\n")
        md += f"- {v['reason']}\n\n"
        (add if v["action"] == "加仓" else
         cut if v["action"] == "减仓" else
         unknown if v["action"] == "数据不足" else hold).append(r["name"])

    md += "### 🧭 今日结论\n"
    md += f"- 🟢 偏加仓：{('、'.join(add)) if add else '无'}\n"
    md += f"- 🔴 偏减仓：{('、'.join(cut)) if cut else '无'}\n"
    md += f"- 🟡 持有观望：{('、'.join(hold)) if hold else '无'}\n"
    if unknown:
        md += f"- ⚪ 数据暂缺（稍后重试或人工核对）：{('、'.join(unknown))}\n"
    md += "\n> 数据源: 乐咕乐股(A股) / siblisresearch(纳指100) | 仅供参考，不构成投资建议"
    return md


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(build_markdown(fetch_all()))


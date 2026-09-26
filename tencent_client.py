"""
腾讯行情直连客户端（云端/海外 IP 友好）
- 日线: proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get（单次请求，支持前/后/不复权）
- 实时: qt.gtimg.cn/q=（批量，含名称/换手率/成交额）

背景：东财 push2his 的 K 线接口在云端 IP 上会频繁
      RemoteDisconnected('Remote end closed connection without response')，
      而 akshare 的 stock_zh_a_hist 同源东财，因此需要腾讯/新浪作为独立兜底数据源。

统一字段口径（与 data_collector 保持一致）：
    索引 date(datetime) / open / close / high / low / volume(股) / amount(元)
    / turnover(%) / pct_chg(%) / amplitude(%)
"""
import json
import logging
import os
import random
import ssl
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://gu.qq.com/",
    "Accept": "*/*",
}

# 日线接口：主用 newfqkline（返回换手率/成交额），备用 fqkline（仅 6 字段）
_KLINE_URLS = [
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
]
_REALTIME_URLS = [
    "https://qt.gtimg.cn/q=",
    "https://web.sqt.gtimg.cn/q=",
]

TIMEOUT = float(os.environ.get("TENCENT_TIMEOUT", "10"))
MAX_RETRIES = int(os.environ.get("TENCENT_MAX_RETRIES", "3"))


def to_tx_symbol(symbol: str) -> str:
    """股票代码转腾讯格式：000962 -> sz000962, 600738 -> sh600738, 83xxxx -> bjxxxx"""
    s = symbol.strip().lower()
    if s.startswith(("sh", "sz", "bj")):
        return s
    if s.startswith(("6", "9", "5")):
        return f"sh{s}"
    if s.startswith(("4", "8")):
        return f"bj{s}"
    return f"sz{s}"


def _num(v) -> Optional[float]:
    try:
        f = float(v)
        return None if f != f else f
    except Exception:
        return None


def _http_get(url: str, encoding: str = "utf-8",
              timeout: float = TIMEOUT, max_retries: int = MAX_RETRIES) -> str:
    """带重试的 GET，返回解码后的文本"""
    last_err = None
    for i in range(max(1, max_retries)):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read().decode(encoding, "replace")
        except Exception as e:
            last_err = e
            logger.debug(f"腾讯请求失败 {url}: {e}")
            if i < max_retries - 1:
                time.sleep(0.5 * (i + 1))
    raise last_err


def _find_json(text: str) -> Dict:
    """剥离 JSONP 前缀后解析 JSON"""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("腾讯返回内容不是 JSON")
    return json.loads(text[start:end + 1])


def _parse_kline_row(row: List) -> Optional[Dict]:
    """单行 K 线：
    [0]日期 [1]开 [2]收 [3]高 [4]低 [5]成交量(手) [6]{} [7]换手率(%) [8]成交额(万元)
    """
    try:
        if len(row) < 6 or not row[0]:
            return None
        open_ = _num(row[1])
        close = _num(row[2])
        high = _num(row[3])
        low = _num(row[4])
        vol = _num(row[5])
        item = {
            "date": str(row[0])[:10],
            "open": open_,
            "close": close,
            "high": high,
            "low": low,
            "volume": None if vol is None else vol * 100,  # 手 -> 股
            "amount": None,
            "turnover": None,   # 已是百分比，供 L5 换手率判断使用
            "pct_chg": None,
            "amplitude": None,
        }
        if len(row) > 7:
            item["turnover"] = _num(row[7])
        if len(row) > 8:
            amt = _num(row[8])
            item["amount"] = None if amt is None else amt * 10000  # 万元 -> 元
        if None not in (open_, close, high, low):
            if low:
                item["amplitude"] = round((high - low) / low * 100, 2)
        return item
    except Exception:
        return None


def get_daily_kline(symbol: str, days: int = 120, adjust: str = "qfq") -> List[Dict]:
    """获取日线 K 线（默认前复权），单次请求即可拿到近 N 根"""
    tx_symbol = to_tx_symbol(symbol)
    limit = min(max(int(days) * 2, 120), 640)
    key = {"qfq": "qfqday", "hfq": "hfqday", "": "day"}.get(adjust, "day")

    last_err = None
    for url in _KLINE_URLS:
        try:
            if "newfqkline" in url:
                params = {
                    "_var": f"kline_{adjust or 'day'}",
                    "param": f"{tx_symbol},day,,,{limit},{adjust}",
                    "r": f"{random.random():.6f}",
                }
            else:
                params = {"param": f"{tx_symbol},day,,,{limit},{adjust}"}
            text = _http_get(f"{url}?{urllib.parse.urlencode(params)}")
            data = (_find_json(text).get("data") or {}).get(tx_symbol) or {}
            rows = data.get(key) or data.get("day") or data.get("qfqday") or []
            klines = [k for k in (_parse_kline_row(r) for r in rows) if k]
            if klines:
                return klines[-limit:]
            logger.debug(f"腾讯日线为空: {url.split('/')[2]} {symbol}")
        except Exception as e:
            last_err = e
            logger.debug(f"腾讯日线失败({url.split('/')[2]}): {e}")

    if last_err is not None:
        logger.warning(f"{symbol} 腾讯日线获取失败: {last_err}")
    return []


def daily_to_dataframe(symbol: str, days: int = 60, adjust: str = "qfq") -> pd.DataFrame:
    """获取日线并转为标准 DataFrame（兼容 data_collector 接口）"""
    klines = get_daily_kline(symbol, days=days, adjust=adjust)
    if not klines:
        return pd.DataFrame()

    df = pd.DataFrame(klines)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).set_index("date").sort_index()
    df = df[~df.index.duplicated(keep="last")]

    if "turnover" not in df.columns:
        df["turnover"] = 0.0
    else:
        df["turnover"] = pd.to_numeric(df["turnover"], errors="coerce").fillna(0.0)

    if "pct_chg" not in df.columns or df["pct_chg"].isna().all():
        df["pct_chg"] = df["close"].pct_change() * 100

    return df.tail(days)


# ===== 实时行情 =====

def _parse_realtime_line(line: str) -> Optional[Dict]:
    """解析 qt.gtimg.cn 单行：v_sz000962="51~名称~代码~现价~昨收~今开~成交量(手)~..."
    关键位: 1名称 2代码 3现价 4昨收 5今开 6成交量(手) 31涨跌额 32涨跌幅%
            33最高 34最低 37成交额(万元) 38换手率 39市盈率 43振幅 49量比
    """
    try:
        if "=" not in line:
            return None
        body = line.split("=", 1)[1].strip().strip(";").strip('"')
        p = body.split("~")
        if len(p) < 45:
            return None
        vol = _num(p[6])
        amount_wan = _num(p[37])
        high, low, prev = _num(p[33]), _num(p[34]), _num(p[4])
        amplitude = _num(p[43])
        if amplitude is None and None not in (high, low, prev) and prev:
            amplitude = (high - low) / prev * 100
        return {
            "代码": p[2],
            "名称": p[1],
            "最新价": _num(p[3]),
            "涨跌幅": _num(p[32]),
            "涨跌额": _num(p[31]),
            "成交量": None if vol is None else vol * 100,                  # 手 -> 股
            "成交额": None if amount_wan is None else amount_wan * 10000,  # 万元 -> 元
            "振幅": amplitude,
            "换手率": _num(p[38]),
            "市盈率": _num(p[39]),
            "量比": _num(p[49]) if len(p) > 49 else None,
            "昨收": prev,
            "今开": _num(p[5]),
            "最高": high,
            "最低": low,
        }
    except Exception:
        return None


def get_realtime_symbols(symbols: List[str]) -> List[Dict]:
    """批量获取实时行情（每批 60 只，单请求）"""
    if not symbols:
        return []
    result: List[Dict] = []
    batch_size = 60
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        query = ",".join(to_tx_symbol(s) for s in batch)
        last_err = None
        for base in _REALTIME_URLS:
            try:
                text = _http_get(base + query, encoding="gbk")
                for line in text.strip().split(";"):
                    item = _parse_realtime_line(line.strip())
                    if item:
                        result.append(item)
                last_err = None
                break
            except Exception as e:
                last_err = e
                logger.debug(f"腾讯实时行情失败({base}): {e}")
        if last_err is not None:
            logger.warning(f"腾讯实时行情失败: {last_err}")
    return result


def realtime_to_dataframe(symbols: List[str]) -> pd.DataFrame:
    """获取实时行情并转 DataFrame（列名与东财版保持一致）"""
    data = get_realtime_symbols(symbols)
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data)


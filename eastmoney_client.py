"""
东财直连客户端 - 移植自 llmstudio/fetch_movers.py
支持海外主机、指数退避重试、多主机轮询
"""
import urllib.request
import json
import os
import ssl
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
import pandas as pd

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

# 公开 UT Token（akshare 同源）
UT = "bd1d9ddb04089700cf9c27f6f7426281"
UT_KLINE = "fa5fd1943c67418ea634a5f3508544a5fee1ac"

# 行情/快照主机池（clist/ulist/stock 接口）
_HOSTS_CN = [
    "82.push2.eastmoney.com",
    "33.push2.eastmoney.com", 
    "17.push2.eastmoney.com",
    "push2.eastmoney.com",
    "push2delay.eastmoney.com",   # 海外专用延迟行情主机（仅实时快照，无历史K线）
]

# K线专用主机：实测仅 push2his 提供历史 K 线
# （push2delay 对 kline 接口返回空 klines，拿它做兜底只会白等）
KLINE_HOSTS = [h.strip() for h in os.environ.get(
    "EM_KLINE_HOSTS", "push2his.eastmoney.com").split(",") if h.strip()]

# 海外环境变量控制主机优先级
if os.environ.get("EM_OVERSEAS", "") in ("1", "true"):
    HOSTS = ["push2delay.eastmoney.com"] + [h for h in _HOSTS_CN if h != "push2delay.eastmoney.com"]
else:
    HOSTS = _HOSTS_CN

CONTEXT = ssl.create_default_context()
CONTEXT.check_hostname = False
CONTEXT.verify_mode = ssl.CERT_NONE

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://quote.eastmoney.com/",
    "Connection": "close",
}


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None


def get_json(path: str, hosts: List[str] = None, max_retries: int = None,
             timeout: float = None) -> Dict:
    """多主机轮询 + 重试（云端 IP 常被东财直接断连，失败后快速轮换/重试）

    可用环境变量调整：EM_MAX_RETRIES（默认 5）、EM_TIMEOUT（默认 10 秒）
    """
    hosts = hosts or HOSTS
    max_retries = max_retries or int(os.environ.get("EM_MAX_RETRIES", "5"))
    timeout = timeout or float(os.environ.get("EM_TIMEOUT", "10"))
    backoff = (0, 1, 2, 3, 4, 5, 5, 5)
    last_err = None
    for i in range(max(1, max_retries)):
        host = hosts[i % len(hosts)]
        try:
            url = f"https://{host}{path}"
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=CONTEXT) as r:
                data = json.loads(r.read().decode("utf-8"))
                logger.debug(f"东财请求成功: {host}{path}")
                return data
        except Exception as e:
            last_err = e
            logger.debug(f"东财请求失败 {host}{path}: {e}")
            if i < max_retries - 1:
                time.sleep(backoff[min(i, len(backoff) - 1)])
    raise last_err


class EastMoneyClient:
    """东财 API 统一封装"""
    
    def __init__(self):
        self.ut = UT
        self.ut_kline = UT_KLINE
    
    # ===== 1. 全市场实时快照 =====
    def get_realtime_all(self) -> List[Dict]:
        """获取全市场实时行情（clist 接口）"""
        path = (
            "/api/qt/clist/get?pn=1&pz=5000&po=1&np=1"
            f"&ut={self.ut}&fltt=2&invt=2&fid=f3"
            "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
            "&fields=f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13,f14,f15,f16,f17,f18,f20,"
            "f62,f100,f128,f140,f141,f142,f144,f145,f146,f147,f148,f149,f150,f151,f152"
        )
        try:
            d = get_json(path)
            diff = (d.get("data") or {}).get("diff", [])
            return diff
        except Exception as e:
            logger.error(f"获取全市场实时行情失败: {e}")
            return []
    
    def get_realtime_symbols(self, symbols: List[str]) -> List[Dict]:
        """获取指定股票实时行情"""
        if not symbols:
            return []
        secids = []
        for s in symbols:
            if s.startswith(("6", "68")):
                secids.append(f"1.{s}")
            else:
                secids.append(f"0.{s}")
        path = (
            f"/api/qt/ulist.np/get?fltt=2&invt=2&ut={self.ut}"
            f"&secids={','.join(secids)}"
            "&fields=f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13,f14,f15,f16,f17,f18,f20,"
            "f43,f44,f45,f46,f47,f48,f49,f50,f57,f58,f60,f62,f100,f116,f117,f162,f163,"
            "f164,f167,f168,f170,f171,f173,f184,f186"
        )
        try:
            d = get_json(path)
            diff = (d.get("data") or {}).get("diff", [])
            return diff
        except Exception as e:
            logger.error(f"获取指定股票实时行情失败: {e}")
            return []
    
    # ===== 2. 历史日线 K线 =====
    def get_daily_kline(self, symbol: str, days: int = 120) -> List[Dict]:
        """获取日线 K 线（前复权 fqt=1）"""
        if symbol.startswith(("6", "68")):
            secid = f"1.{symbol}"
        else:
            secid = f"0.{symbol}"
        
        path = (
            f"/api/qt/stock/kline/get?secid={secid}&klt=101&fqt=1"
            f"&lmt={days}&end=20500101"
            f"&ut={self.ut_kline}"
            "&fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        )
        try:
            d = get_json(path, hosts=KLINE_HOSTS)
            klines = (d.get("data") or {}).get("klines") or []
            result = []
            for line in klines:
                p = line.split(",")
                if len(p) < 6:
                    continue
                result.append({
                    "date": p[0],
                    "open": _num(p[1]),
                    "close": _num(p[2]),
                    "high": _num(p[3]),
                    "low": _num(p[4]),
                    "volume": _num(p[5]),   # 单位：手（统一在 daily_to_dataframe 转股）
                    "amount": _num(p[6]) if len(p) > 6 else None,
                    "amplitude": _num(p[7]) if len(p) > 7 else None,
                    "pct_chg": _num(p[8]) if len(p) > 8 else None,
                    "change": _num(p[9]) if len(p) > 9 else None,
                    "turnover": _num(p[10]) if len(p) > 10 else None,
                })
            if not result:
                logger.warning(f"获取 {symbol} 日线为空（{KLINE_HOSTS[0]} 返回空 klines）")
            return result
        except Exception as e:
            logger.error(f"获取 {symbol} 日线失败: {e}")
            return []
    
    # ===== 3. 单股票详细实时+估值+财务 =====
    def get_stock_detail(self, symbol: str) -> Dict:
        """获取单股票详细数据（实时指标+估值+财务）"""
        if symbol.startswith(("6", "68")):
            secid = f"1.{symbol}"
        else:
            secid = f"0.{symbol}"
        
        path = (
            f"/api/qt/stock/get?secid={secid}"
            f"&ut={self.ut}&fltt=2&invt=2"
            "&fields=f43,f44,f45,f46,f47,f48,f49,f50,f57,f58,f60,f62,f100,"
            "f116,f117,f162,f163,f164,f167,f168,f170,f171,f173,f184,f186"
        )
        try:
            d = get_json(path)
            dd = d.get("data") or {}
            if not dd or dd.get("f43") in (None, "-"):
                return {}
            return dd
        except Exception as e:
            logger.error(f"获取 {symbol} 详细数据失败: {e}")
            return {}
    
    # ===== 4. 指数实时 =====
    def get_indices(self) -> List[Dict]:
        """获取主要指数实时"""
        path = (
            "/api/qt/ulist.np/get?fltt=2&invt=2"
            f"&ut={self.ut}&secids=1.000001,0.399001,0.399006,1.000688"
            "&fields=f2,f3,f12,f14"
        )
        try:
            d = get_json(path)
            diff = (d.get("data") or {}).get("diff", [])
            result = []
            for it in diff:
                chg = _num(it.get("f3"))
                if chg is not None:
                    result.append({
                        "name": it["f14"], 
                        "close": _num(it.get("f2")), 
                        "change": chg
                    })
            return result
        except Exception as e:
            logger.error(f"获取指数失败: {e}")
            return []
    
    # ===== 5. 板块涨幅榜 =====
    def get_sector_rank(self, sector_type: str = "industry", top: int = 20) -> List[Dict]:
        """获取板块涨幅榜
        sector_type: industry=行业, concept=概念
        """
        fs = "m:90+t:2" if sector_type == "industry" else "m:90+t:3"
        path = (
            f"/api/qt/clist/get?pn=1&pz={top}&po=1&np=1"
            f"&ut={self.ut}&fltt=2&invt=2&fid=f3&fs={fs}"
            "&fields=f3,f14,f128"
        )
        try:
            d = get_json(path)
            diff = (d.get("data") or {}).get("diff", [])
            result = []
            for it in diff:
                chg = _num(it.get("f3"))
                if chg is not None:
                    result.append({
                        "name": it["f14"], 
                        "change": chg, 
                        "leader": it.get("f128", "") or ""
                    })
            return result
        except Exception as e:
            logger.error(f"获取板块榜失败: {e}")
            return []
    
    # ===== 5. 涨停池 / 异动股 =====
    def get_limit_up_pool(self, min_change: float = 5.0, min_amount: float = 2e8, top_n: int = 20) -> List[Dict]:
        """获取涨幅榜/异动股"""
        path = (
            "/api/qt/clist/get?pn=1&pz=100&po=1&np=1"
            f"&ut={self.ut}&fltt=2&invt=2&fid=f3"
            "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
            "&fields=f2,f3,f6,f8,f12,f14,f62,f100"
        )
        try:
            d = get_json(path)
            diff = (d.get("data") or {}).get("diff", [])
            result = []
            for it in diff:
                chg = _num(it.get("f3"))
                amt = _num(it.get("f6"))
                name = it.get("f14", "") or ""
                if chg is None or chg < min_change:
                    continue
                if "ST" in name.upper() or "退" in name:
                    continue
                if amt is None or amt < min_amount:
                    continue
                inflow = _num(it.get("f62"))
                result.append({
                    "code": it.get("f12"), 
                    "name": name, 
                    "change": chg,
                    "price": it.get("f2"),
                    "amount_yi": round(amt / 1e8, 2),
                    "turnover": _num(it.get("f8")),
                    "main_inflow_yi": round(inflow / 1e8, 2) if inflow is not None else None,
                    "sector": it.get("f100", "") or "",
                })
                if len(result) >= top_n:
                    break
            return result
        except Exception as e:
            logger.error(f"获取涨停池失败: {e}")
            return []


# 单例
_client_instance = None

def get_client() -> EastMoneyClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = EastMoneyClient()
    return _client_instance


# ===== 便捷函数：转 DataFrame 格式（兼容原有接口）=====
def daily_to_dataframe(symbol: str, days: int = 60) -> pd.DataFrame:
    """获取日线并转为标准 DataFrame（兼容原 data_collector 接口）
    统一口径：volume=股 / amount=元 / turnover=% / pct_chg=%
    """
    client = get_client()
    klines = client.get_daily_kline(symbol, days=days)
    if not klines:
        return pd.DataFrame()
    
    df = pd.DataFrame(klines)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    df = df[~df.index.duplicated(keep='last')]
    # 成交量：手 -> 股（与腾讯/新浪数据口径一致）
    if 'volume' in df.columns:
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce') * 100
    # 补齐字段
    if 'turnover' not in df.columns:
        df['turnover'] = 0.0
    else:
        df['turnover'] = pd.to_numeric(df['turnover'], errors='coerce').fillna(0.0)
    if 'pct_chg' not in df.columns or df['pct_chg'].isna().all():
        df['pct_chg'] = df['close'].pct_change() * 100
    return df.tail(days)


def realtime_to_dataframe(symbols: List[str]) -> pd.DataFrame:
    """获取实时行情并转 DataFrame"""
    client = get_client()
    data = client.get_realtime_symbols(symbols)
    if not data:
        return pd.DataFrame()
    
    rows = []
    for it in data:
        vol = _num(it.get('f5'))
        rows.append({
            '代码': it.get('f12'),
            '名称': it.get('f14'),
            '最新价': _num(it.get('f2')),
            '涨跌幅': _num(it.get('f3')),
            '涨跌额': _num(it.get('f4')),
            '成交量': vol * 100 if vol is not None else None,  # 手 -> 股
            '成交额': _num(it.get('f6')),
            '振幅': _num(it.get('f7')),
            '换手率': _num(it.get('f8')),
            '市盈率': _num(it.get('f9')),
            '量比': _num(it.get('f10')),
        })
    return pd.DataFrame(rows)
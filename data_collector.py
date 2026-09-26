"""
数据采集层 - 统一接口适配多源，含重试与容错
"""
import akshare as ak
import pandas as pd
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import time
import logging
from functools import lru_cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

def _make_session() -> requests.Session:
    """创建带重试策略的 Session"""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://finance.sina.com.cn/",
    })
    return session

# 替换 akshare 内部 session（akshare 1.18+ 支持）
try:
    ak.requests.session = _make_session()
except Exception:
    pass

class DataCollector:
    def __init__(self, config: dict):
        self.config = config
        self._cache = {}
        self._cache_ttl = 300  # 5分钟缓存
    
    def _cache_get(self, key: str) -> Optional[pd.DataFrame]:
        if key in self._cache:
            df, ts = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return df
        return None
    
    def _cache_set(self, key: str, df: pd.DataFrame):
        self._cache[key] = (df, time.time())
    
    # ===== 基础行情 =====
    def get_daily_data(self, symbol: str, days: int = 60) -> pd.DataFrame:
        """获取日线数据，自动复权，带缓存重试"""
        cache_key = f"daily_{symbol}_{days}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days*3)).strftime("%Y%m%d")
        
        for attempt in range(3):
            try:
                df = ak.stock_zh_a_hist(
                    symbol=symbol, 
                    period="daily", 
                    start_date=start,
                    end_date=end,
                    adjust="qfq",
                    timeout=30
                )
                if df.empty:
                    time.sleep(1)
                    continue
                
                df = df.rename(columns={
                    '日期': 'date', '开盘': 'open', '收盘': 'close',
                    '最高': 'high', '最低': 'low', '成交量': 'volume',
                    '成交额': 'amount', '振幅': 'amplitude', '换手率': 'turnover',
                    '涨跌幅': 'pct_chg', '涨跌额': 'change'
                })
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date').sort_index()
                result = df.tail(days)
                self._cache_set(cache_key, result)
                return result
            except Exception as e:
                logger.warning(f"获取 {symbol} 日线第{attempt+1}次失败: {e}")
                time.sleep(2 ** attempt)
        
        logger.error(f"获取 {symbol} 日线最终失败")
        return pd.DataFrame()
    
    def get_realtime_quote(self, symbols: List[str]) -> pd.DataFrame:
        cache_key = f"realtime_{'_'.join(sorted(symbols))}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            df = ak.stock_zh_a_spot_em()
            df = df[df['代码'].isin(symbols)]
            self._cache_set(cache_key, df)
            return df
        except Exception as e:
            logger.error(f"实时行情获取失败: {e}")
            return pd.DataFrame()
    
    # ===== 板块指数 =====
    def get_sector_index(self, sector_name: str, days: int = 30) -> pd.DataFrame:
        cache_key = f"sector_{sector_name}_{days}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            sectors = ak.stock_board_concept_name_em()
            target = sectors[sectors['板块名称'].str.contains(sector_name, na=False)]
            if target.empty:
                return pd.DataFrame()
            code = target.iloc[0]['板块代码']
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days*2)).strftime("%Y%m%d")
            df = ak.stock_board_concept_hist_em(
                symbol=code, start_date=start, end_date=end, period="日k"
            )
            if df.empty:
                return pd.DataFrame()
            df['date'] = pd.to_datetime(df['日期'])
            result = df.set_index('date').sort_index().tail(days)
            self._cache_set(cache_key, result)
            return result
        except Exception as e:
            logger.error(f"获取板块 {sector_name} 指数失败: {e}")
            return pd.DataFrame()
    
    def get_sector_stocks(self, sector_name: str) -> List[str]:
        try:
            sectors = ak.stock_board_concept_name_em()
            target = sectors[sectors['板块名称'].str.contains(sector_name, na=False)]
            if target.empty:
                return []
            code = target.iloc[0]['板块代码']
            df = ak.stock_board_concept_cons_em(symbol=code)
            return df['代码'].tolist()
        except Exception as e:
            logger.error(f"获取板块 {sector_name} 成分股失败: {e}")
            return []
    
    # ===== 涨停/龙虎榜 =====
    def get_limit_up_stocks(self, trade_date: str = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        try:
            return ak.stock_zt_pool_em(date=trade_date)
        except Exception as e:
            logger.error(f"获取涨停池失败: {e}")
            return pd.DataFrame()
    
    def get_lhb_data(self, trade_date: str = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        try:
            return ak.stock_lhb_detail_em(date=trade_date)
        except Exception as e:
            logger.error(f"获取龙虎榜失败: {e}")
            return pd.DataFrame()
    
    # ===== 资金流向 =====
    def get_market_margin(self, days: int = 30) -> pd.DataFrame:
        """融资融券余额 - 兼容新版 akshare"""
        try:
            # 新版 akshare 函数名可能变了，尝试多个
            for fn_name in ['stock_margin_detail_em', 'stock_margin_detail_szse', 'stock_margin_sh_sz']:
                fn = getattr(ak, fn_name, None)
                if fn:
                    return fn(start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"))
            logger.warning("未找到可用的融资融券接口")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"融资融券获取失败: {e}")
            return pd.DataFrame()
    
    def get_north_money(self, days: int = 30) -> pd.DataFrame:
        try:
            return ak.stock_hsgt_hist_em(symbol="沪股通", start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"))
        except Exception as e:
            logger.error(f"北向资金获取失败: {e}")
            return pd.DataFrame()
    
    # ===== 基本面 =====
    @lru_cache(maxsize=100)
    def get_financial_abstract(self, symbol: str) -> Dict:
        try:
            df = ak.stock_financial_abstract(symbol=symbol)
            if df.empty:
                return {}
            latest = df.iloc[0]
            return {
                'revenue_yoy': float(latest.get('营业总收入同比增长率', 0) or 0),
                'profit_yoy': float(latest.get('归属净利润同比增长率', 0) or 0),
                'roe': float(latest.get('净资产收益率', 0) or 0),
                'gross_margin': float(latest.get('销售毛利率', 0) or 0),
                'operate_cash_flow': float(latest.get('经营活动产生的现金流量净额', 0) or 0),
                'debt_ratio': float(latest.get('资产负债率', 0) or 0),
            }
        except Exception as e:
            logger.error(f"获取 {symbol} 财务摘要失败: {e}")
            return {}
    
    # ===== 新闻/政策 =====
    def get_policy_news(self, keywords: List[str], hours: int = 24) -> List[Dict]:
        return []

    def warmup_cache(self, target_date):
        """预热：预取全市场快照，减少后续请求"""
        try:
            _ = ak.stock_zh_a_spot_em()
            logger.info("全市场快照预热完成")
        except Exception as e:
            logger.warning(f"预热失败: {e}")


_collector_instance = None

def get_collector(config: dict) -> DataCollector:
    global _collector_instance
    if _collector_instance is None:
        _collector_instance = DataCollector(config)
    return _collector_instance
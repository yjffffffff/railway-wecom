"""
数据采集层 - 统一接口适配多源
优先：tushare Pro (云端稳定) -> 东财直连 -> 兜底：akshare
"""
import pandas as pd
import time
import logging
from functools import lru_cache
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import os

logger = logging.getLogger(__name__)

# 尝试导入 tushare Pro
try:
    import tushare as ts
    TUSHARE_AVAILABLE = True
except ImportError:
    TUSHARE_AVAILABLE = False
    logger.warning("tushare 不可用")

# 尝试导入东财客户端
try:
    from eastmoney_client import (
        get_client, daily_to_dataframe, realtime_to_dataframe,
        EastMoneyClient
    )
    EASTMONEY_AVAILABLE = True
except ImportError:
    EASTMONEY_AVAILABLE = False
    logger.warning("eastmoney_client 不可用")

# 尝试导入 akshare 作为兜底
try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False
    logger.warning("akshare 不可用")


class DataCollector:
    def __init__(self, config: dict):
        self.config = config
        
        # 数据源优先级配置
        ds = config.get('data_source', {})
        self.use_tushare = ds.get('use_tushare', True) and TUSHARE_AVAILABLE
        self.use_eastmoney = ds.get('use_eastmoney', True) and EASTMONEY_AVAILABLE
        self.use_akshare = ds.get('use_akshare', True) and AKSHARE_AVAILABLE
        
        # tushare Pro 初始化
        self.ts_pro = None
        if self.use_tushare:
            token = ds.get('tushare', {}).get('token') or os.environ.get('TUSHARE_TOKEN')
            if token:
                try:
                    ts.set_token(token)
                    self.ts_pro = ts.pro_api()
                    logger.info("tushare Pro 初始化成功")
                except Exception as e:
                    logger.warning(f"tushare Pro 初始化失败: {e}")
                    self.use_tushare = False
            else:
                logger.warning("未配置 TUSHARE_TOKEN，跳过 tushare")
                self.use_tushare = False
        
        # 缓存
        self._cache = {}
        self._cache_ttl = 300
    
    def _cache_get(self, key: str) -> Optional[pd.DataFrame]:
        if key in self._cache:
            df, ts_ = self._cache[key]
            if time.time() - ts_ < self._cache_ttl:
                return df
        return None
    
    def _cache_set(self, key: str, df: pd.DataFrame):
        self._cache[key] = (df, time.time())
    
    # ===== 基础行情 =====
    def get_daily_data(self, symbol: str, days: int = 60) -> pd.DataFrame:
        """获取日线数据：tushare Pro -> 东财直连 -> akshare"""
        cache_key = f"daily_{symbol}_{days}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        
        # 1. 优先 tushare Pro
        if self.use_tushare:
            df = self._get_daily_tushare(symbol, days)
            if not df.empty:
                self._cache_set(cache_key, df)
                logger.debug(f"{symbol} tushare Pro 获取日线成功")
                return df
        
        # 2. 东财直连
        if self.use_eastmoney:
            try:
                df = daily_to_dataframe(symbol, days=days)
                if not df.empty:
                    self._cache_set(cache_key, df)
                    logger.debug(f"{symbol} 东财直连获取日线成功")
                    return df
            except Exception as e:
                logger.warning(f"{symbol} 东财获取日线失败: {e}")
        
        # 3. 兜底 akshare
        if self.use_akshare:
            df = self._get_daily_akshare(symbol, days)
            if not df.empty:
                self._cache_set(cache_key, df)
                return df
        
        logger.error(f"获取 {symbol} 日线最终失败：所有数据源均不可用")
        return pd.DataFrame()
    
    def _get_daily_tushare(self, symbol: str, days: int) -> pd.DataFrame:
        """tushare Pro 获取日线（前复权）"""
        try:
            # tushare 代码格式：600000.SH / 000001.SZ
            if symbol.startswith(("6", "68")):
                ts_code = f"{symbol}.SH"
            else:
                ts_code = f"{symbol}.SZ"
            
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days*3)).strftime("%Y%m%d")
            
            # pro_bar 支持复权、自动获取
            df = ts.pro_bar(
                ts_code=ts_code,
                adj='qfq',  # 前复权
                start_date=start,
                end_date=end,
                freq='D',
                asset='E'
            )
            
            if df is None or df.empty:
                return pd.DataFrame()
            
            # 标准化列名
            df = df.rename(columns={
                'trade_date': 'date', 'open': 'open', 'close': 'close',
                'high': 'high', 'low': 'low', 'vol': 'volume',
                'amount': 'amount', 'pct_chg': 'pct_chg', 'change': 'change'
            })
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date').sort_index()
            
            # 补齐字段
            if 'turnover' not in df.columns:
                df['turnover'] = df['volume'] / df['volume'].rolling(20).mean() * 100
            if 'amplitude' not in df.columns:
                df['amplitude'] = (df['high'] - df['low']) / df['low'] * 100
            
            return df.tail(days)
            
        except Exception as e:
            logger.warning(f"{symbol} tushare Pro 获取日线失败: {e}")
            return pd.DataFrame()
    
    def _get_daily_akshare(self, symbol: str, days: int) -> pd.DataFrame:
        """akshare 兜底获取日线"""
        import akshare as ak
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days*3)).strftime("%Y%m%d")
        for attempt in range(2):
            try:
                df = ak.stock_zh_a_hist(
                    symbol=symbol, period="daily", start_date=start, end_date=end,
                    adjust="qfq", timeout=30
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
                return df.tail(days)
            except Exception as e:
                logger.warning(f"{symbol} akshare 第{attempt+1}次失败: {e}")
                time.sleep(2)
        return pd.DataFrame()
    
    def get_realtime_quote(self, symbols: List[str]) -> pd.DataFrame:
        """实时行情快照"""
        cache_key = f"realtime_{'_'.join(sorted(symbols))}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        
        # 优先东财直连
        if self.use_eastmoney:
            try:
                df = realtime_to_dataframe(symbols)
                if not df.empty:
                    self._cache_set(cache_key, df)
                    return df
            except Exception as e:
                logger.warning(f"东财实时行情失败: {e}")
        
        # 兜底 akshare
        if self.use_akshare:
            try:
                import akshare as ak
                df = ak.stock_zh_a_spot_em()
                df = df[df['代码'].isin(symbols)]
                self._cache_set(cache_key, df)
                return df
            except Exception as e:
                logger.warning(f"akshare 实时行情失败: {e}")
        
        return pd.DataFrame()
    
    # ===== 板块指数 =====
    def get_sector_index(self, sector_name: str, days: int = 30) -> pd.DataFrame:
        if self.use_tushare:
            try:
                # tushare 概念/行业指数
                df = self.ts_pro.index_daily(ts_code='', start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"))
                if not df.empty:
                    return df
            except Exception:
                pass
        return pd.DataFrame()
    
    def get_sector_stocks(self, sector_name: str) -> List[str]:
        if self.use_tushare:
            try:
                # 可以用 tushare 获取概念/行业成分股
                pass
            except Exception:
                pass
        return []
    
    # ===== 涨停/龙虎榜 =====
    def get_limit_up_stocks(self, trade_date: str = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        
        if self.use_eastmoney:
            try:
                client = get_client()
                movers = client.get_limit_up_pool(min_change=5.0, min_amount=2e8, top_n=50)
                if movers:
                    return pd.DataFrame(movers)
            except Exception as e:
                logger.warning(f"东财涨停池失败: {e}")
        
        if self.use_akshare:
            try:
                import akshare as ak
                return ak.stock_zt_pool_em(date=trade_date)
            except Exception as e:
                logger.warning(f"akshare 涨停池失败: {e}")
        
        return pd.DataFrame()
    
    def get_lhb_data(self, trade_date: str = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        if self.use_akshare:
            try:
                import akshare as ak
                return ak.stock_lhb_detail_em(date=trade_date)
            except Exception as e:
                logger.error(f"龙虎榜获取失败: {e}")
        return pd.DataFrame()
    
    # ===== 资金流向 =====
    def get_market_margin(self, days: int = 30) -> pd.DataFrame:
        if self.use_tushare:
            try:
                df = self.ts_pro.margin(start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"))
                return df
            except Exception:
                pass
        if self.use_akshare:
            try:
                import akshare as ak
                for fn_name in ['stock_margin_detail_em', 'stock_margin_detail_szse', 'stock_margin_sh_sz']:
                    fn = getattr(ak, fn_name, None)
                    if fn:
                        return fn(start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"))
            except Exception as e:
                logger.error(f"融资融券获取失败: {e}")
        return pd.DataFrame()
    
    def get_north_money(self, days: int = 30) -> pd.DataFrame:
        if self.use_tushare:
            try:
                df = self.ts_pro.moneyflow_hsgt(start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"))
                return df
            except Exception:
                pass
        if self.use_akshare:
            try:
                import akshare as ak
                return ak.stock_hsgt_hist_em(symbol="沪股通", start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"))
            except Exception as e:
                logger.error(f"北向资金获取失败: {e}")
        return pd.DataFrame()
    
    # ===== 基本面 =====
    @lru_cache(maxsize=100)
    def get_financial_abstract(self, symbol: str) -> Dict:
        """核心财务指标：tushare Pro 优先"""
        if self.use_tushare:
            try:
                if symbol.startswith(("6", "68")):
                    ts_code = f"{symbol}.SH"
                else:
                    ts_code = f"{symbol}.SZ"
                
                # 主要财务指标
                df = self.ts_pro.fina_indicator(ts_code=ts_code, period='20240630')
                if not df.empty:
                    latest = df.iloc[0]
                    return {
                        'revenue_yoy': float(latest.get('revenue_yoy', 0) or 0),
                        'profit_yoy': float(latest.get('profit_yoy', 0) or 0),
                        'roe': float(latest.get('roe', 0) or 0),
                        'gross_margin': float(latest.get('grossprofit_margin', 0) or 0),
                        'operate_cash_flow': float(latest.get('operate_cash_flow', 0) or 0),
                        'debt_ratio': float(latest.get('debt_to_assets', 0) or 0),
                    }
            except Exception as e:
                logger.warning(f"{symbol} tushare 财务失败: {e}")
        
        # 东财详细接口兜底
        if self.use_eastmoney:
            try:
                client = get_client()
                dd = client.get_stock_detail(symbol)
                if dd:
                    return {
                        'revenue_yoy': 0,
                        'profit_yoy': 0,
                        'roe': _num(dd.get('f173')),
                        'gross_margin': 0,
                        'operate_cash_flow': 0,
                        'debt_ratio': 0,
                    }
            except Exception as e:
                logger.warning(f"东财财务兜底失败: {e}")
        
        # akshare 兜底
        if self.use_akshare:
            try:
                import akshare as ak
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
    
    def get_policy_news(self, keywords: List[str], hours: int = 24) -> List[Dict]:
        return []
    
    def warmup_cache(self, target_date):
        """预热：预取全市场快照"""
        if self.use_eastmoney:
            try:
                client = get_client()
                _ = client.get_realtime_all()
                logger.info("东财全市场快照预热完成")
            except Exception as e:
                logger.warning(f"预热失败: {e}")


_collector_instance = None

def get_collector(config: dict) -> DataCollector:
    global _collector_instance
    if _collector_instance is None:
        _collector_instance = DataCollector(config)
    return _collector_instance


def _num(v):
    try:
        return float(v)
    except Exception:
        return None
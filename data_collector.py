"""
数据采集层 - 统一接口适配多源
日线优先级（可用 data_source.daily_priority 调整）：
    tushare Pro -> 腾讯直连 -> 东财直连 -> akshare(新浪) -> akshare(东财)

说明：云端 IP 常被东财 push2his K线接口断连
      （RemoteDisconnected: Remote end closed connection without response），
      而 akshare 的 stock_zh_a_hist 同源东财，因此引入腾讯/新浪独立数据源。
"""
import pandas as pd
import time
import logging
from functools import lru_cache
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import os

logger = logging.getLogger(__name__)


def _market_symbol(symbol: str) -> str:
    """000962 -> sz000962（akshare 新浪接口要求带市场前缀）"""
    s = symbol.strip().lower()
    if s.startswith(("sh", "sz", "bj")):
        return s
    if s.startswith(("6", "9", "5")):
        return f"sh{s}"
    if s.startswith(("4", "8")):
        return f"bj{s}"
    return f"sz{s}"

# 尝试导入 tushare Pro
try:
    import tushare as ts
    TUSHARE_AVAILABLE = True
except ImportError:
    TUSHARE_AVAILABLE = False
    logger.warning("tushare 不可用（可选数据源，不影响腾讯/东财/akshare 兜底）")

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

# 尝试导入腾讯直连（云端友好：历史日线 + 实时行情）
try:
    from tencent_client import (
        daily_to_dataframe as tencent_daily_to_dataframe,
        realtime_to_dataframe as tencent_realtime_to_dataframe,
    )
    TENCENT_AVAILABLE = True
except ImportError:
    TENCENT_AVAILABLE = False
    logger.warning("tencent_client 不可用")

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
        
        # 数据源开关
        ds = config.get('data_source', {})
        self.use_tushare = ds.get('use_tushare', True) and TUSHARE_AVAILABLE
        self.use_eastmoney = ds.get('use_eastmoney', True) and EASTMONEY_AVAILABLE
        self.use_akshare = ds.get('use_akshare', True) and AKSHARE_AVAILABLE
        self.use_tencent = ds.get('use_tencent', True) and TENCENT_AVAILABLE

        # 日线数据源优先级（可在 config/triggers.yaml 覆盖）
        self.daily_priority = list(ds.get('daily_priority') or
                                   ['tushare', 'tencent', 'eastmoney', 'akshare'])

        # 数据源熔断：某源连续失败后冷却一段时间，避免每个股票都白等超时
        self._source_fails = {}
        self._source_down_until = {}
        self._source_cooldown = float(os.environ.get('SOURCE_COOLDOWN_SECONDS', '600'))
        
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

    # ===== 数据源熔断 =====
    def _source_ok(self, name: str) -> bool:
        """冷却期内视为不可用直接跳过（云端 IP 被拒连时避免反复超时）"""
        return time.time() >= self._source_down_until.get(name, 0)

    def _source_success(self, name: str):
        self._source_fails.pop(name, None)
        self._source_down_until.pop(name, None)

    def _source_fail(self, name: str, reason: str = "", empty: bool = False):
        fails = self._source_fails.get(name, 0) + 1
        self._source_fails[name] = fails
        # 异常（被拒连/超时）2 次即熔断；空数据可能是停牌/无效代码，需 3 次
        threshold = 3 if empty else 2
        if fails >= threshold and self._source_ok(name):
            self._source_down_until[name] = time.time() + self._source_cooldown
            logger.warning(
                f"数据源 {name} 连续 {fails} 次失败，熔断 {int(self._source_cooldown)} 秒"
                + (f"（{reason}）" if reason else "")
            )

    # ===== 基础行情 =====
    def get_daily_data(self, symbol: str, days: int = 60) -> pd.DataFrame:
        """获取日线数据，按 daily_priority 依次尝试各数据源（带失败熔断）"""
        cache_key = f"daily_{symbol}_{days}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        handlers = {
            'tushare': self._get_daily_tushare,
            'tencent': self._get_daily_tencent,
            'eastmoney': self._get_daily_eastmoney,
            'akshare': self._get_daily_akshare,
        }
        enabled = {
            'tushare': self.use_tushare,
            'tencent': self.use_tencent,
            'eastmoney': self.use_eastmoney,
            'akshare': self.use_akshare,
        }

        tried = []
        for name in self.daily_priority:
            fn = handlers.get(name)
            if fn is None or not enabled.get(name):
                continue
            if not self._source_ok(name):
                tried.append(f"{name}(熔断中)")
                continue
            try:
                df = fn(symbol, days)
            except Exception as e:
                logger.warning(f"{symbol} {name} 获取日线异常: {e}")
                df = pd.DataFrame()

            if df is not None and not df.empty:
                self._source_success(name)
                self._cache_set(cache_key, df)
                logger.info(f"{symbol} 日线获取成功: {name}（{len(df)} 根）")
                return df

            self._source_fail(name, "返回空数据", empty=True)
            tried.append(name)

        logger.error(
            f"获取 {symbol} 日线最终失败：所有数据源均不可用"
            f"（已尝试: {', '.join(tried) if tried else '无'}）"
        )
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
            # 成交量：手 -> 股（与腾讯/新浪口径一致）
            if 'volume' in df.columns:
                df['volume'] = pd.to_numeric(df['volume'], errors='coerce') * 100
            
            # 补齐字段（tushare daily 无换手率，置 0 避免触发误报；振幅可由高低价计算）
            if 'turnover' not in df.columns:
                df['turnover'] = 0.0
            if 'amplitude' not in df.columns:
                df['amplitude'] = (df['high'] - df['low']) / df['low'] * 100
            
            return df.tail(days)
            
        except Exception as e:
            logger.warning(f"{symbol} tushare Pro 获取日线失败: {e}")
            return pd.DataFrame()
    
    def _get_daily_tencent(self, symbol: str, days: int) -> pd.DataFrame:
        """腾讯直连获取日线（前复权，云端 IP 友好：单请求即返回）"""
        return tencent_daily_to_dataframe(symbol, days=days)

    def _get_daily_eastmoney(self, symbol: str, days: int) -> pd.DataFrame:
        """东财直连获取日线（push2his，云端易被断开，连续失败后由熔断跳过）"""
        return daily_to_dataframe(symbol, days=days)

    def _get_daily_akshare(self, symbol: str, days: int) -> pd.DataFrame:
        """akshare 兜底获取日线：新浪日线优先（与东财不同源），东财可用时也尝试"""
        import akshare as ak
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d")

        attempts = []
        # 东财接口与 push2his 同源，熔断期间不再重复尝试
        if self._source_ok('eastmoney'):
            attempts.append((
                '东财', False, False,
                lambda: ak.stock_zh_a_hist(
                    symbol=symbol, period="daily", start_date=start, end_date=end,
                    adjust="qfq", timeout=15),
            ))
        attempts.append((
            '新浪', True, True,
            lambda: ak.stock_zh_a_daily(
                symbol=_market_symbol(symbol), start_date=start, end_date=end, adjust="qfq"),
        ))

        for src_name, volume_in_lots, turnover_is_fraction, fn in attempts:
            for attempt in range(2):
                try:
                    df = fn()
                    if df is None or df.empty:
                        raise ValueError("返回空数据")
                    df = self._normalize_daily(
                        df, volume_in_lots=volume_in_lots,
                        turnover_is_fraction=turnover_is_fraction)
                    if df.empty:
                        raise ValueError("标准化后无有效数据")
                    return df.tail(days)
                except Exception as e:
                    logger.warning(f"{symbol} akshare({src_name}) 第{attempt+1}次失败: {e}")
                    time.sleep(1)
        return pd.DataFrame()

    @staticmethod
    def _normalize_daily(df: pd.DataFrame, volume_in_lots: bool = False,
                         turnover_is_fraction: bool = False) -> pd.DataFrame:
        """统一 akshare 各接口列名与口径：
        索引 date；列 open/close/high/low/volume(股)/amount(元)/turnover(%)/pct_chg(%)
        """
        rename = {
            '日期': 'date', '开盘': 'open', '收盘': 'close', '最高': 'high',
            '最低': 'low', '成交量': 'volume', '成交额': 'amount',
            '振幅': 'amplitude', '涨跌幅': 'pct_chg', '涨跌额': 'change',
            '换手率': 'turnover',
        }
        df = df.rename(columns=rename).copy()
        if 'date' not in df.columns:
            df = df.reset_index().rename(columns={'index': 'date'})
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.dropna(subset=['date']).set_index('date').sort_index()
        df = df[~df.index.duplicated(keep='last')]

        numeric_cols = ('open', 'close', 'high', 'low', 'volume', 'amount',
                        'turnover', 'pct_chg', 'amplitude')
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        if 'volume' in df.columns and volume_in_lots:
            df['volume'] = df['volume'] * 100          # 手 -> 股
        if 'turnover' in df.columns:
            if turnover_is_fraction:
                df['turnover'] = df['turnover'] * 100  # 小数 -> 百分比
            df['turnover'] = df['turnover'].fillna(0.0)
        else:
            df['turnover'] = 0.0
        if 'pct_chg' not in df.columns or df['pct_chg'].isna().all():
            df['pct_chg'] = df['close'].pct_change() * 100
        return df
    
    def get_realtime_quote(self, symbols: List[str]) -> pd.DataFrame:
        """实时行情快照：腾讯直连 -> 东财直连 -> akshare"""
        cache_key = f"realtime_{'_'.join(sorted(symbols))}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        # 1. 腾讯直连（单请求可批量 60 只，云端稳定）
        if self.use_tencent:
            try:
                df = tencent_realtime_to_dataframe(symbols)
                if not df.empty:
                    self._cache_set(cache_key, df)
                    logger.info(f"实时行情获取成功: 腾讯直连（{len(df)} 只）")
                    return df
            except Exception as e:
                logger.warning(f"腾讯实时行情失败: {e}")

        # 2. 东财直连（快照接口在部分网络亦会被断连，失败计入熔断）
        if self.use_eastmoney and self._source_ok('eastmoney'):
            try:
                df = realtime_to_dataframe(symbols)
                if not df.empty:
                    self._source_success('eastmoney')
                    self._cache_set(cache_key, df)
                    logger.info(f"实时行情获取成功: 东财直连（{len(df)} 只）")
                    return df
                self._source_fail('eastmoney', '实时行情为空', empty=True)
            except Exception as e:
                logger.warning(f"东财实时行情失败: {e}")
                self._source_fail('eastmoney', f'实时行情异常: {e}')

        # 3. 兜底 akshare
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
        """两融余额（L6 风控用）：tushare -> akshare 沪市逐日汇总 -> 深市"""
        if self.use_tushare:
            try:
                df = self.ts_pro.margin(start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"))
                if df is not None and not df.empty:
                    return df
            except Exception:
                pass
        if self.use_akshare:
            import akshare as ak
            start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
            end = datetime.now().strftime("%Y%m%d")
            for fn_name, kwargs in (
                ('stock_margin_sse', {'start_date': start, 'end_date': end}),
                ('stock_margin_szse', {'date': end}),
            ):
                fn = getattr(ak, fn_name, None)
                if fn is None:
                    continue
                try:
                    df = fn(**kwargs)
                    if df is None or df.empty:
                        continue
                    # 统一按日期升序，方便取"最近一日"和"环比"
                    sort_col = next((c for c in ('信用交易日期', '日期') if c in df.columns), None)
                    if sort_col:
                        df = df.copy()
                        df[sort_col] = pd.to_datetime(df[sort_col], errors='coerce')
                        df = df.sort_values(sort_col)
                    return df
                except Exception as e:
                    logger.warning(f"两融数据 {fn_name} 获取失败: {e}")
        return pd.DataFrame()
    
    def get_north_money(self, days: int = 30) -> pd.DataFrame:
        """北向资金：tushare -> akshare"""
        if self.use_tushare:
            try:
                df = self.ts_pro.moneyflow_hsgt(start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"))
                if df is not None and not df.empty:
                    return df
            except Exception:
                pass
        if self.use_akshare:
            try:
                import akshare as ak
                df = ak.stock_hsgt_hist_em(symbol="北向资金")
                if df is not None and not df.empty:
                    return df.tail(days)
            except Exception as e:
                logger.warning(f"北向资金获取失败: {e}")
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
        
        # akshare（新浪关键指标，宽表格式：列为报告期，行为指标）；指标最全，优先于东财
        if self.use_akshare:
            try:
                import akshare as ak
                df = ak.stock_financial_abstract(symbol=symbol)
                if df is not None and not df.empty and '指标' in df.columns:
                    period_cols = [c for c in df.columns if c not in ('选项', '指标')]
                    if period_cols:
                        latest_period = period_cols[0]   # 报告期倒序排列，第一列即最新一期
                        items = (df[['指标', latest_period]]
                                 .drop_duplicates(subset=['指标'], keep='first')
                                 .set_index('指标')[latest_period])
                        return {
                            'revenue_yoy': _num(items.get('营业总收入增长率')) or 0,
                            'profit_yoy': _num(items.get('归属母公司净利润增长率')) or 0,
                            'roe': _num(items.get('净资产收益率(ROE)')) or 0,
                            'gross_margin': _num(items.get('毛利率')) or 0,
                            'operate_cash_flow': _num(items.get('经营现金流量净额')) or 0,
                            'debt_ratio': _num(items.get('资产负债率')) or 0,
                            'period': latest_period,
                        }
            except Exception as e:
                logger.warning(f"获取 {symbol} 财务摘要失败: {e}")

        # 东财详细接口兜底（仅含 ROE，熔断期间跳过）
        if self.use_eastmoney and self._source_ok('eastmoney'):
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
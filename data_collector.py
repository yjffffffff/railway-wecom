"""
数据采集层 - 统一接口适配多源
"""
import akshare as ak
import pandas as pd
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import time
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

class DataCollector:
    def __init__(self, config: dict):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    # ===== 基础行情 =====
    def get_daily_data(self, symbol: str, days: int = 60) -> pd.DataFrame:
        """获取日线数据，自动复权"""
        try:
            # akshare 返回前复权数据
            df = ak.stock_zh_a_hist(
                symbol=symbol, 
                period="daily", 
                start_date=(datetime.now() - timedelta(days=days*2)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
                adjust="qfq"  # 前复权
            )
            if df.empty:
                return pd.DataFrame()
            
            # 标准化列名
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
            logger.error(f"获取 {symbol} 日线失败: {e}")
            return pd.DataFrame()
    
    def get_realtime_quote(self, symbols: List[str]) -> pd.DataFrame:
        """实时行情快照"""
        try:
            df = ak.stock_zh_a_spot_em()
            df = df[df['代码'].isin(symbols)]
            return df
        except Exception as e:
            logger.error(f"实时行情获取失败: {e}")
            return pd.DataFrame()
    
    # ===== 板块指数 =====
    def get_sector_index(self, sector_name: str, days: int = 30) -> pd.DataFrame:
        """获取板块指数（使用概念板块）"""
        try:
            # 获取概念板块列表
            sectors = ak.stock_board_concept_name_em()
            target = sectors[sectors['板块名称'].str.contains(sector_name, na=False)]
            if target.empty:
                return pd.DataFrame()
            
            code = target.iloc[0]['板块代码']
            df = ak.stock_board_concept_hist_em(
                symbol=code, 
                start_date=(datetime.now() - timedelta(days=days*2)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
                period="日k"
            )
            if df.empty:
                return pd.DataFrame()
            df['date'] = pd.to_datetime(df['日期'])
            return df.set_index('date').sort_index().tail(days)
        except Exception as e:
            logger.error(f"获取板块 {sector_name} 指数失败: {e}")
            return pd.DataFrame()
    
    def get_sector_stocks(self, sector_name: str) -> List[str]:
        """获取板块成分股"""
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
        """获取涨停股"""
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        try:
            df = ak.stock_zt_pool_em(date=trade_date)
            return df
        except Exception as e:
            logger.error(f"获取涨停池失败: {e}")
            return pd.DataFrame()
    
    def get_lhb_data(self, trade_date: str = None) -> pd.DataFrame:
        """龙虎榜"""
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        try:
            df = ak.stock_lhb_detail_em(date=trade_date)
            return df
        except Exception as e:
            logger.error(f"获取龙虎榜失败: {e}")
            return pd.DataFrame()
    
    # ===== 资金流向 =====
    def get_market_margin(self, days: int = 30) -> pd.DataFrame:
        """融资融券余额"""
        try:
            df = ak.stock_margin_detail_em(start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"))
            return df
        except Exception as e:
            logger.error(f"融资融券获取失败: {e}")
            return pd.DataFrame()
    
    def get_north_money(self, days: int = 30) -> pd.DataFrame:
        """北向资金"""
        try:
            df = ak.stock_hsgt_hist_em(symbol="沪股通", start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"))
            return df
        except Exception as e:
            logger.error(f"北向资金获取失败: {e}")
            return pd.DataFrame()
    
    # ===== 基本面 =====
    @lru_cache(maxsize=100)
    def get_financial_abstract(self, symbol: str) -> Dict:
        """核心财务指标（缓存）"""
        try:
            # 主要指标
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
        """政策新闻抓取（简化版，实际建议接入专业终端）"""
        # 这里用akshare的财经新闻作为演示
        try:
            df = ak.stock_info_global_em()  # 备用
            return []
        except:
            return []


# 单例模式
_collector_instance = None

def get_collector(config: dict) -> DataCollector:
    global _collector_instance
    if _collector_instance is None:
        _collector_instance = DataCollector(config)
    return _collector_instance
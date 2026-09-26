"""
选股模型 - 形态识别 + 基本面评分 + 相对强度
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

@dataclass
class SetupResult:
    symbol: str
    name: str
    sector: str
    phase: str  # SHRINK, PLATFORM, BREAKOUT, RETEST, CONFIRMED
    setup_score: float
    volume_shrink_ok: bool
    platform_ok: bool
    breakout_ok: bool
    retest_ok: bool
    fundamental_score: float
    rs_score: float
    details: Dict
    timestamp: datetime

class TechnicalAnalyzer:
    def __init__(self, params: dict):
        self.params = params
    
    def analyze(self, df: pd.DataFrame, symbol: str, name: str, sector: str, fundamental: Dict) -> Optional[SetupResult]:
        """主分析入口"""
        if df.empty or len(df) < 30:
            return None
        
        # 计算技术指标
        df = self._calc_indicators(df)
        
        # 阶段识别
        phase, details = self._identify_phase(df)
        
        # 基本面评分
        fund_score = self._score_fundamental(fundamental)
        
        # 相对强度（简化：相对沪深300）
        rs_score = self._calc_rs_score(df)
        
        # 综合评分
        setup_score = self._calc_setup_score(phase, details, fund_score, rs_score)
        
        return SetupResult(
            symbol=symbol,
            name=name,
            sector=sector,
            phase=phase,
            setup_score=setup_score,
            volume_shrink_ok=details.get('volume_shrink_ok', False),
            platform_ok=details.get('platform_ok', False),
            breakout_ok=details.get('breakout_ok', False),
            retest_ok=details.get('retest_ok', False),
            fundamental_score=fund_score,
            rs_score=rs_score,
            details=details,
            timestamp=datetime.now()
        )
    
    def _calc_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        # 均线
        for ma in [5, 10, 20, 60]:
            df[f'MA{ma}'] = df['close'].rolling(ma).mean()
        # 成交量均线
        df['VOL_MA5'] = df['volume'].rolling(5).mean()
        df['VOL_MA20'] = df['volume'].rolling(20).mean()
        # 换手率
        if 'turnover' not in df.columns:
            df['turnover'] = df['volume'] / df['volume'].rolling(20).mean() * 100  # 近似
        # 相对强度（这里用价格动量代理）
        df['RS_20'] = df['close'].pct_change(20)
        return df
    
    def _identify_phase(self, df: pd.DataFrame) -> Tuple[str, Dict]:
        """识别所处阶段"""
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        
        details = {}
        
        # 1. 缩量下跌检测（最近10-15日）
        shrink_lookback = self.params.get('volume_shrink_days', 10)
        recent = df.tail(shrink_lookback)
        if len(recent) >= 5:
            vol_trend = recent['volume'].rolling(3).mean().diff().dropna()
            price_trend = recent['close'].diff().dropna()
            # 价格下跌且成交量递减
            vol_shrinking = (vol_trend < 0).sum() / len(vol_trend) > 0.6
            price_falling = (price_trend < 0).sum() / len(price_trend) > 0.5
            details['volume_shrink_ok'] = bool(vol_shrinking and price_falling)
            details['shrink_days'] = int((price_trend < 0).sum())
        
        # 2. 底部平台检测
        platform_days = self.params.get('platform_days_min', 5)
        platform_width = self.params.get('platform_width_max', 0.04)
        recent_platform = df.tail(platform_days + 5).head(platform_days)
        if len(recent_platform) >= platform_days:
            high_max = recent_platform['high'].max()
            low_min = recent_platform['low'].min()
            width = (high_max - low_min) / low_min
            ma_aligned = abs(recent_platform['MA5'].iloc[-1] - recent_platform['MA10'].iloc[-1]) / recent_platform['MA10'].iloc[-1] < 0.02
            ma_aligned = ma_aligned and abs(recent_platform['MA10'].iloc[-1] - recent_platform['MA20'].iloc[-1]) / recent_platform['MA20'].iloc[-1] < 0.03
            details['platform_ok'] = bool(width < platform_width and ma_aligned)
            details['platform_width'] = width
            details['platform_days'] = platform_days
        
        # 3. 放量突破检测
        breakout_vol_ratio = self.params.get('breakout_volume_ratio', 2.0)
        breakout_body = self.params.get('breakout_body_ratio', 0.03)
        platform_vol = df['VOL_MA20'].iloc[-(platform_days+5)] if len(df) > platform_days+5 else df['VOL_MA20'].iloc[-6]
        vol_ratio = last['volume'] / platform_vol if platform_vol > 0 else 0
        body_ratio = abs(last['close'] - last['open']) / last['open']
        price_break = last['close'] > df['high'].rolling(10).max().iloc[-2]  # 突破前高
        details['breakout_ok'] = bool(vol_ratio >= breakout_vol_ratio and body_ratio >= breakout_body and price_break)
        details['vol_ratio'] = vol_ratio
        details['body_ratio'] = body_ratio
        
        # 4. 回踩确认
        retest_vol_ratio = self.params.get('retest_volume_ratio', 0.5)
        retest_ma10 = self.params.get('retest_ma10', True)
        if len(df) >= 3:
            retest_day = df.iloc[-2]  # 假设昨天是突破，今天回踩
            retest_vol_ok = retest_day['volume'] < last['volume'] * retest_vol_ratio
            retest_ma_ok = not retest_ma10 or retest_day['close'] > retest_day['MA10']
            retest_support = retest_day['close'] > df['high'].rolling(10).max().iloc[-3]  # 不跌破平台上沿
            details['retest_ok'] = bool(retest_vol_ok and retest_ma_ok and retest_support)
            details['retest_vol_ratio'] = retest_day['volume'] / last['volume']
        
        # 判定阶段
        if details.get('retest_ok'):
            phase = 'CONFIRMED'
        elif details.get('breakout_ok'):
            phase = 'BREAKOUT'
        elif details.get('platform_ok'):
            phase = 'PLATFORM'
        elif details.get('volume_shrink_ok'):
            phase = 'SHRINK'
        else:
            phase = 'NONE'
        
        return phase, details
    
    def _score_fundamental(self, fund: Dict) -> float:
        """基本面评分 0-100"""
        if not fund:
            return 0
        score = 0
        weights = {
            'revenue_growth': 25,
            'profit_growth': 25,
            'cash_flow': 20,
            'gross_margin_stable': 15,
            'roe': 15
        }
        # 营收增速
        rev = fund.get('revenue_yoy', 0)
        if rev > 20: score += 25
        elif rev > 10: score += 20
        elif rev > 0: score += 10
        elif rev > -10: score += 5
        
        # 利润增速
        prof = fund.get('profit_yoy', 0)
        if prof > 20: score += 25
        elif prof > 10: score += 20
        elif prof > 0: score += 10
        elif prof > -10: score += 5
        
        # 经营现金流
        cf = fund.get('operate_cash_flow', 0)
        if cf > 0: score += 20
        elif cf > -5000: score += 10
        
        # 毛利率稳定
        gm = fund.get('gross_margin', 0)
        if gm > 20: score += 15
        elif gm > 10: score += 10
        elif gm > 0: score += 5
        
        # ROE
        roe = fund.get('roe', 0)
        if roe > 15: score += 15
        elif roe > 10: score += 10
        elif roe > 5: score += 5
        
        return min(score, 100)
    
    def _calc_rs_score(self, df: pd.DataFrame) -> float:
        """相对强度评分（简化版）"""
        if 'RS_20' not in df.columns:
            return 50
        rs = df['RS_20'].iloc[-1]
        if rs > 0.1: return 90
        elif rs > 0.05: return 75
        elif rs > 0: return 60
        elif rs > -0.05: return 40
        elif rs > -0.1: return 25
        return 10
    
    def _calc_setup_score(self, phase: str, details: Dict, fund_score: float, rs_score: float) -> float:
        """综合选股评分"""
        phase_scores = {
            'CONFIRMED': 40,
            'BREAKOUT': 30,
            'PLATFORM': 15,
            'SHRINK': 5,
            'NONE': 0
        }
        base = phase_scores.get(phase, 0)
        # 基本面权重 35%，相对强度 25%
        total = base + fund_score * 0.35 + rs_score * 0.25
        return min(total, 100)


class StockSelector:
    def __init__(self, config: dict, collector):
        self.config = config
        self.collector = collector
        self.analyzer = TechnicalAnalyzer(config.get('stock_selection', {}))
        self.sector_whitelist = config.get('sector_whitelist', [])
        self.fund_threshold = config.get('stock_selection', {}).get('fundamental_threshold', 60)
        self.min_setup_score = config.get('push_rules', {}).get('L4_stock_pick', {}).get('min_setup_score', 70)
        self._name_cache = {}
    
    def scan_all(self) -> List[SetupResult]:
        """全量扫描白名单板块"""
        analyzed = []
        for sector_cfg in self.sector_whitelist:
            sector_name = sector_cfg['name']
            symbols = self._get_sector_symbols(sector_cfg)
            logger.info(f"扫描板块 {sector_name}: {len(symbols)} 只")
            self._prefetch_names(symbols[:30])
            
            for symbol in symbols[:30]:  # 限制每板块最多30只，避免超时
                try:
                    df = self.collector.get_daily_data(symbol, days=60)
                    if df.empty:
                        continue
                    fundamental = self.collector.get_financial_abstract(symbol)
                    name = self._get_stock_name(symbol)
                    result = self.analyzer.analyze(df, symbol, name, sector_name, fundamental)
                    if result:
                        analyzed.append(result)
                except Exception as e:
                    logger.error(f"分析 {symbol} 失败: {e}")
                    continue
        
        # 按评分排序；输出 TOP 便于确认取数与评分链路是否正常
        analyzed.sort(key=lambda x: x.setup_score, reverse=True)
        if analyzed:
            top = "; ".join(
                f"{r.name}({r.symbol}) {r.phase} {r.setup_score:.1f}" for r in analyzed[:3])
            logger.info(f"评分 TOP3（推送阈值 {self.min_setup_score}）: {top}")
        results = [r for r in analyzed if r.setup_score >= self.min_setup_score]
        logger.info(f"扫描完成: 分析 {len(analyzed)} 只，达标 {len(results)} 只")
        return results
    
    def _get_sector_symbols(self, sector_cfg: dict) -> List[str]:
        """获取板块成分股，优先用配置的龙头"""
        leaders = sector_cfg.get('leaders', [])
        # 可选：补充板块全量成分股
        # all_stocks = self.collector.get_sector_stocks(sector_cfg['name'])
        # return list(dict.fromkeys(leaders + all_stocks))[:50]
        return leaders

    def _prefetch_names(self, symbols: List[str]):
        """批量预取股票名称到本地缓存（一次请求，替代逐只拉全市场快照）"""
        pending = [s for s in symbols if s not in self._name_cache]
        if not pending:
            return
        try:
            df = self.collector.get_realtime_quote(pending)
            if df is None or df.empty or '名称' not in df.columns:
                return
            for _, row in df.iterrows():
                code = str(row.get('代码') or '').strip()
                name = row.get('名称')
                if code and name:
                    self._name_cache[code] = str(name)
        except Exception as e:
            logger.debug(f"预取名称失败: {e}")
    
    def _get_stock_name(self, symbol: str) -> str:
        """获取股票名称：东财/腾讯实时快照 + 本地缓存
        （旧实现每只股票都拉一次全市场快照，东财被限流时会拖垮整个扫描）
        """
        if symbol in self._name_cache:
            return self._name_cache[symbol]
        name = symbol
        try:
            df = self.collector.get_realtime_quote([symbol])
            if df is not None and not df.empty and '名称' in df.columns:
                val = df.iloc[0].get('名称')
                if val:
                    name = str(val)
        except Exception as e:
            logger.debug(f"获取 {symbol} 名称失败: {e}")
        self._name_cache[symbol] = name
        return name
"""
企微推送引擎
"""
import requests
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
import logging
from collections import defaultdict
import hashlib

logger = logging.getLogger(__name__)

@dataclass
class PushMessage:
    msg_type: str  # L1/L2/L3/L4/L5/L6
    title: str
    content: str
    symbols: List[str]
    sectors: List[str]
    priority: int  # 1=highest
    timestamp: datetime
    metadata: Dict

class WeComPusher:
    def __init__(self, config: dict):
        self.config = config
        self.wecom_config = config.get('wecom', {})
        self.corp_id = self.wecom_config.get('corp_id', '')
        self.agent_id = self.wecom_config.get('agent_id', '')
        self.secret = self.wecom_config.get('secret', '')
        self.webhook_url = self.wecom_config.get('webhook_url', '')
        self.template = self.wecom_config.get('push_template', 'markdown')
        
        self.access_token = None
        self.token_expires = 0
        
        # 频控状态
        self.symbol_cooldown = {}  # symbol -> last_push_time
        self.sector_daily_count = defaultdict(int)  # sector -> count
        self.sector_daily_reset = datetime.now().date()
        self.macro_daily_count = 0
        self.macro_daily_reset = datetime.now().date()
        
        self.freq_config = config.get('frequency_control', {})
        self.symbol_cooldown_days = self.freq_config.get('same_symbol_cooldown_days', 10)
        self.sector_daily_limit = self.freq_config.get('same_sector_daily_limit', 2)
        self.macro_daily_limit = self.freq_config.get('macro_daily_limit', 1)
    
    def _get_access_token(self) -> Optional[str]:
        """获取access_token"""
        if self.access_token and time.time() < self.token_expires - 300:
            return self.access_token
        
        if not all([self.corp_id, self.secret]):
            logger.warning("企微配置不完整，尝试使用webhook")
            return None
        
        url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken"
        params = {'corpid': self.corp_id, 'corpsecret': self.secret}
        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            if data.get('errcode') == 0:
                self.access_token = data['access_token']
                self.token_expires = time.time() + data['expires_in']
                return self.access_token
            else:
                logger.error(f"获取access_token失败: {data}")
        except Exception as e:
            logger.error(f"获取access_token异常: {e}")
        return None
    
    def _check_frequency(self, msg: PushMessage) -> bool:
        """频控检查"""
        now = datetime.now()
        today = now.date()
        
        # 重置每日计数
        if today != self.sector_daily_reset:
            self.sector_daily_count.clear()
            self.sector_daily_reset = today
        if today != self.macro_daily_reset:
            self.macro_daily_count = 0
            self.macro_daily_reset = today
        
        # 同一股票冷却
        for sym in msg.symbols:
            last = self.symbol_cooldown.get(sym)
            if last and (now - last).days < self.symbol_cooldown_days:
                logger.info(f"股票 {sym} 在冷却期内，跳过")
                return False
        
        # 板块日限额
        for sec in msg.sectors:
            if self.sector_daily_count[sec] >= self.sector_daily_limit:
                logger.info(f"板块 {sec} 今日推送已达上限")
                return False
        
        # 宏观日限额
        if msg.msg_type in ['L1', 'L3'] and self.macro_daily_count >= self.macro_daily_limit:
            logger.info("宏观类推送今日已达上限")
            return False
        
        # 交易时间控制
        if self.freq_config.get('trading_hours_only', True):
            hour = now.hour
            minute = now.minute
            # 9:15-11:30, 13:00-15:00
            in_trading = (9 <= hour <= 11 and not (hour == 11 and minute > 30)) or (13 <= hour < 15)
            quiet_hours = self.freq_config.get('quiet_hours', [])
            in_quiet = any(
                self._in_time_range(now, qh) for qh in quiet_hours
            )
            if not in_trading or in_quiet:
                logger.info("非交易时间/静默期，延迟推送")
                # 这里可以选择队列延迟或直接返回False
                return False
        
        return True
    
    def _in_time_range(self, dt: datetime, time_range: str) -> bool:
        """检查是否在时间范围内"""
        try:
            start_str, end_str = time_range.split('-')
            start_h, start_m = map(int, start_str.split(':'))
            end_h, end_m = map(int, end_str.split(':'))
            start = dt.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
            end = dt.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
            if start <= end:
                return start <= dt <= end
            else:  # 跨天
                return dt >= start or dt <= end
        except:
            return False
    
    def _record_push(self, msg: PushMessage):
        """记录推送历史"""
        now = datetime.now()
        for sym in msg.symbols:
            self.symbol_cooldown[sym] = now
        for sec in msg.sectors:
            self.sector_daily_count[sec] += 1
        if msg.msg_type in ['L1', 'L3']:
            self.macro_daily_count += 1
    
    def _format_markdown(self, msg: PushMessage) -> str:
        """格式化Markdown消息"""
        type_labels = {
            'L1': '🔴 【宏观阶段切换】',
            'L2': '🟠 【政策落地实锤】',
            'L3': '🔵 【经贸利好窗口】',
            'L4': '🟢 【个股达标买点】',
            'L5': '🟡 【龙头异动风向】',
            'L6': '🚨 【风控熔断预警】'
        }
        label = type_labels.get(msg.msg_type, msg.msg_type)
        
        md = f"{label} **{msg.title}**\n\n"
        md += f"> {msg.content}\n\n"
        
        if msg.symbols:
            md += f"**标的**: {', '.join(msg.symbols)}\n"
        if msg.sectors:
            md += f"**板块**: {', '.join(msg.sectors)}\n"
        
        md += f"\n**时间**: {msg.timestamp.strftime('%H:%M:%S')}\n"
        md += f"**优先级**: {'⭐' * msg.priority}\n"
        
        if msg.metadata:
            md += "\n**详情**:\n"
            for k, v in msg.metadata.items():
                md += f"- {k}: {v}\n"
        
        return md
    
    def push(self, msg: PushMessage, force: bool = False) -> bool:
        """发送推送"""
        if not force and not self._check_frequency(msg):
            return False
        
        content = self._format_markdown(msg)
        
        # 优先用应用消息（支持@all、格式更丰富）
        if self._push_via_app(content, msg.priority == 1):
            self._record_push(msg)
            return True
        
        # 备用：群机器人webhook
        if self.webhook_url and self._push_via_webhook(content):
            self._record_push(msg)
            return True
        
        logger.error("所有推送渠道均失败")
        return False
    
    def _push_via_app(self, content: str, is_urgent: bool) -> bool:
        """通过企微应用推送"""
        token = self._get_access_token()
        if not token:
            return False
        
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        payload = {
            "touser": "@all",
            "msgtype": "markdown",
            "agentid": int(self.agent_id),
            "markdown": {"content": content},
            "safe": 0,
            "enable_id_trans": 0,
            "enable_duplicate_check": 0
        }
        if is_urgent:
            payload["enable_duplicate_check"] = 1
            payload["duplicate_check_interval"] = 1800
        
        try:
            resp = requests.post(url, json=payload, timeout=10)
            data = resp.json()
            if data.get('errcode') == 0:
                logger.info(f"企微应用推送成功: {data.get('msgid')}")
                return True
            else:
                logger.error(f"企微应用推送失败: {data}")
        except Exception as e:
            logger.error(f"企微应用推送异常: {e}")
        return False
    
    def _push_via_webhook(self, content: str) -> bool:
        """通过群机器人webhook推送"""
        payload = {"msgtype": "markdown", "markdown": {"content": content}}
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            data = resp.json()
            if data.get('errcode') == 0:
                logger.info("Webhook推送成功")
                return True
            else:
                logger.error(f"Webhook推送失败: {data}")
        except Exception as e:
            logger.error(f"Webhook推送异常: {e}")
        return False


class TriggerEngine:
    """触发规则引擎"""
    def __init__(self, config: dict, collector, selector, pusher: WeComPusher, force: bool = False):
        self.config = config
        self.collector = collector
        self.selector = selector
        self.pusher = pusher
        self.force = force
        self.rules = config.get('push_rules', {})
        self.last_macro_signal = None
    
    def run_all_checks(self):
        """运行所有触发检查"""
        # L4 个股达标（核心）
        if self.rules.get('L4_stock_pick', {}).get('enabled', True):
            self.check_stock_setups()
        
        # L2 政策落地
        if self.rules.get('L2_policy_landing', {}).get('enabled', True):
            self.check_policy_landing()
        
        # L5 龙头异动
        if self.rules.get('L5_leader_anomaly', {}).get('enabled', True):
            self.check_leader_anomaly()
        
        # L6 风控熔断
        if self.rules.get('L6_risk_circuit', {}).get('enabled', True):
            self.check_risk_circuit()
        
        # L1/L3 需要外部新闻源，这里留接口
        # self.check_macro_shift()
        # self.check_trade_window()
    
    def check_stock_setups(self):
        """L4 个股达标检查"""
        results = self.selector.scan_all()
        daily_limit = self.rules['L4_stock_pick'].get('daily_limit', 3)
        
        for i, r in enumerate(results[:daily_limit]):
            msg = PushMessage(
                msg_type='L4',
                title=f"{r.name}({r.symbol}) 形态确认",
                content=f"板块 {r.sector} | 阶段: {r.phase} | 综合评分: {r.setup_score:.1f}",
                symbols=[r.symbol],
                sectors=[r.sector],
                priority=2,
                timestamp=datetime.now(),
                metadata={
                    'phase': r.phase,
                    'setup_score': f"{r.setup_score:.1f}",
                    'fundamental_score': f"{r.fundamental_score:.1f}",
                    'rs_score': f"{r.rs_score:.1f}",
                    'volume_shrink': r.volume_shrink_ok,
                    'platform': r.platform_ok,
                    'breakout': r.breakout_ok,
                    'retest': r.retest_ok,
                    'details': str(r.details)[:200]
                }
            )
            self.pusher.push(msg, force=self.force)
            time.sleep(0.5)  # 避免频控
    
    def check_policy_landing(self):
        """L2 政策落地检查 - 简化版，实际需接入政策监控"""
        # 这里演示：检查白名单板块是否有政策关键词命中
        # 实际应对接政策日历/券商研报API
        pass
    
    def check_leader_anomaly(self):
        """L5 龙头异动检查"""
        for sector_cfg in self.config.get('sector_whitelist', []):
            leaders = sector_cfg.get('leaders', [])
            for symbol in leaders[:3]:  # 只监控前3只龙头
                df = self.collector.get_daily_data(symbol, days=10)
                if df.empty:
                    continue
                last = df.iloc[-1]
                vol_ratio = last['volume'] / df['volume'].rolling(20).mean().iloc[-1]
                turnover = last.get('turnover', 0)
                
                vol_spike = self.rules['L5_leader_anomaly'].get('volume_spike_ratio', 3.0)
                turn_thresh = self.rules['L5_leader_anomaly'].get('turnover_threshold', 0.30)
                
                anomaly = []
                if vol_ratio >= vol_spike:
                    anomaly.append(f"放量{vol_ratio:.1f}x")
                if turnover >= turn_thresh * 100:
                    anomaly.append(f"高换手{turnover:.1f}%")
                
                # 跌破MA10
                ma10_break = last['close'] < last['MA10'] if 'MA10' in last else False
                if ma10_break:
                    anomaly.append("跌破MA10")
                
                if anomaly:
                    name = self.selector._get_stock_name(symbol)
                    msg = PushMessage(
                        msg_type='L5',
                        title=f"{name}({symbol}) 异动",
                        content=f"板块 {sector_cfg['name']} 龙头出现: {', '.join(anomaly)}",
                        symbols=[symbol],
                        sectors=[sector_cfg['name']],
                        priority=2,
                        timestamp=datetime.now(),
                        metadata={
                            'anomaly_types': anomaly,
                            'vol_ratio': f"{vol_ratio:.1f}",
                            'turnover': f"{turnover:.1f}%",
                            'close': f"{last['close']:.2f}",
                            'ma10': f"{last.get('MA10', 0):.2f}"
                        }
                    )
                    self.pusher.push(msg, force=self.force)
    
    def check_risk_circuit(self):
        """L6 风控熔断检查"""
        # 简化：检查两融余额、大盘趋势
        try:
            margin_df = self.collector.get_market_margin(days=5)
            if not margin_df.empty:
                last_margin = margin_df.iloc[-1]
                prev_margin = margin_df.iloc[-2] if len(margin_df) > 1 else last_margin
                margin_drop = prev_margin.get('融资余额', 0) - last_margin.get('融资余额', 0)
                threshold = self.rules['L6_risk_circuit'].get('margin_drop_threshold', 50e8)
                if margin_drop > threshold:
                    msg = PushMessage(
                        msg_type='L6',
                        title="两融大幅缩量 预警",
                        content=f"融资余额单日净减 {margin_drop/1e8:.1f}亿，超阈值",
                        symbols=[],
                        sectors=['全市场'],
                        priority=1,
                        timestamp=datetime.now(),
                        metadata={'margin_drop_yi': f"{margin_drop/1e8:.1f}"}
                    )
                    self.pusher.push(msg, force=self.force)
        except Exception as e:
            logger.error(f"风控检查失败: {e}")
    
    def check_macro_shift(self, news_items: List[Dict]):
        """L1 宏观阶段切换 - 需外部传入新闻"""
        pass
    
    def check_trade_window(self, news_items: List[Dict]):
        """L3 经贸窗口 - 需外部传入新闻"""
        pass


# 单例
_trigger_engine = None

def get_trigger_engine(config, collector, selector, pusher):
    global _trigger_engine
    if _trigger_engine is None:
        _trigger_engine = TriggerEngine(config, collector, selector, pusher)
    return _trigger_engine
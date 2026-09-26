"""
配置加载器：读取 YAML + 环境变量
"""
import os
import yaml
from pathlib import Path
from typing import Dict, Any


def _load_dotenv(path: Path) -> None:
    """把 .env 键值写入 os.environ（仅补齐缺失项，不覆盖已有环境变量）

    本地测试时按 README 的 `cp .env.example .env` 流程即可直接生效；
    Railway / GitHub Actions 注入的环境变量优先级更高。
    """
    if not path.exists():
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as e:
        print(f"警告: 读取 {path.name} 失败: {e}")


def load_config() -> Dict[str, Any]:
    """加载配置，环境变量优先于 YAML"""
    config_path = Path(__file__).parent / 'config' / 'triggers.yaml'

    # 0. 本地 .env 兜底（已存在的环境变量不覆盖）
    _load_dotenv(Path(__file__).parent / '.env')
    
    # 1. 读取 YAML 基础配置
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}
        print(f"警告: 配置文件 {config_path} 不存在，使用默认配置")
    
    # 2. 环境变量覆盖（敏感信息不写入代码仓库）
    env_mapping = {
        # 数据源
        'TUSHARE_TOKEN': ('tushare', 'token'),
        'AKSHARE_ENABLED': ('data_source', 'use_akshare'),
        'EASTMONEY_ENABLED': ('data_source', 'use_eastmoney'),
        'TENCENT_ENABLED': ('data_source', 'use_tencent'),
        # 企微
        'WECOM_CORP_ID': ('wecom', 'corp_id'),
        'WECOM_AGENT_ID': ('wecom', 'agent_id'),
        'WECOM_SECRET': ('wecom', 'secret'),
        'WECOM_WEBHOOK_URL': ('wecom', 'webhook_url'),
        # Railway/运行时
        'LOG_LEVEL': ('runtime', 'log_level'),
        'TIMEZONE': ('runtime', 'timezone'),
    }
    
    for env_key, (section, key) in env_mapping.items():
        val = os.getenv(env_key)
        if val is not None:
            # 类型转换
            if val.lower() in ('true', 'false'):
                val = val.lower() == 'true'
            elif val.isdigit():
                val = int(val)
            else:
                try:
                    val = float(val)
                except ValueError:
                    pass
            
            if section not in config:
                config[section] = {}
            config[section][key] = val
    
    # 3. 设置默认值
    defaults = {
        'data_source': {
            'use_akshare': True,
            'use_tushare': False,
            'use_eastmoney': True,
            'use_tencent': True,
            # 日线数据源优先级：云端 IP 常被东财断连，故腾讯优先于东财
            'daily_priority': ['tushare', 'tencent', 'eastmoney', 'akshare'],
        },
        'tushare': {'token': ''},
        'wecom': {
            'corp_id': '', 'agent_id': '', 'secret': '', 'webhook_url': '',
            'push_template': 'markdown'
        },
        'runtime': {'log_level': 'INFO', 'timezone': 'Asia/Shanghai'},
        'frequency_control': {
            'same_symbol_cooldown_days': 10,
            'same_sector_daily_limit': 2,
            'macro_daily_limit': 1,
            'trading_hours_only': True,
            'quiet_hours': ['11:30-13:00', '15:00-09:15']
        },
        'push_rules': {
            'L4_stock_pick': {'enabled': True, 'daily_limit': 3},
            'L2_policy_landing': {'enabled': True},
            'L5_leader_anomaly': {'enabled': True, 'volume_spike_ratio': 3.0, 'turnover_threshold': 0.30},
            'L6_risk_circuit': {'enabled': True, 'margin_drop_threshold': 50e8},
        },
        'selector': {
            'min_score': 60,
            'volume_shrink_threshold': 0.5,
            'platform_days': (10, 60),
            'breakout_vol_ratio': 1.5,
            'retest_days': 5,
            'rs_period': 20
        }
    }
    
    # 深度合并默认值
    def deep_merge(base: dict, override: dict) -> dict:
        result = base.copy()
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = deep_merge(result[k], v)
            else:
                result[k] = v
        return result
    
    config = deep_merge(defaults, config)
    return config
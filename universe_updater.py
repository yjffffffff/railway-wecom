#!/usr/bin/env python3
"""
universe_updater.py
===================
全市场自适应股票池与板块动态轮动模块
"""

import os
import re
import sys
import json
import shutil
import logging
import requests
import yaml
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# 精选覆盖主流资金主线的 10 个行业节点
INDUSTRY_MAPPING = [
    {
        'key': '半导体与器件',
        'node': 'new_dzqj',
        'keywords': ['半导体', '芯片', '集成电路', '分立器件', '光刻胶'],
        'weight': 1.0
    },
    {
        'key': '通信与算力',
        'node': 'new_dzxx',
        'keywords': ['算力', '光模块', '通信设备', 'PCB', '光纤光缆'],
        'weight': 0.95
    },
    {
        'key': '高端制造与工控',
        'node': 'new_jxhy',
        'keywords': ['工业母机', '数控机床', '机器人', '自动化', '装备制造'],
        'weight': 0.90
    },
    {
        'key': '新能源与发电设备',
        'node': 'new_fdsb',
        'keywords': ['特高压', '智能电网', '风电', '光伏', '储能装备'],
        'weight': 0.85
    },
    {
        'key': '商业航天与低空',
        'node': 'new_fjzz',
        'keywords': ['商业航天', '低空经济', 'eVTOL', '无人机', '通航装备'],
        'weight': 0.90
    },
    {
        'key': '智能汽车与零部件',
        'node': 'new_qczz',
        'keywords': ['智能汽车', '新能源汽车', '自动驾驶', '汽车零部件', '一体化压铸'],
        'weight': 0.85
    },
    {
        'key': '新型储能与锂电',
        'node': 'new_dqhy',
        'keywords': ['固态电池', '新型储能', '锂电池', '逆变器', '电气自控'],
        'weight': 0.80
    },
    {
        'key': '央企与核心金融',
        'node': 'new_jrhy',
        'keywords': ['央企', '国企', '高股息', '市值管理', '红利资产'],
        'weight': 0.80
    },
    {
        'key': '战略资源与有色',
        'node': 'new_ysjs',
        'keywords': ['工业金属', '贵金属', '稀有金属', '铜', '稀土'],
        'weight': 0.75
    },
    {
        'key': '生物医药与创新药',
        'node': 'new_swzz',
        'keywords': ['创新药', 'CXO', '生物制药', '合成生物', '新药出海'],
        'weight': 0.75
    }
]

class UniverseUpdater:
    def __init__(self, config_path: str = None):
        self.root_dir = Path(__file__).resolve().parent
        if config_path:
            self.config_path = Path(config_path)
        else:
            self.config_path = self.root_dir / 'config' / 'triggers.yaml'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://finance.sina.com.cn'
        }

    def fetch_dynamic_universe(self, leaders_per_sector: int = 5) -> List[Dict[str, Any]]:
        """从全市场行情动态拉取最新成交额最大、最具流动性与代表性的龙头板块与标的"""
        logger.info("开始拉取全市场各主线板块最新龙头数据...")
        stock_url = 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'
        
        new_whitelist = []
        for ind in INDUSTRY_MAPPING:
            name = ind['key']
            node = ind['node']
            keywords = ind['keywords']
            weight = ind['weight']
            
            try:
                params = {
                    'page': '1',
                    'num': '15',
                    'sort': 'amount',
                    'asc': '0',
                    'node': node,
                    'symbol': '',
                    '_s_r_a': 'page'
                }
                resp = requests.get(stock_url, params=params, headers=self.headers, timeout=8)
                if resp.status_code != 200:
                    logger.warning(f"获取板块 {name} ({node}) 失败: 状态码 {resp.status_code}")
                    continue
                
                raw_items = resp.json()
                valid_leaders = []
                for item in raw_items:
                    code = str(item.get('code', '')).strip()
                    s_name = str(item.get('name', '')).strip()
                    if not code or len(code) != 6:
                        continue
                    if 'ST' in s_name or '退' in s_name or '临' in s_name:
                        continue
                    if code.startswith('920') or code.startswith('8') or code.startswith('4'):
                        continue
                    
                    valid_leaders.append(code)
                    if len(valid_leaders) >= leaders_per_sector:
                        break
                
                if valid_leaders:
                    new_whitelist.append({
                        'name': name,
                        'keywords': keywords,
                        'leaders': valid_leaders,
                        'weight': weight
                    })
                    logger.info(f"板块 [{name}] 动态入池 {len(valid_leaders)} 只标的: {valid_leaders}")
            except Exception as e:
                logger.error(f"拉取板块 {name} 异常: {e}")
                continue
        
        return new_whitelist

    def fetch_market_focus_stocks(self, top_n: int = 8) -> List[Dict[str, Any]]:
        """全市场成交额 TOP 榜筛选动态核心关注池（100% 算法生成，无任何硬编码标的）"""
        logger.info("开始拉取全市场成交额排行榜，生成动态核心关注池...")
        url = 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'
        focus = []
        try:
            params = {
                'page': '1', 'num': '60', 'sort': 'amount', 'asc': '0',
                'node': 'hs_a', 'symbol': '', '_s_r_a': 'page'
            }
            resp = requests.get(url, params=params, headers=self.headers, timeout=8)
            items = resp.json() if resp.status_code == 200 else []
            for item in items:
                code = str(item.get('code', '')).strip()
                name = str(item.get('name', '')).strip()
                if not code or len(code) != 6:
                    continue
                if 'ST' in name or '退' in name or '临' in name:
                    continue
                if code.startswith(('920', '8', '4')):
                    continue
                focus.append({
                    'symbol': code,
                    'name': name,
                    'amount_yi': round(float(item.get('amount', 0) or 0) / 1e8, 1),
                    'pct_chg': round(float(item.get('changepercent', 0) or 0), 2),
                })
                if len(focus) >= top_n:
                    break
        except Exception as e:
            logger.error(f"拉取全市场成交额榜异常: {e}")
        logger.info(f"动态核心关注池生成 {len(focus)} 只: {[f['symbol'] for f in focus]}")
        return focus

    def update_triggers_file(self, new_whitelist: List[Dict[str, Any]],
                             focus_stocks: List[Dict[str, Any]] = None) -> bool:
        """安全写回 triggers.yaml（板块轮动池 + 动态核心关注池，全部算法生成）"""
        if not new_whitelist:
            logger.error("新白名单为空，取消写回！")
            return False

        if not self.config_path.exists():
            logger.error(f"配置文件不存在: {self.config_path}")
            return False

        bak_path = self.config_path.with_suffix('.yaml.bak')
        shutil.copy2(self.config_path, bak_path)
        logger.info(f"原配置文件已备份至: {bak_path}")

        try:
            content = self.config_path.read_text(encoding='utf-8')
            lines = content.splitlines(keepends=True)
            start_idx, end_idx = -1, -1
            for i, line in enumerate(lines):
                if line.strip().startswith('sector_whitelist:'):
                    start_idx = i
                elif start_idx != -1 and line.strip().startswith('historical_samples:'):
                    end_idx = i
                    break

            stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
            block = [
                "# ==========================================\n",
                "# 全市场自适应轮动池（由 universe_updater.py 自动生成，请勿手工维护）\n",
                f"# 最近一次自动轮换: {stamp}\n",
                "# ==========================================\n",
                "sector_whitelist:\n",
            ]
            for s in new_whitelist:
                block.append(f"  # 动态轮动板块: {s['name']}\n")
                block.append(f"  - name: \"{s['name']}\"\n")
                block.append(f"    keywords: {json.dumps(s['keywords'], ensure_ascii=False)}\n")
                block.append("    leaders:\n")
                for l in s['leaders']:
                    block.append(f"      - \"{l}\"\n")
                block.append(f"    weight: {s['weight']}\n\n")

            if focus_stocks:
                block.append("# ==========================================\n")
                block.append("# 全市场流动性核心关注池（自动生成，同样参与动态轮换）\n")
                block.append("# ==========================================\n")
                block.append("focus_stocks:\n")
                for f in focus_stocks:
                    block.append(f"  - symbol: \"{f['symbol']}\"\n")
                    block.append(f"    name: \"{f['name']}\"\n")
                    block.append(f"    turnover_yi: {f['amount_yi']}\n")
                    block.append(f"    pct_chg: {f['pct_chg']}\n")
                block.append("\n")

            if start_idx != -1 and end_idx != -1:
                new_content = "".join(lines[:start_idx] + block + lines[end_idx:])
            else:
                cfg = yaml.safe_load(content)
                cfg['sector_whitelist'] = new_whitelist
                if focus_stocks:
                    cfg['focus_stocks'] = focus_stocks
                new_content = yaml.dump(cfg, allow_unicode=True, sort_keys=False)

            self.config_path.write_text(new_content, encoding='utf-8')
            logger.info("triggers.yaml 轮动池更新成功！")
            return True
        except Exception as e:
            logger.error(f"写回配置文件失败: {e}，正在还原备份...")
            shutil.copy2(bak_path, self.config_path)
            return False

    def _state_path(self) -> Path:
        return self.root_dir / 'config' / 'universe_state.json'

    def load_previous(self) -> Dict[str, List[str]]:
        """读取上一期轮动快照（用于生成纳入/剔除差异）"""
        p = self._state_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding='utf-8')).get('sectors', {}) or {}
        except Exception as e:
            logger.warning(f"读取历史轮动快照失败: {e}")
            return {}

    def save_snapshot(self, new_whitelist: List[Dict[str, Any]],
                      focus_stocks: List[Dict[str, Any]] = None) -> None:
        """保存本期轮动快照，供下一次轮换生成差异报告"""
        try:
            payload = {
                'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'sectors': {s['name']: s['leaders'] for s in new_whitelist},
                'focus': [f['symbol'] for f in (focus_stocks or [])],
            }
            self._state_path().write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            logger.info(f"轮动快照已保存: {self._state_path()}")
        except Exception as e:
            logger.warning(f"保存轮动快照失败: {e}")

    def build_report_markdown(self, new_whitelist: List[Dict[str, Any]],
                              focus_stocks: List[Dict[str, Any]] = None,
                              prev_sectors: Dict[str, List[str]] = None) -> str:
        """构建企微轮换报告 Markdown（含新增/剔除差异与全市场流动性核心池）"""
        import tencent_client as tx
        all_syms = []
        for s in new_whitelist:
            all_syms.extend(s['leaders'])
        for f in (focus_stocks or []):
            all_syms.append(f['symbol'])

        name_map = {f['symbol']: f['name'] for f in (focus_stocks or [])}
        try:
            quotes = tx.get_realtime_symbols(list(set(all_syms)))
            for q in quotes:
                name_map.setdefault(q.get('代码'), q.get('名称'))
        except Exception as e:
            logger.warning(f"股票名称补全失败: {e}")

        prev_sectors = prev_sectors or {}
        prev_all = {sym for syms in prev_sectors.values() for sym in syms}
        new_all = {sym for syms in (s['leaders'] for s in new_whitelist) for sym in syms}
        added = sorted(new_all - prev_all)
        removed = sorted(prev_all - new_all)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        md = "### 🔄 【量化监控池】自动轮换报告\n\n"
        md += f"> 更新时间: `{now_str}` | 机制: 全市场流动性优胜劣汰（全动态，无固定标的）\n\n"
        md += f"**本期自适应纳入 {len(new_whitelist)} 个主线板块，合计 {len(all_syms)} 只流动性核心标的**\n\n"

        for idx, s in enumerate(new_whitelist, 1):
            leader_strs = [
                f"{name_map.get(l, l)}(`{l}`)" for l in s['leaders']
            ]
            md += f"{idx}. **{s['name']}** (权重: {s['weight']})\n"
            md += f"   - 核心龙头: {', '.join(leader_strs)}\n"

        if focus_stocks:
            md += "\n**💧 全市场流动性核心池（成交额榜）**\n"
            for f in focus_stocks:
                md += f"- {f['name']}(`{f['symbol']}`) 成交 {f['amount_yi']}亿 | 涨跌 {f['pct_chg']:+.2f}%\n"

        if prev_sectors:
            md += "\n---\n"
            md += f"**本期调整**: 新增 {len(added)} 只 / 剔除 {len(removed)} 只\n"
            if added:
                md += f"- 🟢 新进池: {', '.join(name_map.get(a, a) + '(' + a + ')' for a in added)}\n"
            if removed:
                md += f"- 🔴 移出池: {', '.join(name_map.get(r, r) + '(' + r + ')' for r in removed)}\n"

        md += "\n---\n"
        md += "📌 **模型执行提示**：新周期轮动池已写回 config/triggers.yaml，下周起自动跟踪以上标的的「五阶段平台蓄势与放量买点」。"
        return md


def run_update(notify_wecom: bool = True):
    from config_loader import load_config
    from push_engine import WeComPusher

    cfg = load_config()
    updater = UniverseUpdater()
    prev_sectors = updater.load_previous()

    new_whitelist = updater.fetch_dynamic_universe()
    if not new_whitelist:
        print("未获取到有效新轮动池，退出更新。")
        return False

    focus_stocks = updater.fetch_market_focus_stocks()

    ok = updater.update_triggers_file(new_whitelist, focus_stocks)
    if not ok:
        print("轮动池写入 triggers.yaml 失败。")
        return False

    updater.save_snapshot(new_whitelist, focus_stocks)

    report_md = updater.build_report_markdown(new_whitelist, focus_stocks, prev_sectors)
    print("\n--- 轮换报告预览 ---\n")
    print(report_md)

    if notify_wecom:
        pusher = WeComPusher(cfg)
        push_ok = pusher._push_via_webhook(report_md)
        print(f"\n企微通知结果: {push_ok}")

    return True


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
    # Windows 控制台默认 GBK，emoji 报告需强制 UTF-8 输出
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    notify = '--no-notify' not in sys.argv
    run_update(notify_wecom=notify)


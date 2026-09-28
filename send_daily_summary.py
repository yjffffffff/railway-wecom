"""
发送最近交易日完整复盘日报（日期自动定位，可直接用于 GitHub Actions 定时任务）
用法:
  python send_daily_summary.py            # 计算并推送
  python send_daily_summary.py --dry-run  # 仅本地预览，不推送
"""
import argparse
import sys
from datetime import datetime

import tencent_client as tx
from config_loader import load_config
from data_collector import DataCollector
from stock_selector import StockSelector
from push_engine import WeComPusher
from main import resolve_trade_date


def pick_row(df, target_date):
    """取目标交易日所在行；缺失则退回最后一行"""
    if df.empty:
        return None
    idx = [d.date() if hasattr(d, 'date') else d for d in df.index]
    if target_date in idx:
        return df.iloc[idx.index(target_date)]
    return df.iloc[-1]


def run(dry_run: bool = False):
    target_date = resolve_trade_date()
    print(f"正在获取最近交易日 ({target_date}) 完整数据...")
    cfg = load_config()
    col = DataCollector(cfg)
    sel = StockSelector(cfg, col)
    pusher = WeComPusher(cfg)

    # 1. 大盘指数
    idx_sh = pick_row(tx.daily_to_dataframe('sh000001', 10), target_date)
    idx_sz = pick_row(tx.daily_to_dataframe('sz399001', 10), target_date)
    idx_cy = pick_row(tx.daily_to_dataframe('sz399006', 10), target_date)
    tot_amt = (idx_sh['amount'] + idx_sz['amount']) / 1e12

    # 2. 两融（数据源可能只返回单日，缺失环比时按 0 处理）
    m_df = col.get_market_margin(days=10)
    last_m = m_df.iloc[-1] if len(m_df) > 0 else None
    drop_yi = 0.0
    if len(m_df) >= 2:
        drop_yi = (m_df.iloc[-2]['融资余额'] - last_m['融资余额']) / 1e8


    # 2. 板块真实强弱计算（根据各板块龙头股票当天的平均涨跌幅自动计算排序）
    sector_perf = {}
    for sector_cfg in sel.sector_whitelist:
        sector_name = sector_cfg['name']
        symbols = sel._get_sector_symbols(sector_cfg)
        chgs = []
        for s in symbols:
            df = col.get_daily_data(s, days=2)
            if not df.empty and 'pct_chg' in df.columns:
                chgs.append(df.iloc[-1]['pct_chg'])
        if chgs:
            sector_perf[sector_name] = sum(chgs) / len(chgs)
    sorted_sectors = sorted(sector_perf.items(), key=lambda x: x[1], reverse=True)

    # 3. 股票分析与评分
    analyzed = []
    for sector_cfg in sel.sector_whitelist:
        sector_name = sector_cfg['name']
        symbols = sel._get_sector_symbols(sector_cfg)
        for symbol in symbols[:30]:
            df = col.get_daily_data(symbol, days=60)
            if df.empty:
                continue
            name = sel._get_stock_name(symbol)
            r = sel.analyzer.analyze(df, symbol, name, sector_name, {})
            if r and sel._should_fetch_fundamental(r):
                f = col.get_financial_abstract(symbol)
                if f:
                    r = sel.analyzer.analyze(df, symbol, name, sector_name, f)
            if r:
                analyzed.append(r)
    analyzed.sort(key=lambda x: x.setup_score, reverse=True)

    sh_c = idx_sh['close']
    sh_p = idx_sh['pct_chg']
    sh_amt = idx_sh['amount'] / 1e8
    sz_c = idx_sz['close']
    sz_p = idx_sz['pct_chg']
    sz_amt = idx_sz['amount'] / 1e8
    cy_c = idx_cy['close']
    cy_p = idx_cy['pct_chg']

    md = f"## 📊 {target_date} 交易日完整复盘日报\n\n"
    md += "### 📈 一、大盘与流动性\n"
    md += f"- **上证指数**: {sh_c:.2f} ({sh_p:+.2f}%) | 成交 {sh_amt:.0f}亿\n"
    md += f"- **深证成指**: {sz_c:.2f} ({sz_p:+.2f}%) | 成交 {sz_amt:.0f}亿\n"
    md += f"- **创业板指**: {cy_c:.2f} ({cy_p:+.2f}%) | 两市成交: **{tot_amt:.2f}万亿**\n"
    if last_m is not None:
        rz_tot = last_m['融资余额'] / 1e12
        if drop_yi >= 50:
            margin_note = f"单日净减 **{drop_yi:.1f}亿** 🚨触发L6风控"
        elif drop_yi <= -50:
            margin_note = f"单日净增 **{abs(drop_yi):.1f}亿** 🟢杠杆资金回流"
        else:
            margin_note = f"单日变动 {drop_yi:+.1f}亿（平稳）"
        md += f"- **全市场两融**: {rz_tot:.3f}万亿 ({margin_note})\n\n"
    else:
        md += "- **全市场两融**: 数据源暂不可用\n\n"

    md += "### 🏆 二、重点监控板块（动态强弱榜）\n"
    for name, chg in sorted_sectors:
        tag = "🟢" if chg > 0 else "⚪" if chg >= -1.0 else "🔴"
        md += f"- {tag} **{name}**: `{chg:+.2f}%`\n"
    md += "\n"

    md += "### 🎯 三、选股模型评分 TOP3（阈值 70）\n"
    for i, r in enumerate(analyzed[:3], 1):
        md += f"{i}. **{r.name} ({r.symbol})** · {r.sector}\n"
        md += f"   - 形态阶段: `{r.phase}` | **综合评分: {r.setup_score:.1f}**\n"
        md += f"   - 基本面得分: {r.fundamental_score:.1f} | 相对强度: {r.rs_score:.1f}\n"

    hits = [r for r in analyzed if r.setup_score >= 70]
    md += "\n### 🚨 四、触发与风控提示\n"
    if last_m is not None:
        if drop_yi >= 50:
            md += f"- **L6 风控预警**: 融资余额单日大幅缩量 {drop_yi:.1f} 亿（>50亿警戒线），警惕杠杆资金离场风险。\n"
        elif drop_yi <= -50:
            md += f"- **L6 资金信号**: 融资余额单日净增 {abs(drop_yi):.1f} 亿，杠杆资金回流，风险偏好回升。\n"
        else:
            md += f"- **L6 资金信号**: 融资余额环比 {drop_yi:+.1f} 亿，处平稳区间，无风控触发。\n"
    if hits:
        md += f"- **L4 个股买点**: 达标（≥70分）{len(hits)} 只 — " + "、".join(
            f"{r.name}({r.symbol}) {r.setup_score:.1f}" for r in hits
        ) + "\n\n"
    else:
        md += "- **L4 个股买点**: 暂无个股突破 70 分门槛，等待形态与量能共振。\n\n"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    md += f"> 发送时间: {now_str} | 数据源: 腾讯 Direct API + AkShare"

    print("--- 预览推送内容 ---")
    print(md)

    if dry_run:
        print("\n[dry-run] 未实际推送")
        return True

    ok = pusher._push_via_webhook(md)
    print(f"\n企微 Webhook 推送结果: {ok}")
    return ok


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='最近交易日复盘日报推送')
    parser.add_argument('--dry-run', action='store_true', help='仅打印预览，不推送企微')
    args = parser.parse_args()
    # Windows 控制台默认 GBK，emoji 报告需强制 UTF-8 输出
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    sys.exit(0 if run(dry_run=args.dry_run) else 1)


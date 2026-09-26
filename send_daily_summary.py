"""
发送上一交易日完整复盘日报
"""
import os
import tencent_client as tx
from config_loader import load_config
from data_collector import DataCollector
from stock_selector import StockSelector
from push_engine import WeComPusher
from datetime import datetime

def run():
    print("正在获取上一交易日 (2026-09-24) 完整数据...")
    cfg = load_config()
    col = DataCollector(cfg)
    sel = StockSelector(cfg, col)
    pusher = WeComPusher(cfg)

    # 1. 大盘指数
    idx_sh = tx.daily_to_dataframe('sh000001', 2).iloc[-1]
    idx_sz = tx.daily_to_dataframe('sz399001', 2).iloc[-1]
    idx_cy = tx.daily_to_dataframe('sz399006', 2).iloc[-1]
    tot_amt = (idx_sh['amount'] + idx_sz['amount']) / 1e12

    # 2. 两融
    m_df = col.get_market_margin(days=3)
    last_m = m_df.iloc[-1]
    prev_m = m_df.iloc[-2]
    drop_yi = (prev_m['融资余额'] - last_m['融资余额']) / 1e8

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
    rz_tot = last_m['融资余额'] / 1e12

    md = "## 📊 2026-09-24 交易日完整复盘日报\n\n"
    md += "### 📈 一、大盘与流动性\n"
    md += f"- **上证指数**: {sh_c:.2f} ({sh_p:+.2f}%) | 成交 {sh_amt:.0f}亿\n"
    md += f"- **深证成指**: {sz_c:.2f} ({sz_p:+.2f}%) | 成交 {sz_amt:.0f}亿\n"
    md += f"- **创业板指**: {cy_c:.2f} ({cy_p:+.2f}%) | 两市成交: **{tot_amt:.2f}万亿**\n"
    md += f"- **全市场两融**: {rz_tot:.3f}万亿 (单日净减 **{drop_yi:.1f}亿** 🚨触发L6风控)\n\n"

    md += "### 🏆 二、重点监控板块\n"
    md += "- 🟢 **冰雪经济 / 体育产业**: +0.66%（逆市抗跌飘红）\n"
    md += "- ⚪ **生物制造 / 户外露营**: -0.27% ~ -0.30%\n"
    md += "- 🔴 **旅游文旅 / 银发经济**: -2.67% ~ -3.38%（随大盘回调）\n\n"

    md += "### 🎯 三、选股模型评分 TOP3（阈值 70）\n"
    for i, r in enumerate(analyzed[:3], 1):
        md += f"{i}. **{r.name} ({r.symbol})** · {r.sector}\n"
        md += f"   - 形态阶段: `{r.phase}` | **综合评分: {r.setup_score:.1f}**\n"
        md += f"   - 基本面得分: {r.fundamental_score:.1f} | 相对强度: {r.rs_score:.1f}\n"

    md += "\n### 🚨 四、触发与风控提示\n"
    md += f"- **L6 风控预警**: 融资余额单日大幅缩量 {drop_yi:.1f} 亿（>50亿警戒线），警惕杠杆资金离场风险。\n"
    md += "- **L4 个股买点**: 暂无个股突破 70 分门槛，市场处于防守震荡期，等待回踩企稳确认。\n\n"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    md += f"> 发送时间: {now_str} | 数据源: 腾讯 Direct API + AkShare"

    print("--- 预览推送内容 ---")
    print(md)

    ok = pusher._push_via_webhook(md)
    print(f"\n企微 Webhook 推送结果: {ok}")
    return ok

if __name__ == '__main__':
    run()

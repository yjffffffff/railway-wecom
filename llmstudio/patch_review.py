# -*- coding: utf-8 -*-
"""程序化修改 generate_review.py：紫光单股专项分析泛化为多自选股五段式"""
import io
import ast

path = "generate_review.py"
with io.open(path, "r", encoding="utf-8") as f:
    src = f.read()

# 1. 替换紫光数据组装块为多自选股数据组装
old_zg_block = '''    zg = md.get("ziguang", {})
    if zg and zg.get("close"):
        recent5 = ", ".join(
            "{}收盘{}({:+.2f}%)".format(r["date"][5:], r["close"], r["pct"] or 0)
            for r in zg.get("recent5", []))
        zg_text = (f"交易日{zg.get('date')} | 收盘{zg.get('close')} ({zg.get('pct') or 0:+.2f}%) | "
                   f"成交量{zg.get('volume_hand')}手（为前5日均量的{zg.get('vol_vs_5d_avg')}倍） | "
                   f"成交额{zg.get('amount_yi')}亿 | 换手率{zg.get('turnover')}%\\n"
                   f"均线位置：{zg.get('vs_ma5')}、{zg.get('vs_ma10')}、"
                   f"{zg.get('vs_ma20')}、{zg.get('vs_ma60')}\\n"
                   f"近5日：{recent5}")
    else:
        zg_text = "（紫光股份数据抓取失败，请基于已有信息分析并注明）"
    zg_news = "\\n".join(l for l in lines if ("紫光" in l or "新华三" in l)) \\
              or "（新闻池中今日无紫光股份/新华三相关新闻）"

    # 紫光估值/财务快照 + 交易信号卡（全维度专项分析素材）
    z = md.get("ziguang") or {}
    if z.get("pe_ttm"):
        zg_val = (f"总市值 {z.get('mktcap_yi')}亿 | 流通市值 {z.get('floatcap_yi')}亿 | "
                  f"PE(动) {z.get('pe_dyn')} | PE(TTM) {z.get('pe_ttm')} | "
                  f"PE(静) {z.get('pe_static')} | PB {z.get('pb')} | ROE {z.get('roe')}% | "
                  f"营收同比 {z.get('rev_yoy')}% | 净利同比 {z.get('np_yoy')}%")
    else:
        zg_val = "（估值数据缺失）"
    card_text = "（交易信号卡生成失败）"
    try:
        subprocess.run([sys.executable, "ziguang_trading_card.py"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        with open("ziguang_trading_card.txt", "r", encoding="utf-8") as f:
            card_text = f.read()
    except Exception:
        pass'''

new_zg_block = '''    # 自选股全维度专项分析素材（技术指标+估值财务+交易信号卡+相关新闻）
    watch = md.get("watchlist") or []
    watch_blocks = []
    try:
        subprocess.run([sys.executable, "ziguang_trading_card.py"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    except Exception:
        pass
    for w in watch:
        name = w.get("name", "")
        if not w.get("close"):
            watch_blocks.append(f"【{name}】数据抓取失败，请基于已有信息分析并注明")
            continue
        recent5 = ", ".join(
            "{}收盘{}({:+.2f}%)".format(r["date"][5:], r["close"], r["pct"] or 0)
            for r in w.get("recent5", [])) or "（历史数据累积中）"
        tech = (f"交易日{w.get('date')} | 收盘{w.get('close')} ({w.get('pct') or 0:+.2f}%) | "
                f"成交量{w.get('volume_hand')}手 | 量比{w.get('vol_ratio')} | "
                f"成交额{w.get('amount_yi')}亿 | 换手率{w.get('turnover')}%\\n"
                f"均线位置：{w.get('vs_ma5')}、{w.get('vs_ma10')}、"
                f"{w.get('vs_ma20')}、{w.get('vs_ma60')} | 近5日：{recent5}")
        val = (f"总市值 {w.get('mktcap_yi')}亿 | 流通市值 {w.get('floatcap_yi')}亿 | "
               f"PE(动) {w.get('pe_dyn')} | PE(TTM) {w.get('pe_ttm')} | "
               f"PE(静) {w.get('pe_static')} | PB {w.get('pb')} | ROE {w.get('roe')}% | "
               f"营收同比 {w.get('rev_yoy')}% | 净利同比 {w.get('np_yoy')}%") \\
            if w.get("pe_ttm") else "（估值数据缺失）"
        card_path = f"{name}_trading_card.txt"
        card_text = "（交易信号卡生成失败）"
        try:
            with open(card_path, "r", encoding="utf-8") as f:
                card_text = f.read()
        except Exception:
            pass
        w_news = "\\n".join(l for l in lines if (name in l or (name == "紫光股份" and "新华三" in l))) \\
                 or f"（新闻池中今日无{name}相关新闻）"
        watch_blocks.append(
            f"【{name}({w.get('code')}) 技术指标】\\n{tech}\\n\\n"
            f"【{name} 估值/财务快照】\\n{val}\\n\\n"
            f"【{name} 交易信号卡（趋势/关键价位/止损/仓位/触发条件）】\\n{card_text[:3500]}\\n\\n"
            f"【{name} 相关新闻】\\n{w_news}")
    watch_text = "\\n\\n".join(watch_blocks) if watch_blocks else "（自选股数据抓取失败）"'''

assert old_zg_block in src, "zg block not found"
src = src.replace(old_zg_block, new_zg_block)

# 2. 替换系统提示词中的紫光专项分析段为多自选股
old_sys = '''              "## 🎯 紫光股份(000938)全维度专项分析（常驻板块，用户自选股）\\n"
              "基于给出的紫光股份技术指标、估值财务快照、交易信号卡与新闻池，输出五段：\\n"
              "①技术面：今日涨跌幅、量能（量比及含义）、换手率水平、"
              "股价与MA5/MA10/MA20/MA60的位置关系（站稳还是跌破、是否破位或金叉）、短期趋势判断；\\n"
              "②估值与基本面：引用给出的PE(动)/PE(TTM)/PB/ROE/市值数据，"
              "结合营收同比与净利同比判断当前估值是贵还是合理（PE-TTM与PE-动的差异说明市场对增长的预期），"
              "结合公司基本面（ICT设备、新华三、算力基础设施龙头）分析；\\n"
              "③消息面与资金面：引用新闻池相关新闻（无则注明），结合主力资金/换手率判断资金行为；\\n"
              "④次日交易计划：引用信号卡中的关键价位（压力/支撑/MA分界）、止损位、仓位建议与触发条件，"
              "给出明确的触发式操作建议（什么条件买/什么条件卖/买多少），强调按计划执行不盘中临时起意；\\n"
              "⑤结论：明确给出下一交易日操作建议——**【推荐买入/观望/不建议追高】**三选一，"
              "并列出2-3条核心理由。该结论仅供参考不构成投资建议。\\n\\n"'''

new_sys = '''              "## 🎯 自选股全维度专项分析（常驻板块，用户自选股）\\n"
              "对给出的每只自选股（紫光股份/比亚迪/长江电力）分别输出五段式分析：\\n"
              "①技术面：今日涨跌幅、量能（量比及含义）、换手率水平、"
              "股价与MA5/MA10/MA20/MA60的位置关系（站稳还是跌破、是否破位或金叉）、短期趋势判断；\\n"
              "②估值与基本面：引用给出的PE(动)/PE(TTM)/PB/ROE/市值数据，"
              "结合营收同比与净利同比判断当前估值是贵还是合理（PE-TTM与PE-动的差异说明市场对增长的预期），"
              "结合公司基本面（紫光=ICT/新华三/算力基础设施龙头，比亚迪=新能源/汽车/电池龙头，"
              "长江电力=水电/低估值高股息龙头）分析；\\n"
              "③消息面与资金面：引用新闻池相关新闻（无则注明），结合主力资金/换手率判断资金行为；\\n"
              "④次日交易计划：引用信号卡中的关键价位（压力/支撑/MA分界）、止损位、仓位建议与触发条件，"
              "给出明确的触发式操作建议（什么条件买/什么条件卖/买多少），强调按计划执行不盘中临时起意；\\n"
              "⑤结论：明确给出下一交易日操作建议——**【推荐买入/观望/不建议追高】**三选一，"
              "并列出2-3条核心理由。该结论仅供参考不构成投资建议。\\n\\n"'''

assert old_sys in src, "sys block not found"
src = src.replace(old_sys, new_sys)

# 3. 替换用户提示词中的紫光数据块为多自选股数据
old_user = '''            f"【紫光股份(000938)今日技术指标】\\n{zg_text}\\n\\n"
            f"【紫光股份估值/财务快照】\\n{zg_val}\\n\\n"
            f"【紫光交易信号卡（趋势/关键价位/止损/仓位/触发条件）】\\n{card_text[:3500]}\\n\\n"
            f"【紫光股份相关新闻】\\n{zg_news}\\n\\n"'''

new_user = '''            f"【自选股全维度专项分析素材】\\n{watch_text}\\n\\n"'''

assert old_user in src, "user block not found"
src = src.replace(old_user, new_user)

with io.open(path, "w", encoding="utf-8", newline="\n") as f:
    f.write(src)

ast.parse(src)
print("OK: generate_review.py patched + syntax OK")
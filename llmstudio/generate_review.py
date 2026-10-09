# -*- coding: utf-8 -*-
"""
盘后异动复盘流水线（交易日15:30运行）：
1. fetch_movers.py 抓取异动股 -> movers.json
2. fetch_news.py 抓当日新闻（NEWS_HOURS=9，覆盖今日开盘至今）
3. LLM逐股多角度归因 + 提炼选股学习点
4. 后处理保存 briefings/YYYY-MM-DD_review.md + 推送
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.parse
import ssl
from datetime import datetime, timezone, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LM_API = os.environ.get("LLM_API_URL", "https://api.deepseek.com/chat/completions")
LM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
# Key来源：环境变量优先（云端Actions），其次push_config.json的llm_api_key（本地调试）
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
if not LLM_API_KEY:
    try:
        with open("push_config.json", "r", encoding="utf-8") as f:
            LLM_API_KEY = json.load(f).get("llm_api_key", "")
    except Exception:
        pass
MAX_TOKENS = 8192
CST = timezone(timedelta(hours=8))
BRIEFING_TYPE = "review"

# 推送配置（与generate_briefing.py一致）
_sc_env = os.environ.get("SERVERCHAN_KEY", "")
if _sc_env:
    SERVERCHAN_KEYS = [k.strip() for k in _sc_env.split(",") if k.strip()]
else:
    try:
        with open("push_config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        SERVERCHAN_KEYS = cfg.get("serverchan_keys", [])
    except Exception:
        SERVERCHAN_KEYS = []


def _push_enabled():
    """推送开关：PUSH_ENABLED环境变量优先，其次push_config.json（复盘默认不推送）"""
    env = os.environ.get("PUSH_ENABLED", "")
    if env:
        return env.lower() in ("1", "true", "yes")
    try:
        with open("push_config.json", "r", encoding="utf-8") as f:
            return bool(json.load(f).get("push_enabled", False))
    except Exception:
        return False


PUSH_ON = _push_enabled()

CONTEXT = ssl.create_default_context()
CONTEXT.check_hostname = False
CONTEXT.verify_mode = ssl.CERT_NONE


def _load_lessons(max_chars=3500):
    """加载历史积累的选股知识点"""
    try:
        with open("knowledge/lessons.json", "r", encoding="utf-8") as f:
            lessons = json.load(f)
    except Exception:
        return ""
    if not lessons:
        return ""
    lines, total = [], 0
    for it in lessons[-20:]:
        line = f"- [{it.get('date', '')}] {it.get('topic', '')}：{it.get('lesson', '')}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return ("\n\n【历史积累的选股知识点（以往复盘总结的经验，归因与学习点需参考运用）】\n"
            + "\n".join(lines))


def _load_predictions(today_str):
    """加载今天早报/午报存档的预测，用于准确率复盘"""
    try:
        with open(f"briefings/predictions_{today_str}.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""
    if not data:
        return ""
    parts = []
    for entry in data:
        parts.append(f"【{entry.get('type', '')}的预测 @ {str(entry.get('time', ''))[:16]}】\n"
                     + "\n".join(entry.get("predictions", [])))
    return "\n\n".join(parts)


def step1_fetch_data():
    """抓取异动股和当日新闻"""
    print("[1/4] 抓取异动股+当日新闻...")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", NEWS_HOURS="9")
    r1 = subprocess.run([sys.executable, "fetch_movers.py"],
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace", env=env)
    if "MOVERS" not in r1.stdout:
        print(r1.stdout[-1500:], r1.stderr[-1500:])
        raise RuntimeError("异动股抓取失败")
    r2 = subprocess.run([sys.executable, "fetch_news.py"],
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace", env=env)
    if "SAVED" not in r2.stdout:
        raise RuntimeError("新闻抓取失败")
    with open("movers.json", "r", encoding="utf-8") as f:
        movers_data = json.load(f)
    with open("news_24h.json", "r", encoding="utf-8") as f:
        news_items = json.load(f)
    print(f"      异动股 {len(movers_data['movers'])} 只，新闻 {len(news_items)} 条")
    return movers_data, news_items


def step2_build_prompt(movers_data, news_items):
    lessons_ctx = _load_lessons()
    pred_ctx = _load_predictions(datetime.now(CST).strftime("%Y-%m-%d"))
    # 当日新闻（含描述，压缩到16000字符）
    lines, total = [], 0
    seen = set()
    for iso, src, title, desc, link in news_items:
        t = re.sub(r"\s+-\s+[^-]{4,40}$", "", title).strip()
        key = t[:40].lower()
        if key in seen:
            continue
        seen.add(key)
        line = f"- [{src}] {t}"
        if desc and desc not in t:
            line += f" —— {desc[:120]}"
        if total + len(line) > 16000:
            break
        lines.append(line)
        total += len(line)
    news_text = "\n".join(lines)

    md = movers_data
    indices = " | ".join(f"{i['name']} {i['change']:+.2f}%" for i in md["indices"])
    ind_boards = " | ".join(f"{b['name']} +{b['change']}%(龙头:{b['leader']})"
                            for b in md["industry_boards"])
    con_boards = " | ".join(f"{b['name']} +{b['change']}%(龙头:{b['leader']})"
                            for b in md["concept_boards"])
    stocks = "\n".join(
        f"- {m['code']} {m['name']} +{m['change']}% | 成交{m['amount_yi']}亿 | "
        f"换手{m['turnover']}% | 主力净流入{m['main_inflow_yi']}亿 | 所属: {m['sector']}"
        for m in md["movers"])

    # 自选股专项分析板块已移除（2026-09-23，用户要求）

    # 市场情绪指标（涨跌家数/涨停跌停/炸板率/连板高度/情绪周期定位）
    senti_text = "（情绪数据缺失）"
    try:
        with open("sentiment.json", "r", encoding="utf-8") as f:
            s = json.load(f)
        if s:
            senti_text = (
                f"情绪定位: {s.get('sentiment')} | 涨跌家数: 涨{s.get('up_count')}/跌{s.get('down_count')} | "
                f"涨停{s.get('zt_count')}家 跌停{s.get('dt_count')}家 炸板{s.get('zb_count')}家"
                f"（炸板率{(s.get('zb_rate') or 0) * 100:.0f}%） | 最高连板{s.get('max_lianban')}板"
                f"（{s.get('lianban_ladder', '')}） | 解读: {s.get('explain')}")
    except Exception:
        pass

    system = ("你是一名资深A股投资教练。今天是交易日收盘后，用户给你：①今日收盘数据"
              "（指数、领涨板块、涨幅超5%的异动股及其成交/换手/主力资金/所属行业）；"
              "②今日全天新闻列表。请用中文输出一份《盘后异动复盘》，核心目的是教会读者"
              "'看懂异动背后的驱动因素，学会日后关注什么信息'。总字数不少于1200字，"
              "严格遵守以下格式，不要输出任何思考过程：\n\n"
              "## 📊 今日盘面速览\n2-3句话概括三大指数表现+市场情绪，必须引用给出的【市场情绪指标】"
              "（涨跌家数比、涨停/跌停家数、炸板率、最高连板高度、情绪周期定位如冰点/偏暖/亢奋），"
              "并给出今天适合进攻还是防守的明确判断，列出领涨行业与概念板块。\n\n"
              "## 🎯 预测准确性复盘\n"
              "对照用户材料中的【早报/午报预测存档】，逐条用今日实际收盘数据验证，输出判定表：\n"
              "每条格式：· **[✅准确/⚠️部分准确/❌不准确/❓无法验证] 预测摘要**：实际结果"
              "（引用收盘指数/板块/个股数据）+ 偏差原因（1-2句，点明当时漏看了什么信息、"
              "下次预测同类情况应注意什么）\n"
              "若材料中无预测存档，此板块写'今日无预测存档，跳过复盘'。\n\n"
              "## 🔍 异动股逐个归因（对给出的每只异动股各写一条，约10-15条）\n"
              "每条格式：· **股票名(代码) +X.X%｜驱动因素类型标签**\n"
              "  归因分析（2-3句）：从以下角度交叉判断——①政策/监管（今日或近期政策、部委表态）；"
              "②行业/产业事件（涨价、订单、技术突破、行业景气）；③公司公告/业绩（若新闻无据可查，"
              "结合所属板块整体动因说明大概率是板块联动或资金行为，并明确标注'暂无公开消息，建议盘后查公告'）；"
              "④资金面（结合主力净流入、换手率判断是机构行为还是游资炒作）；⑤题材/概念联动（属哪个热点链条）。\n"
              "归因必须与当日新闻互相印证（引用新闻中的具体事件），新闻里没有线索的要诚实标注，不要编造。\n\n"
              "## 📚 今日选股学习点（3-5条）\n"
              "从今天的异动中提炼可复用的方法论，每条格式：· **学习点标题**：今天哪类股票因什么涨了"
              "（1句）→ 以后应养成关注什么新闻/数据/时间点的习惯（1-2句，如'以后每晚关注发改委官网"
              "产业政策发布'、'每月X日统计局数据'、'行业产品涨价要盯期货价格'）。\n\n"
              "## ⚠️ 风险提示\n1-2条：今日异动股中哪些可能是纯情绪炒作（高换手+无基本面消息），追高风险。\n\n"
              "要求：信息具体、有数字；归因有据可依，无法判断的诚实说明；用markdown加粗关键词。"
              + lessons_ctx)

    pred_block = pred_ctx if pred_ctx else "（今日无预测存档）"
    user = (f"今天是北京时间 {datetime.now(CST):%Y-%m-%d %H:%M}，A股已收盘。\n\n"
            f"【早报/午报预测存档（用于准确率复盘）】\n{pred_block}\n\n"
            f"【市场情绪指标（盘面速览必须引用）】\n{senti_text}\n\n"
            f"【指数收盘】{indices}\n\n"
            f"【领涨行业板块】{ind_boards}\n\n"
            f"【领涨概念板块】{con_boards}\n\n"
            f"【今日异动股（涨幅>5%，共{len(md['movers'])}只）】\n{stocks}\n\n"
            f"【今日新闻列表】\n{news_text}")
    return system, user


def step3_llm_generate(system, user):
    print("[2/4] LLM归因分析...")
    body = {
        "model": LM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    req = urllib.request.Request(LM_API, data=json.dumps(body).encode("utf-8"),
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=900, context=CONTEXT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:600]
        print(f"      ❌ LLM请求失败 HTTP {e.code}: {err_body}")
        print(f"      (请求体大小: {len(json.dumps(body))} 字符, 消息数: {len(body['messages'])})")
        raise
    msg = data["choices"][0]["message"]
    content = msg.get("content") or ""
    if not content.strip():
        reasoning = msg.get("reasoning_content") or ""
        raw_all = reasoning + (content or "")
        idx = raw_all.rfind("## 📊")
        if idx >= 0:
            content = raw_all[idx:]
            print("      ⚠️ 从reasoning_content兜底提取")
    print(f"      LLM输出 {len(content)} 字")
    return content


def step4_postprocess_and_push(raw, movers_count):
    print("[3/4] 后处理...")
    text = re.sub(r"^```(markdown|md)?\s*|\s*```$", "", raw).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"^\s*[-*•]\s+", "· ", text, flags=re.M)

    now_cst = datetime.now(CST)
    full = (f"# 📈 盘后异动复盘 | {now_cst:%Y-%m-%d %A}\n\n"
            f"> 覆盖今日涨幅>5%异动股 {movers_count} 只 | "
            f"数据源: 东方财富 | 由LLM {LM_MODEL} 归因分析\n\n---\n\n"
            + text)
    os.makedirs("briefings", exist_ok=True)
    path = f"briefings/{now_cst:%Y-%m-%d}_review.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(full)
    with open("briefings/latest.md", "w", encoding="utf-8") as f:
        f.write(full)
    print(f"      已保存 {path}")

    print("[4/4] 推送...")
    if not PUSH_ON:
        print("      (推送已关闭：仅生成存档，不推送。可在运行时设置PUSH_ENABLED开启)")
    # Server酱推送已停用（2026-09-23起）
    else:
        print("      (Server酱已停用，仅走飞书/企微/Bark通道)")
    # 飞书/企业微信机器人推送（内容直接在群里展示，不暴露GitHub地址）
    if PUSH_ON:
        try:
            from feishu_push import push_feishu
            push_feishu("盘后异动复盘", text[:3000])
        except Exception as _fs:
            print(f"      [feishu] err: {_fs}")
        try:
            from wecom_push import push_wecom
            wecom_text = text
            try:
                from wecom_digest import digest_for_wecom
                dig = digest_for_wecom(text, "review")
                if dig:
                    wecom_text = dig
            except Exception as _dg:
                print(f"      [wecom摘要] 失败，回退原文压缩: {_dg}")
            push_wecom("盘后异动复盘", wecom_text)
        except Exception as _wc:
            print(f"      [wecom] err: {_wc}")
        # Bark推送（iOS锁屏弹窗，不带链接避免暴露GitHub）
        try:
            from bark_push import push_bark
            push_bark("盘后异动复盘", text[:400], level="timeSensitive")
        except Exception as _bk:
            print(f"      [bark] err: {_bk}")
    # 微信测试号推送已停用（2026-09-23起）
    return full, path


def _extract_and_save_lessons(review_text, today_str):
    """用LLM从复盘中提炼知识点，追加沉淀到knowledge/lessons.json供日后预测参考"""
    print("[5/5] 提炼新知识点...")
    try:
        system = ("从A股盘后复盘中提炼可长期复用的选股知识点（含预测失误的教训）。"
                  "输出JSON数组，每项格式{\"topic\":\"简短主题\",\"lesson\":\"经验规律，"
                  "必须包含以后应关注什么信息/数据/时间点\"}，最多6项，只输出JSON数组。")
        body = {
            "model": LM_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": review_text[:6000]},
            ],
            "temperature": 0.3, "max_tokens": 1024, "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if LLM_API_KEY:
            headers["Authorization"] = f"Bearer {LLM_API_KEY}"
        req = urllib.request.Request(LM_API, data=json.dumps(body).encode("utf-8"),
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=300, context=CONTEXT) as resp:
            data = json.loads(resp.read())
        content = (data["choices"][0]["message"].get("content") or "").strip()
        m = re.search(r"\[.*\]", content, re.S)
        items = json.loads(m.group(0)) if m else []
        os.makedirs("knowledge", exist_ok=True)
        path = "knowledge/lessons.json"
        try:
            with open(path, "r", encoding="utf-8") as f:
                lessons = json.load(f)
        except Exception:
            lessons = []
        added = 0
        for it in items[:6]:
            if isinstance(it, dict) and it.get("lesson"):
                lessons.append({"date": today_str,
                                "topic": str(it.get("topic", ""))[:50],
                                "lesson": str(it.get("lesson", ""))[:300]})
                added += 1
        lessons = lessons[-200:]   # 最多保留200条，防止无限膨胀
        with open(path, "w", encoding="utf-8") as f:
            json.dump(lessons, f, ensure_ascii=False, indent=1)
        print(f"      新增 {added} 条知识点，累计 {len(lessons)} 条 -> {path}")
    except Exception as e:
        print(f"      ⚠️ 知识点提炼失败（不影响复盘本身）: {e}")


def main():
    t0 = datetime.now(CST)
    movers_data, news_items = step1_fetch_data()
    system, user = step2_build_prompt(movers_data, news_items)
    raw = step3_llm_generate(system, user)
    full, path = step4_postprocess_and_push(raw, len(movers_data["movers"]))
    _extract_and_save_lessons(full, t0.strftime("%Y-%m-%d"))
    print(f"\n✅ 完成！用时 {(datetime.now(CST)-t0).seconds}s，文件: {path}")


if __name__ == "__main__":
    main()
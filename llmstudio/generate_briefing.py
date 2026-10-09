# -*- coding: utf-8 -*-
"""
每日早报生成流水线：
1. 调用 fetch_news.py 抓取过去24小时新闻 -> news_24h.json
2. 将新闻送入 LM Studio 本地 LLM (OpenAI兼容API) 生成中文早报
3. 后处理：清洗LLM输出、加日期头、保存到 briefings/YYYY-MM-DD.md
4. (可选) 推送到 Bark / Server酱 —— 填入URL即可启用
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

# ========== 配置 ==========
# LLM配置：支持云端API和本地LM Studio双模式
# 云端模式：设置环境变量 LLM_API_KEY（如DeepSeek官方API），可选 LLM_API_URL / LLM_MODEL
# 本地模式：不设LLM_API_KEY时，自动使用 localhost:1234 的LM Studio
LM_API = os.environ.get("LLM_API_URL", "https://api.deepseek.com/chat/completions")
LM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
if not LLM_API_KEY:
    # 本地调试兜底：从push_config.json读取llm_api_key（与generate_review.py/generate_x_posts.py一致）
    try:
        with open("push_config.json", "r", encoding="utf-8") as f:
            LLM_API_KEY = json.load(f).get("llm_api_key", "")
    except Exception:
        pass
MAX_NEWS_CHARS = 24000        # 送入LLM的新闻文本上限（含描述，内容更丰富）
MAX_TOKENS = 8192
CST = timezone(timedelta(hours=8))  # 北京时间

# 早报类型：morning(早报,默认) / midday(午间快报)
BRIEFING_TYPE = os.environ.get("BRIEFING_TYPE", "morning")
# 新闻时间窗口（小时）：早报24，午间默认5（覆盖8:00-12:40）
NEWS_HOURS = os.environ.get("NEWS_HOURS", "24" if BRIEFING_TYPE == "morning" else "5")

# 推送配置：优先从 push_config.json 读取，留空则跳过推送
def _load_push_config():
    """返回 (serverchan_keys列表, bark_url)。支持一个或多个接收人"""
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "push_config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        keys = cfg.get("serverchan_keys", [])
        if not keys and cfg.get("serverchan_key"):   # 兼容旧的单key字段
            keys = [cfg["serverchan_key"]]
        return keys, cfg.get("bark_url", "")
    except Exception:
        return [], ""

# 环境变量优先（云端GitHub Actions用），其次本地配置文件
# SERVERCHAN_KEYS: 逗号分隔的多个SendKey，实现多人推送
_sc_env = os.environ.get("SERVERCHAN_KEY", "")
if _sc_env:
    SERVERCHAN_KEYS = [k.strip() for k in _sc_env.split(",") if k.strip()]
    BARK_URL = ""
else:
    SERVERCHAN_KEYS, BARK_URL = _load_push_config()
# 环境变量优先，未设置时回退push_config.json（修复：Actions设SERVERCHAN_KEY时Bark配置丢失的bug）
BARK_URL = os.environ.get("BARK_URL", "") or BARK_URL


def _push_enabled():
    """推送开关：PUSH_ENABLED环境变量优先，其次push_config.json的push_enabled字段"""
    env = os.environ.get("PUSH_ENABLED", "")
    if env:
        return env.lower() in ("1", "true", "yes")
    try:
        with open("push_config.json", "r", encoding="utf-8") as f:
            return bool(json.load(f).get("push_enabled", True))
    except Exception:
        return True


PUSH_ON = _push_enabled()

CONTEXT = ssl.create_default_context()
CONTEXT.check_hostname = False
CONTEXT.verify_mode = ssl.CERT_NONE


def _norm_title(t):
    """标题归一化：去空格标点、转小写、取前30字符，用于跨批次去重"""
    t = re.sub(r"[\s\W_]+", "", t.lower())
    return t[:30]

def _load_used_titles(today_str):
    """加载今天已推送过的新闻标题（用于午间快报去重）"""
    path = f"briefings/used_titles_{today_str}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def _load_morning_briefing(today_str):
    """加载当天早报内容，作为午间快报的对比上下文"""
    path = f"briefings/{today_str}.md"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

def _save_used_titles(today_str, titles):
    """保存今天已推送的新闻标题"""
    os.makedirs("briefings", exist_ok=True)
    path = f"briefings/used_titles_{today_str}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(titles), f, ensure_ascii=False, indent=1)


def _load_lessons(max_chars=3500):
    """加载历史积累的选股知识点（盘后复盘沉淀），注入提示词"""
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
    return ("\n\n【历史积累的选股知识点（以往复盘总结的经验，分析预测时应参考运用）】\n"
            + "\n".join(lines))


def _extract_sections(text, headers, max_len=1800):
    """从早报/午报正文中提取指定章节"""
    out = []
    for h in headers:
        idx = text.find(h)
        if idx < 0:
            continue
        nxt = text.find("\n## ", idx + len(h))
        seg = text[idx:nxt if nxt >= 0 else None].strip()
        out.append(seg[:max_len])
    return out


def _save_predictions(today_str, btype, text):
    """存档本次的预测板块，供盘后复盘做准确率分析"""
    if btype == "morning":
        headers = ["## 🇺🇸 隔夜美股与A股展望", "## 💡 投资者视角"]
    else:
        headers = ["## 📌 午间核心变化", "## 💡 下午盘面提示"]
    sections = _extract_sections(text, headers)
    if not sections:
        return
    os.makedirs("briefings", exist_ok=True)
    path = f"briefings/predictions_{today_str}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = []
    data.append({"type": btype, "time": datetime.now(CST).isoformat(),
                 "predictions": sections})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"      预测已存档 {path}（{len(sections)}段，供盘后准确率复盘）")

def step1_fetch_news():
    """运行 fetch_news.py 抓取新闻，返回 items 列表（午间模式自动去重）"""
    print(f"[1/4] 抓取新闻（类型:{BRIEFING_TYPE}, 窗口:{NEWS_HOURS}小时）...")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", NEWS_HOURS=NEWS_HOURS)
    r = subprocess.run([sys.executable, "fetch_news.py"],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if "SAVED" not in r.stdout:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise RuntimeError("新闻抓取失败")
    with open("news_24h.json", "r", encoding="utf-8") as f:
        items = json.load(f)
    today_str = datetime.now(CST).strftime("%Y-%m-%d")
    if BRIEFING_TYPE == "midday":
        used = _load_used_titles(today_str)
        before = len(items)
        items = [it for it in items if _norm_title(it[2]) not in used]
        print(f"      获取 {before} 条，去除早报已推送 {before - len(items)} 条，剩余 {len(items)} 条")
    else:
        print(f"      获取 {len(items)} 条新闻")
    return items


def step2_build_prompt(items):
    """把新闻拼成给LLM的提示词"""
    lessons_ctx = _load_lessons()
    lines = []
    total = 0
    seen = set()
    for iso, src, title, desc, link in items:
        # 去掉Google News常见的" - 来源名"后缀
        t = re.sub(r"\s+-\s+[^-]{4,40}$", "", title).strip()
        # 简单去重（标题前40字符相同视为重复）
        key = t[:40].lower()
        if key in seen:
            continue
        seen.add(key)
        line = f"- [{src}] {t}"
        if desc and desc not in t:
            line += f" —— {desc[:150]}"
        if total + len(line) > MAX_NEWS_CHARS:
            break
        lines.append(line)
        total += len(line)
    news_text = "\n".join(lines)

    if BRIEFING_TYPE == "midday":
        # 加载当天早报作为上下文，供LLM对比分析事件发酵
        morning_brief = _load_morning_briefing(datetime.now(CST).strftime("%Y-%m-%d"))
        morning_ctx = ""
        if morning_brief:
            # 截断防止上下文过长
            morning_ctx = (f"\n\n【今晨早报全文（供对比参考，直接引用其事件）】\n"
                           f"{morning_brief[:6000]}")
        system = ("你是一名资深财经编辑，为个人投资者撰写午间快报。"
                  "用户给你两部分材料：①今晨早报全文（记录了今天早上8点前推送的事件）；"
                  "②早报推送之后新抓取的全球新闻列表（已过滤掉早报用过的重复新闻）。"
                  "请用中文输出午间快报，总字数不少于600字，严格遵守以下格式，不要输出任何思考过程：\n\n"
                  "## 📌 午间核心变化\n用2-3句话概括：相对今晨早报，上午全球局势的最大变化，及对下午A股/港股开盘的影响方向（利好/利空/中性）。\n\n"
                  "## 🔄 早间事件发酵追踪（2-4条）\n"
                  "将新新闻与早报中的事件对照，判断哪些早间事件在上午出现了**发酵/升级/缓和/反转**：\n"
                  "每条格式：· **事件名（早报已报）**：上午最新进展（1-2句，注明升级/缓和/新增细节），对下午盘面的边际影响变化（1句）\n"
                  "只列确有新进展的；若早间事件上午完全无新消息，不要罗列。\n\n"
                  "## 🆕 上午全新事件（2-5条）\n"
                  "只列早报中完全没有出现过的新事件：\n"
                  "每条格式：· **事件名称**：详情（1-2句），对下午盘面的影响（1句）\n"
                  "按重要性排序，覆盖地缘政治、宏观数据、政策、大宗商品等。\n\n"
                  "## 💡 下午盘面提示（2-3条）\n每条格式：· **方向判断**：具体板块/指数影响+逻辑（1-2句），需综合'早间事件发酵+新事件'两条线。\n\n"
                  "要求：信息具体（有数字、公司名、国家）；严格区分'早报已报事件的进展'与'全新事件'，不要混在同类；只依据给定材料，不要编造。")
    else:
        system = ("你是一名资深财经编辑，为个人投资者撰写详尽的每日早报。"
              "用户给你过去24小时的全球新闻列表（含描述，可能有重复，请自行去重并剔除娱乐八卦等无关内容）。"
              "请用中文输出一份详细早报，总字数不少于900字，严格遵守以下格式，不要输出任何思考过程：\n\n"
              "## 📌 核心总览\n用2-3句话概括过去24小时全球最重要的事态及其对市场的整体影响，并点明隔夜美股表现对今日A股开盘的总体指向。\n\n"
              "## 🇺🇸 隔夜美股与A股展望（2-4条）\n"
              "每条格式：· **美股表现**：道指/纳指/标普涨跌幅及驱动因素（财报、数据、美联储表态等，1-2句），对今日A股相关板块的开盘影响（1句，如'纳指大涨利好A股AI算力链开盘'）\n"
              "必须覆盖：三大指数涨跌、中概股/纳斯达克金龙指数表现、对A股对应板块（科技/AI/新能源/消费等）的开盘传导判断；如新闻中有美股期货、美债收益率、美元指数信息也一并纳入。\n\n"
              "## 🌍 地缘政治（4-6条）\n每条格式：· **事件名称**：事件详情（1-2句），对市场/相关行业的影响（1句）\n"
              "必须覆盖：军事冲突、大国博弈、能源地缘、制裁与关税等。\n\n"
              "## 📉 宏观经济（4-6条）\n每条格式：· **事件名称**：关键数据/政策详情（1-2句），对利率/汇率/债市/通胀的含义（1句）\n"
              "必须覆盖：央行动向、就业/通胀数据、债券市场、房地产等。\n\n"
              "## 🤖 科技与AI（3-5条）\n每条格式：· **公司/事件**：事件详情（1-2句），行业影响或竞争格局变化（1句）\n\n"
              "## 🏢 公司与市场（4-6条）\n每条格式：· **公司名**：发生了什么（财报/并购/裁员/股价异动，含具体数字），市场反应或分析师观点（1句）\n"
              "优先覆盖：知名大公司财报与重大动作、大宗商品（原油/黄金）、个股异动。\n\n"
              "## 💡 投资者视角（3-4条）\n每条格式：· **风险/机会**：具体提示+逻辑（1-2句），如'关注能源股：油价逼近90美元...'\n"
              "应包含：1条风险警示、1-2条板块机会、1条仓位/情绪建议。\n\n"
              "要求：信息具体（有数字、有公司名、有国家），不要空泛；使用markdown加粗标注关键词；只依据给定新闻，不要编造。")
    us_note = ""
    if BRIEFING_TYPE == "morning":
        wd = datetime.now(CST).weekday()
        if wd <= 4:
            monday_note = ("今天周一：隔夜美股为上周五收盘（美国时间周五），距A股开盘跨度较大，"
                           "分析时请明确注明美股是上周五的交易。") if wd == 0 else ""
            us_note = ("\n\n【补充要求：美股活跃板块→A股板块映射】"
                       + monday_note
                       + "\n请在'隔夜美股与A股展望'板块中，除三大指数与中概股外，还必须从新闻中识别隔夜美股"
                         "表现最强与最弱的各2-3个板块（如半导体、新能源车、生物医药、大型科技、军工等），"
                         "并对每个板块给出今日A股对应板块的开盘传导判断，"
                         "格式：· **美股板块(涨跌幅)** → A股对应板块：传导逻辑与强弱预判（1-2句）。")
        else:
            us_note = ("\n\n【补充说明】今天是周六或周日，A股休市：本次早报不需要输出美股板块对A股板块的"
                       "映射分析，其他内容正常输出即可。")
    system = system + lessons_ctx + us_note
    if BRIEFING_TYPE == "midday":
        user = (f"今天是北京时间 {datetime.now(CST):%Y-%m-%d %H:%M}，A股即将于13:00开盘。"
                f"{morning_ctx}\n\n"
                f"【上午新抓取的新闻（已去重）】\n{news_text}")
    else:
        user = f"今天是北京时间 {datetime.now(CST):%Y-%m-%d %H:%M}。以下是过去24小时新闻：\n{news_text}"
    return system, user


def _chat(body):
    """发送一次chat请求，返回 (content, finish_reason)"""
    headers = {"Content-Type": "application/json"}
    api_url, model = LM_API, LM_MODEL
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    else:
        # 本地调试兜底：无Key时回退本地LM Studio（云端Actions必有Key不受影响）
        api_url, model = "http://localhost:1234/v1/chat/completions", "qwen/qwen3.5-9b"
        body = dict(body)
        body["model"] = model
        print("      (无LLM_API_KEY，本地调试回退 LM Studio qwen3.5-9b)")
    if "localhost" in api_url:
        body["reasoning_effort"] = "none"   # 本地LM Studio关闭思考模式
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(api_url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=900, context=CONTEXT) as resp:
        data = json.loads(resp.read())
    msg = data["choices"][0]["message"]
    finish = data["choices"][0].get("finish_reason", "")
    return (msg.get("content") or "", finish)


def step3_llm_generate(system, user):
    """调用LLM生成早报（云端API优先，无Key回退LM Studio本地）
    截断修复：finish_reason=length时自动续写拼接，避免早报尾部内容丢失"""
    print("[2/4] 调用LLM生成早报...")
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
    content, finish = _chat(body)
    # 截断兜底：输出达到token上限时续写（最多2次），拼接为完整早报
    rounds = 0
    while finish == "length" and rounds < 2:
        rounds += 1
        print(f"      ⚠️ 输出被max_tokens截断，自动续写第{rounds}次...")
        body["messages"].append({"role": "assistant", "content": content})
        body["messages"].append({
            "role": "user",
            "content": "你的上一条回复在结尾处被系统截断了。请从中断处无缝继续输出剩余内容，"
                       "不要重复任何已输出的内容，不要重新开始，直接接着写完。"})
        more, finish = _chat(body)
        if more.strip():
            content += more
    if rounds:
        print(f"      已续写{rounds}次，最终输出 {len(content)} 字")
    print(f"      LLM输出 {len(content)} 字")
    return content


def step4_postprocess(raw, items_count):
    """后处理：去掉思考标签/冗余空行，加元信息头，落盘"""
    print("[3/4] 后处理...")
    # 移除 DeepSeek/Qwen 思维链标签
    text = re.sub(r"", "", raw, flags=re.S).strip()
    # 去掉开头的多余引导语和markdown代码围栏
    text = re.sub(r"^```(markdown|md)?\s*|\s*```$", "", text).strip()
    # 压缩3个以上连续空行为2个
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 统一全角/半角项目符号
    text = re.sub(r"^\s*[-*•]\s+", "· ", text, flags=re.M)

    now_cst = datetime.now(CST)
    if BRIEFING_TYPE == "midday":
        title = f"📊 午间要闻快报 | {now_cst:%Y-%m-%d %H:%M}"
        scope = f"覆盖今晨早报之后的新事件 | 数据源: {items_count} 条"
        fname = f"briefings/{now_cst:%Y-%m-%d}_midday.md"
    else:
        title = f"# 🌏 全球要闻早报 | {now_cst:%Y-%m-%d %A}"
        scope = f"覆盖过去24小时 | 数据源: {items_count} 条"
        fname = f"briefings/{now_cst:%Y-%m-%d}.md"
    header = (f"{title}\n\n"
              f"> {scope} (BBC/CNBC/MarketWatch/Reuters) | 由LLM {LM_MODEL} 生成\n\n---\n\n")
    full = header + text
    import os
    os.makedirs("briefings", exist_ok=True)
    path = fname
    with open(path, "w", encoding="utf-8") as f:
        f.write(full)
    # 同时维护 latest.md 方便App/推送读取
    with open("briefings/latest.md", "w", encoding="utf-8") as f:
        f.write(full)
    # 记录本次使用的新闻标题，供下一批次去重
    print(f"      已保存 {path}")
    return full, path


def step5_push(briefing):
    """推送到手机（PUSH_ENABLED环境变量/push_config.json控制开关）"""
    print("[4/4] 推送...")
    if not PUSH_ON:
        print("      (推送已关闭：仅生成存档，不推送Server酱)")
        return False
    now_cst = datetime.now(CST)
    push_title = "午间要闻快报" if BRIEFING_TYPE == "midday" else "全球要闻早报"
    # 提取总览作为推送摘要（兼容新旧两种格式）
    m = (re.search(r"## 📌 午间核心变化\s*\n(.+)", briefing)
         or re.search(r"【一句话总览】\s*(.+)", briefing)
         or re.search(r"## 📌 核心总览\s*\n(.+)", briefing))
    summary = m.group(1).strip() if m else "今日全球要闻已生成"
    summary = summary[:120]

    sent = False
    if BARK_URL:
        try:
            url = f"{BARK_URL.rstrip('/')}/{urllib.request.quote(push_title)}/{urllib.request.quote(summary)}"
            urllib.request.urlopen(url, timeout=15, context=CONTEXT)
            print("      ✅ Bark 推送成功")
            sent = True
        except Exception as e:
            print(f"      ❌ Bark 推送失败: {e}")
    # Server酱推送已停用（2026-09-23起，用户要求停用server酱与测试公众号）
    # 飞书/企业微信机器人推送（内容直接在群里展示，不暴露GitHub地址）
    if PUSH_ON:
        try:
            from feishu_push import push_feishu
            push_feishu(push_title, briefing[:3000])
            sent = True
        except Exception as _fs:
            print(f"      [feishu] err: {_fs}")
        try:
            from wecom_push import push_wecom
            wecom_text = briefing
            try:
                from wecom_digest import digest_for_wecom
                dig = digest_for_wecom(briefing, BRIEFING_TYPE)
                if dig:
                    wecom_text = dig
            except Exception as _dg:
                print(f"      [wecom摘要] 失败，回退原文压缩: {_dg}")
            push_wecom(push_title, wecom_text)
            sent = True
        except Exception as _wc:
            print(f"      [wecom] err: {_wc}")
    if not sent:
        print("      (未配置推送通道，跳过。在脚本顶部填 BARK_URL 或 SERVERCHAN_KEY 启用)")
    return sent


def main():
    t0 = datetime.now(CST)
    today_str = t0.strftime("%Y-%m-%d")
    items = step1_fetch_news()
    system, user = step2_build_prompt(items)
    raw = step3_llm_generate(system, user)
    briefing, path = step4_postprocess(raw, len(items))
    # 存档本次预测板块，供盘后复盘做准确率分析
    _save_predictions(today_str, BRIEFING_TYPE, briefing)
    # 记录本次使用的新闻标题（早报新建，午间合并追加），供下一批次去重
    used = _load_used_titles(today_str)
    used.update(_norm_title(it[2]) for it in items)
    _save_used_titles(today_str, used)
    step5_push(briefing)
    print(f"\n✅ 完成！用时 {(datetime.now(CST)-t0).seconds}s，早报文件: {path}")
    print("\n" + "=" * 50 + "\n")
    print(briefing)


if __name__ == "__main__":
    main()
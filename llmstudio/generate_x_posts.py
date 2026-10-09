# -*- coding: utf-8 -*-
"""
X（Twitter）推文生成器：
从当天的早报/午报/复盘中提取关键信息，生成可直接发布的推文草稿。

用法：
  python generate_x_posts.py              # 按当前时间自动选择类型
  python generate_x_posts.py morning      # 指定从早报生成
  python generate_x_posts.py midday       # 指定从午报生成
  python generate_x_posts.py review       # 指定从复盘生成

输出：
  1. x_posts/YYYY-MM-DD_{type}.md  （标准版+加长版草稿+发布检查清单）
  2. 标准版自动复制到剪贴板（Windows clip）
  3. 控制台打印全部内容

说明：
  - LLM配置与 generate_briefing.py 一致（环境变量 LLM_API_KEY/LLM_API_URL/LLM_MODEL）
  - 推文只基于简报中的事实生成，提示词已禁止编造；发布前请人工核对数字
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import ssl
from datetime import datetime, timezone, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ========== 配置（与 generate_briefing.py 保持一致） ==========
LM_API = os.environ.get("LLM_API_URL", "https://api.deepseek.com/chat/completions")
LM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
if not LLM_API_KEY:
    try:
        with open("push_config.json", "r", encoding="utf-8") as f:
            LLM_API_KEY = json.load(f).get("llm_api_key", "")
    except Exception:
        pass
MAX_TOKENS = 2048
CST = timezone(timedelta(hours=8))

# X 免费账号单帖上限（中文字符按2计的保守估算值）
STANDARD_LIMIT = 280

CONTEXT = ssl.create_default_context()
CONTEXT.check_hostname = False
CONTEXT.verify_mode = ssl.CERT_NONE


def _detect_type():
    """按当前时间自动选择简报类型：8-12点早报，12-15点午报，15点后复盘"""
    h = datetime.now(CST).hour
    if 8 <= h < 12:
        return "morning"
    elif 12 <= h < 15:
        return "midday"
    return "review"


def _load_briefing(btype, today_str):
    """加载对应类型的简报文件"""
    if btype == "morning":
        path = f"briefings/{today_str}.md"
    elif btype == "midday":
        path = f"briefings/{today_str}_midday.md"
    else:
        path = f"briefings/{today_str}_review.md"
    if not os.path.exists(path):
        # 兜底：用latest.md
        if os.path.exists("briefings/latest.md"):
            print(f"      ⚠️ 未找到 {path}，回退使用 briefings/latest.md")
            path = "briefings/latest.md"
        else:
            raise FileNotFoundError(f"未找到简报文件 {path}，请先运行对应的生成脚本")
    with open(path, "r", encoding="utf-8") as f:
        return f.read(), path


def _build_prompt(btype, briefing):
    """构建推文生成提示词：只允许使用简报中的事实"""
    type_desc = {"morning": "早报", "midday": "午间快报", "review": "盘后复盘"}[btype]

    system = (
        "你是一名资深财经自媒体编辑，运营一个A股/美股主题的X（Twitter）中文账号。"
        f"用户给你一份今天生成的《{type_desc}》，请从中提取最重要、最有冲击力的信息，"
        "生成两条可直接发布到X的推文草稿。严格遵守以下要求：\n\n"
        "【铁律】\n"
        "1. 只能使用简报中出现的事实、数字、公司名，绝对禁止编造或夸大任何数据；\n"
        "2. 简报中没有的信息不要写；不确定的表述用'或/或有望/警惕'等留有余地的措辞；\n"
        "3. 不出现'据我系统''AI生成'等字眼；\n"
        "4. 涉及个股观点时保持中性客观，结尾可加'非投资建议'。\n\n"
        "【输出格式】严格按以下格式输出，不要输出任何思考过程：\n\n"
        "## 标准版（≤280字符，免费账号可发）\n"
        "结构：①首行钩子（用🚨或📊开头，制造悬念/数字冲击）→ ②2-4条核心事实（每条一行，"
        "用①②③编号，保留具体数字）→ ③一句方向判断 → ④互动提问（如'你怎么看？👇'）→ "
        "⑤2-3个话题标签（如 #A股 #美股）\n"
        "总字符数（含emoji和标签）严格控制在280以内。\n\n"
        "## 加长版（X Premium用，信息量更足）\n"
        "结构：①钩子开头 → ②3-4个要点（用1️⃣2️⃣3️⃣编号，每条2-3句，保留数字细节）→ "
        "③'💡 策略/关注点'小节（2-3条）→ ④互动引导 → ⑤3-4个话题标签\n\n"
        "## 发布检查清单\n"
        "列出3-5条发布前需人工核对的关键数字（如指数涨跌幅、订单金额等），"
        "格式：'- [ ] 核对：XXX是否为简报中的原值'\n\n"
        "要求：语言口语化、有节奏感，像交易员发推而不是新闻稿；钩子必须放在第一行。"
    )
    user = f"今天是北京时间 {datetime.now(CST):%Y-%m-%d %H:%M}。以下是今天的{type_desc}：\n\n{briefing[:12000]}"
    return system, user


def _llm_generate(system, user):
    """调用LLM生成推文草稿"""
    print("[2/4] 调用LLM生成推文草稿...")
    body = {
        "model": LM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.5,
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    api_url, model = LM_API, LM_MODEL
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    else:
        # 本地调试兜底：无Key时回退本地LM Studio（与generate_briefing.py一致）
        api_url, model = "http://localhost:1234/v1/chat/completions", "qwen/qwen3.5-9b"
        body["model"] = model
        body["reasoning_effort"] = "none"
        print("      (无LLM_API_KEY，本地调试回退 LM Studio)")
    req = urllib.request.Request(api_url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=600, context=CONTEXT) as resp:
        data = json.loads(resp.read())
    msg = data["choices"][0]["message"]
    content = msg.get("content") or ""
    if not content.strip():
        # 兜底：从reasoning_content中提取
        raw_all = (msg.get("reasoning_content") or "") + content
        idx = raw_all.find("## 标准版")
        if idx >= 0:
            content = raw_all[idx:]
            print("      ⚠️ 从reasoning_content兜底提取")
    content = re.sub(r"^```(markdown|md)?\s*|\s*```$", "", content.strip())
    print(f"      LLM输出 {len(content)} 字")
    return content


def _count_chars(text):
    """估算X字符数：中文/emoji按2计，英文数字按1计（X实际按权重计算，此为保守估算）"""
    n = 0
    for ch in text:
        n += 2 if ord(ch) > 0x2000 else 1
    return n


def _extract_standard_section(text):
    """提取标准版正文并统计字符数"""
    m = re.search(r"##\s*标准版[^\n]*\n(.*?)(?=\n##\s|\Z)", text, re.S)
    if not m:
        return "", 0
    body = m.group(1).strip()
    # 去掉markdown代码围栏
    body = re.sub(r"^```\w*\n?|\n?```$", "", body).strip()
    return body, _count_chars(body)


def _copy_to_clipboard(text):
    """复制到系统剪贴板：Windows用clip，macOS用pbcopy，Linux尝试xclip/xsel"""
    try:
        if sys.platform.startswith("win"):
            p = subprocess.Popen("clip", stdin=subprocess.PIPE, shell=True)
            _, _ = p.communicate(text.encode("utf-16-le"))
        elif sys.platform == "darwin":
            p = subprocess.Popen("pbcopy", stdin=subprocess.PIPE)
            _, _ = p.communicate(text.encode("utf-8"))
        else:
            cmd = ("xclip -selection clipboard" if shutil.which("xclip")
                   else "xsel --clipboard --input" if shutil.which("xsel") else "")
            if not cmd:
                print("      ⚠️ 未找到剪贴板工具(xclip/xsel)，请手动复制")
                return False
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, shell=True)
            _, _ = p.communicate(text.encode("utf-8"))
        if p.returncode != 0:
            print(f"      ⚠️ 剪贴板命令退出码异常: {p.returncode}")
            return False
        return True
    except Exception as e:
        print(f"      ⚠️ 剪贴板复制失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="从当日简报生成X推文草稿")
    parser.add_argument("type", nargs="?", default=None,
                        choices=["morning", "midday", "review"],
                        help="简报类型：morning/midday/review（默认按时间自动选择）")
    args = parser.parse_args()

    t0 = datetime.now(CST)
    today_str = t0.strftime("%Y-%m-%d")
    btype = args.type or _detect_type()

    print(f"[1/4] 加载{btype}简报...")
    briefing, src_path = _load_briefing(btype, today_str)
    print(f"      来源: {src_path}（{len(briefing)} 字）")

    system, user = _build_prompt(btype, briefing)
    raw = _llm_generate(system, user)

    print("[3/4] 后处理与字符校验...")
    std_body, std_chars = _extract_standard_section(raw)
    over = std_chars > 560  # X按2字符/中文计，280上限=560权重
    warn = f"⚠️ 超限（约{std_chars}权重，上限560）" if over else f"✅ 约{std_chars}权重（限560）"

    # 组装输出文件
    type_cn = {"morning": "早报", "midday": "午报", "review": "复盘"}[btype]
    header = (f"# X推文草稿 | {today_str} {type_cn}\n\n"
              f"> 来源: `{src_path}` | 生成时间: {t0:%H:%M} | "
              f"标准版字符校验: {warn}\n\n"
              f"> ⚠️ 发布前必做：①核对下方检查清单中的数字 ②快速搜索确认无重大突发"
              f" ③账号观察期内只发纯财经内容\n\n---\n\n")
    full = header + raw.strip() + "\n"

    os.makedirs("x_posts", exist_ok=True)
    out_path = f"x_posts/{today_str}_{btype}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full)
    print(f"      已保存 {out_path}")

    print("[4/4] 复制标准版到剪贴板...")
    if std_body:
        if _copy_to_clipboard(std_body):
            print("      ✅ 标准版已复制到剪贴板，直接 Ctrl+V 到 X 发布框")
        else:
            print("      (复制失败，请从输出文件手动复制)")
    else:
        print("      ⚠️ 未解析到标准版，请从输出文件手动复制")

    # 控制台输出完整草稿
    print("\n" + "=" * 50)
    print(full)
    print("=" * 50)
    if over:
        print("⚠️ 标准版超长，请删减后再发布！")
    print(f"\n✅ 完成！用时 {(datetime.now(CST)-t0).seconds}s")


if __name__ == "__main__":
    main()
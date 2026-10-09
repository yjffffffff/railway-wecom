# -*- coding: utf-8 -*-
"""企业微信推送专用摘要模块
用 DeepSeek 把长报告改写成适合群聊阅读的精简版：
- 总量控制在企微4096字节上限以内（约1100个汉字）
- 只保留关键信息（结论/数字/可操作提示），条目必须是完整短句，不截断、不产生省略号
- LLM失败或超限时返回None，调用方回退原文 + wecom_push的结构化压缩兜底
配置来源与 generate_briefing.py/generate_review.py 一致：
环境变量 LLM_API_URL / LLM_MODEL / LLM_API_KEY 优先，其次 push_config.json
"""
import json
import os
import re
import ssl
import urllib.request

try:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LM_API = os.environ.get("LLM_API_URL", "https://api.deepseek.com/chat/completions")
LM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
if not LLM_API_KEY:
    try:
        with open("push_config.json", "r", encoding="utf-8") as f:
            LLM_API_KEY = json.load(f).get("llm_api_key", "")
    except Exception:
        pass

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

MAX_DIGEST_BYTES = 3400   # 摘要正文目标（不含【标题】行），给4096上限留足余量

_RULES = ("要求：\n"
          "1. 总字数不超过1000个汉字；宁可少列条目、缩短句子，也不要超字数；\n"
          "2. 保留markdown章节标题（##开头）与'·'列表格式；每条必须是完整短句，"
          "禁止省略号、禁止句子被截断、禁止用'等等'收尾；\n"
          "3. 只保留关键信息：明确结论、具体数字、可操作提示；删掉铺垫、背景解释、"
          "重复论述和修饰性文字；\n"
          "4. 不要输出开场白、客套话或总结语，直接从第一个章节标题开始输出。")

# 各类报告的板块压缩规则
_SECTIONS = {
    "morning": (
        "## 📌 核心总览：2-3句；\n"
        "## 🇺🇸 隔夜美股与A股展望：每条压成1句（美股表现+对A股板块的传导结论）；\n"
        "## 🌍 地缘政治 / ## 📉 宏观经济 / ## 🤖 科技与AI / ## 🏢 公司与市场："
        "每个板块只挑最重要的1-2条，每条1句；\n"
        "## 💡 投资者视角：风险1条、机会1-2条、仓位建议1条，各1句"),
    "midday": (
        "## 📌 午间核心变化：2-3句；\n"
        "## 🔄 早间事件发酵追踪：每条压成1句；\n"
        "## 🆕 上午全新事件：最多4条，每条1句；\n"
        "## 💡 下午盘面提示：2-3条，每条1句"),
    "review": (
        "## 📊 今日盘面速览：2-3句（指数涨跌+情绪定位+进攻还是防守）；\n"
        "## 🎯 预测准确性复盘：每条预测的判定必须保留（✅/⚠️/❌/❓ + 预测对象 + "
        "一句话实际验证结果），偏差原因压成半句；\n"
        "## 🔍 异动股逐个归因：只挑最重要的3-4只，每只1-2句点明驱动因素与资金面结论；\n"
        "## 📚 今日选股学习点：保留2-3条，每条1句；\n"
        "## ⚠️ 风险提示：1-2条"),
}


def _chat(prompt):
    body = {
        "model": LM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 2048,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    req = urllib.request.Request(LM_API, data=json.dumps(body).encode("utf-8"),
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=180, context=_CTX) as resp:
        data = json.loads(resp.read())
    return (data["choices"][0]["message"].get("content") or "").strip()


def _clean(text):
    """去掉代码围栏、开场白（首个##章节之前的内容）与多余空行"""
    text = re.sub(r"^```(markdown|md)?\s*|\s*```$", "", text.strip()).strip()
    i = text.find("## ")
    if i > 0:
        text = text[i:]
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def digest_for_wecom(text, kind="review"):
    """把报告全文摘要成企微可读的精简版；失败时返回None（调用方回退原文）"""
    if not LLM_API_KEY:
        print("      [wecom摘要] 无LLM_API_KEY，跳过摘要")
        return None
    prompt = (f"以下是一份A股投资日报的完整内容，请改写成企业微信群机器人推送的精简版。\n"
              f"{_RULES}\n"
              f"必须保留的板块及各板块压缩规则：\n{_SECTIONS.get(kind, _SECTIONS['review'])}\n\n"
              f"【原文】\n{text[:12000]}")
    try:
        out = _clean(_chat(prompt))
        if not out:
            return None
        # 超限时再压一轮
        if len(out.encode("utf-8")) > MAX_DIGEST_BYTES:
            shorter = _clean(_chat(
                f"把下面内容进一步压缩到{(MAX_DIGEST_BYTES - 200) // 3}个汉字以内，"
                f"保持'##'章节结构与完整短句，不要截断：\n{out}"))
            if shorter and len(shorter.encode("utf-8")) < len(out.encode("utf-8")):
                out = shorter
        print(f"      [wecom摘要] 生成精简版 {len(out)} 字 / "
              f"{len(out.encode('utf-8'))} 字节")
        return out
    except Exception as e:
        print(f"      [wecom摘要] LLM失败: {e}")
        return None

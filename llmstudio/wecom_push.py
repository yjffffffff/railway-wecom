# -*- coding: utf-8 -*-
"""企业微信群机器人推送模块
配置来源：环境变量 WECOM_WEBHOOK 优先，其次 push_config.json 的 wecom_webhook
获取方式（手机2分钟）：
  1. 下载企业微信App（微信身份直接登录）
  2. 建一个群（可只拉自己，或建"文件传输助手"式单人群——群聊至少3人？
     企业微信支持"仅自己"的内部群，或者拉1个家人凑3人）
  3. 群设置 → 群机器人 → 添加 → 自定义机器人 → 复制Webhook
  （企业微信群机器人无需关键词校验，直接可用）

用法：
  from wecom_push import push_wecom
  push_wecom("盘后异动复盘", "正文...")
"""
import urllib.request
import json
import ssl
import sys
import os

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0"}

MAX_BYTES = 4000   # 企业微信text消息上限4096字节(utf-8)，留余量

# 行首属于"结构行"（标题/引用/分隔线）的前缀，压缩时完整保留，保证章节不丢
_STRUCT_PREFIX = ("#", ">", "---")
# 句末标点：裁剪时优先在这些字符后断开，避免句子被拦腰截断
_SENT_END = "。！？；…!?;."


def _utf8_len(s):
    return len(s.encode("utf-8"))


def _trim_line(line, target_bytes):
    """把单行裁剪到target_bytes字节以内
    优先在前缀后半段的句末标点处断开并追加省略号；预算过小时丢弃该行"""
    if _utf8_len(line) <= target_bytes:
        return line
    if target_bytes < 8:
        return ""
    budget = target_bytes - 3            # 为省略号"…"预留3字节
    lo, hi = 0, len(line)
    while lo < hi:                        # 二分找到不超过budget的最长前缀
        mid = (lo + hi + 1) // 2
        if _utf8_len(line[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    cut = line[:lo]
    for i in range(len(cut) - 1, max(0, len(cut) // 2) - 1, -1):
        if cut[i] in _SENT_END:
            cut = cut[:i + 1]
            break
    return cut + "…"


def condense_content(content, max_bytes):
    """把正文压缩到max_bytes（UTF-8字节）以内，用于企微推送
    思路：结构行（#/引用/分隔线）原样保留 -> 所有章节标题都在；
    其余行按字节占比等比裁剪 -> 每章都保留摘要，而不是尾部整体硬截断；
    装不下时循环收紧比例，最终兜底按字节截断且不拆碎UTF-8字符"""
    if _utf8_len(content) <= max_bytes:
        return content
    lines = content.split("\n")
    struct = {i for i, l in enumerate(lines) if l.lstrip().startswith(_STRUCT_PREFIX)}
    body_idx = [i for i in range(len(lines)) if i not in struct]
    seps = len(lines) - 1                          # 行间换行符
    struct_bytes = sum(_utf8_len(lines[i]) for i in struct)
    body_bytes = sum(_utf8_len(lines[i]) for i in body_idx)
    budget = max_bytes - struct_bytes - seps
    ratio = (budget / body_bytes) if body_bytes else 1.0
    out = content
    for _ in range(40):
        if ratio <= 0:
            ratio = 0.01
        new = list(lines)
        for i in body_idx:
            new[i] = _trim_line(lines[i], int(_utf8_len(lines[i]) * ratio))
        out = "\n".join(new)
        if _utf8_len(out) <= max_bytes:
            return out
        ratio *= 0.92
    return out.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def _build_body(title, content):
    """组包：【标题】+正文，正文按剩余预算压缩，整体不超过MAX_BYTES"""
    head = f"【{title}】"
    avail = MAX_BYTES - _utf8_len(head) - 1        # -1：标题后的换行符
    return f"{head}\n{condense_content(content, avail)}"


def _load_webhook():
    """环境变量优先，其次push_config.json"""
    url = os.environ.get("WECOM_WEBHOOK", "")
    if url:
        return url
    try:
        with open("push_config.json", "r", encoding="utf-8") as f:
            return json.load(f).get("wecom_webhook", "")
    except Exception:
        return ""


def push_wecom(title, content, retries=2):
    """推送企业微信群文本消息，返回是否成功"""
    hook = _load_webhook()
    if not hook:
        print("[wecom] 未配置 WECOM_WEBHOOK，跳过")
        return False
    # 结构化压缩到4096字节上限以内（保留全部章节标题，逐行按比例裁剪，避免硬截断）
    body = _build_body(title, content)
    # 兜底：任何异常情况仍按字节截断，防止超4096报错
    while _utf8_len(body) > MAX_BYTES:
        body = body[:int(len(body) * 0.9)]
    payload = json.dumps({
        "msgtype": "text",
        "text": {"content": body},
    }, ensure_ascii=False).encode("utf-8")

    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                hook, data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                d = json.loads(r.read().decode("utf-8", errors="replace"))
            if d.get("errcode") == 0:
                print("      ✅ 企业微信推送成功")
                return True
            last_err = RuntimeError(f"企业微信返回: {d}")
        except Exception as e:
            last_err = e
    print(f"      ❌ 企业微信推送失败: {last_err}")
    return False


if __name__ == "__main__":
    ok = push_wecom("企业微信通道测试",
                    "如果你在企业微信群里看到这条消息，说明机器人推送已就绪 ✅")
    sys.exit(0 if ok else 1)
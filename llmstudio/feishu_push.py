# -*- coding: utf-8 -*-
"""飞书自定义机器人推送模块
配置来源：环境变量 FEISHU_WEBHOOK 优先，其次 push_config.json 的 feishu_webhook
获取方式：飞书群 → 设置 → 群机器人 → 添加自定义机器人（安全设置选"自定义关键词"
  如"复盘/快报/监控"，或不勾任何校验）→ 复制webhook地址

用法：
  from feishu_push import push_feishu
  push_feishu("盘后异动复盘", "正文内容...")     # 纯文本，长内容自动分段
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

MAX_CHARS = 8000   # 飞书单条text上限约150KB，这里按可读性截断


def _load_webhook():
    """环境变量优先，其次push_config.json"""
    url = os.environ.get("FEISHU_WEBHOOK", "")
    if url:
        return url
    try:
        with open("push_config.json", "r", encoding="utf-8") as f:
            return json.load(f).get("feishu_webhook", "")
    except Exception:
        return ""


def push_feishu(title, content, retries=2):
    """推送飞书文本消息，返回是否成功
    title: 标题（加粗首行）
    content: 正文（纯文本/markdown符号会原样显示）"""
    hook = _load_webhook()
    if not hook:
        print("[feishu] 未配置 FEISHU_WEBHOOK，跳过")
        return False
    body = f"**{title}**\n{content}"[:MAX_CHARS]
    payload = json.dumps({
        "msg_type": "text",
        "content": {"text": body},
    }, ensure_ascii=False).encode("utf-8")

    last_err = None
    for i in range(2):
        try:
            req = urllib.request.Request(
                hook, data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                d = json.loads(r.read().decode("utf-8", errors="replace"))
            if d.get("code") == 0 or d.get("StatusCode") == 0:
                print("      ✅ 飞书推送成功")
                return True
            last_err = RuntimeError(f"飞书返回: {d}")
        except Exception as e:
            last_err = e
    print(f"      ❌ 飞书推送失败: {last_err}")
    return False


if __name__ == "__main__":
    ok = push_feishu("飞书通道测试",
                     "如果你在飞书群里看到这条消息，说明飞书机器人推送已就绪 ✅")
    sys.exit(0 if ok else 1)
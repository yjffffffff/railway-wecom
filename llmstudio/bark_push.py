# -*- coding: utf-8 -*-
"""Bark 推送模块（iOS锁屏弹窗，无限量）
统一入口：push_bark(title, content, url="", level="active")
配置来源：环境变量 BARK_URL 优先，其次 push_config.json 的 bark_url
  - BARK_URL 形如 https://api.day.app/你的Key
  - 支持自建服务器，只要 base URL + key
用法：
  from bark_push import push_bark
  push_bark("标题", "正文")
  push_bark("标题", "正文", url="https://...", level="timeSensitive")
level 说明（Bark官方）：
  active    默认，亮屏提醒
  timeSensitive 时效性通知，专注模式也可弹出
  critical  勿扰模式持续响铃（重要告警用）
"""
import urllib.request
import urllib.parse
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

LEVELS = {"active", "timeSensitive", "critical"}


def _load_bark_url():
    """环境变量优先，其次push_config.json"""
    url = os.environ.get("BARK_URL", "")
    if url:
        return url
    try:
        with open("push_config.json", "r", encoding="utf-8") as f:
            return json.load(f).get("bark_url", "")
    except Exception:
        return ""


def push_bark(title, content, url="", level="active", retries=2):
    """推送Bark通知，返回是否成功
    title/content: 文本（自动URL编码）
    url: 非空时点击通知跳转该网页
    level: active/timeSensitive/critical
    """
    base = _load_bark_url().rstrip("/")
    if not base:
        print("[bark] 未配置 BARK_URL，跳过")
        return False
    if level not in LEVELS:
        level = "active"

    # Bark URL格式: {base}/{title}/{body}?level=xx&url=xx&group=xx
    path = f"{urllib.parse.quote(title, safe='')}/{urllib.parse.quote(content[:900], safe='')}"
    params = {"level": level, "group": "stockMonitor"}
    if url:
        params["url"] = url
    qs = urllib.parse.urlencode(params)
    full = f"{base}/{path}?{qs}"

    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(full, headers=UA)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                d = json.loads(r.read().decode("utf-8", errors="replace"))
            if d.get("code") == 200:
                print(f"      ✅ Bark 推送成功（level={level}）")
                return True
            last_err = RuntimeError(f"Bark返回异常: {d}")
        except Exception as e:
            last_err = e
            print(f"      [bark] retry {i + 1}/{retries} failed: {e}")
    print(f"      ❌ Bark 推送失败: {last_err}")
    return False


if __name__ == "__main__":
    # 自测：python bark_push.py
    ok = push_bark("Bark测试", "如果你看到这条弹窗，说明Bark通道已就绪 ✅",
                   level="timeSensitive")
    sys.exit(0 if ok else 1)
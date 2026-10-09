# -*- coding: utf-8 -*-
"""微信公众号推送模块（测试号/服务号模板消息 + 订阅号群发）
支持 url：点击模板消息卡片直接打开网页（放完整内容）
用法：
  from wechat_push import push_wechat
  push_wechat("标题", "正文", url="https://...")
"""
import urllib.request
import urllib.parse
import json
import ssl
import sys
import os
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0"}

TOKEN_CACHE_FILE = "knowledge/wechat_token.json"


def _load_cfg():
    """读取微信配置：环境变量优先，其次push_config.json"""
    cfg = {
        "channel": os.environ.get("WECHAT_CHANNEL", ""),
        "app_id": os.environ.get("WECHAT_APP_ID", ""),
        "app_secret": os.environ.get("WECHAT_APP_SECRET", ""),
        "openids": [x.strip() for x in os.environ.get("WECHAT_OPENIDS", "").split(",") if x.strip()],
        "template_id": os.environ.get("WECHAT_TEMPLATE_ID", ""),
    }
    if not cfg["app_id"]:
        try:
            with open("push_config.json", "r", encoding="utf-8") as f:
                w = json.load(f).get("wechat", {})
            for k in cfg:
                if not cfg[k] and w.get(k):
                    cfg[k] = w[k]
        except Exception:
            pass
    if not cfg["channel"]:
        cfg["channel"] = "test" if cfg["template_id"] else "mass"
    return cfg


def _get_token(app_id, app_secret):
    """获取access_token，缓存7200秒"""
    now = time.time()
    try:
        with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
            c = json.load(f)
        if c.get("app_id") == app_id and c.get("expire", 0) > now + 60:
            return c["token"]
    except Exception:
        pass
    url = ("https://api.weixin.qq.com/cgi-bin/token?"
           f"grant_type=client_credential&appid={app_id}&secret={app_secret}")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        d = json.loads(r.read().decode("utf-8", errors="replace"))
    if "access_token" not in d:
        raise RuntimeError(f"获取token失败: {d}")
    token = d["access_token"]
    os.makedirs("knowledge", exist_ok=True)
    with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"app_id": app_id, "token": token,
                   "expire": now + d.get("expires_in", 7200)}, f)
    return token


def _post_json(url, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def push_template(title, content, url=""):
    """测试号/服务号：模板消息推送（无限次、实时弹提醒）
    url: 非空时，点击卡片打开该网页（放完整内容）"""
    cfg = _load_cfg()
    if not cfg["app_id"] or not cfg["app_secret"]:
        print("[wechat] 未配置 app_id/app_secret，跳过")
        return False
    if not cfg["template_id"]:
        print("[wechat] 未配置 template_id，跳过")
        return False
    if not cfg["openids"]:
        print("[wechat] 未配置 openids，跳过")
        return False
    token = _get_token(cfg["app_id"], cfg["app_secret"])
    api = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={token}"
    ok = 0
    for openid in cfg["openids"]:
        payload = {
            "touser": openid,
            "template_id": cfg["template_id"],
            "data": {
                "first": {"value": title, "color": "#173177"},
                "content": {"value": content[:1500], "color": "#000000"},
                "remark": {"value": "", "color": "#999999"},
            },
        }
        if url:
            payload["url"] = url
        try:
            d = _post_json(api, payload)
            if d.get("errcode") == 0:
                ok += 1
            else:
                print(f"[wechat] 推送失败 openid={openid[:8]}...: {d}")
        except Exception as e:
            print(f"[wechat] 推送异常: {e}")
    print(f"[wechat] 模板消息推送 {ok}/{len(cfg['openids'])} 成功")
    return ok > 0


def push_wechat(title, content, url="", force_channel=None):
    """统一入口（已全局停用 2026-09-23：用户要求停用测试公众号推送）
    如需恢复，将下面的 WECHAT_PUSH_DISABLED 改为 False"""
    WECHAT_PUSH_DISABLED = True
    if WECHAT_PUSH_DISABLED:
        print(f"[wechat] 推送已全局停用（2026-09-23起），跳过: {title}")
        return False
    cfg = _load_cfg()
    ch = force_channel or cfg["channel"]
    if ch == "test" or (ch != "mass" and cfg["template_id"]):
        return push_template(title, content, url=url)
    return push_mass(title, content)


def push_mass(title, content):
    """订阅号群发（每天1次，弹提醒）"""
    cfg = _load_cfg()
    if not cfg["app_id"]:
        print("[wechat] 未配置 app_id，跳过群发")
        return False
    token = _get_token(cfg["app_id"], cfg["app_secret"])
    draft_url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
    article = {
        "articles": [{
            "title": title, "author": "AI复盘", "digest": content[:100],
            "content": content.replace("\n", "<br>"), "content_source_url": "",
            "need_open_comment": 0, "only_fans_can_comment": 0,
        }]
    }
    try:
        d = _post_json(draft_url, article)
        if "media_id" not in d:
            print(f"[wechat] 建草稿失败: {d}")
            return False
        media_id = d["media_id"]
    except Exception as e:
        print(f"[wechat] 建草稿异常: {e}")
        return False
    mass_url = f"https://api.weixin.qq.com/cgi-bin/message/mass/sendall?access_token={token}"
    payload = {"filter": {"is_to_all": True}, "mpnews": {"media_id": media_id}, "msgtype": "mpnews"}
    try:
        d = _post_json(mass_url, payload)
        if d.get("errcode") == 0:
            print("[wechat] 群发成功")
            return True
        print(f"[wechat] 群发失败: {d}")
    except Exception as e:
        print(f"[wechat] 群发异常: {e}")
    return False


if __name__ == "__main__":
    push_wechat("测试推送", "这是一条测试消息。", url="https://github.com/tothemoonYJF/GlobalHotTopics")
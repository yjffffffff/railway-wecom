# -*- coding: utf-8 -*-
"""抓取多个全球新闻RSS源，筛选过去24小时内的新闻并输出。"""
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import ssl, re, html, sys, os

# Windows控制台可能是GBK，强制UTF-8并容忍不可编码字符
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CONTEXT = ssl.create_default_context()
CONTEXT.check_hostname = False
CONTEXT.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

FEEDS = {
    "Wallstreetcn": "https://dedicated.wallstreetcn.com/rss.xml",
    "BBC-World": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "CNBC-TopNews": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "CNBC-Economy": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    "MarketWatch-Top": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Reuters-ViaGoogle": "https://news.google.com/rss/search?q=when:1d+allinurl:reuters.com&hl=en-US&gl=US&ceid=US:en",
    "Google-World24h": "https://news.google.com/rss/search?q=world+news+when:1d&hl=en-US&gl=US&ceid=US:en",
}

now = datetime.now(timezone.utc)
# 时间窗口：默认过去24小时；可通过环境变量 NEWS_HOURS 覆盖（如午间快报用5）
NEWS_HOURS = float(os.environ.get("NEWS_HOURS", "24"))
cutoff = now - timedelta(hours=NEWS_HOURS)

def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25, context=CONTEXT) as r:
        return r.read()

def clean(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()

items = []
for name, url in FEEDS.items():
    try:
        root = ET.fromstring(fetch(url))
    except Exception as e:
        print(f"# FETCH-FAIL {name}: {e}")
        continue
    for item in root.iter("item"):
        title = clean(item.findtext("title"))
        link = clean(item.findtext("link") or "")
        desc = clean(item.findtext("description"))
        pub = item.findtext("pubDate")
        try:
            dt = parsedate_to_datetime(pub)
        except Exception:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt >= cutoff:
            items.append((dt, name, title, desc[:220], link))

items.sort(key=lambda x: x[0], reverse=True)
print(f"# TOTAL: {len(items)}  (window: {NEWS_HOURS}h, UTC now: {now:%Y-%m-%d %H:%M})")
for dt, src, title, desc, link in items:
    print(f"[{dt:%m-%d %H:%M} UTC] ({src}) {title}")
    if desc:
        print(f"    {desc}")
items_out = [(dt.isoformat(), s, t, d, l) for dt, s, t, d, l in items]
import json
with open("news_24h.json", "w", encoding="utf-8") as f:
    json.dump(items_out, f, ensure_ascii=False, indent=1)
print("# SAVED news_24h.json")
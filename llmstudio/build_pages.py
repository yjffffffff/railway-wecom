# -*- coding: utf-8 -*-
"""把 briefings/*.md 渲染成静态HTML发布到 docs/，供 GitHub Pages 托管
生成：
  docs/<name>.html  —— 每篇简报的网页版
  docs/index.html   —— 首页，按日期倒序列出所有简报
"""
import os
import re
import glob
from datetime import datetime

BRIEFINGS_DIR = "briefings"
DOCS_DIR = "docs"


def md_to_html(md):
    """轻量Markdown→HTML（覆盖标题/加粗/列表/引用/分割线/链接）"""
    lines = md.split("\n")
    html, in_ul = [], False

    def inline(s):
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", s)
        s = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2" target="_blank">\1</a>', s)
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        return s

    for ln in lines:
        if ln.startswith("### "):
            if in_ul:
                html.append("</ul>"); in_ul = False
            html.append(f"<h3>{inline(ln[4:])}</h3>")
        elif ln.startswith("## "):
            if in_ul:
                html.append("</ul>"); in_ul = False
            html.append(f"<h2>{inline(ln[3:])}</h2>")
        elif ln.startswith("# "):
            if in_ul:
                html.append("</ul>"); in_ul = False
            html.append(f"<h1>{inline(ln[2:])}</h1>")
        elif ln.strip() in ("---", "***"):
            if in_ul:
                html.append("</ul>"); in_ul = False
            html.append("<hr>")
        elif ln.startswith("> "):
            if in_ul:
                html.append("</ul>"); in_ul = False
            html.append(f"<blockquote>{inline(ln[2:])}</blockquote>")
        elif ln.strip().startswith("- ") or ln.strip().startswith("· ") or \
                ln.lstrip().startswith("· "):
            if not in_ul:
                html.append("<ul>"); in_ul = True
            item = ln.strip()
            item = item[2:] if item[:2] in ("- ", "· ") else item
            html.append(f"<li>{inline(item)}</li>")
        elif ln.strip() == "":
            if in_ul:
                html.append("</ul>"); in_ul = False
        else:
            if in_ul:
                html.append("</ul>"); in_ul = False
            html.append(f"<p>{inline(ln)}</p>")
    if in_ul:
        html.append("</ul>")
    return "\n".join(html)


PAGE_TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
 body{{max-width:820px;margin:0 auto;padding:16px 18px 60px;
   font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
   line-height:1.75;color:#222;background:#fff}}
 h1{{font-size:22px;border-bottom:2px solid #eaeaea;padding-bottom:10px}}
 h2{{font-size:19px;color:#173177;margin-top:28px;border-left:4px solid #173177;padding-left:10px}}
 h3{{font-size:17px;color:#333;margin-top:20px}}
 strong{{color:#c0392b}}
 blockquote{{background:#f6f8fa;border-left:4px solid #ccc;margin:10px 0;padding:8px 14px;color:#555;font-size:14px}}
 ul{{padding-left:20px}} li{{margin:6px 0}}
 hr{{border:none;border-top:1px solid #eaeaea;margin:20px 0}}
 a{{color:#173177}}
 code{{background:#f2f2f2;padding:1px 5px;border-radius:3px;font-size:13px}}
 .nav{{font-size:13px;color:#888;margin-bottom:14px}}
</style>
</head>
<body>
<div class="nav"><a href="./index.html">← 返回目录</a></div>
{body}
</body>
</html>"""

INDEX_TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A股AI复盘 · 每日简报</title>
<style>
 body{{max-width:720px;margin:0 auto;padding:20px 18px 60px;
   font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#222;background:#fff}}
 h1{{font-size:22px}} .sub{{color:#888;font-size:13px;margin-bottom:20px}}
 .day{{margin:18px 0 6px;font-weight:600;color:#173177}}
 a.item{{display:block;padding:10px 14px;margin:6px 0;background:#f8f9fb;
   border-radius:8px;text-decoration:none;color:#222;font-size:15px}}
 a.item:hover{{background:#eef2ff}}
</style>
</head>
<body>
<h1>📈 A股AI复盘 · 每日简报</h1>
<div class="sub">自动生成 · 数据源东方财富 · 由 AI 归因分析</div>
{items}
</body>
</html>"""


def main():
    os.makedirs(DOCS_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(BRIEFINGS_DIR, "*.md")), reverse=True)
    items = []
    cur_date = None
    for path in files:
        name = os.path.splitext(os.path.basename(path))[0]
        if name == "latest":
            continue
        with open(path, "r", encoding="utf-8") as f:
            md = f.read()
        title = md.split("\n")[0].lstrip("# ").strip() or name
        body = md_to_html(md)
        out = PAGE_TPL.replace("{title}", title).replace("{body}", body)
        with open(os.path.join(DOCS_DIR, f"{name}.html"), "w", encoding="utf-8") as f:
            f.write(out)

        # 首页分组
        date_part = name[:10]
        if re.match(r"\d{4}-\d{2}-\d{2}", date_part):
            if date_part != cur_date:
                items.append(f'<div class="day">{date_part}</div>')
                cur_date = date_part
            label = name[11:].replace("_", " ") or "简报"
            typ = {"review": "📈 盘后复盘", "midday": "🌤 午间快报",
                   "lhb": "🐉 龙虎榜解读"}.get(label, "📰 " + label)
            items.append(f'<a class="item" href="./{name}.html">{typ}</a>')
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(INDEX_TPL.replace("{items}", "\n".join(items)))
    print(f"生成 {len(files)} 篇 + index.html -> {DOCS_DIR}/")


if __name__ == "__main__":
    main()
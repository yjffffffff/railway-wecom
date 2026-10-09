# -*- coding: utf-8 -*-
"""wecom_push 结构化压缩的离线验证（不发真实请求）：
取 briefings/ 下真实的早报、午间快报、盘后复盘，
压缩后必须 <= MAX_BYTES 字节，且所有章节标题原样保留"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wecom_push import MAX_BYTES, _build_body, condense_content, _trim_line

CASES = [
    ("全球要闻早报", "briefings/2026-09-25.md"),
    ("午间要闻快报", "briefings/2026-09-25_midday.md"),
    ("盘后异动复盘", "briefings/2026-09-25_review.md"),
    ("盘后异动复盘", "briefings/2026-09-24_review.md"),
]


def main():
    passed = 0
    for title, path in CASES:
        if not os.path.exists(path):
            print(f"SKIP {path}（文件不存在）")
            continue
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        body = _build_body(title, text)
        n = len(body.encode("utf-8"))
        assert n <= MAX_BYTES, f"{path}: {n}B > {MAX_BYTES}B"
        # 所有章节标题必须原样保留
        headers = [l for l in text.split("\n") if l.lstrip().startswith("#")]
        missing = [h for h in headers if h not in body]
        assert not missing, f"{path}: 章节标题丢失 {missing}"
        # 不能出现多字节字符被截断产生的替换符
        assert "\ufffd" not in body, f"{path}: 出现字节截断替换符"
        print(f"OK {path}: {len(text.encode('utf-8'))}B -> {n}B, "
              f"{len(headers)}个章节标题全部保留")
        print(f"   尾部: ...{body[-100:]!r}")
        passed += 1

    # 单元级边界用例
    assert condense_content("短文本", 1000) == "短文本"          # 放得下不动
    tiny = condense_content("甲。乙。丙。丁。戊。己。庚。辛。", 20)
    assert len(tiny.encode("utf-8")) <= 20, tiny                # 极小预算
    assert _trim_line("这是一条很长的句子，包含标点。后面还有内容要写。", 30) \
        .endswith("…")                                          # 句末截断+省略号
    assert _trim_line("短行", 4) == "短行" or _trim_line("短行", 4) in ("短行", "")
    body = _build_body("标题", "")
    assert len(body.encode("utf-8")) <= MAX_BYTES and body.endswith("\n")
    print(f"边界用例通过（上限 {MAX_BYTES} 字节）")
    print(f"ALL PASS ({passed} 个真实文件)")


if __name__ == "__main__":
    main()

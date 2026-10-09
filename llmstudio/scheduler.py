# -*- coding: utf-8 -*-
"""本地调度器：替代 GitHub Actions 的全部定时任务
按北京时间调度，任务完成后自动 git commit+push 存档（briefings/knowledge/movers等）

调度表（北京时间，当前生效）：
  07:35  generate_briefing.py        早报（每日）
  09:35  dragon_signals.py          游资信号·龙头前瞻（交易日）
  12:40  generate_briefing.py       午报（交易日，BRIEFING_TYPE=midday）
  14:45  dragon_signals.py          游资信号·打板/低吸（交易日）
  15:30  generate_review.py         盘后异动复盘（交易日）
  已暂停（取消对应注释即可恢复）：周期题材+形态扫描、游资信号·席位跟踪、
                                盘中监控、龙虎榜晚报、隔夜闪报
  任务成功后: build_pages.py + git push

用法：
  python scheduler.py                          # 前台常驻运行
  python scheduler.py --once 脚本.py "任务名"    # 手动单次
"""
import subprocess
import sys
import os
import time
import json
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CST = timezone(timedelta(hours=8))

# 任务表: (时, 分, 星期过滤, 脚本, 环境变量, 说明)
# 星期过滤: None=每天, "1-5"=周一到周五
JOBS = [
    (7, 35, None, "generate_briefing.py", {}, "每日早报"),
    # 盘中监控暂时停用（2026-09-23，用户要求），恢复时取消下行注释
    # (9, 20, "1-5", "intraday_monitor.py", {}, "盘中监控长循环"),
    (9, 35, "1-5", "dragon_signals.py", {"DRAGON_MODE": "preopen"}, "游资信号·龙头前瞻"),
    (12, 40, "1-5", "generate_briefing.py", {"BRIEFING_TYPE": "midday"}, "午间快报"),
    (14, 45, "1-5", "dragon_signals.py", {"DRAGON_MODE": "intraday"}, "游资信号·打板低吸"),
    (15, 30, "1-5", "generate_review.py", {}, "盘后异动复盘"),
    # 周期题材+形态扫描暂停（2026-09-28，用户要求：与早报/盘后复盘内容重复），恢复时取消下行注释
    # (15, 40, "1-5", "pattern_scanner.py", {}, "周期题材+形态扫描"),
    # 龙虎榜晚报暂停（2026-09-23，用户要求），恢复时取消下行注释
    # (18, 30, "1-5", "generate_lhb.py", {}, "龙虎榜晚报"),
    # 游资信号·席位跟踪暂停（2026-09-28，用户要求：与龙虎榜晚报重复），恢复时取消下行注释
    # (18, 35, "1-5", "dragon_signals.py", {"DRAGON_MODE": "hotmoney"}, "游资信号·席位跟踪"),
]
# 隔夜闪报已暂停（2026-09-23，用户要求）：空列表=不触发；恢复时还原下行注释的整点/半点列表
# FLASH_HOURS = [(h, m) for h in (21, 22, 23, 0, 1, 2, 3) for m in (0, 30)]
FLASH_HOURS = []

STATE_FILE = "scheduler_state.json"
LOG_FILE = "scheduler_log.txt"


def now_cst():
    return datetime.now(CST)


def log(msg):
    line = f"[{now_cst():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def weekday_ok(now, spec):
    if spec is None:
        return True
    if spec == "1-5":
        return now.weekday() <= 4   # Python: 0=周一
    return True


def run_job(script, env_extra, desc):
    """运行任务脚本，返回是否成功"""
    log(f"▶ 开始任务: {desc} ({script})")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PUSH_ENABLED="true",
               **env_extra)
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, script], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=7200, env=env)
        ok = r.returncode == 0
        tail = ((r.stdout or "") + (r.stderr or ""))[-300:]
        log(f"{'✅' if ok else '❌'} {desc} rc={r.returncode} "
            f"用时{time.time() - t0:.0f}s | {tail.strip()}")
        return ok
    except Exception as e:
        log(f"❌ {desc} 异常: {e}")
        return False


def _push_url():
    """push目标URL：有GITHUB_TOKEN时注入认证（Railway等容器环境）"""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return ["git", "push"]
    try:
        url = subprocess.run(["git", "config", "--get", "remote.origin.url"],
                             capture_output=True, text=True,
                             timeout=30).stdout.strip()
        if url.startswith("https://github.com/"):
            path = url[len("https://github.com/"):]
            return ["git", "push",
                    f"https://x-access-token:{token}@github.com/{path}"]
    except Exception:
        pass
    return ["git", "push"]


def git_commit_push(desc):
    """任务产物自动提交推送（briefings/knowledge/movers等）
    容器环境需环境变量 GITHUB_TOKEN（repo读写权限token）"""
    try:
        subprocess.run(["git", "add", "briefings/", "knowledge/",
                        "movers.json", "sentiment.json", "docs/"],
                       capture_output=True, timeout=60)
        diff = subprocess.run(["git", "diff", "--staged", "--quiet"],
                              capture_output=True, timeout=60)
        if diff.returncode == 0:
            log("  (git: 无变更，跳过提交)")
            return
        msg = f"local scheduler: {desc} {now_cst():%Y-%m-%d %H:%M}"
        subprocess.run(["git", "-c", "user.name=llmstudio-scheduler",
                        "-c", "user.email=scheduler@llmstudio.local",
                        "commit", "-m", msg],
                       capture_output=True, timeout=60)
        r = subprocess.run(_push_url(), capture_output=True, text=True,
                           timeout=120)
        log(f"  git push: {'✅' if r.returncode == 0 else '❌ ' + (r.stderr or '')[-200:]}")
        subprocess.run([sys.executable, "build_pages.py"],
                       capture_output=True, timeout=120)
    except Exception as e:
        log(f"  git/构建异常: {e}")


def due_jobs(now):
    """返回当前时刻应触发的任务列表"""
    jobs = []
    for h, m, wd, script, envx, desc in JOBS:
        envx = dict(envx)
        if script == "dragon_signals.py" and "DRAGON_MODE" in envx:
            envx["DRAGON_MODE"] = envx["DRAGON_MODE"]   # 透传模式参数
        if now.hour == h and now.minute == m and weekday_ok(now, wd):
            jobs.append((script, envx, desc))
    if (now.hour, now.minute) in FLASH_HOURS:
        jobs.append(("flash_alert.py", {}, "隔夜闪报检查"))
    return jobs


def _ensure_git_repo():
    """容器环境（railway up上传无.git）：clone远端仓库到当前目录以支持git存档推送"""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        log("  (未配置GITHUB_TOKEN，跳过git存档功能)")
        return
    if os.path.isdir(".git"):
        return
    try:
        # 找到远端仓库：从GITHUB_REPO环境变量或默认仓库
        repo = os.environ.get("GITHUB_REPO", "tothemoonYJF/GlobalHotTopics")
        log(f"  初始化git仓库（clone {repo}）...")
        # 先备份运行时生成的文件，clone后恢复
        import shutil
        keep = {}
        for p in ("briefings", "knowledge", "movers.json", "sentiment.json"):
            if os.path.isdir(p):
                keep[p] = "_bak_" + p
                shutil.move(p, keep[p])
            elif os.path.isfile(p):
                keep[p] = "_bak_" + p
                os.rename(p, keep[p])
        r = subprocess.run(
            ["git", "clone", "--depth", "1",
             f"https://x-access-token:{token}@github.com/{repo}.git", "_repo"],
            capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            log(f"  ❌ clone失败: {(r.stderr or '')[-200:]}")
            for p, bak in keep.items():
                if os.path.isdir(bak):
                    shutil.move(bak, p)
                elif os.path.isfile(bak):
                    os.rename(bak, p)
            return
        # 把clone内容移到当前目录
        for item in os.listdir("_repo"):
            src = os.path.join("_repo", item)
            if item == ".git":
                os.rename(src, ".git")
                continue
            if os.path.exists(src):
                if os.path.isdir(src) and not os.path.exists(item):
                    shutil.move(src, item)
                elif os.path.isfile(src) and not os.path.exists(item):
                    shutil.copy2(src, item)
        shutil.rmtree("_repo", ignore_errors=True)
        # 恢复运行时文件（云端新生成的优先）
        for p, bak in keep.items():
            if os.path.isdir(bak):
                shutil.rmtree(p, ignore_errors=True)
                shutil.move(bak, p)
            elif os.path.isfile(bak):
                os.replace(bak, p)
        subprocess.run(["git", "config", "user.name", "llmstudio-scheduler"],
                       capture_output=True, timeout=30)
        subprocess.run(["git", "config", "user.email", "scheduler@llmstudio.local"],
                       capture_output=True, timeout=30)
        log("  ✅ git仓库初始化完成")
    except Exception as e:
        log(f"  ❌ git初始化异常: {e}")


def main_loop():
    log("=" * 60)
    log("本地调度器启动（替代 GitHub Actions）")
    log(f"已注册 {len(JOBS)} 个定时任务 + 隔夜闪报{len(FLASH_HOURS)}次/天")
    log("=" * 60)
    _ensure_git_repo()
    state = load_state()
    while True:
        now = now_cst()
        for script, envx, desc in due_jobs(now):
            key = f"{desc}@{now:%Y-%m-%d-%H-%M}"
            if state.get(key):
                continue
            state[key] = True
            cutoff = (now - timedelta(days=3)).strftime("%Y-%m-%d")
            state = {k: v for k, v in state.items()
                     if k.split("@")[-1][:10] >= cutoff}
            save_state(state)
            if run_job(script, envx, desc):
                git_commit_push(desc)
        time.sleep(30)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--once":
        ok = run_job(sys.argv[2], {}, sys.argv[3] if len(sys.argv) > 3 else "手动任务")
        if ok:
            git_commit_push(sys.argv[3])
        sys.exit(0 if ok else 1)
    main_loop()
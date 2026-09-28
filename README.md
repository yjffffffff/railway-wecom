# A股多主线自适应轮动量化推送系统

基于 GitHub Actions / Railway + 企微，实现「缩量下跌→底部平台→放量突破→回踩确认→退潮」五阶段形态的全自动监控与推送，
监控池（主线板块 + 龙头 + 全市场流动性核心池）**每周全自动轮换，零硬编码标的**。

## 核心逻辑

### 六层触发体系
| 层级 | 类型 | 触发条件 | 频控 |
|------|------|----------|------|
| L1 | 宏观阶段切换 | 大盘量价+政策信号共振 | 1/日 |
| L2 | 政策落地实锤 | 白名单板块政策文件发布 | - |
| L3 | 经贸利好窗口 | 免签/关税/协定利好落地 | 1/日 |
| L4 | **个股达标买点** | 五阶段形态评分≥60 | 3/日，股票10日冷却 |
| L5 | 龙头异动风向 | 龙头放量3x/换手30%/跌破MA10 | 板块2/日 |
| L6 | 风控熔断预警 | 两融单日净减>50亿/大盘破位 | 即时 |

### 选股五阶段必要条件（L4 核心）
1. **缩量下跌**：近20日量能较前高缩减 ≥50%
2. **底部平台**：横盘整理 10-60 日，振幅 ≤25%
3. **放量突破**：放量 ≥1.5x 均量，收盘站上平台上沿
4. **回踩确认**：5日内回踩不破突破位，缩量企稳
5. **退潮未至**：板块情绪未进入退潮期（龙头未跌破 MA10）

综合评分 = 形态分(40) + 基本面分(30) + 强弱度分(30)

## 部署步骤

### 1. 准备企微推送渠道
**方式一：企微应用（推荐）**
- 企业微信管理后台 → 应用管理 → 创建应用
- 获取：`CorpID`、`AgentID`、`Secret`
- 设置应用可信域名、配置回调（可选）

**方式二：群机器人（备用）**
- 群设置 → 添加群机器人 → 复制 Webhook URL

### 2. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env 填入真实值
```

### 3. 本地测试
```bash
# 安装依赖
pip install -r requirements.txt

# 测试模式（仅打印不推送）
python main.py --date 2026-09-24 --mode test

# 正式运行（收盘后跑必须加 --force，否则被交易时段频控静默丢弃）
python main.py --date 2026-09-24 --mode scan --force

# 日报预览 / 正式推送
python send_daily_summary.py --dry-run
python send_daily_summary.py

# 全市场自适应轮换（板块+龙头+流动性核心池，全部算法生成）
python main.py --mode rebalance --no-notify
```

### 4. Railway / GitHub Actions 部署
1. 将代码推送到 GitHub 仓库
2. Railway Dashboard → New Project → Deploy from GitHub（或直接用仓库内置的 GitHub Actions）
3. 在 Variables / Repository secrets 添加环境变量（从 `.env` 复制）
4. 定时任务（UTC）：
   - `.github/workflows/push.yml`：`30 7 * * 1-5`（北京时间周一至周五 15:30）→ `python main.py --mode scan --force` + `python send_daily_summary.py`
   - `.github/workflows/universe_rebalance.yml`：`30 0 * * 6`（北京时间周六 08:30）→ `python main.py --mode rebalance`，自动提交更新后的 `config/triggers.yaml`
   - Railway Cron 等价配置：`30 7 * * 1-5` → `python main.py --mode scan --force`

> ⚠️ 定时任务在 15:30（收盘后）执行，必须带 `--force`：`frequency_control.trading_hours_only=true`
> 且 `quiet_hours` 覆盖 `15:00-09:15`，否则所有消息会被静默丢弃（日志显示"非交易时间/静默期，延迟推送"），
> 表现为"任务成功但企微收不到推送"。

### 5. 手动触发/补数
```bash
# Railway CLI
railway run python main.py --date 2026-09-24 --mode scan --force

# 或在 Dashboard 点击 Deploy → Deploy Latest；GitHub Actions 页面可 Run workflow
```

### 6. 国内网络推送 / 拉取（GitHub 直连被重置时）
```bash
# 仓库已内置 mirror remote（ghfast.top 反向代理，读写均可，实测可 push）
git push mirror main

# 或临时用任意 gh-proxy 系镜像推送（示例）
git push https://<user>:<token>@ghfast.top/https://github.com/yjffffffff/railway-wecom.git main:main

# 仅拉取时也可用只读镜像
git clone https://ghproxy.net/https://github.com/yjffffffff/railway-wecom.git
```
> 说明：`github.com:443` 在部分网络下会被重置（`Recv failure: Connection was reset`），
> 此时走 gh-proxy 系镜像即可（`ghfast.top` / `gh-proxy.com` / `ghproxy.net` 已验证可读，`ghfast.top` 可写）。

```
railway-wecom/
├── main.py              # CLI 入口（scan / test / rebalance）
├── config_loader.py     # 配置加载（YAML + ENV）
├── data_collector.py    # 数据采集（多源自动切换 + 熔断）
├── eastmoney_client.py  # 东财直连（快照/K线）
├── tencent_client.py    # 腾讯直连（日线/实时，云端友好）
├── stock_selector.py    # 选股引擎（五阶段评分）
├── universe_updater.py  # 全市场自适应轮动池更新（板块/龙头/流动性核心池）
├── send_daily_summary.py# 收盘复盘日报（动态日期 + 动态板块强弱榜）
├── push_engine.py       # 触发引擎 + 企微推送
├── check_sources.py     # 数据源连通性自检脚本
├── config/
│   ├── triggers.yaml    # 核心配置（sector_whitelist / focus_stocks 由脚本自动生成）
│   └── universe_state.json # 上一期轮动快照（用于生成纳入/剔除差异）
├── .github/workflows/
│   ├── push.yml             # 盘中/收盘扫描 + 复盘日报
│   └── universe_rebalance.yml # 每周六自动轮换监控池并提交
├── requirements.txt
├── Dockerfile
├── railway.toml
├── .env.example
└── README.md
```

## 监控池轮动机制
- **全动态、零硬编码**：`sector_whitelist`（10 大主线 × 5 只龙头）与 `focus_stocks`（全市场成交额榜）全部由
  `universe_updater.py` 依据真实行情（新浪行业/个股资金数据）生成，`config/triggers.yaml` 请勿手工维护标的。
- **为什么周频而非日频**：五阶段模型依赖 10-60 日的平台蓄势与缩量结构，日频换池会不断打断形态识别；
  周频（周六盘前）在时效性与样本连续性之间取平衡。
- **风控过滤**：自动剔除 `ST / 退市 / 北交所(8、4、920 开头)` 标的，仅保留真实可交易品种。
- **可追溯**：每期生成 `config/universe_state.json` 快照，轮换报告自动列出「新进池 / 移出池」名单并推送到企微。

## 数据源说明
日线数据按 `data_source.daily_priority` 顺序自动切换，失败自动降级：

| 顺序 | 数据源 | 说明 |
|------|--------|------|
| 1 | tushare Pro | 配置 `TUSHARE_TOKEN` 后启用，最稳定（可选） |
| 2 | **腾讯直连** | `proxy.finance.qq.com` / `qt.gtimg.cn`，云端 IP 友好，日线主力来源 |
| 3 | 东财直连 | `push2his` K线接口在云端/云主机 IP 上会被拒连（RemoteDisconnected） |
| 4 | akshare | 先新浪日线（`stock_zh_a_daily`），再东财日线；财务指标走新浪关键指标 |

- 某数据源连续失败会自动**熔断 600 秒**（`SOURCE_COOLDOWN_SECONDS` 可调），避免每只股票都白等超时
- 实时行情（股票名称/快照）：腾讯直连 → 东财直连 → akshare
- 统一口径：`volume=股 / amount=元 / turnover=% / pct_chg=%`

### 数据源自检
在 Railway / GitHub Actions 等云端环境排查“取不到数据”时先跑：
```bash
python check_sources.py 000962 600738
```
输出各数据源可用性与耗时；只要「腾讯日线」或「akshare新浪日线」为 OK，工作流即可正常取数。

## 自定义扩展
- **新增/调整主线板块**：修改 `universe_updater.py` 的 `INDUSTRY_MAPPING`（行业节点 + 关键词 + 权重），
  `sector_whitelist` 由脚本自动生成，不建议手工编辑标的
- **调整参数**：修改 `selector`、`push_rules`、`frequency_control`
- **新增触发器**：在 `TriggerEngine` 中添加 `check_xxx` 方法
- **调整轮换节奏**：修改 `.github/workflows/universe_rebalance.yml` 的 cron（当前每周六 08:30 北京时间）

## 注意事项
- akshare 请求频率限制：建议单次运行间隔 ≥30 秒
- 企微应用消息频控：同一内容 30 分钟内不重复
- 仅监控 A 股主板/创业板，不含北交所/科创板（可扩展）
- 历史样本仅供复盘参考，不参与实时扫描

## 常见问题
**Q: 日志出现 `Remote end closed connection without response` / `所有数据源均不可用`？**
A: 东财 `push2his` K线接口对云端/机房 IP 会直接断连（akshare 日线同源东财，一并失败）。
已内置腾讯/新浪独立数据源自动降级，正常情况下日志会显示
`日线获取成功: tencent（60 根）`；若仍失败，先执行 `python check_sources.py` 定位出口网络。

**Q: 为什么没有推送？**
A: 两种典型原因：
1. **频控静默**（最常见）：`frequency_control.trading_hours_only=true` 且 `quiet_hours` 含 `15:00-09:15`，
   定时任务 15:30 触发时已收盘，所有消息会被丢弃（日志出现 `非交易时间/静默期，延迟推送`，任务本身却是 success）。
   解决：收盘后的扫描/日报命令加 `--force`（仓库内置 workflow 已默认开启）。
2. **评分未达标**：L4 默认阈值 `push_rules.L4_stock_pick.min_setup_score`（默认 70）。
   日志中的 `评分 TOP3（推送阈值 70）`、`扫描完成: 分析 N 只，达标 M 只` 可用于确认取数与评分链路是否正常；
   需要放宽时降低该阈值或在 `config/triggers.yaml` 中调整。

**Q: 为什么轮动池是周更而不是日更？**
A: 五阶段模型依赖 10-60 日的平台与缩量结构，日频换池会不断打断形态识别；周六盘前轮换既跟上资金主线切换，
又保证样本在周内连续。轮换报告（企微）会列出本期「新进池 / 移出池」名单。

## 免责声明
本系统仅供量化研究与监控辅助，**不构成投资建议**。实盘决策请结合基本面、资金面、风控体系综合判断。
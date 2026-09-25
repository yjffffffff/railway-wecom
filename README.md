# 冰雪/旅游/周期股触发推送系统

基于 Railway + 企微群机器人，实现「缩量下跌→底部平台→放量突破→回踩确认→退潮」五阶段形态的自动化监控与推送。

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

# 正式运行
python main.py --date 2026-09-24 --mode scan
```

### 4. Railway 部署
1. 将代码推送到 GitHub 仓库
2. Railway Dashboard → New Project → Deploy from GitHub
3. 选择仓库，Railway 自动检测 `railway.toml` 和 `Dockerfile`
4. 在 Variables 标签页添加环境变量（从 `.env` 复制）
5. 部署成功后，可在 Settings → Cron Jobs 添加定时任务：
   - Cron 表达式（UTC）：`30 7 * * 1-5`（北京时间周一至周五 15:30）
   - Command：`python main.py --mode scan`

### 5. 手动触发/补数
```bash
# Railway CLI
railway run python main.py --date 2026-09-24 --mode scan

# 或在 Dashboard 点击 Deploy → Deploy Latest
```

## 项目结构
```
railway-wecom/
├── main.py              # CLI 入口
├── config_loader.py     # 配置加载（YAML + ENV）
├── data_collector.py    # 数据采集（akshare/tushare）
├── stock_selector.py    # 选股引擎（五阶段评分）
├── push_engine.py       # 触发引擎 + 企微推送
├── config/
│   └── triggers.yaml    # 核心配置（板块/龙头/参数）
├── requirements.txt
├── Dockerfile
├── railway.toml
├── .env.example
└── README.md
```

## 数据源说明
- **主数据源**：akshare（免费、无需 Token、覆盖全市场日线/板块/两融）
- **备用数据源**：tushare Pro（需 Token，数据更标准）
- 自动缓存到 `data/` 目录，避免重复请求

## 自定义扩展
- **新增板块**：编辑 `config/triggers.yaml` 的 `sector_whitelist`
- **调整参数**：修改 `selector`、`push_rules`、`frequency_control`
- **新增触发器**：在 `TriggerEngine` 中添加 `check_xxx` 方法

## 注意事项
- akshare 请求频率限制：建议单次运行间隔 ≥30 秒
- 企微应用消息频控：同一内容 30 分钟内不重复
- 仅监控 A 股主板/创业板，不含北交所/科创板（可扩展）
- 历史样本仅供复盘参考，不参与实时扫描

## 免责声明
本系统仅供量化研究与监控辅助，**不构成投资建议**。实盘决策请结合基本面、资金面、风控体系综合判断。
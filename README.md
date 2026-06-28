# 理财助手 · licai

> 一个本地化的**个人理财助手**——把 A股 / 基金 / 银行理财 / 现金账户 / 数字资产 / 量化机器人 全部装进一个看板，再叠加市场 AI 问答、个股详情页（K线/盘口/分时）、板块对比、配置建议、早盘信息简报、资讯 AI 解读、解套档位等决策辅助。**只给客观信息，不给买卖建议。**

**没有云端**、**没有账号**、**数据全部跑在你自己的机器上**。SQLite 单文件存储，你随时可以拷走、删掉、备份。

![主看板](docs/screenshots/01-dashboard.png)

> 演示数据，运行 `python scripts/seed_demo.py --use` 一键重现。

## 为啥做这个

国内散户工具市场两极分化：
- 一头是券商 App / 银行 App，每家给你看自家的资产，**跨平台对账靠手算**
- 一头是 Excel + 雪球记账，**没有任何数据接入和决策辅助**

我自己的需求很简单：**把所有理财头寸装进一个屏幕，看清楚自己的钱在哪、配比合不合理、今天该不该动**。

## 🔍 问问市场 · 市场 AI 问答（独立页）

一个挂了 **28 个数据工具**的问答 agent，自由问个股涨跌 / 市场风格 / 资金主线 / 产业链 / 自己的成交，它自己决定调哪些工具取数据（并行调用），再客观解读——**只给客观信息，不给任何买卖建议**。流式打字机展示每一步在调什么工具，支持多轮追问（"它明天呢"会顺着上文标的继续）。

工具覆盖六个维度：

- **行情/走势** — 实时报价（含涨停/跌停/炸板/封板盘口判断）+ 近 N 日走势（A/港/美）。走势带**裸K量价**：每日开/收/高/低位置 + **量比** + **单根K线形态**（光头光脚阳/长上影十字…）+ 多周期摘要（5d/20d/60d 涨幅、距20日高、均线排列）
- **资金面** — 主力资金流（超大/大/中/小单净额 + 近几日趋势）、龙虎榜（游资/机构席位）、同行横向对比、筹码面（十大流通股东 + 北向增减 + 解禁抛压）、打板情绪、**板块动量矩阵（带量价配合判真上升趋势 vs 虚涨，趋势窗口 5/10/20 可调）**、热门概念榜、资金人气榜
- **基本面/公司画像** — 营收/净利及同比、ROE/毛利率/净利率、PE/PB/总市值（**A/港/美**）；**公司画像**（主营业务 + 细分行业 + 主营构成 + 控股/实控背景，判国资/央企/中科院系/民营）；**客观红线清单**（监管处罚/退市/业绩预亏/商誉减值/问询/减持/解禁抛压扫描）
- **消息面** — 个股新闻（A/港/美）、结构化公告、政策面 + 全球宏观快讯（东财 + 财联社 + 同花顺 + 金十）、Anthropic 联网搜索 + **read_url 读网页全文**（搜到的研报/原文读全再下结论，免 key）
- **我的持仓** — 成交流水（个股 + 场内ETF + 场外基金）、综合成本 / 已实现盈亏 / **持有天数 + 开仓日（带星期）**、做T识别、**买入逻辑复盘（thesis）**、**全资产配置问答**（现金/理财框架 + 现状分析），按日期区间筛
- **产业链全景** — 问"X 产业链上游到下游" → 联网拿各环节代表公司 → **批量量价一览表**（环节 / 标的 / 角色 / 均线 / 5d / 60d / 距20高 / 量能），一眼看清整条链强弱分布

几条贯穿的"读盘原则"（来自实战反馈，固化进 agent）：

- **裸K量价为主轴，资金流只作线索** — 当下拆单 + 多子账户操作让"超大单=主力"的分档失真，所以以价格行为 + 量能为准，资金净流入只当参考，背离时点明
- **可信度分级 + 可溯源** — 关键数字标 `[实测]`（本地工具）/ `[联网]`（搜到的，带来源时间）/ `[待核实]`（仅记忆）；联网结论正文带**可点角标**，点击直跳原文核实，底部列完整来源
- **分清"正在发生"和"背景脉络"** — 几个月前的旧政策不冒充"这两天在炒"，行情数据周末锚定到最近交易日（不说成"今日"）
- **严格不输出操作建议** — 描述"市场在奖励动量"可以，绝不说"所以你该追"；方向性进出永远留给你自己定

## 核心能力

### 1. 全资产看板（UnifiedPortfolio）

六大类一站式：
- **A股** — 实时行情，含手续费综合成本（券商 App 口径）
- **基金** — 场内 ETF（实时市价）+ 场外公募（官方净值，跟主流基金平台对齐）
- **理财** — 银行 T+30 锁定型，年化 + 起投日双向估算
- **现金** — T+0 货币基金 + 银行活期，单字段录余额，可选估月息
- **数字资产** — 交易所现货实时
- **机器人** — 交易所网格 / DCA 自动同步盈亏

附配套：
- 大类饼图 + 子分类小计（基金按"黄金/海外/A股宽基"等聚合）
- **集中度警告** + **同源风险检测**（A股有色 + 基金白银期货 = 同源）
- 加仓 4 模式：按股买 / 本金+净值 / 本金+份额 / 待确认（基金 T+1/T+2）

![加仓表单 - 按股买实时预览](docs/screenshots/05-add-lot-form.png)

![板块雷达 + 配置建议](docs/screenshots/02-sector-allocation.png)

### 2. 板块雷达

每只 A股 vs **同花顺行业板块**实时对比（90 个细粒度板块自动匹配）：

```
铜陵有色 → 工业金属 | 60 日: 你 -9% / 板块 -7% → α -2% (跑输)
格林美   → 其他电源设备 | 60 日: 你 -7.5% / 板块 -1.2% → α -6% (跑输)
```

### 3. 早盘 LLM 简报

每天 9:00 自动跑（也可手动触发）。基于每只持仓的近期新闻 + 公告 + K 线 + 基本面健康度，给一份**客观信息摘要 + 风险提示**——**不给任何操作建议**：
- `signal`：偏暖 / 中性 / 偏冷 / 警惕（只描述消息面倾向，不是买卖指令）
- 一句话点出今天最该知道的事 + 2-4 条客观要点（新闻/公告/基本面/技术位）
- 明确风险（业绩雷 / 监管 / 板块利空 / 股东减持 / 技术破位）单独标出
- 按个股真实行业拉对应板块新闻；飞书推送

### 4. 配置建议（AllocationAdvisor）

3 套预设模板（保守 / 平衡 / 激进），显示**当前 vs 目标**配比 + 该加多少 / 该减多少：

| 模板 | 现金 | 理财 | A股 | 基金 | 加密 |
|---|---|---|---|---|---|
| 保守 | 15% | 50% | 12% | 23% | 0% |
| 平衡 | 8% | 30% | 28% | 29% | 5% |
| 激进 | 5% | 12% | 38% | 35% | 10% |

### 5. 基金代理标的（场外基金盘中预判）

天天基金 NAV 是 T+1 公布的，盘中估值不准——所以拉**真实 top10 持仓股的实时涨跌**加权算预判：

```
易方达全球成长 QDII (012922) → top10 持仓加权 -2.42% (覆盖 52% 净值)
  美 TSM 台积电   -3.12% × 8.88%
  美 LITE         -7.92% × 8.68%
  深 300502 新易盛 +0.66% × 6.02%
  ...
```

A股 / 港股 / 美股个股实时报价全部走 Sina 免费接口，无需 API key。

![基金代理 tooltip](docs/screenshots/04-fund-proxy-tooltip.png)

### 6. 解套档位（UnwindView）

针对 A股 套牢仓位的金字塔加仓计划：
- 自动算每档触发价（按 ATR / 历史回撤）
- 健康度门槛（基本面变红时自动锁档）
- NPV 持有 vs 割肉对比（GBM 首达模型算回本概率）

![解套档位 + NPV 持有 vs 割肉](docs/screenshots/03-unwind-npv.png)

### 7. 风险提醒（客观警示，非操作信号）

- 单板块 > 50% / 70% 集中度警告
- 跨大类同源风险检测（A股有色 + 基金白银期货的隐藏共振）
- 基本面健康度红/黄灯
- 早盘简报里的风险项（业绩雷 / 监管 / 减持 / 技术破位）单独标出，飞书推送

> 注：早期的"实时价格条件单 / 解套提醒"模块已下线——实测下来，低吸/做T 类的提示性推送反复打脸，**客观看板 + 警示信息**才是真正有价值的部分，所以收敛到只做客观呈现、不做提示性信号。

### 8. 个股详情页（K线 + 盘口 + 分时）

任意 A股 / 场内 ETF 持仓点开看专业详情页：
- **多周期 K 线**（日/周/月）：蜡烛 + MA5/10/20 + 成本线 + 自己的买卖点（B/S 标记，精确到成交时刻）+ 可切换量 / MACD / KDJ 副图。K 线走**前复权**，除权/份额折算不再断崖
- **当日分时**：价 / 均价线 + 昨收基准 + 成交量按主动买卖着色（红买绿卖）+ 09:30 开盘点
- **五档盘口**（封单 / 内外盘，5s 刷新）+ **逐笔成交**（同价归并、大单高亮）
- 数据源可插拔（[通达信协议](https://github.com/SnowWarri0r/tdx-api)，不启用则自动回退）

成交记录还支持**精确到时分**录入、**每笔各记券商**（同一只票跨券商分别按各自费率算手续费）。

### 9. 资讯流 + AI 解读

- 全球宏观 / 地缘 / 央行实时快讯流，**重要**高亮、**关联我持仓**一键筛选
- 任意一条点开出三段式 AI 解读：**讲了啥 / 为何重要 / 跟你持仓什么关系**——带你的全部持仓上下文（A股 + 基金 + 数字资产 + 机器人），**只解读不荐买卖**

## 技术栈

**后端**：FastAPI + SQLite + akshare + Sina API + 东方财富 API + 同花顺 API + Claude API（OAuth，含 tool-calling agent + SSE 流式）

**前端**：React + Vite + Tailwind CSS + PWA（可装到桌面）

**数据源**（全部公开免费）：
- A股 行情：Sina `hq.sinajs.cn`
- A股 历史 K 线 + 行业：Sina money.finance + EastMoney emweb
- 基金 NAV：天天基金（fund.eastmoney.com）
- 基金持仓：天天基金 fundf10
- 港股个股：Sina hk
- 美股个股：Sina gb_
- 商品期货 / 海外指数：Sina nf_ / hf_
- 行业板块：同花顺（akshare 内置）
- 数字资产：交易所公开 ticker
- 问问市场 agent：东财 个股资金流(fflow/kline) / 龙虎榜 / F10 所属概念 / 财务摘要 / 港美股财务(em) / 板块成分 / 个股公告(np-anotice)
- 网页全文（read_url）：Firecrawl 免 key `/v1/scrape` 主源 + Jina Reader（r.jina.ai）免 key 备用，配额用完自动切换
- LLM：Claude API（OAuth via Claude Code 或 ANTHROPIC_API_KEY），个股问答 agent 走 tool-calling + 服务端联网搜索 + 网页全文抓取

## 快速启动

```bash
git clone https://github.com/<your-name>/licai
cd licai

# Python 后端
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 复制配置模板
cp config.example.py config.py
# 按需修改: commission_rate (你的券商佣金率) / patience_years / index_annual_return

# 前端构建
cd frontend && npm install && npm run build && cd ..

# 启动
python run.py
# 访问 http://localhost:8888
```

第一次启动会自动建空 SQLite (`portfolio.db`) 在项目根目录。

### 演示模式（不录数据先看效果）

```bash
python scripts/seed_demo.py --use   # 备份你的 DB + 写入演示数据 (4 只 A股 + 11 笔外部资产)
# 重启服务器, 看演示效果

python scripts/seed_demo.py --restore   # 看完恢复真实 DB
```

也可以 `--peek` 只生成 `portfolio.demo.db` 不动当前 DB。

### 可选：飞书通知

设置 → 飞书 Webhook → 粘贴 URL 保存。所有告警（档位触发 / 基本面恶化 / 早盘简报）会推送过去。

### 可选：交易所自动同步

设置 → 交易所 → 填 API Key + Secret + Passphrase（建议**只勾"读取"权限**）。机器人和现货持仓会自动同步。

### 可选：LLM 早盘简报

需要 Claude API 凭证。两种方式：
1. **OAuth**：装好 Claude Code（CLI）登录，会自动从 macOS Keychain 读 OAuth token
2. **API key**：`export ANTHROPIC_API_KEY=sk-ant-...`

不配也能用，只是早盘简报功能停用。

## 项目结构

```
licai/
├── api/                  # FastAPI 路由
│   ├── portfolio_routes  # A股 持仓 / 历史交易
│   ├── assets_routes     # 外部资产 (基金/理财/现金/加密/机器人)
│   ├── unwind_routes     # 解套档位
│   ├── briefing_routes   # 早盘简报
│   ├── sector_routes     # 板块雷达
│   ├── settings_routes   # 飞书 / 风控配置
│   ├── market_routes     # 市场指数 / 情绪 / 人气榜
│   ├── ask_routes        # 问问市场 agent 端点（SSE 流式 + 多轮）
│   └── ws.py             # WebSocket + 后台任务
├── services/
│   ├── stock_agent       # 问问市场 agent（28 个工具 + tool-calling loop + 裸K量价/产业链/来源角标/read_url）
│   ├── market_data       # 行情接口 (Sina/EM)
│   ├── external_assets   # 基金 + 数字资产 + 期货 + 港美股 quote
│   ├── fund_proxy        # 基金代理标的（top10 持仓加权）
│   ├── fund_holdings     # 天天基金 top10 抓取
│   ├── sector_compare    # 同花顺板块对比
│   ├── morning_briefing  # LLM 早盘简报
│   ├── fundamental_score # 基本面健康度（期货 + 新闻 + LLM）
│   ├── position_ledger   # 综合成本法（含手续费/印花税/过户费）
│   ├── exchange_client   # 数字资产平台私有 API（机器人/现货同步）
│   ├── feishu_notify     # 飞书 webhook
│   ├── llm_client        # Claude API (OAuth + API key 双模式)
│   └── news              # 新闻抓取
├── frontend/             # React + Vite
├── config.example.py     # 配置模板
├── database.py           # SQLite + schema
├── run.py                # FastAPI entry
└── requirements.txt
```

## 数据隐私

- **所有数据存本地 SQLite**，不上传任何云端
- 实时行情从公开接口拉，**不需要任何账号**
- 交易所 / LLM 凭证存数据库本地，飞书 webhook 也是
- `portfolio.db` 已在 `.gitignore` 里，不会被 commit
- 备份在 `backups/` 目录每天自动保留 30 天

## 已知限制

- **akshare** 依赖东方财富 API，部分接口（push2.eastmoney.com）会限流，已对这种情况做了 fallback（同花顺 + 硬编码 ETF 兜底）
- **交易所 DCA 端点** 文档标"读取"但只读 Key 返 50120（已反馈客服）
- **LibreSSL 老版本** macOS 系统 Python 3.9 用 LibreSSL 2.8.3，跟某些 EM 接口 TLS 握手不稳，已用 subprocess curl 兜底
- 跑在国内非代理环境，海外接口（Claude API / 交易所）需要自行处理网络

## License

[GNU AGPL-3.0](./LICENSE)

为啥选 AGPL：这是个**完整应用**而不是库，AGPL 防止有人 fork 后包成 SaaS 卖钱不开源回馈。你 self-host 用 / 个人 fork 改造完全没限制。

## Contributing

欢迎 Issue / PR。因为是个人理财工具，特别欢迎：
- 你自己用着不爽的细节体验
- 新数据源接入（券商对账单导入 / 银行 OCR / 雪球同步）
- 投资组合分析新指标（夏普比率 / 最大回撤 / VaR）
- 国际化（目前只有中文 + A股/港股/美股；如果要做欧洲市场 PR welcome）

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=SnowWarri0r/licai&type=Date)](https://star-history.com/#SnowWarri0r/licai&Date)

# 前端功能板块重排(信息架构)设计

日期:2026-09-08

## 背景与问题

导航是 8 项扁平结构(持仓 / 板块 / 榜单 / 宏观 / 资讯 / 复盘 / 问问市场 / 设置)。实测现状:

- **「板块」挂 9 张卡,只有 4 张真是板块**。板块成交份额、板块趋势矩阵、板块动量算板块;板块雷达其实是"持仓 vs 行业 ETF";剩下的早盘简报、市场情绪温度计、ETF 题材透视、资金热度榜(本质是榜单)、机构增仓+热门题材都不属于板块。
- **「复盘」挂 6 张卡,只有 1 张真是复盘**。AI 复盘算复盘;跑赢基准对照是持仓绩效、月度现金流是账户、配置建议是持仓配置、A 股行业缺口是板块、大盘·股池是市场数据。
- **「持仓」只挂 1 个组件**(UnifiedPortfolio),它的天然伙伴(现金流 / 跑赢基准 / 配置建议 / 板块雷达)散落在别的页。
- **榜单分散三处**:榜单页 + 板块页的资金热度榜 + 复盘页的大盘·股池。
- **11 个死组件**无人引用。

根因:卡片按"什么时候加的"落位,不按"它是什么"落位,于是「板块」和「复盘」退化成两个杂物抽屉。

## 目标

1. 顶层按 **「我的钱 / 看市场」** 分组——边界是"跟我的钱有关 vs 无关",最不容易再退化成杂物抽屉。
2. 每个子页单一职责,防止大杂烩复发。
3. 该合并的合并、该拆分的拆分、死代码删掉。

## 非目标(单列下一期)

纯代码体积的拆分,与信息架构无关,不在本次范围:

- `UnifiedPortfolio` 4263 行 / 内部 25 个组件(全项目最该拆)
- `Settings` 911 行 / 8 个子面板
- `StockKlineModal` 816 行、`ProKline` 660 行

理由:本次已改动导航、路由、11 个页面并删除 11 个文件;再叠加一个 4263 行的大拆分,出问题时无法定位是哪一层引入的。

## 一、导航结构

`Sidebar` 的 `NAV` 从扁平数组改为分组结构 `[{ group, items: [{key, label}] }]`,渲染分组标题 + 子项。

```
【我的】
  持仓            portfolio
  资金·现金流      cashflow      (新)
  绩效·基准        performance   (新)
  配置建议         allocation    (新)
  复盘            review
【市场】
  开盘·情绪        open          (新)
  板块            sector
  榜单            rankings
  资金·机构        capital       (新)
  宏观            macro
  资讯            news
——
  问问市场         ask
  设置            settings
```

新增 5 个 view key:`cashflow` `performance` `allocation` `open` `capital`。保留的 6 个(`portfolio` `sector` `rankings` `macro` `news` `review`)内容瘦身。

**侧栏折叠态**:当前折叠只保留图标、隐藏文字。分组标题在折叠态退化为一条分隔线(文字无处安放)。

## 二、页面 ↔ 卡片映射

### 【我的】

| 页 | 装什么 |
| --- | --- |
| 持仓 | `UnifiedPortfolio`(含交易历史 / 编辑弹窗) |
| 资金·现金流 | `Cashflow` |
| 绩效·基准 | `BenchmarkCompare` + `SectorRadar` + `PortfolioCurve` + `PortfolioCorrelation` |
| 配置建议 | `AllocationAdvisor` + `AShareSectorGap` + 我的 ETF 暴露(自 `EtfXray` 拆出) |
| 复盘 | `AITradeReview`(内含 `DailyReview` / `EodSummaryCard`) |

### 【市场】

| 页 | 装什么 |
| --- | --- |
| 开盘·情绪 | `MorningBriefing` + `SentimentThermometer`(含 `SentimentDetailModal`) |
| 板块 | `SectorShare` + `SectorMatrix` + `SectorOpportunities` + 题材 ETF(自 `EtfXray` 拆出) |
| 榜单 | `Rankings`(内含 `RotationBoard`)+ `HotRank` + `MarketPools`,合为页内 tab |
| 资金·机构 | `KplInstTheme`(机构增仓 + 本月热门题材) |
| 宏观 | `MacroDashboard` |
| 资讯 | `PortfolioNews` |

### 全局(不动)

`Dashboard` 概览条 + `RiskBanner` 风险条固定在内容区顶部,每页都显示。

## 三、合并

1. **榜单三合一**:榜单页当前是撑满全屏的面板(`h-full flex`),把热度榜/股池直接堆上去会破坏该布局。做法是**并入 `Rankings` 内部 tab**(个股榜 / 机构 / 热度榜 / 股池 / 轮动),保持全屏面板体验,且是真合并而非堆叠。
2. **绩效四合一**:跑赢基准 + 板块雷达 + 盈亏曲线 + 相关性,四者都是"你 vs 参照物"。
3. **配置三合一**:配置建议 + 行业缺口 + 我的 ETF 暴露,三者都回答"该怎么配"。

## 四、拆分

| 拆什么 | 怎么拆 | 为什么 |
| --- | --- | --- |
| `EtfXray` | 两个 tab 拆开:「我的 ETF 暴露」→【我的·配置建议】;「题材 ETF」→【市场·板块】 | 两个 tab 本就跨了两个大类,不拆必然有一边放错 |
| `AITradeReview` | 抽出 `PortfolioCurve` + `PortfolioCorrelation` → 【我的·绩效】 | 它们是持仓绩效,不是复盘 |
| `Rankings`(1120 行) | 抽出 `StockPanel`、`GroupPicker` 为独立文件 | 该页要新增两个 tab,先理清内部结构再加 |

## 五、删除死代码

无人引用的直接孤儿(9):`BudgetAllocator` `FundamentalMeter` `Portfolio` `PriceChart` `SmallMetalNews` `TradeJournal` `TradeReview` `UnwindCard` `UnwindView`

仅被上述死组件引用、传染性死亡(2):`NPVPanel` `TrancheLadder`(只被 `UnwindCard` 引用)

注意:`DailyReview` 同时被 `AITradeReview`(存活)和 `UnwindView`(死)引用,**保留**。

## 六、路由与向后兼容

view 走 `window.location.hash`(`App.jsx:44-49`:初始化时读 hash,`setView` 写回 hash)。

本次**没有删除任何旧 view key**(8 个全部保留:`portfolio` `sector` `rankings` `macro` `news` `review` `ask` `settings`),所以旧书签仍能解析到一个有效页面——只是 `sector` / `review` 的页内内容变少了(卡片迁走)。这是预期行为,不需要映射表。

合法 key 校验**已经存在**:`App.jsx:43` 定义了 `_VIEWS` 数组,第 44-48 行初始化时 `_VIEWS.includes(h) ? h : 'portfolio'`。所以手输错的 hash 本来就会兜底到持仓页,这条无需新建。

本次真正要动的是两处:

1. **`_VIEWS` 要补 5 个新 key**(`cashflow` `performance` `allocation` `open` `capital`)。它是硬编码数组,不补的话新页面无法通过 hash 直接打开(deep-link 失效)。
2. **`setView` 不做校验**(`App.jsx:49`:`_setView(v)` 后直接写 hash)。初始化那道校验只在页面加载时生效,运行时传入非法 key 会让内容区变空白——这正是 §七 缺陷的成因。给 `setView` 加同一套校验,非法值回落 `portfolio`。

## 七、顺带修复的缺陷

`App.jsx:184` 关闭设置时调用 `setView('dashboard')`,而 `dashboard` 不是任何一个 view key(只在 `Sidebar` 的 `ICONS` 里留了个同名图标)。结果:关掉设置得到一片空白内容区。改为回 `portfolio`。

## 八、验证方式

项目**前端没有任何测试**(无 vitest / jest,`package.json` 只有 dev/build/lint/preview)。因此验证靠构建 + 人工视觉核对:

1. `npm run build` 必须通过;`npm run lint` 不引入新错误。
2. **逐页浏览器截图**:11 个子页全部打开确认——卡片落在对的页、没有空页、没有重复渲染;侧栏展开态与折叠态各截一次。
3. **hash 回归**:访问 `#sector` `#review` 等保留的老 key,确认页面正常(内容变少是预期);访问 5 个新 key 的 hash(如 `#performance`)确认能直接打开(验证 `_VIEWS` 已补全);访问 `#nonsense` 确认兜底到持仓页;进设置再关闭,确认回到持仓页而非空白(验证 §七)。
4. **后端回归**:本次是纯前端改动,跑一遍后端测试(基线 665 passing)确认未被牵连。
5. 死代码删除后 `grep` 全量确认无残留引用。

## 风险

- 榜单合并要改 `Rankings` 的 tab 机制,该文件 1120 行。风险比预想低:tab 来自 hash query(`#rankings?t=inst`),`Rankings.jsx:297-303` 已对 `TABS` 做合法性校验并保留了旧值映射(`coiled`/`unbroken` → `structure`),非法值回落 `gainers`。新增 tab 只需往 `TABS` 加项,无存量数据迁移问题。
- 无自动化测试兜底,回归全靠截图核对,因此改动分步提交、每步可单独回滚。

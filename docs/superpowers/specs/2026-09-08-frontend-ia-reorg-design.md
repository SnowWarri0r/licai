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
  大盘·股池        pools         (新, 见 §九 修订)
  资金·机构        capital       (新)
  宏观            macro
  资讯            news
——
  问问市场         ask
  设置            settings
```

新增 6 个 view key:`cashflow` `performance` `allocation` `open` `capital` `pools`。保留的 6 个(`portfolio` `sector` `rankings` `macro` `news` `review`)内容瘦身。

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
| 榜单 | `Rankings`(内含 `RotationBoard`)+ 资金热度(自 `HotRank` 接入,作为**标准列表型 tab**) |
| 大盘·股池 | `MarketPools`(满宽复盘面板,不作为榜单 tab —— 见 §九 修订) |
| 资金·机构 | `KplInstTheme`(机构增仓 + 本月热门题材) |
| 宏观 | `MacroDashboard` |
| 资讯 | `PortfolioNews` |

### 全局(不动)

`Dashboard` 概览条 + `RiskBanner` 风险条固定在内容区顶部,每页都显示。

## 三、合并

1. **榜单合并(经 §九 修订)**:资金热度榜并入 `Rankings` 成为一个**标准列表型 tab**,与其余 8 个 tab 共用同一套页面骨架(概念条 / 板块筛选 / 查股 / 左列表 + 右 K 线)。`MarketPools`(大盘·股池)**不并入**,改为【市场】下的独立页。
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

## 九、修订(2026-09-08,首版实现上线后)

首版按"榜单三合一"把 `HotRank` 与 `MarketPools` 都做成了 `Rankings` 的页内 tab。上线后实拍三个 tab 对比,发现**切 tab 时整个页面骨架在跳**:

| | 列表型 8 个 | 资金热度(首版) | 股池(首版) |
| --- | --- | --- | --- |
| 页签行 | 挤在 420px 左栏内、**被截断** | 满宽 | 满宽 |
| 概念条 / 板块筛选 / 查股 / 刷新 | 有 | 无 | 无 |
| 布局 | 左列表 + 右 K 线两栏 | 单栏稀疏列表 + 大片空白 | 三列卡 + 连板梯队 + 历史分池表 |

两个缺陷:

1. **骨架不一致**:两个"整卡"tab 实质是另一个页面,只是共用了一条页签栏。
2. **页签行溢出**:8 个 tab 挤在 420px 左栏本已勉强,加到 10 个直接截断 —— 这是首版改动的直接副作用。

根因判断:**"榜单三合一"合并过头了。** `MarketPools` 不是一个"榜",它是"昨日涨停今天怎么样"的复盘面板(三列卡 + 连板梯队 + 历史分池回测),信息形态与列表型榜天生冲突。而 `HotRank` 是真正的榜,其接口 `/api/market/hot-rank` 返回 `items: [{rank, code, name, price, pct}]`,`pct` 键名与标准行渲染器已经一致,零成本即可同构。

修订方案:

- **页签行移出 420px 左栏,恒定满宽** —— 修掉截断,并消除"页签行在 420px 挤压态与满宽之间跳变"这一骨架跳动。
  ⚠️ 措辞更正:初稿写的是"位置/宽度在所有 tab 间恒定",**这个要求本身不成立** —— 概念条只在 `gainers`/`by_amount` 两个 tab 上渲染且位于页签行上方,故页签行的**垂直位置**在这两个 tab 与其余七个之间必然相差一个概念条的高度。恒定的是**宽度与所在层级**(始终是面板根的直接子元素、始终满宽),这才是消除骨架跳动的关键。实现按本节的结构图执行是正确的。
- **资金热度改为标准列表型 tab**:按仓库既有模式接入(自有 state + `load()` 里一条 fetch 分支 + 懒加载 entry + `rawList` 里一条映射),从而自动继承概念条、板块筛选、查股与右侧 K 线面板。
- **`MarketPools` 移出为【市场】下的独立页 `pools`**(满宽复盘面板)。

**连带简化**:上述两步之后 `CARD_TABS` 为空,首版为承载整卡 tab 而引入的 `isCard` 机制(含满宽左栏、隐藏筛选行/查股/StockPanel 的一系列条件分支、滚动包裹层)可整体删除。首版 review 中 parked 的"`list.map` 缺 `!isCard` 守卫"遗留项随之消失 —— 榜单页回到只有一种形态。

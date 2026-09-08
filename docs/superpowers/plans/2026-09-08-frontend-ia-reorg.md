# 前端功能板块重排(IA)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把前端 8 项扁平导航重排为「我的钱 / 看市场」两组共 11 个单一职责子页,合并同类卡片、拆开跨类组件、删除死代码。

**Architecture:** 侧栏 `NAV` 从扁平数组改为分组结构;`App.jsx` 增加 5 个 view key 与对应视图块,把卡片按语义迁位;三处合并(榜单并入 `Rankings` 页内 tab、绩效四合一、配置三合一);三处拆分(`EtfXray` 按 `mode` prop 拆两张卡、`AITradeReview` 抽出绩效卡、`Rankings` 抽出内部子组件)。

**Tech Stack:** React 19 + Vite(rolldown)+ Tailwind。构建 `npm run build`(产物进 `../static/`,由后端 :8888 提供)。

**Spec:** `docs/superpowers/specs/2026-09-08-frontend-ia-reorg-design.md`

## Global Constraints

- **前端没有任何测试框架**(`package.json` 只有 `dev` / `build` / `lint` / `preview`,无 vitest/jest)。每个任务的验证 = `npm run build` 通过 + 浏览器逐页视觉核对。不要为本次改动引入测试框架(spec 非目标)。
- 工作目录:`/Users/lovart/stock-trading-assistant`;前端根目录 `frontend/`。
- 构建后需**重启或重载**后端提供的静态产物才能在 :8888 看到变化(产物路径 `frontend/../static/`)。
- **每个任务单独提交,保证可单独回滚**(无自动化测试兜底)。
- 颜色约定:A 股红涨绿跌 —— `text-bear*` 是涨/正,`text-bull*` 是跌/负。不要按西方习惯反用。
- **非目标,不要碰**:`UnifiedPortfolio`(4263 行)、`Settings`(911 行)、`StockKlineModal`、`ProKline` 的体积拆分。
- `DailyReview` 必须保留(被存活的 `AITradeReview` 引用),尽管它也被要删的 `UnwindView` 引用。
- git 提交作者:`熊朝晖 <xiongzhaohui@liblib.ai>`。**不要 push**,只本地提交。

---

### Task 1: 删除 11 个死组件

**Files:**
- Delete: `frontend/src/components/BudgetAllocator.jsx`
- Delete: `frontend/src/components/FundamentalMeter.jsx`
- Delete: `frontend/src/components/Portfolio.jsx`
- Delete: `frontend/src/components/PriceChart.jsx`
- Delete: `frontend/src/components/SmallMetalNews.jsx`
- Delete: `frontend/src/components/TradeJournal.jsx`
- Delete: `frontend/src/components/TradeReview.jsx`
- Delete: `frontend/src/components/UnwindCard.jsx`
- Delete: `frontend/src/components/UnwindView.jsx`
- Delete: `frontend/src/components/NPVPanel.jsx`
- Delete: `frontend/src/components/TrancheLadder.jsx`

**Interfaces:**
- Consumes: 无
- Produces: 无(纯删除)。后续任务不得引用这些文件。

- [ ] **Step 1: 删除前再确认一次无人引用**

```bash
cd /Users/lovart/stock-trading-assistant/frontend/src
for n in BudgetAllocator FundamentalMeter Portfolio PriceChart SmallMetalNews TradeJournal TradeReview UnwindCard UnwindView NPVPanel TrancheLadder; do
  hits=$(grep -rn "from '\(\./\|\.\./\)*\(components/\)\?$n'" . 2>/dev/null | grep -v "^\./components/$n\.jsx:" | grep -vE "components/(UnwindCard|UnwindView)\.jsx:" | wc -l | tr -d ' ')
  printf "%-20s 外部引用=%s\n" "$n" "$hits"
done
```

预期:每一项 `外部引用=0`。(`NPVPanel`/`TrancheLadder` 只被同批删除的 `UnwindCard` 引用,已用 `grep -vE` 排除。)
若任何一项非 0,**停止**并报告——说明它其实还活着。

- [ ] **Step 2: 删除文件**

```bash
cd /Users/lovart/stock-trading-assistant/frontend/src/components
rm BudgetAllocator.jsx FundamentalMeter.jsx Portfolio.jsx PriceChart.jsx SmallMetalNews.jsx TradeJournal.jsx TradeReview.jsx UnwindCard.jsx UnwindView.jsx NPVPanel.jsx TrancheLadder.jsx
```

- [ ] **Step 3: 确认无残留引用**

```bash
cd /Users/lovart/stock-trading-assistant/frontend/src
grep -rnE "BudgetAllocator|FundamentalMeter|PriceChart|SmallMetalNews|TradeJournal|TradeReview|UnwindCard|UnwindView|NPVPanel|TrancheLadder" . 2>/dev/null
grep -rn "from '\./Portfolio'\|from './components/Portfolio'" . 2>/dev/null
```

预期:两条命令都无输出。
注意第二条单独查 `Portfolio` —— 因为 `PortfolioCurve` / `PortfolioNews` / `PortfolioCorrelation` / `UnifiedPortfolio` 都含 "Portfolio" 子串,不能用宽匹配。

- [ ] **Step 4: 构建验证**

```bash
cd /Users/lovart/stock-trading-assistant/frontend && npm run build
```

预期:`✓ built`,无 "Could not resolve" 报错。

- [ ] **Step 5: 提交**

```bash
cd /Users/lovart/stock-trading-assistant
git add -A frontend/src/components
git -c user.name="熊朝晖" -c user.email="xiongzhaohui@liblib.ai" commit -m "chore(frontend): 删除 11 个无人引用的死组件

直接孤儿 9 个 + 仅被 UnwindCard 引用的 NPVPanel/TrancheLadder。
DailyReview 保留(AITradeReview 仍在用)。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 修复 setView 无校验导致的空白页缺陷

**Files:**
- Modify: `frontend/src/App.jsx:43-49`(`_VIEWS` / `setView`)
- Modify: `frontend/src/App.jsx:184`(`Settings onClose`)

**Interfaces:**
- Consumes: 无
- Produces: `setView(v)` 对非法 key 回落 `'portfolio'`。Task 7 会往 `_VIEWS` 追加 5 个新 key,依赖这里把 `_VIEWS` 提到组件外作为模块级常量。

- [ ] **Step 1: 把 `_VIEWS` 提为模块级常量并给 setView 加校验**

当前代码(`App.jsx:43-49`):

```jsx
  const _VIEWS = ['portfolio', 'sector', 'rankings', 'macro', 'news', 'review', 'ask', 'settings']
  const [view, _setView] = useState(() => {
    // 支持 #view?k=v 形式的 deep-link(子参数由各组件自行读取)
    const h = (window.location.hash || '').slice(1).split('?')[0]
    return _VIEWS.includes(h) ? h : 'portfolio'
  })
  const setView = (v) => { _setView(v); try { window.location.hash = v } catch {} }
```

把 `_VIEWS` 移到 `export default function App()` **之外**(紧跟 import 段之后),并让 `setView` 复用同一套校验:

```jsx
// 合法 view key。setView 与 hash 初始化共用 —— 只在初始化校验的话,
// 运行时传入非法 key(历史上 setView('dashboard'))会让内容区渲染成空白。
const VIEWS = ['portfolio', 'sector', 'rankings', 'macro', 'news', 'review', 'ask', 'settings']
const normalizeView = (v) => (VIEWS.includes(v) ? v : 'portfolio')
```

组件内改为:

```jsx
  const [view, _setView] = useState(() => {
    // 支持 #view?k=v 形式的 deep-link(子参数由各组件自行读取)
    const h = (window.location.hash || '').slice(1).split('?')[0]
    return normalizeView(h)
  })
  const setView = (v) => {
    const next = normalizeView(v)
    _setView(next)
    try { window.location.hash = next } catch {}
  }
```

- [ ] **Step 2: 修掉 `dashboard` 这个错误调用**

`App.jsx:184` 当前:

```jsx
              <Settings onClose={() => setView('dashboard')} />
```

改为(`dashboard` 从来不是 view key,只在 Sidebar 的 ICONS 里留了个同名图标):

```jsx
              <Settings onClose={() => setView('portfolio')} />
```

- [ ] **Step 3: 构建验证**

```bash
cd /Users/lovart/stock-trading-assistant/frontend && npm run build
```

预期:`✓ built`。

- [ ] **Step 4: 浏览器验证兜底与缺陷修复**

重启后端以提供新产物,然后在浏览器验证三件事:

```bash
cd /Users/lovart/stock-trading-assistant
PID=$(lsof -nP -iTCP:8888 -sTCP:LISTEN -t | head -1); [ -n "$PID" ] && kill "$PID"; sleep 2
nohup venv/bin/python run.py > logs/backend_restart.log 2>&1 & sleep 7
```

1. 打开 `http://localhost:8888/#nonsense` → 应落到持仓页,内容区不空白。
2. 打开 `http://localhost:8888/#rankings` → 应正常显示榜单页(确认没把正常 key 也兜掉)。
3. 点右上「设置」进入,再点设置里的关闭 → 应回到**持仓页**,不是空白页。

- [ ] **Step 5: 提交**

```bash
cd /Users/lovart/stock-trading-assistant
git add frontend/src/App.jsx
git -c user.name="熊朝晖" -c user.email="xiongzhaohui@liblib.ai" commit -m "fix(frontend): setView 增加合法 key 校验, 修复关闭设置后空白页

hash 初始化本就有校验, 但 setView 运行时不校验;
Settings onClose 传了从不存在的 'dashboard', 导致关闭设置后内容区空白。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 从 Rankings 抽出 GroupPicker 与 StockPanel(纯重构,零行为变化)

**Files:**
- Create: `frontend/src/components/rankings/GroupPicker.jsx`(源自 `Rankings.jsx:50-131`)
- Create: `frontend/src/components/rankings/StockPanel.jsx`(源自 `Rankings.jsx:132-295`)
- Create: `frontend/src/components/rankings/shared.js`(`pctColor` / `boardOf`,源自 `Rankings.jsx:18-49`)
- Modify: `frontend/src/components/Rankings.jsx`(删掉被抽出的部分,改为 import)

**Interfaces:**
- Consumes: 无
- Produces:
  - `shared.js`: `export function pctColor(v): string`、`export function boardOf(code): string`
  - `GroupPicker.jsx`: `export default function GroupPicker({ groups, current, onSet, onClose, className })`
  - `StockPanel.jsx`: `export default function StockPanel({ stock, watched, onToggleWatch, groups, myGroups, onSetGroups })`
  - Task 4 在 `Rankings.jsx` 里新增 tab,依赖这三个文件已抽离、`Rankings.jsx` 变短。

- [ ] **Step 1: 先记录基线截图(重构不得改变外观)**

重启后端后打开 `http://localhost:8888/#rankings`,对「涨幅」和「机构」两个 tab 各截一张图留底,重构后要对比一致。

- [ ] **Step 2: 建 `rankings/shared.js`**

把 `Rankings.jsx` 第 18-25 行的 `pctColor` 和第 26-49 行的 `boardOf` **整段剪切**到新文件,唯一改动是各自前面加 `export` 关键字。函数体一个字符都不要改,既有注释(如"场内基金(1x/5x)不属于任何板块,单独标出 —— 588xxx 是科创板")一并带走。

文件顶部加一行说明:

```js
// 从 Rankings.jsx 抽出 —— 榜单相关的纯函数, 供 Rankings 与其子组件共用。
```

**不要凭记忆重写这两个函数** —— 它们含板块前缀判断的边界条件,重写极易出错。用编辑器剪切粘贴,然后回到 `Rankings.jsx` 确认那 32 行已消失。

- [ ] **Step 3: 建 `rankings/GroupPicker.jsx`**

把 `Rankings.jsx:50-131` 的 `GroupPicker` 原样搬入,改成 `export default`。它用到的 `useState` / `useEffect` 从 `react` import;若用到 `pctColor`/`boardOf` 则从 `./shared` import。

- [ ] **Step 4: 建 `rankings/StockPanel.jsx`**

把 `Rankings.jsx:132-295` 的 `StockPanel` 原样搬入,改成 `export default`。它依赖:
- `react` 的 `useState` / `useEffect`
- `../../hooks/useApi` 的 `fetchJSON` / `prefetchJSON`(注意目录深了一层,相对路径要多一个 `../`)
- `./GroupPicker`
- `./shared` 的 `pctColor` / `boardOf`(按实际使用情况 import)
- 原文件里 `StockPanel` 引用的其它组件(如问问弹窗),路径同样要加一层 `../`

- [ ] **Step 5: 改 `Rankings.jsx` 为 import**

删掉 `Rankings.jsx` 里第 18-295 行被抽走的三块,在文件头加:

```jsx
import GroupPicker from './rankings/GroupPicker'
import StockPanel from './rankings/StockPanel'
import { pctColor, boardOf } from './rankings/shared'
```

`Rankings.jsx` 应从 1120 行降到约 830 行。**不要动 `Rankings()` 内部任何逻辑。**

- [ ] **Step 6: 构建 + 外观回归**

```bash
cd /Users/lovart/stock-trading-assistant/frontend && npm run build
```

预期 `✓ built`。重启后端,打开 `#rankings`,对比 Step 1 的基线截图:
- 「涨幅」tab 列表、点开个股的右侧面板、分组下拉,三者外观与交互必须与基线一致。
- 「机构」tab 正常。
- 浏览器控制台无新报错(用 `read_console_messages` 检查)。

- [ ] **Step 7: 提交**

```bash
cd /Users/lovart/stock-trading-assistant
git add frontend/src/components/Rankings.jsx frontend/src/components/rankings
git -c user.name="熊朝晖" -c user.email="xiongzhaohui@liblib.ai" commit -m "refactor(frontend): Rankings 抽出 GroupPicker/StockPanel/shared

1120 行降到约 830 行, 为榜单页新增 tab 让路。纯搬移, 零行为变化。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 榜单三合一 —— 资金热度榜与大盘·股池并入 Rankings 页内 tab

**Files:**
- Modify: `frontend/src/components/Rankings.jsx`(`TABS` 加两项;渲染分支加两个 tab)
- Modify: `frontend/src/App.jsx`(从 `sector` 视图移除 `<HotRank />`,从 `review` 视图移除 `<MarketPools />`,并删掉这两个 import)

**Interfaces:**
- Consumes: Task 3 抽离后的 `Rankings.jsx`
- Produces: `Rankings` 的 `TABS` 含 `hotrank` / `pools` 两个 key,可用 `#rankings?t=hotrank` deep-link 直达。Task 7 迁页时 `sector` / `review` 已不含这两张卡。

- [ ] **Step 1: `TABS` 追加两项**

`Rankings.jsx:7-16` 当前 6 项,在末尾追加:

```jsx
const TABS = [
  { key: 'gainers', label: '涨幅' },
  { key: 'by_amount', label: '成交额' },
  { key: 'lhb', label: '龙虎榜' },
  { key: 'structure', label: '蓄势/强势' },
  { key: 'inst', label: '机构' },
  { key: 'earnings', label: '业绩' },
  { key: 'hotrank', label: '资金热度' },
  { key: 'pools', label: '股池' },
]
```

tab 初始化(`Rankings.jsx:297-303`)已用 `TABS.some(x => x.key === t)` 校验,新 key 自动生效,**无需改动那段**。

- [ ] **Step 2: 在渲染里加两个 tab 分支**

`HotRank` 与 `MarketPools` 都是自取数的独立卡片(各自 `useEffect` 里 `fetchJSON`,无需外部 props),直接挂上即可。在 `Rankings()` 的 tab 内容渲染处,与其它 tab 分支并列加入:

```jsx
{tab === 'hotrank' && (
  <div className="overflow-y-auto h-full"><HotRank /></div>
)}
{tab === 'pools' && (
  <div className="overflow-y-auto h-full"><MarketPools /></div>
)}
```

在 `Rankings.jsx` 文件头加 import:

```jsx
import HotRank from './HotRank'
import MarketPools from './MarketPools'
```

**注意**:榜单页外壳是撑满全屏的(`App.jsx` 里 `view === 'rankings'` 用 `h-full flex flex-col`,`Rankings` 自己吃掉剩余高度)。这两张卡是普通文档流卡片,所以必须包一层 `overflow-y-auto h-full` 才不会溢出被裁掉。

- [ ] **Step 3: 从 App.jsx 摘掉这两张卡**

删除 `sector` 视图里的 `<HotRank />` 一行、`review` 视图里的 `<MarketPools />` 一行,并删掉文件头对应的两条 import:

```jsx
import HotRank from './components/HotRank'      // 删除
import MarketPools from './components/MarketPools'  // 删除
```

- [ ] **Step 4: 构建**

```bash
cd /Users/lovart/stock-trading-assistant/frontend && npm run build
```

预期 `✓ built`。若报 "HotRank is not defined",说明 App.jsx 里还有残留使用。

- [ ] **Step 5: 浏览器验证**

重启后端,验证:
1. `#rankings?t=hotrank` → 资金热度榜正常显示且可滚动,不被裁切。
2. `#rankings?t=pools` → 大盘·股池正常显示。
3. `#rankings` → 默认仍是「涨幅」tab。
4. `#sector` → 已**没有**资金热度榜。
5. `#review` → 已**没有**大盘·股池。
6. 控制台无报错。

- [ ] **Step 6: 提交**

```bash
cd /Users/lovart/stock-trading-assistant
git add frontend/src/components/Rankings.jsx frontend/src/App.jsx
git -c user.name="熊朝晖" -c user.email="xiongzhaohui@liblib.ai" commit -m "feat(frontend): 榜单三合一 — 资金热度榜/股池并入榜单页 tab

榜原先散在三处(榜单页 + 板块页热度榜 + 复盘页股池), 现统一为榜单页 tab。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 拆分 EtfXray —— 按 mode 拆成「我的 ETF 暴露」与「题材 ETF」两张卡

**Files:**
- Modify: `frontend/src/components/EtfXray.jsx`(加 `mode` prop,按 mode 只渲染一半)

**Interfaces:**
- Consumes: 无
- Produces: `EtfXray` 接受 `mode` prop:`<EtfXray mode="mine" />` 渲染「我的 ETF 暴露」(无 tab 切换),`<EtfXray mode="theme" />` 渲染「题材 ETF」(输入框 + 快捷词)。Task 7 分别挂到【我的·配置建议】和【市场·板块】。

**为什么用 prop 而不是拆成两个文件:** 两个 tab 共用同一套取数(`load`)、错误态(`err`)、加载态和结果渲染器。拆成两个文件会把这套骨架复制一遍。用 `mode` 收敛成一个组件、两个入口,是真拆(两张卡落在两个页)且不重复代码。

- [ ] **Step 1: 改签名并按 mode 固定 tab**

`EtfXray.jsx:69-70` 当前:

```jsx
export default function EtfXray() {
  const [tab, setTab] = useState('mine')      // mine | theme
```

改为:

```jsx
// mode: 'mine' = 我的ETF暴露(归【我的·配置建议】) | 'theme' = 题材ETF查询(归【市场·板块】)
// 两种模式共用取数与渲染骨架, 只是入口和默认视角不同。
export default function EtfXray({ mode = 'mine' }) {
  const tab = mode
```

删掉 `setTab` 这个 state(不再有页内切换)。随之要处理原先调用 `setTab` 的地方:
- `goTheme`(`EtfXray.jsx:91-95`)里的 `setTab('theme')` 删掉,只保留 `setTheme(t); setInput(t); load('theme', t)`。
- 头部两个 tab 按钮(`EtfXray.jsx:102-111` 那个 `<div className="flex gap-1 ml-auto">` 整块)删掉。

- [ ] **Step 2: 标题按 mode 区分**

`EtfXray.jsx:100-101` 当前是固定标题。改为:

```jsx
        <h3 className="text-[14px] font-semibold text-text-bright m-0">
          {mode === 'mine' ? '我的 ETF 暴露' : '题材 ETF 透视'}
        </h3>
        <span className="text-[10.5px] text-text-muted">
          {mode === 'mine' ? '我持有的 ETF 真实成分 vs 名称主题' : '季报真实成分 vs 名称主题 · 避雷挂羊头'}
        </span>
```

- [ ] **Step 3: `theme` 模式首次进入要自动出内容**

原来 `useEffect`(`EtfXray.jsx:89`)只在 `tab === 'mine'` 时取数,`theme` 靠用户输入触发。现在 `theme` 是独立卡片,空着不好看。改为:

```jsx
  useEffect(() => {
    if (mode === 'mine') load('mine')
    else if (QUICK.length) goTheme(QUICK[0])
  }, [mode, load])   // goTheme 稳定, 首屏只跑一次
```

若 lint 抱怨 `goTheme` 未列入依赖,在该行上方加 `// eslint-disable-next-line react-hooks/exhaustive-deps`(与仓库既有做法一致;不要为此重构成 useCallback 链)。

- [ ] **Step 4: 临时双挂验证等价性**

暂时把 `App.jsx` 的 `sector` 视图里原来的 `<EtfXray />` 换成两行:

```jsx
              <EtfXray mode="mine" />
              <EtfXray mode="theme" />
```

(Task 7 会把它们分到两个页;这一步只为验证拆分本身没坏。)

- [ ] **Step 5: 构建 + 验证**

```bash
cd /Users/lovart/stock-trading-assistant/frontend && npm run build
```

重启后端,打开 `#sector`,验证:
1. 出现**两张**卡:「我的 ETF 暴露」和「题材 ETF 透视」。
2. 「我的 ETF 暴露」自动加载我的 ETF,内容与拆分前 `mine` tab 一致。
3. 「题材 ETF 透视」首屏自动展示第一个快捷词的结果;输入别的主题词回车能查。
4. 两张卡都**没有**顶部 tab 切换按钮了。
5. 控制台无报错。

- [ ] **Step 6: 提交**

```bash
cd /Users/lovart/stock-trading-assistant
git add frontend/src/components/EtfXray.jsx frontend/src/App.jsx
git -c user.name="熊朝晖" -c user.email="xiongzhaohui@liblib.ai" commit -m "refactor(frontend): EtfXray 按 mode 拆成我的ETF暴露/题材ETF两张卡

原组件两个 tab 分属"我的"和"市场"两个大类, 不拆必然放错一边。
共用取数骨架, 用 mode prop 收敛, 不复制代码。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 从 AITradeReview 抽出盈亏曲线与相关性

**Files:**
- Modify: `frontend/src/components/AITradeReview.jsx`(删两条 import 与两行渲染)
- Modify: `frontend/src/App.jsx`(在 `review` 视图临时挂上这两张卡)

**Interfaces:**
- Consumes: 无
- Produces: `PortfolioCurve` 与 `PortfolioCorrelation` 成为可独立挂载的顶层卡片(两者都无 props)。Task 7 把它们移到【我的·绩效】。

- [ ] **Step 1: 从 AITradeReview 摘除**

`AITradeReview.jsx:5-6` 删掉:

```jsx
import PortfolioCurve from './PortfolioCurve'          // 删除
import PortfolioCorrelation from './PortfolioCorrelation'  // 删除
```

`AITradeReview.jsx:66-67` 删掉:

```jsx
          <PortfolioCurve />          // 删除
          <PortfolioCorrelation />    // 删除
```

若这两行外层有个仅用于包裹它们的容器 `<div>`(检查 65 与 68 行),把空掉的容器一并删除;若容器还包着别的内容,只删这两行。

- [ ] **Step 2: 临时挂到 review 视图**

`App.jsx` 的 `review` 视图里,`<AITradeReview />` **之后**加两行,并在文件头加 import:

```jsx
import PortfolioCurve from './components/PortfolioCurve'
import PortfolioCorrelation from './components/PortfolioCorrelation'
```

```jsx
              <AITradeReview />
              <PortfolioCurve />
              <PortfolioCorrelation />
```

- [ ] **Step 3: 构建 + 验证**

```bash
cd /Users/lovart/stock-trading-assistant/frontend && npm run build
```

重启后端,打开 `#review`,验证:
1. 盈亏曲线、相关性两张卡**仍然显示**且数据正常(只是位置从 AI 复盘内部挪到了它下面)。
2. AI 复盘本体(含 DailyReview / 收盘摘要)不受影响。
3. 控制台无报错。

- [ ] **Step 4: 提交**

```bash
cd /Users/lovart/stock-trading-assistant
git add frontend/src/components/AITradeReview.jsx frontend/src/App.jsx
git -c user.name="熊朝晖" -c user.email="xiongzhaohui@liblib.ai" commit -m "refactor(frontend): 盈亏曲线/相关性 从 AITradeReview 内部抽为顶层卡片

它们是持仓绩效, 不是复盘内容, 为迁入【我的·绩效】让路。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 分组导航 + 5 个新页 + 卡片全迁位(核心)

**Files:**
- Modify: `frontend/src/components/Sidebar.jsx`(`NAV` 改分组结构、渲染分组标题、补 5 个 ICONS)
- Modify: `frontend/src/App.jsx`(`VIEWS` 补 5 个 key、新增 5 个视图块、既有 3 个视图瘦身)

**Interfaces:**
- Consumes: Task 2 的模块级 `VIEWS` / `normalizeView`;Task 5 的 `EtfXray mode` prop;Task 6 抽出的两张绩效卡
- Produces: 11 个子页全部可达。本任务是最后一个功能任务。

- [ ] **Step 1: `Sidebar.jsx` 的 NAV 改分组结构**

替换 `Sidebar.jsx:13-22` 的扁平 `NAV`:

```jsx
// 顶层按「我的钱 / 看市场」分组 —— 边界是"跟我的钱有关 vs 无关",
// 这条界最硬, 不容易再退化成杂物抽屉。group 为 null 的是不归属两大类的独立项。
const NAV = [
  { group: '我的', items: [
    { key: 'portfolio',   label: '持仓' },
    { key: 'cashflow',    label: '资金·现金流' },
    { key: 'performance', label: '绩效·基准' },
    { key: 'allocation',  label: '配置建议' },
    { key: 'review',      label: '复盘' },
  ] },
  { group: '市场', items: [
    { key: 'open',     label: '开盘·情绪' },
    { key: 'sector',   label: '板块' },
    { key: 'rankings', label: '榜单' },
    { key: 'capital',  label: '资金·机构' },
    { key: 'macro',    label: '宏观' },
    { key: 'news',     label: '资讯' },
  ] },
  { group: null, items: [
    { key: 'ask',      label: '问问市场' },
    { key: 'settings', label: '设置' },
  ] },
]
```

- [ ] **Step 2: 补 5 个新 key 的图标**

在 `Sidebar.jsx` 的 `ICONS` 对象里追加(顺手删掉从不使用的 `dashboard` 图标):

```jsx
  cashflow: <><path d="M3 7h18v10H3z" /><circle cx="12" cy="12" r="2.5" /><path d="M7 12h.01M17 12h.01" /></>,
  performance: <><path d="M4 19h16" /><path d="M6 16V9M11 16V5M16 16v-4" /></>,
  allocation: <><circle cx="12" cy="12" r="8" /><path d="M12 4v8l7 3" /></>,
  open: <><circle cx="12" cy="12" r="8" /><path d="M12 8v4l3 2" /><path d="M12 2v2M22 12h-2" /></>,
  capital: <><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>,
```

- [ ] **Step 3: 渲染分组标题(含折叠态退化)**

替换 `Sidebar.jsx:35-49` 的 `<nav>`:

```jsx
      <nav className="flex-1 py-2 overflow-y-auto">
        {NAV.map((sec, si) => (
          <div key={sec.group || `misc-${si}`}>
            {/* 展开时显示分组标题; 折叠时只剩图标, 文字无处安放 → 退化成一条分隔线 */}
            {sec.group
              ? (open
                  ? <div className="px-4 pt-3 pb-1 text-[10px] tracking-wider text-text-muted">{sec.group}</div>
                  : <div className="mx-3 my-2 border-t border-border-subtle" />)
              : <div className="mx-3 my-2 border-t border-border-subtle" />}
            {sec.items.map(n => {
              const on = active === n.key
              return (
                <button key={n.key} onClick={() => onNav(n.key)} title={n.label}
                  className={`w-full flex items-center gap-3 px-4 h-11 text-left transition-colors
                    ${on ? 'text-accent bg-accent/12 border-r-2 border-accent' : 'text-text-dim hover:text-text hover:bg-surface-3/50 border-r-2 border-transparent'}`}>
                  <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                    {ICONS[n.key]}
                  </svg>
                  {open && <span className="text-[13px] font-medium">{n.label}</span>}
                </button>
              )
            })}
          </div>
        ))}
      </nav>
```

侧栏宽度 `w-44`(展开)对「资金·现金流」「绩效·基准」这类 5-6 字标签够用;若实测折行,把 `w-44` 调为 `w-48`。

- [ ] **Step 4: `App.jsx` 的 VIEWS 补 5 个 key**

Task 2 建立的模块级常量,追加新 key(顺序与侧栏一致,便于对读):

```jsx
const VIEWS = [
  'portfolio', 'cashflow', 'performance', 'allocation', 'review',
  'open', 'sector', 'rankings', 'capital', 'macro', 'news',
  'ask', 'settings',
]
```

- [ ] **Step 5: 重排 App.jsx 的视图块**

按 spec 的映射重写各视图块。`portfolio` / `rankings` / `macro` / `news` / `ask` / `settings` 保持原样不动;改动如下。

`review` 视图**瘦身**为只剩复盘(Task 6 临时挂的两张卡从这里移走):

```jsx
          {view === 'review' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <AITradeReview />
            </div>
          )}
```

`sector` 视图**瘦身**为只剩真板块 + 题材 ETF:

```jsx
          {view === 'sector' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <SectorShare />
              <SectorMatrix />
              <SectorOpportunities />
              <EtfXray mode="theme" />
            </div>
          )}
```

**新增** 5 个视图块(放在 `sector` 块之后即可,顺序不影响行为):

```jsx
          {view === 'cashflow' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <Cashflow />
            </div>
          )}

          {view === 'performance' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <BenchmarkCompare />
              <SectorRadar />
              <PortfolioCurve />
              <PortfolioCorrelation />
            </div>
          )}

          {view === 'allocation' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <AllocationAdvisor />
              <AShareSectorGap />
              <EtfXray mode="mine" />
            </div>
          )}

          {view === 'open' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <MorningBriefing />
              <SentimentThermometer />
            </div>
          )}

          {view === 'capital' && (
            <div className={`${PAD} space-y-3 md:space-y-4`}>
              <KplInstTheme />
            </div>
          )}
```

- [ ] **Step 6: 构建**

```bash
cd /Users/lovart/stock-trading-assistant/frontend && npm run build
```

预期 `✓ built`。常见错误:某个组件的 import 被上一步误删 → "X is not defined";把 import 段与实际使用对齐即可(`MorningBriefing` `SentimentThermometer` `SectorShare` `SectorMatrix` `SectorOpportunities` `SectorRadar` `KplInstTheme` `EtfXray` `BenchmarkCompare` `Cashflow` `AllocationAdvisor` `AShareSectorGap` `PortfolioCurve` `PortfolioCorrelation` `AITradeReview` 全部仍需保留)。

- [ ] **Step 7: 逐页浏览器验证**

重启后端后,依次打开 11 个页并确认卡片正确、无空页、无重复:

| hash | 应看到 |
| --- | --- |
| `#portfolio` | 持仓总览 |
| `#cashflow` | 月度现金流 |
| `#performance` | 跑赢基准 + 板块雷达 + 盈亏曲线 + 相关性 |
| `#allocation` | 配置建议 + A股行业缺口 + 我的 ETF 暴露 |
| `#review` | 只有 AI 复盘 |
| `#open` | 早盘简报 + 情绪温度计 |
| `#sector` | 板块成交份额 + 趋势矩阵 + 板块动量 + 题材 ETF |
| `#rankings` | 榜单(含资金热度/股池 tab) |
| `#capital` | 机构增仓 + 本月热门题材 |
| `#macro` | 宏观面板 |
| `#news` | 资讯 |

同时确认:侧栏出现【我的】【市场】两个分组标题;点「收起」后标题变成分隔线、图标仍可点;控制台无报错。

- [ ] **Step 8: 提交**

```bash
cd /Users/lovart/stock-trading-assistant
git add frontend/src/App.jsx frontend/src/components/Sidebar.jsx
git -c user.name="熊朝晖" -c user.email="xiongzhaohui@liblib.ai" commit -m "feat(frontend): 导航按「我的钱/看市场」分组, 卡片按语义迁位

新增 cashflow/performance/allocation/open/capital 五页;
板块页从 9 张卡瘦身到 4 张真板块, 复盘页只留 AI 复盘;
绩效四合一、配置三合一。侧栏折叠态分组标题退化为分隔线。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: 全量验收

**Files:** 无改动(纯验证)

**Interfaces:**
- Consumes: Task 1-7 全部产出
- Produces: 验收结论

- [ ] **Step 1: 构建与 lint**

```bash
cd /Users/lovart/stock-trading-assistant/frontend
npm run build
npm run lint 2>&1 | tail -20
```

预期:build 通过;lint 不出现**新增**错误(与改动前对比,既有告警不算回归)。

- [ ] **Step 2: 后端回归(确认纯前端改动没牵连)**

```bash
cd /Users/lovart/stock-trading-assistant && venv/bin/python -m pytest -q 2>&1 | tail -3
```

预期:`665 passed`(基线)。

- [ ] **Step 3: hash 与缺陷回归**

浏览器验证:
1. `#performance`、`#capital` 等 5 个新 key 能直接打开(证明 `VIEWS` 补全生效)。
2. `#nonsense` → 兜底到持仓页。
3. 进设置再关闭 → 回持仓页,不空白。
4. `#rankings?t=hotrank` → 直达资金热度 tab。

- [ ] **Step 4: 死代码残留扫描**

```bash
cd /Users/lovart/stock-trading-assistant/frontend/src
grep -rnE "BudgetAllocator|FundamentalMeter|PriceChart|SmallMetalNews|TradeJournal|TradeReview|UnwindCard|UnwindView|NPVPanel|TrancheLadder" . 2>/dev/null
grep -rn "dashboard" App.jsx components/Sidebar.jsx 2>/dev/null
```

预期:第一条无输出;第二条无输出(`dashboard` 图标已在 Task 7 删除)。

- [ ] **Step 5: 报告结论**

汇总:11 个页的截图核对结果、build/lint/后端测试状态、以及任何遗留问题。若发现问题,不要在本任务里顺手改——记录下来单独开任务。

---

## 附:执行顺序与回滚

任务 1→8 顺序执行。每个任务一次提交,任一任务出问题可 `git revert` 单个提交而不影响其它。

Task 5 与 Task 6 会产生"临时双挂/临时挂载"的中间态(卡片暂时留在旧页),这是有意的——保证每个提交点应用都是可用的,由 Task 7 收口到最终位置。

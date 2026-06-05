# 券商费率配置（Broker Fee Profiles）— 设计

## 目标

手续费现在写死「万x + 最低 ¥5」。用户有多个券商（招商、银河），费率不同，且
A 股股票和 ETF 费率也不同。做成**可自定义的券商档案**，每个持仓挂一个券商，
手续费预填 + A 股成本计算都按该券商的对应费率算。预置招商、银河两条，其他用户可自配。

## 范围

**做：**
- 后端券商档案 CRUD（含预置招商 + 银河）。
- A 股 holdings + 场内 ETF 加 `broker` 字段；建仓/加仓/编辑表单加券商下拉。
- 手续费预填按「券商 + 股票/ETF」算 `max(金额×费率, 最低)`。
- A 股成本计算（`position_ledger`）佣金改用该持仓券商的费率（印花/过户/规费不变）。
- Settings 加「券商费率」管理区。

**不做（YAGNI）：**
- 场外基金券商（无佣金，走申购费率，不动）。
- 现金/理财/加密（不涉及券商佣金）。
- 历史已成交 `fee` 的批量改写（只影响新预填 + 成本按券商重算）。

## 数据模型

### 券商档案（后端，新表 `brokers`）

放后端而非 localStorage —— A 股成本计算在服务端要用费率。

```
brokers(
  id INTEGER PK,
  name TEXT UNIQUE,
  stock_rate REAL,   -- 股票佣金费率 (小数, 万1.854 = 0.0001854)
  stock_min REAL,    -- 股票每笔最低 (元)
  etf_rate REAL,     -- 场内 ETF 佣金费率
  etf_min REAL,      -- 场内 ETF 每笔最低
  is_default INTEGER DEFAULT 0
)
```

**预置（首次迁移时 seed，仅当表空）：**
- 招商证券：`stock_rate 0.0001854, stock_min 5, etf_rate 0.0001854, etf_min 5, is_default 1`
- 银河证券：`stock_rate 0.000086, stock_min 5, etf_rate 0.00005, etf_min 0.1, is_default 0`

约束：始终恰好一个 `is_default=1`；**默认券商禁止删除**（要删先把别的设为默认）。
设某券商为默认时，原默认自动取消。

### 持仓挂券商

- `holdings` 加列 `broker TEXT`（券商 name，NULL = 用默认券商）。
- `external_assets` 加列 `broker TEXT`（仅场内 ETF 用；场外基金 NULL）。

迁移：两列都 `ADD COLUMN ... TEXT`，老数据为 NULL → 走默认券商（= 招商）→ 行为不变。

## 费率应用

### 解析「某持仓用哪套费率」

辅助函数（后端 + 前端各一份，逻辑一致）：
```
resolveFee(broker_name | null, kind):   # kind ∈ {'stock','etf'}
  b = brokers[broker_name] or default_broker
  return (b.stock_rate, b.stock_min) if kind=='stock' else (b.etf_rate, b.etf_min)
```
kind 判定：A 股 holding → 'stock'；场内 ETF（`isOnchainEtf(code)`）→ 'etf'。

### 前端预填

`/api/brokers` 拉全量配置缓存到前端。建仓/加仓/编辑表单：
- 手续费默认值 = `max(amount × rate, min)`，rate/min 来自 `resolveFee(持仓券商, kind)`。
- 表单加「券商」下拉（A股 + 场内ETF 显示；选项来自配置；默认选默认券商）。
- 用户仍可手改手续费（覆盖预填）。

### 后端 A 股成本

`services/position_ledger.py`：
- `estimate_trade_fee(action_type, price, shares, stock_code, commission_rate=DEFAULT, commission_min=DEFAULT)`
  —— 佣金那项改用传入的 `commission_rate/min`；印花税/过户费/规费费率不变。
- `compute_position_state(actions, today, stock_code, commission_rate=None, commission_min=None)`
  —— 把佣金费率透传给 `estimate_trade_fee`；未传时用模块默认（= 招商，向后兼容）。
- 调用方 `api/portfolio_routes.py`：对每只持仓，按 `holding.broker` 解析出股票佣金费率/最低，传进 `compute_position_state`。

## API

```
GET    /api/brokers            → [{id,name,stock_rate,stock_min,etf_rate,etf_min,is_default}]
POST   /api/brokers            → 新建 {name,stock_rate,stock_min,etf_rate,etf_min}
PUT    /api/brokers/{id}       → 改字段 / 设 is_default
DELETE /api/brokers/{id}       → 删 (默认券商不可删, 返回 400)
```
持仓券商通过既有 holding / asset 的 PUT 接口写 `broker` 字段。

**请求示例**
```
PUT /api/brokers/2
{ "is_default": true }
```
**响应示例**
```json
{ "id": 2, "name": "银河证券", "stock_rate": 0.000086, "stock_min": 5,
  "etf_rate": 0.00005, "etf_min": 0.1, "is_default": true }
```

## UI

- **Settings 加「券商费率」区**：表格列券商（名称 / 股票万x·起 / ETF万x·起 / 默认），
  可增、删、改、设默认。预置招商 + 银河。
- **建仓/加仓/编辑表单**：A股 + 场内ETF 显示「券商」下拉，默认选默认券商；
  改券商即时重算预填手续费。

## 容错 / 边界

- `/api/brokers` 拉不到 → 前端回退到内置默认（招商 万1.854/5），不挡建仓。
- 删券商后仍被持仓引用 → 该持仓按默认券商算（broker name 解析不到 → default）。
- 始终保证有且仅有一个默认券商。

## 老数据 / 影响

- 老持仓 broker=NULL → 默认券商（招商）→ 跟现在一致，不破坏。
- 用户把某持仓改成银河 → 其综合成本按银河佣金重算（微变，符合预期）。

## 测试

**后端（pytest）：**
- broker CRUD：建/改/删/设默认；始终唯一默认。
- `estimate_trade_fee` 传不同 `commission_rate/min` → 佣金对，印花/过户/规费不变。
- `compute_position_state` 传券商费率 → 综合成本反映该费率；不传 → 等于旧默认（招商）。
- 场内 ETF kind='etf' 取 etf 费率，股票 kind='stock' 取 stock 费率。

**手动：**
- Settings 改银河 ETF 最低 0.1 → 建场内 ETF（选银河）预填手续费 ≈ 金额×万0.5 或 0.1 起。
- 老持仓不选券商 → 成本不变。

## 文件清单

- `database.py` — 建 `brokers` 表 + seed；holdings/external_assets 加 `broker` 列迁移；CRUD helper。
- `api/broker_routes.py` — 新建 brokers CRUD router。
- `api/portfolio_routes.py` — 按 holding.broker 解析费率传进 compute_position_state；holding PUT 支持 broker。
- `api/assets_routes.py` — asset 创建/编辑支持 broker。
- `services/position_ledger.py` — estimate_trade_fee / compute_position_state 接收佣金费率参数。
- `frontend/src/components/Settings.jsx` — 券商费率管理区。
- `frontend/src/components/UnifiedPortfolio.jsx` — 表单券商下拉 + 预填用 resolveFee；建仓/加仓 fee 估算改券商口径。
- `frontend/src/helpers.js` — resolveFee 辅助 + brokers 缓存拉取。
- `frontend/public/sw.js` — 版本 bump。
- `tests/test_broker_fees.py` — 新建。

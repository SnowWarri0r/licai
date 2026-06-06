# 券商费率配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 可自定义券商档案（股票/ETF 各自费率+最低），每个持仓挂券商，手续费预填 + A 股成本佣金按券商口径算。

**Architecture:** 后端新 `brokers` 表（seed 招商/银河）+ CRUD API；holdings/external_assets 加 `broker` 列；`position_ledger` 佣金费率参数化，`portfolio_routes` 按持仓券商解析费率传入；前端 Settings 管理券商 + 表单券商下拉 + 预填用 resolveFee。

**Tech Stack:** FastAPI + aiosqlite (SQLite) 后端；React + Vite 前端；pytest 测试。venv 在 `./venv`，后端 `./venv/bin/python run.py`（端口 8888），前端 `cd frontend && npm run build`（产物进 `../static`）。

参考 spec：`docs/superpowers/specs/2026-06-05-broker-fee-profiles-design.md`

---

## File Structure

- `database.py` — `brokers` 表建表+seed；holdings/external_assets 加 `broker` 列；broker CRUD helper。
- `api/broker_routes.py`（新）— brokers CRUD router。
- `run.py` — 注册 broker_router。
- `services/position_ledger.py` — `estimate_trade_fee` / `compute_position_state` 接收佣金费率参数。
- `api/portfolio_routes.py` — 按 holding.broker 解析费率传进 compute_position_state（3 处调用点）；holding PUT 支持 broker。
- `api/assets_routes.py` — asset 创建/编辑支持 broker。
- `frontend/src/helpers.js` — `loadBrokers()` + `resolveFee()`。
- `frontend/src/components/Settings.jsx` — 券商费率管理区。
- `frontend/src/components/UnifiedPortfolio.jsx` — 表单券商下拉 + 预填用 resolveFee。
- `frontend/public/sw.js` — 版本 bump。
- `tests/test_broker_fees.py`（新）— 后端测试。

---

### Task 1: brokers 表 + seed + CRUD helper

**Files:**
- Modify: `database.py`（迁移区 ~line 218 后；helper 加在文件末尾区域）
- Test: `tests/test_broker_fees.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_broker_fees.py
import asyncio
import os
import tempfile
import pytest
import config as cfg
import database as db


@pytest.fixture
def fresh_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(cfg, "db_path", path, raising=False)
    # config 模块里 db_path 可能在 dataclass 实例上; 兼容两种
    if hasattr(cfg, "config"):
        monkeypatch.setattr(cfg.config, "db_path", path, raising=False)
    asyncio.run(db.init_db())
    yield path
    os.remove(path)


def test_seed_creates_zhaoshang_yinhe(fresh_db):
    rows = asyncio.run(db.list_brokers())
    names = {r["name"] for r in rows}
    assert "招商证券" in names and "银河证券" in names
    zs = next(r for r in rows if r["name"] == "招商证券")
    assert abs(zs["stock_rate"] - 0.0001854) < 1e-9
    assert zs["stock_min"] == 5 and zs["is_default"] == 1
    yh = next(r for r in rows if r["name"] == "银河证券")
    assert abs(yh["etf_rate"] - 0.00005) < 1e-9 and yh["etf_min"] == 0.1


def test_set_default_is_exclusive(fresh_db):
    rows = asyncio.run(db.list_brokers())
    yh = next(r for r in rows if r["name"] == "银河证券")
    asyncio.run(db.update_broker(yh["id"], is_default=1))
    rows2 = asyncio.run(db.list_brokers())
    defaults = [r for r in rows2 if r["is_default"]]
    assert len(defaults) == 1 and defaults[0]["name"] == "银河证券"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_broker_fees.py -q`
Expected: FAIL（`db.list_brokers` 不存在 / 表不存在）

- [ ] **Step 3: 建表 + seed（database.py 的 init_db 迁移区，紧跟 `bot_budget_override_usdt` 迁移之后）**

```python
        # 券商费率档案
        await db.execute("""
            CREATE TABLE IF NOT EXISTS brokers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                stock_rate REAL NOT NULL,
                stock_min REAL NOT NULL,
                etf_rate REAL NOT NULL,
                etf_min REAL NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0
            )
        """)
        cur = await db.execute("SELECT COUNT(*) FROM brokers")
        if (await cur.fetchone())[0] == 0:
            await db.execute(
                "INSERT INTO brokers (name, stock_rate, stock_min, etf_rate, etf_min, is_default) VALUES (?,?,?,?,?,?)",
                ("招商证券", 0.0001854, 5.0, 0.0001854, 5.0, 1))
            await db.execute(
                "INSERT INTO brokers (name, stock_rate, stock_min, etf_rate, etf_min, is_default) VALUES (?,?,?,?,?,?)",
                ("银河证券", 0.000086, 5.0, 0.00005, 0.1, 0))
        # holdings / external_assets 加 broker 列
        cur = await db.execute("PRAGMA table_info(holdings)")
        hcols = {r[1] for r in await cur.fetchall()}
        if "broker" not in hcols:
            await db.execute("ALTER TABLE holdings ADD COLUMN broker TEXT")
        cur = await db.execute("PRAGMA table_info(external_assets)")
        ecols = {r[1] for r in await cur.fetchall()}
        if "broker" not in ecols:
            await db.execute("ALTER TABLE external_assets ADD COLUMN broker TEXT")
```

- [ ] **Step 4: 加 CRUD helper（database.py 末尾）**

```python
async def list_brokers() -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM brokers ORDER BY is_default DESC, id ASC")
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def get_default_broker() -> dict | None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM brokers WHERE is_default=1 LIMIT 1")
        r = await cur.fetchone()
        if r is None:
            cur = await db.execute("SELECT * FROM brokers ORDER BY id ASC LIMIT 1")
            r = await cur.fetchone()
        return dict(r) if r else None
    finally:
        await db.close()


async def add_broker(name, stock_rate, stock_min, etf_rate, etf_min) -> int:
    db = await get_db()
    try:
        cur = await db.execute(
            "INSERT INTO brokers (name, stock_rate, stock_min, etf_rate, etf_min, is_default) VALUES (?,?,?,?,?,0)",
            (name, stock_rate, stock_min, etf_rate, etf_min))
        await db.commit()
        return cur.lastrowid
    finally:
        await db.close()


async def update_broker(broker_id: int, **kwargs):
    if not kwargs:
        return
    db = await get_db()
    try:
        if kwargs.get("is_default"):
            await db.execute("UPDATE brokers SET is_default=0")  # 默认唯一
        cols = ", ".join(f"{k}=?" for k in kwargs)
        await db.execute(f"UPDATE brokers SET {cols} WHERE id=?", (*kwargs.values(), broker_id))
        await db.commit()
    finally:
        await db.close()


async def delete_broker(broker_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM brokers WHERE id=? AND is_default=0", (broker_id,))
        await db.commit()
    finally:
        await db.close()
```

- [ ] **Step 5: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_broker_fees.py -q`
Expected: PASS（2 passed）

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_broker_fees.py
git commit -m "feat: brokers 表 + seed 招商/银河 + CRUD helper"
```

---

### Task 2: broker CRUD API

**Files:**
- Create: `api/broker_routes.py`
- Modify: `run.py`（注册 router，紧跟其它 include_router）

- [ ] **Step 1: 写 router**

```python
# api/broker_routes.py
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import (
    list_brokers, add_broker, update_broker, delete_broker, get_default_broker,
)

router = APIRouter(prefix="/api/brokers", tags=["brokers"])


class BrokerCreate(BaseModel):
    name: str
    stock_rate: float
    stock_min: float
    etf_rate: float
    etf_min: float


class BrokerUpdate(BaseModel):
    name: Optional[str] = None
    stock_rate: Optional[float] = None
    stock_min: Optional[float] = None
    etf_rate: Optional[float] = None
    etf_min: Optional[float] = None
    is_default: Optional[bool] = None


@router.get("")
async def get_brokers():
    return await list_brokers()


@router.post("")
async def create_broker(data: BrokerCreate):
    bid = await add_broker(data.name, data.stock_rate, data.stock_min, data.etf_rate, data.etf_min)
    return {"id": bid, "message": "created"}


@router.put("/{broker_id}")
async def modify_broker(broker_id: int, data: BrokerUpdate):
    payload = data.model_dump(exclude_unset=True)
    if "is_default" in payload:
        payload["is_default"] = 1 if payload["is_default"] else 0
    if not payload:
        raise HTTPException(400, "无可改字段")
    await update_broker(broker_id, **payload)
    return {"message": "updated"}


@router.delete("/{broker_id}")
async def remove_broker(broker_id: int):
    rows = await list_brokers()
    target = next((b for b in rows if b["id"] == broker_id), None)
    if not target:
        raise HTTPException(404, "券商不存在")
    if target["is_default"]:
        raise HTTPException(400, "默认券商不可删, 请先把别的设为默认")
    await delete_broker(broker_id)
    return {"message": "deleted"}
```

- [ ] **Step 2: 注册 router（run.py，在 `app.include_router(portfolio_router)` 附近加）**

```python
from api.broker_routes import router as broker_router
app.include_router(broker_router)
```
（import 放文件顶部 router import 区，include 放其它 include_router 旁）

- [ ] **Step 3: 手动冒烟**

Run（先重启后端）：
```bash
pkill -f run.py; sleep 1; nohup ./venv/bin/python run.py > /tmp/lb.log 2>&1 &
sleep 3; curl -s localhost:8888/api/brokers
```
Expected: 返回含招商/银河两条 JSON。

- [ ] **Step 4: Commit**

```bash
git add api/broker_routes.py run.py
git commit -m "feat: /api/brokers CRUD 接口"
```

---

### Task 3: position_ledger 佣金费率参数化

**Files:**
- Modify: `services/position_ledger.py:39-56`（estimate_trade_fee）、`69-73`（compute_position_state 签名）
- Test: `tests/test_broker_fees.py`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_broker_fees.py
from datetime import date
from services.position_ledger import estimate_trade_fee, compute_position_state


def test_fee_uses_passed_commission():
    # 10000 元买入: 招商 万1.854 → 佣金 1.854; 银河 万0.86 → 0.86 (都过最低 5? 否, <5 → 取5)
    # 用大额避开最低: 100万
    amt_shares = 100000  # 100万元 @ 10
    zs = estimate_trade_fee("BUY", 10.0, amt_shares, "000001",
                            commission_rate=0.0001854, commission_min=5)
    yh = estimate_trade_fee("BUY", 10.0, amt_shares, "000001",
                            commission_rate=0.000086, commission_min=5)
    # 仅佣金不同: 差 = 100万 × (0.0001854-0.000086)
    assert abs((zs - yh) - 1000000 * (0.0001854 - 0.000086)) < 0.01


def test_compute_state_passes_commission():
    actions = [{"action_type": "BUY", "price": 10.0, "shares": 100000, "trade_date": "2026-01-01"}]
    s_zs = compute_position_state(actions, today=date(2026, 6, 1), stock_code="000001",
                                  commission_rate=0.0001854, commission_min=5)
    s_yh = compute_position_state(actions, today=date(2026, 6, 1), stock_code="000001",
                                  commission_rate=0.000086, commission_min=5)
    # 银河佣金低 → 成本更低
    assert s_yh["cost_price"] < s_zs["cost_price"]


def test_compute_state_default_is_zhaoshang():
    # 不传 commission → 等于传招商默认
    actions = [{"action_type": "BUY", "price": 10.0, "shares": 100000, "trade_date": "2026-01-01"}]
    a = compute_position_state(actions, today=date(2026, 6, 1), stock_code="000001")
    b = compute_position_state(actions, today=date(2026, 6, 1), stock_code="000001",
                               commission_rate=0.0001854, commission_min=5)
    assert abs(a["cost_price"] - b["cost_price"]) < 1e-9
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_broker_fees.py -k commission -v`
Expected: FAIL（estimate_trade_fee 不接受 commission_rate 参数）

- [ ] **Step 3: 改 estimate_trade_fee 签名 + 佣金项**

把 `def estimate_trade_fee(action_type, price, shares, stock_code=""):` 改为：
```python
def estimate_trade_fee(action_type: str, price: float, shares: int, stock_code: str = "",
                       commission_rate: float | None = None,
                       commission_min: float | None = None) -> float:
    """Estimate A-share trading fees. commission_rate/min 不传则用模块默认 (招商)。"""
    if stock_code and not is_a_share(stock_code):
        return 0.0
    if action_type not in ACQUIRE and action_type not in RELEASE:
        return 0.0
    amount = price * shares
    if amount <= 0:
        return 0.0
    c_rate = _COMMISSION_RATE if commission_rate is None else commission_rate
    c_min = _COMMISSION_MIN if commission_min is None else commission_min
    commission = max(amount * c_rate, c_min)
    stamp = amount * _STAMP_RATE if action_type in RELEASE else 0.0
    transfer = amount * _TRANSFER_RATE if _is_shanghai(stock_code) else 0.0
    regulatory = amount * (_EXCHANGE_HANDLE_RATE + _REGULATORY_FEE_RATE)
    return commission + stamp + transfer + regulatory
```

- [ ] **Step 4: 改 compute_position_state 签名 + 透传**

签名改为：
```python
def compute_position_state(
    actions: Iterable[dict],
    today: date | None = None,
    stock_code: str = "",
    commission_rate: float | None = None,
    commission_min: float | None = None,
) -> dict:
```
在函数内部把局部 `_fee_of` 调用 estimate_trade_fee 的地方带上费率。找到：
```python
    def _fee_of(a, t, price, shares):
        if not stock_code:
            return 0.0
        override = a.get("fee")
        return float(override) if override is not None else estimate_trade_fee(t, price, shares, stock_code)
```
改为：
```python
    def _fee_of(a, t, price, shares):
        if not stock_code:
            return 0.0
        override = a.get("fee")
        return float(override) if override is not None else estimate_trade_fee(
            t, price, shares, stock_code, commission_rate, commission_min)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_broker_fees.py -q`
Expected: PASS（全部）

- [ ] **Step 6: Commit**

```bash
git add services/position_ledger.py tests/test_broker_fees.py
git commit -m "feat: position_ledger 佣金费率参数化 (默认招商)"
```

---

### Task 4: holdings 挂券商 + portfolio_routes 按券商算

**Files:**
- Modify: `api/portfolio_routes.py`（`_recompute_holding`、`list_holdings` 现算块、realized 端点；HoldingUpdate / modify_holding）

- [ ] **Step 1: 加费率解析 helper（portfolio_routes.py 顶部，import 之后）**

```python
from database import list_brokers, get_default_broker


async def _broker_stock_fee(broker_name: str | None) -> tuple[float | None, float | None]:
    """按券商 name 返回 (股票佣金费率, 最低). 找不到 → 默认券商. 都没有 → (None, None) 用模块默认。"""
    brokers = await list_brokers()
    b = next((x for x in brokers if x["name"] == broker_name), None) if broker_name else None
    if b is None:
        b = next((x for x in brokers if x["is_default"]), None) or (brokers[0] if brokers else None)
    if b is None:
        return (None, None)
    return (b["stock_rate"], b["stock_min"])
```

- [ ] **Step 2: 改 3 处 compute_position_state 调用带券商费率**

`_recompute_holding`（line ~43）：
```python
async def _recompute_holding(stock_code: str):
    actions = await get_position_actions(stock_code, limit=500)
    h = await get_holding(stock_code)
    c_rate, c_min = await _broker_stock_fee((h or {}).get("broker"))
    state = compute_position_state(actions, stock_code=stock_code,
                                   commission_rate=c_rate, commission_min=c_min)
    if state["shares"] > 0:
        await update_holding(stock_code, shares=state["shares"], cost_price=state["cost_price"])
    else:
        await update_holding(stock_code, shares=0, cost_price=0)
```
`list_holdings` 现算块（line ~78）：在 `_st = compute_position_state(...)` 处改为：
```python
            c_rate, c_min = await _broker_stock_fee(h.get("broker"))
            _st = compute_position_state(_acts, stock_code=code,
                                         commission_rate=c_rate, commission_min=c_min)
```
realized 端点（line ~165，`state = compute_position_state(actions, stock_code=code)`）：
```python
        c_rate, c_min = await _broker_stock_fee((holdings_map.get(code) or {}).get("broker"))
        state = compute_position_state(actions, stock_code=code,
                                       commission_rate=c_rate, commission_min=c_min)
```

- [ ] **Step 3: HoldingUpdate 加 broker + modify_holding 透传**

找到 `class HoldingUpdate(BaseModel)`，加：
```python
    broker: Optional[str] = None
```
在 `modify_holding` 的 kwargs 组装处加：
```python
    if data.broker is not None:
        kwargs["broker"] = data.broker
```
（`list_holdings` 返回的 HoldingResponse 也加 `broker` 字段透出：在 HoldingResponse 模型加 `broker: Optional[str] = None`，并在构造处 `broker=h.get("broker")`。）

- [ ] **Step 4: 重启冒烟 — 老持仓 broker=NULL 成本不变**

```bash
pkill -f run.py; sleep 1; nohup ./venv/bin/python run.py > /tmp/lb.log 2>&1 &
sleep 3
curl -s localhost:8888/api/portfolio | python3 -c "import sys,json;d=json.load(sys.stdin);print([(h['stock_code'],h['cost_price']) for h in d[:3]])"
```
Expected: 跟改动前一致（broker 为空走默认招商）。

- [ ] **Step 5: Commit**

```bash
git add api/portfolio_routes.py
git commit -m "feat: A股持仓按 broker 佣金费率算成本; holding 支持 broker 字段"
```

---

### Task 5: 场内 ETF (external_assets) 挂券商

**Files:**
- Modify: `api/assets_routes.py`（AssetCreate / AssetUpdate 加 broker；create_asset / modify_asset 透传；add_external_asset 调用）、`database.py`（add_external_asset 加 broker 参数）

- [ ] **Step 1: database.add_external_asset 加 broker 参数**

在 `add_external_asset(... purchase_fee_rate=None)` 签名末尾加 `broker: str | None = None`，
INSERT 的列加 `broker`、VALUES 加 `broker`。

- [ ] **Step 2: AssetCreate / AssetUpdate 加字段 + 透传**

`AssetCreate` 加 `broker: Optional[str] = None`；`AssetUpdate` 加 `broker: Optional[str] = None`。
`create_asset` 的 `add_external_asset(...)` 调用加 `broker=data.broker`。
`modify_asset` 用 `model_dump(exclude_unset=True)` → broker 自动进 payload（无需额外代码，确认 update_external_asset 是通用 kwargs，已是）。

- [ ] **Step 3: 冒烟 — 建一只场内 ETF 带 broker**

```bash
curl -s -X POST localhost:8888/api/assets -H 'Content-Type: application/json' \
  -d '{"asset_type":"FUND","code":"159995","name":"T测","cost_amount":100,"shares":50,"broker":"银河证券"}' >/dev/null
curl -s localhost:8888/api/assets | python3 -c "import sys,json;d=json.load(sys.stdin);d=d.get('assets',d);print([(a['code'],a.get('broker')) for a in d if a['code']=='159995'])"
```
Expected: `[('159995','银河证券')]`。然后删测试：
```bash
python3 -c "import sqlite3;c=sqlite3.connect('portfolio.db');c.execute(\"DELETE FROM external_assets WHERE code='159995'\");c.execute(\"DELETE FROM external_asset_actions WHERE asset_id NOT IN (SELECT id FROM external_assets)\");c.commit()"
```

- [ ] **Step 4: Commit**

```bash
git add api/assets_routes.py database.py
git commit -m "feat: 场内 ETF 资产支持 broker 字段"
```

---

### Task 6: 前端 brokers 拉取 + resolveFee

**Files:**
- Modify: `frontend/src/helpers.js`（末尾加）

- [ ] **Step 1: 加 helper**

```javascript
// 券商费率: 拉一次缓存. resolveFee 按 (券商名, kind) 给 {rate, min}
let _brokersCache = null
export async function loadBrokers() {
  if (_brokersCache) return _brokersCache
  try {
    const r = await fetch('/api/brokers')
    _brokersCache = await r.json()
  } catch { _brokersCache = [] }
  return _brokersCache
}
export function clearBrokersCache() { _brokersCache = null }

// kind: 'stock' | 'etf'. 找不到券商→默认券商→内置兜底(招商 万1.854/5)
export function resolveFee(brokers, brokerName, kind) {
  const list = brokers || []
  let b = brokerName ? list.find(x => x.name === brokerName) : null
  if (!b) b = list.find(x => x.is_default) || list[0]
  if (!b) return kind === 'etf' ? { rate: 0.0001854, min: 5 } : { rate: 0.0001854, min: 5 }
  return kind === 'etf'
    ? { rate: b.etf_rate, min: b.etf_min }
    : { rate: b.stock_rate, min: b.stock_min }
}
export function estimateFee(amount, brokers, brokerName, kind) {
  const { rate, min } = resolveFee(brokers, brokerName, kind)
  return Math.max(amount * rate, min)
}
```

- [ ] **Step 2: 冒烟 build**

Run: `cd frontend && npm run build 2>&1 | tail -2`
Expected: build 成功。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/helpers.js
git commit -m "feat: 前端 loadBrokers + resolveFee/estimateFee helper"
```

---

### Task 7: Settings 券商费率管理区

**Files:**
- Modify: `frontend/src/components/Settings.jsx`

- [ ] **Step 1: 加券商管理 section（在现有最后一个 `</section>` 之后、组件 return 的容器内追加一个新 section）**

```jsx
{/* 券商费率 */}
<section className="rounded-xl border border-accent/20 bg-surface-2/80 overflow-hidden mt-4">
  <div className="px-4 py-3 border-b border-border flex items-center justify-between">
    <h2 className="text-[13px] font-medium text-accent tracking-wide">券商费率</h2>
    <button onClick={addRow} className="text-[11px] px-2 py-1 rounded border border-accent/40 text-accent hover:bg-accent/10 cursor-pointer">+ 新增券商</button>
  </div>
  <div className="p-3 space-y-2">
    {brokers.map(b => (
      <div key={b.id} className="grid grid-cols-[1fr_auto_auto_auto_auto_auto] gap-2 items-center text-[12px]">
        <input value={b.name} onChange={e => edit(b.id, 'name', e.target.value)} className="bg-bg border border-border rounded px-2 py-1" placeholder="券商名" />
        <input type="number" step="0.0001" value={(b.stock_rate*10000).toFixed(4)} onChange={e => edit(b.id,'stock_rate', parseFloat(e.target.value)/10000)} className="w-20 bg-bg border border-border rounded px-2 py-1" title="股票费率(万)" />
        <input type="number" step="0.1" value={b.stock_min} onChange={e => edit(b.id,'stock_min', parseFloat(e.target.value))} className="w-16 bg-bg border border-border rounded px-2 py-1" title="股票最低¥" />
        <input type="number" step="0.0001" value={(b.etf_rate*10000).toFixed(4)} onChange={e => edit(b.id,'etf_rate', parseFloat(e.target.value)/10000)} className="w-20 bg-bg border border-border rounded px-2 py-1" title="ETF费率(万)" />
        <input type="number" step="0.1" value={b.etf_min} onChange={e => edit(b.id,'etf_min', parseFloat(e.target.value))} className="w-16 bg-bg border border-border rounded px-2 py-1" title="ETF最低¥" />
        <div className="flex gap-1">
          <button onClick={() => setDefault(b.id)} className={`text-[10px] px-1.5 py-1 rounded border cursor-pointer ${b.is_default ? 'border-accent text-accent bg-accent/10' : 'border-border text-text-dim'}`}>{b.is_default ? '默认' : '设默认'}</button>
          {!b.is_default && <button onClick={() => del(b.id)} className="text-[10px] px-1.5 py-1 rounded border border-bear/40 text-bear cursor-pointer">删</button>}
        </div>
      </div>
    ))}
    <div className="text-[10px] text-text-muted">费率单位「万」(如 1.854 = 万1.854)；最低为每笔最低收费(元)。改完自动保存。</div>
  </div>
</section>
```

- [ ] **Step 2: 加状态 + 逻辑（Settings 组件函数体顶部）**

```jsx
const [brokers, setBrokers] = React.useState([])
React.useEffect(() => { fetch('/api/brokers').then(r => r.json()).then(setBrokers).catch(()=>{}) }, [])
const reload = () => fetch('/api/brokers').then(r => r.json()).then(setBrokers).catch(()=>{})
const edit = (id, field, val) => {
  setBrokers(bs => bs.map(b => b.id===id ? {...b, [field]: val} : b))
  clearTimeout(window.__brokerSaveT)
  window.__brokerSaveT = setTimeout(() => {
    const b = (brokersRef.current || []).find(x => x.id===id)
    if (b) fetch(`/api/brokers/${id}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({[field]: val})})
  }, 600)
}
const brokersRef = React.useRef(brokers); React.useEffect(()=>{brokersRef.current=brokers},[brokers])
const setDefault = (id) => fetch(`/api/brokers/${id}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({is_default:true})}).then(reload)
const del = (id) => { if(confirm('删除该券商？')) fetch(`/api/brokers/${id}`,{method:'DELETE'}).then(r=>r.json()).then(d=>{ if(d.detail) alert(d.detail); reload() }) }
const addRow = () => fetch('/api/brokers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'新券商',stock_rate:0.0001854,stock_min:5,etf_rate:0.0001854,etf_min:5})}).then(reload)
```
（注意 `import React from 'react'` 已在文件顶部；若是具名 import，改用对应写法。）

- [ ] **Step 3: build + 手动验证**

Run: `cd frontend && npm run build 2>&1 | tail -2`，重启后端，打开 Settings → 看到招商/银河、能改费率/设默认/新增。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Settings.jsx
git commit -m "feat: Settings 券商费率管理区"
```

---

### Task 8: 表单券商下拉 + 预填用 resolveFee

**Files:**
- Modify: `frontend/src/components/UnifiedPortfolio.jsx`（AddAShareForm、AddAssetForm、AddLotRow、EditAssetRow）

- [ ] **Step 1: 顶层拉 brokers（UnifiedPortfolio 组件挂载时）**

在 `UnifiedPortfolio` 函数体加：
```jsx
const [brokers, setBrokers] = useState([])
useEffect(() => { loadBrokers().then(setBrokers) }, [])
```
并在 import 行加入 `loadBrokers, resolveFee, estimateFee`（已有 `isOnchainEtf` import 行）。
把 `brokers` 透传给需要的子表单组件（AddAShareForm / AddAssetForm / AddLotRow / EditAssetRow 的 props）。

- [ ] **Step 2: AddAssetForm / AddLotRow 的 fee 自动估算改用券商口径**

AddAssetForm 自动估 fee 的 effect（现 `const est = Math.max(amount * BROKER_COMMISSION_RATE, BROKER_COMMISSION_MIN)`）改为：
```jsx
const kind = isOnchainEtf(code) ? 'etf' : 'stock'
const est = estimateFee(amount, brokers, selectedBroker, kind)
```
AddLotRow 同处（line ~2433）同样替换，kind 用 `isOnchainEtf(asset.code) ? 'etf' : 'stock'`，券商用 `asset.broker`。
`selectedBroker` 见下一步（AddAssetForm 新增 state）。

- [ ] **Step 3: 加「券商」下拉（仅 A股 + 场内ETF）**

AddAShareForm（A股建仓）加 state `const [broker, setBroker] = useState('')` 和一个下拉：
```jsx
<div className="flex flex-col gap-1">
  <label className="text-[11px] text-text-dim">券商</label>
  <select className={`${inp} w-28`} value={broker} onChange={e=>setBroker(e.target.value)}>
    <option value="">默认</option>
    {brokers.map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
  </select>
</div>
```
并在 `api.addHolding({...})` 的 payload 加 `broker: broker || null`。
（确认 `api.addHolding` / 后端 create_holding 接受 broker；若 create_holding 不接受，在 HoldingCreate 加 `broker: Optional[str]=None` 并在 add_holding 后 `await update_holding(stock_code, broker=...)`。）

AddAssetForm 加同样下拉（仅 `assetType==='FUND' && isOnchainEtf(code)` 时显示），state `selectedBroker`，payload 加 `broker`。

EditAssetRow 加券商下拉（仅场内 ETF），payload 加 `broker`。

- [ ] **Step 4: build + 手动验证**

- 建场内 ETF 选「银河证券」→ 手续费预填按 万0.5 / 0.1 起。
- A股建仓选券商 → 写入 holding.broker。
- 老持仓不带券商 → 成本不变。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/UnifiedPortfolio.jsx
git commit -m "feat: 建仓/加仓/编辑表单加券商下拉 + 手续费按券商预填"
```

---

### Task 9: HoldingCreate broker（若 Task 8 Step 3 发现缺）+ sw bump + 收尾

**Files:**
- Modify: `api/portfolio_routes.py`（HoldingCreate）、`frontend/public/sw.js`

- [ ] **Step 1: create_holding 支持 broker（若尚未）**

`HoldingCreate` 加 `broker: Optional[str] = None`；`create_holding` 在 `add_holding(...)` 之后加：
```python
    if data.broker:
        await update_holding(stock_code, broker=data.broker)
```

- [ ] **Step 2: sw 版本 bump**

`frontend/public/sw.js` 的 `CACHE_NAME` 升一版（如 `licai-v104` → `licai-v105`）。

- [ ] **Step 3: 全量测试 + build**

Run: `./venv/bin/python -m pytest tests/ -q && cd frontend && npm run build 2>&1 | tail -2`
Expected: 全 PASS + build 成功。

- [ ] **Step 4: 端到端手动验证**

- Settings 改银河 ETF 最低 0.1 → 建场内 ETF（银河）预填 ≈ 万0.5 或 0.1 起。
- A股持仓改券商为银河 → 综合成本按银河佣金微变。
- 老持仓/老 ETF 不带券商 → 数值不变。

- [ ] **Step 5: Commit**

```bash
git add api/portfolio_routes.py frontend/public/sw.js
git commit -m "feat: create_holding 支持 broker + sw bump; 券商费率功能收尾"
```

---

## Self-Review 记录

- Spec 各节均有对应任务：brokers 表/seed(T1)、CRUD API(T2)、position_ledger 参数化(T3)、holdings 券商+成本(T4)、ETF 券商(T5)、前端 helper(T6)、Settings UI(T7)、表单下拉+预填(T8)、收尾(T9)。
- 默认券商保护：T1 seed is_default、T2 删默认 400、T4/T6 解析兜底默认。
- 类型一致：`resolveFee(brokers, brokerName, kind)` 在 T6 定义、T8 调用签名一致；`_broker_stock_fee` 在 T4 定义并在 3 处调用。

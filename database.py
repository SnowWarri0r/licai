"""SQLite database setup and operations."""
from __future__ import annotations
import aiosqlite
from config import config


def resolve_action_time(action: dict) -> str:
    """成交时刻: trade_time(HH:MM, 用户手填)优先; 否则用 created_at(存的是 UTC)转北京
    时间取 HH:MM:SS。供分时图按真实时刻打 B/S 点。拿不到返回 ""。"""
    tt = (action.get("trade_time") or "").strip()
    if tt:
        return tt
    ca = action.get("created_at")
    if ca:
        try:
            from datetime import datetime, timedelta
            dt = datetime.fromisoformat(str(ca).replace("T", " ").split(".")[0])
            return (dt + timedelta(hours=8)).strftime("%H:%M:%S")
        except Exception:
            pass
    return ""

SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL UNIQUE,
    stock_name TEXT NOT NULL DEFAULT '',
    shares INTEGER NOT NULL,
    cost_price REAL NOT NULL,
    purchase_date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trade_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    trade_type TEXT NOT NULL,
    price REAL NOT NULL,
    shares INTEGER NOT NULL,
    signal_source TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alert_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL UNIQUE,
    buy_zone_low REAL,
    buy_zone_high REAL,
    sell_zone_low REAL,
    sell_zone_high REAL,
    enabled INTEGER DEFAULT 1,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kline_cache (
    stock_code TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL DEFAULT 0,
    PRIMARY KEY (stock_code, date)
);

CREATE TABLE IF NOT EXISTS custom_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    price REAL NOT NULL,
    message TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    triggered INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unwind_plans (
    stock_code TEXT PRIMARY KEY,
    total_budget REAL NOT NULL,
    used_budget REAL DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS unwind_tranches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    idx INTEGER NOT NULL,
    trigger_price REAL NOT NULL,
    shares INTEGER NOT NULL,
    requires_health TEXT DEFAULT 'any',
    status TEXT DEFAULT 'pending',
    triggered_at TIMESTAMP,
    executed_at TIMESTAMP,
    executed_price REAL,
    sold_back_price REAL,
    sold_back_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS external_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_type TEXT NOT NULL,       -- FUND / CRYPTO / BOT
    code TEXT NOT NULL,             -- fund code / symbol / bot label
    name TEXT NOT NULL,
    platform TEXT,                  -- 支付宝 / 招商 / OKX / 币安 / etc
    cost_amount REAL NOT NULL,      -- total cost in CNY (投入本金)
    shares REAL,                    -- optional: units held (funds use this)
    manual_value REAL,              -- manual override for current value (bots)
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS position_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    action_type TEXT NOT NULL,
    price REAL NOT NULL,
    shares INTEGER NOT NULL,
    tranche_id INTEGER,
    note TEXT DEFAULT '',
    trade_date TEXT,
    fee REAL,                            -- 手续费 (CNY) 覆盖; NULL = 用 estimate_trade_fee 自动算
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS morning_briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    briefing_date TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, briefing_date)
);

CREATE TABLE IF NOT EXISTS cashflow_monthly (
    month TEXT PRIMARY KEY,             -- YYYY-MM
    income REAL DEFAULT 0,              -- 月收入(税后)
    fixed_cost REAL DEFAULT 0,          -- 固定开销 (房租/餐饮/账单/还贷)
    discretionary REAL DEFAULT 0,       -- 实际可自由支配开销 (购物/娱乐/旅行)
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 外部资产 (基金/加密/理财/现金) 的交易流水, 用于 FIFO 算实现盈亏.
-- BOT 不走这张表 (走 OKX 同步).
CREATE TABLE IF NOT EXISTS external_asset_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,           -- FK external_assets.id
    action_type TEXT NOT NULL,           -- BUY | ADD | REDEEM | DEPOSIT | WITHDRAW | INTEREST | DIVIDEND
    amount REAL NOT NULL DEFAULT 0,      -- CNY 本金/赎回金额(总额); INTEREST/DIVIDEND 时为派息金额
    shares REAL,                         -- FUND/CRYPTO 用; +加仓 / -赎回; WEALTH/CASH 留空
    unit_price REAL,                     -- FUND/CRYPTO 当时净值/价
    fee REAL DEFAULT 0,                  -- 手续费 (CNY), 含在 amount 里 (amount = 总付出含费)
    trade_date TEXT,                     -- YYYY-MM-DD (申请日)
    status TEXT DEFAULT 'confirmed',     -- confirmed: 进 ledger; pending: T+1 待确认 (OTC 基金 申购/赎回)
    note TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_eaa_asset_date ON external_asset_actions (asset_id, trade_date);

-- 定投计划: 按 frequency 触发, 每次写一条 pending ADD action.
CREATE TABLE IF NOT EXISTS dca_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    mode TEXT NOT NULL DEFAULT 'amount',     -- 'amount' (固定金额) | 'shares' (固定份额)
    value REAL NOT NULL,                     -- amount=CNY; shares=份数
    frequency TEXT NOT NULL DEFAULT 'monthly', -- 'daily_trading' | 'weekly' | 'monthly'
    day_of_month INTEGER,                    -- 1-31 (frequency=monthly), 月末 clamp
    day_of_week INTEGER,                     -- 1=Mon..7=Sun (frequency=weekly)
    status TEXT NOT NULL DEFAULT 'active',   -- 'active' | 'paused'
    next_due TEXT,                           -- YYYY-MM-DD
    last_fired_at TEXT,                      -- YYYY-MM-DD
    note TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dca_status_due ON dca_schedules (status, next_due);

-- 持仓逻辑跟踪: 记录"当初为什么买这只", 供复盘客观对照逻辑是否还成立 (A股 + 场内外资产共用 code)。
CREATE TABLE IF NOT EXISTS position_thesis (
    code TEXT PRIMARY KEY,                    -- 股票/基金代码 (裸码, 如 600519 / 159516)
    name TEXT DEFAULT '',
    thesis TEXT NOT NULL,                     -- 买入逻辑 (为什么买、看中什么、预期)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ask_session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT DEFAULT '',                    -- 用首个问题做标题
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ask_message (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,                        -- user | assistant
    content TEXT NOT NULL,
    meta TEXT DEFAULT '',                      -- JSON: {tools_used, sources}
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ask_message_session ON ask_message(session_id, id);
"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(config.db_path)
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    db = await get_db()
    try:
        await db.executescript(SCHEMA)
        # Migration: add trade_date to position_actions if missing
        cursor = await db.execute("PRAGMA table_info(position_actions)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "trade_date" not in cols:
            await db.execute("ALTER TABLE position_actions ADD COLUMN trade_date TEXT")
        if "fee" not in cols:
            # NULL = 用 estimate_trade_fee 自动算; 非 NULL = 用户手填覆盖
            await db.execute("ALTER TABLE position_actions ADD COLUMN fee REAL")
        if "trade_time" not in cols:
            # 成交时刻 HH:MM (可选); NULL → 用 created_at(转北京时间)推断, 供分时图精准打 B/S 点
            await db.execute("ALTER TABLE position_actions ADD COLUMN trade_time TEXT")
        if "broker" not in cols:
            # 本笔成交的券商 (可选); NULL → 用持仓默认券商。支持同股跨券商, 手续费按各自费率
            await db.execute("ALTER TABLE position_actions ADD COLUMN broker TEXT")
        # Migration: add trade_time to external_asset_actions
        cursor = await db.execute("PRAGMA table_info(external_asset_actions)")
        eaa_cols = {row[1] for row in await cursor.fetchall()}
        if "trade_time" not in eaa_cols:
            await db.execute("ALTER TABLE external_asset_actions ADD COLUMN trade_time TEXT")
        # Migration: add purchase_date to holdings if missing (reserved for future)
        cursor = await db.execute("PRAGMA table_info(holdings)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "purchase_date" not in cols:
            await db.execute("ALTER TABLE holdings ADD COLUMN purchase_date TEXT")
        # Migration: add sold_back fields to unwind_tranches for T-sell tracking
        cursor = await db.execute("PRAGMA table_info(unwind_tranches)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "sold_back_price" not in cols:
            await db.execute("ALTER TABLE unwind_tranches ADD COLUMN sold_back_price REAL")
        if "sold_back_at" not in cols:
            await db.execute("ALTER TABLE unwind_tranches ADD COLUMN sold_back_at TIMESTAMP")
        # Migration: add okx_algo_id + okx_bot_type to external_assets for auto-sync
        cursor = await db.execute("PRAGMA table_info(external_assets)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "okx_algo_id" not in cols:
            await db.execute("ALTER TABLE external_assets ADD COLUMN okx_algo_id TEXT")
        if "okx_bot_type" not in cols:
            await db.execute("ALTER TABLE external_assets ADD COLUMN okx_bot_type TEXT")
        # WEALTH 类型用：年化收益率 + 起投日 → 自动算当前总额
        if "annual_yield_rate" not in cols:
            await db.execute("ALTER TABLE external_assets ADD COLUMN annual_yield_rate REAL")
        if "start_date" not in cols:
            await db.execute("ALTER TABLE external_assets ADD COLUMN start_date TEXT")
        # 基金/加密 待确认份额：买了但份额还没结算
        if "pending_amount" not in cols:
            await db.execute("ALTER TABLE external_assets ADD COLUMN pending_amount REAL DEFAULT 0")
        # OKX 马丁实际总预算 (USDT). raw 字段没"总预算", 算法反推不准, 让用户手填覆盖.
        if "bot_budget_override_usdt" not in cols:
            await db.execute("ALTER TABLE external_assets ADD COLUMN bot_budget_override_usdt REAL")
        # 基金申购费率 (小数, 如 0.0015 = 0.15%). C 类/无申购费 = 0/NULL. 批量结算时内扣算份额。
        if "purchase_fee_rate" not in cols:
            await db.execute("ALTER TABLE external_assets ADD COLUMN purchase_fee_rate REAL")

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
        cur = await db.execute("PRAGMA table_info(holdings)")
        hcols = {r[1] for r in await cur.fetchall()}
        if "broker" not in hcols:
            await db.execute("ALTER TABLE holdings ADD COLUMN broker TEXT")
        cur = await db.execute("PRAGMA table_info(external_assets)")
        ecols = {r[1] for r in await cur.fetchall()}
        if "broker" not in ecols:
            await db.execute("ALTER TABLE external_assets ADD COLUMN broker TEXT")
        # 平仓归档(BOT/策略止损结束等): closed=1 退出在持, closed_realized 记平仓时已实现盈亏(CNY)
        if "closed" not in ecols:
            await db.execute("ALTER TABLE external_assets ADD COLUMN closed INTEGER DEFAULT 0")
        if "closed_realized" not in ecols:
            await db.execute("ALTER TABLE external_assets ADD COLUMN closed_realized REAL")
        if "closed_date" not in ecols:
            await db.execute("ALTER TABLE external_assets ADD COLUMN closed_date TEXT")

        # external_asset_actions: status 字段 + fee 字段 (旧库迁移)
        cursor = await db.execute("PRAGMA table_info(external_asset_actions)")
        cols = {row[1] for row in await cursor.fetchall()}
        if cols and "status" not in cols:
            await db.execute("ALTER TABLE external_asset_actions ADD COLUMN status TEXT DEFAULT 'confirmed'")
        if cols and "fee" not in cols:
            # 手续费 (CNY), 包含在 amount 里 (amount = 总付出含费), 单存方便看净额
            await db.execute("ALTER TABLE external_asset_actions ADD COLUMN fee REAL DEFAULT 0")
        if cols and "interest_part" not in cols:
            # WEALTH/CASH 赎回时拆分: amount 里有多少是利息. NULL=不区分(走 FIFO 兜底)
            await db.execute("ALTER TABLE external_asset_actions ADD COLUMN interest_part REAL")

        # dca_schedules: 旧库迁移 frequency / day_of_week / day_of_month nullable
        cursor = await db.execute("PRAGMA table_info(dca_schedules)")
        cols = {row[1] for row in await cursor.fetchall()}
        if cols:
            if "frequency" not in cols:
                await db.execute("ALTER TABLE dca_schedules ADD COLUMN frequency TEXT NOT NULL DEFAULT 'monthly'")
            if "day_of_week" not in cols:
                await db.execute("ALTER TABLE dca_schedules ADD COLUMN day_of_week INTEGER")
        await db.commit()

        # Seed: any holding without a position_action → create initial BUY action
        cursor = await db.execute("""
            SELECT h.stock_code, h.shares, h.cost_price, h.created_at
            FROM holdings h
            WHERE NOT EXISTS (
                SELECT 1 FROM position_actions a WHERE a.stock_code = h.stock_code
            )
        """)
        rows = await cursor.fetchall()
        for r in rows:
            code = r["stock_code"]
            shares = r["shares"]
            cost = r["cost_price"]
            created = str(r["created_at"])[:10] if r["created_at"] else None
            await db.execute(
                """INSERT INTO position_actions
                   (stock_code, action_type, price, shares, note, trade_date)
                   VALUES (?, 'BUY', ?, ?, 'initial (auto-migrated)', ?)""",
                (code, cost, shares, created),
            )
            print(f"[migration] Seeded initial BUY for {code}: {shares}股 @ {cost} on {created}")
        await db.commit()

        # Seed external_asset_actions: 给已有 external_assets 还没 actions 的, 按当前 cost_amount 补一条 BUY/DEPOSIT
        cursor = await db.execute("""
            SELECT a.id, a.asset_type, a.cost_amount, a.shares, a.start_date, a.created_at
            FROM external_assets a
            WHERE NOT EXISTS (
                SELECT 1 FROM external_asset_actions ea WHERE ea.asset_id = a.id
            )
        """)
        rows = await cursor.fetchall()
        for r in rows:
            asset_id = r["id"]
            atype = r["asset_type"] or ""
            cost = float(r["cost_amount"] or 0)
            shares = r["shares"]
            unit_price = None
            if atype in ("FUND", "CRYPTO") and shares and float(shares) > 0:
                unit_price = round(cost / float(shares), 6) if cost > 0 else None
            action_type = "BUY" if atype in ("FUND", "CRYPTO", "BOT") else "DEPOSIT"
            seed_date = (r["start_date"] or str(r["created_at"] or "")[:10]) or None
            await db.execute(
                """INSERT INTO external_asset_actions
                   (asset_id, action_type, amount, shares, unit_price, trade_date, note)
                   VALUES (?, ?, ?, ?, ?, ?, 'initial (auto-migrated)')""",
                (asset_id, action_type, cost, float(shares) if shares else None, unit_price, seed_date),
            )
            print(f"[migration] Seeded {action_type} for asset#{asset_id} ({atype}): ¥{cost} on {seed_date}")
        await db.commit()
    finally:
        await db.close()


# --- Holdings CRUD ---

async def get_all_holdings() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM holdings ORDER BY stock_code")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_holding(stock_code: str) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM holdings WHERE stock_code = ?", (stock_code,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def add_holding(stock_code: str, stock_name: str, shares: int, cost_price: float):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO holdings (stock_code, stock_name, shares, cost_price) VALUES (?, ?, ?, ?)",
            (stock_code, stock_name, shares, cost_price),
        )
        await db.commit()
    finally:
        await db.close()


async def update_holding(stock_code: str, **kwargs):
    db = await get_db()
    try:
        sets = []
        vals = []
        for k, v in kwargs.items():
            if v is not None:
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            return
        sets.append("updated_at = CURRENT_TIMESTAMP")
        vals.append(stock_code)
        await db.execute(
            f"UPDATE holdings SET {', '.join(sets)} WHERE stock_code = ?",
            vals,
        )
        await db.commit()
    finally:
        await db.close()


async def delete_holding(stock_code: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM holdings WHERE stock_code = ?", (stock_code,))
        await db.commit()
    finally:
        await db.close()


# --- K-line Cache ---

async def get_cached_klines(stock_code: str, limit: int = 250) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT date, open, high, low, close, volume FROM kline_cache WHERE stock_code = ? ORDER BY date DESC LIMIT ?",
            (stock_code, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        await db.close()


async def get_cached_latest_date(stock_code: str) -> str | None:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT MAX(date) as d FROM kline_cache WHERE stock_code = ?", (stock_code,)
        )
        row = await cursor.fetchone()
        return row["d"] if row and row["d"] else None
    finally:
        await db.close()


async def save_klines(stock_code: str, rows: list[dict]):
    if not rows:
        return
    db = await get_db()
    try:
        await db.executemany(
            "INSERT OR REPLACE INTO kline_cache (stock_code, date, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(stock_code, r["日期"], r["开盘"], r["最高"], r["最低"], r["收盘"], r.get("成交量", 0)) for r in rows],
        )
        await db.commit()
    finally:
        await db.close()


# --- Custom Alerts ---

async def get_custom_alerts(stock_code: str = None, enabled_only: bool = True) -> list[dict]:
    db = await get_db()
    try:
        where = []
        params = []
        if stock_code:
            where.append("stock_code = ?")
            params.append(stock_code)
        if enabled_only:
            where.append("enabled = 1 AND triggered = 0")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        cursor = await db.execute(f"SELECT * FROM custom_alerts {clause} ORDER BY created_at DESC", params)
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def add_custom_alert(stock_code: str, alert_type: str, price: float, message: str = ""):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO custom_alerts (stock_code, alert_type, price, message) VALUES (?, ?, ?, ?)",
            (stock_code, alert_type, price, message),
        )
        await db.commit()
    finally:
        await db.close()


async def mark_alert_triggered(alert_id: int):
    db = await get_db()
    try:
        await db.execute("UPDATE custom_alerts SET triggered = 1 WHERE id = ?", (alert_id,))
        await db.commit()
    finally:
        await db.close()


async def delete_custom_alert(alert_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM custom_alerts WHERE id = ?", (alert_id,))
        await db.commit()
    finally:
        await db.close()


# --- App Config ---

async def get_config(key: str) -> str | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT value FROM app_config WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row["value"] if row else None
    finally:
        await db.close()


async def set_config(key: str, value: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO app_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value),
        )
        await db.commit()
    finally:
        await db.close()


# ── 持仓逻辑 (thesis) ──
async def get_thesis(code: str) -> dict | None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM position_thesis WHERE code = ?", (code,))
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def list_theses() -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute("SELECT code, name, thesis, updated_at FROM position_thesis")
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def set_thesis(code: str, thesis: str, name: str = ""):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO position_thesis (code, name, thesis, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(code) DO UPDATE SET thesis = excluded.thesis, "
            "name = CASE WHEN excluded.name != '' THEN excluded.name ELSE position_thesis.name END, "
            "updated_at = CURRENT_TIMESTAMP",
            (code, name, thesis),
        )
        await db.commit()
    finally:
        await db.close()


async def delete_thesis(code: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM position_thesis WHERE code = ?", (code,))
        await db.commit()
    finally:
        await db.close()


# --- 问问市场 会话历史 CRUD ---

async def create_ask_session(title: str = "") -> int:
    db = await get_db()
    try:
        cur = await db.execute("INSERT INTO ask_session (title) VALUES (?)", (title[:80],))
        await db.commit()
        return cur.lastrowid
    finally:
        await db.close()


async def add_ask_message(session_id: int, role: str, content: str, meta: str = "") -> None:
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO ask_message (session_id, role, content, meta) VALUES (?, ?, ?, ?)",
            (session_id, role, content, meta or ""))
        await db.execute("UPDATE ask_session SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
        await db.commit()
    finally:
        await db.close()


async def list_ask_sessions(limit: int = 50) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT s.id, s.title, s.created_at, s.updated_at, "
            "(SELECT COUNT(*) FROM ask_message m WHERE m.session_id = s.id) AS msg_count "
            "FROM ask_session s "
            "WHERE EXISTS (SELECT 1 FROM ask_message m WHERE m.session_id = s.id) "
            "ORDER BY s.updated_at DESC LIMIT ?", (limit,))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_ask_session(session_id: int) -> dict | None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM ask_session WHERE id = ?", (session_id,))
        s = await cur.fetchone()
        if not s:
            return None
        cur = await db.execute(
            "SELECT role, content, meta, created_at FROM ask_message WHERE session_id = ? ORDER BY id", (session_id,))
        msgs = [dict(r) for r in await cur.fetchall()]
        return {**dict(s), "messages": msgs}
    finally:
        await db.close()


async def delete_ask_session(session_id: int) -> None:
    db = await get_db()
    try:
        await db.execute("DELETE FROM ask_message WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM ask_session WHERE id = ?", (session_id,))
        await db.commit()
    finally:
        await db.close()


# --- Unwind Plan CRUD ---

async def save_unwind_plan(stock_code: str, total_budget: float, status: str = "active"):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO unwind_plans (stock_code, total_budget, status)
               VALUES (?, ?, ?)
               ON CONFLICT(stock_code) DO UPDATE SET
                 total_budget = excluded.total_budget,
                 status = excluded.status,
                 updated_at = CURRENT_TIMESTAMP""",
            (stock_code, total_budget, status),
        )
        await db.commit()
    finally:
        await db.close()


async def get_unwind_plan(stock_code: str) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM unwind_plans WHERE stock_code = ?", (stock_code,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_all_unwind_plans() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM unwind_plans ORDER BY stock_code")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def delete_unwind_plan(stock_code: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM unwind_tranches WHERE stock_code = ?", (stock_code,))
        await db.execute("DELETE FROM unwind_plans WHERE stock_code = ?", (stock_code,))
        await db.commit()
    finally:
        await db.close()


async def update_unwind_used_budget(stock_code: str, used_budget: float):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE unwind_plans SET used_budget = ?, updated_at = CURRENT_TIMESTAMP WHERE stock_code = ?",
            (used_budget, stock_code),
        )
        await db.commit()
    finally:
        await db.close()


# --- Tranche CRUD ---

async def add_tranche(stock_code: str, idx: int, trigger_price: float, shares: int, requires_health: str = "any"):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO unwind_tranches (stock_code, idx, trigger_price, shares, requires_health)
               VALUES (?, ?, ?, ?, ?)""",
            (stock_code, idx, trigger_price, shares, requires_health),
        )
        await db.commit()
    finally:
        await db.close()


async def get_tranches(stock_code: str) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM unwind_tranches WHERE stock_code = ? ORDER BY idx",
            (stock_code,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_tranche(tranche_id: int) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM unwind_tranches WHERE id = ?", (tranche_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def clear_tranches(stock_code: str):
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM unwind_tranches WHERE stock_code = ? AND status = 'pending'",
            (stock_code,),
        )
        await db.commit()
    finally:
        await db.close()


async def mark_tranche_executed(tranche_id: int, executed_price: float):
    db = await get_db()
    try:
        await db.execute(
            """UPDATE unwind_tranches
               SET status = 'executed', executed_price = ?, executed_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (executed_price, tranche_id),
        )
        await db.commit()
    finally:
        await db.close()


# --- External assets (ETFs / funds / crypto / bots) ---

async def list_external_assets(include_closed: bool = False) -> list[dict]:
    """在持外部资产(全系统'我的持仓/看板'的唯一来源)。默认排除已平仓归档的(closed=1),
    一处过滤处处生效; include_closed=True 才返回全部(含归档)。"""
    db = await get_db()
    try:
        sql = "SELECT * FROM external_assets"
        if not include_closed:
            sql += " WHERE COALESCE(closed, 0) = 0"
        sql += " ORDER BY asset_type, id"
        cursor = await db.execute(sql)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def list_closed_external_assets() -> list[dict]:
    """已平仓归档的外部资产(供已实现盈亏统计)。"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM external_assets WHERE COALESCE(closed, 0) = 1 ORDER BY closed_date DESC")
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def close_external_asset(asset_id: int, realized_cny: float, closed_date: str) -> None:
    """平仓归档: 标记 closed=1, 记下平仓时已实现盈亏(CNY), 市值归零(退出在持)。"""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE external_assets SET closed = 1, closed_realized = ?, closed_date = ?, "
            "manual_value = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (round(float(realized_cny), 2), closed_date, asset_id),
        )
        await db.commit()
    finally:
        await db.close()


async def get_external_asset(asset_id: int) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM external_assets WHERE id = ?", (asset_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def add_external_asset(asset_type: str, code: str, name: str, platform: str,
                              cost_amount: float, shares: float | None = None,
                              manual_value: float | None = None, note: str = "",
                              okx_algo_id: str | None = None,
                              okx_bot_type: str | None = None,
                              annual_yield_rate: float | None = None,
                              start_date: str | None = None,
                              pending_amount: float | None = None,
                              purchase_fee_rate: float | None = None,
                              broker: str | None = None) -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO external_assets
               (asset_type, code, name, platform, cost_amount, shares, manual_value, note,
                okx_algo_id, okx_bot_type, annual_yield_rate, start_date, pending_amount, purchase_fee_rate, broker)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (asset_type, code, name, platform, cost_amount, shares, manual_value, note,
             okx_algo_id, okx_bot_type, annual_yield_rate, start_date, pending_amount or 0, purchase_fee_rate, broker),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_external_asset(asset_id: int, **kwargs):
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    db = await get_db()
    try:
        await db.execute(
            f"UPDATE external_assets SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*kwargs.values(), asset_id),
        )
        await db.commit()
    finally:
        await db.close()


async def delete_external_asset(asset_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM external_assets WHERE id = ?", (asset_id,))
        await db.commit()
    finally:
        await db.close()


async def reassign_external_actions(source_id: int, target_id: int) -> int:
    """把 source 资产的全部流水 + DCA 计划改挂到 target (合并重复资产用)。返回搬动的流水条数。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "UPDATE external_asset_actions SET asset_id = ? WHERE asset_id = ?", (target_id, source_id))
        await db.execute(
            "UPDATE dca_schedules SET asset_id = ? WHERE asset_id = ?", (target_id, source_id))
        await db.commit()
        return cur.rowcount
    finally:
        await db.close()


async def mark_tranche_sold_back(tranche_id: int, sold_price: float):
    """Record the sell-leg of a tranche (做T 回收). Tranche remains 'executed'
    so it stays in the ladder; status of sell leg is tracked via sold_back_price."""
    db = await get_db()
    try:
        await db.execute(
            """UPDATE unwind_tranches
               SET sold_back_price = ?, sold_back_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (sold_price, tranche_id),
        )
        await db.commit()
    finally:
        await db.close()


async def clear_tranche_sold_back(tranche_id: int):
    """Undo the sell leg."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE unwind_tranches SET sold_back_price = NULL, sold_back_at = NULL WHERE id = ?",
            (tranche_id,),
        )
        await db.commit()
    finally:
        await db.close()


# --- Position Actions Log ---

async def log_position_action(stock_code: str, action_type: str, price: float, shares: int,
                               tranche_id: int | None = None, note: str = ""):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO position_actions (stock_code, action_type, price, shares, tranche_id, note)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (stock_code, action_type, price, shares, tranche_id, note),
        )
        await db.commit()
    finally:
        await db.close()


async def get_position_actions(stock_code: str = None, limit: int = 200) -> list[dict]:
    db = await get_db()
    try:
        if stock_code:
            cursor = await db.execute(
                "SELECT * FROM position_actions WHERE stock_code = ? ORDER BY created_at DESC LIMIT ?",
                (stock_code, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM position_actions ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


# --- Position Action CRUD (full, not just append) ---

async def add_position_action(stock_code: str, action_type: str, price: float, shares: int,
                               trade_date: str = None, note: str = "", tranche_id: int = None,
                               fee: float | None = None, trade_time: str | None = None,
                               broker: str | None = None) -> int:
    """Insert a new action. Returns the new action id."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO position_actions
               (stock_code, action_type, price, shares, trade_date, note, tranche_id, fee, trade_time, broker)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (stock_code, action_type, price, shares, trade_date, note, tranche_id, fee, trade_time, broker),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_position_action(action_id: int, action_type: str = None, price: float = None,
                                  shares: int = None, trade_date: str = None, note: str = None,
                                  fee: float | None = None, fee_explicit: bool = False,
                                  trade_time: str = None, broker: str = None):
    """Update fields. fee_explicit=True 表示明确想改 fee (即使传 None 也写入 NULL).
    fee 默认 None + fee_explicit=False 不动 fee 列。trade_time/broker 传空串 "" 可清空。"""
    db = await get_db()
    try:
        sets, vals = [], []
        for k, v in [("action_type", action_type), ("price", price), ("shares", shares),
                     ("trade_date", trade_date), ("note", note)]:
            if v is not None:
                sets.append(f"{k} = ?")
                vals.append(v)
        if trade_time is not None:        # "" → 清空(NULL), "HH:MM" → 设值
            sets.append("trade_time = ?")
            vals.append(trade_time or None)
        if broker is not None:            # "" → 清空(回退持仓默认), 券商名 → 设值
            sets.append("broker = ?")
            vals.append(broker or None)
        if fee_explicit:
            sets.append("fee = ?")
            vals.append(fee)
        if not sets:
            return
        vals.append(action_id)
        await db.execute(
            f"UPDATE position_actions SET {', '.join(sets)} WHERE id = ?", vals
        )
        await db.commit()
    finally:
        await db.close()


async def delete_position_action(action_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM position_actions WHERE id = ?", (action_id,))
        await db.commit()
    finally:
        await db.close()


# --- Morning Briefings ---

async def save_briefing(stock_code: str, briefing_date: str, payload_json: str):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO morning_briefings (stock_code, briefing_date, payload_json)
               VALUES (?, ?, ?)
               ON CONFLICT(stock_code, briefing_date) DO UPDATE SET
                 payload_json = excluded.payload_json,
                 created_at = CURRENT_TIMESTAMP""",
            (stock_code, briefing_date, payload_json),
        )
        await db.commit()
    finally:
        await db.close()


async def get_briefings_for_date(briefing_date: str) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM morning_briefings WHERE briefing_date = ? ORDER BY stock_code",
            (briefing_date,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_latest_briefing(stock_code: str) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM morning_briefings WHERE stock_code = ? ORDER BY briefing_date DESC LIMIT 1",
            (stock_code,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# --- Cashflow Monthly ---

async def upsert_cashflow(month: str, income: float, fixed_cost: float, discretionary: float, notes: str = ""):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO cashflow_monthly (month, income, fixed_cost, discretionary, notes)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(month) DO UPDATE SET
                 income = excluded.income,
                 fixed_cost = excluded.fixed_cost,
                 discretionary = excluded.discretionary,
                 notes = excluded.notes,
                 updated_at = CURRENT_TIMESTAMP""",
            (month, float(income or 0), float(fixed_cost or 0), float(discretionary or 0), notes or ""),
        )
        await db.commit()
    finally:
        await db.close()


async def get_cashflow(month: str) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM cashflow_monthly WHERE month = ?", (month,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def list_cashflow(months: int = 12) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM cashflow_monthly ORDER BY month DESC LIMIT ?",
            (max(1, int(months)),),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def delete_cashflow(month: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM cashflow_monthly WHERE month = ?", (month,))
        await db.commit()
    finally:
        await db.close()



# --- External Asset Actions ---

async def list_external_actions(asset_id: int) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM external_asset_actions WHERE asset_id = ? ORDER BY trade_date, id",
            (asset_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def add_external_action(asset_id: int, action_type: str, amount: float = 0,
                              shares: float | None = None, unit_price: float | None = None,
                              trade_date: str | None = None, note: str = "",
                              status: str = "confirmed",
                              interest_part: float | None = None,
                              fee: float | None = None,
                              trade_time: str | None = None) -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO external_asset_actions
               (asset_id, action_type, amount, shares, unit_price, trade_date, status, note, interest_part, fee, trade_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (asset_id, action_type, float(amount or 0),
             float(shares) if shares is not None else None,
             float(unit_price) if unit_price is not None else None,
             trade_date, status, note or "",
             float(interest_part) if interest_part is not None else None,
             float(fee) if fee is not None else None, trade_time),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_external_action(action_id: int, **kwargs):
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs.keys())
    db = await get_db()
    try:
        await db.execute(
            f"UPDATE external_asset_actions SET {cols} WHERE id = ?",
            (*kwargs.values(), action_id),
        )
        await db.commit()
    finally:
        await db.close()


async def delete_external_action(action_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM external_asset_actions WHERE id = ?", (action_id,))
        await db.commit()
    finally:
        await db.close()


# --- DCA Schedules ---

async def list_dca_schedules(asset_id: int | None = None) -> list[dict]:
    db = await get_db()
    try:
        if asset_id is None:
            cursor = await db.execute("SELECT * FROM dca_schedules ORDER BY id")
        else:
            cursor = await db.execute(
                "SELECT * FROM dca_schedules WHERE asset_id = ? ORDER BY id",
                (asset_id,),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_dca_schedule(dca_id: int) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dca_schedules WHERE id = ?", (dca_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def add_dca_schedule(asset_id: int, mode: str, value: float,
                           frequency: str = "monthly",
                           day_of_month: int | None = None,
                           day_of_week: int | None = None,
                           next_due: str | None = None, note: str = "") -> int:
    # 旧 schema day_of_month 是 NOT NULL, 给 daily/weekly 模式时塞个占位 (1), fire 逻辑会按 frequency 忽略
    dom = int(day_of_month) if day_of_month is not None else 1
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO dca_schedules
               (asset_id, mode, value, frequency, day_of_month, day_of_week, next_due, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (asset_id, mode, float(value), frequency, dom,
             int(day_of_week) if day_of_week is not None else None,
             next_due, note or ""),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_dca_schedule(dca_id: int, **kwargs):
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs.keys())
    db = await get_db()
    try:
        await db.execute(
            f"UPDATE dca_schedules SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*kwargs.values(), dca_id),
        )
        await db.commit()
    finally:
        await db.close()


async def delete_dca_schedule(dca_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM dca_schedules WHERE id = ?", (dca_id,))
        await db.commit()
    finally:
        await db.close()


async def list_due_dca_schedules(today_str: str) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT * FROM dca_schedules
               WHERE status = 'active' AND next_due IS NOT NULL AND next_due <= ?""",
            (today_str,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


# --- Brokers ---

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
            await db.execute("UPDATE brokers SET is_default=0")
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

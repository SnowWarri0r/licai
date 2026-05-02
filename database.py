"""SQLite database setup and operations."""
from __future__ import annotations
import aiosqlite
from config import config

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

async def list_external_assets() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM external_assets ORDER BY asset_type, id")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
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
                              pending_amount: float | None = None) -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO external_assets
               (asset_type, code, name, platform, cost_amount, shares, manual_value, note,
                okx_algo_id, okx_bot_type, annual_yield_rate, start_date, pending_amount)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (asset_type, code, name, platform, cost_amount, shares, manual_value, note,
             okx_algo_id, okx_bot_type, annual_yield_rate, start_date, pending_amount or 0),
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
                               trade_date: str = None, note: str = "", tranche_id: int = None) -> int:
    """Insert a new action. Returns the new action id."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO position_actions
               (stock_code, action_type, price, shares, trade_date, note, tranche_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (stock_code, action_type, price, shares, trade_date, note, tranche_id),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_position_action(action_id: int, action_type: str = None, price: float = None,
                                  shares: int = None, trade_date: str = None, note: str = None):
    db = await get_db()
    try:
        sets, vals = [], []
        for k, v in [("action_type", action_type), ("price", price), ("shares", shares),
                     ("trade_date", trade_date), ("note", note)]:
            if v is not None:
                sets.append(f"{k} = ?")
                vals.append(v)
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

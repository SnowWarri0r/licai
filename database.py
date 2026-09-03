"""SQLite database setup and operations."""
from __future__ import annotations
import aiosqlite
from config import config

# 观察池的默认分组。它是一个真实分组(watchlist_group 里有行), 不是"没有分组" ——
# 一只票可以同时在「自选」和「金矿」里。加进观察池时自动落这个组; 取消到一个组都不剩时
# 也退回它, 保证任何一只票至少能在某个分组视图里被看见。
DEFAULT_WATCH_GROUP = "自选"


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
    amount REAL DEFAULT 0,               -- 成交额(元); 副图切「成交额」用, 源缺则 0
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

-- 每日组合市值快照(收盘后记一次): 机器人/加密等无价史资产的真实市值序列,
-- 净值曲线用它替代成本基线(快照攒得越久, 这部分曲线越真)
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snap_date TEXT PRIMARY KEY,          -- YYYY-MM-DD
    total_value REAL NOT NULL,
    by_asset TEXT,                       -- JSON {"EXT:<id>"|"A:<code>": value}
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 自选观察池(在跟踪但未必持有的票)。分组不在这张表上, 见 watchlist_group。
CREATE TABLE IF NOT EXISTS watchlist (
    stock_code TEXT PRIMARY KEY,
    stock_name TEXT,
    added_at TEXT,                       -- YYYY-MM-DD
    added_price REAL,                    -- 加自选时现价(看"自选以来"涨跌)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sort_order REAL DEFAULT 0            -- 全局手动位次(「全部」视图用; 与分组无关)
);

-- 观察池的分组成员表: 一行 = 这只票在这个分组里。一票多组。
-- 为什么不是 watchlist 上的一列: 单值列只能表达"属于一个组", 而同一只票常常既属于
-- 「高端内存」又属于「博弈」。组内手动位次同理 —— 每个组各有一份顺序, 挂在 watchlist
-- 上的话一只票只能有一个位次, 进第二个组就必然跟第一个组抢。
-- 默认组「自选」在这张表里没有行: 一只票在本表里查不到任何分组 = 还没归组。
CREATE TABLE IF NOT EXISTS watchlist_group (
    stock_code TEXT NOT NULL,
    grp TEXT NOT NULL,                   -- 分组名(非空; 空串是"未归组", 不占行)
    group_order REAL DEFAULT 0,          -- 该组内的手动位次(各组独立)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, grp)
);

-- 全市场代码↔名称字典(个股 + 场内基金): 搜股的匹配底表。
-- 现拉一次要 20s+(stock_info_a_code_name 17 个分页 + ETF 表), 每次重启都重付, 首次
-- 搜索被阻塞几十秒。落盘后重启即时可用, 过期只在后台刷。
CREATE TABLE IF NOT EXISTS symbol_dict (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    is_etf INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 板块成交额份额逐日档案(收盘后定格): 看资金往哪个板块聚拢/撤离
CREATE TABLE IF NOT EXISTS sector_share_history (
    snap_date TEXT,                      -- YYYY-MM-DD
    board TEXT,                          -- 同花顺行业板块名
    amount_yi REAL,                      -- 板块总成交额(亿)
    share_pct REAL,                      -- 占全市场板块总成交比重(%)
    PRIMARY KEY (snap_date, board)
);

-- 市场量能逐日档案(沪/深/创业/科创 量+额): 东财可达时回填历史, 收盘后定格当日
CREATE TABLE IF NOT EXISTS market_volume_history (
    snap_date TEXT,                      -- YYYY-MM-DD
    market TEXT,                         -- 沪/深/创业/科创
    vol REAL,                            -- 成交量(股)
    amt REAL,                            -- 成交额(元)
    PRIMARY KEY (snap_date, market)
);

-- 市场情绪逐日档案(交易日收盘后定格, 情绪周期时间轴用)
CREATE TABLE IF NOT EXISTS sentiment_history (
    snap_date TEXT PRIMARY KEY,          -- YYYY-MM-DD
    n_zt INTEGER, n_dt INTEGER, n_zb INTEGER,
    zbl_rate REAL, max_lb INTEGER, money_effect REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 逐日逐只涨停档案。sentiment_history 只记「几个涨停」, 这里记「涨停的质量」:
-- 封单额(买一挂单额, 真实盘口量) + 首次封板时刻 + 炸板次数。52 个涨停配 57 亿封单
-- 和 52 个涨停配 20 亿封单是两个完全不同的盘, 只数一样看不出来。
-- seal_amount 已用新浪盘口买一逐只验过(东财 49 只零偏离; 开盘啦历史 79 只与东财全等)。
CREATE TABLE IF NOT EXISTS limit_up_pool (
    snap_date TEXT NOT NULL,             -- YYYY-MM-DD
    stock_code TEXT NOT NULL,
    name TEXT,
    seal_amount REAL,                    -- 封单额(元) = 买一量 × 买一价
    first_seal TEXT,                     -- 首次封板 HH:MM:SS ('09:25:00'=集合竞价一字板)
    last_seal TEXT,                      -- 最后封板 HH:MM:SS (与首封不同 = 中间开过板)
    lb_count INTEGER,                    -- 连板数
    broken_times INTEGER,                -- 炸板次数(仅东财有, 开盘啦那份留空)
    zt_days INTEGER, zt_ct INTEGER,      -- N 天 M 板(仅东财)
    industry TEXT, theme TEXT,
    amount REAL,                         -- 成交额(元)
    float_mv REAL,                       -- 流通市值(元, 仅东财)
    turnover REAL,                       -- 换手%(仅东财)
    pct REAL,                            -- 当日涨跌幅%
    source TEXT,                         -- em(日常, 字段全) | kpl(一次性历史回填)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snap_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_lup_date ON limit_up_pool(snap_date);

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

-- 一轮 agent 执行的过程流水。答案本身进 ask_message, 这张表存的是"跑的过程":
-- 调了哪些工具、画了什么图、中途报了什么错。
-- 为什么要落盘: run 本来只活在内存里, 服务一重启(改代码/掉电)在跑的那轮就地消失 ——
-- 界面上是一行永远转不完的"分析中", 而且连它已经查过什么都看不到。落了盘之后, 重启后
-- 那一轮会被标成 interrupted, 已经跑出来的步骤还在, 至少知道它走到哪一步。
CREATE TABLE IF NOT EXISTS ask_run (
    run_id TEXT PRIMARY KEY,
    session_id INTEGER,
    scope TEXT DEFAULT '',                     -- market | stock:<code>
    question TEXT DEFAULT '',
    status TEXT DEFAULT 'running',             -- running | done | interrupted
    answered INTEGER DEFAULT 0,
    events TEXT DEFAULT '[]',                  -- JSON 数组, 下标即游标
    started_at REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_ask_run_started ON ask_run(started_at DESC);

-- 工具缺口台账: agent 调工具时取不到数就记一笔, 攒成清单。
-- 为什么要它: 实测 agent 的错误质量集中在取数层而不是推理层 —— 一次典型的翻车是
-- get_news 对美股返回了 10 条(所以模型以为消息面已覆盖), 但最新一条比异动早三天;
-- 另一次是美股行情把 开/高/低 全返 0(源里其实有)。这类"看着成功其实没数"的情况
-- 不记账就永远只能等用户发现。按 (工具, 缺口类型, 市场) 聚合计数, 不存每次调用。
CREATE TABLE IF NOT EXISTS tool_gap (
    tool TEXT NOT NULL,
    kind TEXT NOT NULL,                        -- error | empty | stale | zero_fields
    market TEXT NOT NULL DEFAULT '',           -- A | HK | US | '' (与标的无关的工具)
    hits INTEGER NOT NULL DEFAULT 0,
    detail TEXT DEFAULT '',                    -- 最近一次的说明 (如 "latest_time 早 3 天")
    sample TEXT DEFAULT '',                    -- 最近一次的入参样本
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tool, kind, market)
);

-- 从"用户纠正"沉淀出来的候选规则, 人批准后才进 system prompt。
-- 为什么要闸门: prompt 里已有 56 条规则, 自动往里加最容易造成规则互撞, 而且 prompt
-- 对单个词都敏感(曾因词表缺"主题/标题"两个词让模型吞掉首句)。所以只自动"提案", 不自动
-- 生效; status=pending 的一律不进 prompt。
CREATE TABLE IF NOT EXISTS prompt_rule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,                       -- 【】里的标题
    body TEXT NOT NULL,                        -- 规则正文(正向陈述)
    status TEXT NOT NULL DEFAULT 'pending',    -- pending | active | rejected
    evidence TEXT DEFAULT '',                  -- 触发它的那次纠正原文(截断)
    session_id INTEGER,                        -- 来自哪个会话
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prompt_rule_status ON prompt_rule(status, id);
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
        # Migration: kline_cache 加成交额列(老库只有 volume)
        cursor = await db.execute("PRAGMA table_info(kline_cache)")
        kc_cols = {row[1] for row in await cursor.fetchall()}
        if "amount" not in kc_cols:
            await db.execute("ALTER TABLE kline_cache ADD COLUMN amount REAL DEFAULT 0")
        # Migration: 自选加「全局手动排序」(老库建表时没有这列)
        cursor = await db.execute("PRAGMA table_info(watchlist)")
        wl_cols = {row[1] for row in await cursor.fetchall()}
        if "sort_order" not in wl_cols:
            await db.execute("ALTER TABLE watchlist ADD COLUMN sort_order REAL DEFAULT 0")
            # 老数据按加入时间倒序给初始位次, 保持用户当前看到的顺序不变
            await db.execute(
                "UPDATE watchlist SET sort_order = (SELECT COUNT(*) FROM watchlist w2 "
                "WHERE w2.created_at > watchlist.created_at)")
        # Migration: 分组从 watchlist.grp/group_order 两列搬到 watchlist_group 关联表(一票多组)。
        # 搬完必须把老列去掉: 留着就是第二本账, 以后谁读到的都是搬迁那一刻的快照。而删列
        # 之前得先把上面的 ADD COLUMN 兜底去掉, 否则下次启动又原样加回来。
        if "grp" in wl_cols:
            await db.execute(
                "INSERT OR IGNORE INTO watchlist_group (stock_code, grp, group_order) "
                "SELECT stock_code, grp, COALESCE(group_order, 0) FROM watchlist "
                "WHERE COALESCE(grp, '') <> ''")
            cursor = await db.execute(
                "SELECT (SELECT COUNT(*) FROM watchlist WHERE COALESCE(grp,'') <> ''), "
                "(SELECT COUNT(*) FROM watchlist w JOIN watchlist_group g "
                " ON g.stock_code = w.stock_code AND g.grp = w.grp "
                " WHERE COALESCE(w.grp,'') <> '')")
            expect, moved = await cursor.fetchone()
            if moved >= expect:                      # 逐行核对搬到位了才动老列
                try:
                    await db.execute("ALTER TABLE watchlist DROP COLUMN grp")
                    await db.execute("ALTER TABLE watchlist DROP COLUMN group_order")
                except Exception:
                    # SQLite < 3.35 没有 DROP COLUMN: 列留着但没人读。清空值, 否则下次
                    # 启动又照着老列搬一遍 —— 会把用户后来移出的分组复活。
                    await db.execute("UPDATE watchlist SET grp = ''")
        # Migration(一次性): 把「还没归组」的票补进默认组「自选」。
        # 「自选」是一个真实分组, 不是"没有分组" —— 一只票可以同时在「自选」和「金矿」里。
        # 只能跑一次: 跑第二遍会把用户后来从「自选」里取消勾选的票又塞回去(他明确只想留
        # 「金矿」)。所以拿 app_config 一个键当一次性闸门, 而不是"看它有没有分组"。
        cursor = await db.execute(
            "SELECT value FROM app_config WHERE key = 'watchlist_default_group_filled'")
        if not await cursor.fetchone():
            await db.execute(
                "INSERT OR IGNORE INTO watchlist_group (stock_code, grp, group_order) "
                "SELECT w.stock_code, ?, COALESCE(w.sort_order, 0) FROM watchlist w "
                "WHERE NOT EXISTS (SELECT 1 FROM watchlist_group g WHERE g.stock_code = w.stock_code)",
                (DEFAULT_WATCH_GROUP,))
            await db.execute(
                "INSERT OR IGNORE INTO app_config (key, value) VALUES "
                "('watchlist_default_group_filled', '1')")
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
            "SELECT date, open, high, low, close, volume, COALESCE(amount,0) AS amount "
            "FROM kline_cache WHERE stock_code = ? ORDER BY date DESC LIMIT ?",
            (stock_code, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        await db.close()


async def get_cached_amounts(codes: list[str], since: str) -> dict[str, dict[str, float]]:
    """一次取多只票的逐日成交额 → {日期: {代码: 成交额}}。给"这条概念线前几天多少钱"用。

    逐只查会打出上百次往返(榜单 100 只 × 一周), 这里一条 IN 查询取回。
    """
    if not codes:
        return {}
    db = await get_db()
    try:
        out: dict[str, dict[str, float]] = {}
        for i in range(0, len(codes), 400):          # SQLite 变量上限 999, 分批
            chunk = codes[i:i + 400]
            q = ",".join("?" * len(chunk))
            cur = await db.execute(
                f"SELECT date, stock_code, COALESCE(amount,0) AS amount FROM kline_cache "
                f"WHERE stock_code IN ({q}) AND date >= ? AND COALESCE(amount,0) > 0",
                (*chunk, since))
            for r in await cur.fetchall():
                out.setdefault(r["date"], {})[r["stock_code"]] = float(r["amount"])
        return out
    finally:
        await db.close()


# ---- agent 执行流水(ask_run) ----

async def save_ask_run(run_id: str, session_id: int, scope: str, question: str, started_at: float):
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO ask_run (run_id, session_id, scope, question, status, events, "
            "started_at, updated_at) VALUES (?, ?, ?, ?, 'running', '[]', ?, ?)",
            (run_id, session_id, scope, question, started_at, started_at))
        await db.commit()
    finally:
        await db.close()


async def update_ask_run(run_id: str, events_json: str, status: str, answered: bool, now: float):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE ask_run SET events = ?, status = ?, answered = ?, updated_at = ? WHERE run_id = ?",
            (events_json, status, 1 if answered else 0, now, run_id))
        await db.commit()
    finally:
        await db.close()


async def get_ask_run(run_id: str) -> dict | None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM ask_run WHERE run_id = ?", (run_id,))
        r = await cur.fetchone()
        return dict(r) if r else None
    finally:
        await db.close()


async def list_ask_runs(limit: int = 20) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT run_id, session_id, scope, question, status, answered, started_at, updated_at "
            "FROM ask_run ORDER BY started_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def interrupt_running_ask_runs() -> int:
    """启动时把上一进程留下的 running 全部标成 interrupted。

    进程一没, 那些 asyncio task 就不存在了 —— 留着 running 会让界面一直转, 而它永远不会有
    结果。这里不做"自动重跑": 重跑要再烧一遍 token, 该不该跑由人决定。
    """
    db = await get_db()
    try:
        cur = await db.execute("UPDATE ask_run SET status = 'interrupted' WHERE status = 'running'")
        await db.commit()
        return cur.rowcount or 0
    finally:
        await db.close()


async def prune_ask_runs(keep: int = 200) -> None:
    """只留最近 keep 条流水(答案在 ask_message 里, 这张表是过程)。"""
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM ask_run WHERE run_id NOT IN "
            "(SELECT run_id FROM ask_run ORDER BY started_at DESC LIMIT ?)", (keep,))
        await db.commit()
    finally:
        await db.close()


async def get_cached_closes(codes: list[str], day: str) -> dict[str, float]:
    """每只票在 day(含)之前最近一个交易日的收盘价 → {代码: 收盘}。回溯估值用。

    停牌/那天没数据的票取更早那根 —— 按当时能看到的最后价格算, 比直接漏掉整只票诚实。
    """
    if not codes:
        return {}
    db = await get_db()
    try:
        out: dict[str, float] = {}
        for i in range(0, len(codes), 400):
            chunk = codes[i:i + 400]
            q = ",".join("?" * len(chunk))
            cur = await db.execute(
                f"SELECT stock_code, close FROM kline_cache k WHERE stock_code IN ({q}) AND date <= ? "
                f"AND date = (SELECT MAX(date) FROM kline_cache k2 "
                f"            WHERE k2.stock_code = k.stock_code AND k2.date <= ?)",
                (*chunk, day, day))
            for r in await cur.fetchall():
                if r["close"]:
                    out[r["stock_code"]] = float(r["close"])
        return out
    finally:
        await db.close()


async def get_position_actions_until(day: str) -> dict[str, list[dict]]:
    """截至 day(含)的全部持仓动作, 按股票代码分组(时间升序)。回放账本还原当时的持股。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM position_actions WHERE COALESCE(trade_date, date(created_at)) <= ? "
            "ORDER BY COALESCE(trade_date, date(created_at)), id", (day,))
        out: dict[str, list[dict]] = {}
        for r in await cur.fetchall():
            out.setdefault(r["stock_code"], []).append(dict(r))
        return out
    finally:
        await db.close()


async def get_snapshot_on_or_before(day: str) -> dict | None:
    """day(含)之前最近一条组合快照。外部资产(基金/理财/现金)没有可回溯的价格历史,
    只有这条快照记了当时的真实市值。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT snap_date, total_value, by_asset FROM portfolio_snapshots "
            "WHERE snap_date <= ? ORDER BY snap_date DESC LIMIT 1", (day,))
        r = await cur.fetchone()
        return dict(r) if r else None
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
            "INSERT OR REPLACE INTO kline_cache (stock_code, date, open, high, low, close, volume, amount) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(stock_code, r["日期"], r["开盘"], r["最高"], r["最低"], r["收盘"],
              r.get("成交量", 0), r.get("成交额", 0) or 0) for r in rows],
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
    """删资产时连带清掉它的定投计划和流水。

    不连带的后果实测过: 计划还 active, 每天往已不存在的 asset_id 写一条 pending
    流水, settle-pending 又扫不到(它只遍历现存资产), 于是无限攒垃圾。
    流水离了资产在 UI 上也已经不可达。合并资产走 reassign_external_actions
    先搬走再删, 所以这里删到的只会是真正的孤儿。
    """
    db = await get_db()
    try:
        await db.execute("DELETE FROM dca_schedules WHERE asset_id = ?", (asset_id,))
        await db.execute("DELETE FROM external_asset_actions WHERE asset_id = ?", (asset_id,))
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


# ---- 每日组合市值快照 ----

async def save_portfolio_snapshot(snap_date: str, total_value: float, by_asset_json: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO portfolio_snapshots (snap_date, total_value, by_asset) VALUES (?, ?, ?)",
            (snap_date, total_value, by_asset_json))
        await db.commit()
    finally:
        await db.close()


async def list_portfolio_snapshots(limit: int = 500) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT snap_date, total_value, by_asset FROM portfolio_snapshots ORDER BY snap_date DESC LIMIT ?",
            (limit,))
        rows = await cur.fetchall()
        return [{"snap_date": r[0], "total_value": r[1], "by_asset": r[2]} for r in rows]
    finally:
        await db.close()


# ---- 板块成交额份额档案 ----

async def save_sector_shares(snap_date: str, rows: list[dict]):
    db = await get_db()
    try:
        await db.executemany(
            "INSERT OR REPLACE INTO sector_share_history (snap_date, board, amount_yi, share_pct) VALUES (?, ?, ?, ?)",
            [(snap_date, r["board"], r["amount_yi"], r["share_pct"]) for r in rows])
        await db.commit()
    finally:
        await db.close()


async def get_sector_shares_on(snap_date: str) -> dict:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT board, share_pct FROM sector_share_history WHERE snap_date = ?", (snap_date,))
        return {r[0]: r[1] for r in await cur.fetchall()}
    finally:
        await db.close()


async def save_market_volume_history(market: str, rows: list):
    """rows: [(YYYY-MM-DD, vol股, amt元)], INSERT OR REPLACE 幂等回填。"""
    if not rows:
        return
    db = await get_db()
    try:
        await db.executemany(
            "INSERT OR REPLACE INTO market_volume_history (snap_date, market, vol, amt) VALUES (?, ?, ?, ?)",
            [(d, market, v, a) for d, v, a in rows])
        await db.commit()
    finally:
        await db.close()


async def get_market_volume_history(markets: list, days: int = 20) -> dict:
    """→ {market: [(date, amt元)]} 升序, 近 days 个档案日。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT snap_date, market, amt FROM market_volume_history "
            "WHERE market IN (%s) ORDER BY snap_date DESC LIMIT ?" % ",".join("?" * len(markets)),
            (*markets, days * len(markets)))
        out: dict = {m: [] for m in markets}
        for d, m, a in await cur.fetchall():
            out[m].append((d, a))
        for m in out:
            out[m].reverse()
        return out
    finally:
        await db.close()


async def list_sector_share_dates(limit: int = 20) -> list[str]:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT DISTINCT snap_date FROM sector_share_history ORDER BY snap_date DESC LIMIT ?", (limit,))
        return [r[0] for r in await cur.fetchall()]
    finally:
        await db.close()


# ---- 自选观察池 ----

async def add_watchlist(code: str, name: str, price: float | None):
    import datetime
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO watchlist (stock_code, stock_name, added_at, added_price) VALUES (?, ?, ?, ?)",
            (code, name, datetime.date.today().isoformat(), price))
        # 默认落进「自选」组。它是真实分组, 所以这一步会真写一行 —— 不写的话新加的票
        # 一个组都不在, 只能在「全部」里看到。
        cur = await db.execute(
            "SELECT COALESCE(MAX(group_order),0)+1 FROM watchlist_group WHERE grp=?",
            (DEFAULT_WATCH_GROUP,))
        nxt = (await cur.fetchone())[0] or 0
        await db.execute(
            "INSERT OR IGNORE INTO watchlist_group (stock_code, grp, group_order) VALUES (?,?,?)",
            (code, DEFAULT_WATCH_GROUP, nxt))
        await db.commit()
    finally:
        await db.close()


async def remove_watchlist(code: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM watchlist WHERE stock_code = ?", (code,))
        # 分组成员一起删: 留着就成了孤儿行, 以后这只票重新加回自选会连着旧分组一起复活
        await db.execute("DELETE FROM watchlist_group WHERE stock_code = ?", (code,))
        await db.commit()
    finally:
        await db.close()


async def list_watchlist() -> list[dict]:
    """自选列表。按全局位次排(位次相同则退回加入时间倒序, 与改造前一致)。
    分组不参与全局排序 —— 一只票可以在好几个组里, 「全部」视图只有一种顺序。
    groups=所属分组名(空列表=还没归组, 即默认组「自选」);
    group_orders={分组名: 组内位次}, 前端选中某个组时按它排。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT stock_code, stock_name, added_at, added_price, COALESCE(sort_order,0) "
            "FROM watchlist ORDER BY COALESCE(sort_order,0), created_at DESC")
        rows = await cur.fetchall()
        cur = await db.execute(
            "SELECT stock_code, grp, COALESCE(group_order,0) FROM watchlist_group ORDER BY grp")
        gm: dict[str, list[str]] = {}
        om: dict[str, dict[str, float]] = {}
        for code, grp, order in await cur.fetchall():
            gm.setdefault(code, []).append(grp)
            om.setdefault(code, {})[grp] = order or 0
        return [{"code": r[0], "name": r[1], "added_at": r[2], "added_price": r[3],
                 "sort_order": r[4] or 0,
                 "groups": gm.get(r[0], []), "group_orders": om.get(r[0], {})}
                for r in rows]
    finally:
        await db.close()


async def set_watchlist_groups(code: str, groups: list[str]) -> list[str]:
    """整套覆盖这只票的分组(前端本地勾完提交全集, 幂等)。返回落库后的分组表。

    已经在的组保留原位次 —— 重写位次的话勾一个新组会把这只票在老组里挪到末尾。
    新进的组排到该组末尾。全局位次 sort_order 一概不动: 在「全部」视图里改分组标签
    不该让这只票凭空跳位置。
    传空列表 = 退回默认组「自选」(而不是一个组都不在: 那样这只票只能在「全部」里看到)。
    """
    want = []
    for g in groups or []:
        g = (str(g) or "").strip()
        if g and g not in want:
            want.append(g)
    if not want:
        want = [DEFAULT_WATCH_GROUP]
    db = await get_db()
    try:
        cur = await db.execute("SELECT grp FROM watchlist_group WHERE stock_code=?", (code,))
        have = {r[0] for r in await cur.fetchall()}
        for g in have - set(want):
            await db.execute("DELETE FROM watchlist_group WHERE stock_code=? AND grp=?", (code, g))
        for g in want:
            if g in have:
                continue
            cur = await db.execute(
                "SELECT COALESCE(MAX(group_order),0)+1 FROM watchlist_group WHERE grp=?", (g,))
            nxt = (await cur.fetchone())[0] or 0
            await db.execute(
                "INSERT OR IGNORE INTO watchlist_group (stock_code, grp, group_order) "
                "VALUES (?,?,?)", (code, g, nxt))
        await db.commit()
        return want
    finally:
        await db.close()


async def reorder_watchlist(codes: list[str], scope: str = "global", group: str | None = None) -> None:
    """按给定顺序覆盖写位次。

    scope='global': 写 sort_order(「全部」视图拖动), 不碰分组;
    scope='group' : 写这些票在 group 里的位次, 顺带把还不在该组的加进去(拖进组=入组)。
                    只动这一个组的成员关系, 票在别的组里待着不受影响。
    两套位次独立, 所以在组内调顺序不会打乱「全部」视图, 反之亦然。
    """
    db = await get_db()
    try:
        for i, c in enumerate(codes or []):
            if scope == "group":
                if not (group or ""):
                    continue               # 组名为空是脏入参, 不建这种行(默认组叫「自选」)
                await db.execute(
                    "INSERT INTO watchlist_group (stock_code, grp, group_order) VALUES (?,?,?) "
                    "ON CONFLICT(stock_code, grp) DO UPDATE SET group_order=excluded.group_order",
                    (c, group, float(i)))
            else:
                await db.execute("UPDATE watchlist SET sort_order=? WHERE stock_code=?",
                                 (float(i), c))
        await db.commit()
    finally:
        await db.close()


# ---- 全市场代码↔名称字典(搜股底表, 落盘避免每次重启重拉 20s+) ----

async def save_symbol_dict(rows: list[tuple]) -> int:
    """rows: [(code, name, is_etf)]。整表覆盖式 upsert, 不删旧码(退市票留着仍可搜历史)。"""
    if not rows:
        return 0
    db = await get_db()
    try:
        await db.executemany(
            "INSERT INTO symbol_dict (code, name, is_etf, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name, is_etf=excluded.is_etf, "
            "updated_at=CURRENT_TIMESTAMP",
            [(str(c), str(n), int(e)) for c, n, e in rows])
        await db.commit()
        return len(rows)
    finally:
        await db.close()


async def load_symbol_dict() -> tuple[list[tuple], str | None]:
    """→ ([(code, name, is_etf)], 最新更新时间)。空表返回 ([], None)。"""
    db = await get_db()
    try:
        cur = await db.execute("SELECT code, name, COALESCE(is_etf,0) FROM symbol_dict")
        rows = [(r[0], r[1], int(r[2])) for r in await cur.fetchall()]
        cur = await db.execute("SELECT MAX(updated_at) FROM symbol_dict")
        ts = (await cur.fetchone())[0]
        return rows, ts
    finally:
        await db.close()


# ---- 市场情绪逐日档案 ----

async def save_sentiment_day(row: dict):
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO sentiment_history (snap_date, n_zt, n_dt, n_zb, zbl_rate, max_lb, money_effect)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row["date"], row.get("n_zt"), row.get("n_dt"), row.get("n_zb"),
             row.get("zbl_rate"), row.get("max_lb"), row.get("money_effect")))
        await db.commit()
    finally:
        await db.close()


async def list_sentiment_history(limit: int = 60) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT snap_date, n_zt, n_dt, n_zb, zbl_rate, max_lb, money_effect"
            " FROM sentiment_history ORDER BY snap_date DESC LIMIT ?", (limit,))
        rows = await cur.fetchall()
        return [{"date": r[0], "n_zt": r[1], "n_dt": r[2], "n_zb": r[3],
                 "zbl_rate": r[4], "max_lb": r[5], "money_effect": r[6]} for r in rows]
    finally:
        await db.close()


_LUP_COLS = ("snap_date", "stock_code", "name", "seal_amount", "first_seal", "last_seal",
             "lb_count", "broken_times", "zt_days", "zt_ct", "industry", "theme",
             "amount", "float_mv", "turnover", "pct", "source")


async def save_limit_up_pool(rows: list[dict]) -> int:
    """写入逐只涨停档案。

    **东财可以盖开盘啦, 开盘啦不许盖东财** —— 东财那份多了炸板次数/换手/流通市值/N天M板,
    历史回填要是后跑就会把这些列刷成空。所以冲突时只在「同源」或「来源是 em」时才更新。
    """
    if not rows:
        return 0
    db = await get_db()
    try:
        n = 0
        for r in rows:
            cur = await db.execute(
                f"INSERT INTO limit_up_pool ({','.join(_LUP_COLS)})"
                f" VALUES ({','.join('?' * len(_LUP_COLS))})"
                " ON CONFLICT(snap_date, stock_code) DO UPDATE SET"
                "   name=excluded.name, seal_amount=excluded.seal_amount,"
                "   first_seal=excluded.first_seal, last_seal=excluded.last_seal,"
                "   lb_count=excluded.lb_count, broken_times=excluded.broken_times,"
                "   zt_days=excluded.zt_days, zt_ct=excluded.zt_ct,"
                "   industry=excluded.industry, theme=excluded.theme, amount=excluded.amount,"
                "   float_mv=excluded.float_mv, turnover=excluded.turnover, pct=excluded.pct,"
                "   source=excluded.source"
                " WHERE excluded.source = 'em' OR limit_up_pool.source = excluded.source",
                tuple(r.get(c) for c in _LUP_COLS))
            n += cur.rowcount or 0
        await db.commit()
        return n
    finally:
        await db.close()


async def get_limit_up_pool(day: str) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute(
            f"SELECT {','.join(_LUP_COLS)} FROM limit_up_pool WHERE snap_date = ?"
            " ORDER BY seal_amount DESC", (day,))
        return [dict(zip(_LUP_COLS, r)) for r in await cur.fetchall()]
    finally:
        await db.close()


async def list_limit_up_days(limit: int = 60, end: str | None = None) -> list[dict]:
    """逐日汇总: 只数 / 封单合计 / 最高连板。序列用, 不拉明细。"""
    db = await get_db()
    try:
        sql = ("SELECT snap_date, COUNT(*), SUM(seal_amount), MAX(lb_count)"
               " FROM limit_up_pool")
        args: tuple = ()
        if end:
            sql += " WHERE snap_date <= ?"
            args = (end,)
        sql += " GROUP BY snap_date ORDER BY snap_date DESC LIMIT ?"
        cur = await db.execute(sql, args + (limit,))
        rows = await cur.fetchall()
        return [{"date": r[0], "n": r[1], "seal_sum": r[2] or 0, "max_lb": r[3]}
                for r in reversed(rows)]
    finally:
        await db.close()


async def get_next_bars(codes: list[str], day: str) -> dict[str, dict]:
    """每只票在 day **之后**第一根日线 → {代码: {date, open, close}}。

    封单额兑现度回测用: 涨停发生在 day, 要看的是次日开盘接不接、收盘守不守得住。
    严格大于 day, 所以停牌一天就顺延到复牌那根 —— 调用方按返回的 date 自己决定要不要采信。
    """
    if not codes:
        return {}
    db = await get_db()
    try:
        out: dict[str, dict] = {}
        for i in range(0, len(codes), 400):
            chunk = codes[i:i + 400]
            q = ",".join("?" * len(chunk))
            cur = await db.execute(
                f"SELECT stock_code, date, open, close FROM kline_cache WHERE stock_code IN ({q})"
                " AND date > ? ORDER BY stock_code, date", (*chunk, day))
            for code, d, o, c in await cur.fetchall():
                if code not in out:          # 已按 date 升序, 第一条即次日
                    out[code] = {"date": d, "open": o, "close": c}
        return out
    finally:
        await db.close()


async def limit_up_pool_coverage() -> dict:
    """回填进度/来源构成。凭空说"有历史"没用, 要能报出到底攒了多少天。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT COUNT(DISTINCT snap_date), COUNT(*), MIN(snap_date), MAX(snap_date)"
            " FROM limit_up_pool")
        days, rows, lo, hi = await cur.fetchone()
        cur = await db.execute(
            "SELECT source, COUNT(DISTINCT snap_date) FROM limit_up_pool GROUP BY source")
        by_src = {r[0]: r[1] for r in await cur.fetchall()}
        return {"days": days or 0, "rows": rows or 0, "first": lo, "last": hi,
                "days_by_source": by_src}
    finally:
        await db.close()

"""回看某一天的穿透结构。

为什么要它: 敞口是按当下持仓算的, 已清仓的票根本不进来 —— 而复盘时想问的恰恰是"我卖掉的
那两注当时其实是同一块风险吗"。8-25 清掉 山东黄金 + 兴业银锡 之后, 当下的穿透里一点痕迹都
没有了。

两部分的来路不同, 所以分开测:
  · A股回放账本(截至当天)× 当天收盘 —— 账本是权威的, 而且**事后补录**的成交也算得进去。
    不能拿当天的组合快照当持股清单: 快照写于当时, 那天晚上才补录的成交它不知道。
  · 基金/理财/现金没有可回溯的价格历史, 只有每日组合快照记了当时的真实市值。
"""
import asyncio
import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr("config.config.db_path", path)
    from database import init_db
    asyncio.run(init_db())
    yield path
    os.unlink(path)


def _seed(path):
    con = sqlite3.connect(path)
    try:
        # 8-20 建仓两只, 8-25 清掉黄金那只(卖出在 as_of 之后就不该影响 8-24 的结果)
        con.executemany(
            "INSERT INTO position_actions (stock_code, action_type, price, shares, trade_date) "
            "VALUES (?,?,?,?,?)",
            [("600547", "BUY", 36.0, 700, "2026-08-20"),
             ("000426", "BUY", 41.0, 600, "2026-08-20"),
             ("600547", "SELL", 34.7, 700, "2026-08-25")])
        con.executemany(
            "INSERT INTO kline_cache (stock_code, date, open, high, low, close, volume, amount) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [("600547", "2026-08-24", 36, 37, 35, 36.5, 1, 1),
             ("600547", "2026-08-22", 36, 37, 35, 36.0, 1, 1),
             ("000426", "2026-08-24", 40, 42, 40, 41.5, 1, 1)])
        con.execute("INSERT INTO external_assets (id, name, asset_type, code, cost_amount, shares) "
                    "VALUES (?,?,?,?,?,?)",
                    (7, "华夏黄金ETF联接C", "FUND", "008701", 6000.0, 2300.0))
        con.execute("INSERT INTO portfolio_snapshots (snap_date, total_value, by_asset) VALUES (?,?,?)",
                    ("2026-08-24", 60000.0, '{"A:600547": 99999.0, "EXT:7": 5000.0}'))
        con.commit()
    finally:
        con.close()


def _run(**kw):
    from services.exposure import _positions_as_of
    return asyncio.run(_positions_as_of(**kw))


# ── A股: 回放账本 ────────────────────────────────────────

def test_holdings_replayed_from_ledger_at_that_date(temp_db):
    """8-24 两只都还在, 市值 = 当天收盘 × 股数。"""
    _seed(temp_db)
    stocks, _, _, _ = _run(day="2026-08-24")
    got = {s["code"]: round(s["mv"]) for s in stocks}
    assert got == {"600547": round(700 * 36.5), "000426": round(600 * 41.5)}


def test_later_sell_does_not_affect_earlier_day(temp_db):
    """8-25 才卖的, 不能让 8-24 那天凭空少一只 —— 截断按 trade_date, 不按现在的持仓表。"""
    _seed(temp_db)
    stocks, _, _, _ = _run(day="2026-08-24")
    assert "600547" in {s["code"] for s in stocks}
    later, _, _, _ = _run(day="2026-08-26")
    assert "600547" not in {s["code"] for s in later}     # 那天已经清掉了
    assert "000426" in {s["code"] for s in later}


def test_snapshot_is_not_used_as_the_stock_list(temp_db):
    """快照里那条 A:600547=99999 是当时写下的, 事后补录的成交它不知道 —— A股一律回放账本。"""
    _seed(temp_db)
    stocks, _, _, _ = _run(day="2026-08-24")
    assert all(round(s["mv"]) != 99999 for s in stocks)


def test_price_falls_back_to_last_close_before_that_day(temp_db):
    """停牌/那天没数据: 取更早那根收盘, 比整只漏掉诚实。"""
    _seed(temp_db)
    stocks, _, _, _ = _run(day="2026-08-23")             # 只有 8-22 那根
    assert {s["code"]: round(s["mv"]) for s in stocks}["600547"] == round(700 * 36.0)


def test_stock_without_any_close_is_reported_not_silently_dropped(temp_db):
    """一只票在缓存里一根日线都没有 → 不计入, 但要在 basis 里报出来(不然总资产悄悄少一块)。"""
    _seed(temp_db)
    con = sqlite3.connect(temp_db)
    con.execute("INSERT INTO position_actions (stock_code, action_type, price, shares, trade_date) "
                "VALUES ('601869','BUY',369.0,100,'2026-08-20')")
    con.commit(); con.close()
    stocks, _, _, basis = _run(day="2026-08-24")
    assert "601869" not in {s["code"] for s in stocks}
    assert basis["missing_price"] == ["601869"]


# ── 基金/外部资产: 取当时的快照 ──────────────────────────

def test_funds_valued_from_that_days_snapshot(temp_db):
    _seed(temp_db)
    _, funds, total_other, basis = _run(day="2026-08-24")
    assert [(f["code"], f["mv"]) for f in funds] == [("008701", 5000.0)]
    assert total_other == 5000.0
    assert basis["snapshot_date"] == "2026-08-24"


def test_missing_snapshot_says_so_instead_of_pretending_zero(temp_db):
    """那天之前一条快照都没有 → 外部资产未计入, basis 里说清楚(总资产会偏小, 不能装作没事)。"""
    _seed(temp_db)
    _, funds, total_other, basis = _run(day="2026-08-10")
    assert funds == [] and total_other == 0.0
    assert basis["snapshot_date"] is None and "未计入" in basis["others"]


# ── 结论里必须标明这是回看 ──────────────────────────────

def test_warning_flags_the_lookback_and_its_basis():
    """回看用的是**现在**的季报前十大与行业表 —— 这一条不跟着结论走, 数字就会被当成当时算的。"""
    from services.exposure import _warnings
    ws = _warnings([], [], [], 130000.0,
                   {"as_of": "2026-08-24", "stocks": "A股回放账本(截至2026-08-24)× 当天收盘",
                    "others": "外部资产取 2026-08-24 的组合快照", "missing_price": ["601869"]})
    assert ws and ws[0]["kind"] == "as_of"
    assert "2026-08-24" in ws[0]["text"] and "现在这一份" in ws[0]["text"]
    assert "1 只票" in ws[0]["text"]          # 缺价的也要报出来


def test_no_lookback_warning_for_live_view():
    from services.exposure import _warnings
    assert _warnings([], [], [], 130000.0, {"as_of": None}) == []

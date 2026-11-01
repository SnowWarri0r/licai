"""get_holdings 带上持仓页同源的现价/市值/浮动盈亏和个股合计: 模型不必再逐只查行情、手算盈亏
(2026-10-27 实测, 重题里单独一轮 calc 多半就是在算这几项)。"""
import asyncio

import services.stock_agent as sa


def _run(monkeypatch, stocks, holds):
    async def active():
        return holds

    async def dca():
        return {}

    def snap(fresh=False):
        f = asyncio.get_running_loop().create_future()
        f.set_result({"assets": [], "stocks": stocks})
        return f
    monkeypatch.setattr(sa, "_active_holdings", active)
    monkeypatch.setattr(sa, "_dca_plan_map", dca)
    monkeypatch.setattr(sa, "_assets_snapshot", snap)
    return asyncio.run(sa._tool_get_holdings())


def test_rows_carry_live_pnl_and_summary(monkeypatch):
    holds = [{"stock_code": "000001", "stock_name": "甲", "shares": 100, "cost_price": 10.0},
             {"stock_code": "HK.00700", "stock_name": "乙", "shares": 10, "cost_price": 400.0}]
    stocks = {
        "000001": {"current_price": 12.0, "price_change_pct": 1.5, "cost_price": 10.0, "today_valid": None,
                   "market_value": 1200.0, "cost_value": 1000.0, "unrealized_pnl": 200.0, "pnl_pct": 20.0},
        "HK.00700": {"current_price": 380.0, "price_change_pct": -1.0, "cost_price": 400.0, "today_valid": False,
                     "market_value": 3500.0, "cost_value": 3700.0, "unrealized_pnl": -200.0, "pnl_pct": -5.0},
    }
    r = _run(monkeypatch, stocks, holds)
    a = r["A股"][0]
    assert (a["现价"], a["今日涨跌%"], a["浮动盈亏(人民币)"], a["浮动盈亏%"]) == (12.0, 1.5, 200.0, 20.0)
    hk = r["港股"][0]
    assert "最近交易日涨跌%" in hk and "今日涨跌%" not in hk     # 港股那场不属于今天, 不叫今日
    assert r["个股合计(人民币)"] == {"市值": 4700.0, "成本": 4700.0, "浮动盈亏": 0.0, "浮动盈亏%": 0.0}


def test_missing_quote_leaves_row_without_live_fields(monkeypatch):
    holds = [{"stock_code": "000001", "stock_name": "甲", "shares": 100, "cost_price": 10.0}]
    r = _run(monkeypatch, {}, holds)
    assert "现价" not in r["A股"][0] and "个股合计(人民币)" not in r

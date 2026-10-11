"""港美股现金分红识别: 方案解析、拆股还原、除净前持股、去重、建议与一键记入(网络全 mock)。"""
import asyncio

import pytest

import services.dividends as dv


def _a(t, price, shares, day, **kw):
    return {"action_type": t, "price": price, "shares": shares, "trade_date": day, **kw}


# ---------- 数据源解析 ----------

def test_parse_hk_plan_variants():
    p = dv.parse_hk_plan("每股派港币5.3元")
    assert p["per_share"] == 5.3 and p["declared_ccy"] == "HKD" and not p["estimated"]
    p = dv.parse_hk_plan("每股派美元0.1元(相当于港币0.784088元)")
    assert p["per_share"] == 0.784088 and p["declared_ccy"] == "USD" and p["declared_ps"] == 0.1
    p = dv.parse_hk_plan("每股派人民币0.201元(相当于港币0.232375元(计算值))")
    assert p["per_share"] == 0.232375 and p["declared_ccy"] == "CNY" and p["estimated"]
    assert dv.parse_hk_plan("每股派港币0.89元(可选择以股代息)")["scrip"]
    p = dv.parse_hk_plan("每股派美元0.2元")                  # 没给港币等值 → 不猜
    assert p["per_share"] is None and p["declared_ccy"] == "USD"
    assert dv.parse_hk_plan("不分配") is None


def test_hk_events_from_eastmoney(monkeypatch):
    class R:
        def json(self):
            return {"result": {"data": [
                {"EX_DIVIDEND_DATE": "2026/05/15", "DIVIDEND_DATE": "2026/06/01", "REPORT_TYPE": "年度分配",
                 "PLAN_EXPLAIN": "每股派港币5.3元"},
                {"EX_DIVIDEND_DATE": None, "DIVIDEND_DATE": None, "REPORT_TYPE": "中期分配",
                 "PLAN_EXPLAIN": "每股派人民币0.2元(相当于港币0.23元(计算值))"},   # 除净日未定 → 跳过
            ]}}
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    ev = dv._hk_events_sync("00700")
    assert ev == [dict(ev[0], ex_date="2026-05-15", pay_date="2026-06-01", per_share=5.3)]
    assert len(ev) == 1


def test_yahoo_amounts_unadjusted_by_later_splits():
    # NVDA: 2024-03 每股 0.04, Yahoo 按 2024-06 的 1 拆 10 复权成 0.004
    divs = {"1709649000": {"amount": 0.004, "date": 1709649000},       # 2024-03-05
            "1726147800": {"amount": 0.01, "date": 1726147800}}        # 2024-09-12(拆股之后)
    splits = {"1718026200": {"date": 1718026200, "numerator": 10.0, "denominator": 1.0}}
    ev = dv.yahoo_unadjust(divs, splits, gmtoffset=-14400)
    assert [(e["ex_date"], e["per_share"], e["pay_date"]) for e in ev] == \
        [("2024-03-05", 0.04, None), ("2024-09-12", 0.01, None)]


def test_us_falls_back_to_yahoo_when_not_on_nasdaq(monkeypatch):
    dv._fcache.clear()
    monkeypatch.setattr(dv, "_us_nasdaq_sync", lambda s: None)
    monkeypatch.setattr(dv, "_us_yahoo_sync", lambda s: [{"ex_date": "2026-09-15", "pay_date": None,
                                                         "per_share": 0.53, "source": "yahoo"}])
    assert dv.foreign_events_sync("US.KO")[0]["source"] == "yahoo"
    dv._fcache.clear()
    monkeypatch.setattr(dv, "_us_yahoo_sync", lambda s: (_ for _ in ()).throw(OSError("down")))
    assert dv.foreign_events_sync("US.KO") is None                  # 拉不到 → None, 不缓存
    assert "US.KO" not in dv._fcache


# ---------- 资格 / 去重 ----------

def test_shares_before_ex_date_strict_and_splits():
    acts = [_a("BUY", 100, 100, "2026-01-05"), _a("SELL", 110, 40, "2026-03-01"),
            _a("SPLIT", 4, 0, "2026-04-01"), _a("BUY", 30, 10, "2026-05-15")]
    assert dv.shares_before(acts, "2026-03-01") == 100               # 除净日当天卖出仍拿分红
    assert dv.shares_before(acts, "2026-03-02") == 60
    assert dv.shares_before(acts, "2026-05-15") == 240               # 除净日当天买入拿不到
    assert dv.shares_before(acts, "2026-05-16") == 250


def _hk_ev(ex, pay, ps, **kw):
    return {"ex_date": ex, "pay_date": pay, "per_share": ps, "currency": "HKD", "declared_ccy": "HKD",
            "declared": f"每股派港币{ps}元", "kind": "年度分配", "estimated": False, "source": "eastmoney", **kw}


def test_pending_basic_hk_and_skips():
    acts = [_a("BUY", 400, 200, "2026-03-02")]
    ev = [_hk_ev("2025-05-16", "2025-05-30", 4.5),          # 买入之前 → 没持股
          _hk_ev("2026-05-15", "2026-06-01", 5.3),
          _hk_ev("2026-11-10", "2026-11-25", 1.0)]          # 除净日未到
    out = dv.pending_for_code("HK.00700", acts, ev, "2026-10-09")
    assert len(out) == 1
    it = out[0]
    assert (it["ex_date"], it["shares"], it["gross"], it["tax"], it["status"], it["trade_date"]) == \
        ("2026-05-15", 200, 1060.0, 0.0, "payable", "2026-06-01")


def test_pending_us_tax_and_upcoming():
    acts = [_a("BUY", 200, 10, "2026-01-05")]
    ev = [{"ex_date": "2026-09-10", "pay_date": "2026-10-15", "per_share": 0.25, "currency": "USD",
           "declared": "$0.25", "source": "nasdaq"}]
    it = dv.pending_for_code("US.NVDA", acts, ev, "2026-10-09")[0]
    assert it["status"] == "upcoming" and it["gross"] == 2.5 and it["tax"] == 0.25 and it["net"] == 2.25


def test_already_recorded_and_dismissed_are_hidden():
    acts = [_a("BUY", 400, 200, "2026-03-02"), _a("DIVIDEND", 5.3, 200, "2026-06-03")]   # 结单到账日略晚
    ev = [_hk_ev("2026-05-15", "2026-06-01", 5.3), _hk_ev("2026-08-20", "2026-09-10", 1.0)]
    out = dv.pending_for_code("HK.00700", acts, ev, "2026-10-09")
    assert [x["ex_date"] for x in out] == ["2026-08-20"]
    out = dv.pending_for_code("HK.00700", acts, ev, "2026-10-09", dismissed={"HK.00700|2026-08-20"})
    assert out == []


def test_one_dividend_row_claims_only_nearest_event():
    acts = [_a("BUY", 50, 100, "2025-01-02"), _a("DIVIDEND", 0.78, 100, "2026-06-26")]
    ev = [_hk_ev("2026-05-14", "2026-06-26", 0.78), _hk_ev("2026-08-13", "2026-09-25", 0.78)]
    out = dv.pending_for_code("HK.00005", acts, ev, "2026-10-09")
    assert [x["ex_date"] for x in out] == ["2026-08-13"]


def test_same_ex_date_regular_plus_special_merged_and_flagged():
    acts = [_a("BUY", 80, 100, "2025-01-02")]
    ev = [_hk_ev("2025-06-11", "2025-07-03", 1.024319, declared_ccy="USD", estimated=True, kind="年度分配"),
          _hk_ev("2025-06-11", "2025-07-03", 0.926765, declared_ccy="USD", estimated=True, kind="特别分配")]
    it = dv.pending_for_code("HK.09988", acts, ev, "2026-10-09")[0]
    assert it["per_share"] == 1.951084 and it["kind"] == "年度分配 + 特别分配"
    assert any("美元" in w for w in it["warnings"]) and any("估算" in w for w in it["warnings"])


def test_rmb_declared_and_missing_hkd_need_attention():
    acts = [_a("BUY", 6, 1000, "2025-01-02")]
    it = dv.pending_for_code("HK.00939", acts, [_hk_ev("2026-07-02", "2026-08-21", 0.234, declared_ccy="CNY")],
                             "2026-10-09")[0]
    assert any("10%" in w for w in it["warnings"])
    it = dv.pending_for_code("HK.00005", acts, [_hk_ev("2026-07-02", "2026-08-21", None, declared_ccy="USD")],
                             "2026-10-09")[0]
    assert it["status"] == "needs_input" and it["gross"] is None


# ---------- 接口: 建议 → 一键记入 → 不再重复 ----------

@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    import database as db
    monkeypatch.setattr("config.config.db_path", str(tmp_path / "t.db"))
    asyncio.run(db.init_db())
    return db


def test_confirm_writes_dividend_once(fresh_db, monkeypatch):
    db = fresh_db
    import api.portfolio_routes as pr
    asyncio.run(db.add_holding("US.AAPL", "Apple", 0, 0))
    asyncio.run(db.add_position_action("US.AAPL", "BUY", 200.0, 10, trade_date="2026-01-05", fx_rate=7.1))
    monkeypatch.setattr(dv, "foreign_events_sync", lambda c: [
        {"ex_date": "2026-08-10", "pay_date": "2026-08-13", "per_share": 0.27, "currency": "USD",
         "declared": "$0.27", "source": "nasdaq"}])
    monkeypatch.setattr(dv, "market_today", lambda c: "2026-10-09")

    async def fx(code, day, given=None):
        return 7.12 if day == "2026-08-13" else None
    monkeypatch.setattr(pr, "_trade_fx", fx)

    got = asyncio.run(pr.pending_dividends())
    assert [(x["stock_code"], x["shares"], x["gross"]) for x in got["items"]] == [("US.AAPL", 10, 2.7)]
    asyncio.run(pr.confirm_dividend("US.AAPL", pr.DividendConfirm(ex_date="2026-08-10")))
    rows = [r for r in asyncio.run(db.get_position_actions("US.AAPL")) if r["action_type"] == "DIVIDEND"]
    assert len(rows) == 1
    r = rows[0]
    assert (r["price"], r["shares"], r["trade_date"], r["fx_rate"], r["fee"]) == (0.27, 10, "2026-08-13", 7.12, None)
    assert asyncio.run(pr.pending_dividends())["items"] == []
    with pytest.raises(Exception):                                   # 再点一次 → 409, 不会记两笔
        asyncio.run(pr.confirm_dividend("US.AAPL", pr.DividendConfirm(ex_date="2026-08-10")))


def test_dismiss_persists(fresh_db, monkeypatch):
    db = fresh_db
    import api.portfolio_routes as pr
    asyncio.run(db.add_holding("HK.00700", "腾讯控股", 0, 0))
    asyncio.run(db.add_position_action("HK.00700", "BUY", 400.0, 100, trade_date="2026-03-02"))
    monkeypatch.setattr(dv, "foreign_events_sync", lambda c: [_hk_ev("2026-05-15", "2026-06-01", 5.3)])
    monkeypatch.setattr(dv, "market_today", lambda c: "2026-10-09")
    assert len(asyncio.run(pr.pending_dividends())["items"]) == 1
    asyncio.run(pr.dismiss_dividend("HK.00700", pr.DividendConfirm(ex_date="2026-05-15")))
    assert asyncio.run(pr.pending_dividends())["items"] == []

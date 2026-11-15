"""期间复盘: 先进先出配对、持有期分档、换手率口径。标的与金额均为虚构。"""
from services import period_review as pr


def _t(code, d, side, px, sh, fee=0.0, mkt="A", ratio=None):
    return {"code": code, "name": code, "market": mkt, "date": d, "side": side, "price": px,
            "shares": sh, "fee": fee, "ratio": ratio}


def test_fifo_consumes_oldest_lot_and_weights_days_by_cost():
    evs = [_t("Z1", "2026-01-05", "B", 10.0, 100), _t("Z1", "2026-01-20", "B", 20.0, 100),
           _t("Z1", "2026-01-25", "S", 15.0, 150)]
    s = pr.fifo_sells(evs)[0]
    assert s["shares"] == 150 and s["cost"] == 100 * 10 + 50 * 20 and s["proceeds"] == 150 * 15
    # 20 天 × 1000 成本 + 5 天 × 1000 成本, 按成本加权 = 12.5 天
    assert abs(s["wdays"] - 12.5) < 1e-9


def test_split_rescales_lots():
    evs = [_t("Z2", "2026-02-01", "B", 100.0, 10, mkt="US"),
           {**_t("Z2", "2026-02-10", "SPLIT", 0, 0, mkt="US"), "ratio": 4.0},
           _t("Z2", "2026-02-20", "S", 30.0, 40, mkt="US")]
    s = pr.fifo_sells(evs)[0]
    assert s["shares"] == 40 and abs(s["cost"] - 1000.0) < 1e-6 and s["proceeds"] == 1200.0


def test_unmatched_sell_is_ignored():
    """账本缺早期买入时, 多卖出的部分不配对, 不能凭空造出盈亏。"""
    s = pr.fifo_sells([_t("Z3", "2026-03-01", "B", 5.0, 100), _t("Z3", "2026-03-02", "S", 6.0, 300)])[0]
    assert s["shares"] == 100 and s["proceeds"] == 600.0


def test_hold_bucket_edges():
    assert [pr.hold_bucket(d) for d in (0, 1, 2, 7, 8, 30, 31, 90, 91)] == [
        "1 天内", "1 天内", "2~7 天", "2~7 天", "8~30 天", "8~30 天", "31~90 天", "31~90 天", "90 天以上"]


def test_summarize_counts_decisions_fees_and_period_sells_only():
    trades = [_t("Z4", "2025-12-30", "B", 10.0, 100, fee=5),
              _t("Z4", "2026-01-03", "S", 12.0, 50, fee=5), _t("Z4", "2026-01-03", "S", 12.0, 50, fee=1),
              _t("Z5", "2026-01-04", "B", 1.0, 1000, fee=0.5, mkt="ETF")]
    r = pr.summarize("2026", trades, [], [])
    tr = r["trading"]
    assert (tr["n_buy"], tr["n_sell"]) == (1, 1)          # 同一天同方向两笔算一次
    assert tr["sell_amt"] == 1200.0 and tr["buy_amt"] == 1000.0
    assert r["fees"]["total"] == 6.5                      # 只算期间内的成交费用
    assert r["fees"]["realized_gross"] == 200.0           # 2025 年的买入照样参与配对
    g = {x["label"]: x for x in r["holding"]["groups"]}
    assert g["2~7 天"]["n"] == 1 and g["2~7 天"]["win"] == 1.0
    assert tr["turnover_pct"] is None and "算不出换手率" in tr["turnover_note"]


def test_turnover_uses_only_snapshot_covered_trades():
    trades = [_t("Z6", "2026-01-10", "B", 10.0, 1000), _t("Z6", "2026-08-10", "S", 10.0, 1000)]
    snaps = [{"snap_date": "2026-07-01", "total_value": 10000.0}, {"snap_date": "2026-08-01", "total_value": 10000.0}]
    tr = pr.summarize("2026", trades, snaps, [])["trading"]
    # 1 月那笔在快照开始之前, 不进分子: 10000 / 2 / 10000 = 50%
    assert tr["turnover_pct"] == 50.0 and "2026-07-01" in tr["turnover_note"]


def test_entries_filtered_to_period():
    es = [{"date": "2026-05-01", "r5_pct": 1.0, "r20_pct": 4.0, "ex20_pct": 2.0},
          {"date": "2026-05-02", "r5_pct": -1.0, "r20_pct": -2.0, "ex20_pct": -1.0},
          {"date": "2026-06-01", "r5_pct": 9.0, "r20_pct": None, "ex20_pct": None}]
    e = pr.summarize("2026-05", [], [], es)["entries"]
    assert (e["n"], e["n_done"], e["mean20_pct"], e["win20"]) == (2, 2, 1.0, 0.5)


def test_period_validation_and_listing():
    assert pr.valid_period("2026") and pr.valid_period("2026-11") and not pr.valid_period("26-1")
    p = pr.periods_of([_t("Z7", "2025-12-01", "B", 1, 1), _t("Z7", "2026-02-01", "S", 1, 1)])
    assert p == {"years": ["2026", "2025"], "months": ["2026-02", "2025-12"]}

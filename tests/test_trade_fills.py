"""分笔成交合并: 一次委托成交两笔, 不该被复盘当成两次决策。

真实那笔(8-25): 紫光股份 200股@34.93 + 100股@34.94, 都在 10:06。账本里是两条(一条建仓
一条加仓), AI 复盘于是写了「同日拆成两笔买…更像是一次决策被拆成两下手」—— 它点评的其实
是记录方式, 不是交易行为。

这里守的是两头: 该合的合(分笔成交), 不该合的绝不能合 —— 真追高(34.9 买完 36.5 再买)、
真做T(同日买了又卖)、隔了几小时的第二次决策, 都是复盘该抓的东西, 合掉就等于把问题抹了。
"""
from services.trade_fills import merge_fills, fills_note


def _t(price, shares, kind="buy", date="2026-08-25", time=None, code="000938", **kw):
    return {"date": date, "code": code, "name": "紫光股份", "kind": kind, "price": price,
            "shares": shares, "time": time, "asset_class": "stock", **kw}


# ── 该合的 ──────────────────────────────────────────────

def test_same_order_two_fills_becomes_one_decision():
    out = merge_fills([_t(34.93, 200, time="10:06"), _t(34.94, 100, time="10:06")])
    assert len(out) == 1
    m = out[0]
    assert m["shares"] == 300
    assert m["price"] == 34.933          # 均价, 不是随便取一笔
    assert m["fills"] == 2


def test_merge_without_times_falls_back_to_price():
    """老记录没填时间(trade_time 可空)。价差 0.03% 还是一次决策。"""
    out = merge_fills([_t(34.93, 200), _t(34.94, 100)])
    assert len(out) == 1 and out[0]["fills"] == 2


def test_merged_pct_and_hit_recomputed_from_vwap():
    """均价变了, '成交后至今%'和命中要跟着重算, 不能留着第一笔的旧值。"""
    out = merge_fills([_t(10.0, 100, current=11.0, pct=10.0, hit=True),
                       _t(10.02, 100, current=11.0, pct=9.78, hit=True)])
    assert out[0]["price"] == 10.01
    assert out[0]["pct"] == 9.89
    assert out[0]["hit"] is True


def test_amount_summed_only_when_every_fill_has_one():
    """场外基金那侧带金额, 个股不带 —— 有一笔没有就整体不给, 别拼出个残缺的总额。"""
    both = merge_fills([_t(1.0, 100, amount=100.0), _t(1.001, 100, amount=100.1)])
    assert both[0]["amount"] == 200.1
    half = merge_fills([_t(1.0, 100, amount=100.0), _t(1.001, 100)])
    assert "amount" not in half[0]


# ── 绝不能合的 ──────────────────────────────────────────

def test_real_chasing_is_not_merged():
    """34.9 买完 36.5 又买 —— 这才是复盘要点的追高, 合掉就把问题抹了。"""
    out = merge_fills([_t(34.90, 100, time="09:35"), _t(36.50, 100, time="14:20")])
    assert len(out) == 2


def test_same_price_hours_apart_is_two_decisions():
    """价格碰巧一样, 但隔了 4 小时 —— 是又下了一次决心, 不是一次委托的分笔。"""
    out = merge_fills([_t(34.93, 100, time="10:06"), _t(34.93, 100, time="14:30")])
    assert len(out) == 2


def test_buy_and_sell_same_day_never_merge():
    """同日买了又卖 = 做T, 正是复盘要看的。"""
    out = merge_fills([_t(34.93, 100, kind="buy", time="10:06"),
                       _t(34.95, 100, kind="sell", time="10:20")])
    assert len(out) == 2


def test_different_days_never_merge():
    out = merge_fills([_t(34.93, 100, date="2026-08-24"), _t(34.94, 100, date="2026-08-25")])
    assert len(out) == 2


def test_no_chain_drift():
    """逐笔跟上一笔比会把 34.90→34.93→34.96 一路串成一簇(首尾 6 跳板), 所以跟**首笔**比。"""
    out = merge_fills([_t(34.90, 100, time="10:00"), _t(34.93, 100, time="10:00"),
                       _t(34.96, 100, time="10:00")])
    assert [t.get("fills", 1) for t in out] == [2, 1]


# ── 价差按跳板算, 不按百分比 ────────────────────────────

def test_high_priced_stock_does_not_get_a_wider_window():
    """9-01 生益科技: 148.62 + 148.95 同在 09:32, 用户说是两次下单。

    差 33 个跳板 —— 一次委托的分笔成交吃不掉那么多档。原来用 ≤0.3% 判, 它只差 0.22% 就被
    合掉了: 同一个百分比在 148 元的票上等于 44 个跳板, 在 34.9 元的票上只有 10 个, 票价越高
    白拿的窗口越宽。换成跳板后两个价位段一视同仁。
    """
    out = merge_fills([_t(148.62, 100, code="600183", name="生益科技", time="09:32"),
                       _t(148.95, 100, code="600183", name="生益科技", time="09:32")])
    assert len(out) == 2
    assert all(t.get("fills", 1) == 1 for t in out)


def test_cheap_stock_one_tick_still_merges():
    """同一次改动不能把紫光那个原始 case 弄坏: 1 跳板、同一分钟, 照旧是一次决策。"""
    out = merge_fills([_t(34.93, 200, time="10:06"), _t(34.94, 100, time="10:06")])
    assert len(out) == 1 and out[0]["fills"] == 2


def test_adjacent_minute_is_two_decisions():
    """跨了分钟就不算一串 —— 与佣金那套(按成交时刻分委托)对齐, 且模糊地带宁可少合。"""
    out = merge_fills([_t(34.93, 100, time="10:06"), _t(34.94, 100, time="10:07")])
    assert len(out) == 2


def test_fund_uses_a_finer_tick():
    """场外基金净值 4 位小数, 跳板是 0.001 —— 别拿个股的 0.01 去量它。"""
    same = merge_fills([_t(1.2340, 100, asset_class="fund", time="15:00"),
                        _t(1.2342, 100, asset_class="fund", time="15:00")])
    assert len(same) == 1                      # 差 2 个跳板
    apart = merge_fills([_t(1.2340, 100, asset_class="fund", time="15:00"),
                         _t(1.2400, 100, asset_class="fund", time="15:00")])
    assert len(apart) == 2                     # 差 60 个跳板


# ── 与佣金那套的包含关系 ────────────────────────────────

def test_review_and_commission_agree_on_what_one_order_is():
    """复盘与佣金对"同一张委托"必须给出**同一个**答案。

    这两处判据各自演化过一次, 结果对同一件事给出过两个答案(8-13 沪电 09:43+09:45: 复盘算
    一次决策、佣金算两张委托)。现在两边共用 one_order_clusters, 这条测试拿真实的两个 case
    正反各验一遍 —— 紫光那次该合、生益那次该分, 两侧结论一致。

    早先这里写的是"复盘 ⊆ 佣金"的包含关系, 靠比较 position_ledger._order_key。那个键后来
    退化成粗键(代码+日期+方向), 于是断言变成永真 —— 换成直接比两侧的分组数。
    """
    from services.position_ledger import allocate_trade_fees
    cases = [
        # 紫光 8-25: 1 跳板同分钟 → 一张委托 / 一次决策
        ("000938", "2026-08-25", [(34.94, 100), (34.93, 200)], 1),
        # 生益 9-01: 33 跳板同分钟 → 两张委托 / 两次决策
        ("600183", "2026-09-01", [(148.62, 100), (148.95, 100)], 2),
    ]
    for code, day, fills, want in cases:
        trades = [{"date": day, "code": code, "kind": "buy", "price": p, "shares": s,
                   "time": "10:06", "asset_class": "stock"} for p, s in fills]
        assert len(merge_fills(trades)) == want, f"{code} 复盘侧分组数不符"
        acts = [{"stock_code": code, "action_type": "BUY", "price": p, "shares": s,
                 "trade_date": day, "trade_time": "10:06"} for p, s in fills]
        fees = allocate_trade_fees(acts, 0.000086, 5.0)
        # 最低佣金被收几次 = 佣金侧认为有几张委托
        n_orders = round(sum(fees) - sum(a["price"] * a["shares"] for a in acts)
                         * (0.00001 + 0.0000341 + 0.00002)) / 5
        assert round(n_orders) == want, f"{code} 佣金侧算作 {n_orders} 张委托, 复盘侧 {want}"


def test_other_stocks_untouched():
    out = merge_fills([_t(34.93, 200, time="10:06"), _t(34.94, 100, time="10:06"),
                       _t(369.07, 100, code="601869", time="10:06")])
    assert len(out) == 2
    assert {t["code"] for t in out} == {"000938", "601869"}


# ── 给复盘看的说明 ──────────────────────────────────────

def test_note_spells_out_the_fills():
    m = merge_fills([_t(34.93, 200, time="10:06"), _t(34.94, 100, time="10:06")])[0]
    assert fills_note(m) == "同一决策分2笔成交: 200@34.93 + 100@34.94"
    assert fills_note(_t(34.93, 200)) == ""      # 没合并就别啰嗦

"""概念线的几日资金曲线。

一天的快照答不了"谁在接力谁在退潮", 所以要把这条线上的票按日把成交额加起来。数据来自本地
日线缓存(东财板块日K 那条路在这台机器上连不上, 连仓库里已有的个股日K 都被掐), 于是有两个
只能靠约束保证的东西:

  · 篮子要一致。某只票缺一天就把它整只剔掉 —— 否则那天少一块, 看着就是"资金退潮", 其实是
    缓存缺数。
  · 覆盖不够就别下结论。实测 8-28 涨幅榜 100 只里只有 5 只有本地日线; 拿 2 只票代表一条
    15 只票的线还标"在退潮", 那是编的。
"""
import asyncio
import pytest


@pytest.fixture
def fake_env(monkeypatch):
    """接管两处外部依赖: 榜单(网络) + 日线缓存(DB)。"""
    import database
    from services import concept_trend as ct

    state = {"hist": {}, "rank": {}}

    async def _amounts(codes, since):
        return {d: {c: v for c, v in row.items() if c in set(codes)}
                for d, row in state["hist"].items()}

    monkeypatch.setattr(database, "get_cached_amounts", _amounts)

    class _MR:
        @staticmethod
        def top_rankings(limit=100):
            return state["rank"]

    # concept_trend 里是 `from services import market_review` —— 取的是包属性, 先真导入
    # 让属性存在, 再换成假的
    import services
    import services.market_review          # noqa: F401
    monkeypatch.setattr(services, "market_review", _MR)
    assert ct is not None
    return state


def _rank(rows, groups, as_of="2026-08-28"):
    return {"as_of": f"{as_of} 11:30", "by_amount": rows, "gainers": rows,
            "groups": {"by_amount": {"concepts": groups, "industries": []},
                       "gainers": {"concepts": groups, "industries": []}}}


def _row(code, amt_yi):
    return {"code": code, "name": code, "pct": 1.0, "成交额亿": amt_yi}


def _grp(name, codes):
    return {"name": name, "codes": codes, "n": len(codes), "amt_yi": 0, "avg_pct": 0}


_CODES = [f"60000{i}" for i in range(10)]


def _hist(per_day):
    """per_day: {日期: {代码: 亿}} → 缓存里的单位是元。"""
    return {d: {c: v * 1e8 for c, v in row.items()} for d, row in per_day.items()}


def _run(scope="by_amount", **kw):
    from services.concept_trend import concept_trend
    return asyncio.run(concept_trend(scope=scope, **kw))


def test_series_uses_live_number_for_today_and_cache_for_past(fake_env):
    """今天那格用实时榜单(缓存里可能还没有今天), 历史用缓存 —— 单位还要从元换成亿。"""
    fake_env["hist"] = _hist({
        "2026-08-26": {c: 10 for c in _CODES},
        "2026-08-27": {c: 20 for c in _CODES},
    })
    fake_env["rank"] = _rank([_row(c, 30) for c in _CODES], [_grp("光模块", _CODES[:4])])
    d = _run(days=3)
    g = d["rows"][0]
    assert [s["date"] for s in g["series"]] == ["2026-08-26", "2026-08-27", "2026-08-28"]
    assert [s["amt_yi"] for s in g["series"]] == [40.0, 80.0, 120.0]


def test_member_missing_one_day_is_dropped_from_every_day(fake_env):
    """篮子必须一致: 缺一天的票整只剔掉。不剔的话那天凭空少一块, 会被读成"退潮"。"""
    hist = {"2026-08-26": {c: 10 for c in _CODES}, "2026-08-27": {c: 10 for c in _CODES}}
    hist["2026-08-27"].pop(_CODES[3])                   # 第4只这天没数
    fake_env["hist"] = _hist(hist)
    fake_env["rank"] = _rank([_row(c, 10) for c in _CODES], [_grp("光模块", _CODES[:4])])
    d = _run(days=3)
    g = d["rows"][0]
    assert g["basket_n"] == 3 and g["total_n"] == 4
    assert [s["amt_yi"] for s in g["series"]] == [30.0, 30.0, 30.0]   # 平的, 不是"跌了一截"


def test_share_is_against_the_whole_ranking_basket(fake_env):
    fake_env["hist"] = _hist({
        "2026-08-26": {c: 10 for c in _CODES[:8]},
        "2026-08-27": {c: 10 for c in _CODES[:8]},
    })
    fake_env["rank"] = _rank([_row(c, 10) for c in _CODES[:8]],
                             [_grp("甲线", _CODES[:4]), _grp("乙线", _CODES[4:8])])
    d = _run(days=3)
    assert all(s["share_pct"] == 50.0 for g in d["rows"] for s in g["series"])


def test_label_follows_share_move(fake_env):
    """标签看份额不看绝对值: 全市场放量时哪条线都涨, 份额才说明钱在往哪挪。"""
    fake_env["hist"] = _hist({
        "2026-08-26": {c: 10 for c in _CODES[:8]},
        "2026-08-27": {c: 10 for c in _CODES[:8]},
    })
    # 今天甲线翻倍、乙线不动 → 甲线份额升、乙线份额降, 但两条线的绝对值都没跌
    rows = [_row(c, 20) for c in _CODES[:4]] + [_row(c, 10) for c in _CODES[4:8]]
    fake_env["rank"] = _rank(rows, [_grp("甲线", _CODES[:4]), _grp("乙线", _CODES[4:8])])
    d = _run(days=3)
    lab = {g["name"]: g["label"] for g in d["rows"]}
    assert lab["甲线"] == "资金在进" and lab["乙线"] == "在退潮"


def test_thin_group_gets_no_verdict(fake_env):
    """整张榜覆盖够(10/20), 但这一条线 10 只票里只有 2 只有日线 —— 单独把它拿掉,
    缺的那 8 只可能正是今天最猛的。"""
    have, missing = _CODES, [f"30000{i}" for i in range(10)]     # 后 10 只没有任何日线
    fake_env["hist"] = _hist({"2026-08-26": {c: 10 for c in have},
                              "2026-08-27": {c: 10 for c in have}})
    fake_env["rank"] = _rank([_row(c, 10) for c in have + missing],
                             [_grp("厚线", have), _grp("薄线", have[:2] + missing[:8])])
    d = _run(days=3)
    assert [g["name"] for g in d["rows"]] == ["厚线"]


def test_overall_coverage_gate_says_why(fake_env):
    """整张榜覆盖不到三成 → 一条曲线都不出, 并说清是覆盖不够(而不是静默空白)。"""
    fake_env["hist"] = _hist({
        "2026-08-26": {c: 10 for c in _CODES[:2]},
        "2026-08-27": {c: 10 for c in _CODES[:2]},
    })
    rows = [_row(c, 10) for c in _CODES]
    fake_env["rank"] = _rank(rows, [_grp("甲线", _CODES[:2])])
    d = _run(days=3)
    assert d["rows"] == []
    assert "2/10" in d["note"] and "覆盖" in d["note"]


def test_intraday_is_flagged(fake_env, monkeypatch):
    """盘中最后一格是半天的量; 不标的话每天上午看都像全线退潮。"""
    import datetime as dt
    from services import concept_trend as ct

    class _FakeDT(dt.datetime):
        @classmethod
        def utcnow(cls):
            return dt.datetime(2026, 8, 28, 3, 0)        # CST 11:00, 还没收盘

    monkeypatch.setattr(ct._dt, "datetime", _FakeDT)
    fake_env["hist"] = _hist({"2026-08-26": {c: 10 for c in _CODES[:4]},
                              "2026-08-27": {c: 10 for c in _CODES[:4]}})
    fake_env["rank"] = _rank([_row(c, 5) for c in _CODES[:4]], [_grp("光模块", _CODES[:4])])
    assert _run(days=3)["today_partial"] is True

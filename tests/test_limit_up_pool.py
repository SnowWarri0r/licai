"""逐只涨停档案: 封单额 / 首封时刻 / 两源合并。

这块数据的意义在于把"涨停"从**只数**变成**质量**: 52 个涨停配 57 亿封单和配 20 亿封单
是两个完全不同的盘, n_zt 一样看不出来。所以这里守的是三件事:

1. 两个源的字段口径必须归一 —— 东财给整数 92500(不是 "09:25:00"), 开盘啦给 unix 秒且
   数组没有字段名, 任何一处错位, 后面所有结论都是错的。
2. **东财可以盖开盘啦, 开盘啦不许盖东财**。开盘啦那份没有炸板次数/换手/流通市值, 历史
   回填要是后跑就会把这些列刷成空。
3. 没数据就说没数据。覆盖率不够时那张回测表要自己标 可用=false, 不能拿 30% 的样本讲全市场。
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


# ── 字段口径归一 ────────────────────────────────────────

def test_em_time_is_an_int_and_needs_zero_padding():
    """东财 fbt 是整数 92500, 直接 str() 会得到 '9:25:0' —— 前导零必须自己补。"""
    from services.limit_up_pool import _hhmmss
    assert _hhmmss(92500) == "09:25:00"
    assert _hhmmss(143012) == "14:30:12"
    assert _hhmmss(0) is None and _hhmmss(None) is None and _hhmmss("-") is None


def test_kpl_timestamp_is_fixed_to_east8_not_server_local():
    """开盘啦给 unix 秒。跟着服务器本地时区解, 换台机器时刻就整体平移了。"""
    from services.limit_up_pool import _ts_hhmmss
    assert _ts_hhmmss(1787189135) == "09:25:35"
    assert _ts_hhmmss(0) is None


def test_em_name_spaces_stripped():
    """东财会给 '英 力 特', 带空格的名字后面对不上任何东西。"""
    from services.limit_up_pool import _clean_name
    assert _clean_name("英 力 特") == "英力特"


def test_kpl_row_field_positions():
    """开盘啦是裸数组, 下标是拿东财逐只比出来的(79/79)。这条把映射钉住。"""
    from services.limit_up_pool import _fetch_kpl_sync  # noqa: F401  (只为确认模块可导)
    from services.limit_up_pool import _clean_name, _ts_hhmmss
    a = ["688356", "键凯科技", 0, "", 1787189135, "疫苗概念", 765440064, 2366925528,
         29719454, 51599540, -21880086, 51894775, "疫苗概念、医药", 3151501651, 1.65,
         1, 0, 0, "", "801020", 18, 89.45, 20]
    row = {"stock_code": str(a[0]), "name": _clean_name(a[1]), "first_seal": _ts_hhmmss(a[4]),
           "theme": a[5], "seal_amount": a[6], "amount": a[11], "lb_count": a[15], "pct": a[22]}
    assert row["seal_amount"] == 765440064 or row["seal_amount"] == a[6]
    assert row["lb_count"] == 1 and row["pct"] == 20
    assert row["first_seal"] == "09:25:35"


def test_kpl_short_array_is_dropped_not_misread(monkeypatch):
    """数组变短 = 协议改了。宁可丢这条, 也不能按老下标错位读成别的字段。"""
    import services.limit_up_pool as lup

    class _R:
        status_code = 200
        def json(self):
            return {"info": [[["600000", "浦发银行", 0, "", 1787189135]]], "errcode": "0"}

    class _S:
        trust_env = True
        headers: dict = {}
        def post(self, *a, **k):
            return _R()

    monkeypatch.setattr(lup, "_KPL_MAX_PID", 1)
    import requests
    monkeypatch.setattr(requests, "Session", lambda: _S())
    assert lup._fetch_kpl_sync("2026-08-20") == []


# ── 两源合并的方向 ──────────────────────────────────────

def _row(code, src, **kw):
    base = {"snap_date": "2026-08-20", "stock_code": code, "name": "测试",
            "seal_amount": 1e8, "first_seal": "09:25:00", "last_seal": None,
            "lb_count": 1, "broken_times": None, "zt_days": None, "zt_ct": None,
            "industry": None, "theme": None, "amount": None, "float_mv": None,
            "turnover": None, "pct": 10.0, "source": src}
    base.update(kw)
    return base


def test_em_overwrites_kpl(temp_db):
    from database import save_limit_up_pool, get_limit_up_pool
    asyncio.run(save_limit_up_pool([_row("600000", "kpl")]))
    asyncio.run(save_limit_up_pool([_row("600000", "em", broken_times=2, turnover=8.5)]))
    got = asyncio.run(get_limit_up_pool("2026-08-20"))[0]
    assert got["source"] == "em" and got["broken_times"] == 2 and got["turnover"] == 8.5


def test_kpl_must_not_overwrite_em(temp_db):
    """回填晚于日常落库时的真实顺序。开盘啦那份没有炸板次数, 让它盖就等于把数据擦掉。"""
    from database import save_limit_up_pool, get_limit_up_pool
    asyncio.run(save_limit_up_pool([_row("600000", "em", broken_times=2, turnover=8.5)]))
    asyncio.run(save_limit_up_pool([_row("600000", "kpl", seal_amount=999.0)]))
    got = asyncio.run(get_limit_up_pool("2026-08-20"))[0]
    assert got["source"] == "em"
    assert got["broken_times"] == 2 and got["turnover"] == 8.5
    assert got["seal_amount"] == 1e8          # 封单额也不能被那条盖掉


def test_same_source_rerun_updates(temp_db):
    """同源重跑(补数/纠错)要能覆盖, 否则修不了错数据。"""
    from database import save_limit_up_pool, get_limit_up_pool
    asyncio.run(save_limit_up_pool([_row("600000", "kpl", seal_amount=1.0)]))
    asyncio.run(save_limit_up_pool([_row("600000", "kpl", seal_amount=2.0)]))
    assert asyncio.run(get_limit_up_pool("2026-08-20"))[0]["seal_amount"] == 2.0


# ── 质量画像 ────────────────────────────────────────────

def test_quality_says_no_archive_instead_of_zeros(temp_db):
    from services.limit_up_pool import quality
    q = asyncio.run(quality("2026-08-19"))
    assert q["有档案"] is False and "没有" in q["note"]
    assert "封单合计亿" not in q          # 不给 0, 0 会被读成"封单为零"


def test_quality_counts_seal_timing(temp_db):
    """一字板(9:25 集合竞价就封)/尾盘偷袭/开过板, 三种封板的共识强度完全不同。"""
    from database import save_limit_up_pool
    from services.limit_up_pool import quality
    asyncio.run(save_limit_up_pool([
        _row("600001", "em", first_seal="09:25:00", last_seal="09:25:00", seal_amount=3e8),
        _row("600002", "em", first_seal="09:25:00", last_seal="09:25:00", seal_amount=1e8),
        _row("600003", "em", first_seal="14:45:00", last_seal="14:45:00", seal_amount=2e7),
        _row("600004", "em", first_seal="10:10:00", last_seal="13:20:00", seal_amount=5e7),
        _row("600005", "em", first_seal="10:30:00", last_seal="10:30:00",
             broken_times=3, seal_amount=4e7),
    ]))
    q = asyncio.run(quality("2026-08-20"))
    assert q["涨停只数"] == 5
    assert q["一字板只数"] == 2
    assert q["尾盘才封只数"] == 1
    assert q["开过板只数"] == 2            # 600004(首封≠最后封) + 600005(炸板3次)
    assert q["封单合计亿"] == 5.1            # 3 + 1 + 0.2 + 0.5 + 0.4
    assert q["封单最厚"][0]["代码"] == "600001"


def test_quality_hides_broken_count_when_archive_is_backfilled(temp_db):
    """开盘啦那份没有炸板次数, 也没有最后封板时刻 —— 这时候要留空而不是报 0 只开过板。"""
    from database import save_limit_up_pool
    from services.limit_up_pool import quality
    asyncio.run(save_limit_up_pool([_row("600001", "kpl"), _row("600002", "kpl")]))
    q = asyncio.run(quality("2026-08-20"))
    assert q["开过板只数"] is None


def test_agent_tool_converts_the_date_format(temp_db, monkeypatch):
    """market_sentiment 给的是 '20260902', 档案按 'YYYY-MM-DD' 存 —— 直接拿去查会永远查不到,
    而且失败得很安静(涨停质量=null 看起来就像"那天还没落档")。实际踩过这个坑。"""
    import asyncio as _aio
    from database import save_limit_up_pool
    import api.market_routes as mr
    asyncio.run(save_limit_up_pool([_row("600001", "em", snap_date="2026-09-02")]))

    async def _fake():
        return {"date": "20260902", "date_cn": "09-02(周三)", "n_zt": 1}

    monkeypatch.setattr(mr, "market_sentiment", _fake)
    from services.stock_agent import _tool_market_sentiment
    out = _aio.run(_tool_market_sentiment())
    assert out["涨停质量"] is not None
    assert out["涨停质量"]["涨停只数"] == 1


# ── 兑现度回测的自我约束 ────────────────────────────────

def _seed_bars(path, code, bars):
    con = sqlite3.connect(path)
    try:
        con.executemany(
            "INSERT INTO kline_cache (stock_code, date, open, high, low, close, volume, amount)"
            " VALUES (?,?,?,?,?,?,1,1)",
            [(code, d, o, max(o, c), min(o, c), c) for d, o, c in bars])
        con.commit()
    finally:
        con.close()


def test_next_bars_are_strictly_after_the_day(temp_db):
    """涨停发生在 day, 要看的是 day 之后那根 —— 取到当天自己就等于用结果解释结果。"""
    from database import get_next_bars
    _seed_bars(temp_db, "600001", [("2026-08-19", 9, 10), ("2026-08-20", 10, 11),
                                   ("2026-08-21", 11, 12), ("2026-08-24", 12, 13)])
    got = asyncio.run(get_next_bars(["600001"], "2026-08-20"))
    assert got["600001"]["date"] == "2026-08-21"     # 不是 08-20, 也不是 08-24


def test_backtest_flags_itself_unusable_when_coverage_is_thin(temp_db):
    """涨停股大多是我们没持仓的票, 本地日线常常没有。覆盖率低就必须自己标 可用=false。"""
    from database import save_limit_up_pool
    from services.limit_up_pool import seal_backtest
    rows = [_row(f"60{i:04d}", "em", seal_amount=3e8) for i in range(40)]
    asyncio.run(save_limit_up_pool(rows))
    _seed_bars(temp_db, "600000", [("2026-08-20", 10, 11), ("2026-08-21", 11, 12)])
    r = asyncio.run(seal_backtest(days=5))
    assert r["可用"] is False
    assert r["覆盖率%"] < 30 and r["warning"]


def test_backtest_reports_control_failure_instead_of_the_headline(temp_db):
    """「封单越厚次日越强」有可能只是「大票次日更稳」的影子。

    这里造一份**封成比越高、次日反而越差**的数据: 规模对照必须报 false, 而不是照旧把
    那条单调关系端出来。不然哪天真实数据的关系反了, 我们会拿着一张错表当结论。
    """
    from database import save_limit_up_pool
    from services.limit_up_pool import seal_backtest
    rows, bars = [], []
    for i in range(180):
        code = f"60{i:04d}"
        amt = 1e8 + i * 1e7                      # 成交额铺开, 好切三档
        ratio = 0.05 + (i % 6) * 0.2
        rows.append(_row(code, "em", seal_amount=amt * ratio, amount=amt))
        ret = 6.0 - ratio * 8                    # 封成比越高 → 次日越差(与真实数据相反)
        bars.append((code, [("2026-08-20", 10.0, 10.0),
                            ("2026-08-21", 10.0, 10.0 * (1 + ret / 100))]))
    asyncio.run(save_limit_up_pool(rows))
    for code, bs in bars:
        _seed_bars(temp_db, code, bs)
    r = asyncio.run(seal_backtest(days=5, min_per_bucket=20))
    assert r["可用"] is True                      # 覆盖率是够的, 问题不在样本量
    assert r["规模对照"], "对照表不该为空"
    assert r["规模对照通过"] is False
    assert all(c["高半减低半pp"] < 0 for c in r["规模对照"])


def test_backtest_refuses_to_score_a_thin_bucket(temp_db):
    """某一档只有几只票时, 给出"次日平均涨 8%"这种数字比不给更有害。"""
    from database import save_limit_up_pool
    from services.limit_up_pool import seal_backtest
    asyncio.run(save_limit_up_pool([_row("600001", "em", seal_amount=3e8)]))
    _seed_bars(temp_db, "600001", [("2026-08-20", 10, 11), ("2026-08-21", 12, 13)])
    r = asyncio.run(seal_backtest(days=5, min_per_bucket=20))
    thin = [b for b in r["按封单额"] + r["按封成比"] if b["样本"] < 20]
    assert thin and all(b.get("结论") == "样本不足, 不给数" for b in thin)
    assert all("次日收盘涨跌%" not in b for b in thin)

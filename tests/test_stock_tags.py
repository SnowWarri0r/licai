"""归类层: 格子的定义、覆盖的诚实、以及格子之间的迁移。

这一层存在的理由是"问得出问题": 项目里原来有五套各自为政的归类且一处都没落库, 所以
「这只票昨天在哪个格子里」「首板格子今天接力了几只」根本问不出来。

于是这里守的是三件事:
1. 「进」轴的窗口口径不能被随手改 —— 那组命中率(完全命中 95.3% / 新面孔 97.8%)是照着
   断≤2天 + 回溯20日 在 1065 行东财标注上测出来的, 换了参数命中率就不是那个数了。
2. **覆盖要诚实**: 钱轴历史拿不到, 就必须留空。宁可留空也不排假榜 —— 假榜看不出是假的。
3. 迁移只能用板轴, 不许悄悄依赖价格 —— 一旦沾上 kline_cache(只有 31% 代码), 这张表就
   变成"有本地日线的那批票的迁移", 而不是市场的迁移。
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


def _seed_pool(path, rows):
    """rows: [(date, code, name, lb_count, amount)]"""
    con = sqlite3.connect(path)
    try:
        con.executemany(
            "INSERT OR REPLACE INTO limit_up_pool (snap_date, stock_code, name, lb_count,"
            " amount, seal_amount, source) VALUES (?,?,?,?,?,?, 'em')",
            [(d, c, n, lb, amt, 1e8) for d, c, n, lb, amt in rows])
        con.commit()
    finally:
        con.close()


# ── 「进」轴的窗口口径 ──────────────────────────────────

def test_jinji_window_constants_are_the_fitted_ones():
    """这两个数是拿 1065 行东财标注网格搜出来的(断≤2天 + 回溯20日 → 完全命中 95.3%)。

    换成别的值, 模块文档里那组命中率就不成立了 —— 改口径可以, 但要连着重测、重写文档,
    不能顺手一改把命中率留在那儿骗人。
    """
    from services.stock_tags import _JJ_MAX_GAP, _JJ_LOOKBACK
    assert (_JJ_MAX_GAP, _JJ_LOOKBACK) == (2, 20)


def test_jinji_bridges_a_short_gap_but_not_a_long_one():
    """断 2 天算同一波(3天2板这类), 断 3 天就是新的一波。"""
    from services.stock_tags import jinji
    cal = [set() for _ in range(30)]
    # 第 10 天涨停, 隔 2 天(11,12 没有), 第 13 天又涨停 → 4天2板
    cal[10].add("X"); cal[13].add("X")
    n, m, _ = jinji(cal, 13, "X")
    assert (n, m) == (4, 2)
    # 隔 3 天 → 切成新的一波, 只算 1天1板
    cal2 = [set() for _ in range(30)]
    cal2[10].add("Y"); cal2[14].add("Y")
    n2, m2, _ = jinji(cal2, 14, "Y")
    assert (n2, m2) == (1, 1)


def test_jinji_flags_a_truncated_lookback(temp_db):
    """档案最早那 20 个交易日, 回溯窗口撞到起点 —— 这一格的 进 不可信, 必须标出来。"""
    from services.stock_tags import jinji
    cal = [set() for _ in range(5)]
    cal[0].add("Z"); cal[1].add("Z")
    n, m, partial = jinji(cal, 1, "Z")
    assert (n, m) == (2, 2)
    assert partial is True                      # 窗口不全
    cal2 = [set() for _ in range(40)]
    cal2[30].add("Z")
    assert jinji(cal2, 30, "Z")[2] is False     # 前面留够了, 不算不全


def test_consecutive_board_count_is_derived_not_independent(temp_db):
    """连板数就是从涨停名单派生的: 昨天涨停 + 今天又涨停 → 连板数必然 +1。

    所以迁移表里不该再单列「晋级」—— 它与「今日仍涨停」永远相等, 是假精度。
    这条把那个不变式钉住, 顺便也能抓到档案错乱。
    """
    from services.stock_tags import rebuild
    from database import get_stock_tags
    _seed_pool(temp_db, [("2026-08-03", "600001", "甲", 1, 1e8),
                         ("2026-08-04", "600001", "甲", 2, 1e8)])
    asyncio.run(rebuild())
    a = {r["stock_code"]: r for r in asyncio.run(get_stock_tags("2026-08-03"))}
    b = {r["stock_code"]: r for r in asyncio.run(get_stock_tags("2026-08-04"))}
    for code in set(a) & set(b):
        assert b[code]["lb_count"] == a[code]["lb_count"] + 1


# ── 覆盖的诚实 ──────────────────────────────────────────

def test_history_leaves_the_money_axis_empty(temp_db):
    """历史那 244 天拉不到全市场成交额排名(kline_cache 只有 31% 代码)。

    宁可留空也不排假榜 —— 排出来的假榜看不出是假的, 会被当成真位次用下去。
    """
    from services.stock_tags import rebuild, coverage
    _seed_pool(temp_db, [("2026-08-03", "600001", "甲", 1, 5e8),
                         ("2026-08-03", "600002", "乙", 1, 3e8)])
    asyncio.run(rebuild())
    cov = asyncio.run(coverage())
    assert cov["板"] == 2 and cov["进"] == 2
    assert cov["钱"] == 0 and cov["钱_天数"] == 0


def test_money_axis_keeps_a_stock_that_did_not_limit_up(temp_db, monkeypatch):
    """「资金归类」是全市场成交额前 N, **不看是否涨停** —— 钱轴的意义就在没涨停那半。

    这类票涨停档案装不下, 所以归类层必须是独立一张表(这条测试就是它独立存在的理由)。
    """
    import services.stock_tags as stg
    _seed_pool(temp_db, [("2026-08-03", "600001", "甲", 1, 5e8)])

    async def _fake_rank():
        return [{"stock_code": "600001", "name": "甲", "amt_rank": 2, "amt": 5e8,
                 "industry": "化工", "theme": "题A"},
                {"stock_code": "300999", "name": "丙", "amt_rank": 1, "amt": 9e8,
                 "industry": "半导体", "theme": "题B"}]

    monkeypatch.setattr(stg, "_amt_ranking", _fake_rank)
    asyncio.run(stg.build_day("2026-08-03", with_money=True))
    from database import get_stock_tags
    rows = {r["stock_code"]: r for r in asyncio.run(get_stock_tags("2026-08-03"))}
    assert set(rows) == {"600001", "300999"}
    assert rows["300999"]["lb_count"] is None          # 没涨停, 板轴没有格子
    assert rows["300999"]["amt_rank"] == 1             # 但钱轴有
    assert rows["600001"]["amt_rank"] == 2 and rows["600001"]["lb_count"] == 1


def test_theme_axis_is_ordered_by_todays_crowding_not_raw_order(temp_db, monkeypatch):
    """题轴要"归类", 不是"取标签"。

    东财 f103 的顺序是任意的 —— 中际旭创排最前的是"节能环保", 而它今天真正在的线是
    CPO/光通信。按原序取前几个等于随机取, 那正是"给了一堆标签、看不出主线"的来源。
    这里造一份榜单: 甲乙丙都挂 CPO概念(今天的强线), 甲还挂一个没人跟的"节能环保"且排在最前。
    """
    import services.stock_tags as stg
    from services import market_review

    rank = [{"code": "300308", "name": "甲", "成交额亿": 100.0, "行业": "通信设备",
             "概念": ["节能环保", "CPO概念"]},
            {"code": "300502", "name": "乙", "成交额亿": 90.0, "行业": "通信设备",
             "概念": ["CPO概念"]},
            {"code": "300394", "name": "丙", "成交额亿": 80.0, "行业": "通信设备",
             "概念": ["CPO概念"]}]
    monkeypatch.setattr(market_review, "top_rankings", lambda n: {"by_amount": rank})
    _seed_pool(temp_db, [("2026-08-03", "300308", "甲", 1, 1e8)])
    asyncio.run(stg.build_day("2026-08-03", with_money=True))
    from database import get_stock_tags
    rows = {r["stock_code"]: r for r in asyncio.run(get_stock_tags("2026-08-03"))}
    assert rows["300308"]["theme"].split("、")[0] == "CPO概念"     # 不是"节能环保"
    assert rows["300308"]["industry"] == "通信设备"
    assert rows["300502"]["amt"] == 90.0 * 1e8                    # 成交额亿 → 元, 别读成 0


def test_rebuild_is_idempotent(temp_db):
    """归类是纯派生的, 重跑必须得到同一结果(不然每次收盘都会攒出重复行)。"""
    from services.stock_tags import rebuild, coverage
    _seed_pool(temp_db, [("2026-08-03", "600001", "甲", 1, 1e8)])
    asyncio.run(rebuild())
    first = asyncio.run(coverage())
    asyncio.run(rebuild())
    assert asyncio.run(coverage())["rows"] == first["rows"]


# ── 迁移 ────────────────────────────────────────────────

def test_migration_needs_no_price_data(temp_db):
    """kline_cache 一根日线都没有时, 迁移照样能算 —— 它只查涨停名单。

    这条是防回归: 一旦有人给迁移加个"次日涨幅", 它就悄悄变成"有本地日线那批票的迁移"了。
    """
    from services.stock_tags import rebuild, migration
    _seed_pool(temp_db, [
        ("2026-08-03", "600001", "甲", 1, 1e8),
        ("2026-08-03", "600002", "乙", 1, 1e8),
        ("2026-08-03", "600003", "丙", 2, 1e8),
        ("2026-08-04", "600001", "甲", 2, 1e8),      # 接力
        ("2026-08-05", "600002", "乙", 1, 1e8),      # 掉出后反包
    ])
    con = sqlite3.connect(temp_db)
    assert con.execute("select count(*) from kline_cache").fetchone()[0] == 0
    con.close()
    asyncio.run(rebuild())
    r = asyncio.run(migration("2026-08-04"))
    assert r["可用"] is True
    m = {x["上日连板数"]: x for x in r["梯队迁移"]}
    assert m[1]["上日只数"] == 2 and m[1]["今日仍涨停"] == 1 and m[1]["掉出"] == 1
    assert m[1]["接力率%"] == 50.0
    assert m[1]["掉出后两日内反包"] == 1               # 乙 在 08-05 回来了
    assert m[2]["上日只数"] == 1 and m[2]["今日仍涨停"] == 0


# ── 分池兑现率 ──────────────────────────────────────────

def _seed_two_days(path, spec, nxt_ret):
    """spec: [(code, lb_count, 是否前一波涨停过)]; nxt_ret: {code: 次日收盘涨跌%}"""
    rows = []
    for code, lb, repeat in spec:
        if repeat:                                  # 前一波留个涨停日, 让 进 轴数出 M>1
            rows.append(("2026-08-04", code, code, 1, 1e8))
        rows.append(("2026-08-06", code, code, lb, 1e8))
    _seed_pool(path, rows)
    bars = []
    for code, _, _ in spec:
        r = nxt_ret[code]
        bars += [(code, "2026-08-06", 10.0, 10.0),
                 (code, "2026-08-07", 10.0, 10.0 * (1 + r / 100))]
    con = sqlite3.connect(path)
    try:
        con.executemany(
            "INSERT INTO kline_cache (stock_code, date, open, high, low, close, volume, amount)"
            " VALUES (?,?,?,?,?,?,1,1)",
            [(c, d, o, max(o, cl), min(o, cl), cl) for c, d, o, cl in bars])
        con.commit()
    finally:
        con.close()


def test_backtest_benchmark_column_cancels_a_marketwide_move(temp_db):
    """全市场涨停股次日**一律** +5% 时, 各池的"超出同日涨停均值"必须是 0。

    没有这一列, 那天所有池子都会报"次日 +5%", 看起来像池子有优势 —— 实际是市场给的。
    这是本函数最重要的一条: 上一轮 seal_backtest 就是差了对照, 单调关系差点被当成结论。
    """
    from services.stock_tags import rebuild, pool_backtest
    spec = ([(f"60{i:04d}", 1, False) for i in range(70)]
            + [(f"61{i:04d}", 2, True) for i in range(70)])
    _seed_two_days(temp_db, spec, {c: 5.0 for c, _, _ in spec})
    asyncio.run(rebuild())
    r = asyncio.run(pool_backtest(min_sample=10))
    scored = [x for x in r["分池"] if "次日收盘涨跌%" in x]
    assert scored, "应该有池子拿到分数"
    for x in scored:
        assert abs(x["次日收盘涨跌%"] - 5.0) < 0.01          # 原始收益确实是 +5%
        assert abs(x["超出同日涨停均值pp"]) < 0.01           # 但相对同日基准是 0


def test_backtest_benchmark_keeps_a_real_edge(temp_db):
    """基准列不能把真实差异也抹掉: 2连板 +8%、首板 0% 时, 前者必须报出正的超额。"""
    from services.stock_tags import rebuild, pool_backtest
    spec = ([(f"60{i:04d}", 1, False) for i in range(70)]
            + [(f"61{i:04d}", 2, True) for i in range(70)])
    ret = {c: (0.0 if lb == 1 else 8.0) for c, lb, _ in spec}
    _seed_two_days(temp_db, spec, ret)
    asyncio.run(rebuild())
    r = asyncio.run(pool_backtest(min_sample=10))
    by = {x["池"]: x for x in r["分池"]}
    assert by["2连板"]["超出同日涨停均值pp"] > 3
    assert by["首板"]["超出同日涨停均值pp"] < -3


def test_backtest_reports_width_control_failure(temp_db):
    """「板越高次日越强」有可能只是高板池里塞了更多 ±20% 幅度的票。

    这里造一份反过来的数据: 首板池里全是创业板(300xxx, ±20%)且次日大涨, 2连板池全是
    主板(±10%)且次日小涨。全样本会看到"首板更强", 但只取主板重算时首板一只都不剩 ——
    排序对不上, 幅度对照必须报 false, 而不是把那个结论端出去。
    """
    from services.stock_tags import rebuild, pool_backtest
    spec = ([(f"30{i:04d}", 1, False) for i in range(70)]          # 创业板, 首板
            + [(f"60{i:04d}", 2, True) for i in range(70)])        # 主板, 2连板
    ret = {c: (9.0 if c.startswith("30") else 1.0) for c, _, _ in spec}
    _seed_two_days(temp_db, spec, ret)
    asyncio.run(rebuild())
    r = asyncio.run(pool_backtest(min_sample=10))
    full = {x["池"]: x for x in r["分池"] if "超出同日涨停均值pp" in x}
    assert full["首板"]["超出同日涨停均值pp"] > 0        # 全样本里首板看着更强
    assert r["幅度对照通过"] is False                    # 但限主板后排序变了, 自己报不通过
    assert r["幅度构成"]["首板"]["20%"] == 100.0


def test_backtest_gates_itself_on_thin_coverage(temp_db):
    """涨停股大多不是我们持仓的票, 本地日线天生缺 —— 覆盖率不够就必须自己标 可用=false。"""
    from services.stock_tags import rebuild, pool_backtest
    _seed_pool(temp_db, [("2026-08-06", f"60{i:04d}", "x", 1, 1e8) for i in range(100)])
    con = sqlite3.connect(temp_db)
    con.executemany("INSERT INTO kline_cache (stock_code, date, open, high, low, close, volume,"
                    " amount) VALUES (?,?,10,10,10,10,1,1)",
                    [("600000", "2026-08-06"), ("600000", "2026-08-07")])
    con.commit(); con.close()
    asyncio.run(rebuild())
    r = asyncio.run(pool_backtest())
    assert r["可用"] is False and r["warning"]
    assert r["覆盖率%"] < 50


def test_backtest_pools_are_split_by_axes_not_price(temp_db):
    """分池只看板轴/进轴。若掺进价格, 池子就被结果污染了(拿次日涨幅分池再算次日涨幅)。

    这里两只票次日收益天差地别(+10% 与 -10%), 但同属首板 —— 必须落进同一个池。
    """
    from services.stock_tags import rebuild, pool_backtest
    spec = [(f"60{i:04d}", 1, False) for i in range(80)]
    ret = {c: (10.0 if i % 2 == 0 else -10.0) for i, (c, _, _) in enumerate(spec)}
    _seed_two_days(temp_db, spec, ret)
    asyncio.run(rebuild())
    r = asyncio.run(pool_backtest(min_sample=10))
    by = {x["池"]: x for x in r["分池"]}
    assert by["首板"]["样本"] == 80                       # 一只都没被价格挑走
    assert abs(by["首板"]["次日收盘涨跌%"]) < 0.01        # 正负对冲
    assert by["首板"]["次日收红率%"] == 50.0


def test_backtest_separates_fresh_from_repeat(temp_db):
    """首板与反复板要分开: 连板数都是 1, 但一批是新面孔、一批这波已涨停过。"""
    from services.stock_tags import rebuild, pool_backtest
    spec = ([(f"60{i:04d}", 1, False) for i in range(70)]
            + [(f"61{i:04d}", 1, True) for i in range(70)])
    ret = {c: (1.0 if c.startswith("60") else -6.0) for c, _, _ in spec}
    _seed_two_days(temp_db, spec, ret)
    asyncio.run(rebuild())
    r = asyncio.run(pool_backtest(min_sample=10))
    by = {x["池"]: x for x in r["分池"]}
    assert by["首板"]["样本"] == 70 and by["反复板"]["样本"] == 70
    assert by["反复板"]["次日收盘涨跌%"] < by["首板"]["次日收盘涨跌%"]


def test_migration_says_so_when_there_is_nothing_to_compare(temp_db):
    from services.stock_tags import rebuild, migration
    _seed_pool(temp_db, [("2026-08-03", "600001", "甲", 1, 1e8)])
    asyncio.run(rebuild())
    assert asyncio.run(migration("2026-08-03"))["可用"] is False
    assert asyncio.run(migration("2026-07-01"))["可用"] is False

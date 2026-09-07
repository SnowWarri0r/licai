"""逻辑漂移: 三档判定 / 逐句 diff / 缺快照不许冒充没变化 / 修订史只在正文变了才增。

守的核心不变式是"**跟股价走**这一档必须存在且判得准"。ai-berkshire 的 thesis-drift 只分
"事实变化 vs 措辞变化"两档, 而"基本面没动、只有股价跌了, 于是把理由换成一个能解释股价的
说法"在两档划分里会被归进"事实变化"(价格的确变了) —— 于是最该被指出来的那种改写反而看
起来完全正当。所以这里三档, 且判定顺序(基本面优先)也要钉住。
"""
import asyncio
import json

import pytest

from services.thesis_drift import fact_delta, text_delta, _verdict, _pnl_track


def snap(**kw):
    base = {"价格": 10.0, "PE_TTM": 20.0, "PB": 2.0, "报告期": "20250630",
            "营收同比增长%": 12.0, "净利同比增长%": 20.0, "ROE%": 10.0, "资产负债率%": 40.0}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


# ── 事实轴 ──────────────────────────────────────────────

def test_price_only_move_is_not_a_fundamental_change():
    """股价 -30% 而报告期/同比/ROE/负债率都没动 —— 事实轴上只有价格在动。

    这条是"跟股价走"那一档的前提: 如果价格变化被算进基本面, 三档就退化成两档。
    """
    d = fact_delta(snap(), snap(价格=7.0, PE_TTM=14.0, PB=1.4))
    assert d["可判"] is True
    assert [c["项"] for c in d["价格轴"]] == ["价格", "PE_TTM", "PB"]
    assert d["基本面轴"] == []


def test_new_report_period_is_a_fundamental_change_with_no_threshold():
    """换报告期是硬事实, 不设门限 —— 期间出过一份新财报, 这件事本身就是新信息。"""
    d = fact_delta(snap(), snap(报告期="20250930"))
    assert [c["项"] for c in d["基本面轴"]] == ["报告期"]


def test_small_moves_stay_inside_the_thresholds():
    """价格 -8%、净利同比 -10pp 都在门限内。门限存在的意义就是别把日常波动叫做'事实变了'。"""
    d = fact_delta(snap(), snap(价格=9.2, **{"净利同比增长%": 10.0}))
    assert d["价格轴"] == [] and d["基本面轴"] == []


def test_thresholds_are_per_field_not_shared():
    """净利同比门限(20pp)比营收(10pp)宽: 净利同比天生波动大, 用同一门限会把它变成常亮告警。"""
    same = 12.0
    d = fact_delta(snap(), snap(**{"营收同比增长%": same + 12, "净利同比增长%": same + 12}))
    assert [c["项"] for c in d["基本面轴"]] == ["营收同比增长%"]


def test_market_cap_is_not_double_counted_with_price():
    """总市值 = 价格 × 股本, 与价格同一件事。它进了规则表就会把一次涨跌数成两处变化。"""
    from services.thesis_drift import _PRICE_RULES, _FUND_RULES
    keys = [k for k, _, _ in (*_PRICE_RULES, *_FUND_RULES)]
    assert "总市值亿" not in keys


def test_missing_snapshot_is_undecidable_not_unchanged():
    """老数据/取数失败 → 空快照。空必须判成"判不了"; 判成"没变化"会让所有历史修订
    统统显示成"只改了说法", 凭空造出一堆漂移结论。"""
    for a, b in ((None, snap()), (snap(), ""), ("", ""), ("{}", snap())):
        d = fact_delta(a, b)
        assert d["可判"] is False and d["原因"] == "缺事实快照"


def test_zero_and_none_are_not_compared_as_numbers():
    """某项这一版有、上一版没有 → 跳过, 不能当成"从 0 变成 20"。"""
    d = fact_delta(json.dumps({"价格": 10.0}), snap())
    assert [c["项"] for c in d["价格轴"]] == []          # PE/PB 上一版没有 → 不比
    assert d["基本面轴"] == []


# ── 文本轴: 逐句 diff ───────────────────────────────────

def test_reword_is_detected_as_rewrite_not_as_delete_plus_insert():
    """同一条理由改几个字要认成"改写"。认成一删一增, 就会被判成"换了理由"。"""
    td = text_delta("国产算力龙头, 看好数据中心需求", "国产算力龙头, 看好 AI 数据中心的需求")
    assert td["删除"] == [] and td["新增"] == []
    assert len(td["改写"]) == 1 and td["改写"][0]["相似度"] >= 0.55


def test_a_swapped_reason_shows_up_as_delete_and_insert():
    td = text_delta("看好存储涨价兑现到业绩", "行业格局长期向好, 龙头集中度提升")
    assert td["改写"] == []
    assert td["删除"] and td["新增"]


def test_rewrite_matching_works_across_positions():
    """挪了位置又重写的同一条, 在 difflib 的 opcode 里会分裂成一删一增, 必须跨位置配回来。"""
    td = text_delta("A公司是龙头, 看好数据中心需求", "看好数据中心的需求, A公司是行业龙头")
    assert len(td["改写"]) >= 1


def test_unchanged_clauses_are_reported_as_kept():
    td = text_delta("龙头地位稳, 估值不高", "龙头地位稳, 估值不高, 新增产能投产")
    assert "龙头地位稳" in td["保留"] and td["新增"] == ["新增产能投产"]


def test_clauses_split_on_chinese_commas():
    """一条理由的粒度是分句。整段比对只能说"改了", 分句比对才说得出改的是哪一条。

    中文写作里逗号就是分句符, 不切逗号的话"国产龙头, 国资背景, 看好需求"整段算一句,
    删掉其中一条理由只会显示成"改写了一句"。
    """
    td = text_delta("国产龙头, 国资背景, 看好需求", "国产龙头, 国资背景")
    assert td["删除"] == ["看好需求"]
    assert td["保留"] == ["国产龙头", "国资背景"]


def test_single_character_fragments_are_dropped():
    """一个字的碎片不是理由(常来自"等""及"这类残留), 留着只会在 diff 里制造噪音。"""
    from services.thesis_drift import _clauses
    assert _clauses("龙头地位稳, 等") == ["龙头地位稳"]


# ── 三档判定 ────────────────────────────────────────────

def test_fundamental_change_is_judged_as_following_facts():
    fd = fact_delta(snap(), snap(报告期="20250930", **{"净利同比增长%": -30.0}))
    v = _verdict(fd, text_delta("旧", "新的理由"), -5.0)
    assert v["档"] == "跟事实走"


def test_price_only_change_is_judged_as_following_the_price():
    """核心那一档。基本面没动、只有股价跌, 改逻辑就是跟着股价走 —— 还要把当时的浮亏说出来。"""
    fd = fact_delta(snap(), snap(价格=6.5, PE_TTM=13.0, PB=1.3))
    v = _verdict(fd, text_delta("看好存储涨价兑现", "行业长期格局好"), -21.4)
    assert v["档"] == "跟股价走"
    assert "-21.4%" in v["说明"]
    assert "理由被换掉" in v["说明"]


def test_fundamental_wins_when_both_axes_moved():
    """两轴同时变, 归因到事实 —— 基本面变了就有正当理由改, 不该被扣上跟股价走的帽子。
    判定顺序是这条测试守的东西。"""
    fd = fact_delta(snap(), snap(价格=6.5, PE_TTM=13.0, 报告期="20250930"))
    assert _verdict(fd, text_delta("a", "b"), -30.0)["档"] == "跟事实走"


def test_nothing_moved_is_just_a_reword():
    fd = fact_delta(snap(), snap(价格=9.5))
    v = _verdict(fd, text_delta("龙头地位稳", "龙头地位很稳"), 1.0)
    assert v["档"] == "只改了说法" and "同义改写" in v["说明"]


def test_undecidable_says_so_instead_of_guessing():
    v = _verdict(fact_delta("", snap()), text_delta("a", "b"), None)
    assert v["档"] == "判不了" and "快照" in v["说明"]


def test_verdict_never_gives_advice():
    """漂移是行为事实, 不是估值结论。四种判定文案里都不许出现买卖/仓位/目标价。"""
    cases = [fact_delta(snap(), snap(报告期="20250930")),
             fact_delta(snap(), snap(价格=6.0)),
             fact_delta(snap(), snap()),
             fact_delta("", snap())]
    for fd in cases:
        s = _verdict(fd, text_delta("a", "b"), -10.0)["说明"]
        for bad in ("买入", "卖出", "加仓", "减仓", "止损", "目标价", "建议"):
            assert bad not in s, (bad, s)


# ── 浮亏轨迹 ────────────────────────────────────────────

def test_pnl_track_flags_monotonically_deeper_losses():
    """每一版都记在更深的浮亏上 = 逻辑跟着亏损在改。这个信号不用读文本就看得出来。"""
    revs = [{"rev": i + 1, "created_at": f"2026-0{i+1}-01", "facts": snap(**{"浮动盈亏%": p})}
            for i, p in enumerate((-8.0, -15.0, -21.0))]
    t = _pnl_track(revs)
    assert t["逐次走低"] is True and t["说明"]


def test_pnl_track_does_not_flag_a_recovery():
    revs = [{"rev": i + 1, "created_at": "2026-01-01", "facts": snap(**{"浮动盈亏%": p})}
            for i, p in enumerate((-8.0, -15.0, -3.0))]
    assert _pnl_track(revs)["逐次走低"] is False


def test_pnl_track_needs_at_least_two_points():
    assert _pnl_track([{"rev": 1, "created_at": "2026-01-01", "facts": snap(**{"浮动盈亏%": -8.0})}])["逐次走低"] is False


def test_pnl_track_survives_missing_snapshots():
    revs = [{"rev": 1, "created_at": "2026-01-01", "facts": ""},
            {"rev": 2, "created_at": "2026-02-01", "facts": snap(**{"浮动盈亏%": -12.0})}]
    t = _pnl_track(revs)
    assert t["序列"][0]["浮动盈亏%"] is None and t["逐次走低"] is False


# ── 修订史(落库) ────────────────────────────────────────
# 本仓没装 pytest-asyncio, 异步用例一律 asyncio.run 包一层(与 test_multi_lens 同写法)。

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg.config, "db_path", str(tmp_path / "t.db"))
    import database as d
    return d


def test_saving_the_same_text_twice_appends_only_one_revision(tmp_db):
    """前端每次打开弹窗点保存都会 PUT 一次。原文没动也记一版, 修订史就被灌成一堆同文,
    "改过几次"这个数字随即失去意义。"""
    async def body():
        d = tmp_db
        await d.init_db()
        r1 = await d.set_thesis("600176", "龙头地位稳", "中国巨石", snap())
        r2 = await d.set_thesis("600176", "龙头地位稳", "中国巨石", snap(价格=6.0))
        r3 = await d.set_thesis("600176", "龙头地位稳, 估值不高", "中国巨石", snap(价格=6.0))
        assert (r1["appended"], r2["appended"], r3["appended"]) == (True, False, True)
        revs = await d.get_thesis_revisions("600176")
        assert [r["rev"] for r in revs] == [1, 2]
        assert revs[1]["thesis"].endswith("估值不高")
    asyncio.run(body())


def test_revisions_keep_their_own_snapshot(tmp_db):
    """每版存自己那一刻的事实。共用一份快照就没法算"两版之间变了什么"。"""
    async def body():
        d = tmp_db
        await d.init_db()
        await d.set_thesis("600176", "一句话", "", snap(价格=10.0))
        await d.set_thesis("600176", "另一句话", "", snap(价格=6.0))
        revs = await d.get_thesis_revisions("600176")
        assert json.loads(revs[0]["facts"])["价格"] == 10.0
        assert json.loads(revs[1]["facts"])["价格"] == 6.0
    asyncio.run(body())


def test_clearing_the_thesis_drops_its_revisions(tmp_db):
    """逻辑都不要了, 留着修订史会让下次重记从 rev=N+1 接着编号 —— 看上去像接着改的。"""
    async def body():
        d = tmp_db
        await d.init_db()
        await d.set_thesis("600176", "一句话", "", snap())
        await d.set_thesis("600176", "另一句话", "", snap())
        await d.delete_thesis("600176")
        assert await d.get_thesis_revisions("600176") == []
        r = await d.set_thesis("600176", "重新写的逻辑", "", snap())
        assert r["rev"] == 1
    asyncio.run(body())


def test_migration_seeds_rev1_without_faking_a_snapshot(tmp_db):
    """老库里已有的逻辑补 rev=1, 但 facts 必须留空。

    拿今天的数字冒充"第一版时的事实", 会让这段时间的所有变化在漂移分析里显示为 0 ——
    一个凭空生成的"逻辑对得上事实"结论。
    """
    async def body():
        d = tmp_db
        await d.init_db()
        db = await d.get_db()
        try:                                # 绕过 set_thesis, 模拟加这张表之前写下的老数据
            await db.execute(
                "INSERT INTO position_thesis (code, name, thesis) VALUES ('000333','美的','老逻辑')")
            await db.commit()
        finally:
            await db.close()
        await d.init_db()                   # 重启一次 → 迁移补种
        revs = await d.get_thesis_revisions("000333")
        assert [r["rev"] for r in revs] == [1]
        assert revs[0]["thesis"] == "老逻辑" and revs[0]["facts"] == ""
        await d.init_db()                   # 再启一次不能重复补种
        assert len(await d.get_thesis_revisions("000333")) == 1
    asyncio.run(body())


def test_drift_on_a_code_with_no_thesis_says_so(tmp_db):
    async def body():
        await tmp_db.init_db()
        from services.thesis_drift import drift
        r = await drift("600176")
        assert r["有记录"] is False
    asyncio.run(body())


def test_drift_walks_every_adjacent_pair(tmp_db, monkeypatch):
    """三版 → 两次改写。少算一次(比如只比首末版)会把中间那次跟股价走的改写整个漏掉。"""
    async def body():
        d = tmp_db
        await d.init_db()
        await d.set_thesis("600176", "看好存储涨价兑现到业绩", "巨石",
                           snap(价格=10.0, **{"浮动盈亏%": -2.0}))
        await d.set_thesis("600176", "看好存储涨价兑现到业绩, 行业格局好", "巨石",
                           snap(价格=6.5, PE_TTM=13.0, PB=1.3, **{"浮动盈亏%": -18.0}))
        await d.set_thesis("600176", "行业格局长期向好, 龙头集中度提升", "巨石",
                           snap(价格=6.4, PE_TTM=13.0, PB=1.3, 报告期="20250930",
                                **{"浮动盈亏%": -19.0}))
        import services.thesis_drift as td

        async def _snap(_c):
            return {}                       # 现在的事实取不到 → 尾段判不了, 不影响逐次改写
        monkeypatch.setattr(td, "snapshot", _snap)
        r = await td.drift("600176")
        assert r["修订次数"] == 3 and len(r["逐次改写"]) == 2
        assert r["逐次改写"][0]["判定"]["档"] == "跟股价走"
        assert r["逐次改写"][0]["改写时浮动盈亏%"] == -18.0
        assert r["逐次改写"][1]["判定"]["档"] == "跟事实走"
        assert r["自末版以来的事实变化"]["可判"] is False
    asyncio.run(body())


def test_drift_reports_facts_moving_after_the_last_revision(tmp_db, monkeypatch):
    """只写过一版、之后再没碰过, 但事实已经变了 —— 这也是漂移的一种(逻辑没跟上)。"""
    async def body():
        d = tmp_db
        await d.init_db()
        await d.set_thesis("600176", "看好存储涨价", "巨石", snap(价格=10.0))
        import services.thesis_drift as td

        async def _snap(_c):
            return json.loads(snap(价格=6.0, 报告期="20250930"))
        monkeypatch.setattr(td, "snapshot", _snap)
        r = await td.drift("600176")
        assert r["逐次改写"] == []
        assert "逻辑没跟着动" in r["自末版以来"]
        assert "报告期" in r["自末版以来"] and "价格" in r["自末版以来"]
    asyncio.run(body())


def test_snapshot_omits_fields_it_could_not_fetch(monkeypatch):
    """取不到的项不放键, 尤其不许拿 0 占位 —— 0 会当成一个真实数值参与门限比较。"""
    import services.thesis_drift as td

    async def _q(_codes):
        raise RuntimeError("行情源不可达")

    async def _f(_c):
        return {}

    async def _c(_c):
        return None
    monkeypatch.setattr("services.market_data.get_realtime_quotes", _q)
    monkeypatch.setattr(td, "_fundamentals", _f)
    monkeypatch.setattr(td, "_cost_of", _c)
    s = asyncio.run(td.snapshot("600176"))
    assert "价格" not in s and "PE_TTM" not in s and "浮动盈亏%" not in s
    assert s["取于"]


def test_snapshot_skips_pnl_when_position_is_cleared(monkeypatch):
    """已清仓 → 没有成本, 就不该有浮动盈亏。holdings.cost_price 对清仓票是陈旧值, 拿它算
    会得出一个凭空的浮亏数。"""
    import services.thesis_drift as td

    async def _q(_codes):
        return {"600176": {"price": 8.0}}

    async def _f(_c):
        return {"valuation": {"PE_TTM": 16.0}, "financials": {"报告期": "20250630"}}

    async def _c(_c):
        return None
    monkeypatch.setattr("services.market_data.get_realtime_quotes", _q)
    monkeypatch.setattr(td, "_fundamentals", _f)
    monkeypatch.setattr(td, "_cost_of", _c)
    s = asyncio.run(td.snapshot("600176"))
    assert s["价格"] == 8.0 and s["PE_TTM"] == 16.0
    assert "成本" not in s and "浮动盈亏%" not in s


# ── agent 那一侧 ────────────────────────────────────────

def test_agent_tool_description_mentions_drift():
    """工具描述不提漂移, 模型就不会为了看漂移去调它 —— 能力挂上了但没人读, 等于没做。"""
    from services.stock_agent import _TOOLS
    t = next(t for t in _TOOLS if t["name"] == "get_thesis")
    assert "漂移" in t["description"] and "股价" in t["description"]


def test_agent_tool_returns_the_drift_block_and_explains_the_three_tiers(tmp_db, monkeypatch):
    """漂移要真的进 get_thesis 的返回, 并且 note 里要把三档讲清楚。

    只把数据算出来、不塞进模型看得见的返回里, 就是上一轮踩过的"留了接口没接上"。
    """
    async def body():
        d = tmp_db
        await d.init_db()
        await d.set_thesis("600176", "看好存储涨价兑现", "巨石",
                           snap(价格=10.0, **{"浮动盈亏%": -2.0}))
        await d.set_thesis("600176", "行业格局长期向好", "巨石",
                           snap(价格=6.5, PE_TTM=13.0, PB=1.3, **{"浮动盈亏%": -20.0}))
        import services.thesis_drift as td

        async def _snap(_c):
            return {}
        monkeypatch.setattr(td, "snapshot", _snap)
        from services.stock_agent import _tool_get_thesis
        out = await _tool_get_thesis("600176")
        assert out["逻辑漂移"]["修订次数"] == 2
        assert out["逻辑漂移"]["逐次改写"][0]["判定"]["档"] == "跟股价走"
        for k in ("跟事实走", "跟股价走", "只改了说法"):
            assert k in out["note"], k
        assert "不引申成买卖结论" in out["note"]
    asyncio.run(body())


def test_prompt_carries_the_per_market_source_pairings():
    """多源校验从"原则"变成"具体配哪两个源"; 少了这个, 模型会两次都落在同一家的转载上。"""
    from services.stock_agent import _SYSTEM
    for s in ("cninfo", "macrotrends", "stockanalysis", "aastocks", "EDGAR", "hkexnews"):
        assert s in _SYSTEM, s
    assert "口径" in _SYSTEM

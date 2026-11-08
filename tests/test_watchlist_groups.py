"""观察池分组: 一票多组 + 从老的单值列迁过来。

「自选」是一个真实分组, 跟「金矿」平级 —— 一只票可以同时在「自选」和「金矿」里。
它只有两处特殊: 加进观察池时默认落它, 以及取消到一个组都不剩时退回它(不然这只票哪个
分组视图都进不去, 只能在「全部」里看到)。

为什么必须测迁移: 老库里分组是 watchlist.grp 一列, 搬到 watchlist_group 关联表之后
老列要删掉 —— 不删就是第二本账, 读到的永远是搬迁那一刻的快照。而删列这件事只在真实
老库上跑一次, 跑错了就是用户手工归的那些组全丢。

组内位次也一并搬: 每个组各有一份顺序, 挂在 watchlist 上时一只票只能有一个位次, 进
第二个组必然跟第一个组抢。
"""
import asyncio
import os
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient

from run import app


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # config 模块底部导出的是单例 config = Config(); 打到类属性上是不生效的
    monkeypatch.setattr("config.config.db_path", path)
    yield path
    os.unlink(path)


def _init(path):
    from database import init_db
    asyncio.run(init_db())
    return path


def _cols(path, table="watchlist"):
    con = sqlite3.connect(path)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def _add(code, name="X", price=10.0):
    from database import add_watchlist
    asyncio.run(add_watchlist(code, name, price))


def _rows():
    from database import list_watchlist
    return {r["code"]: r for r in asyncio.run(list_watchlist())}


def _set(code, groups):
    from database import set_watchlist_groups
    return asyncio.run(set_watchlist_groups(code, groups))


# ── 一票多组 ────────────────────────────────────────────

def test_one_stock_in_several_groups(temp_db):
    _init(temp_db)
    _add("600519")
    _set("600519", ["高端内存", "博弈"])
    assert _rows()["600519"]["groups"] == ["博弈", "高端内存"]     # 按组名排, 稳定


def test_groups_are_deduped_and_trimmed(temp_db):
    _init(temp_db)
    _add("600519")
    assert _set("600519", ["  金矿 ", "金矿", "", "  "]) == ["金矿"]
    assert _rows()["600519"]["groups"] == ["金矿"]


def test_empty_list_falls_back_to_default_group(temp_db):
    """清空分组 = 退回默认组「自选」, 不是"一个组都不在"。"""
    _init(temp_db)
    _add("600519")
    _set("600519", ["金矿"])                                # 加进来时本来就在「自选」
    _set("600519", [])
    assert _rows()["600519"]["groups"] == ["自选"]


def test_default_group_coexists_with_others(temp_db):
    """核心诉求: 同时在「自选」和某个分组里。勾了「金矿」不该把「自选」顶掉。"""
    _init(temp_db)
    _add("600519")
    assert _rows()["600519"]["groups"] == ["自选"]           # ☆ 加入 = 落默认组
    _set("600519", ["自选", "金矿"])
    assert sorted(_rows()["600519"]["groups"]) == sorted(["自选", "金矿"])


def test_default_group_can_be_dropped_when_others_remain(temp_db):
    """只想留「金矿」也行 —— 「自选」不是强制的, 只是默认的。"""
    _init(temp_db)
    _add("600519")
    _set("600519", ["金矿"])
    assert _rows()["600519"]["groups"] == ["金矿"]
    _init(temp_db)                                           # 再启动一次也不给塞回来
    assert _rows()["600519"]["groups"] == ["金矿"]


def test_adding_a_group_keeps_position_in_the_old_one(temp_db):
    """勾第二个组不能让这只票在原来那个组里挪到末尾 —— 已在的组要保留原位次。"""
    _init(temp_db)
    for c in ("000001", "000002", "000003"):
        _add(c)
        _set(c, ["金矿"])
    before = _rows()["000001"]["group_orders"]["金矿"]
    _set("000001", ["金矿", "博弈"])
    after = _rows()["000001"]
    assert after["group_orders"]["金矿"] == before
    assert "博弈" in after["group_orders"]
    # 组内顺序也没变: 000001 仍在金矿组第一个
    r = _rows()
    assert r["000001"]["group_orders"]["金矿"] < r["000002"]["group_orders"]["金矿"]


def test_new_group_membership_goes_to_the_end_of_that_group(temp_db):
    _init(temp_db)
    for c in ("000001", "000002"):
        _add(c)
        _set(c, ["金矿"])
    _add("000003")
    _set("000003", ["金矿"])
    orders = {c: _rows()[c]["group_orders"]["金矿"] for c in ("000001", "000002", "000003")}
    assert orders["000003"] > orders["000002"] > orders["000001"]


def test_group_order_is_per_group(temp_db):
    """同一只票在两个组里各有一份位次 —— 在 A 组调顺序不该动它在 B 组的位置。"""
    from database import reorder_watchlist
    _init(temp_db)
    for c in ("000001", "000002"):
        _add(c)
        _set(c, ["甲", "乙"])
    asyncio.run(reorder_watchlist(["000002", "000001"], scope="group", group="甲"))
    r = _rows()
    assert r["000002"]["group_orders"]["甲"] < r["000001"]["group_orders"]["甲"]
    # 乙组不受影响: 还是先加的在前
    assert r["000001"]["group_orders"]["乙"] < r["000002"]["group_orders"]["乙"]


def test_reorder_in_group_adds_membership_without_touching_others(temp_db):
    """拖进某个组 = 入组, 但它在别的组里待着不受影响。"""
    from database import reorder_watchlist
    _init(temp_db)
    _add("000001")
    _set("000001", ["甲"])
    asyncio.run(reorder_watchlist(["000001"], scope="group", group="乙"))
    assert sorted(_rows()["000001"]["groups"]) == sorted(["甲", "乙"])


def test_reorder_global_does_not_touch_groups(temp_db):
    from database import reorder_watchlist
    _init(temp_db)
    for c in ("000001", "000002"):
        _add(c)
    _set("000001", ["甲"])
    asyncio.run(reorder_watchlist(["000002", "000001"], scope="global"))
    r = _rows()
    assert r["000002"]["sort_order"] < r["000001"]["sort_order"]
    assert r["000001"]["groups"] == ["甲"] and r["000002"]["groups"] == ["自选"]


def test_reorder_with_blank_group_name_is_a_noop(temp_db):
    """组名为空是脏入参(默认组叫「自选」), 别给它凭空造出一行空组名。"""
    from database import reorder_watchlist
    _init(temp_db)
    _add("000001")
    asyncio.run(reorder_watchlist(["000001"], scope="group", group=""))
    assert _rows()["000001"]["groups"] == ["自选"]


def test_reorder_in_default_group_writes_its_own_order(temp_db):
    """「自选」是真实分组, 有自己那套组内位次 —— 在它里面调顺序不该动全局位次。"""
    from database import reorder_watchlist
    _init(temp_db)
    for c in ("000001", "000002"):
        _add(c)
    before = {c: _rows()[c]["sort_order"] for c in ("000001", "000002")}
    asyncio.run(reorder_watchlist(["000002", "000001"], scope="group", group="自选"))
    r = _rows()
    assert r["000002"]["group_orders"]["自选"] < r["000001"]["group_orders"]["自选"]
    assert {c: r[c]["sort_order"] for c in ("000001", "000002")} == before


def test_removing_from_watchlist_drops_group_rows(temp_db):
    """不删成员行的话, 这只票再加回观察池会连着旧分组一起复活。"""
    from database import remove_watchlist
    _init(temp_db)
    _add("600519")
    _set("600519", ["金矿"])
    asyncio.run(remove_watchlist("600519"))
    _add("600519")
    assert _rows()["600519"]["groups"] == ["自选"]          # 只剩默认组, 金矿没复活


# ── 从老的单值列迁移 ────────────────────────────────────

def _legacy_db(path, rows):
    """按改造前的表结构建库: grp/group_order 两列都在 watchlist 上。"""
    con = sqlite3.connect(path)
    try:
        con.execute("""CREATE TABLE watchlist (
            stock_code TEXT PRIMARY KEY, stock_name TEXT, added_at TEXT, added_price REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            grp TEXT DEFAULT '', sort_order REAL DEFAULT 0, group_order REAL DEFAULT 0)""")
        con.executemany(
            "INSERT INTO watchlist (stock_code, stock_name, grp, sort_order, group_order) "
            "VALUES (?,?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()


def test_migration_moves_groups_and_drops_the_old_columns(temp_db):
    _legacy_db(temp_db, [("601728", "中国电信", "高端内存", 0, 5),
                         ("600489", "中金黄金", "金矿", 1, 2),
                         ("000001", "平安银行", "", 2, 0)])
    _init(temp_db)
    r = _rows()
    assert r["601728"]["groups"] == ["高端内存"]
    assert r["601728"]["group_orders"]["高端内存"] == 5      # 组内位次一起搬, 顺序不重置
    assert r["600489"]["groups"] == ["金矿"]
    assert r["000001"]["groups"] == ["自选"]                 # 老的空串 = 补进默认组
    assert r["000001"]["sort_order"] == 2                    # 全局位次原样保留
    # 老列必须没了: 留着就是第二本账
    assert "grp" not in _cols(temp_db) and "group_order" not in _cols(temp_db)
    assert "sort_order" in _cols(temp_db)


def test_migration_does_not_resurrect_a_group_the_user_removed(temp_db):
    """搬完再启动一次不能照着老列重搬 —— 那会把用户后来移出的分组复活。"""
    _legacy_db(temp_db, [("601728", "中国电信", "高端内存", 0, 0)])
    _init(temp_db)
    _set("601728", ["自选"])                # 用户把它移出高端内存
    _init(temp_db)                         # 再启动一次
    assert _rows()["601728"]["groups"] == ["自选"]


def test_migration_survives_when_the_old_columns_cannot_be_dropped(temp_db):
    """SQLite < 3.35 没有 DROP COLUMN。删不掉时得把老列清空, 否则下次启动照着老列
    再搬一遍, 把用户后来移出的分组复活。这里用"给 grp 建索引"制造删列失败
    (SQLite 拒绝删掉被索引引用的列), 走的是同一条 except 分支。"""
    _legacy_db(temp_db, [("601728", "中国电信", "高端内存", 0, 0)])
    con = sqlite3.connect(temp_db)
    con.execute("CREATE INDEX ix_wl_grp ON watchlist(grp)")
    con.commit(); con.close()

    _init(temp_db)
    assert _rows()["601728"]["groups"] == ["高端内存"]     # 分组照样搬到位
    assert "grp" in _cols(temp_db)                         # 列确实没删掉
    _set("601728", ["自选"])
    _init(temp_db)
    assert _rows()["601728"]["groups"] == ["自选"]          # 但高端内存不会复活


def test_migration_is_idempotent_on_a_new_db(temp_db):
    _init(temp_db)
    _add("600519")
    _set("600519", ["金矿", "博弈"])
    _init(temp_db)
    assert _rows()["600519"]["groups"] == ["博弈", "金矿"]


# ── 批量编辑 ────────────────────────────────────────────

def _batch(codes, **kw):
    from database import batch_edit_watchlist
    return asyncio.run(batch_edit_watchlist(codes, **kw))


def test_batch_add_groups_keeps_existing(temp_db):
    _init(temp_db)
    _add("300999"); _add("605999")
    _set("605999", ["博弈"])
    r = _batch(["300999", "605999"], add=["金矿"])
    rows = _rows()
    assert rows["300999"]["groups"] == ["自选", "金矿"]
    assert rows["605999"]["groups"] == ["博弈", "金矿"]
    assert r == {"changed": 2, "skipped": 0, "capped": 0}


def test_batch_new_members_go_to_end_in_given_order(temp_db):
    _init(temp_db)
    _add("000991"); _add("300999"); _add("605999")
    _set("000991", ["金矿"])
    _batch(["605999", "300999"], add=["金矿"])
    o = {c: r["group_orders"]["金矿"] for c, r in _rows().items()}
    assert o["000991"] < o["605999"] < o["300999"]


def test_batch_remove_group_falls_back_to_default(temp_db):
    _init(temp_db)
    _add("300999"); _add("605999")
    _set("300999", ["金矿"])
    _set("605999", ["金矿", "博弈"])
    _batch(["300999", "605999"], remove=["金矿"])
    rows = _rows()
    assert rows["300999"]["groups"] == ["自选"]
    assert rows["605999"]["groups"] == ["博弈"]


def test_batch_move_between_groups(temp_db):
    _init(temp_db)
    _add("300999")
    _set("300999", ["自选", "博弈"])
    _batch(["300999"], add=["金矿"], remove=["自选"])
    assert _rows()["300999"]["groups"] == ["博弈", "金矿"]


def test_batch_drop_removes_rows_and_groups(temp_db):
    _init(temp_db)
    _add("300999"); _add("605999")
    _set("300999", ["金矿"])
    r = _batch(["300999"], drop=True)
    assert set(_rows()) == {"605999"}
    con = sqlite3.connect(temp_db)
    try:
        assert con.execute("SELECT COUNT(*) FROM watchlist_group WHERE stock_code='300999'").fetchone()[0] == 0
    finally:
        con.close()
    assert r["changed"] == 1


def test_batch_skips_codes_not_in_pool(temp_db):
    _init(temp_db)
    _add("300999")
    r = _batch(["300999", "000991"], add=["金矿"])
    assert set(_rows()) == {"300999"}
    assert r["skipped"] == 1 and r["changed"] == 1


def test_batch_respects_group_cap(temp_db):
    from database import WATCH_MAX_GROUPS_PER_STOCK as N
    _init(temp_db)
    _add("300999")
    _set("300999", [f"g{i}" for i in range(N - 1)])
    r = _batch(["300999"], add=["甲", "乙"])
    assert len(_rows()["300999"]["groups"]) == N
    assert r["capped"] == 1


def test_batch_route_validates_and_dispatches(temp_db, monkeypatch):
    _init(temp_db)
    _add("300999"); _add("605999")
    c = TestClient(app)
    assert c.post("/api/market/watchlist-batch", json={"codes": [], "op": "remove"}).status_code == 400
    assert c.post("/api/market/watchlist-batch", json={"codes": ["300999"], "op": "add_groups"}).status_code == 400
    assert c.post("/api/market/watchlist-batch",
                  json={"codes": ["300999"], "op": "move", "groups": ["自选"], "from_group": "自选"}).status_code == 400
    r = c.post("/api/market/watchlist-batch",
               json={"codes": ["300999", "605999"], "op": "move", "groups": ["金矿"], "from_group": "自选"})
    assert r.status_code == 200 and r.json()["changed"] == 2
    assert {k: v["groups"] for k, v in _rows().items()} == {"300999": ["金矿"], "605999": ["金矿"]}
    r = c.post("/api/market/watchlist-batch", json={"codes": ["300999"], "op": "remove"})
    assert r.json()["changed"] == 1 and set(_rows()) == {"605999"}


# ── 批量添加 ────────────────────────────────────────────

def _import(items, group=""):
    from database import import_watchlist
    return asyncio.run(import_watchlist(items, group))


def test_import_new_codes_go_to_default_group(temp_db):
    _init(temp_db)
    r = _import([("300999", "虚构甲", 10.0), ("605999", "虚构乙", 20.0)])
    rows = _rows()
    assert r == {"added": 2, "existed": 0, "grouped": 0}
    assert rows["300999"]["groups"] == ["自选"] and rows["605999"]["added_price"] == 20.0
    o = rows["300999"]["group_orders"]["自选"], rows["605999"]["group_orders"]["自选"]
    assert o[0] < o[1]


def test_import_into_group_only(temp_db):
    _init(temp_db)
    _import([("300999", "虚构甲", 10.0)], group="金矿")
    assert _rows()["300999"]["groups"] == ["金矿"]


def test_import_existing_keeps_added_info_and_gains_group(temp_db):
    _init(temp_db)
    _add("300999", "虚构甲", 5.0)
    con = sqlite3.connect(temp_db)
    con.execute("UPDATE watchlist SET added_at='2026-01-02' WHERE stock_code='300999'")
    con.commit(); con.close()
    r = _import([("300999", "虚构甲", 99.0), ("300999", "虚构甲", 99.0)], group="金矿")
    row = _rows()["300999"]
    assert r == {"added": 0, "existed": 1, "grouped": 1}
    assert row["added_price"] == 5.0 and row["added_at"] == "2026-01-02"
    assert row["groups"] == ["自选", "金矿"]


def test_import_existing_without_group_changes_nothing(temp_db):
    _init(temp_db)
    _add("300999")
    r = _import([("300999", "X", 1.0)])
    assert r == {"added": 0, "existed": 1, "grouped": 0}
    assert _rows()["300999"]["groups"] == ["自选"]


def test_parse_import_text(monkeypatch):
    import services.stock_agent as sa
    import services.market_data as md
    from services.watchlist import parse_import_text

    async def maps():
        return {"虚构甲": "300999", "虚构乙": "605999"}, {"300999": "虚构甲", "605999": "虚构乙", "000991": "虚构丙"}

    async def us(t):
        return {"code": f"US.{t}", "name": t} if t == "ZZZQ" else None

    async def resolve(q):
        if q == "虚构港股":
            return {"code": "HK.09999", "name": "虚构港股"}
        return {"candidates": [{"code": "000991", "name": "虚构丙"}]}

    async def quotes(codes):
        return {c: {"price": 1.0, "stock_name": "港甲"} for c in codes if c == "HK.09998"}

    monkeypatch.setattr(sa, "_code_name_maps", maps)
    monkeypatch.setattr(sa, "_us_ticker_hit", us)
    monkeypatch.setattr(sa, "_tool_resolve_stock", resolve)
    monkeypatch.setattr(md, "get_realtime_quotes", quotes)
    text = "300999 虚构甲 12.5 +3.2%\nsh605999、虚构乙；000991.SZ\n09998 hk9997 ZZZQ 虚构港股 虚构"
    r = asyncio.run(parse_import_text(text))
    got = {x["code"]: x["token"] for x in r["resolved"]}
    assert got == {"300999": "300999", "605999": "sh605999", "000991": "000991.SZ",
                   "HK.09998": "09998", "US.ZZZQ": "ZZZQ", "HK.09999": "虚构港股"}
    un = {x["token"]: x["suggest"] for x in r["unresolved"]}
    assert set(un) == {"hk9997", "虚构"}
    assert un["虚构"] == [{"code": "000991", "name": "虚构丙"}]
    assert r["truncated"] is False


def test_import_route(temp_db, monkeypatch):
    _init(temp_db)
    import services.market_data as md

    async def quotes(codes):
        return {c: {"price": 3.0, "stock_name": "虚构甲"} for c in codes}

    monkeypatch.setattr(md, "get_realtime_quotes", quotes)
    c = TestClient(app)
    assert c.post("/api/market/watchlist-import", json={"codes": []}).status_code == 400
    r = c.post("/api/market/watchlist-import", json={"codes": ["300999", "300999"], "group": "博弈"})
    assert r.json() == {"ok": True, "added": 1, "existed": 0, "grouped": 0}
    assert _rows()["300999"]["groups"] == ["博弈"] and _rows()["300999"]["name"] == "虚构甲"

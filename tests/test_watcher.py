"""盯盘员: 发现按 key 去重, 同检测器的新状态取代旧的; 现金对上了不产出, 对不上给出差额。"""
import asyncio

import pytest

import database
from services import watcher as w


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("config.config.db_path", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())


def test_drift_finding_tolerance_and_text():
    assert w.drift_finding(1000.4, 1000.0, "现金", "2026-01-05") is None
    f = w.drift_finding(1500.0, 1000.0, "现金", "2026-01-05")
    assert f["key"] == "books_drift::2026-01-05::1500.00" and "+500.00" in f["body"]


def test_record_dedups_and_new_state_supersedes(db):
    d = "2026-01-05"
    f1 = w.drift_finding(1500.0, 1000.0, "现金", d)
    assert len(asyncio.run(w.record([f1], d))) == 1
    assert asyncio.run(w.record([f1], d)) == []                       # 同 key 只记一次
    f2 = w.drift_finding(1800.0, 1000.0, "现金", d)
    asyncio.run(w.resolve("books_drift", d, keep_key=f2["key"]))
    asyncio.run(w.record([f2], d))
    items = asyncio.run(w.list_findings(d))["items"]
    assert [x["key"] for x in items] == [f2["key"]]                    # 旧状态收起, 只剩新的
    assert asyncio.run(w.dismiss(items[0]["id"]))
    assert asyncio.run(w.list_findings(d))["items"] == []


def test_broker_cash_reads_number_and_fails_quietly():
    assert asyncio.run(w._broker_cash("echo 12,345.67")) == 12345.67
    assert asyncio.run(w._broker_cash("false")) is None                # 客户端没开: 非零退出
    assert asyncio.run(w._broker_cash("echo 没有")) is None


def test_cash_cmd_never_read_from_db(db, monkeypatch):
    """要执行的命令只认本机 config.py; 数据库(导入备份可写)里的同名键不执行。"""
    monkeypatch.setattr("config.config.watch_cash_cmd", "", raising=False)
    asyncio.run(database.set_config("watch_cash_cmd", "touch /nonexistent/x"))
    asyncio.run(database.set_config("watch_cash_asset_id", "1"))
    ran = []
    monkeypatch.setattr(w, "_broker_cash", lambda cmd: ran.append(cmd))
    assert asyncio.run(w.check_books_drift()) is None and ran == []


CAT = {"flags": {"a": {"label": "甲"}, "b": {"label": "乙"}, "c": {"label": "丙"}},
       "by_count": {"post": {"2": {"ex20": -0.015, "bad": 0.14}, "3": {"ex20": -0.029, "bad": 0.26}}}}


def test_risk_findings_fire_only_when_hits_rise_to_two_or_more():
    rf = {"date": "2026-01-05", "prev_date": "2026-01-02", "catalog": CAT,
          "flags": {"300999": ["a", "b"], "688999": ["a", "b"]}, "prev_flags": {"300999": ["a"], "688999": ["a", "c"]}}
    out = w.risk_findings({"300999": "虚构甲", "688999": "虚构乙"}, rf, "2026-01-05")
    assert [f["code"] for f in out] == ["300999"]                       # 688999 没变多, 不报
    f = out[0]
    assert f["title"] == "虚构甲 排雷命中 2 项(前一交易日 1 项)" and "甲 · 乙" in f["body"] and "-1.5%" in f["body"]
    assert w.risk_findings({"300999": "虚构甲"}, {**rf, "date": "2026-01-02"}, "2026-01-05") == []   # 当日文件还没出
    assert w.risk_findings({"300999": "虚构甲"}, {"stale": True}, "2026-01-05") == []


def test_fresh_buy_finding_drops_realtime_dim_and_adds_hits():
    act = {"id": 7, "stock_code": "300999", "shares": 500.0, "price": 12.3, "trade_date": "2026-01-05"}
    mir = {"matches": [{"dim": "买入价相对前一日收盘", "label": "涨 2%~5% 时买", "n_done": 9, "mean20_pct": -1, "win20": 0.3, "metric": "绝对涨跌"},
                       {"dim": "买入前 20 个交易日的涨跌", "label": "+20% 以上", "n_done": 12, "mean20_pct": -6.5, "win20": 0.25, "metric": "绝对涨跌"}]}
    rf = {"date": "2026-01-02", "catalog": CAT, "flags": {"300999": ["a", "b", "c"]}, "in_pool": {"300999": True}}
    f = w.fresh_buy_finding([act, {**act, "id": 8, "shares": 200.0, "price": 12.5}], "虚构甲", mir, rf)
    assert "买入 500 股 @12.3 / 200 股 @12.5" in f["body"] and f["key"] == "fresh_buy::300999::2026-01-05::7,8"
    assert "涨 2%~5%" not in f["body"] and "12 次, 平均 -6.5%, 赚钱 25%" in f["body"]
    assert f["level"] == "warn" and "排雷命中 3 项" in f["body"] and "01-02 收盘" in f["body"]


def test_fresh_buys_first_run_sets_watermark_then_reports_new(db, monkeypatch):
    import sys
    import types
    import services.analyzers as az

    async def mir(code):
        return {"matches": []}

    async def held():
        return {"300999": "虚构甲"}
    # entry_review 导入时建 asyncio.Lock(3.9 要有事件循环), 测试里整个换掉
    monkeypatch.setitem(sys.modules, "services.entry_review", types.SimpleNamespace(mirror=mir))
    monkeypatch.setattr(w, "_held_a_shares", held)
    monkeypatch.setattr(az, "risk_flags_all", lambda codes: {"stale": True})

    async def add(code, act):
        con = await database.get_db()
        await con.execute("INSERT INTO position_actions (stock_code, action_type, price, shares, trade_date) VALUES (?,?,?,?,?)",
                          (code, act, 10.0, 100, w.today()))
        await con.commit()
        await con.close()
    asyncio.run(add("300999", "BUY"))
    assert asyncio.run(w.check_fresh_buys()) == []                       # 第一次只记水位
    asyncio.run(add("300999", "BUY"))
    asyncio.run(add("300999", "BUY"))
    asyncio.run(add("300999", "SELL"))
    out = asyncio.run(w.check_fresh_buys())
    assert [f["detector"] for f in out] == ["fresh_buy"] and out[0]["title"] == "新买入 虚构甲"   # 两单合成一条
    asyncio.run(add("300999", "BUY"))                                    # 同一天又买一单: 旧的收起, 只留最新合并的
    asyncio.run(w.check_fresh_buys())
    items = asyncio.run(w.list_findings())["items"]
    assert len(items) == 1 and items[0]["body"].count("100 股 @10") == 4    # 当天 4 单都列出(含水位前那单)
    assert asyncio.run(w.check_fresh_buys()) == []                       # 水位已推进


EV = {"tail_pull": {"min_tail30": 0.02, "lines": ["历史甲"]}, "sealed_close": {"normal": ["普通板"], "yizi": ["一字板"]}}
PTS = [{"time": "14:29", "price": 9.9}, {"time": "14:30", "price": 10.0}, {"time": "14:31", "price": 10.1}]


def test_tail_pull_fires_at_threshold_and_not_on_limit_close():
    q = {"price": 10.25, "prev_close": 10.0, "open": 10.0, "low": 9.8}
    f = w.close_findings("300999", "虚构甲", PTS, q, 20.0, EV, "2026-01-05")
    assert [x["detector"] for x in f] == ["tail_pull"] and "+2.5%" in f[0]["title"] and "历史甲" in f[0]["body"]
    assert w.close_findings("300999", "虚构甲", PTS, {**q, "price": 10.15}, 20.0, EV, "2026-01-05") == []
    # 收盘跌停没有尾盘行为可言
    assert w.close_findings("300999", "虚构甲", [{"time": "14:30", "price": 7.5}], {**q, "price": 8.0}, 20.0, EV, "2026-01-05") == []


def test_sealed_close_tells_yizi_apart():
    q = {"price": 11.0, "prev_close": 10.0, "open": 10.5, "low": 10.3}
    f = w.close_findings("605999", "虚构乙", PTS, q, 10.0, EV, "2026-01-05")
    assert [x["detector"] for x in f] == ["sealed_close"] and "普通板" in f[0]["body"] and "一字" not in f[0]["title"]
    f = w.close_findings("605999", "虚构乙", PTS, {**q, "open": 11.0, "low": 11.0}, 10.0, EV, "2026-01-05")
    assert f[0]["title"].endswith("(一字板)") and "一字板" in f[0]["body"]


def test_risk_body_notes_lagging_lhb_date():
    rf = {"date": "2026-01-05", "prev_date": "2026-01-02", "lhb_through": "2026-01-02", "catalog": CAT,
          "flags": {"300999": ["a", "b"]}, "prev_flags": {}}
    f = w.risk_findings({"300999": "虚构甲"}, rf, "2026-01-05")[0]
    assert f["body"].endswith("数据截至 01-05 收盘(龙虎榜截至 01-02)")


def test_watchlist_findings_are_tagged_info_and_listed_after_holdings(db, monkeypatch):
    import services.analyzers as az

    async def held():
        return {"300999": "虚构甲"}

    async def wl():
        return [{"code": "300999", "name": "虚构甲"}, {"code": "688999", "name": "虚构乙"}, {"code": "hk00999", "name": "虚构港"}]
    monkeypatch.setattr(w, "_held_a_shares", held)
    monkeypatch.setattr(database, "list_watchlist", wl)
    rf = {"date": w.today(), "catalog": CAT, "flags": {"300999": ["a", "b"], "688999": ["a", "b", "c"]}, "prev_flags": {}}
    asked = []
    monkeypatch.setattr(az, "risk_flags_all", lambda codes: asked.append(codes) or rf)
    asyncio.run(w.record(as_wl := w.as_watchlist([{"key": "x::688999", "detector": "tail_pull", "code": "688999",
                                                      "level": "warn", "title": "虚构乙 尾盘", "evidence": {}}])))
    assert as_wl[0]["title"] == "观察池 虚构乙 尾盘" and as_wl[0]["level"] == "info"
    out = asyncio.run(w.check_risk_hits())
    assert asked == [["300999", "688999"]]                               # 已持有的不重复, 港股不进
    assert [(f["code"], f["level"]) for f in out] == [("300999", "warn"), ("688999", "info")]
    assert out[1]["title"].startswith("观察池 虚构乙 排雷命中 3 项")
    items = asyncio.run(w.list_findings())["items"]
    assert [i["code"] for i in items] == ["300999", "688999", "688999"]  # 持仓的在前, 观察池按写入顺序

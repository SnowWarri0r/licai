"""交易计划: 触发/失效判定、计划对照成交、落库与盯价推送。"""
import asyncio
import os
import tempfile

import pytest

from services import trade_plan as tp


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr("config.config.db_path", path)
    from database import init_db
    asyncio.run(init_db())

    async def name_of(code):
        return {"600036": "招商银行", "588080": "科创50ETF易方达"}.get(code, "")
    monkeypatch.setattr(tp, "_name_of", name_of)
    yield path
    os.unlink(path)


def _p(**kw):
    base = {"id": 1, "code": "600036", "name": "招商银行", "side": "BUY", "trigger_op": "<=",
            "trigger_price": 45.0, "qty": 200, "invalid_op": "<=", "invalid_price": 43.5,
            "status": "active", "reason": "回踩20日线"}
    return {**base, **kw}


def test_evaluate_trigger_and_invalidation():
    assert tp.evaluate([_p()], {"600036": {"price": 45.2}}) == []
    assert tp.evaluate([_p()], {"600036": {"price": 44.9}})[0][1] == "triggered"
    # 跳空直接到失效位: 前提已不在, 按失效处理
    assert tp.evaluate([_p()], {"600036": {"price": 43.0}})[0][1] == "invalidated"
    # 已触发的不再重复触发, 但仍盯失效
    assert tp.evaluate([_p(status="triggered")], {"600036": {"price": 44.9}}) == []
    assert tp.evaluate([_p(status="triggered")], {"600036": {"price": 43.4}})[0][1] == "invalidated"


def test_compare_verdicts():
    plans = [_p(id=1, status="triggered", triggered_at="10:01:00"),
             _p(id=2, code="588080", name="科创50ETF易方达", side="SELL", trigger_op=">=", trigger_price=0.9,
                qty=None, invalid_op=None, invalid_price=None)]
    fills = [{"code": "600036", "side": "BUY", "price": 44.9, "shares": 200, "time": "10:02"},
             {"code": "588080", "side": "SELL", "price": 0.863, "shares": 40400, "time": "09:35"},
             {"code": "601398", "side": "SELL", "price": 11.47, "shares": 1000, "time": "09:37"}]
    r = tp.compare(plans, fills)
    v = {p["code"]: p["对照"] for p in r["plans"]}
    assert v == {"600036": "按计划执行", "588080": "没到触发价就成交"}
    assert [f["code"] for f in r["计划外成交"]] == ["601398"]
    assert r["plans"][0]["数量偏差"] == 0


def test_compare_triggered_not_executed_and_after_invalidation():
    r = tp.compare([_p(status="triggered")], [])
    assert r["plans"][0]["对照"] == "触发未执行"
    r = tp.compare([_p(status="invalidated", invalidated_at="10:30:00")],
                   [{"code": "600036", "side": "BUY", "price": 43.2, "shares": 200, "time": "10:40"}])
    assert r["plans"][0]["对照"] == "失效后仍成交"


def test_add_validates_and_monitor_notifies_once(temp_db, monkeypatch):
    day = tp.today()

    async def main():
        r = await tp.add_plans([
            {"code": "600036", "side": "BUY", "trigger_op": "<=", "trigger_price": 45, "qty": 200, "plan_date": day},
            {"code": "600036", "side": "买", "trigger_op": "<=", "trigger_price": 45, "plan_date": day}])
        assert len(r["saved"]) == 1 and len(r["rejected"]) == 1
        assert r["saved"][0]["name"] == "招商银行"

        async def quotes(codes):
            return {"600036": {"price": 44.8}}
        import services.market_data as md
        monkeypatch.setattr(md, "get_realtime_quotes", quotes)
        sent = []

        async def notify(text, ev):
            sent.append(ev)
        await tp.monitor_once(notify)
        await tp.monitor_once(notify)            # 第二轮不该重复推
        return sent, await tp.list_plans(day)

    sent, plans = asyncio.run(main())
    assert len(sent) == 1 and sent[0]["status"] == "triggered" and "是否执行由你决定" in sent[0]["text"]
    assert plans[0]["status"] == "triggered" and plans[0]["triggered_price"] == 44.8


def test_expire_old(temp_db):
    async def main():
        await tp.add_plans([{"code": "600036", "side": "BUY", "trigger_op": "<=", "trigger_price": 45,
                             "plan_date": "2020-01-02"}])
        n = await tp.expire_old()
        return n, await tp.list_plans("2020-01-02")
    n, plans = asyncio.run(main())
    assert n == 1 and plans[0]["status"] == "expired"


def test_replace_cancels_old(temp_db):
    day = tp.today()

    async def main():
        r = await tp.add_plans([{"code": "600036", "side": "BUY", "trigger_op": "<=", "trigger_price": 45, "plan_date": day}])
        old = r["saved"][0]["id"]
        await tp.add_plans([{"code": "600036", "side": "BUY", "trigger_op": "<=", "trigger_price": 44,
                             "plan_date": day, "replace_id": old}])
        return {p["trigger_price"]: p["status"] for p in await tp.list_plans(day)}
    assert asyncio.run(main()) == {45.0: "cancelled", 44.0: "active"}

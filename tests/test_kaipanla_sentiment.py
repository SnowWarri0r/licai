"""开盘啦情绪面: 免登录多接口聚合。守 —— 非交易日不当数据、部分接口挂不连累其他、字段口径。

这是东财之外的**第二数据源**, 意义在交叉印证(尤其'实际涨停'两个源口径差几只)。所以最该钉的是:
单个接口失败/返回 1020(非交易日) 时, 那一块干净地缺席, 而不是变成 0 或半截数字。
"""
import asyncio

import pytest

import services.kaipanla_sentiment as ks


@pytest.fixture(autouse=True)
def _clear_cache():
    """模块级 _cache 按天缓存, 会在用例间串数据 —— 每个用例前清掉。"""
    ks._cache.clear()
    yield


def _fake_post(mapping):
    """按 (host, action) 返回预置响应。缺的键返回 None(模拟接口抖动)。"""
    def _p(host, extra, day):
        return mapping.get((host, extra.get("a")))
    return _p


def test_non_trading_day_1020_is_not_treated_as_data(monkeypatch):
    """1020=非交易日/无数据。每个接口都返 1020 时, 整块应为空 dict, 不是一堆 0。"""
    monkeypatch.setattr(ks, "_post", lambda host, extra, day: {"errcode": "1020", "errmsg": "参数出错"})
    r = asyncio.run(ks.sentiment("2026-09-05"))
    assert r == {}


def test_one_endpoint_down_does_not_sink_the_rest(monkeypatch):
    """跳水榜接口挂了(返 None), 多空风向标照常出 —— 单点失败不连累其他源。"""
    monkeypatch.setattr(ks, "_post", _fake_post({
        ("apphis", "SharpWithdrawal"): None,
        ("apphwhq", "MoodNumCount"): {"list": {"SZJS": 3167, "XDJS": 2196, "ZTJS": 93,
                                                "DTJS": 2, "bl": -4.17}, "errcode": "0"},
    }))
    r = asyncio.run(ks.sentiment("2026-09-04"))
    assert "跳水榜" not in r
    assert r["多空风向标"]["量能较昨同期%"] == -4.17 and r["多空风向标"]["上涨家数"] == 3167


def test_sharp_withdrawal_parses_rows(monkeypatch):
    monkeypatch.setattr(ks, "_post", _fake_post({
        ("apphis", "SharpWithdrawal"): {
            "num": 12, "errcode": "0",
            "info": [["002909", "集泰股份", -9.99, -15.8693, 7.21],
                     ["688001", "华兴源创", -5.36, -14.2226, 48.55],
                     ["600000", "浦发", 1.0]]},   # 短行(<4列)要被丢, 不能错位读
    }))
    r = asyncio.run(ks.sentiment("2026-09-04"))
    sw = r["跳水榜"]
    assert sw["只数"] == 12
    assert len(sw["前几只"]) == 2                       # 第三条短行被丢
    assert sw["前几只"][0] == {"代码": "002909", "名称": "集泰股份",
                              "当日涨跌%": -9.99, "回撤%": -15.87}


def test_ladder_needs_full_array(monkeypatch):
    """连板梯队 info 是定长数组(≥12), 变短就是协议改了 —— 宁可不给, 不能按老下标错位。"""
    monkeypatch.setattr(ks, "_post", _fake_post({
        ("apphis", "ZhangTingExpression"): {"info": [32, 6, 0], "errcode": "0"},
    }))
    assert "连板梯队" not in asyncio.run(ks.sentiment("2026-09-04"))


def test_ladder_parses_market_verdict(monkeypatch):
    monkeypatch.setattr(ks, "_post", _fake_post({
        ("apphis", "ZhangTingExpression"): {
            "info": [32, 6, 0, 1, 17.1429, 0, 33.3333, 55.1724, -0.627, -0.893, -0.561,
                     "题材存在炒作机会"], "errcode": "0"},
    }))
    la = asyncio.run(ks.sentiment("2026-09-04"))["连板梯队"]
    assert la["首板"] == 32 and la["市场评价"] == "题材存在炒作机会"
    assert la["昨连板今表现%"] == -0.89           # a[9], 高位资金接力赚钱效应


def test_actual_zt_can_differ_from_pool(monkeypatch):
    """实际涨停(SJZT, 盘中触及)与东财池(封住到收盘)本就可能差几只 —— 这正是第二源的价值,
    如实带出两个字段, 不合并成一个。"""
    monkeypatch.setattr(ks, "_post", _fake_post({
        ("apphis", "HisZhangFuDetail"): {"info": {"SJZT": "39", "SJDT": "9", "ZT": "41", "DT": "9"},
                                          "errcode": "0"},
    }))
    z = asyncio.run(ks.sentiment("2026-09-04"))["实际涨跌停"]
    assert z["实际涨停"] == 39 and z["涨停"] == 41    # 两个口径都在


def test_source_tag_present_when_any_data(monkeypatch):
    monkeypatch.setattr(ks, "_post", _fake_post({
        ("apphwhq", "MoodNumCount"): {"list": {"bl": -4.17}, "errcode": "0"}}))
    r = asyncio.run(ks.sentiment("2026-09-04"))
    assert r["数据源"] == "开盘啦(免登录)"


def test_empty_stays_empty_no_source_tag(monkeypatch):
    """全空时连数据源标签都不加 —— 空 dict 让调用方能干净地判'这块没有'。"""
    monkeypatch.setattr(ks, "_post", lambda h, e, d: None)
    assert asyncio.run(ks.sentiment("2026-09-04")) == {}


def test_agent_tool_desc_mentions_second_source():
    from services.stock_agent import _TOOLS
    t = next(t for t in _TOOLS if t["name"] == "get_market_sentiment")
    assert "开盘啦" in t["description"] and "跳水" in t["description"]

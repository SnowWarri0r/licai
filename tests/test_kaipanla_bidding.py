"""开盘啦板块竞价异动: 解析 List1/2/3 三分组 + 空数据/登录态失效的如实报告。

守的核心与 test_kaipanla_auth 同源: Token 失效要**明确 need_login**, 非竞价时段的空
要说清是"时段未产出"而不是故障或登录过期 —— 否则用户对着空界面会误以为号坏了。
接口只有当日、无历史(apphis 历史 host 已下线), 这条约束也在这里钉住。
"""
import asyncio

import pytest

import services.kaipanla_bidding as kb


def _rows(*codes):
    # 行=字符串数组: [0]代码 [1]名称 [2]竞价换手 [3]涨幅 [4]_ [5]主力净额(元)
    return [[c, f"板块{c}", "3.21%", "5.66", "x", str(2_1350_0000)] for c in codes]


def test_row_maps_columns_and_folds_yi():
    r = kb._row(["801660", "通信", "4.10%", "6.05", "_", "581600000"])
    assert r["代码"] == "801660" and r["名称"] == "通信"
    assert r["竞价换手"] == "4.10%"
    assert r["竞价涨幅"] == 6.05
    assert r["竞价主力净额亿"] == 5.82   # 581600000 元 → 5.82 亿


def test_row_short_array_is_safe():
    # 只有代码+名称也不炸, 缺列给 None
    r = kb._row(["801001", "芯片"])
    assert r["代码"] == "801001" and r["竞价主力净额亿"] is None
    assert kb._row(["only_one"]) is None
    assert kb._row(None) is None


def test_parses_three_sections(monkeypatch):
    async def _data(host, params):
        assert params["a"] == "GetBKJJ_W36" and params["c"] == "StockBidYiDong"
        return {"errcode": "0", "Day": "2026-09-08", "State": 1,
                "List1": _rows("801660"), "List2": _rows("801464", "801188"),
                "List3": []}
    monkeypatch.setattr(kb, "call", _data)
    r = asyncio.run(kb.plate_bidding())
    assert r["有数据"] is True and r["date"] == "2026-09-08"
    groups = {g["分组"]: g for g in r["分组"]}
    assert groups["今日新增竞价异动"]["板块数"] == 1
    assert groups["昨日爆发板块延续异动"]["板块数"] == 2
    assert groups["其他异动板块"]["板块数"] == 0
    assert groups["今日新增竞价异动"]["板块"][0]["竞价主力净额亿"] == 2.13


def test_empty_outside_window_is_explained_not_error(monkeypatch):
    """三段全空 → 有数据=false + note 说明是时段问题(且点明无历史), 不报错、不 need_login。"""
    async def _empty(host, params):
        return {"errcode": "0", "Day": "2026-09-08", "State": 0,
                "List1": [], "List2": [], "List3": []}
    monkeypatch.setattr(kb, "call", _empty)
    r = asyncio.run(kb.plate_bidding())
    assert r["有数据"] is False
    assert "error" not in r and "need_login" not in r
    assert "历史" in r["note"]


def test_auth_error_reports_need_login(monkeypatch):
    async def _boom(host, params):
        raise kb.KplAuthError("Token 失效")
    monkeypatch.setattr(kb, "call", _boom)
    r = asyncio.run(kb.plate_bidding())
    assert r.get("need_login") is True and "失效" in r["error"]


def test_bad_errcode_reports_raw(monkeypatch):
    async def _bad(host, params):
        return {"errcode": "1020", "errmsg": "参数出错"}
    monkeypatch.setattr(kb, "call", _bad)
    r = asyncio.run(kb.plate_bidding())
    assert "error" in r and r["raw_errcode"] == "1020"


def test_agent_tool_registered_and_flags_login():
    from services.stock_agent import _TOOLS, _EXECUTORS
    assert "get_kpl_bidding" in _EXECUTORS
    t = next(t for t in _TOOLS if t["name"] == "get_kpl_bidding")
    assert "竞价" in t["description"] and "need_login" in t["description"]

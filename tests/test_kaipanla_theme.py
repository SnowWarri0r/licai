"""开盘啦月度热门题材榜: 按热度降序编号 + 空/登录态失效如实报。"""
import asyncio

import services.kaipanla_theme as kt


def test_parses_ranked_themes(monkeypatch):
    async def _data(host, params):
        assert params["a"] == "GetHotPlateByMonth"
        return {"errcode": "0", "list": [{"title": "通信", "code": "801660"},
                                         {"title": "芯片", "code": "801001"}]}
    monkeypatch.setattr(kt, "call", _data)
    r = asyncio.run(kt.hot_themes())
    assert r["有数据"] is True and r["数量"] == 2
    assert r["题材榜"][0] == {"排名": 1, "题材": "通信", "代码": "801660"}
    assert r["题材榜"][1]["排名"] == 2


def test_empty_list_is_explicit(monkeypatch):
    async def _empty(host, params):
        return {"errcode": "0", "list": []}
    monkeypatch.setattr(kt, "call", _empty)
    r = asyncio.run(kt.hot_themes())
    assert r["有数据"] is False and "error" not in r


def test_auth_error_reports_need_login(monkeypatch):
    async def _boom(host, params):
        raise kt.KplAuthError("Token 失效")
    monkeypatch.setattr(kt, "call", _boom)
    r = asyncio.run(kt.hot_themes())
    assert r.get("need_login") is True and "失效" in r["error"]


def test_agent_tool_registered():
    from services.stock_agent import _TOOLS, _EXECUTORS
    assert "get_kpl_hot_theme" in _EXECUTORS
    t = next(t for t in _TOOLS if t["name"] == "get_kpl_hot_theme")
    assert "题材" in t["description"] and "need_login" in t["description"]

"""开盘啦机构增仓榜: 列位映射(合计口径核对过)+ 增/减仓分榜 + 季报日回退 + 登录态。"""
import asyncio

import services.kaipanla_inst_position as ki


def _row(code, name, zcje_yuan, hold_yuan, pct):
    # [0]码[1]名[2]增仓金额元[3]_[4]持仓市值元[5]_[6]占流通%[7]流通市值元[8]flag
    return [code, name, str(zcje_yuan), "-1", str(hold_yuan), "-1", str(pct), "1e13", "1"]


def test_row_maps_signed_yi_columns():
    r = ki._row(_row("801660", "通信", -22854549936, 411071079859, 38.25))
    assert r["代码"] == "801660"
    assert r["增仓金额亿"] == -228.55      # 带符号
    assert r["机构持仓市值亿"] == 4110.71
    assert r["持仓占流通%"] == 38.25
    assert ki._row(["x"]) is None


def test_splits_increase_and_decrease_sorted(monkeypatch):
    async def _data(host, params):
        return {"errcode": "0", "Date": "2026-06-30", "Sum_ZCJE": "5e10",
                "DateList": ["2026-06-30", "2026-03-31"],
                "List": [_row("A", "增多", 3e10, 1e11, 20),
                         _row("B", "增少", 1e10, 1e11, 10),
                         _row("C", "减多", -4e10, 1e11, 5),
                         _row("D", "减少", -1e10, 1e11, 8)]}
    monkeypatch.setattr(ki, "call", _data)
    r = asyncio.run(ki.inst_position(top=5))
    assert r["有数据"] is True and r["date"] == "2026-06-30"
    assert [x["名称"] for x in r["增仓榜"]] == ["增多", "增少"]      # 降序
    assert [x["名称"] for x in r["减仓榜"]] == ["减多", "减少"]      # 最负在前
    assert r["全市场机构增仓合计亿"] == 500.0


def test_falls_back_to_latest_quarter_when_date_has_no_rows(monkeypatch):
    """传的日子不是季报日 → List 空但带 DateList → 自动回退到最新季报重取。"""
    calls = []

    async def _data(host, params):
        calls.append(params.get("Date"))
        if params.get("Date") == "2026-09-05":      # 非季报日, 空
            return {"errcode": "0", "List": [], "DateList": ["2026-06-30", "2026-03-31"]}
        return {"errcode": "0", "Date": "2026-06-30", "Sum_ZCJE": "1e10",
                "DateList": ["2026-06-30"], "List": [_row("A", "板块", 2e10, 1e11, 20)]}
    monkeypatch.setattr(ki, "call", _data)
    r = asyncio.run(ki.inst_position(date="2026-09-05"))
    assert r["有数据"] is True and r["date"] == "2026-06-30"
    assert "2026-06-30" in calls                     # 确实回退重取了


def test_auth_error_reports_need_login(monkeypatch):
    async def _boom(host, params):
        raise ki.KplAuthError("Token 失效")
    monkeypatch.setattr(ki, "call", _boom)
    r = asyncio.run(ki.inst_position())
    assert r.get("need_login") is True and "失效" in r["error"]


def test_agent_tool_registered():
    from services.stock_agent import _TOOLS, _EXECUTORS
    assert "get_kpl_inst_position" in _EXECUTORS
    t = next(t for t in _TOOLS if t["name"] == "get_kpl_inst_position")
    assert "机构" in t["description"] and "need_login" in t["description"]

"""新浪板块备用源: 解析 var 对象; 东财板块列表拉不到时报"不可达"而不是"找不到"。"""
import services.sina_boards as sb
import services.stock_agent as sa

_LIST = ('var S_Finance_bankuai_class = {"gn_hwqc":"gn_hwqc,华为汽车,97,22.03,-0.57,-2.56,1673795867,'
         '25497037032,sz002886,4.210,22.030,0.890,沃特股份"}')


def test_parse_list():
    rows = sb._parse_list(_LIST, "概念")
    assert rows == [{"node": "gn_hwqc", "name": "华为汽车", "kind": "概念", "count": 97, "pct": -2.56,
                     "amount_yi": 254.97, "leader": "沃特股份", "leader_code": "002886", "leader_pct": 4.21}]


def test_board_list_unreachable_is_not_reported_as_unknown_name(monkeypatch):
    monkeypatch.setattr(sa, "_fetch_board_code_sync", lambda name: None)
    monkeypatch.setattr(sa, "_sina_board_stocks", lambda name, top: None)
    sa._board_list_cache.clear()
    r = sa._fetch_board_stocks_sync("半导体")
    assert "暂不可达" in r["error"] and "找不到" not in r["error"]


def test_falls_back_to_sina_when_eastmoney_down(monkeypatch):
    monkeypatch.setattr(sa, "_fetch_board_code_sync", lambda name: None)
    monkeypatch.setattr(sb, "find_board", lambda name: {"node": "new_cmyl", "name": "传媒娱乐", "kind": "行业"})
    monkeypatch.setattr(sb, "board_stocks", lambda node, top: [{"name": "x", "code": "000001"}])
    r = sa._fetch_board_stocks_sync("传媒")
    assert r["板块"] == "传媒娱乐" and "新浪" in r["数据源"]

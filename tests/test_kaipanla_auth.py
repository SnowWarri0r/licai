"""开盘啦登录态: 凭证读取 / Token 失效识别 / 席位解析。

登录态是"用户自己账号"这条线, 守的核心: Token 失效时**明确报错**(need_login), 不把登录过期
静默成"今天没数据" —— 后者让用户对着空界面纳闷。凭证不硬编码、不进 git(env/DB 双通道)。
"""
import asyncio
import os

import pytest

import services.kaipanla_auth as ka


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("KPL_UID", raising=False)
    monkeypatch.delenv("KPL_TOKEN", raising=False)
    yield


def test_env_credentials_take_priority(monkeypatch):
    monkeypatch.setenv("KPL_UID", "111"); monkeypatch.setenv("KPL_TOKEN", "aaa")
    assert asyncio.run(ka.credentials()) == ("111", "aaa")


def test_no_credentials_returns_none():
    # 无 env; DB get_config 也没有(测试环境无表) → None
    assert asyncio.run(ka.credentials()) in (None,)


def test_auth_fail_detection_ignores_success():
    assert ka._looks_like_auth_fail({"errcode": "0", "list": []}) is False
    assert ka._looks_like_auth_fail({"errcode": "0"}) is False
    # 真实登录成功响应 errmsg 就是"登录成功", 含"登录"二字 —— errcode=0 时必须短路,
    # 否则会把每一次成功都误判成"登录失效"(这正是变异 if False 会放过的漏洞)。
    assert ka._looks_like_auth_fail({"errcode": "0", "errmsg": "登录成功"}) is False


def test_auth_fail_detection_catches_login_errors():
    assert ka._looks_like_auth_fail({"errcode": "1", "errmsg": "请重新登录"}) is True
    assert ka._looks_like_auth_fail({"errcode": "-1", "errmsg": "Token失效"}) is True
    assert ka._looks_like_auth_fail({"errcode": "2", "errmsg": "身份验证失败"}) is True


def test_non_auth_error_is_not_flagged_as_login_fail():
    """别的业务错误(如参数错)不该被当成登录失效 —— 否则会误导用户去重登。"""
    assert ka._looks_like_auth_fail({"errcode": "1020", "errmsg": "参数出错"}) is False


def test_call_without_credentials_raises_auth_error():
    with pytest.raises(ka.KplAuthError):
        asyncio.run(ka.call("applhb", {"a": "x", "c": "y"}))


def test_call_raises_on_expired_token(monkeypatch):
    monkeypatch.setenv("KPL_UID", "1"); monkeypatch.setenv("KPL_TOKEN", "expired")
    monkeypatch.setattr(ka, "_post_sync", lambda h, p, u, t: {"errcode": "1", "errmsg": "请重新登录"})
    with pytest.raises(ka.KplAuthError):
        asyncio.run(ka.call("applhb", {"a": "x", "c": "y"}))


def test_call_returns_json_on_success(monkeypatch):
    monkeypatch.setenv("KPL_UID", "1"); monkeypatch.setenv("KPL_TOKEN", "good")
    monkeypatch.setattr(ka, "_post_sync", lambda h, p, u, t: {"errcode": "0", "list": [1, 2]})
    assert asyncio.run(ka.call("applhb", {"a": "x", "c": "y"}))["list"] == [1, 2]


def test_network_failure_returns_none_not_auth_error(monkeypatch):
    """网络挂了返回 None(调用方按'这次没取到'处理), 不该误报成登录失效。"""
    monkeypatch.setenv("KPL_UID", "1"); monkeypatch.setenv("KPL_TOKEN", "good")
    monkeypatch.setattr(ka, "_post_sync", lambda h, p, u, t: None)
    assert asyncio.run(ka.call("applhb", {"a": "x", "c": "y"})) is None


# ── 席位解析 ────────────────────────────────────────────

def test_seat_parsing_folds_yuan_to_yi_and_reads_youzi_tag():
    from services.kaipanla_lhb import _seat
    s = _seat({"Name": "章盟主专用", "Buy": "213501909", "Sell": "166146",
               "YouZiIcon": 1, "GroupIcon": [{"Name": "量化抢筹"}]})
    assert s["营业部"] == "章盟主专用"
    assert s["买入亿"] == 2.14 and s["卖出亿"] == 0.0
    assert "游资" in s["标签"] and "量化抢筹" in s["标签"]


def test_seat_without_tags_has_none():
    from services.kaipanla_lhb import _seat
    s = _seat({"Name": "广发证券深圳后海", "Buy": "0", "Sell": "0", "YouZiIcon": 0, "GroupIcon": []})
    assert s["标签"] is None


def test_institutional_seat_name_is_not_duplicated_into_tag():
    """营业部名就叫"机构专用"时, 不该再往标签里塞一遍(界面会显示'机构专用 机构专用')。
    身份已经在营业部名里, 标签留给游资名号/分组这类额外信息。"""
    from services.kaipanla_lhb import _seat
    s = _seat({"Name": "机构专用", "Buy": "1e8", "Sell": "0"})
    assert s["营业部"] == "机构专用"
    assert s["标签"] is None            # 不重复
    # 但真游资名号仍进标签
    s2 = _seat({"Name": "章盟主专用", "Buy": "1e8", "Sell": "0", "YouZiIcon": 1})
    assert "游资" in s2["标签"]


def test_stock_lhb_reports_need_login_on_auth_error(monkeypatch):
    import services.kaipanla_lhb as kl

    async def _boom(host, params):
        raise ka.KplAuthError("Token 失效")
    monkeypatch.setattr(kl, "call", _boom)
    r = asyncio.run(kl.stock_lhb("003040", "2026-09-04"))
    assert r.get("need_login") is True and "失效" in r["error"]


def test_stock_lhb_not_listed_is_explicit(monkeypatch):
    """未上榜 ≠ 出错 —— 明说未上榜并给最近上榜日, 不返回空让人以为接口坏了。"""
    import services.kaipanla_lhb as kl

    async def _empty(host, params):
        return {"errcode": "0", "Name": "某股", "List": [], "OnTimeList": ["2026-09-01", "2026-08-20"]}
    monkeypatch.setattr(kl, "call", _empty)
    r = asyncio.run(kl.stock_lhb("600000", "2026-09-04"))
    assert r["上榜"] is False and "未上榜" in r["note"] and "2026-09-01" in r["note"]


def test_stock_lhb_parses_boards(monkeypatch):
    import services.kaipanla_lhb as kl

    async def _data(host, params):
        return {"errcode": "0", "Name": "楚天龙", "CurPrice": "21.12", "Turnover": "1040333097",
                "BuyIn": 671060551, "lbnum": 4, "OnTimeList": ["2026-09-04"],
                "List": [{"UpReason": "日换手率达20%", "BuyTotal": "500000000", "SellTotal": "100000000",
                          "BuyList": [{"Name": "广发深圳后海", "Buy": "213501909", "Sell": "166146"}],
                          "SellList": []}]}
    monkeypatch.setattr(kl, "call", _data)
    r = asyncio.run(kl.stock_lhb("003040", "2026-09-04"))
    assert r["上榜"] is True and r["龙虎榜成交额亿"] == 10.4
    b = r["席位"][0]
    assert b["上榜原因"] == "日换手率达20%" and b["买入合计亿"] == 5.0
    assert b["买入席位"][0]["买入亿"] == 2.14


def test_stock_lhb_needs_6digit_code():
    from services.kaipanla_lhb import stock_lhb
    assert "error" in asyncio.run(stock_lhb("abc"))


def test_agent_tool_registered_and_flags_login():
    from services.stock_agent import _TOOLS, _EXECUTORS
    assert "get_kpl_lhb" in _EXECUTORS
    t = next(t for t in _TOOLS if t["name"] == "get_kpl_lhb")
    assert "游资" in t["description"] and "need_login" in t["description"]

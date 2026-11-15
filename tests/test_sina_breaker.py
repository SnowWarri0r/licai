"""新浪熔断与腾讯兜底。行情数据均为虚构。"""
import asyncio

import pytest
import requests

from services import market_data as md


@pytest.fixture(autouse=True)
def _reset():
    md._sina_state.update(fails=0, open_until=0.0)
    md._cache.clear()
    yield
    md._sina_state.update(fails=0, open_until=0.0)
    md._cache.clear()


def test_breaker_opens_after_consecutive_timeouts(monkeypatch):
    calls = []

    def boom(url, **kw):
        calls.append(url)
        raise requests.Timeout("slow")
    monkeypatch.setattr(md._requests, "get", boom)
    for _ in range(md.SINA_FAIL_LIMIT):
        with pytest.raises(requests.Timeout):
            md.sina_get("https://hq.sinajs.cn/list=x")
    # 熔断后不再发请求, 直接抛, 且是 ConnectionError 的子类(现有 except 能接住)
    with pytest.raises(requests.ConnectionError) as ei:
        md.sina_get("https://hq.sinajs.cn/list=x")
    assert isinstance(ei.value, md.SinaUnavailable)
    assert len(calls) == md.SINA_FAIL_LIMIT
    assert md.sina_status()["open"]


def test_success_resets_failures(monkeypatch):
    seq = [requests.Timeout("a"), requests.Timeout("b"), "ok"]

    def flaky(url, **kw):
        x = seq.pop(0)
        if isinstance(x, Exception):
            raise x
        return x
    monkeypatch.setattr(md._requests, "get", flaky)
    for _ in range(2):
        with pytest.raises(requests.Timeout):
            md.sina_get("u")
    assert md.sina_get("u") == "ok"
    assert md.sina_status() == {"open": False, "cooldown_left": 0, "fails": 0}


def test_probe_after_cooldown(monkeypatch):
    md._sina_state.update(fails=5, open_until=0.0)        # 冷却已过
    monkeypatch.setattr(md._requests, "get", lambda url, **kw: "ok")
    assert md.sina_get("u") == "ok" and md._sina_state["fails"] == 0


_TX = ('v_sh688901="1~测试科技~688901~10.50~10.00~10.10~12345~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~'
       '~20261118150000~0.50~5.00~10.80~10.05~0~12345~1296~2.50~0";\n')


def test_parse_tencent_units():
    q = md._parse_tencent_quotes(_TX)["688901"]
    assert (q["stock_name"], q["price"], q["prev_close"], q["high"], q["low"]) == ("测试科技", 10.5, 10.0, 10.8, 10.05)
    assert q["volume"] == 1234500 and q["amount"] == 12960000      # 手 → 股, 万元 → 元
    assert q["change_pct"] == 5.0 and q["turnover_rate"] == 2.5


def test_a_share_falls_back_to_tencent_without_dropping_others(monkeypatch):
    def sina_down(codes):
        raise md.SinaUnavailable("down")
    monkeypatch.setattr(md, "_fetch_sina_quotes", sina_down)
    monkeypatch.setattr(md, "_fetch_tencent_quotes", lambda codes: md._parse_tencent_quotes(_TX))
    monkeypatch.setattr(md, "_fetch_us_stock_quote", lambda s: None)
    monkeypatch.setattr(md, "_fetch_tencent_foreign", lambda m, s: {
        "stock_name": "测试美股", "price": 20.0, "prev_close": 19.0, "currency": "USD", "market": "US"})
    monkeypatch.setattr(md, "get_fx_info", lambda c: {"rate": 7.0})
    r = asyncio.run(md.get_realtime_quotes(["688901", "US.ZZTEST"]))
    assert r["688901"]["source"] == "tencent"
    assert r["US.ZZTEST"]["price"] == 20.0 and r["US.ZZTEST"]["fx_rate"] == 7.0

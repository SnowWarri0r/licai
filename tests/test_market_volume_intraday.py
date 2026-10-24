"""当日分时量能: 腾讯为主/新浪兜底、失败重试、退回上一份结果、剖面按日缓存,
以及两市缺一边时不拿半个市场冒充两市。"""
import asyncio

import pytest

from services import market_volume as mv


def _day(scale_amt: float = 1.0) -> dict:
    """一个完整交易日(240 档)+ 今天 10 档的累计序列, 形如 _sina_min_sync/_tx_min5d_sync 的返回。"""
    def series(n):
        out, t = [], 9 * 60 + 31
        for i in range(n):
            if t == 11 * 60 + 31:
                t = 13 * 60 + 1
            out.append((f"{t // 60:02d}:{t % 60:02d}", (i + 1) * 1e8, (i + 1) * 1e9 * scale_amt))
            t += 1
        return out
    return {"2026-10-22": series(240), "2026-10-23": series(10)}


def _down(*a, **k):
    raise TimeoutError("read timed out")


@pytest.fixture(autouse=True)
def _clear_cache():
    mv._intraday_cache.clear()
    mv._profile_cache.clear()
    yield
    mv._intraday_cache.clear()
    mv._profile_cache.clear()


def test_tencent_primary(monkeypatch):
    calls = []
    monkeypatch.setattr(mv, "_tx_min5d_sync", lambda sym: _day())
    monkeypatch.setattr(mv, "_sina_min_sync", lambda sym, n, scale: calls.append(scale) or _day())
    out = asyncio.run(mv.market_volume_intraday("创业"))
    assert len(out["points"]) == 10
    assert calls == [5]             # 新浪只用来拉 5 分钟剖面


def test_sina_fallback_after_tencent_and_retry(monkeypatch):
    calls = {"n": 0}

    def flaky(sym, datalen, scale):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("read timed out")
        return _day()

    monkeypatch.setattr(mv, "_tx_min5d_sync", _down)
    monkeypatch.setattr(mv, "_sina_min_sync", flaky)
    out = asyncio.run(mv.market_volume_intraday("创业"))
    assert len(out["points"]) == 10


def test_profile_cached_per_day(monkeypatch):
    calls = []
    monkeypatch.setattr(mv, "_tx_min5d_sync", lambda sym: _day())
    monkeypatch.setattr(mv, "_sina_min_sync", lambda sym, n, scale: calls.append(scale) or _day())
    asyncio.run(mv.market_volume_intraday("创业"))
    mv._intraday_cache.clear()
    asyncio.run(mv.market_volume_intraday("创业"))
    assert calls == [5]


def test_profile_falls_back_to_tencent_days(monkeypatch):
    monkeypatch.setattr(mv, "_tx_min5d_sync", lambda sym: _day())
    monkeypatch.setattr(mv, "_sina_min_sync", _down)
    out = asyncio.run(mv.market_volume_intraday("创业"))
    assert out["projected"] is not None


def test_falls_back_to_last_good_result(monkeypatch):
    monkeypatch.setattr(mv, "_tx_min5d_sync", lambda sym: _day())
    monkeypatch.setattr(mv, "_sina_min_sync", lambda sym, n, scale: _day())
    good = asyncio.run(mv.market_volume_intraday("创业"))
    res, ts = mv._intraday_cache["创业"]
    mv._intraday_cache["创业"] = (res, ts - 3600)         # 让缓存过期, 迫使重新拉

    monkeypatch.setattr(mv, "_tx_min5d_sync", _down)
    monkeypatch.setattr(mv, "_sina_min_sync", _down)
    out = asyncio.run(mv.market_volume_intraday("创业"))
    assert out["stale"] is True
    assert out["points"] == good["points"]


def test_two_markets_need_both_exchanges(monkeypatch):
    def only_sh(sym, *a):
        if sym == mv._SYM["深"]:
            raise TimeoutError("read timed out")
        return _day()

    monkeypatch.setattr(mv, "_tx_min5d_sync", only_sh)
    monkeypatch.setattr(mv, "_sina_min_sync", only_sh)
    out = asyncio.run(mv.market_volume_intraday("两市"))
    assert out["points"] == []
    assert "两市" not in mv._intraday_cache


def test_two_markets_sum_both_exchanges(monkeypatch):
    f = lambda sym, *a: _day(2.0 if sym == mv._SYM["深"] else 1.0)  # noqa: E731
    monkeypatch.setattr(mv, "_tx_min5d_sync", f)
    monkeypatch.setattr(mv, "_sina_min_sync", f)
    out = asyncio.run(mv.market_volume_intraday("两市"))
    assert out["actual"]["amt"] == round((10e9 + 20e9) / 1e8)


def test_parse_tencent_rows(monkeypatch):
    monkeypatch.setattr(mv, "_get", lambda url: {"data": {"sz399102": {"data": [
        {"date": "20261023", "data": ["0930 3238.75 1363604 4052350522.93",
                                      "0931 3236.62 7543908 18530764492.53"]}]}}})
    d = mv._tx_min5d_sync("sz399102")
    assert d == {"2026-10-23": [("09:31", 754390800.0, 18530764492.53)]}

"""个股新闻按市场分流。

起因: 问"迈威尔为什么涨", 工具返回了 10 条新闻所以模型以为消息面已覆盖, 但那 10 条
最新只到 8-16(异动发生在 8-19 盘前), 且一半与本股无关(段永平持仓/创业板指/央行) ——
东财的 stock_news_em 对美股是按关键词搜中文新闻, 不是个股新闻流。
"""
from unittest import mock

import api.news_routes as nr
import services.stock_agent as sa


def test_yahoo_symbol_mapping():
    assert nr._yahoo_symbol("US.MRVL") == "MRVL"
    assert nr._yahoo_symbol("MRVL") == "MRVL"
    assert nr._yahoo_symbol("HK.00700") == "0700.HK"      # Yahoo 港股是 4 位
    assert nr._yahoo_symbol("HK.09988") == "9988.HK"
    assert nr._yahoo_symbol("HK.ABC") == "ABC.HK"          # 非数字不崩


class _Resp:
    def __init__(self, payload=None, text=""):
        self._payload, self.text = payload or {}, text

    def json(self):
        return self._payload


SEARCH = {"news": [
    {"title": "Marvell gives Google option to buy $12.2 billion stake in custom chip deal",
     "publisher": "Reuters", "providerPublishTime": 1787056692, "link": "https://r/1",
     "relatedTickers": ["MRVL", "GOOGL"]},
    {"title": "Kraft Natural Cheese Celebrates Back-to-School Season",
     "publisher": "PR Newswire", "providerPublishTime": 1787060000, "link": "https://r/2",
     "relatedTickers": ["KHC"]},
]}
RSS = """<rss><channel>
<item><title>Here's Why Marvell Technology (MRVL) Fell More Than Broader Market</title>
<pubDate>Tue, 18 Aug 2026 21:45:03 +0000</pubDate><link>https://y/1</link></item>
<item><title><![CDATA[Marvell gives Google option to buy $12.2 billion stake in custom chip deal]]></title>
<pubDate>Wed, 19 Aug 2026 12:38:12 +0000</pubDate><link>https://y/2</link></item>
</channel></rss>"""


def test_search_drops_items_not_tagged_to_this_stock(monkeypatch):
    """search 端点会掺整片市场的泛新闻(实测港股能返回芝士广告), 带 relatedTickers
    却不含本股的一律丢掉。"""
    import requests
    sess = mock.MagicMock()
    sess.get.side_effect = lambda url, **kw: (_Resp(SEARCH) if "search" in url
                                              else _Resp(text=RSS))
    monkeypatch.setattr(requests, "Session", lambda: sess)

    items = nr._fetch_overseas_news_yahoo_sync("US.MRVL")
    titles = [i["title"] for i in items]
    assert any("Marvell gives Google" in t for t in titles)
    assert not any("Kraft" in t for t in titles)


def test_search_and_rss_merged_and_deduped(monkeypatch):
    """同一条稿子两个端点都有(RSS 还包在 CDATA 里), 只保留一条, 且保留 search 的真实来源名。
    单用一个端点都会漏: search 有当天的, RSS 慢半天但覆盖稳。"""
    import requests
    sess = mock.MagicMock()
    sess.get.side_effect = lambda url, **kw: (_Resp(SEARCH) if "search" in url
                                              else _Resp(text=RSS))
    monkeypatch.setattr(requests, "Session", lambda: sess)

    items = nr._fetch_overseas_news_yahoo_sync("US.MRVL")
    goog = [i for i in items if "Marvell gives Google" in i["title"]]
    assert len(goog) == 1, "同一条稿子去重后应只剩一条"
    assert goog[0]["source"] == "Reuters"          # 不是 "Yahoo Finance"
    # RSS 独有的那条也在
    assert any("Fell More Than Broader Market" in i["title"] for i in items)
    # 时间倒序
    times = [i["time"] for i in items if i["time"]]
    assert times == sorted(times, reverse=True)


def test_rss_pubdate_converted_to_beijing(monkeypatch):
    """RSS 是 GMT, 页面其他时间都是北京时间, 不折算会差 8 小时。"""
    import requests
    sess = mock.MagicMock()
    sess.get.side_effect = lambda url, **kw: (_Resp({"news": []}) if "search" in url
                                              else _Resp(text=RSS))
    monkeypatch.setattr(requests, "Session", lambda: sess)

    items = nr._fetch_overseas_news_yahoo_sync("US.MRVL")
    got = {i["title"]: i["time"] for i in items}
    # Wed, 19 Aug 2026 12:38:12 +0000 → 北京 20:38:12
    key = next(k for k in got if k.startswith("Marvell gives Google"))
    assert got[key] == "2026-08-19 20:38:12"


def test_source_broken_returns_empty_not_crash(monkeypatch):
    """两个端点都打不通时返回空列表, 让上层去退回东财。"""
    import requests
    sess = mock.MagicMock()
    sess.get.side_effect = RuntimeError("boom")
    monkeypatch.setattr(requests, "Session", lambda: sess)
    assert nr._fetch_overseas_news_yahoo_sync("US.MRVL") == []


# ── 分流 ────────────────────────────────────────────────

def _route(code, monkeypatch):
    """跑一次 _tool_get_news, 返回实际被调到的源名。"""
    called = []

    def em(c):
        called.append("em")
        return [{"title": "东财条目", "content": "", "time": "2026-08-19 10:00:00",
                 "source": "证券时报网"}]

    def yh(c):
        called.append("yahoo")
        return [{"title": "yahoo item", "content": "", "time": "2026-08-19 20:38:12",
                 "source": "Reuters"}]

    monkeypatch.setattr(nr, "_fetch_stock_news_em_sync", em)
    monkeypatch.setattr(nr, "_fetch_overseas_news_yahoo_sync", yh)
    import asyncio
    out = asyncio.run(sa._tool_get_news(code))
    return called, out


def test_us_goes_to_yahoo(monkeypatch):
    called, out = _route("US.MRVL", monkeypatch)
    assert called == ["yahoo"]
    assert out["latest_time"] == "2026-08-19 20:38:12"


def test_hk_stays_on_eastmoney(monkeypatch):
    """港股反过来: 东财又准又是中文当天的, 而 Yahoo 对 0700.HK 返回整片市场的泛新闻。"""
    called, _ = _route("HK.00700", monkeypatch)
    assert called == ["em"]


def test_a_share_stays_on_eastmoney(monkeypatch):
    called, _ = _route("600519", monkeypatch)
    assert called == ["em"]


def test_us_falls_back_to_eastmoney_when_yahoo_dry(monkeypatch):
    """Yahoo 打不通时退回东财, 有一条算一条。"""
    called = []
    monkeypatch.setattr(nr, "_fetch_overseas_news_yahoo_sync",
                        lambda c: called.append("yahoo") or [])
    monkeypatch.setattr(nr, "_fetch_stock_news_em_sync",
                        lambda c: called.append("em") or
                        [{"title": "兜底", "content": "", "time": "2026-08-16 09:00:00",
                          "source": "每日经济新闻"}])
    import asyncio
    out = asyncio.run(sa._tool_get_news("US.MRVL"))
    assert called == ["yahoo", "em"]
    assert out["news"][0]["title"] == "兜底"


def test_latest_time_absent_when_no_timestamps(monkeypatch):
    """条目没时间就不要凭空造一个 latest_time —— 那会让模型以为消息面是新的。"""
    monkeypatch.setattr(nr, "_fetch_stock_news_em_sync",
                        lambda c: [{"title": "无时间", "content": "", "time": "",
                                    "source": "x"}])
    import asyncio
    out = asyncio.run(sa._tool_get_news("600519"))
    assert "latest_time" not in out

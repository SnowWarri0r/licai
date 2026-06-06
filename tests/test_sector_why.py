from fastapi.testclient import TestClient
import services.llm_client as llm
import api.news_routes as news
from run import app

client = TestClient(app)


async def _fake_market_news():
    return {"items": [{"source": "财联社", "title": "铜价隔夜下跌", "content": "", "time": "2026-06-05 09:00"}]}


def test_why_returns_two_parts(monkeypatch):
    calls = {"n": 0}
    def fake(user_prompt, system=None, model=None, max_tokens=500):
        calls["n"] += 1
        return '{"why":"铜价下跌拖累","relation":"你持有有色股受影响"}'
    monkeypatch.setattr(llm, "call_claude", fake)
    monkeypatch.setattr(news, "market_news", _fake_market_news)
    body = {"market": "A", "name": "有色金属", "change_1d": -2.3, "change_5d": -5.1, "held": True, "leader": "洛阳钼业"}
    r = client.post("/api/sector/why", json=body)
    assert r.status_code == 200
    d = r.json()
    assert d["why"] and d["relation"]
    assert calls["n"] == 1
    r2 = client.post("/api/sector/why", json=body)
    assert r2.json().get("cached") is True
    assert calls["n"] == 1


def test_why_llm_error_graceful(monkeypatch):
    monkeypatch.setattr(news, "market_news", _fake_market_news)
    def boom(*a, **k): raise RuntimeError("no creds")
    monkeypatch.setattr(llm, "call_claude", boom)
    r = client.post("/api/sector/why", json={"market": "US", "name": "信息技术xyz", "held": False})
    assert r.status_code == 200 and r.json().get("error")


def test_why_non_json_fallback(monkeypatch):
    monkeypatch.setattr(news, "market_news", _fake_market_news)
    monkeypatch.setattr(llm, "call_claude", lambda *a, **k: "就是一段话不是JSON")
    r = client.post("/api/sector/why", json={"market": "HK", "name": "金融abc", "held": False})
    assert r.status_code == 200 and r.json()["why"]

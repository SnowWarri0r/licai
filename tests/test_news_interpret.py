from fastapi.testclient import TestClient
import services.llm_client as llm
from run import app

client = TestClient(app)


def test_interpret_returns_three_parts(monkeypatch):
    calls = {"n": 0}
    def fake(user_prompt, system=None, model=None, max_tokens=600):
        calls["n"] += 1
        return '{"what":"讲了降准","why":"利好流动性","relation":"你持有银行股受益"}'
    monkeypatch.setattr(llm, "call_claude", fake)
    r = client.post("/api/news/interpret", json={"title": "央行降准", "content": "全文片段", "code": "601398", "name": "工商银行"})
    assert r.status_code == 200
    d = r.json()
    assert d["what"] and d["why"] and d["relation"]
    assert calls["n"] == 1
    r2 = client.post("/api/news/interpret", json={"title": "央行降准", "content": "全文片段", "code": "601398", "name": "工商银行"})
    assert r2.status_code == 200 and r2.json().get("cached") is True
    assert calls["n"] == 1


def test_interpret_llm_error_graceful(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no creds")
    monkeypatch.setattr(llm, "call_claude", boom)
    r = client.post("/api/news/interpret", json={"title": "另一条新闻xyz", "content": "x"})
    assert r.status_code == 200
    assert r.json().get("error")


def test_interpret_non_json_fallback(monkeypatch):
    monkeypatch.setattr(llm, "call_claude", lambda *a, **k: "这不是JSON只是一段话")
    r = client.post("/api/news/interpret", json={"title": "标题abc", "content": "y"})
    assert r.status_code == 200
    d = r.json()
    assert d["what"]

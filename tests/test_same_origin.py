"""改数据的请求只收本应用页面发来的(防别的网站借浏览器朝本机发 POST)。"""
import asyncio
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import run

c = TestClient(run.app, base_url="http://localhost:8888")
URL = "/api/settings/memory/999999/approve"


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr("config.config.db_path", path)
    from database import init_db
    asyncio.run(init_db())
    yield
    os.unlink(path)


def test_cross_site_post_rejected():
    assert c.post(URL, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post(URL, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_own_page_and_scripts_pass():
    for h in ({"Origin": "http://localhost:8888"}, {"Origin": "http://localhost:5173"},
              {"Sec-Fetch-Site": "same-origin"}, {}):
        assert c.post(URL, headers=h).status_code != 403


def test_reads_unaffected():
    assert c.get("/api/app-version", headers={"Origin": "https://evil.example"}).status_code != 403


def test_dns_rebinding_host_rejected():
    r = TestClient(run.app, base_url="http://evil.example:8888")
    assert r.get("/api/app-version").status_code == 403
    assert r.post(URL, headers={"Origin": "http://evil.example:8888"}).status_code == 403


def test_lan_ip_host_allowed():
    r = TestClient(run.app, base_url="http://192.168.1.20:8888")
    assert r.get("/api/app-version").status_code == 200


def test_websocket_rejects_foreign_origin_and_host():
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws", headers={"Origin": "https://evil.example"}):
            pass
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws", headers={"Host": "evil.example:8888"}):
            pass
    with c.websocket_connect("/ws", headers={"Origin": "http://localhost:8888"}) as ws:
        ws.send_text('{"type": "ping"}')
        assert "pong" in ws.receive_text()

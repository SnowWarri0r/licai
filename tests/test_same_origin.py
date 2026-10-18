"""改数据的请求只收本应用页面发来的(防别的网站借浏览器朝本机发 POST)。"""
from fastapi.testclient import TestClient

import run

c = TestClient(run.app, base_url="http://localhost:8888")
URL = "/api/settings/memory/999999/approve"


def test_cross_site_post_rejected():
    assert c.post(URL, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post(URL, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_own_page_and_scripts_pass():
    for h in ({"Origin": "http://localhost:8888"}, {"Origin": "http://localhost:5173"},
              {"Sec-Fetch-Site": "same-origin"}, {}):
        assert c.post(URL, headers=h).status_code != 403


def test_reads_unaffected():
    assert c.get("/api/app-version", headers={"Origin": "https://evil.example"}).status_code != 403

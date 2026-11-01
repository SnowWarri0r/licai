"""东财 push2 熔断: 整组连上即断才冷却, 冷却期内不再发请求; 超时与偶发掐首连不触发。"""
from unittest import mock

import pytest
import requests

import services.em_push2 as em


@pytest.fixture(autouse=True)
def fresh():
    em._down_until = 0.0
    yield
    em._down_until = 0.0


class _R:
    def __init__(self, j):
        self._j = j

    def json(self):
        return self._j


def _seq(*outcomes):
    """依次返回/抛出的假 requests.get, 记录调用次数。"""
    it = iter(outcomes)
    calls = []

    def get(url, **kw):
        calls.append(url)
        o = next(it)
        if isinstance(o, Exception):
            raise o
        return _R(o)
    return get, calls


def test_all_refused_trips_and_short_circuits():
    get, calls = _seq(*[requests.exceptions.ConnectionError("RemoteDisconnected")] * 6)
    with mock.patch.object(em.requests, "get", get):
        assert em.get_json("/x", {}) is None
        assert em.cooling() and len(calls) == 6
        assert em.get_json("/x", {}) is None
    assert len(calls) == 6                          # 冷却期内一次都没再发


def test_flaky_first_connect_then_success():
    good = {"data": {"diff": [1]}}
    get, calls = _seq(requests.exceptions.ConnectionError("x"), good)
    with mock.patch.object(em.requests, "get", get):
        assert em.get_json("/x", {}, ok=lambda j: j["data"]["diff"]) == good
    assert not em.cooling()


def test_timeouts_do_not_trip():
    get, _ = _seq(*[requests.exceptions.ConnectTimeout("slow")] * 6)
    with mock.patch.object(em.requests, "get", get):
        assert em.get_json("/x", {}) is None
    assert not em.cooling()


def test_ok_predicate_rejects_empty_payloads():
    get, calls = _seq(*[{"data": None}] * 6)
    with mock.patch.object(em.requests, "get", get):
        assert em.get_json("/x", {}, ok=lambda j: (j.get("data") or {}).get("diff")) is None
    assert len(calls) == 6 and not em.cooling()     # 主机通但没数, 不算限流

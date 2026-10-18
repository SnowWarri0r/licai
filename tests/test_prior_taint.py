"""前几轮读过第三方文本时, 这一轮的画像写入也只存待确认。"""
import asyncio, json

from services import ask_runs
from services.stock_agent import _prior_tainted


def _state(metas, monkeypatch):
    msgs = [{"role": "user", "content": "q"}]
    for m in metas:
        msgs += [{"role": "assistant", "content": "a", "meta": json.dumps(m) if m is not None else ""},
                 {"role": "user", "content": "q"}]

    async def sess(_):
        return {"messages": msgs}
    monkeypatch.setattr(ask_runs, "get_ask_session", sess)
    return asyncio.run(ask_runs.session_state(1, 99))


def test_session_with_news_turn_is_tainted(monkeypatch):
    st = _state([{"tools_used": ["get_holdings"]}, {"tools_used": ["get_news"]}], monkeypatch)
    assert st["tainted"] is True and _prior_tainted([], st)


def test_session_with_trusted_turns_only(monkeypatch):
    st = _state([{"tools_used": ["get_holdings", "calc"]}], monkeypatch)
    assert st["tainted"] is False and not _prior_tainted([{"role": "assistant", "content": "a"}], st)


def test_answer_without_meta_counts_as_tainted(monkeypatch):
    assert _state([None], monkeypatch)["tainted"] is True


def test_frontend_history_without_session():
    assert _prior_tainted([{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}], None)
    assert not _prior_tainted([], None)

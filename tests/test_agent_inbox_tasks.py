"""主对话执行中补充(收件箱)与子任务(主 agent 派出的子 agent)。"""
import asyncio
import json
import os
import tempfile

import pytest

import services.stock_agent as sa
from services import agent_ctx


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr("config.config.db_path", path)
    from database import init_db
    asyncio.run(init_db())
    yield path
    os.unlink(path)


@pytest.fixture(autouse=True)
def fresh_tasks(monkeypatch):
    from services import agent_tasks as at
    monkeypatch.setattr(at, "_LIVE", {})


@pytest.fixture
def runs(monkeypatch):
    from services import ask_runs
    monkeypatch.setattr(ask_runs, "_RUNS", {})
    return ask_runs


@pytest.fixture
def quiet_agent(monkeypatch):
    """agent loop 的外部依赖全换掉: LLM 轮次按脚本回, 工具不取数, 护栏不挑刺。"""
    monkeypatch.setattr(sa, "_assets_snapshot", lambda fresh=False: None)
    monkeypatch.setattr(sa._guard, "inspect", lambda *a, **k: [])

    def script(rounds, on_tool=None):
        seen = []

        async def llm_round(messages, stream=False):
            seen.append(json.loads(json.dumps(messages)))
            yield ("resp", rounds[len(seen) - 1])

        async def run_tool(tu):
            if on_tool:
                on_tool(tu)
            return {"price": 10.0}
        monkeypatch.setattr(sa, "_llm_round", llm_round)
        monkeypatch.setattr(sa, "_run_tool", run_tool)
        return seen
    return script


def _tool(name="get_quote", code="600000"):
    return {"content": [{"type": "tool_use", "id": "t1", "name": name, "input": {"code": code}}]}


def _text(t):
    return {"content": [{"type": "text", "text": t}]}


def _drive(gen, inbox):
    async def go():
        agent_ctx.run.set({"run_id": "r", "session_id": None, "take_inbox": lambda: [inbox.pop(0) for _ in list(inbox)], "spawned": 0})
        return [ev async for ev in gen]
    return asyncio.run(go())


# ── 收件箱进 agent loop ────────────────────────────────

def test_message_sent_during_tool_step_reaches_next_round(quiet_agent):
    inbox = []
    seen = quiet_agent([_tool(), _text("浦发今天涨 1%, 顺带港股恒生涨 0.5%。")],
                       on_tool=lambda tu: inbox.append({"id": 7, "text": "顺便看看港股"}))
    evs = _drive(sa.ask_stock_stream("浦发今天怎么样"), inbox)
    last_user = seen[1][-1]["content"]
    assert last_user[0]["type"] == "tool_result"                       # 工具结果在前
    assert "执行期间补充" in last_user[-1]["text"] and "- 顺便看看港股" in last_user[-1]["text"]
    assert {"type": "interject", "ids": [7], "texts": ["顺便看看港股"]} in evs
    assert evs[-2] == {"type": "answer", "text": "浦发今天涨 1%, 顺带港股恒生涨 0.5%。"}


def test_message_arriving_while_answer_written_gets_a_rewrite(quiet_agent):
    inbox = [{"id": 9, "text": "只说结论"}]
    seen = quiet_agent([_text("第一版很长的回答"), _text("结论: 涨 1%")])
    evs = _drive(sa.ask_stock_stream("浦发今天怎么样"), inbox)
    assert len(seen) == 2 and "作答期间补充" in seen[1][-1]["content"][0]["text"]
    assert [e["text"] for e in evs if e["type"] == "answer"] == ["结论: 涨 1%"]


def test_no_inbox_outside_a_run(quiet_agent):
    quiet_agent([_text("好")])

    async def go():
        return [ev async for ev in sa.ask_stock_stream("q")]
    assert [e["type"] for e in asyncio.run(go())] == ["answer", "done"]


# ── ask_runs: 投递 / 撤回 / 剩下的续跑下一轮 ────────────────

def _msgs(sid):
    from database import get_ask_session
    return asyncio.run(get_ask_session(sid))["messages"]


def test_leftover_messages_start_next_run_and_withdraw_works(temp_db, runs, monkeypatch):
    calls = []

    def agent(gate):
        async def gen(question, history=None, images=None, state=None):
            calls.append((question, history))
            await gate.wait()
            yield {"type": "answer", "text": f"答: {question}"}
        return gen

    async def main():
        gate = asyncio.Event()
        monkeypatch.setattr(runs, "ask_stock_stream", agent(gate))
        run = await runs.start("茅台怎么了")
        a = await runs.post_inbox(run.id, "顺便看五粮液")
        b = await runs.post_inbox(run.id, "撤回这句")
        c = await runs.post_inbox(run.id, "还有泸州老窖")
        assert a["status"] == b["status"] == c["status"] == "queued"
        assert await runs.withdraw(run.id, b["id"]) is True
        gate.set()
        await run.task
        nxt = runs.get(run.next_run_id)
        await nxt.task
        assert await runs.post_inbox(run.id, "晚了") == {"status": "closed"}
        return run, nxt

    run, nxt = asyncio.run(main())
    ev = [e for e in run.events if e["type"] == "next_run"][0]
    assert ev["question"] == "顺便看五粮液\n还有泸州老窖" and run.events[-1]["type"] == "done"
    assert calls[1] == ("顺便看五粮液\n还有泸州老窖", [{"role": "user", "content": "茅台怎么了"},
                                                    {"role": "assistant", "content": "答: 茅台怎么了"}])
    assert [(m["role"], m["content"]) for m in _msgs(run.session_id)] == [
        ("user", "茅台怎么了"), ("assistant", "答: 茅台怎么了"),
        ("user", "顺便看五粮液\n还有泸州老窖"), ("assistant", "答: 顺便看五粮液\n还有泸州老窖")]


def test_merged_interjection_is_folded_into_its_turn(temp_db, runs, monkeypatch):
    async def gen(question, history=None, images=None, state=None):
        await asyncio.sleep(0.05)
        got = agent_ctx.take_inbox()
        yield {"type": "interject", "ids": [m["id"] for m in got], "texts": [m["text"] for m in got]}
        yield {"type": "answer", "text": "答完"}

    async def main():
        monkeypatch.setattr(runs, "ask_stock_stream", gen)
        run = await runs.start("茅台怎么了")
        await runs.post_inbox(run.id, "只看今天")
        await run.task
        return run

    run = asyncio.run(main())
    assert run.next_run_id is None
    msgs = _msgs(run.session_id)
    assert json.loads(msgs[1]["meta"]) == {"interject": run.id, "state": "merged"}
    assert runs._fold_turns(msgs) == [{"q": "茅台怎么了\n(执行中补充: 只看今天)", "a": "答完"}]


# ── 子任务 ────────────────────────────────────────────

def _fake_sub(answers, delay=0.0):
    def stream(question):
        async def gen():
            assert agent_ctx.depth.get() == 1 and agent_ctx.run.get() is None
            yield {"type": "step", "tool": "get_quote", "label": "查行情"}
            await asyncio.sleep(delay)
            yield {"type": "answer", "text": answers.get(question.split("【任务】")[1].split("\n")[0], "无")}
        return gen()
    return stream


def _in_run(coro_fn, session_id=None):
    async def go():
        agent_ctx.run.set({"run_id": "r1", "session_id": session_id, "take_inbox": lambda: [], "spawned": 0})
        return await coro_fn()
    return asyncio.run(go())


def test_dispatch_wait_returns_all_results(temp_db, monkeypatch):
    from services import agent_tasks as at
    monkeypatch.setattr(at, "_stream", _fake_sub({"甲": "甲的结论", "乙": "乙的结论"}, delay=0.02))
    out = _in_run(lambda: at.dispatch([{"title": "甲", "brief": "查甲"}, {"title": "乙", "brief": "查乙"}], True))
    assert [(t["title"], t["status"], t["result"]) for t in out["tasks"]] == [("甲", "done", "甲的结论"), ("乙", "done", "乙的结论")]


def test_background_task_writes_a_card_and_shows_in_session_state(temp_db, runs, monkeypatch):
    from database import create_ask_session
    from services import agent_tasks as at
    monkeypatch.setattr(at, "_stream", _fake_sub({"体检": "持仓无异常"}))
    sid = asyncio.run(create_ask_session("t"))

    async def go():
        out = await at.dispatch([{"title": "体检", "brief": "全持仓体检"}], False)
        await at.wait([out["tasks"][0]["task_id"]], 5)
        return out
    out = _in_run(go, session_id=sid)
    assert out["tasks"][0]["status"] == "queued" and "后台" in out["note"]
    msgs = _msgs(sid)
    assert msgs[-1]["role"] == "task" and msgs[-1]["content"] == "持仓无异常"
    st = asyncio.run(runs.session_state(sid))
    assert st["tasks"][0]["title"] == "体检" and st["tasks"][0]["status"] == "done"
    assert "#1 体检(后台, 已完成): 持仓无异常" in sa._state_block(st)


def test_dispatch_limits(temp_db, monkeypatch):
    from services import agent_tasks as at
    monkeypatch.setattr(at, "_stream", _fake_sub({}))
    assert "error" in asyncio.run(at.dispatch([{"title": "a", "brief": "b"}], True))       # 不在某一轮里
    out = _in_run(lambda: at.dispatch([{"title": str(i), "brief": "b"} for i in range(7)], True))
    assert len(out["tasks"]) == 5 and "2 项超出上限" in out["note"]

    async def many():
        for _ in range(2):
            await at.dispatch([{"title": "x", "brief": "b"}] * 5, True)
        return await at.dispatch([{"title": "x", "brief": "b"}], True)
    assert "上限" in _in_run(many)["error"]


def test_cancelling_main_run_cancels_tasks_it_waits_for(temp_db, monkeypatch):
    from services import agent_tasks as at
    monkeypatch.setattr(at, "_stream", _fake_sub({}, delay=5))

    async def go():
        t = asyncio.create_task(at.dispatch([{"title": "慢", "brief": "b"}], True))
        await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        tid = max(at._LIVE)
        await at._LIVE[tid]["done"].wait()
        return await at.view(tid)
    assert _in_run(go)["status"] == "cancelled"


def test_sub_agents_get_no_task_or_write_tools(monkeypatch):
    from services.providers import gateway

    async def caps():
        return frozenset()
    monkeypatch.setattr(gateway, "capabilities", caps)

    async def names(depth, in_run):
        agent_ctx.depth.set(depth)
        if in_run:
            agent_ctx.run.set({"run_id": "r"})
        return {t.get("name") for t in await sa._active_tools()}
    main = asyncio.run(names(0, True))
    sub = asyncio.run(names(1, False))
    bare = asyncio.run(names(0, False))
    assert {"dispatch_tasks", "get_tasks", "remember"} <= main
    assert not ({"dispatch_tasks", "get_tasks", "remember", "save_trade_plan"} & sub) and "get_quote" in sub
    assert "dispatch_tasks" not in bare and "remember" in bare


def test_blocked_tools_are_refused_at_execution(monkeypatch):
    called = []
    async def fake(a):
        called.append(a)
        return {"ok": True}
    monkeypatch.setitem(sa._EXECUTORS, "remember", fake)

    async def run(depth):
        agent_ctx.depth.set(depth)
        agent_ctx.run.set({"run_id": "r"})
        return await sa._run_tool({"name": "remember", "input": {"text": "x"}})
    assert "error" in asyncio.run(run(1)) and called == []
    assert asyncio.run(run(0)) == {"ok": True} and len(called) == 1

    async def bare():
        return await sa._run_tool({"name": "dispatch_tasks", "input": {}})
    assert "主 agent" in asyncio.run(bare())["error"]

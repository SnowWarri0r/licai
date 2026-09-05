"""后台跑的一轮(run): 前端走开不该杀它, 也不该让这一轮从历史里消失。

原来的行为: 问一句然后切到别的页 → 组件卸载 → abort() → 客户端断开 → Starlette 取消响应
任务 → agent 在下一个 yield 处 CancelledError 死掉; 而落库挂在"前端收到完整答案之后",
会话又是"存第一条消息"时才建的 —— 所以那一轮既没跑完也没留痕, 历史里连问题都找不到。

这里测的就是这两条反过来: 停止跟看 ≠ 取消(run 照跑完, 答案自己落库), 以及问题在开跑那
一刻就已经在库里(哪怕答案还没出来, 历史里也看得见"我问过什么")。
"""
import asyncio
import os
import tempfile
import time

import pytest


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr("config.config.db_path", path)
    from database import init_db
    asyncio.run(init_db())
    yield path
    os.unlink(path)


@pytest.fixture
def runs(monkeypatch):
    """干净的 run 表: 模块级字典, 测试之间别互相看见。"""
    from services import ask_runs
    monkeypatch.setattr(ask_runs, "_RUNS", {})
    return ask_runs


ANSWER = "茅台今天跌 1.2%, 是白酒整体回调。"
EVENTS = [
    {"type": "thought", "text": "查一下"},
    {"type": "step", "tool": "resolve_stock", "label": "解析代码"},
    {"type": "step", "tool": "llm_retry"},                 # 过程提示, 不是工具
    {"type": "step", "tool": "get_quote", "label": "取行情"},
    {"type": "sources", "sources": [{"title": "某新闻", "url": "https://x.com/a"}]},
    {"type": "chart", "url": "/api/chart/abc.png"},
    {"type": "answer", "text": ANSWER},
]


def _fake_agent(events=EVENTS, delay=0.0, seen=None):
    async def gen(question, history=None, images=None):
        if seen is not None:
            seen.append({"question": question, "history": history, "images": images})
        for ev in events:
            if delay:
                await asyncio.sleep(delay)
            yield ev
    return gen


def _msgs(session_id):
    from database import get_ask_session
    s = asyncio.run(get_ask_session(session_id))
    return s["messages"] if s else []


# ── 走开不杀 ────────────────────────────────────────────

def test_leaving_mid_stream_does_not_kill_the_run(temp_db, runs, monkeypatch):
    """看两条就走(=切页/关抽屉), run 必须自己跑完, 答案必须落库。"""
    monkeypatch.setattr(runs, "ask_stock_stream", _fake_agent(delay=0.01))

    async def main():
        run = await runs.start("茅台今天怎么了", scope="market")
        got = []
        async for ev in runs.follow(run.id, 0):
            got.append(ev)
            if len(got) == 2:
                break                      # 前端不看了
        await run.task
        return run, got

    run, got = asyncio.run(main())
    assert len(got) == 2                    # 前端只收到两条
    assert run.done and run.answered        # 但它跑完了, 而且落了库
    roles = [m["role"] for m in _msgs(run.session_id)]
    assert roles == ["user", "assistant"]


def test_resume_from_cursor_gets_exactly_the_missed_part(temp_db, runs, monkeypatch):
    """回来时按游标续拉: 缺席期间的过程一条不少、也不重复。"""
    monkeypatch.setattr(runs, "ask_stock_stream", _fake_agent())

    async def main():
        run = await runs.start("茅台今天怎么了")
        head = []
        async for ev in runs.follow(run.id, 0):
            head.append(ev)
            if len(head) == 3:
                break
        await run.task
        tail = [ev async for ev in runs.follow(run.id, head[-1]["cursor"])]
        return head, tail

    head, tail = asyncio.run(main())
    assert [e["cursor"] for e in head] == [1, 2, 3]
    assert [e["cursor"] for e in tail] == list(range(4, 4 + len(tail)))
    assert tail[-1]["type"] == "done"
    # 拼起来正好是完整事件流(done 是结尾标记, 不算 agent 事件)
    assert len(head) + len(tail) - 1 == len(EVENTS)


def test_answer_persisted_even_though_nobody_watched(temp_db, runs, monkeypatch):
    """一条都不看: 答案照样进库 —— 这就是"切走了也能在历史里翻到"。"""
    monkeypatch.setattr(runs, "ask_stock_stream", _fake_agent())

    async def main():
        run = await runs.start("茅台今天怎么了")
        await run.task
        return run

    run = asyncio.run(main())
    msgs = _msgs(run.session_id)
    assert msgs[1]["content"] == ANSWER


# ── 开跑就留痕 ──────────────────────────────────────────

def test_question_is_in_history_before_the_answer(temp_db, runs, monkeypatch):
    """答案还没出来的时候, 会话列表里就得有这条(原来是一行都不写, 所以历史里空白)。"""
    monkeypatch.setattr(runs, "ask_stock_stream", _fake_agent(delay=5))
    from database import list_ask_sessions

    async def main():
        run = await runs.start("茅台今天怎么了")
        sessions = await list_ask_sessions()
        run.task.cancel()
        await asyncio.sleep(0)
        return sessions, run

    sessions, run = asyncio.run(main())
    assert [s["id"] for s in sessions] == [run.session_id]
    assert sessions[0]["title"] == "茅台今天怎么了"
    assert sessions[0]["msg_count"] == 1


def test_followup_reuses_the_same_session(temp_db, runs, monkeypatch):
    monkeypatch.setattr(runs, "ask_stock_stream", _fake_agent())

    async def main():
        a = await runs.start("茅台今天怎么了")
        await a.task
        b = await runs.start("那明天呢", session_id=a.session_id)
        await b.task
        return a, b

    a, b = asyncio.run(main())
    assert a.session_id == b.session_id
    assert [m["role"] for m in _msgs(a.session_id)] == ["user", "assistant", "user", "assistant"]


def test_drawer_prefix_goes_to_the_agent_not_into_history(temp_db, runs, monkeypatch):
    """抽屉喂 agent 的是"名称(代码): 问题", 但历史里存的是人问的原话。"""
    seen = []
    monkeypatch.setattr(runs, "ask_stock_stream", _fake_agent(seen=seen))

    async def main():
        run = await runs.start("量价配合怎么看", agent_question="贵州茅台(600519): 量价配合怎么看",
                               title="贵州茅台(600519) 量价配合怎么看", scope="stock:600519")
        await run.task
        return run

    run = asyncio.run(main())
    assert seen[0]["question"] == "贵州茅台(600519): 量价配合怎么看"
    assert _msgs(run.session_id)[0]["content"] == "量价配合怎么看"


# ── 落库的 meta ─────────────────────────────────────────

def test_meta_keeps_tools_sources_charts_but_drops_process_hints(temp_db, runs, monkeypatch):
    """llm_retry/self_check 是过程提示不是工具: 存进去, 重开会话会多出假工具胶囊。"""
    import json
    monkeypatch.setattr(runs, "ask_stock_stream", _fake_agent())

    async def main():
        run = await runs.start("茅台今天怎么了")
        await run.task
        return run

    run = asyncio.run(main())
    meta = json.loads(_msgs(run.session_id)[1]["meta"])
    assert meta["tools_used"] == ["resolve_stock", "get_quote"]
    assert meta["charts"] == ["/api/chart/abc.png"]
    assert [s["url"] for s in meta["sources"]] == ["https://x.com/a"]


# ── 明确取消 / 找不着 ───────────────────────────────────

def test_cancel_stops_it_and_writes_no_answer(temp_db, runs, monkeypatch):
    """"停"是真停: 不落答案(半截答案落进历史比没有更糟)。"""
    monkeypatch.setattr(runs, "ask_stock_stream", _fake_agent(delay=0.05))

    async def main():
        run = await runs.start("茅台今天怎么了")
        await asyncio.sleep(0.06)
        assert runs.cancel(run.id) is True
        await asyncio.sleep(0.05)
        return run

    run = asyncio.run(main())
    assert run.done and not run.answered
    assert any(e.get("type") == "error" and "取消" in e.get("error", "") for e in run.events)
    assert [m["role"] for m in _msgs(run.session_id)] == ["user"]


def test_cancel_is_a_noop_once_finished(temp_db, runs, monkeypatch):
    monkeypatch.setattr(runs, "ask_stock_stream", _fake_agent())

    async def main():
        run = await runs.start("茅台今天怎么了")
        await run.task
        return runs.cancel(run.id)

    assert asyncio.run(main()) is False


def test_following_an_unknown_run_says_so_instead_of_hanging(runs):
    """服务重启后内存里没了: 得给个明确说法, 不能让前端干等一个永不来的答案。"""
    async def main():
        return [ev async for ev in runs.follow("nope", 0)]

    evs = asyncio.run(main())
    assert len(evs) == 1 and evs[0]["gone"] is True


def test_live_filters_by_scope(temp_db, runs, monkeypatch):
    """抽屉只认领自己那只票的 run, 别把「问问市场」的那条抢过来显示。"""
    monkeypatch.setattr(runs, "ask_stock_stream", _fake_agent())

    async def main():
        a = await runs.start("大盘怎么样", scope="market")
        b = await runs.start("量价怎么看", scope="stock:600519")
        await asyncio.gather(a.task, b.task)

    asyncio.run(main())
    scoped = asyncio.run(runs.live("stock:600519"))
    assert [r["scope"] for r in scoped] == ["stock:600519"]
    assert len(asyncio.run(runs.live())) == 2


def test_gc_drops_stale_finished_runs(runs):
    """内存里只留"在跑 + 刚跑完"; 老的原文归 DB, 不在这儿占着。"""
    old = runs.Run("旧问题", 1, "market", "旧问题")
    old.done = True
    old.finished_at = time.time() - runs._KEEP_DONE_SEC - 1
    fresh = runs.Run("新问题", 2, "market", "新问题")
    fresh.done = True
    fresh.finished_at = time.time()
    runs._RUNS[old.id] = old
    runs._RUNS[fresh.id] = fresh
    runs._gc()
    assert list(runs._RUNS) == [fresh.id]


# ── 服务重启也不该让这一轮凭空消失 ──────────────────────

def test_events_are_on_disk_while_running(temp_db, runs, monkeypatch):
    """跑的过程要落盘: 进程一没内存就没了, 盘上这份是重启后唯一还认得这一轮的东西。"""
    import json
    from database import get_ask_run
    monkeypatch.setattr(runs, "ask_stock_stream", _fake_agent())

    async def main():
        run = await runs.start("茅台今天怎么了")
        await run.task
        return run

    run = asyncio.run(main())
    row = asyncio.run(get_ask_run(run.id))
    assert row and row["status"] == "done" and row["answered"] == 1
    kinds = [e.get("type") for e in json.loads(row["events"])]
    assert "answer" in kinds and kinds[-1] == "done"


def _leftover_row(run_id="deadbeef1234", events=None, scope="market"):
    """模拟上一个进程留下的行: 状态还是 running, 但那个 task 早随进程没了。

    注意不能用 task.cancel() 来模拟 —— 取消只是让协程走 except 分支, 它还会把自己收尾成
    done; 真的进程死亡不给这个机会, 行就停在 running 上。
    """
    import json
    from database import save_ask_run, update_ask_run
    import time as _t

    async def go():
        await save_ask_run(run_id, 1, scope, "茅台今天怎么了", _t.time())
        if events:
            await update_ask_run(run_id, json.dumps(events, ensure_ascii=False), "running", False, _t.time())
    asyncio.run(go())
    return run_id


def test_restart_marks_running_as_interrupted(temp_db, runs):
    """重启后那些 running 永远不会再有结果 —— 必须落地成 interrupted, 否则界面上是一行
    永远转不完的"分析中"。"""
    from database import get_ask_run, interrupt_running_ask_runs
    rid = _leftover_row()
    assert asyncio.run(get_ask_run(rid))["status"] == "running"
    assert asyncio.run(interrupt_running_ask_runs()) == 1
    assert asyncio.run(get_ask_run(rid))["status"] == "interrupted"


def test_follow_falls_back_to_disk_after_restart(temp_db, runs):
    """重启后内存是空的。跟看那一轮要能从盘上重放已经跑出来的步骤, 并明说它被打断了 ——
    比一句"不在内存里了"有用: 至少知道它查到哪一步。"""
    from database import interrupt_running_ask_runs
    rid = _leftover_row(events=[{"type": "thought", "text": "查一下"},
                                {"type": "step", "tool": "get_quote"}])
    asyncio.run(interrupt_running_ask_runs())
    runs._RUNS.clear()                              # 进程重启: 内存全空
    evs = asyncio.run(_collect(runs.follow(rid, 0)))
    assert [e.get("tool") for e in evs if e["type"] == "step"] == ["get_quote"]
    assert [e["cursor"] for e in evs] == sorted(e["cursor"] for e in evs)   # 游标单调
    assert any("重启" in (e.get("error") or "") for e in evs)
    assert evs[-1]["type"] == "done"                # 别让前端干等


def test_follow_from_disk_respects_cursor(temp_db, runs):
    """断点续看在盘上这条路径同样成立: 从游标 1 起只补后面的。"""
    from database import interrupt_running_ask_runs
    rid = _leftover_row(events=[{"type": "thought", "text": "a"}, {"type": "step", "tool": "b"}])
    asyncio.run(interrupt_running_ask_runs())
    runs._RUNS.clear()
    evs = asyncio.run(_collect(runs.follow(rid, 1)))
    assert [e["type"] for e in evs if e["type"] in ("thought", "step")] == ["step"]


def test_disk_runs_show_up_as_finished_in_live(temp_db, runs):
    """盘上那些一律 done —— 它们不可能再产出新事件, 前端不该去"接着等"。"""
    from database import interrupt_running_ask_runs
    _leftover_row()
    asyncio.run(interrupt_running_ask_runs())
    runs._RUNS.clear()
    rows = asyncio.run(runs.live("market"))
    assert len(rows) == 1
    assert rows[0]["done"] is True and rows[0]["interrupted"] is True


def test_unknown_run_still_says_gone(temp_db, runs):
    """盘上也没有(流水清理过) → 还是要给明确说法。"""
    evs = asyncio.run(_collect(runs.follow("nope", 0)))
    assert len(evs) == 1 and evs[0]["gone"] is True


async def _collect(agen):
    return [ev async for ev in agen]

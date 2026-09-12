"""多视角对抗: 共享事实、缺口不许被圆掉、部分失败要如实报、护栏不许松。

设计上刻意与 ai-berkshire 有一处不同: 他们那四个后台 agent 各自 WebSearch, 取到的数据都
不一样, 于是**分歧可能只是取数的分歧**。这里四层共享同一份 fact pack, 冲突才归因得到视角
本身 —— 所以"共享"这件事是本模块的核心不变式, 第一条测试就守它。
"""
import asyncio

import pytest


@pytest.fixture
def fake_llm(monkeypatch):
    """拦下 call_claude, 记下每次的 (system, user), 返回可辨识的假文本。"""
    calls = []

    def _fake(user_prompt, system=None, model="", max_tokens=0):
        calls.append({"system": system or "", "user": user_prompt or ""})
        if "只做三件事" in (system or ""):          # 综合那一步
            return "## 分歧点\n(假)"
        return "**结论**: 假结论\n**失效条件**: 若 X 则不成立"

    from services import llm_client
    monkeypatch.setattr(llm_client, "call_claude", _fake)
    return calls


@pytest.fixture
def fake_pack(monkeypatch):
    async def _p(code):
        return "### 实时行情\n{\"price\": 10}", []
    import services.multi_lens as ml
    monkeypatch.setattr(ml, "_fact_pack", _p)


# ── 核心不变式: 四层看同一份事实 ────────────────────────

def test_all_lenses_get_the_exact_same_facts(fake_llm, fake_pack):
    """四层必须吃同一份 fact pack。

    各自取数的话, 层与层之间的"冲突"可能只是数据不一致 —— 那种冲突没有信息量, 还会让人
    以为是视角分歧。这条把共享钉住。
    """
    from services.multi_lens import analyze, _LENSES
    asyncio.run(analyze("600176", "中国巨石", "质地怎么样"))
    lens_calls = [c for c in fake_llm if "只做三件事" not in c["system"]]
    assert len(lens_calls) == len(_LENSES)
    assert len({c["user"] for c in lens_calls}) == 1, "四层拿到的事实不一致"
    assert len({c["system"] for c in lens_calls}) == len(_LENSES), "四层的视角提示应各不相同"


def test_synthesis_sees_the_lens_reports_not_the_raw_facts(fake_llm, fake_pack):
    """综合那一步的输入是四份报告, 不该再塞一遍原始事实(否则它会绕过四层自己重写一份)。"""
    from services.multi_lens import analyze
    asyncio.run(analyze("600176"))
    synth = [c for c in fake_llm if "只做三件事" in c["system"]]
    assert len(synth) == 1
    assert "第1份" in synth[0]["user"] and "第4份" in synth[0]["user"]
    assert "fact pack" not in synth[0]["user"]


# ── 缺口不许被圆掉 ──────────────────────────────────────

def test_failed_facts_are_named_in_the_prompt(fake_llm, monkeypatch):
    """取数失败的项必须显式进 prompt 并写明不许用记忆补 —— 与 stock_agent 那条约束同源。"""
    import services.multi_lens as ml

    async def _p(code):
        return "### 实时行情\n{}\n\n### 取数失败的项(不许用记忆补)\n- 同行对照(超时)", ["同行对照(超时)"]

    monkeypatch.setattr(ml, "_fact_pack", _p)
    r = asyncio.run(ml.analyze("600176"))
    lens_calls = [c for c in fake_llm if "只做三件事" not in c["system"]]
    assert all("取数失败的项" in c["user"] for c in lens_calls)
    assert all("不许用记忆补" in c["user"] for c in lens_calls)
    assert r["缺口"] == ["同行对照(超时)"]
    assert "取数缺 1 项" in r["口径"]


def test_fact_pack_marks_tool_errors(monkeypatch):
    """工具返回 error 或直接抛异常, 都要落进缺口列表, 不能静默丢掉。"""
    import services.multi_lens as ml

    async def _ok(_a):
        return {"price": 1}

    async def _err(_a):
        return {"error": "接口超时"}

    async def _boom(_a):
        raise RuntimeError("断连")

    from services import stock_agent
    fake = dict(stock_agent._EXECUTORS)
    fake.update({"get_quote": _ok, "get_peers": _err, "get_red_flags": _boom})
    monkeypatch.setattr(stock_agent, "_EXECUTORS", fake)
    pack, missing = asyncio.run(ml._fact_pack("600176"))
    joined = " ".join(missing)
    assert "同行对照" in joined and "客观红线" in joined
    assert "实时行情" in pack
    # 关键: 缺口不只要进 missing 列表, 还必须**写进 pack 文本**并带上禁令 —— 模型只看 pack,
    # 列表它是看不到的。上一版这条只验了列表, 把生成缺口小节的代码删掉测试照样全过(空测试)。
    assert "取数失败的项(不许用记忆补)" in pack
    assert "同行对照" in pack.split("取数失败的项")[1]


def test_all_facts_failing_is_reported_not_faked(monkeypatch):
    import services.multi_lens as ml

    async def _p(code):
        return "", ["全都失败了"]

    monkeypatch.setattr(ml, "_fact_pack", _p)
    r = asyncio.run(ml.analyze("600176"))
    assert r["可用"] is False and "取数失败" in r["note"]


# ── 部分失败要如实报 ────────────────────────────────────

def test_one_lens_failing_does_not_sink_the_rest(fake_pack, monkeypatch):
    """一层挂了, 其余三层照样出, 但成功层数要如实写进口径 —— 别让人以为是四层齐活。"""
    import services.multi_lens as ml
    from services import llm_client
    n = {"i": 0}

    def _fake(user_prompt, system=None, model="", max_tokens=0):
        if "只做三件事" in (system or ""):
            return "## 分歧点\n(假)"
        n["i"] += 1
        if n["i"] == 2:
            raise RuntimeError("上游 529")
        return "**结论**: 假"

    monkeypatch.setattr(llm_client, "call_claude", _fake)
    r = asyncio.run(ml.analyze("600176"))
    assert r["可用"] is True
    assert sum(1 for l in r["各层"] if l.get("error")) == 1
    assert "3/4 层成功" in r["口径"]


def test_all_lenses_failing_is_not_dressed_up(fake_pack, monkeypatch):
    import services.multi_lens as ml
    from services import llm_client

    def _boom(*a, **k):
        raise RuntimeError("全挂")

    monkeypatch.setattr(llm_client, "call_claude", _boom)
    r = asyncio.run(ml.analyze("600176"))
    assert r["可用"] is False and "四层全部调用失败" in r["note"]


# ── 护栏 ────────────────────────────────────────────────

def test_no_lens_may_output_a_recommendation():
    """项目硬护栏: 不给买卖/仓位/目标价/评分。四层与综合的提示里都要有这条。"""
    from services.multi_lens import _LENS_SYSTEM, _SYNTH_SYSTEM
    for p in (_LENS_SYSTEM, _SYNTH_SYSTEM):
        assert "不给买卖建议" in p and "目标价" in p and "评分" in p


def test_valuation_lens_says_position_not_intrinsic_value():
    """估值那层只说"在历史与同行区间的位置", 不做内在价值/三情景 —— ai-berkshire 有
    three-scenario, 这里刻意不搬。"""
    from services.multi_lens import _LENSES
    v = next(l for l in _LENSES if l["key"] == "数")
    assert "不给内在价值" in v["checklist"] and "三情景" in v["checklist"]


def test_every_lens_must_self_report_failure_conditions():
    """只给结论的四份报告拼起来还是四段描述; 失效条件是让综合那步有东西可碰的前提。"""
    from services.multi_lens import _LENS_SYSTEM
    assert "失效条件" in _LENS_SYSTEM and "什么条件下就不成立" in _LENS_SYSTEM


def test_synthesis_must_not_write_another_summary():
    from services.multi_lens import _SYNTH_SYSTEM
    assert "不是" in _SYNTH_SYSTEM and "再写一份综述" in _SYNTH_SYSTEM
    for k in ("挑冲突", "分开事实与假设", "证伪条件"):
        assert k in _SYNTH_SYSTEM, k


def test_synthesis_may_not_invent_conflicts():
    """没有冲突就说没有 —— 为了凑数编分歧比不给分歧更有害。"""
    from services.multi_lens import _SYNTH_SYSTEM
    assert "不要为了凑数编分歧" in _SYNTH_SYSTEM


def test_lenses_do_not_overlap_in_scope():
    """四层各管一块。重叠会让四次调用退化成一次调用的四份复印。"""
    from services.multi_lens import _LENSES
    assert len({l["key"] for l in _LENSES}) == 4
    assert "这一层之外的事一句都不要写" in __import__(
        "services.multi_lens", fromlist=["x"])._LENS_SYSTEM


# ── 前端渲染的流式中间态(纯逻辑, 与 askShared.hideDanglingBold 同一算法) ──

def _hide_dangling_bold(s: str) -> str:
    """与 frontend/src/components/askShared.jsx 的 hideDanglingBold 保持一致。

    流式逐字吐字时常出现"开了 ** 闭合的还没到"的中间态; 加粗正则要求配对, 配不上就把 **
    当字面量画出来, 屏幕上露出一串星号。四层交叉那种五千字满是加粗的答案要吐几分钟,
    这个中间态会一直闪。
    """
    n = s.count("**")
    if n % 2 == 0:
        return s
    at = s.rfind("**")
    return s[:at] + s[at + 2:]


def test_dangling_bold_is_hidden_while_streaming():
    assert _hide_dangling_bold("**冲突1: PB估值位置判断—") == "冲突1: PB估值位置判断—"


def test_closed_bold_is_left_alone():
    assert _hide_dangling_bold("**冲突1**: 一致") == "**冲突1**: 一致"
    assert _hide_dangling_bold("**A** 与 **B** 都在") == "**A** 与 **B** 都在"


def test_only_the_last_unclosed_marker_is_dropped():
    """前面配好对的不能动 —— 只抹掉最后那个还没闭合的。"""
    assert _hide_dangling_bold("**A**: 见 **B") == "**A**: 见 B"

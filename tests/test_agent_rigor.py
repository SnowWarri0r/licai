"""agent 的三条严谨性约束: 取数失败自曝 / 关键数字不许心算 / 算术工具本身不能被玩坏。

灵感来自 ai-berkshire(MIT) 的两条硬规则 —— 「联网失败禁止伪装」与「禁止心算, 必须调工具」。
它们踩过的坑(issue #58)是: 后台 agent 的联网被静默拦截, 退化成拿训练知识作答, **却仍然
输出一份看起来完整的伪研究**。我们这边的同类洞更隐蔽: 工具是并发跑的、单个失败不连累其他,
所以"其余部分正常"恰恰最容易让人看不出缺口, 而界面上失败的工具原本还照样打绿勾。

所以这里守三件事:
1. 工具报错必须被识别出来并回填到界面(不然缺口是隐形的);
2. 算术走 Decimal 精算, 且量级错要认得出来(市值差 100 倍是单位错, 不是"需查口径");
3. 算术工具只认四则运算 —— 字符白名单挡不住幂运算炸弹, 必须走 AST 白名单。
"""
import asyncio

import pytest


# ── 取数失败要自曝 ──────────────────────────────────────

def test_failed_tools_are_reported_as_events():
    """失败的工具要产出 step_result 事件, 成功的不产出 —— 界面靠它把绿勾改掉。"""
    from services.stock_agent import _failed_steps
    tus = [{"name": "get_quote"}, {"name": "get_fund_flow"}, {"name": "get_trend"}]
    outs = [{"price": 10.0}, {"error": "接口超时"}, {"daily_pct": []}]
    evs = _failed_steps(tus, outs)
    assert [e["tool"] for e in evs] == ["get_fund_flow"]
    assert evs[0]["ok"] is False and "超时" in evs[0]["err"]


def test_no_events_when_everything_worked():
    from services.stock_agent import _failed_steps
    assert _failed_steps([{"name": "get_quote"}], [{"price": 1.0}]) == []


def test_error_message_is_truncated_not_dropped():
    """错误信息可能是一整段 traceback 字符串; 截断但不能丢 —— 界面要显示它当 tooltip。"""
    from services.stock_agent import _failed_steps
    evs = _failed_steps([{"name": "x"}], [{"error": "长" * 500}])
    assert evs and 0 < len(evs[0]["err"]) <= 80


def test_tool_exception_becomes_an_error_dict(monkeypatch):
    """执行器抛异常也要变成 {"error": ...} —— 否则 gather 直接炸掉整轮, 更看不出发生了什么。"""
    import services.stock_agent as sa

    async def _boom(_a):
        raise RuntimeError("上游断连")

    monkeypatch.setitem(sa._EXECUTORS, "get_quote", _boom)
    out = asyncio.run(sa._run_tool({"name": "get_quote", "input": {}}))
    assert "error" in out and "上游断连" in out["error"]


def test_unknown_tool_also_errors():
    from services.stock_agent import _run_tool
    out = asyncio.run(_run_tool({"name": "不存在的工具", "input": {}}))
    assert "error" in out


# ── 系统提示里必须留着这两条约束 ────────────────────────

def test_prompt_forbids_filling_gaps_from_memory():
    """这条是本次改动的核心: 工具报错必须点名说明缺了哪块, 不许用记忆补。

    prompt 对单个词都敏感, 之前有人删掉一句就让整块行为消失过, 所以钉住关键词。
    """
    from services.stock_agent import _SYSTEM
    assert "取数失败必须自曝" in _SYSTEM
    assert "严禁" in _SYSTEM and "记忆" in _SYSTEM


def test_prompt_keeps_the_no_mental_arithmetic_rule():
    from services.stock_agent import _SYSTEM
    assert "不许心算" in _SYSTEM and "calc" in _SYSTEM


def test_prompt_keeps_the_information_richness_tiering():
    """信息充裕就该做反面检验, 而不是复述人人都知道的多头逻辑。"""
    from services.stock_agent import _SYSTEM
    assert "信息丰富度" in _SYSTEM
    assert "资料多≠确定性高" in _SYSTEM


def test_calc_tool_is_registered():
    from services.stock_agent import _TOOLS, _EXECUTORS
    assert "calc" in _EXECUTORS
    assert any(t.get("name") == "calc" for t in _TOOLS)


# ── 算术工具 ────────────────────────────────────────────

def test_decimal_not_float():
    """0.1+0.2 必须是 0.3。

    只设 getcontext().prec 是没用的 —— 表达式里的字面量会被 Python 先解析成 float,
    等拿到结果再套 Decimal 精度早丢了(第一版实测得到 0.30000000000000004)。
    """
    from services.calc_rigor import evaluate
    assert evaluate("0.1+0.2")["精确值"] == "0.3"


def test_percent_sugar_and_real_expression():
    from services.calc_rigor import evaluate
    assert evaluate("50%")["结果"] == 0.5
    r = evaluate("(4.36-3.96)/3.96*100")
    assert abs(r["结果"] - 10.1010101) < 1e-6


def test_power_bomb_is_refused():
    """字符白名单挡不住这个: 白名单里有 `*`, 于是 `9**9**9` 写得出来, 足够挂死进程。

    所以求值走 AST 白名单而不是 eval —— Pow 根本不在允许的运算表里。
    """
    from services.calc_rigor import evaluate
    r = evaluate("9**9**9")
    assert "error" in r and "Pow" in r["error"]


def test_no_code_execution():
    from services.calc_rigor import evaluate
    for bad in ("__import__('os').system('echo hi')", "open('/etc/passwd').read()",
                "[].__class__", "lambda: 1"):
        assert "error" in evaluate(bad), bad


def test_divide_by_zero_is_an_error_not_a_crash():
    from services.calc_rigor import evaluate
    assert "error" in evaluate("1/0")


def test_market_cap_spots_a_unit_error_in_both_directions():
    """报出值比算出值大 100 倍时偏差是 -99%, 用 abs(偏差)>900 判"差数量级"会漏掉。

    所以看**比值**: 4.36 × 21.9亿股 = 95.48亿, 对上 9550 与对上 0.9548 都该判成单位错。
    """
    from services.calc_rigor import verify_market_cap
    for reported in (9550, 0.9548):
        v = verify_market_cap(4.36, 21.9, reported)
        assert "100 倍" in v["判定"], (reported, v["判定"])
    assert verify_market_cap(4.36, 21.9, 95.5)["判定"] == "一致"
    assert "查口径" in verify_market_cap(4.36, 21.9, 130)["判定"]      # 不是整数倍 → 让人去查


def test_cross_validate_needs_two_sources():
    from services.calc_rigor import cross_validate
    assert "error" in cross_validate("净利润", [12.3])
    r = cross_validate("净利润", [12.3, 15.8])
    assert r["一致"] is False and r["离散%"] > 28
    assert cross_validate("净利润", [12.30, 12.31])["一致"] is True


def test_calc_does_not_do_valuation():
    """算术工具只做能验证的事。内在价值/目标价/三情景是估值结论, 本项目不出 ——
    ai-berkshire 有 three-scenario, 我们刻意不搬。"""
    from services import calc_rigor
    assert not [n for n in dir(calc_rigor)
                if any(k in n.lower() for k in ("scenario", "intrinsic", "target", "fair"))]


def test_prompt_forbids_emoji_in_answers():
    """实测发现的缺口: prompt 从来没禁过 emoji, 于是 7 条去劣筛选被渲染成一列 ✅❌⚠️。

    问题不只是风格 —— 「⚠️判不了」读起来像一个警告, 而它的本意只是"数据不够"; 勾叉代替
    文字会把三档(通过/排除/判不了)压成对错两档。
    """
    from services.stock_agent import _SYSTEM
    assert "不用 emoji" in _SYSTEM
    assert "判不了" in _SYSTEM and "没有信息量" in _SYSTEM


def test_system_prompt_itself_carries_no_emoji():
    """规则自己也得守: prompt 里举例用的符号是唯一允许出现的地方, 其余不许夹带。"""
    import re
    from services.stock_agent import _SYSTEM
    body = _SYSTEM.split("【不用 emoji】")[0]        # 举例那句本身要写出符号才说得清
    assert not re.findall(r"[\U0001F300-\U0001FAFF✅❌⚠]", body)

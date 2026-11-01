"""纠正轮次的处理: 上一轮是助手回答、这句是纠正时, 声明纠正并按需替换被纠正的回答。"""
import services.stock_agent as sa

HIST = [{"role": "user", "content": "该死的黄金, 卖晚了"},
        {"role": "assistant", "content": "黄金ETF(518880)今天跌了 2%, 山东黄金(600547)跌 3%"}]


def _text(msg):
    c = msg["content"]
    return c if isinstance(c, str) else "".join(b.get("text", "") for b in c)


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_CORRECTION_MODE", raising=False)
    m = sa._seed_messages("我说的是黄金的两个股票", HIST)
    assert m[1]["content"] == HIST[1]["content"] and "纠正" not in _text(m[-1])


def test_mark_keeps_answer_and_declares_correction(monkeypatch):
    monkeypatch.setenv("AGENT_CORRECTION_MODE", "mark")
    m = sa._seed_messages("我说的是黄金的两个股票", HIST)
    assert m[1]["content"] == HIST[1]["content"]
    assert "对上一轮回答的纠正" in _text(m[-1]) and _text(m[-1]).endswith("我说的是黄金的两个股票")


def test_neutral_replaces_answer_keeping_codes(monkeypatch):
    monkeypatch.setenv("AGENT_CORRECTION_MODE", "neutral")
    m = sa._seed_messages("我说的是黄金的两个股票", HIST)
    assert "跌了" not in m[1]["content"] and "518880" in m[1]["content"] and "600547" in m[1]["content"]
    assert "对上一轮回答的纠正" in _text(m[-1])


def test_plain_followup_untouched(monkeypatch):
    monkeypatch.setenv("AGENT_CORRECTION_MODE", "neutral")
    m = sa._seed_messages("那明天呢", HIST)
    assert m[1]["content"] == HIST[1]["content"] and m[-1]["content"] == "那明天呢"


def test_first_turn_never_treated_as_correction(monkeypatch):
    monkeypatch.setenv("AGENT_CORRECTION_MODE", "mark")
    m = sa._seed_messages("我说的是总亏损", [])
    assert m == [{"role": "user", "content": "我说的是总亏损"}]


def test_resendable_drops_empty_text_blocks():
    tu = {"type": "tool_use", "id": "t", "name": "get_quote", "input": {}}
    assert sa._resendable([{"type": "text", "text": ""}, tu]) == [tu]
    assert sa._resendable([{"type": "text", "text": "  "}]) == [{"type": "text", "text": "(无正文)"}]

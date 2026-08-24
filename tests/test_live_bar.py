"""日 K 末尾补"今天(进行中)"那根蜡烛。

起因: 日 K 接口是收盘后才发当天那根。盘中打开宏观面板, 上面报价是实时的
(上证 3861.63 −1.12%), 下面 K 线却停在上一交易日 —— 图和数字对不上, 今天那根根本
看不见。实测 08-24 13:37 盘中, 新浪 getKLineData 最新一根还是 08-21。

判据故意不查交易日历: A股/港股/美股/日经各自的交易日与时区都不同, 查日历必然漏。改用
**报价自带的昨收**对齐 —— 昨收等于序列最后一根的收盘, 说明那根是上一交易日、实时报价
属于新的一根。对不上就不补(宁缺勿造): 港股/日经的源本来就带当天那根, 补了会重。
"""
from services.market_data import with_live_bar

_ROWS = [
    {"date": "2026-08-20", "close": 3903.721, "open": 3907.206, "high": 3925.062, "low": 3888.099},
    {"date": "2026-08-21", "close": 3905.203, "open": 3891.175, "high": 3912.131, "low": 3883.787},
]
_QUOTE = {"price": 3861.627, "prev_close": 3905.2026, "open": 3902.6963,
          "high": 3910.2394, "low": 3860.9413}


def test_appends_today_when_source_lags():
    out = with_live_bar(_ROWS, _QUOTE)
    assert len(out) == len(_ROWS) + 1
    bar = out[-1]
    assert bar["live"] is True
    assert bar["close"] == 3861.627
    assert bar["open"] == 3902.6963 and bar["high"] == 3910.2394


def test_no_append_when_source_already_has_today():
    """港股(腾讯)/日经的日 K 本来就含盘中那根 —— 昨收对不上最后一根收盘, 就该不补。"""
    rows = _ROWS + [{"date": "2026-08-24", "close": 3861.5, "open": 3902.7,
                     "high": 3910.2, "low": 3860.9}]
    assert with_live_bar(rows, _QUOTE) == rows


def test_no_append_when_prev_close_mismatch():
    """报价的昨收跟序列末尾对不上(数据错位/符号停更) → 什么都不补, 别造一根假的。"""
    q = {**_QUOTE, "prev_close": 3000.0}
    assert with_live_bar(_ROWS, q) == _ROWS


def test_high_low_stretched_to_include_live_price():
    """实时价可能已经破了源给的日高/日低(报价的高低是快照, 会滞后一两秒)。"""
    q = {**_QUOTE, "price": 3999.0}      # 高于 high
    bar = with_live_bar(_ROWS, q)[-1]
    assert bar["high"] == 3999.0
    q2 = {**_QUOTE, "price": 3000.0, "prev_close": 3905.2026}
    bar2 = with_live_bar(_ROWS, q2)[-1]
    assert bar2["low"] == 3000.0


def test_close_only_bar_when_ohlc_missing():
    """汇率/部分商品的报价没有开高低 —— 只给收盘点(前端退回折线), 不硬造蜡烛。"""
    q = {"price": 3861.6, "prev_close": 3905.203}
    bar = with_live_bar(_ROWS, q)[-1]
    assert bar["close"] == 3861.6
    assert "open" not in bar and "high" not in bar


def test_bad_inputs_are_passthrough():
    assert with_live_bar([], _QUOTE) == []
    assert with_live_bar(_ROWS, None) == _ROWS
    assert with_live_bar(_ROWS, {}) == _ROWS
    assert with_live_bar(_ROWS, {"price": 0, "prev_close": 3905.2}) == _ROWS
    assert with_live_bar(_ROWS, {"price": "x", "prev_close": "y"}) == _ROWS

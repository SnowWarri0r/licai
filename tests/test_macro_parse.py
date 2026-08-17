"""新浪宏观行情行解析: 昨收取错字段会让整块涨跌幅系统性偏小。

港股/美股的行里「今开」排在「昨收」前面, 原实现拿今开当昨收算涨跌幅, 实测恒生指数
被算成 +0.59%(真值 +1.34%)、纳斯达克 -0.45%(真值 -0.28%)。这里用实盘抓下来的行做回归,
判据是新浪自己在行内给出的涨跌幅字段。
"""
from services.market_data import _parse_macro_line

# 2026-08-17 实盘抓取
LINE_SH = ("上证指数,3930.1044,3927.1764,3982.6535,3983.5066,3924.4738,0,0,"
           "489834027,1112818620627,0,0,0,0")
LINE_HK = ("HSI,恒生指数,25303.410,25116.850,25578.270,25303.410,25453.230,336.381,"
           "1.339,0.00000,0.00000,210770397,12584632671,0.000")
LINE_US = ("纳斯达克,26729.1644,-0.28,2026-08-15 09:48:23,-73.8606,26851.1480,"
           "26862.1673,26661.9537,27190.2070,20690.2500,6405507373,7105167631,0,0.00")


def test_hk_prev_close_is_not_today_open():
    d = _parse_macro_line("hkHSI", LINE_HK)
    assert d["prev_close"] == 25116.85            # 昨收, 不是今开 25303.41
    assert abs(d["change_pct"] - 1.339) < 0.005   # 与新浪自带的涨跌幅字段吻合
    assert d["open"] == 25303.41


def test_us_prev_close_backs_out_of_change():
    d = _parse_macro_line("gb_ixic", LINE_US)
    assert abs(d["change_pct"] - (-0.28)) < 0.005  # 新浪自带涨跌幅 -0.28
    assert abs(d["prev_close"] - 26803.03) < 0.01  # = 现价 - 涨跌额
    assert d["open"] == 26851.148                  # fields[5] 是今开


def test_a_index_carries_amount_and_ohl():
    d = _parse_macro_line("sh000001", LINE_SH)
    assert abs(d["change_pct"] - 1.413) < 0.005
    assert d["amount"] == 1112818620627.0          # 成交额(元), 放大图里显示成 1.11万亿
    assert d["volume"] == 489834027.0
    assert (d["open"], d["high"], d["low"]) == (3930.1044, 3983.5066, 3924.4738)


def test_zero_fields_are_dropped_not_zeroed():
    """盘前/停牌时高低量额是 0, 不能当成真值发给前端(会显示 今开 0.00)。"""
    d = _parse_macro_line("sh000001", "某指数,0,3927.1764,3982.6535,0,0,0,0,0,0")
    assert "open" not in d and "high" not in d and "amount" not in d
    assert d["price"] == 3982.6535 and d["prev_close"] == 3927.1764


def test_overseas_and_fx_unaffected():
    nk = _parse_macro_line("int_nikkei", "日经指数,44946.64,-408.35,-0.90")
    assert abs(nk["change_pct"] - (-0.90)) < 0.01
    assert "amount" not in nk
    fx = _parse_macro_line("fx_susdcnh", "20:00:00,7.1234,7.1240,0,0,7.1300,7.1310,7.1305")
    assert fx["price"] == 7.1234 and fx["prev_close"] == 7.13
